"""
Auto Dream 记忆整合模块
======================

定期审查并整合记忆系统，保持记忆
质量——合并重复条目、更新过时内容、解决冲突、修剪无价值条目。

触发条件（三条件同时满足才运行）：
    1. 时间间隔：距上次整合 >= dream_min_hours（默认 24 小时）
    2. 会话数量：自上次整合以来 >= dream_min_sessions 个会话（默认 5）
    3. 锁机制：无其他整合/提取任务正在运行

整合流程：
    Phase 1 — Orient: 读取现有记忆文件 + MEMORY.md 索引
    Phase 2 — Gather: 识别新信号、过时信号、冲突信号
    Phase 3 — Consolidate: 合并重复、更新过时、解决冲突
    Phase 4 — Prune: 更新 MEMORY.md 索引，修剪已整合/过时条目

函数说明：
    - record_session_start: 会话启动时调用，递增会话计数并检查触发条件
    - maybe_schedule_dream: 判断并调度后台整合任务
    - _run_dream_task: 后台执行整合（受限子代理）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from illusion.api.effort import EffortLevel
from illusion.memory.extract import build_extract_tool_registry
from illusion.memory.log import get_memory_logger, truncate
from illusion.memory.paths import get_memory_dir_for_cwd

logger = logging.getLogger(__name__)

# 状态文件名（位于记忆目录内）
DREAM_STATE_FILE = ".dream_state.json"
# 整合锁文件（防止多会话/多进程并发整合）
DREAM_LOCK_FILE = ".dream_state.lock"
# 锁文件过期时间（秒）：崩溃残留锁超过此时长自动清除
DREAM_LOCK_STALE_SECONDS = 600.0

DEFAULT_MIN_HOURS = 24
DEFAULT_MIN_SESSIONS = 5
# 整合任务需要读取全部记忆文件（可能很多）再整合写入，给足轮次
MAX_DREAM_TURNS = 50


def _load_dream_state(memory_dir: Path) -> dict[str, Any]:
    """读取 dream 状态文件。

    Returns:
        dict: {"last_dream_at": ISO时间或None, "session_count": int}
    """
    path = memory_dir / DREAM_STATE_FILE
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "last_dream_at": data.get("last_dream_at"),
                "session_count": int(data.get("session_count", 0)),
            }
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("Failed to read dream state file %s", path)
    return {"last_dream_at": None, "session_count": 0}


def _save_dream_state(memory_dir: Path, state: dict[str, Any]) -> None:
    """写入 dream 状态文件（原子写入）。"""
    path = memory_dir / DREAM_STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    """返回当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _acquire_dream_lock(memory_dir: Path) -> bool:
    """尝试获取整合文件锁（原子 O_EXCL 创建）。

    防止多会话/多进程同时整合同一记忆目录。崩溃残留的锁
    超过 DREAM_LOCK_STALE_SECONDS 自动清除。

    Args:
        memory_dir: 记忆目录

    Returns:
        bool: 是否成功获取锁
    """
    lock_path = memory_dir / DREAM_LOCK_FILE
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        # 检查是否过期（崩溃残留）
        try:
            if time.time() - lock_path.stat().st_mtime > DREAM_LOCK_STALE_SECONDS:
                lock_path.unlink()
                return _acquire_dream_lock(memory_dir)
        except OSError:
            pass
        return False
    except OSError:
        return False


def _release_dream_lock(memory_dir: Path) -> None:
    """释放整合文件锁。"""
    try:
        (memory_dir / DREAM_LOCK_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def record_session_start(engine: Any) -> bool:
    """会话启动时调用：递增会话计数并检查是否触发整合。

    在 QueryEngine 首次 submit_message 时调用一次。

    Args:
        engine: QueryEngine 实例

    Returns:
        bool: 是否调度了整合任务
    """
    try:
        from illusion.config.settings import load_settings
        from illusion.memory.manager import is_memory_enabled

        # 子代理守卫：提取/整合/标题生成子代理不是真实会话，不得计数或触发整合
        if getattr(engine, "_is_memory_subagent", False) or getattr(
            engine, "_is_title_subagent", False
        ):
            return False
        if not is_memory_enabled(engine.cwd):
            return False
        settings = load_settings()
        # 手动模式：允许记忆但禁用后台 LLM 整合（不额外调用子代理）
        if not settings.memory.auto_extract:
            return False
        min_hours = max(1, settings.memory.dream_min_hours)
        min_sessions = max(1, settings.memory.dream_min_sessions)

        memory_dir = get_memory_dir_for_cwd(engine.cwd)
        state = _load_dream_state(memory_dir)

        # 会话计数递增
        state["session_count"] = state.get("session_count", 0) + 1
        _save_dream_state(memory_dir, state)

        # 触发条件检查
        last_dream_at = state.get("last_dream_at")
        if last_dream_at:
            try:
                last = datetime.fromisoformat(last_dream_at)
                now = datetime.now(timezone.utc)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                hours_since = (now - last).total_seconds() / 3600
            except ValueError:
                hours_since = float("inf")
        else:
            hours_since = float("inf")

        if hours_since < min_hours:
            return False
        if state.get("session_count", 0) < min_sessions:
            return False

        # 锁检查：提取任务正在运行 或 其他整合已持有文件锁则跳过
        extract_state = getattr(engine, "_memory_extract_state", None)
        if extract_state is not None and getattr(extract_state, "running", False):
            return False
        if not _acquire_dream_lock(memory_dir):
            return False

        # 触发整合（锁在整合完成后释放）
        asyncio.create_task(_run_dream_task(engine, memory_dir))
        return True
    except Exception:
        logger.exception("Failed to schedule auto dream")
        return False


def _dream_prompt(memory_dir: Path) -> str:
    """构建整合子代理提示词（注入记忆目录路径）。"""
    return (
        "You are now acting as the memory consolidation subagent.\n"
        f"The memory directory is: {memory_dir}\n"
        "CRITICAL: Your entire task is confined to this memory directory. Read ONLY "
        "files inside it, and write/edit files ONLY inside it (access elsewhere is "
        "rejected). Do NOT read project source code or tests to 'verify' memories — "
        "that is not your job.\n"
        "Review the persistent memory system and consolidate it into a clean, "
        "high-quality state. Work through these phases:\n"
        "\n"
        "Phase 1 — Orient: Read MEMORY.md and every memory file in the memory "
        "directory to understand the current index and memory landscape.\n"
        "\n"
        "Phase 2 — Gather: Identify signals that need attention: stale or outdated "
        "facts, duplicate entries covering the same topic, conflicting claims, "
        "poorly described entries, and files missing from the MEMORY.md index.\n"
        "\n"
        "Phase 3 — Consolidate: Merge duplicates into a single topic file, update "
        "outdated content, resolve conflicts (keep the more recent claim, note the "
        "why), and fix frontmatter (name/description/type). Keep the name, "
        "description, and type fields up to date.\n"
        "\n"
        "Phase 4 — Prune: Rewrite MEMORY.md so every index entry points to an "
        "existing file with a one-line hook: `- [Title](user/user_role.md) — one-line "
        "hook` (path relative to the memory directory, including the type "
        "subdirectory). Remove index entries for files you deleted. Remove memories "
        "that are superseded, no longer relevant, or ephemeral task detail.\n"
        "\n"
        "Do not save new memories about the user here — this task is only about "
        "consolidating what already exists. If everything is already clean, "
        "respond with 'Memory is clean.' and stop."
    )


async def _run_dream_task(engine: Any, memory_dir: Path) -> None:
    """后台执行记忆整合（受限子代理，最多 50 turns）。

    无感保证：任务首个 await 前先让出当前事件循环 tick（sleep(0) 非延迟），
    确保调用方（行任务收尾、busy 释放、line_complete 推送）先完成；
    之后的同步初始化（logger 首次创建、client 构建）不再阻塞任何收尾路径。
    子代理的事件流在此处被直接消费丢弃，不会渲染到主对话；
    其活动通过 ~/.illusion/logs/memory_dream.log 透明记录。
    """
    # 让出当前 tick：仅让事件循环先完成调用方剩余同步收尾，无实际等待
    await asyncio.sleep(0)
    activity = get_memory_logger("dream")
    success = False
    try:
        registry = build_extract_tool_registry(memory_dir)
        # 后台整合不应打扰用户：工具注册表已限制为只读 + 记忆目录内写入，
        # 因此子代理使用 FULL_AUTO 权限模式（无需用户确认）。
        from illusion.config.settings import PermissionSettings
        from illusion.permissions.checker import PermissionChecker
        from illusion.permissions.modes import PermissionMode

        auto_checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
        # 解析配置的整合模型：未指定（None）则继承当前引擎模型。
        # 指定了其他 env 的模型时，按该 env 的端点/凭据独立构建 client
        # （跨环境：不能复用主对话 client，否则模型不被当前 provider 支持）。
        # client 与 model 必须原子选择：构建失败时 model 同步回退当前模型，
        # 否则用主 client 调另一 provider 的模型必然 400（回退机制名存实亡）。
        from illusion.config.settings import load_settings

        settings = load_settings()
        env_key, configured_model = settings.resolve_model_ref_with_env(settings.memory.dream_model)
        sub_api_client = engine.api_client
        if env_key and env_key != settings._active_env_key and configured_model:
            try:
                from illusion.api.factory import build_api_client_for_env

                sub_api_client = build_api_client_for_env(settings, env_key)
            except (ValueError, RuntimeError):
                logger.warning(
                    "Failed to build API client for env %s, falling back to current model",
                    env_key,
                )
                configured_model = None
        sub_engine = engine.__class__(
            api_client=sub_api_client,
            tool_registry=registry,
            permission_checker=auto_checker,
            cwd=engine.cwd,
            model=configured_model or engine.model,
            system_prompt=(
                "You are a background memory consolidation subagent. You review "
                "and clean up a file-based memory system. You may only read files "
                "and write inside the memory directory."
            ),
            max_tokens=65536,  # 记忆会持续增长，token 上限按最大配置
            max_turns=MAX_DREAM_TURNS,
            # 思考强度固定 high：不继承主会话（记忆整合质量优先）
            effort=EffortLevel.HIGH,
        )
        # 子代理守卫标记：其回合结束不得再触发提取/整合（防级联）
        sub_engine._is_memory_subagent = True
        # 消费事件流（不向用户展示）：同时收集活动写入透明日志
        from illusion.engine.stream_events import (
            AssistantTurnComplete,
            ErrorEvent,
            ToolExecutionCompleted,
            ToolExecutionStarted,
        )

        activity.info("Auto dream started: consolidating memory directory %s", memory_dir)
        async for event in sub_engine.submit_message(_dream_prompt(memory_dir)):
            if isinstance(event, ToolExecutionStarted):
                activity.info(
                    "  tool: %s(%s)",
                    event.tool_name,
                    truncate(str(event.tool_input)),
                )
            elif isinstance(event, ToolExecutionCompleted):
                if event.is_error:
                    activity.warning("  result: ERROR %s", truncate(event.output))
                else:
                    activity.info("  result: OK (%d chars)", len(event.output))
            elif isinstance(event, AssistantTurnComplete):
                activity.info("  model: %s", truncate(event.message.text, 1000))
            elif isinstance(event, ErrorEvent):
                activity.error("  error: %s", truncate(event.message))
        success = True
        activity.info("Auto dream finished")
    except asyncio.CancelledError:
        logger.info("Auto dream cancelled")
        activity.warning("Auto dream cancelled")
    except Exception:
        logger.exception("Auto dream failed")
        activity.exception("Auto dream failed")
    finally:
        if success:
            # 整合成功：记录完成时间并重置会话计数
            state = _load_dream_state(memory_dir)
            state["last_dream_at"] = _now_iso()
            state["session_count"] = 0
            _save_dream_state(memory_dir, state)
        else:
            # 整合失败：保留 last_dream_at（时间闸门保持打开），
            # 但重置会话计数防忙转——需要新的 min_sessions 个会话才重试
            state = _load_dream_state(memory_dir)
            state["session_count"] = 0
            _save_dream_state(memory_dir, state)
        # 释放文件锁
        _release_dream_lock(memory_dir)
