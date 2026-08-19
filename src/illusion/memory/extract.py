"""
后台记忆提取模块
==============

每轮对话结束后，在后台运行一个
受限子代理分析最近对话，主动将值得保存的内容写入记忆系统。

设计要点：
    - 游标追踪：只分析上次提取后的新消息
    - 节流控制：每 N 轮运行一次（settings.memory.extract_interval）
    - 互斥机制：主代理本轮已写过记忆文件（目录 mtime 变化）则跳过，
      避免重复劳动
    - 工具限制：只读工具（read/grep/glob）+ 记忆目录内的写工具
    - 最大轮次：5 turns，防止过度探索
    - 异常隔离：任何失败只记日志，不影响主会话

函数说明：
    - MemoryExtractState: 提取状态（游标/节流/快照/并发锁）
    - maybe_schedule_extract: 回合结束后调用，判断并调度后台提取
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from illusion.api.effort import EffortLevel
from illusion.engine.messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from illusion.memory.log import get_memory_logger, truncate
from illusion.memory.paths import get_memory_dir_for_cwd
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from illusion.tools.file_edit_tool import FileEditTool
from illusion.tools.file_read_tool import FileReadTool
from illusion.tools.file_write_tool import FileWriteTool
from illusion.tools.glob_tool import GlobTool
from illusion.tools.grep_tool import GrepTool

logger = logging.getLogger(__name__)

# 对齐 Claude Code extractMemories 的限制
MAX_EXTRACT_TURNS = 20  # 最大轮次：记忆持续增长，需要更多轮次完成读取+写入
MAX_EXTRACT_MESSAGES = 20  # 最多分析最近 N 条消息


def _memory_extract_prompt(message_count: int, memory_dir: Path) -> str:
    """构建提取子代理提示词（注入记忆目录与 frontmatter 格式）。"""
    return (
        "You are now acting as the memory extraction subagent.\n"
        f"Analyze the most recent ~{message_count} messages above and use them to "
        "update your persistent memory systems.\n"
        f"The memory directory is: {memory_dir}\n"
        "CRITICAL: Your entire task is confined to this memory directory. Read ONLY "
        "files inside it, and write/edit files ONLY inside it (access elsewhere is "
        "rejected). Do NOT read project source code or tests to 'verify' memories — "
        "that is not your job.\n"
        "Follow the memory system instructions you have been given: save durable facts "
        "about the user, their preferences, corrections, and project context that will "
        "be useful in future conversations — but do NOT save ephemeral task details, "
        "code patterns derivable from the project, or anything already in memory.\n"
        "Write each memory to its own file inside the type-specific subdirectory "
        "matching its `type` field (e.g. `user/user_role.md`, "
        "`feedback/feedback_testing.md`, `project/project_plan.md`, "
        "`reference/reference_linear.md`), with YAML frontmatter:\n"
        "```markdown\n"
        "---\n"
        "name: short-slug\n"
        "description: one-line summary for relevance matching\n"
        "type: user|feedback|project|reference\n"
        "---\n"
        "content\n"
        "```\n"
        "Update the MEMORY.md index when you add or change memory files — each index "
        "entry is one line: `- [Title](user/user_role.md) — one-line hook` (path "
        "relative to the memory directory, including the type subdirectory).\n"
        "If nothing is worth saving, respond with 'Nothing to save.' and stop."
    )


def _snapshot_memory_dir(memory_dir: Path) -> dict[str, float]:
    """记录记忆目录中所有 .md 文件的 mtime 快照（含类型子目录）。

    用于互斥检测：快照变化意味着主代理（或其他进程）已写入记忆，
    后台提取可跳过本轮。
    """
    snapshot: dict[str, float] = {}
    for path in memory_dir.rglob("*.md"):
        if path.is_file():
            snapshot[str(path.relative_to(memory_dir)).replace("\\", "/")] = path.stat().st_mtime
    return snapshot


def _render_message(msg: ConversationMessage) -> str:
    """将对话消息渲染为文本，供提取子代理分析。

    注意：不包含 ThinkingBlock（思维链不外泄到子代理上下文）。
    """
    parts: list[str] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            parts.append(f"[tool: {block.name}({str(block.input)[:500]})]")
        elif isinstance(block, ToolResultBlock):
            parts.append(f"[result: {block.text_content[:1000]}]")
    text = "\n".join(part for part in parts if part.strip())
    if not text:
        text = f"[{msg.role}: (no text content)]"
    return f"<{msg.role}>\n{text}"


class _MemoryScopedTool(BaseTool[Any]):
    """限制工具作用域在记忆目录内的包装器。

    拦截工具的 execute，校验路径类参数位于记忆目录内：
        - path_fields: 路径字段（如 file_path / path），必须位于记忆目录内
        - root_fields: 根目录字段（如 root），为空时强制为记忆目录，
          非空时必须位于记忆目录内

    用于限制提取/整合子代理只能读取和写入记忆目录（防止子代理
    跑去读项目源码做"验证"，跑偏整合任务）。
    """

    def __init__(
        self,
        inner: BaseTool[Any],
        memory_dir: Path,
        *,
        path_fields: tuple[str, ...] = (),
        root_fields: tuple[str, ...] = (),
    ) -> None:
        self.name = inner.name
        self.description = inner.description
        self.input_model = inner.input_model
        self._inner = inner
        self._memory_dir = Path(memory_dir).resolve()
        self._path_fields = path_fields
        self._root_fields = root_fields

    def is_read_only(self, arguments: Any) -> bool:
        return self._inner.is_read_only(arguments)

    def to_api_schema(self) -> dict[str, Any]:
        return self._inner.to_api_schema()

    def _resolve(self, raw: str, cwd: Path) -> Path:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (cwd / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate

    async def execute(self, arguments: Any, context: ToolExecutionContext) -> ToolResult:
        # 路径字段：必须位于记忆目录内
        for field in self._path_fields:
            value = getattr(arguments, field, None)
            if value:
                candidate = self._resolve(str(value), context.cwd)
                try:
                    candidate.relative_to(self._memory_dir)
                except ValueError:
                    return ToolResult(
                        output=(
                            f"Permission denied: can only access inside the memory "
                            f"directory {self._memory_dir}"
                        ),
                        is_error=True,
                    )
        # 根目录字段：为空强制为记忆目录；非空必须位于记忆目录内
        for field in self._root_fields:
            value = getattr(arguments, field, None)
            if value:
                candidate = self._resolve(str(value), context.cwd)
                try:
                    candidate.relative_to(self._memory_dir)
                except ValueError:
                    return ToolResult(
                        output=(
                            f"Permission denied: can only access inside the memory "
                            f"directory {self._memory_dir}"
                        ),
                        is_error=True,
                    )
            else:
                arguments = arguments.model_copy(update={field: str(self._memory_dir)})
        return await self._inner.execute(arguments, context)


def build_extract_tool_registry(memory_dir: Path) -> ToolRegistry:
    """构建提取子代理的受限工具注册表。

    允许：read_file / glob / grep（仅记忆目录内）+ 记忆目录内的
    write_file / edit_file。拒绝：bash、web、权限修改等一切其他工具。
    读工具同样限定在记忆目录内：子代理只应处理记忆，不应读取项目源码。
    """
    registry = ToolRegistry()
    registry.register(_MemoryScopedTool(FileReadTool(), memory_dir, path_fields=("file_path",)))
    registry.register(_MemoryScopedTool(GlobTool(), memory_dir, root_fields=("root",)))
    registry.register(_MemoryScopedTool(GrepTool(), memory_dir, path_fields=("path",)))
    registry.register(_MemoryScopedTool(FileWriteTool(), memory_dir, path_fields=("file_path",)))
    registry.register(_MemoryScopedTool(FileEditTool(), memory_dir, path_fields=("file_path",)))
    return registry


class MemoryExtractState:
    """后台提取状态（绑定单个 QueryEngine 实例）。

    Attributes:
        cwd: 工作目录
        last_extracted_index: 游标——上次提取到的消息位置
        turns_since_extract: 距上次提取的轮数（节流）
        snapshot: 上次提取/检查时的记忆目录 mtime 快照
        running: 提取任务是否正在运行（并发锁）
    """

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = str(cwd)
        self.last_extracted_index = 0
        self.turns_since_extract = 0
        self.snapshot: dict[str, float] | None = None
        self.running = False


def maybe_schedule_extract(engine: Any) -> None:
    """回合结束后调用：判断并调度后台记忆提取。

    判断链（全部通过才启动）：
        0. 后台自动提取开关（settings.memory.auto_extract，关闭则纯手动模式）
        1. 记忆功能启用（settings.memory.enabled + 权限）
        2. 节流：距上次提取 >= extract_interval 轮
        3. 互斥：记忆目录 mtime 快照未变化（主代理本轮未写记忆）
        4. 游标：有新消息可分析

    Args:
        engine: QueryEngine 实例（需要 _messages、api_client 等属性）
    """
    try:
        from illusion.config.settings import load_settings
        from illusion.memory.manager import is_memory_enabled

        # 子代理守卫：提取/整合/标题生成子代理自身是 QueryEngine，其回合结束
        # 不得再触发提取，防止无限级联
        if getattr(engine, "_is_memory_subagent", False) or getattr(
            engine, "_is_title_subagent", False
        ):
            return
        if not is_memory_enabled(engine.cwd):
            return
        settings = load_settings()
        # 手动模式：允许记忆但禁用后台 LLM 提取（用户显式要求时由
        # 主对话 LLM 直接 Write/Edit 记忆文件，不额外调用子代理）
        if not settings.memory.auto_extract:
            return
        interval = max(1, settings.memory.extract_interval)

        state = getattr(engine, "_memory_extract_state", None)
        if state is None:
            state = MemoryExtractState(engine.cwd)
            engine._memory_extract_state = state

        # 节流计数
        state.turns_since_extract += 1
        if state.turns_since_extract < interval:
            return

        messages = engine.messages
        # 自动压缩后消息列表缩短，游标必然越界 → 复位游标重新全量分析
        if len(messages) < state.last_extracted_index:
            state.last_extracted_index = 0
        if len(messages) <= state.last_extracted_index:
            return
        if state.running:
            return

        # 互斥：记忆目录 mtime 变化 → 主代理本轮已写记忆，
        # 跳过该区间并推进游标（避免下一轮重复分析已处理的段落）
        memory_dir = get_memory_dir_for_cwd(engine.cwd)
        current_snapshot = _snapshot_memory_dir(memory_dir)
        if state.snapshot is not None and current_snapshot != state.snapshot:
            state.snapshot = current_snapshot
            state.last_extracted_index = len(messages)
            return

        # 通过所有检查，调度后台提取
        state.turns_since_extract = 0
        state.running = True
        messages_snapshot = list(messages)
        asyncio.create_task(_run_extract_task(engine, messages_snapshot, memory_dir, state))
    except Exception:
        logger.exception("Failed to schedule memory extraction")


async def _run_extract_task(
    engine: Any,
    messages_snapshot: list[ConversationMessage],
    memory_dir: Path,
    state: MemoryExtractState,
) -> None:
    """后台执行记忆提取（受限子代理，最多 20 turns）。

    无感保证：任务首个 await 前先让出当前事件循环 tick（sleep(0) 非延迟），
    确保调用方（行任务收尾、busy 释放、line_complete 推送）先完成；
    之后的同步初始化（logger 首次创建、client 构建）不再阻塞任何收尾路径。
    子代理的事件流在此处被直接消费丢弃，不会渲染到主对话。活动通过
    ~/.illusion/logs/memory_extract.log 透明记录。
    """
    # 让出当前 tick：仅让事件循环先完成调用方剩余同步收尾，无实际等待
    await asyncio.sleep(0)
    activity = get_memory_logger("extract")
    success = False
    try:
        # 只分析游标之后的新消息，最多取最近 N 条
        recent = messages_snapshot[state.last_extracted_index :]
        if len(recent) > MAX_EXTRACT_MESSAGES:
            recent = recent[-MAX_EXTRACT_MESSAGES:]

        transcript = "\n\n".join(_render_message(m) for m in recent)
        prompt = _memory_extract_prompt(len(recent), memory_dir)
        sub_messages = [ConversationMessage.from_user_text(transcript)]
        activity.info(
            "Memory extraction started: analyzing %d new messages (cursor=%d)",
            len(recent),
            state.last_extracted_index,
        )

        registry = build_extract_tool_registry(memory_dir)
        # 后台提取不应打扰用户：工具注册表已限制为只读 + 记忆目录内写入，
        # 因此子代理使用 FULL_AUTO 权限模式（无需用户确认）。
        from illusion.config.settings import PermissionSettings
        from illusion.permissions.checker import PermissionChecker
        from illusion.permissions.modes import PermissionMode

        auto_checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
        # 解析配置的提取模型：未指定（None）则继承当前引擎模型。
        # 指定了其他 env 的模型时，按该 env 的端点/凭据独立构建 client
        # （跨环境：不能复用主对话 client，否则模型不被当前 provider 支持）。
        # client 与 model 必须原子选择：构建失败时 model 同步回退当前模型，
        # 否则用主 client 调另一 provider 的模型必然 400（回退机制名存实亡）。
        from illusion.config.settings import load_settings

        settings = load_settings()
        env_key, configured_model = settings.resolve_model_ref_with_env(
            settings.memory.extract_model
        )
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
                "You are a background memory extraction subagent. You analyze "
                "conversation transcripts and save durable, useful facts to a "
                "file-based memory system. You may only read files and write "
                "inside the memory directory."
            ),
            max_tokens=2048,
            max_turns=MAX_EXTRACT_TURNS,
            # 思考强度固定 high：不继承主会话（记忆提取质量优先）
            effort=EffortLevel.HIGH,
        )
        # 子代理守卫标记：其回合结束不得再触发提取/整合（防级联）
        sub_engine._is_memory_subagent = True
        sub_engine.load_messages(sub_messages)
        # 消费事件流（不向用户展示）：同时收集活动写入透明日志
        from illusion.engine.stream_events import (
            AssistantTurnComplete,
            ErrorEvent,
            ToolExecutionCompleted,
            ToolExecutionStarted,
        )

        async for event in sub_engine.submit_message(prompt):
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
        activity.info("Memory extraction finished")
    except asyncio.CancelledError:
        logger.info("Memory extraction cancelled")
        activity.warning("Memory extraction cancelled")
    except Exception:
        logger.exception("Memory extraction failed")
        activity.exception("Memory extraction failed")
    finally:
        if success:
            # 成功才推进游标（对齐 Claude Code：失败时游标保留，下次重试）
            state.last_extracted_index = len(messages_snapshot)
        state.snapshot = _snapshot_memory_dir(memory_dir)
        state.running = False
