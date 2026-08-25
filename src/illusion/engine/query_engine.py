"""高级对话引擎。

本模块提供高级对话引擎，管理对话历史和工具感知的模型循环。

主要功能：
    - 管理对话历史
    - 执行用户消息提交
    - 支持待续工具调用继续
    - 跟踪令牌使用成本

主要类：
    - QueryEngine: 对话引擎主类

使用示例：
    >>> from illusion.engine import QueryEngine
    >>> engine = QueryEngine(
    ...     api_client=client,
    ...     tool_registry=registry,
    ...     permission_checker=checker,
    ...     cwd=".",
    ...     model="claude-3-opus",
    ...     system_prompt="你是一个助手"
    ... )
    >>> async for event in engine.submit_message("你好"):
    ...     print(event)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

if TYPE_CHECKING:
    from illusion.services.checkpoint_store import CheckpointStore, RestoreResult

logger = logging.getLogger(__name__)

from illusion.api.client import SupportsStreamingMessages
from illusion.api.effort import EffortLevel
from illusion.api.usage import UsageSnapshot
from illusion.engine.cost_tracker import CostTracker
from illusion.engine.messages import ConversationMessage, ToolResultBlock
from illusion.engine.query import (
    AskUserPrompt,
    BackgroundAgentTracker,
    MaxTurnsExceeded,
    PermissionPrompt,
    PlanApprovalPrompt,
    QueryContext,
    run_query,
)
from illusion.engine.stream_events import GoalStatusEvent, StreamEvent
from illusion.goal.manager import GoalManager
from illusion.goal.prompts import render_goal_round_prompt, render_wrapup_context
from illusion.goal.types import BLOCK_CODE_ROUND_LIMIT
from illusion.hooks import HookEvent, HookExecutor
from illusion.permissions.checker import PermissionChecker
from illusion.services.compact import AutoCompactState, estimate_conversation_tokens
from illusion.services.file_history import (
    FileHistoryState,
    make_snapshot,
    track_edit,
)
from illusion.services.file_history import (
    load as _file_history_load,
)
from illusion.tools.base import ToolRegistry
from illusion.utils.file_state_cache import FileStateCache


class QueryEngine:
    """拥有对话历史和工具感知模型循环的高级引擎。

    管理整个对话生命周期，包括消息提交、工具执行、成本跟踪等。

    Attributes:
        messages: 当前对话历史（只读）
        max_turns: 每个用户输入的最大智能体轮次数
        total_usage: 跨所有轮次的总使用量

    使用示例：
        >>> engine = QueryEngine(
        ...     api_client=client,
        ...     tool_registry=registry,
        ...     permission_checker=checker,
        ...     cwd=".",
        ...     model="claude-3-opus",
        ...     system_prompt="你是一个助手"
        ... )
    """

    # 后台标题生成完成回调：Web 宿主按会话注入，None 表示未注入。
    # 标题生成后调用，用于刷新会话列表使自动命名即时显现。
    _title_on_generated: Callable[[str], Awaitable[None]] | None = None

    def __init__(
        self,
        *,
        api_client: SupportsStreamingMessages,
        tool_registry: ToolRegistry,
        permission_checker: PermissionChecker,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        max_tokens: int = 4096,
        max_turns: int | None = 8,
        permission_prompt: PermissionPrompt | None = None,
        ask_user_prompt: AskUserPrompt | None = None,
        plan_approval_prompt: PlanApprovalPrompt | None = None,
        hook_executor: HookExecutor | None = None,
        tool_metadata: dict[str, object] | None = None,
        effort: EffortLevel | None = None,
        session_id: str = "",
        print_mode: bool = False,
        sandbox_permission_prompt: PermissionPrompt | None = None,
        goal_manager: GoalManager | None = None,
    ) -> None:
        self._api_client = api_client  # API客户端
        self._tool_registry = tool_registry  # 工具注册表
        self._permission_checker = permission_checker  # 权限检查器
        self._cwd = Path(cwd).resolve()  # 当前工作目录
        self._model = model  # 模型名称
        self._system_prompt = system_prompt  # 系统提示词
        self._max_tokens = max_tokens  # 最大令牌数
        self._max_turns = max_turns  # 最大轮次
        self._permission_prompt = permission_prompt  # 权限提示回调
        self._ask_user_prompt = ask_user_prompt  # 用户询问回调
        self._plan_approval_prompt = plan_approval_prompt  # 计划审批回调
        self._hook_executor = hook_executor  # 钩子执行器
        self._tool_metadata = tool_metadata or {}  # 工具元数据
        self._effort = effort  # effort 级别
        self._messages: list[ConversationMessage] = []  # 对话消息历史
        self._cost_tracker = CostTracker()  # 成本跟踪器
        # 最后一次 API 调用的真实用量（含缓存分项），None 表示尚未调用或已失效
        self._last_api_usage: UsageSnapshot | None = None
        # last_api_usage 记录时的消息数快照，用于计算"自上次 API 调用以来新增消息"的增量
        self._last_api_usage_message_count: int = 0
        self._bg_agent_tracker = BackgroundAgentTracker()  # 后台代理追踪器
        self._compact_state = AutoCompactState()  # 自动压缩状态（跨会话持久）
        self._file_history: FileHistoryState | None = None  # 文件历史状态
        self._session_id: str = session_id or ""  # 会话 ID（用于文件历史目录）
        self._print_mode: bool = print_mode  # 是否为 print 模式（非交互多轮退出）
        self._sandbox_permission_prompt = sandbox_permission_prompt  # print 模式沙箱两选项回调
        self._file_state_cache = FileStateCache()  # 文件状态缓存（用于读写去重）
        self._checkpoint_store: CheckpointStore | None = None  # 持久化存储
        self._goal_manager = goal_manager  # goal 域管理器（None = 未启用）

    @property
    def effort(self) -> EffortLevel | None:
        """返回当前的 effort 级别。

        Returns:
            EffortLevel | None: 当前的 effort 级别
        """
        return self._effort

    @effort.setter
    def effort(self, value: EffortLevel | None) -> None:
        """设置 effort 级别。

        Args:
            value: 新的 effort 级别
        """
        self._effort = value

    @property
    def max_tokens(self) -> int:
        """返回当前的最大令牌数。

        Returns:
            int: 最大令牌数
        """
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value: int) -> None:
        """设置最大令牌数。

        Args:
            value: 新的最大令牌数
        """
        self._max_tokens = value

    @property
    def messages(self) -> list[ConversationMessage]:
        """返回当前对话历史。

        Returns:
            list[ConversationMessage]: 消息列表的副本
        """
        return list(self._messages)

    @property
    def max_turns(self) -> int | None:
        """返回每个用户输入的最大智能体轮次数（如果有上限）。

        Returns:
            int | None: 最大轮次数或None（无限制）
        """
        return self._max_turns

    @property
    def total_usage(self) -> UsageSnapshot:
        """返回跨所有轮次的总使用量。

        Returns:
            UsageSnapshot: 累积的使用量快照
        """
        return self._cost_tracker.total

    @property
    def system_prompt(self) -> str:
        """返回当前系统提示词。

        Returns:
            str: 当前 system prompt 文本
        """
        return self._system_prompt or ""

    @property
    def api_client(self) -> SupportsStreamingMessages:
        """返回当前 API 客户端（只读）。

        Returns:
            SupportsStreamingMessages: 当前 API 客户端实例
        """
        return self._api_client

    @property
    def model(self) -> str:
        """返回当前模型名（只读）。

        Returns:
            str: 当前模型名称
        """
        return self._model

    @property
    def last_api_usage(self) -> UsageSnapshot | None:
        """返回最后一次 API 调用的真实用量（含缓存分项）。

        压缩后会被清除，直到下一次 API 调用重新填充。

        Returns:
            UsageSnapshot | None: 最后一次调用的用量，None 表示无数据
        """
        return self._last_api_usage

    def invalidate_last_api_usage(self) -> None:
        """清除 last_api_usage 快照（压缩后调用）。

        压缩后压缩前的真实用量已不代表压缩后的上下文，清除后
        current_context_tokens() 回退到纯估算，直到下一次 API 调用
        提供新的真实值。
        """
        self._last_api_usage = None
        self._last_api_usage_message_count = 0

    def current_context_tokens(self) -> int:
        """当前上下文估算 = 最后一次 API 调用的真实 context_size + 新增消息估算。

        与 Claude Code 的 tokenCountWithEstimation() 同思路：真实 usage 为
        基准，新增消息用本地估算补齐，防止低估（低估会导致自动压缩触发
        过晚，API 调用失败）。

        Returns:
            int: 当前上下文占用 token 估算
        """
        if self._last_api_usage is not None:
            new_messages = self._messages[self._last_api_usage_message_count:]
            if new_messages:
                return (
                    self._last_api_usage.context_size
                    + estimate_conversation_tokens(new_messages)
                )
            return self._last_api_usage.context_size
        return estimate_conversation_tokens(self._messages)

    @property
    def tool_registry(self) -> ToolRegistry:
        """返回工具注册表（只读）。

        供外部服务复用 engine 的工具集，无需重复构建。
        """
        return self._tool_registry

    @property
    def permission_checker(self) -> PermissionChecker:
        """返回权限检查器（只读）。

        供外部服务复用 engine 的权限配置。
        """
        return self._permission_checker

    @property
    def cwd(self) -> Path:
        """返回当前工作目录（只读）。"""
        return self._cwd

    @property
    def tool_metadata(self) -> dict[str, object]:
        """返回工具元数据（只读）。"""
        return self._tool_metadata

    @property
    def goal_manager(self) -> GoalManager | None:
        """返回 goal 域管理器（未启用时 None）。"""
        return self._goal_manager

    def goal_status_payload(self) -> dict[str, Any] | None:
        """返回前端状态栏的 goal 视图载荷（无目标时 None）。"""
        if self._goal_manager is None:
            return None
        return self._goal_manager.status_payload()

    def clear(self) -> None:
        """清除内存中的对话历史。

        同时重置成本跟踪器、last_api_usage 和文件状态缓存。
        注意：不清除 _checkpoint_store 和 _session_id，由 full_reset 处理。
        """
        self._messages.clear()
        self._cost_tracker = CostTracker()
        self._last_api_usage = None
        self._last_api_usage_message_count = 0
        self._file_state_cache.clear()

    def set_checkpoint_store(self, store: CheckpointStore | None) -> None:
        """设置或清除 CheckpointStore。

        Deprecated: 新代码应使用 attach_session()（以 store 为唯一权威并
        同步 session_id/file_history/tool_metadata）。本方法仅保留给无会话
        切换语义的初始化，且不校验 store 与 session_id 的一致性。

        Args:
            store: CheckpointStore 实例或 None
        """
        self._checkpoint_store = store

    def attach_session(self, store: CheckpointStore) -> None:
        """原子绑定会话存储：以 store 为会话数据唯一权威。

        同时设置 checkpoint_store / session_id，并重置 file_history
        （旧会话的文件编辑记录不跨会话迁移，否则 /resume 会把当前会话
        的 file_history 写到目标会话目录）。file_history 由后续
        load_file_history 或 submit_message 按新会话目录懒加载/重建。

        所有会话切换点（启动、/new、/resume、web 新建/删除）都应调用
        本方法，而不是分别调用 set_checkpoint_store + set_session_id——
        后者一旦漏调任一，文件就会散落到不同目录。

        Args:
            store: 新会话的 CheckpointStore（延迟创建，目录可尚不存在）
        """
        self._checkpoint_store = store
        self._session_id = store.session_id
        self._file_history = None
        # 新会话无 goal；持久化 goal 由后续 restore → apply_restore 恢复
        # （恢复后 activation 恒为 disarmed，需人类授权 resume 重新武装）
        if self._goal_manager is not None:
            self._goal_manager.reset()
        # 同步 tool_metadata 中的 session_id，供工具上下文
        # （skill_tool / team_create_tool 等经 query.py 展开）读取
        self._tool_metadata["session_id"] = store.session_id

    @property
    def session_id(self) -> str:
        """返回当前会话 ID（由 attach_session / set_session_id 维护）。

        会话数据目录的唯一权威是 CheckpointStore.session_dir，
        本属性仅用于读取当前会话标识（如 bundle.session_id 同步）。

        Returns:
            str: 当前会话 ID，未绑定会话时为空字符串
        """
        return self._session_id

    def set_session_id(self, session_id: str) -> None:
        """更新引擎内部的 session_id（用于 /new、/resume 后同步）。

        Deprecated: 新代码应使用 attach_session()（session_id 由 store 派生，
        杜绝不同步）。本方法保留给外部插件等无法构造 store 的只读场景。

        同时同步已加载的 file_history.session_id，避免 file_history.json
        写入与 session_dir 不匹配的孤立目录。

        Args:
            session_id: 新的会话 ID
        """
        self._session_id = session_id
        if self._file_history is not None and self._file_history.session_id != session_id:
            self._file_history.session_id = session_id
            # 同步会话数据目录（由 checkpoint_store 派生，保持唯一权威）
            if self._checkpoint_store is not None:
                self._file_history.session_dir = self._checkpoint_store.session_dir
            # 若旧 session_id 下已落盘，则按新 session_id 再保存一次，
            # 确保后续 track_edit/rewind_to 写入正确目录
            from illusion.services.file_history import save as _fh_save
            _fh_save(self._file_history)

    def full_reset(self) -> None:
        """完全重置引擎状态（用于 /new）。

        清空消息历史、cost_tracker、last_api_usage、file_history、
        file_state_cache、session_id 和 checkpoint_store。
        同时重置记忆强化状态（_dream_checked 等），使新会话
        重新参与 Auto Dream 会话计数。
        """
        self._messages.clear()
        self._cost_tracker = CostTracker()
        self._last_api_usage = None
        self._last_api_usage_message_count = 0
        self._file_history = None
        self._file_state_cache.clear()
        self._session_id = ""
        self._checkpoint_store = None
        self._dream_checked = False
        self._memory_extract_state = None
        if self._goal_manager is not None:
            self._goal_manager.reset()

    def apply_restore(self, result: RestoreResult) -> None:
        """从 CheckpointStore.restore() 结果恢复所有状态。

        system_prompt 不从持久化恢复——下一轮 handle_line 会通过
        build_runtime_system_prompt 重新构建。
        last_api_usage 从 checkpoint 中的单次分项恢复（若有），
        使 rewind/resume 后 StatusBar / context 显示立即恢复。

        Args:
            result: restore 结果
        """
        self._messages = list(result.messages)
        self._cost_tracker.apply_restore(result)
        # 恢复最后一次 API 调用的单次用量（含缓存分项），
        # 使 rewind/resume 后 StatusBar / context 显示立即恢复
        # （checkpoint 中无该数据时回退到 None → 纯估算）
        self._last_api_usage = result.last_usage
        self._last_api_usage_message_count = result.last_usage_message_count
        # 恢复持久化 goal（恢复后 activation 恒为 disarmed，
        # 人类以任何措辞要求继续时模型应调用 update_goal resume 重新武装）
        if self._goal_manager is not None:
            self._goal_manager.restore_from(result.goal_state)

    def load_file_history(self, checkpoint_count: int | None = None) -> None:
        """显式加载文件历史状态（用于 /resume 后）。

        在 apply_restore 之后调用，确保 /rewind 前状态已就绪。
        若磁盘上无 file_history.json 则保持现有状态不变。
        file_history.json 以 checkpoint_store.session_dir 定位
        （会话目录唯一权威），避免与 context.jsonl 目录不一致。

        Args:
            checkpoint_count: 当前 CheckpointStore.next_checkpoint_id，
                用于崩溃恢复对齐。None 时不做对齐。
        """
        if not self._session_id:
            return
        store = self._checkpoint_store
        session_dir = store.session_dir if store is not None else None
        loaded = _file_history_load(
            str(self._cwd),
            self._session_id,
            checkpoint_count=checkpoint_count,
            session_dir=session_dir,
        )
        if loaded is not None:
            self._file_history = loaded

    @property
    def checkpoint_store(self) -> CheckpointStore | None:
        """返回当前 CheckpointStore。"""
        return self._checkpoint_store

    async def aclose(self) -> None:
        """关闭查询引擎，cancel 所有未完成后台 task。

        调用 BackgroundAgentTracker.shutdown() 取消 pending task，
        并等待最多 5 秒让 task 完成清理，避免 engine 退出后
        wait_for_completion 永久阻塞。
        """
        self._bg_agent_tracker.shutdown()
        await self._bg_agent_tracker.wait_for_completion(timeout=5.0)

    def set_system_prompt(self, prompt: str) -> None:
        """更新未来轮次的活跃系统提示词。

        Args:
            prompt: 新的系统提示词
        """
        self._system_prompt = prompt

    def set_model(self, model: str) -> None:
        """更新未来轮次的活跃模型。

        Args:
            model: 新的模型名称
        """
        self._model = model

    def set_api_client(self, api_client: SupportsStreamingMessages) -> None:
        """更新未来轮次的活跃API客户端。

        Args:
            api_client: 新的API客户端
        """
        self._api_client = api_client

    def set_max_turns(self, max_turns: int | None) -> None:
        """更新每个用户输入的最大智能体轮次数。

        Args:
            max_turns: 最大轮次数，None表示无限制
        """
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def set_permission_checker(self, checker: PermissionChecker) -> None:
        """更新未来轮次的活跃权限检查器。

        Args:
            checker: 新的权限检查器
        """
        self._permission_checker = checker

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        """替换内存中的对话历史。

        Args:
            messages: 新的消息列表
        """
        self._messages = list(messages)

    @property
    def file_history(self) -> FileHistoryState | None:
        """返回文件历史状态。"""
        return self._file_history

    def _ensure_file_history(self) -> None:
        """懒初始化文件历史状态（load 优先，无则新建）。

        会话目录以 checkpoint_store 为唯一权威（session_id/session_dir
        均由 store 派生），file_history.json 与 context.jsonl 必然同目录。
        submit_message 与 drive_goal_rounds（命令优先会话）共用。

        session_id 为空时不初始化，避免写入随机 id 的孤立目录。
        """
        if self._file_history is not None:
            return
        store = self._checkpoint_store
        sid = store.session_id if store is not None else self._session_id
        session_dir = store.session_dir if store is not None else None
        if not sid:
            return
        loaded = _file_history_load(str(self._cwd), sid, session_dir=session_dir)
        self._file_history = (
            loaded
            if loaded is not None
            else FileHistoryState(
                session_id=sid,
                cwd=str(self._cwd),
                session_dir=session_dir,
            )
        )

    def on_before_tool_execute(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """工具执行前回调：备份即将被修改的文件（copy-on-write）。

        供主引擎和子 agent 共用：子 agent 通过 QueryContext 继承此回调，
        其文件修改也会备份到主 engine 的 file_history，确保 rewind 能覆盖
        子 agent 的修改。

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数
        """
        if self._file_history is None:
            return
        # 跳过只读工具（如 grep、glob），它们不会修改文件
        tool = self._tool_registry.get(tool_name)
        if tool is not None:
            try:
                parsed_input = tool.input_model.model_validate(tool_input)
                if tool.is_read_only(parsed_input):
                    return
            except ValidationError:
                pass
        for fpath in self._extract_file_paths(tool_name, tool_input):
            track_edit(self._file_history, fpath)

    def _extract_file_paths(self, tool_name: str, tool_input: dict[str, Any]) -> list[str]:
        """从工具输入中提取文件路径。"""
        path_keys = ("path", "file_path", "notebook_path")
        paths = []
        for key in path_keys:
            if key in tool_input and isinstance(tool_input[key], str):
                paths.append(tool_input[key])
        return paths

    def has_pending_continuation(self) -> bool:
        """当对话以等待后续模型轮次的工具结果结束时返回True。

        用于检查是否有待续的工具调用需要继续执行。

        Returns:
            bool: 是否有待续的继续
        """
        if not self._messages:
            return False
        last = self._messages[-1]
        if last.role != "user":
            return False
        if not any(isinstance(block, ToolResultBlock) for block in last.content):
            return False
        for msg in reversed(self._messages[:-1]):
            if msg.role != "assistant":
                continue
            return bool(msg.tool_uses)
        return False

    async def submit_message(self, prompt: str) -> AsyncIterator[StreamEvent]:
        """追加用户消息并执行查询循环。

        Args:
            prompt: 用户输入的提示词

        Yields:
            StreamEvent: 流式事件

        使用示例：
            >>> async for event in engine.submit_message("你好"):
            ...     print(event)
        """
        # system_prompt 不再持久化：system_prompt 不含 tools 描述，
        # hash 无法完整代表系统级开销变化。改为每轮反推自校正
        # （update_from_usage 无条件覆盖），resume 后用持久化的 overhead
        # 值显示，第一轮 API 调用后用实测值覆盖。
        # append checkpoint 到 JSONL（替代旧 push_checkpoint）
        checkpoint_id = 0
        if self._checkpoint_store is not None:
            checkpoint_id = await self._checkpoint_store.append_checkpoint()
        # 初始化文件历史状态（load 优先，无则新建）
        # 会话目录以 checkpoint_store 为唯一权威（session_id/session_dir
        # 均由 store 派生），file_history.json 与 context.jsonl 必然同目录，
        # 不再依赖 runtime 手动同步 session_id。
        self._ensure_file_history()

        # 将用户文本转换为消息并添加到历史记录
        self._messages.append(ConversationMessage.from_user_text(prompt))
        # 持久化 user message
        if self._checkpoint_store is not None:
            await self._checkpoint_store.append_message(self._messages[-1])

        # 人类直接输入：goal 权威来源切换为 human
        if self._goal_manager is not None:
            self._goal_manager.current_source = "human"

        # 执行 UserPromptSubmit 钩子
        if self._hook_executor is not None:
            ups_result = await self._hook_executor.execute(
                HookEvent.USER_PROMPT_SUBMIT,
                {"prompt": prompt},
            )
            # 阻止处理
            if ups_result.blocked:
                from illusion.hooks.utils import wrap_in_system_reminder
                error_msg = ups_result.reason or "UserPromptSubmit hook blocked"
                self._messages.append(ConversationMessage.from_user_text(
                    wrap_in_system_reminder(f"Hook blocked: {error_msg}")
                ))
                return
            # preventContinuation
            if ups_result.prevent_continuation:
                return
            # 注入 additionalContext
            for ctx in ups_result.additional_contexts:
                if ctx:
                    from illusion.hooks.utils import wrap_in_system_reminder
                    self._messages.append(ConversationMessage.from_user_text(
                        wrap_in_system_reminder(ctx)
                    ))

        # 为这条用户消息创建文件历史快照（用消息列表长度作为 ID）
        # 仅当 file_history 已初始化（session_id 可用）时才创建快照
        if self._file_history is not None:
            make_snapshot(self._file_history, str(len(self._messages)), checkpoint_id)

        # 文件历史回调：工具执行前备份文件（使用方法，供子 agent 继承复用）
        context = self._build_query_context()
        # 记录循环前的消息数量，用于循环结束后持久化新增消息
        # run_query 内部会直接 append assistant/tool 消息到 self._messages，
        # 这些消息必须持久化，否则 resume 后只看到用户消息，丢失 LLM 回复
        messages_before = len(self._messages)
        try:
            async for event, usage in run_query(context, self._messages):
                if usage is not None:
                    await self._track_usage(usage)
                yield event
        finally:
            await self._persist_checkpoint_after_run(context, messages_before)
        # 同步工具导致的 CWD 变更（如 enter/exit_worktree）
        if context.cwd != self._cwd:
            self._cwd = context.cwd

        # Goal 轮次驱动（空闲边界自动续跑）
        async for event in self.drive_goal_rounds():
            yield event

        # 记忆强化
        # 1. 首次回合触发 Auto Dream 会话计数/整合检查
        # 2. 每轮回合结束后调度后台记忆提取（不阻塞主循环）
        try:
            from illusion.memory.auto_dream import record_session_start
            from illusion.memory.extract import maybe_schedule_extract

            if not getattr(self, "_dream_checked", False):
                self._dream_checked = True
                record_session_start(self)
            maybe_schedule_extract(self)
        except Exception:
            logger.exception("Memory reinforcement scheduling failed")

        # 会话自动标题：回合结束后后台生成简洁标题（不阻塞主循环）
        try:
            from illusion.title.auto_title import maybe_schedule_title

            maybe_schedule_title(self)
        except Exception:
            logger.exception("Auto title scheduling failed")

    def _build_query_context(self, *, max_turns: int | None = None) -> QueryContext:
        """以引擎当前状态构建 QueryContext（三条执行路径共享）。"""
        return QueryContext(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            permission_checker=self._permission_checker,
            cwd=self._cwd,
            model=self._model,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
            max_turns=max_turns if max_turns is not None else self._max_turns,
            permission_prompt=self._permission_prompt,
            ask_user_prompt=self._ask_user_prompt,
            plan_approval_prompt=self._plan_approval_prompt,
            print_mode=self._print_mode,
            sandbox_permission_prompt=self._sandbox_permission_prompt,
            hook_executor=self._hook_executor,
            tool_metadata=self._tool_metadata,
            effort=self._effort,
            bg_agent_tracker=self._bg_agent_tracker,
            # idle 超时阈值：后台 agent 持续有活动（工具调用、文本生成）时
            # 主循环保持 busy；仅当 300s 无任何活动才退出 busy（agent 仍存活，
            # 下轮 handle_line 续接）。与前台 IDLE_TIMEOUT 一致。
            bg_agent_wait_timeout=300.0,
            compact_state=self._compact_state,
            last_api_usage=self._last_api_usage,
            last_api_usage_message_count=self._last_api_usage_message_count,
            on_before_tool_execute=self.on_before_tool_execute,
            file_state_cache=self._file_state_cache,
        )

    async def _track_usage(self, usage: UsageSnapshot) -> None:
        """记录一次 API 调用用量：累加、快照、持久化。"""
        self._cost_tracker.add(usage)
        # 记录最后一次 API 调用的真实用量（含缓存分项）及消息数快照
        self._last_api_usage = usage
        self._last_api_usage_message_count = len(self._messages)
        # 持久化累积 usage + 最后一次调用的单次分项
        # （单次分项用于 rewind/resume 后恢复 StatusBar 显示）
        if self._checkpoint_store is not None:
            await self._checkpoint_store.append_usage(
                input_tokens=self._cost_tracker.total.input_tokens,
                output_tokens=self._cost_tracker.total.output_tokens,
                cache_read_input_tokens=self._cost_tracker.total.cache_read_input_tokens,
                cache_creation_input_tokens=self._cost_tracker.total.cache_creation_input_tokens,
                last_usage=usage,
                last_message_count=len(self._messages),
            )

    async def _append_injected_user_message(self, text: str) -> None:
        """追加一条 harness 注入的 user 消息并持久化（goal round / wrap-up）。"""
        message = ConversationMessage.from_user_text(text)
        self._messages.append(message)
        if self._checkpoint_store is not None:
            await self._checkpoint_store.append_message(message)

    async def record_goal_command(self, text: str) -> None:
        """将 /goal 命令原文作为真实 user 消息追加并持久化。

        命令本身由 handle_line 命令分支执行（不进 engine.messages），但
        /goal 创建目标时用户意图蕴含在命令原文里，需作为首条可见消息：
        - 前端实时转录与重放均按 user 消息渲染
        - 自动标题生成（_user_messages）能捕获它作为标题素材
        - 轮次统计 / 会话摘要 / rewind 将其视为真实一轮

        rewind 一致性：命令路径不经过 submit_message 的 checkpoint 边界，
        这里显式 append_checkpoint，使 rewind 能按轮次回退到 /goal 之前
        （否则 checkpoints 只覆盖普通消息轮，回退会越过 /goal 直接删到
        第一条普通消息）。

        Args:
            text: 用户输入的 /goal 命令原文
        """
        if self._checkpoint_store is not None:
            await self._checkpoint_store.append_checkpoint()
        await self._append_injected_user_message(text)

    async def _flush_goal_state(self, *, force: bool = False) -> None:
        """将 goal 状态以 last-wins 行写入 checkpoint。

        压缩会重建 context.jsonl（rebuild_after_compact 丢弃非消息行），
        因此压缩后即使无变更也强制补写一次。
        """
        manager = self._goal_manager
        if manager is None or self._checkpoint_store is None:
            return
        if manager.dirty or (force and manager.snapshot is not None):
            await self._checkpoint_store.append_goal(manager.persisted_state())

    async def drive_goal_rounds(self) -> AsyncIterator[StreamEvent]:
        """Goal 轮次驱动器。

        在空闲边界（每轮 run_query 结束后）检查 goal 状态：
            1. 有待注入的终态 wrap-up → 注入 <goal_complete>/<goal_blocked>
               消息跑一轮收尾，随后停止（终态）。
            2. goal 为 active + armed 且未达轮次上限 → 准入下一轮，注入
               <goal_round> 消息继续执行，回到 1。
            3. 轮次耗尽 → block('round-limit') 并停止。
            4. MaxTurnsExceeded / 异常 → disarm 并停止（max-tokens/错误
               时驱动器停摆，等待人类 resume 重新武装）。
        """
        manager = self._goal_manager
        if manager is None:
            return
        # 命令优先会话（如首条消息为 /goal）从未经过 submit_message 创建
        # 快照边界：补齐 file_history 初始化与初始快照，保证 goal 轮次内的
        # 文件修改可被 /rewind 跟踪，且 file_history.json 与会话目录同步生成。
        # 已有快照（普通消息会话）时不重复创建。
        # 快照复用最近一次 checkpoint（record_goal_command 已为 /goal 轮
        # append 过），不再额外追加——否则 checkpoint 数 > 用户可见轮数，
        # rewind 的 turns 计数整体偏移，回退第一条消息需两次才能清空。
        self._ensure_file_history()
        if self._file_history is not None and not self._file_history.snapshots:
            checkpoint_id = (
                max(0, self._checkpoint_store.next_checkpoint_id - 1)
                if self._checkpoint_store is not None
                else 0
            )
            make_snapshot(self._file_history, str(len(self._messages)), checkpoint_id)
        while True:
            # 1) 终态 wrap-up 优先注入
            wrapup = manager.take_pending_wrapup()
            if wrapup is not None:
                text = render_wrapup_context(wrapup.objective, wrapup.blocked_reason)
                yield GoalStatusEvent(kind="wrapup", phase=wrapup.kind)
                await self._append_injected_user_message(text)
                context = self._build_query_context()
                messages_before = len(self._messages)
                try:
                    async for event, usage in run_query(context, self._messages):
                        if usage is not None:
                            await self._track_usage(usage)
                        yield event
                finally:
                    await self._persist_checkpoint_after_run(context, messages_before)
                if context.cwd != self._cwd:
                    self._cwd = context.cwd
                return

            # 2) 轮次耗尽：block('round-limit') 并停止
            snap = manager.snapshot
            if (
                snap is not None
                and snap.phase == "active"
                and manager.activation == "armed"
                and manager.rounds_started >= snap.max_goal_rounds
            ):
                manager.block(
                    None,
                    None,
                    code=BLOCK_CODE_ROUND_LIMIT,
                    message=(
                        f"Goal round limit reached (max {snap.max_goal_rounds} rounds); "
                        "goal auto-paused"
                    ),
                )
                await self._flush_goal_state()
                yield GoalStatusEvent(kind="limit", max_rounds=snap.max_goal_rounds)
                return

            # 3) 轮次准入检查
            if not manager.should_continue():
                return
            round_no = manager.admit_round()
            if round_no is None:
                # 并发路径下已被 block（admit_round 内部 round-limit）
                await self._flush_goal_state()
                yield GoalStatusEvent(
                    kind="limit",
                    max_rounds=snap.max_goal_rounds if snap else None,
                )
                return
            snap = manager.snapshot
            assert snap is not None  # admit_round 成功 implies 快照存在
            prompt = render_goal_round_prompt(
                snap.objective,
                round_no,
                snap.max_goal_rounds,
                goal_id=snap.id,
                revision=snap.revision,
            )
            manager.current_source = "goal"
            await self._append_injected_user_message(prompt)
            yield GoalStatusEvent(kind="round", round=round_no, max_rounds=snap.max_goal_rounds)
            context = self._build_query_context()
            messages_before = len(self._messages)
            try:
                async for event, usage in run_query(context, self._messages):
                    if usage is not None:
                        await self._track_usage(usage)
                    yield event
            except MaxTurnsExceeded:
                manager.disarm()
                yield GoalStatusEvent(kind="disarmed")
                return
            finally:
                await self._persist_checkpoint_after_run(context, messages_before)
            if context.cwd != self._cwd:
                self._cwd = context.cwd
            # 回到 1：检查 wrap-up / 下一轮

    async def _persist_checkpoint_after_run(
        self, context: QueryContext, messages_before: int
    ) -> None:
        """run_query 结束后同步 checkpoint。

        压缩是不可逆操作：若 run_query 内发生过压缩，重建 checkpoint
        为压缩后的消息（否则 resume/rewind 会恢复到压缩前的完整历史）；
        否则追加 run_query 期间新增的消息。压缩后若有后续 API 调用，
        保留其真实单次分项，resume 后状态栏立即恢复；若无（last_usage
        仍是压缩前旧值），回退估算。

        Args:
            context: 本次 run_query 的 QueryContext
            messages_before: run_query 开始时的消息数
        """
        # 同步压缩后的消息列表（full compact 后 messages 指向新列表）
        if context.final_messages is not None and context.final_messages is not self._messages:
            self._messages = context.final_messages
        if self._checkpoint_store is None:
            return
        if context.compacted:
            # 压缩后是否有后续 API 调用：有则保留其真实单次分项
            if (
                self._last_api_usage is not None
                and self._last_api_usage_message_count
                >= context.compacted_message_count
            ):
                last_usage = self._last_api_usage
                last_count = self._last_api_usage_message_count
            else:
                last_usage = None
                last_count = 0
            await self._checkpoint_store.rebuild_after_compact(
                self._messages,
                usage_input=self._cost_tracker.total.input_tokens,
                usage_output=self._cost_tracker.total.output_tokens,
                usage_cache_read=self._cost_tracker.total.cache_read_input_tokens,
                usage_cache_creation=self._cost_tracker.total.cache_creation_input_tokens,
                last_usage=last_usage,
                last_message_count=last_count,
            )
        else:
            # 持久化 run_query 期间新增的所有消息（assistant 回复、tool 结果、
            # hook 注入的 user 消息等）。使用 finally 确保即使异常/中断也能
            # 保存已生成的消息，避免 resume 后对话历史缺失。
            for msg in self._messages[messages_before:]:
                await self._checkpoint_store.append_message(msg)
        # goal 状态落盘：压缩重建会丢弃非消息行，需强制补写
        await self._flush_goal_state(force=context.compacted)

    async def continue_pending(self, *, max_turns: int | None = None) -> AsyncIterator[StreamEvent]:
        """继续被中断的工具循环，而不追加新的用户消息。

        用于恢复之前因工具执行而中断的对话。

        Args:
            max_turns: 轮次数（可选，默认使用引擎设置）

        Yields:
            StreamEvent: 流式事件
        """
        context = self._build_query_context(
            max_turns=max_turns if max_turns is not None else self._max_turns
        )
        # 记录循环前的消息数量，用于循环结束后持久化新增消息
        # continue_pending 不 append checkpoint，但 run_query 内部仍会
        # append assistant/tool 消息，需要持久化以支持 resume
        messages_before = len(self._messages)
        try:
            async for event, usage in run_query(context, self._messages):
                if usage is not None:
                    await self._track_usage(usage)
                yield event
        finally:
            await self._persist_checkpoint_after_run(context, messages_before)
        # 同步工具导致的 CWD 变更（如 enter/exit_worktree）
        if context.cwd != self._cwd:
            self._cwd = context.cwd
        # Goal 轮次驱动（被打断的 goal 轮恢复后继续）
        async for event in self.drive_goal_rounds():
            yield event

    async def process_background_completions(self) -> AsyncIterator[StreamEvent]:
        """处理积压的后台完成通知（自动进入 busy），不新增用户输入。

        主循环空闲期间（如后台任务等待的 idle 超时或用户主动退出）后台任务
        完成时，通知堆积在 BackgroundAgentTracker。本方法先 drain 这些通知
        注入为 user 消息，再让模型继续处理；期间若又派发新后台任务，
        run_query 的 wait_for_completion 保持原有续接语义。

        Yields:
            StreamEvent: 流式事件
        """
        context = self._build_query_context()
        # 记录循环前的消息数量，用于循环结束后持久化新增消息
        # process_background_completions 不 append checkpoint，run_query 内部
        # 注入的通知与 assistant/tool 消息由 _persist_checkpoint_after_run 兜底持久化
        messages_before = len(self._messages)
        try:
            async for event, usage in run_query(context, self._messages, bg_auto_drain=True):
                if usage is not None:
                    await self._track_usage(usage)
                yield event
        finally:
            await self._persist_checkpoint_after_run(context, messages_before)
        # 同步工具导致的 CWD 变更（如 enter/exit_worktree）
        if context.cwd != self._cwd:
            self._cwd = context.cwd
        # Goal 轮次驱动（后台通知处理完毕后，激活的 goal 继续续跑）
        async for event in self.drive_goal_rounds():
            yield event
