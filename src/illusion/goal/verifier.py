"""
Goal 对抗性验证编排
===================

单对抗性验证者（N=1）方法：
当模型调用 update_goal(action="complete") 声明完成时，由 harness 同步
生成一个对抗性验证子代理尝试**驳倒**完成声明——复用 illusion 自己的
`verification` 代理定义（coordinator/agent_definitions.py，其系统提示词
要求以 `VERDICT: PASS|FAIL|PARTIAL` 结尾行收束）。

不对称失败语义：
    - 判定失败（输出不可解析）→ fail-closed（合成 FAIL，拒绝完成声明）
    - 基础设施失败（无法生成子代理）→ fail-open（视为通过，避免 harness
      缺陷卡死用户）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from illusion.goal.prompts import (
    CHANGES_UNAVAILABLE,
    VERIFIER_REPORT_MAX_CHARS,
    build_evidence_packet,
    is_goal_system_message,
    render_verifier_gaps_block,
    render_verifier_user_prompt,
)
from illusion.goal.types import (
    BLOCK_CODE_VERIFICATION_CAP,
    BLOCK_CODE_VERIFICATION_STALL,
)

if TYPE_CHECKING:
    from illusion.engine.query_engine import QueryEngine
    from illusion.goal.manager import GoalManager

logger = logging.getLogger(__name__)

# patch 文件截断上限（256 KiB）
_DIFF_MAX_BYTES = 256 * 1024

# VERDICT 结尾行契约（illusion verification 代理的输出契约）
_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL|PARTIAL)")


@dataclass
class VerificationOutcome:
    """一次完成声明验证的结果。"""

    achieved: bool
    """完成声明是否被接受（验证通过 / fail-open / 验证关闭）。"""

    verdict: str | None = None
    """解析出的判定：'PASS' | 'FAIL' | 'PARTIAL'；fail-open 时为 None。"""

    report: str = ""
    """验证者报告（净化 + 截断），回灌给实现者。"""

    gaps_block: str = ""
    """拒绝时的缺陷回灌块。"""

    fail_open: bool = False
    """是否为基础设施失败导致的 fail-open 放行。"""

    auto_paused: bool = False
    """验证 cap / 停滞检测是否已将 goal 自动置为 blocked。"""

    pause_code: str | None = None
    """自动受阻原因码（verification-cap / verification-stall）。"""

    summary: str = ""
    """面向工具结果的一段人类可读结论。"""


def parse_verdict(text: str) -> str | None:
    """解析验证报告末尾的 VERDICT 行（取最后一次出现；不可解析返回 None）。"""
    matches = _VERDICT_RE.findall(text or "")
    return matches[-1] if matches else None


def gaps_fingerprint(report: str) -> str:
    """停滞检测指纹：归一化缺陷文本哈希。"""
    normalized = re.sub(r"\s+", " ", (report or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def run_goal_verification(
    manager: GoalManager,
    engine: QueryEngine | None,
    on_progress: Any = None,
) -> VerificationOutcome:
    """对一个完成声明执行单验证者对抗性验证。

    Args:
        manager: goal 域管理器（快照必须存在）
        engine: 当前根会话引擎（用于证据采集与子代理生成）
        on_progress: 可选进度回调（透传给子代理执行器）

    Returns:
        VerificationOutcome: 验证结果；调用方据 achieved 决定是否 complete
    """
    settings = manager.settings
    snapshot = manager.snapshot
    if snapshot is None:
        return VerificationOutcome(
            achieved=False,
            summary="No current goal to verify.",
        )
    if not settings.verification_enabled:
        # 验证关闭：直接放行（complete 由模型自证）
        return VerificationOutcome(
            achieved=True,
            summary="Goal verification is disabled; completion accepted without adversarial verification.",
        )

    # 基础设施门槛：无法生成子代理 → fail-open
    verifier_def = _get_verifier_definition()
    if engine is None or verifier_def is None:
        logger.warning(
            "[goal-verifier] infra unavailable (engine=%s, verifier_def=%s); failing open",
            engine is not None,
            verifier_def is not None,
        )
        return VerificationOutcome(
            achieved=True,
            fail_open=True,
            summary=(
                "Adversarial verification could not run (verifier spawn unavailable); "
                "completion accepted via fail-open."
            ),
        )

    attempt = manager.verification_attempts + 1
    changes_file, changed_files = await _capture_changes(engine)
    final_response = _last_assistant_text(engine)
    packet = build_evidence_packet(
        objective=snapshot.objective,
        changes_file=changes_file,
        changed_files=changed_files,
        plan_file=None,
        final_response=final_response,
        prior_gaps=manager.last_gaps,
    )
    user_prompt = render_verifier_user_prompt(packet)

    result = await _spawn_verifier(engine, verifier_def, user_prompt, attempt, on_progress)

    if result is None:
        # 生成基础设施失败 → fail-open
        return VerificationOutcome(
            achieved=True,
            fail_open=True,
            summary=(
                "Adversarial verifier could not be spawned; completion accepted via fail-open."
            ),
        )

    report = (result or "").strip()
    verdict = parse_verdict(report)

    if verdict == "PASS":
        manager.record_verification_success()
        return VerificationOutcome(
            achieved=True,
            verdict="PASS",
            report=report[:VERIFIER_REPORT_MAX_CHARS],
            summary="Adversarial verification PASSED; goal marked complete.",
        )

    if verdict is None:
        # fail-closed：输出不可解析 → 合成 FAIL
        report = (
            "The adversarial verifier's output could not be parsed into a "
            "VERDICT: PASS/FAIL/PARTIAL line. Treat this as a rejection: re-run "
            "get_goal, re-check the objective, and only claim completion again "
            "with concrete verified evidence.\n\n"
            "--- verifier output (truncated) ---\n"
            + report[:VERIFIER_REPORT_MAX_CHARS]
        )
        verdict = "FAIL"

    gaps = report[:VERIFIER_REPORT_MAX_CHARS]
    fingerprint = gaps_fingerprint(gaps)
    manager.record_verification_rejection(gaps, fingerprint)

    # 拒绝后处置优先级：cap → 停滞 → 常规回灌
    if manager.verification_attempts >= settings.verification_max_attempts:
        manager.block(
            None,
            None,
            code=BLOCK_CODE_VERIFICATION_CAP,
            message=(
                f"Goal verification rejected completion "
                f"{manager.verification_attempts} times — goal auto-paused"
            ),
        )
        return VerificationOutcome(
            achieved=False,
            verdict=verdict,
            report=gaps,
            gaps_block=render_verifier_gaps_block(gaps),
            auto_paused=True,
            pause_code=BLOCK_CODE_VERIFICATION_CAP,
            summary=(
                f"Verification rejected this completion claim "
                f"{manager.verification_attempts} times (max "
                f"{settings.verification_max_attempts}); the goal is auto-paused as blocked."
            ),
        )
    if manager.same_fingerprint_as_last(fingerprint) and manager.verification_attempts >= 2:
        manager.block(
            None,
            None,
            code=BLOCK_CODE_VERIFICATION_STALL,
            message=(
                "Goal verification found no model-fixable path — the same gaps "
                "recurred across consecutive attempts; paused for your decision"
            ),
        )
        return VerificationOutcome(
            achieved=False,
            verdict=verdict,
            report=gaps,
            gaps_block=render_verifier_gaps_block(gaps),
            auto_paused=True,
            pause_code=BLOCK_CODE_VERIFICATION_STALL,
            summary=(
                "The same verification gaps recurred across consecutive attempts; "
                "the goal is auto-paused as blocked for a user decision."
            ),
        )

    return VerificationOutcome(
        achieved=False,
        verdict=verdict,
        report=gaps,
        gaps_block=render_verifier_gaps_block(gaps),
        summary=(
            f"Adversarial verification returned {verdict}; "
            "fix the flagged gaps before claiming completion again."
        ),
    )


# ---------------------------------------------------------------------------
# 内部：验证者生成与证据采集
# ---------------------------------------------------------------------------


def _get_verifier_definition() -> Any | None:
    """取 illusion 自己的 verification 代理定义（复用其对抗性系统提示词）。"""
    try:
        from illusion.coordinator.agent_definitions import get_agent_definition

        return get_agent_definition("verification")
    except Exception:
        logger.exception("[goal-verifier] failed to load verification agent definition")
        return None


async def _spawn_verifier(
    engine: QueryEngine,
    verifier_def: Any,
    user_prompt: str,
    attempt: int,
    on_progress: Any = None,
) -> str | None:
    """前台生成验证子代理，返回其最终文本。

    Returns:
        str | None: 验证者最终文本；生成基础设施失败时 None（fail-open）
    """
    from illusion.engine.query import QueryContext
    from illusion.swarm.agent_executor import AgentSpawnConfig, run_agent_in_process

    # 仿 agent_tool.py 的进程内执行路径：从引擎构建父级 QueryContext
    query_context = QueryContext(
        api_client=engine.api_client,
        tool_registry=engine.tool_registry,
        permission_checker=engine.permission_checker,
        cwd=engine.cwd,
        model=engine.model,
        system_prompt=engine.system_prompt,
        max_tokens=engine.max_tokens,
        max_turns=engine.max_turns,
        permission_prompt=engine._permission_prompt,
        ask_user_prompt=engine._ask_user_prompt,
        hook_executor=engine._hook_executor,
        effort=engine._effort,
        on_before_tool_execute=getattr(engine, "on_before_tool_execute", None),
        file_state_cache=getattr(engine, "_file_state_cache", None),
    )
    config = AgentSpawnConfig(
        name=f"goal-verifier-{attempt}",
        prompt=user_prompt,
        cwd=str(engine.cwd),
        agent_definition=verifier_def,
    )
    try:
        result = await run_agent_in_process(
            config,
            query_context,
            engine.tool_registry,
            on_progress=on_progress,
        )
    except Exception:
        logger.exception("[goal-verifier] verifier execution crashed")
        return ""
    if not result.success:
        logger.warning("[goal-verifier] verifier failed: %s", result.error)
        # 子代理已生成但运行失败 → fail-closed（合成报告由调用方包装）
        return ""
    return result.result_text or ""


async def _capture_changes(engine: QueryEngine) -> tuple[str, list[str]]:
    """采集变更证据。

    优先 git（porcelain 提供完整 CHANGED_FILES 列表，diff 写入临时 patch 文件）；
    无仓库/失败时回退 FileHistoryState 的 tracked_files；两者皆无则
    (unavailable) / (none captured)。

    Returns:
        (changes_file, changed_files)：patch 文件路径（或 (unavailable)），变更文件列表
    """
    cwd = engine.cwd
    changed_files: list[str] = []
    patch_path: str | None = None
    if (cwd / ".git").exists():
        try:
            status = await _run_git(cwd, "status", "--porcelain", "-z")
            if status is not None:
                for entry in status.split("\0"):
                    if not entry:
                        continue
                    # porcelain: XY <path>（重命名为 R  XY old -> new，取 new）
                    path = entry[3:] if len(entry) > 3 else ""
                    if " -> " in path:
                        path = path.split(" -> ", 1)[1]
                    if path:
                        changed_files.append(path.strip('"'))
            diff = await _run_git(cwd, "diff", "HEAD")
            if diff is not None and diff.strip():
                patch_path = _write_patch_file(diff, engine.session_id)
        except Exception:
            logger.exception("[goal-verifier] git capture failed; falling back")
            changed_files = []
            patch_path = None
    if not changed_files:
        fh = engine.file_history
        if fh is not None and fh.tracked_files:
            changed_files = sorted(fh.tracked_files)
    return (patch_path or CHANGES_UNAVAILABLE, changed_files)


async def _run_git(cwd: Path, *args: str) -> str | None:
    """执行 git 命令，返回 stdout；失败返回 None。

    Windows 下加 CREATE_NO_WINDOW：证据采集发生在 update_goal 工具执行内，
    不抑制控制台窗口会在 terminal 弹出黑色 git 窗口并阻塞直到手动关闭。
    """
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # subprocess.CREATE_NO_WINDOW
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **kwargs,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return None
        return out.decode("utf-8", errors="replace")
    except (OSError, asyncio.TimeoutError):
        return None


def _write_patch_file(diff: str, session_id: str) -> str:
    """将 diff 写入临时 patch 文件（256 KiB 截断 + 显式标记），返回路径。"""
    truncated = False
    encoded = diff.encode("utf-8", errors="replace")
    if len(encoded) > _DIFF_MAX_BYTES:
        encoded = encoded[:_DIFF_MAX_BYTES]
        truncated = True
    stamp = hashlib.sha256((session_id or "goal").encode()).hexdigest()[:8]
    path = Path(tempfile.gettempdir()) / f"illusion-goal-verifier-{stamp}.patch"
    path.write_bytes(encoded + (b"\n(diff truncated at 256 KiB)\n" if truncated else b""))
    return str(path)


def _last_assistant_text(engine: QueryEngine) -> str:
    """引擎消息中 goal 轮次内的最后一条 assistant 文本（FINAL_RESPONSE 证据）。

    锚定最近一次 goal harness 注入消息（<goal_round> 等）之后的消息——
    验证只面向当前 goal 轮次的实现者输出。注入之前的 assistant 属于更早的
    会话轮次（典型场景：会话首条为普通消息、后续才 /goal 创建），误取其
    文本会导致完成声明验证永远失败（验证器盯着与目标无关的旧回复）。
    未发现 goal 注入时回退到最后一条 assistant（兼容旧流程）。
    """
    messages = engine.messages
    start = 0
    for i, msg in enumerate(messages):
        if msg.role == "user" and is_goal_system_message(msg.text):
            start = i + 1
    for msg in reversed(messages[start:]):
        if msg.role == "assistant":
            return msg.text
    return ""


__all__ = [
    "VerificationOutcome",
    "gaps_fingerprint",
    "parse_verdict",
    "run_goal_verification",
]
