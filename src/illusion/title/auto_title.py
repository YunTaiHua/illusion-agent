"""
后台会话标题生成模块
====================

仅在第一回合结束后在后台运行一个轻量子代理，根据用户的首条真实消息生成
简洁标题，写入会话 meta.json 的 title 字段。

设计要点（与 memory/extract 对齐）：
    - 无感调度：通过 asyncio.create_task 后台执行，首个 await 前让出当前
      tick，不阻塞主对话
    - 仅首回合：每会话只尝试一次（state.attempted），且仅在真实用户回合数
      不超过 1 时触发；标题素材只取首条真实用户消息，不分析整段对话
    - /goal 场景：/goal 创建命令原文已作为真实 user 消息入库
      （record_goal_command），标题素材直接捕获它；仅当首条消息是 goal
      harness 注入消息（<goal_round>，非真实用户输入）且无其他真实消息时，
      用当前 goal 的 objective 兜底作为标题素材
    - 一次性生成：会话已有 title（手动重命名或已生成过）则跳过
    - 工具限制：标题生成无需任何工具，使用空工具注册表，仅一轮 LLM 调用
    - 模型可配：settings.title.model（env_N.model_M 格式），None 继承当前
    - 异常隔离：任何失败只记日志，不影响主会话
    - 完成回调：生成后调用 engine._title_on_generated（Web 端借此刷新
      会话列表，使自动命名即时显现）

函数说明：
    - TitleState: 标题生成状态（是否已尝试/已生成）
    - maybe_schedule_title: 回合结束后调用，判断并调度后台标题生成
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from illusion.api.effort import EffortLevel
from illusion.config.paths import get_logs_dir
from illusion.engine.messages import ConversationMessage
from illusion.memory.log import truncate
from illusion.utils.log_cleanup import cleanup_old_files

logger = logging.getLogger(__name__)

# 标题长度上限（与系统提示词中的 ≤50 字符规则对齐）
MAX_TITLE_LEN = 50
# 标题生成最大轮次（单轮足够，纯文本标题无需工具探索）
MAX_TITLE_TURNS = 1
# 标题生成尝试次数：模型偶发返回空正文时重试，次数/间隔为通用取值
MAX_TITLE_ATTEMPTS = 3
TITLE_RETRY_DELAY = 1.5  # 秒

# 标题活动日志文件参数（路径经 get_logs_dir() 解析，无硬编码）
_TITLE_LOG_NAME = "title"  # 对应 ~/.illusion/logs/title.log
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 单文件上限 5MB
_LOG_BACKUP_COUNT = 3  # 滚动备份数
_LOG_TTL_DAYS = 7  # 日志保留天数
_LOG_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 体积兜底阈值 10MB

_activity_logger: logging.Logger | None = None


def _activity_log() -> logging.Logger:
    """获取标题活动文件日志器（写入 ~/.illusion/logs/title.log）。

    后台标题生成的整个过程（调度、跳过原因、生成结果、写入与刷新回调）
    记录到专用文件，便于用户在任何会话结束后查看发生了什么。propagate=False
    避免传播到根 logger 在控制台刷屏。
    """
    global _activity_logger
    if _activity_logger is not None:
        return _activity_logger
    activity = logging.getLogger("illusion.title.log")
    for handler in list(activity.handlers):
        activity.removeHandler(handler)
        handler.close()
    # 先清理超龄/超大的旧标题日志（顺序在创建 handler 之前：Windows 上被
    # 打开的文件无法删除）。glob 覆盖 RotatingFileHandler 滚动备份（.1/.2/.3）
    cleanup_old_files(
        get_logs_dir(),
        "title.log*",
        max_age_days=_LOG_TTL_DAYS,
        max_size_bytes=_LOG_MAX_SIZE_BYTES,
    )
    activity.setLevel(logging.INFO)
    activity.propagate = False
    log_path = get_logs_dir() / f"{_TITLE_LOG_NAME}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    activity.addHandler(handler)
    _activity_logger = activity
    return activity


def _title_system_prompt() -> str:
    """构建标题生成子代理的系统提示词。

    强约束 + 示例引导，明确要求"转换"而非回执原样：标题需概括主题、
    用与用户消息相同的语言、≤ 50 字符、只输出单行标题。若无约束，模型
    常把首条用户消息原封不动当作标题，效果差。
    """
    return (
        "You are a title generator. You output ONLY a thread title. Nothing else.\n"
        "\n"
        "<task>\n"
        "Generate a brief title that would help the user find this conversation later.\n"
        "\n"
        "Follow all rules in <rules>\n"
        "Use the <examples> so you know what a good title looks like.\n"
        "Your output must be:\n"
        "- A single line\n"
        "- <= 50 characters\n"
        "- No explanations\n"
        "</task>\n"
        "\n"
        "<rules>\n"
        "- you MUST use the same language as the user message you are summarizing\n"
        "- Title must be grammatically correct and read naturally - no word salad\n"
        "- Never include tool names in the title (e.g. FileEditTool, BashTool)\n"
        "- Focus on the main topic or question the user needs to retrieve\n"
        "- Vary your phrasing - avoid repetitive patterns like always starting with 'Analyzing'\n"
        "- When a file is mentioned, focus on WHAT the user wants to do WITH the file, "
        "not just that they shared it\n"
        "- Keep exact: technical terms, numbers, filenames, HTTP codes\n"
        "- Remove: the, this, my, a, an\n"
        "- Never assume tech stack\n"
        "- Never use tools\n"
        "- NEVER respond to questions, just generate a title for the conversation\n"
        "- The title should NEVER include 'summarizing' or 'generating' when generating a title\n"
        "- DO NOT SAY YOU CANNOT GENERATE A TITLE OR COMPLAIN ABOUT THE INPUT\n"
        "- Always output something meaningful, even if the input is minimal.\n"
        "- If the user message is short or conversational (e.g. 'hello', 'what's up', 'hey'):\n"
        "  -> create a title that reflects the user's tone or intent (such as Greeting, "
        "Quick check-in, Light chat, Intro message)\n"
        "</rules>\n"
        "\n"
        "<examples>\n"
        '"debug 500 errors in production" -> Debugging production 500 errors\n'
        '"refactor user service" -> Refactoring user service\n'
        '"why is app.js failing" -> app.js failure investigation\n'
        '"implement rate limiting" -> Rate limiting implementation\n'
        '"help me connect postgres to my api" -> Postgres API connection\n'
        '"@src/auth.ts add refresh token support" -> Auth refresh token support\n'
        '"@App.tsx add dark mode toggle" -> Dark mode toggle in App\n'
        "</examples>"
    )


def _clean_title(raw: str) -> str:
    """清洗正文路径的模型输出。

    去除前导/后导的思考块、取首个非空行，保证侧边栏标题为干净单行文本。
    """
    if not raw:
        return ""
    # 去除思考块：兼容  response 与无标签的 <thinking> 包裹
    cleaned = re.sub(r"/?thinking[\s\S]*? response\s*", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"<thinking>[\s\S]*?</thinking>\s*", "", cleaned, flags=re.IGNORECASE)
    # 逐行清理，取首个非空行作为标题
    for line in cleaned.split("\n"):
        line = line.strip()
        if line:
            return line
    return ""


def _user_messages(engine: Any) -> list[str]:
    """收集真实用户消息文本（排除后台任务通知、goal 注入与会话引用快照）。

    注：/goal 创建命令原文已作为真实 user 消息入库（record_goal_command），
    会被收集作为标题素材；goal harness 注入的 <goal_round> 等消息被排除。
    """
    from illusion.engine.messages import ToolResultBlock
    from illusion.goal.prompts import is_goal_system_message
    from illusion.services.session_reference import is_session_reference_snapshot
    from illusion.tasks.types import is_task_notification

    texts: list[str] = []
    for msg in engine.messages:
        if msg.role != "user":
            continue
        if any(isinstance(b, ToolResultBlock) for b in msg.content):
            continue
        text = msg.text.strip()
        if not text:
            continue
        # 后台任务通知、goal harness 注入消息与会话引用快照不是用户输入，排除
        if (
            is_task_notification(text)
            or is_goal_system_message(text)
            or is_session_reference_snapshot(text)
        ):
            continue
        texts.append(text)
    return texts


def _goal_objective(engine: Any) -> str:
    """提取当前 goal 的 objective（/goal 开局时的标题素材兜底）。

    兜底场景：首条消息为 goal harness 注入消息（<goal_round>，非真实用户
    输入）且无其他真实消息时使用；/goal 命令原文已入库的场景优先走
    _user_messages，不会落到这里。
    """
    goal_manager = getattr(engine, "goal_manager", None)
    if goal_manager is None:
        return ""
    snapshot = goal_manager.snapshot
    if snapshot is None:
        return ""
    return (getattr(snapshot, "objective", "") or "").strip()


def _extract_title_source(engine: Any) -> str:
    """提取首条真实用户消息作为标题素材；/goal 开局用 goal objective 兜底。"""
    messages = _user_messages(engine)
    if messages:
        return messages[0]
    return _goal_objective(engine)


class TitleState:
    """标题生成状态（绑定单个 QueryEngine 实例）。

    Attributes:
        cwd: 工作目录
        session_id: 目标会话 ID（由 checkpoint_store 补齐）
        running: 标题生成任务是否正在运行（并发锁）
        attempted: 本会话是否已尝试过标题生成（仅首回合尝试一次）
        generated: 本会话是否已成功生成标题
    """

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = str(cwd)
        self.session_id: str | None = None
        self.running = False
        self.attempted = False
        self.generated = False


def maybe_schedule_title(engine: Any) -> None:
    """回合结束后调用：判断并调度后台标题生成。

    判断链（全部通过才启动）：
        1. 自动标题开关（settings.title.enabled，关闭则跳过）
        2. 会话有消息可分析
        3. 本会话首回合尚未尝试（state.attempted 为 False）
        4. 仅首回合：真实用户回合数不超过 1
        5. 磁盘 meta 尚无 title（手动重命名或此前已生成则跳过）
        6. 存在标题素材（首条真实用户消息 / goal objective）

    Args:
        engine: QueryEngine 实例（需要 messages、goal_manager、api_client、checkpoint_store）
    """
    try:
        # 子代理守卫：提取/整合/标题生成子代理自身是 QueryEngine，其回合结束
        # 不得再触发标题生成，防止无限级联
        if getattr(engine, "_is_memory_subagent", False) or getattr(
            engine, "_is_title_subagent", False
        ):
            return
        if not engine.messages:
            return
        from illusion.config.settings import load_settings

        settings = load_settings()
        title_settings = getattr(settings, "title", None)
        if title_settings is None or not title_settings.enabled:
            # 未启用自动标题：不创建日志/清理，避免默认关闭时产生副作用
            return
        activity = _activity_log()

        state = getattr(engine, "_title_state", None)
        if state is None:
            state = TitleState(engine.cwd)
            engine._title_state = state
        if state.running or state.attempted:
            return

        store = engine.checkpoint_store
        if store is None or not store.session_id:
            return
        state.session_id = store.session_id

        # 会话已有标题（手动重命名或此前已生成）则跳过，避免覆盖用户命名
        from illusion.services.session_storage import read_meta

        existing = read_meta(engine.cwd, state.session_id)
        if existing and existing.get("title"):
            state.attempted = True
            state.generated = True
            activity.info("skip: session=%s already titled", state.session_id)
            return

        # 仅首回合：真实用户回合数超过 1 时不再生成标题
        if len(_user_messages(engine)) > 1:
            activity.info("skip: session=%s past first turn", state.session_id)
            return

        # 提取标题素材（首条真实用户消息；/goal 开局回退到 goal objective）
        source = _extract_title_source(engine)
        if not source:
            # 首回合尚未形成素材（如 goal 尚未落地），留待下一回合再试
            return

        state.attempted = True
        state.running = True
        activity.info(
            "schedule: session=%s source=%s",
            state.session_id,
            truncate(source),
        )
        asyncio.create_task(_run_title_task(engine, source, state))
    except Exception:
        logger.exception("Failed to schedule auto title")
        _activity_log().exception("Failed to schedule auto title")


def _resolve_title_api_client(
    engine: Any, configured_model: str | None
) -> tuple[Any, str]:
    """解析标题生成子代理的 client/model（跨环境独立构建，原子选择）。

    与 memory/extract 相同策略：未指定模型继承当前 client/model；指定了
    其他 env 的模型时按该 env 构建独立 client，构建失败回退当前模型。
    """
    from illusion.config.settings import load_settings

    settings = load_settings()
    env_key, resolved_model = settings.resolve_model_ref_with_env(configured_model)
    sub_api_client = engine.api_client
    if env_key and env_key != settings._active_env_key and resolved_model:
        try:
            from illusion.api.factory import build_api_client_for_env

            sub_api_client = build_api_client_for_env(settings, env_key)
        except (ValueError, RuntimeError):
            logger.warning(
                "Failed to build API client for env %s, falling back to current model",
                env_key,
            )
            resolved_model = None
    return sub_api_client, resolved_model or engine.model


async def _generate_title(engine: Any, source: str) -> str:
    """在后台子代理中基于首条消息生成会话标题（空工具注册表，单轮调用）。

    Args:
        engine: 主会话引擎（用于继承 client/model/配置）
        source: 标题素材（首条真实用户消息或 goal objective）

    Returns:
        str: 生成的标题；失败或无输出时返回空串
    """
    from illusion.config.settings import PermissionSettings, load_settings
    from illusion.permissions.checker import PermissionChecker
    from illusion.permissions.modes import PermissionMode
    from illusion.tools.base import ToolRegistry

    settings = load_settings()
    configured_model = getattr(settings.title, "model", None)
    sub_api_client, model = _resolve_title_api_client(engine, configured_model)
    _activity_log().info("generate: model=%s source=%s", model, truncate(source))

    auto_checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
    sub_engine = engine.__class__(
        api_client=sub_api_client,
        tool_registry=ToolRegistry(),  # 标题生成无需工具，拒绝一切工具调用
        permission_checker=auto_checker,
        cwd=engine.cwd,
        model=model,
        system_prompt=_title_system_prompt(),
        max_tokens=8192,
        max_turns=MAX_TITLE_TURNS,
        # 标题生成固定 high 强度、充足的 max_tokens：保证输出预算，避免
        # 生成被截断导致正文缺失（具体取值对多数模型通用成立）
        effort=EffortLevel.HIGH,
    )
    # 子代理守卫标记：其回合结束不得再触发提取/整合/标题生成（防级联）
    sub_engine._is_title_subagent = True
    # 指令用户消息在前，
    # 首条真实用户消息随后（submit_message 追加）——顺序对模型是否直接
    # 回执上下文影响很大，指令置前可显著降低"照抄原文当标题"。
    sub_engine.load_messages(
        [ConversationMessage.from_user_text("Generate a title for this conversation:\n")]
    )

    title = ""
    from illusion.engine.stream_events import (
        AssistantTurnComplete,
        ErrorEvent,
    )

    async for event in sub_engine.submit_message(source):
        if isinstance(event, AssistantTurnComplete):
            title = (getattr(event.message, "text", "") or "").strip()
        elif isinstance(event, ErrorEvent):
            logger.warning("Auto title subagent error: %s", event.message)
    # 清洗：剥离思考包裹、取首个非空行（对齐 opencode 标题清洗）
    title = _clean_title(title)
    return title


async def _run_title_task(
    engine: Any,
    source: str,
    state: TitleState,
) -> None:
    """后台执行标题生成：子代理生成 → 写 meta.title → 回调。

    无感保证：首个 await 前先让出当前事件循环 tick（sleep(0) 非延迟），
    确保调用方（行任务收尾、busy 释放、line_complete 推送）先完成。
    """
    # 让出当前 tick：仅让事件循环先完成调用方剩余同步收尾，无实际等待
    await asyncio.sleep(0)
    activity = _activity_log()
    session_id = state.session_id
    if not session_id:
        # 调度时已保证非空，此处仅兜底并释放并发锁
        state.running = False
        activity.warning("skip: missing session_id")
        return
    title = ""
    try:
        # 模型偶发返回空正文：在此重试若干次，通用处理、不针对具体模型/提供商
        for attempt in range(1, MAX_TITLE_ATTEMPTS + 1):
            activity.info("start: session=%s attempt=%d", session_id, attempt)
            try:
                result = await _generate_title(engine, source)
            except asyncio.CancelledError:
                logger.info("Auto title generation cancelled")
                activity.warning("cancel: session=%s", session_id)
                raise
            except Exception:
                logger.exception("Auto title generation failed")
                activity.exception(
                    "generate failed: session=%s attempt=%d", session_id, attempt
                )
                result = ""
            title = (result or "").strip()
            if title or attempt >= MAX_TITLE_ATTEMPTS:
                break
            activity.warning(
                "empty result, retrying (%d/%d): session=%s",
                attempt + 1,
                MAX_TITLE_ATTEMPTS,
                session_id,
            )
            await asyncio.sleep(TITLE_RETRY_DELAY)
    finally:
        state.running = False

    title = title.strip('"').strip("`").strip()
    if title:
        # 截断标题，避免过长拥挤侧边栏
        if len(title) > MAX_TITLE_LEN:
            title = title[:MAX_TITLE_LEN]
        try:
            wrote = await _write_title_meta(engine, session_id, title)
        except Exception:
            logger.exception("Failed to write auto title to meta.json")
            activity.exception("write failed: session=%s", session_id)
            wrote = False
            title = ""
        if wrote:
            activity.info("written: session=%s title=%s", session_id, title)
        else:
            activity.info("skip: session=%s already has a title", session_id)
            title = ""
    else:
        activity.warning("generate produced no title: session=%s", session_id)
    if title:
        state.generated = True
        logger.info("Auto title generated: %s", title)

    # 通知宿主（Web 端借此刷新会话列表，使自动命名即时显现）
    on_generated = getattr(engine, "_title_on_generated", None)
    if on_generated is not None:
        try:
            await on_generated(title)
            activity.info(
                "refresh pushed: session=%s title=%s",
                session_id,
                title or "(empty)",
            )
        except Exception:
            logger.exception("Auto title on_generated callback failed")
            activity.exception("refresh callback failed: session=%s", session_id)
    else:
        activity.info("no host callback bound: session=%s", session_id)


async def _write_title_meta(
    engine: Any, session_id: str, title: str
) -> bool:
    """将生成的标题写入会话 meta.json 的 title 字段（保留其余字段）。

    写入前重读 meta：若期间用户已手动重命名（meta.title 非空），则跳过
    覆盖并返回 False，避免自动标题抢占用户命名。

    Args:
        engine: 主会话引擎（提供 cwd）
        session_id: 目标会话 ID
        title: 生成的标题

    Returns:
        bool: 是否实际写入（跳过覆盖时返回 False）
    """
    from illusion.services.session_storage import read_meta, session_dir_for, write_meta

    # 会话目录已不存在（后台生成期间用户删除了该会话）则跳过：直接返回 False，
    # 避免 write_meta 重新创建被删会话的目录与元数据，残留孤立文件。
    session_dir = session_dir_for(engine.cwd, session_id)
    if not session_dir.exists():
        return False
    meta = read_meta(engine.cwd, session_id) or {}
    if meta.get("title"):
        return False
    meta["title"] = title
    meta["updated_at"] = time.time()
    write_meta(engine.cwd, session_id, meta)
    return True