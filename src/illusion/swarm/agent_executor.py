"""
代理执行器模块
==============

本模块提供子代理派发和执行的核心功能。

主要组件：
    - AgentExecutionContext: 代理运行时上下文
    - AgentAbortController: 代理中止控制器
    - TaskNotification: 任务通知数据类
    - run_agent_in_process: 进程内代理执行
    - run_agent_subprocess: 子进程代理执行
    - resolve_agent_tools: 根据代理定义组装工具池
    - format_task_notification / parse_task_notification: XML 序列化

架构概述：
    代理通过 AgentTool 派发，分为同步（前台）和异步（后台）两种模式。
    同步模式直接返回代理最终文本；异步模式通过 task-notification XML 通知完成。
    代理间通信通过内存中的 asyncio.Queue 实现。

使用示例：
    >>> from illusion.swarm.agent_executor import run_agent_in_process, AgentSpawnConfig
    >>> config = AgentSpawnConfig(...)
    >>> result = await run_agent_in_process(config, query_context)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shlex
import shutil
import sys
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from illusion.coordinator.agent_definitions import AgentDefinition
from illusion.engine.messages import ConversationMessage
from illusion.tasks.manager import get_task_manager
from illusion.tools.base import ToolRegistry
from illusion.utils.aioqueue import Queue, QueueShutDown
from illusion.utils.file_state_cache import FileStateCache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 代理中止控制器
# ---------------------------------------------------------------------------


class AgentAbortController:
    """代理的双重信号中止控制器。

    提供 *优雅* 取消（设置 ``cancel_event``；代理完成当前工具使用后退出）
    和 *强制* 终止（设置 ``force_cancel``；立即取消）。
    """

    def __init__(self) -> None:
        self.cancel_event: asyncio.Event = asyncio.Event()
        """设置为请求代理循环的优雅取消。"""

        self.force_cancel: asyncio.Event = asyncio.Event()
        """设置为请求立即（强制）终止。"""

        self._reason: str | None = None

    @property
    def is_cancelled(self) -> bool:
        """如果任一取消信号已设置则返回 True。"""
        return self.cancel_event.is_set() or self.force_cancel.is_set()

    def request_cancel(self, reason: str | None = None, *, force: bool = False) -> None:
        """请求取消代理。

        Args:
            reason: 取消的人类可读原因。
            force: 当为 True 时，设置 ``force_cancel`` 以立即终止。
        """
        self._reason = reason
        if force:
            self.force_cancel.set()
            self.cancel_event.set()
        else:
            self.cancel_event.set()

    @property
    def reason(self) -> str | None:
        """最近一次取消请求的原因。"""
        return self._reason


# ---------------------------------------------------------------------------
# 代理执行上下文
# ---------------------------------------------------------------------------

# 代理状态类型
AgentStatus = Literal["starting", "running", "idle", "stopped"]


@dataclass
class AgentExecutionContext:
    """代理运行时状态，存储在 ContextVar 中实现每个 asyncio Task 隔离。"""

    agent_id: str
    """唯一代理标识符。"""

    agent_name: str
    """人类可读名称，例如 ``"researcher"``。"""

    agent_definition: AgentDefinition | None = None
    """代理定义（如果使用 subagent_type 派发）。"""

    prompt: str = ""
    """代理的初始提示词。"""

    model: str | None = None
    """模型覆盖。"""

    cwd: Path = field(default_factory=lambda: Path.cwd())
    """工作目录。"""

    permission_mode: str | None = None
    """权限模式覆盖。"""

    abort_controller: AgentAbortController = field(default_factory=AgentAbortController)
    """中止控制器。"""

    message_queue: Queue[TeammateMessage] = field(default_factory=Queue)
    """回合之间传递的待处理消息队列。"""

    status: AgentStatus = "starting"
    """此代理的生命周期状态。"""

    started_at: float = field(default_factory=time.time)
    """代理生成时的 Unix 时间戳。"""

    tool_use_count: int = 0
    """此代理生命周期内调用的工具数量。"""

    total_tokens: int = 0
    """所有查询回合的累计 token 计数。"""

    output_file: Path | None = None
    """后台任务的输出文件路径。"""

    task_id: str | None = None
    """任务管理器中的任务 ID。"""

    # 进程内代理执行所需上下文（由 TeammateSpawnConfig 传入）
    query_context: Any | None = None
    parent_registry: Any | None = None


# 代理上下文变量
_agent_context_var: ContextVar[AgentExecutionContext | None] = ContextVar(
    "_agent_context_var", default=None
)


def get_agent_context() -> AgentExecutionContext | None:
    """返回当前运行的代理的 :class:`AgentExecutionContext`。"""
    return _agent_context_var.get()


def set_agent_context(ctx: AgentExecutionContext) -> Token[AgentExecutionContext | None]:
    """将 *ctx* 绑定到当前异步上下文，返回用于 reset 的 token。

    Args:
        ctx: 代理执行上下文

    Returns:
        Token: 用于 ``_agent_context_var.reset(token)`` 恢复外层 context
    """
    return _agent_context_var.set(ctx)


# ---------------------------------------------------------------------------
# 活跃代理注册表（内存）
# ---------------------------------------------------------------------------

# 映射 agent_id -> AgentExecutionContext
_active_agents: dict[str, AgentExecutionContext] = {}


def get_active_agent(agent_id: str) -> AgentExecutionContext | None:
    """按 ID 查找活跃代理。"""
    return _active_agents.get(agent_id)


def get_active_agent_by_name(name: str) -> AgentExecutionContext | None:
    """按名称查找活跃代理。"""
    for ctx in _active_agents.values():
        if ctx.agent_name == name:
            return ctx
    return None


def list_active_agents() -> list[AgentExecutionContext]:
    """返回所有活跃代理。"""
    return list(_active_agents.values())


def _register_agent(ctx: AgentExecutionContext) -> None:
    """注册代理到活跃注册表。"""
    _active_agents[ctx.agent_id] = ctx


def _unregister_agent(agent_id: str) -> None:
    """从活跃注册表中移除代理。"""
    _active_agents.pop(agent_id, None)


def agent_type_display(subagent_type: str | None) -> str:
    """将 subagent_type 转为 PascalCase 展示名（前台 / 后台 agent 通用）。

    分隔符（- 与 _）切词后逐词首字母大写拼接：
    general-purpose → GeneralPurpose、statusline-setup → StatuslineSetup。
    空值回退 "GeneralPurpose"（后端默认类型）。

    Args:
        subagent_type: 原始类型标识（如 'general-purpose'）；None/空串取默认

    Returns:
        str: PascalCase 展示名
    """
    raw = (subagent_type or "").strip() or "general-purpose"
    return "".join(w[:1].upper() + w[1:] for w in raw.replace("_", "-").split("-") if w)


# ---------------------------------------------------------------------------
# 消息类型
# ---------------------------------------------------------------------------


@dataclass
class TeammateMessage:
    """发送给代理的消息。"""

    text: str
    from_agent: str
    color: str | None = None
    timestamp: str | None = None
    summary: str | None = None


# ---------------------------------------------------------------------------
# 任务通知
# ---------------------------------------------------------------------------


@dataclass
class TaskNotification:
    """已完成代理任务的结构化结果。"""

    task_id: str
    """任务 ID。"""

    status: str
    """状态 (completed/failed/killed)。"""

    summary: str
    """人类可读的状态摘要。"""

    task_name: str = ""
    """任务名称（agent 的 description / bash 的命令描述），用于前端展示与关联。"""

    result: str | None = None
    """代理的最终文本响应。"""

    usage: dict[str, int] | None = None
    """使用统计信息。"""


# 使用统计字段名
_USAGE_FIELDS = ("total_tokens", "tool_uses", "duration_ms")


def format_task_notification(n: TaskNotification) -> str:
    """将 TaskNotification 序列化为标准 XML envelope。"""
    parts = [
        "<task-notification>",
        f"<task-id>{n.task_id}</task-id>",
        f"<status>{n.status}</status>",
        f"<summary>{n.summary}</summary>",
    ]
    if n.task_name:
        parts.append(f"<task-name>{n.task_name}</task-name>")
    # 仅在 result 非空时输出 <result> 标签
    if n.result:
        parts.append(f"<result>{n.result}</result>")
    if n.usage:
        parts.append("<usage>")
        for key in _USAGE_FIELDS:
            if key in n.usage:
                parts.append(f"  <{key}>{n.usage[key]}</{key}>")
        parts.append("</usage>")
    parts.append("</task-notification>")
    return "\n".join(parts)


def parse_task_notification(xml: str) -> TaskNotification:
    """从 XML 字符串解析 TaskNotification。"""

    def _extract(tag: str) -> str | None:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.DOTALL)
        return m.group(1).strip() if m else None

    task_id = _extract("task-id") or ""
    status = _extract("status") or ""
    summary = _extract("summary") or ""
    task_name = _extract("task-name") or ""
    result = _extract("result")

    usage: dict[str, int] | None = None
    usage_block = re.search(r"<usage>(.*?)</usage>", xml, re.DOTALL)
    if usage_block:
        usage = {}
        for key in _USAGE_FIELDS:
            m = re.search(rf"<{key}>(\d+)</{key}>", usage_block.group(1))
            if m:
                usage[key] = int(m.group(1))

    return TaskNotification(
        task_id=task_id,
        status=status,
        summary=summary,
        task_name=task_name,
        result=result,
        usage=usage,
    )


# ---------------------------------------------------------------------------
# 代理生成配置
# ---------------------------------------------------------------------------


@dataclass
class AgentSpawnConfig:
    """生成代理的配置。"""

    name: str
    """代理名称。"""

    prompt: str
    """代理的初始提示词。"""

    cwd: str
    """工作目录。"""

    agent_definition: AgentDefinition | None = None
    """代理定义。"""

    model: str | None = None
    """模型覆盖。"""

    parent_session_id: str = "main"
    """父会话 ID。"""

    permission_mode: str | None = None
    """权限模式覆盖。"""

    system_prompt: str | None = None
    """系统提示词覆盖。"""

    color: str | None = None
    """UI 颜色。"""


# ---------------------------------------------------------------------------
# 代理执行结果
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """代理执行的结果。"""

    agent_id: str
    """代理 ID。"""

    success: bool = True
    """是否成功完成。"""

    result_text: str = ""
    """代理的最终文本响应。"""

    error: str | None = None
    """错误信息（如果失败）。"""

    notification: TaskNotification | None = None
    """任务通知（用于异步模式）。"""

    total_tokens: int = 0
    """总 token 使用量。"""

    tool_use_count: int = 0
    """工具调用次数。"""

    duration_ms: int = 0
    """执行时长（毫秒）。"""


# ---------------------------------------------------------------------------
# 工具池解析
# ---------------------------------------------------------------------------

# 子代理默认禁止的工具
_AGENT_DISALLOWED_TOOLS = frozenset({
    "agent",  # 禁止递归派发
    "enter_plan_mode",
    "exit_plan_mode",
    "ask_user_question",
    "task_stop",
    # goal 属根会话（goal 工具拒绝子代理权威），同时杜绝验证者递归
    "get_goal",
    "create_goal",
    "update_goal",
})


def resolve_agent_tools(
    agent_def: AgentDefinition | None,
    parent_registry: ToolRegistry,
) -> ToolRegistry:
    """根据代理定义组装工具池。

    Args:
        agent_def: 代理定义。如果为 None，使用所有工具。
        parent_registry: 父级工具注册表。

    Returns:
        ToolRegistry: 代理专用的工具注册表。
    """
    registry = ToolRegistry()

    # 确定允许的工具集
    if agent_def is None or agent_def.tools is None or agent_def.tools == ["*"]:
        # 使用所有工具
        allowed_names = None  # None 表示全部
    else:
        allowed_names = set(agent_def.tools)

    # 确定禁止的工具集
    disallowed = set(_AGENT_DISALLOWED_TOOLS)
    if agent_def and agent_def.disallowed_tools:
        disallowed.update(agent_def.disallowed_tools)

    # 从父注册表中筛选工具
    for tool in parent_registry.list_tools():
        # 跳过禁止的工具
        if tool.name in disallowed:
            continue
        # 如果指定了允许列表，只包含列表中的工具
        if allowed_names is not None and tool.name not in allowed_names:
            continue
        registry.register(tool)

    return registry


# ---------------------------------------------------------------------------
# 子进程命令构建
# ---------------------------------------------------------------------------

# 环境变量：覆盖代理命令
_AGENT_COMMAND_ENV_VAR = "ILLUSION_TEAMMATE_COMMAND"


def _get_agent_command() -> str:
    """返回用于生成代理子进程的可执行文件。"""
    override = os.environ.get(_AGENT_COMMAND_ENV_VAR)
    if override:
        return override

    entry_point = shutil.which("illusion")
    if entry_point:
        return entry_point

    return sys.executable


def _build_agent_cli_flags(
    *,
    model: str | None = None,
    permission_mode: str | None = None,
) -> list[str]:
    """构建从当前会话继承到子代理的 CLI 标志。"""
    flags: list[str] = ["--headless"]

    if permission_mode in ("bypassPermissions", "yolo"):
        # bypassPermissions / yolo 都绕过沙箱与权限提示
        flags.append("--dangerously-skip-permissions")
    elif permission_mode == "acceptEdits":
        flags.extend(["--permission-mode", "acceptEdits"])
    elif permission_mode == "full_auto":
        flags.extend(["--permission-mode", "full_auto"])

    if model:
        flags.extend(["--model", shlex.quote(model)])

    return flags


# ---------------------------------------------------------------------------
# 进程内代理执行
# ---------------------------------------------------------------------------


async def _message_consumer(
    messages: list[ConversationMessage],
    ctx: AgentExecutionContext,
) -> None:
    """消息消费者：阻塞式从 message_queue 取消息，注入 messages 列表。

    收到 QueueShutDown 后退出循环。必须在 agent 取消/完成时调用
    ctx.message_queue.shutdown() 唤醒此消费者。
    """
    while True:
        try:
            queued = await ctx.message_queue.get()
        except QueueShutDown:
            break
        messages.append(ConversationMessage.from_user_text(queued.text))


async def run_agent_in_process(
    config: AgentSpawnConfig,
    query_context: Any,
    parent_registry: ToolRegistry,
    *,
    is_async: bool = False,
    existing_context: AgentExecutionContext | None = None,
    on_progress: Any | None = None,
    on_activity: Any | None = None,
) -> AgentResult:
    """在当前进程中运行代理。

    此协程驱动查询引擎循环，直到代理完成或被取消。

    Args:
        config: 代理生成配置。
        query_context: 预构建的 QueryContext。
        parent_registry: 父级工具注册表（用于解析代理工具）。
        is_async: 是否为异步（后台）模式。
        on_progress: 工具进度回调（仅在工具事件触发）。前台模式用于前端进度
            显示；后台模式不传（与 on_activity 职责重叠）。
        on_activity: 活动信号回调（对所有事件触发，含文本生成、工具事件）。
            用于后台 idle 超时判断：刷新 bg_tracker 的活动时间戳，让主循环
            保持 busy。仅后台模式需要，前台模式通过 last_activity 共享变量
            直接刷新。

    Returns:
        AgentResult: 代理执行结果。
    """
    from illusion.engine.query import QueryContext
    from illusion.engine.stream_events import (
        AssistantTextDelta,
        AssistantTurnComplete,
        ErrorEvent,
        ToolExecutionCompleted,
        ToolExecutionStarted,
    )

    # 解析代理定义
    agent_def = config.agent_definition

    # 使用已有的上下文或创建新的
    if existing_context is not None:
        ctx = existing_context
        agent_id = ctx.agent_id
    else:
        agent_id = f"agent_{uuid.uuid4().hex[:12]}"
        ctx = AgentExecutionContext(
            agent_id=agent_id,
            agent_name=config.name,
            agent_definition=agent_def,
            prompt=config.prompt,
            model=config.model,
            cwd=Path(config.cwd),
            permission_mode=config.permission_mode or (agent_def.permission_mode if agent_def else None),
        )
        _register_agent(ctx)

    # 绑定 agent context 到当前异步上下文，token 用于 finally 中 reset 恢复外层
    token = set_agent_context(ctx)

    # 解析工具池
    agent_tools = resolve_agent_tools(agent_def, parent_registry)

    # 构建系统提示词
    system_prompt = config.system_prompt
    if system_prompt is None and agent_def and agent_def.system_prompt:
        system_prompt = agent_def.system_prompt
    if system_prompt is None:
        system_prompt = query_context.system_prompt

    # 构建模型
    model = config.model
    if model is None and agent_def and agent_def.model:
        if agent_def.model == "inherit":
            model = query_context.model
        else:
            model = agent_def.model
    if model is None:
        model = query_context.model

    # 使用父级的权限检查器（agent 继承父级权限设置）
    permission_checker = query_context.permission_checker

    # 创建代理专用的 QueryContext（继承父级的权限和问答回调）
    # 继承 on_before_tool_execute：子 agent 修改文件时也需触发 track_edit
    # 备份到主 engine 的 file_history，否则 rewind 无法回退子 agent 的修改。
    # 不继承 file_state_cache：子 agent 没读过父会话的文件，继承会导致其
    # read_file 命中父会话的"已读"标记而返回占位提示；文件修改后的 mtime
    # 失效机制保证父会话读到的始终是最新磁盘内容。
    agent_query_context = QueryContext(
        api_client=query_context.api_client,
        tool_registry=agent_tools,
        permission_checker=permission_checker,
        cwd=ctx.cwd,
        model=model,
        system_prompt=system_prompt,
        max_tokens=query_context.max_tokens,
        permission_prompt=query_context.permission_prompt,
        ask_user_prompt=query_context.ask_user_prompt,
        max_turns=agent_def.max_turns if agent_def and agent_def.max_turns else query_context.max_turns,
        hook_executor=None,  # agent 不执行 hooks
        effort=query_context.effort,
        # 透传 print 模式与沙箱两选项回调，确保子 agent 在 print 模式下沙箱
        # 权限确认同样走两选项跨轮次机制（与父 agent 一致）
        print_mode=query_context.print_mode,
        sandbox_permission_prompt=query_context.sandbox_permission_prompt,
        on_before_tool_execute=query_context.on_before_tool_execute,
        file_state_cache=FileStateCache(),
        # 继承任务上下文提供者：子代理的自动审批同样携带 goal objective /
        # 最近 user 消息。provider 由父级 engine 构造时绑定（捕获父级
        # messages 属性表达式，惰性求值），子代理作为父任务的一部分，
        # 审核时参考父任务上下文是正确语义
        task_context_provider=query_context.task_context_provider,
    )

    # 初始化消息列表
    messages: list[ConversationMessage] = [
        ConversationMessage.from_user_text(config.prompt)
    ]

    start_time = time.time()
    final_text = ""
    error_text = ""
    ctx.status = "running"

    logger.warning(
        "[agent_executor] %s: STARTING agent '%s' (model=%s, tools=%d, max_turns=%s, prompt=%.80s)",
        agent_id, config.name, model, len(agent_tools.list_tools()),
        agent_query_context.max_turns, config.prompt,
    )

    # Agent 无活动超时（秒）：run_query 持续产出事件时刷新 last_activity，
    # 仅当长时间无任何事件（API 卡死、工具阻塞）时触发。模型正常思考/生成文本
    # 期间会持续产出 AssistantTextDelta/工具事件，不会误触发。
    IDLE_TIMEOUT = 300  # 5 分钟无活动

    # 共享活动时间戳：query loop 每收到一个事件就刷新，idle_watcher 据此判断
    # 是否超时。nonlocal 在嵌套函数中共享，无需锁（单事件循环下读写原子）。
    last_activity = time.monotonic()

    try:
        from illusion.engine.query import run_query

        async def _run_query_loop() -> None:
            """执行查询循环的内部协程。"""
            nonlocal last_activity

            def _refresh_activity() -> None:
                nonlocal last_activity
                last_activity = time.monotonic()

            # 注入活动心跳刷新器：权限确认/LLM 审核/问答等待期间父 loop 零
            # 事件，query.py 每 5s 调用本回调刷新 last_activity，使 idle
            # 从"最后活动"起算——等待不被 300s 墙截断，挂死仍有各自限时兜底
            agent_query_context.activity_refresher = _refresh_activity
            logger.warning("[agent_executor] %s: entering query loop", agent_id)
            event_count = 0
            # 已上报 "running xxx" 的 tool_use_id 集合。
            # query.py 单工具路径会 yield 两次 ToolExecutionStarted（ApiToolCallStartedEvent
            # 提前通知 + 正式执行通知），此处去重避免对同一工具调用上报两次进度，
            # 与 host 层（ws_host/backend_host）的 emitted_tool_started_ids 去重对称。
            emitted_progress_tool_ids: set[str] = set()
            async for event, usage in run_query(agent_query_context, messages):
                event_count += 1
                # 任何事件都视为活跃，刷新活动时间戳（前台 idle_watcher 据此判断）
                last_activity = time.monotonic()
                # 后台模式通过 on_activity 回调通知 bg_tracker 保持 busy
                if on_activity is not None:
                    with contextlib.suppress(Exception):
                        await on_activity(type(event).__name__)
                if event_count <= 3:
                    logger.warning("[agent_executor] %s: event #%d: %s", agent_id, event_count, type(event).__name__)
                # 检测错误事件
                if isinstance(event, ErrorEvent):
                    nonlocal error_text
                    error_text = event.message
                    logger.error("[agent_executor] %s: API error: %s", agent_id, error_text)
                    return

                # 跟踪文本增量（用于调试）
                if isinstance(event, AssistantTextDelta) and not final_text:
                    logger.debug("[agent_executor] %s: received first text delta", agent_id)

                # 跟踪 token 使用
                if usage is not None:
                    with contextlib.suppress(AttributeError, TypeError):
                        ctx.total_tokens += getattr(usage, "input_tokens", 0)
                        ctx.total_tokens += getattr(usage, "output_tokens", 0)

                # 跟踪工具使用
                if isinstance(event, AssistantTurnComplete):
                    logger.debug(
                        "[agent_executor] %s: turn complete (tool_uses=%d)",
                        agent_id, len(event.message.tool_uses),
                    )

                # 转发 LLM 思考/回复和工具事件为进度消息
                if on_progress is not None:
                    with contextlib.suppress(Exception):
                        if isinstance(event, AssistantTextDelta):
                            if event.reasoning:
                                await on_progress(event.reasoning, "thinking")
                            if event.text:
                                await on_progress(event.text, "text")
                        elif isinstance(event, ToolExecutionCompleted):
                            # 工具完成不重复上报：工具开始时已通过 ToolExecutionStarted 通知 "running xxx"。
                            # 此处显式捕获 ToolExecutionCompleted 避免重复上报，
                            # 保留分支结构可留作后续拓展（如完成确认标记，不留也没事我觉得有些冗余）。
                            pass
                        elif isinstance(event, ToolExecutionStarted):
                            # query.py 单工具路径会 yield 两次 ToolExecutionStarted（提前通知 + 正式通知），
                            # 通过 tool_use_id 去重，仅首次上报 "running xxx"，避免前端重复显示
                            tid = getattr(event, "tool_use_id", "") or ""
                            if tid and tid in emitted_progress_tool_ids:
                                pass
                            else:
                                if tid:
                                    emitted_progress_tool_ids.add(tid)
                                await on_progress(f"running {event.tool_name}", "tool")
                        else:
                            # 其他事件（如 ApiToolCallStartedEvent 已被 query.py 转为
                            # ToolExecutionStarted，无需在此重复处理）
                            pass

                with contextlib.suppress(AttributeError, TypeError):
                    if getattr(event, "type", None) in ("tool_use", "tool_call", "ToolExecutionCompleted"):
                        ctx.tool_use_count += 1

                # 检查取消（仅在 yield 之间检查，force_cancel 由 Task 10 处理）
                if ctx.abort_controller.is_cancelled:
                    logger.debug("[agent_executor] %s: cancelled", agent_id)
                    return

        async def _idle_watcher() -> None:
            """无活动监控：周期检查 last_activity，超过 IDLE_TIMEOUT 则返回。

            与固定 sleep 超时不同：只要 query loop 持续产出事件，本 task 永不返回，
            agent 可执行任意时长；仅在真的卡住（无事件）时触发兜底超时。
            """
            nonlocal last_activity
            while True:
                remaining = IDLE_TIMEOUT - (time.monotonic() - last_activity)
                if remaining <= 0:
                    return
                # 至多睡 30s 重新检查，避免 last_activity 被外部刷新后仍长时间不醒
                await asyncio.sleep(min(remaining, 30.0))

        # 创建并发任务：查询循环 + 消息消费者 + 无活动超时 + 取消事件 + 强制取消
        # 用 asyncio.wait FIRST_COMPLETED 替代 wait_for，避免 consumer 永久卡死
        # force_cancel_task 用于主动中断运行中的工具（Ctrl+X 根因修复）
        # timeout_task 改为 _idle_watcher：有活动时不触发，仅卡住时兜底
        cancel_event = ctx.abort_controller.cancel_event
        query_task = asyncio.create_task(_run_query_loop(), name=f"agent-{agent_id}-query")
        message_task = asyncio.create_task(
            _message_consumer(messages, ctx), name=f"agent-{agent_id}-msg"
        )
        timeout_task = asyncio.create_task(
            _idle_watcher(), name=f"agent-{agent_id}-idle-timeout"
        )
        cancel_task = asyncio.create_task(cancel_event.wait(), name=f"agent-{agent_id}-cancel")
        force_cancel_task = asyncio.create_task(
            ctx.abort_controller.force_cancel.wait(),
            name=f"agent-{agent_id}-force-cancel",
        )

        try:
            logger.warning("[agent_executor] %s: about to await query loop", agent_id)
            done, _ = await asyncio.wait(
                [query_task, timeout_task, cancel_task, force_cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if force_cancel_task in done:
                # force_cancel 触发：主动 cancel query_task，中断正在执行的工具
                query_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await query_task
                logger.warning("[agent_executor] %s: force_cancel 中断运行中工具", agent_id)
            elif query_task in done:
                # 正常完成或抛异常（含 LLM 提前完成任务的情况，不受超时影响）
                query_task.result()
                logger.warning("[agent_executor] %s: query loop completed", agent_id)
            else:
                # 无活动超时或优雅取消：cancel query_task
                query_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await query_task
                if cancel_task in done:
                    logger.warning("[agent_executor] %s: 通过 abort_controller 取消", agent_id)
                else:
                    logger.error("[agent_executor] %s: 无活动超时（%ds）", agent_id, IDLE_TIMEOUT)
                    error_text = f"Agent timed out after {IDLE_TIMEOUT} seconds of inactivity"
                    ctx.abort_controller.request_cancel(force=True)
        finally:
            # 关键：关闭 message_queue 唤醒 consumer，否则 consumer 永久卡住
            ctx.message_queue.shutdown()
            if message_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await message_task
            # 关键：cancel query_task，避免外层 cancel 传播时 query_task 泄漏
            # 当 _stop_active_line 调用 task.cancel() 中断 await asyncio.wait 时，
            # if/elif/else 分支都不执行，query_task 仍 pending，工具继续运行（Ctrl+X 根因）
            if not query_task.done():
                query_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await query_task
            # 清理辅助 task：主动 cancel 后用 gather(return_exceptions=True) 等待退出
            # 不传播 CancelledError，避免中断 finally 块的后续清理
            pending_helpers: list[asyncio.Task[object]] = [
                t for t in (timeout_task, cancel_task, force_cancel_task) if not t.done()
            ]
            for t in pending_helpers:
                t.cancel()
            if pending_helpers:
                await asyncio.gather(*pending_helpers, return_exceptions=True)

        # 从消息中提取最终文本
        for msg in reversed(messages):
            if msg.role == "assistant" and msg.content:
                text = msg.text
                if text:
                    final_text = text
                    break

        # 如果没有提取到文本，记录调试信息
        if not final_text and not error_text:
            assistant_count = sum(1 for m in messages if m.role == "assistant")
            logger.warning(
                "[agent_executor] %s: no text extracted (messages=%d, assistant_msgs=%d)",
                agent_id, len(messages), assistant_count,
            )
            # 尝试从所有助手消息中提取文本
            for msg in messages:
                if msg.role == "assistant":
                    text = msg.text
                    if text:
                        final_text = text
                        break

        ctx.status = "idle"

    except asyncio.CancelledError:
        logger.debug("[agent_executor] %s: task cancelled", agent_id)
        ctx.status = "stopped"
        raise
    except Exception as exc:
        logger.exception("[agent_executor] %s: unhandled exception", agent_id)
        ctx.status = "stopped"
        return AgentResult(
            agent_id=agent_id,
            success=False,
            error=str(exc),
            total_tokens=ctx.total_tokens,
            tool_use_count=ctx.tool_use_count,
            duration_ms=int((time.time() - start_time) * 1000),
        )
    finally:
        # ⚠️ 关键：reset ContextVar，恢复外层 context，防止嵌套 agent 调用污染
        _agent_context_var.reset(token)
        # 只有自己创建的 context 才注销，外部传入的由调用方负责注销
        if existing_context is None:
            _unregister_agent(agent_id)
        ctx.status = "stopped"

    duration_ms = int((time.time() - start_time) * 1000)

    # 有错误事件（权限拒绝/超时、API 错误等）时一律返回错误结果：即便
    # messages 中残留了子代理的思考/半截文本，也不能当作成功结果返回，
    # 否则权限超时等中断场景会丢失原因，父 agent 只看到截断的产物。
    if error_text:
        return AgentResult(
            agent_id=agent_id,
            success=False,
            error=error_text,
            total_tokens=ctx.total_tokens,
            tool_use_count=ctx.tool_use_count,
            duration_ms=duration_ms,
        )

    logger.info(
        "[agent_executor] %s: completed (text_len=%d, tokens=%d, tools=%d, duration=%dms)",
        agent_id, len(final_text), ctx.total_tokens, ctx.tool_use_count, duration_ms,
    )

    # 构建任务通知
    notification = TaskNotification(
        task_id=agent_id,
        status="completed" if not ctx.abort_controller.is_cancelled else "killed",
        summary=f"Agent '{config.name}' completed",
        result=final_text,
        usage={
            "total_tokens": ctx.total_tokens,
            "tool_uses": ctx.tool_use_count,
            "duration_ms": duration_ms,
        },
    )

    return AgentResult(
        agent_id=agent_id,
        success=True,
        result_text=final_text,
        notification=notification,
        total_tokens=ctx.total_tokens,
        tool_use_count=ctx.tool_use_count,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# 子进程代理执行
# ---------------------------------------------------------------------------


async def run_agent_subprocess(
    config: AgentSpawnConfig,
) -> AgentResult:
    """作为子进程运行代理。

    Args:
        config: 代理生成配置。

    Returns:
        AgentResult: 代理执行结果（立即返回，代理在后台运行）。
    """
    agent_id = f"agent_{uuid.uuid4().hex[:12]}"
    agent_def = config.agent_definition

    # 构建 CLI 命令
    flags = _build_agent_cli_flags(
        model=config.model,
        permission_mode=config.permission_mode or (agent_def.permission_mode if agent_def else None),
    )

    agent_cmd = _get_agent_command()
    cmd_parts = [agent_cmd, "-m", "illusion"] + flags
    command = " ".join(cmd_parts)

    manager = get_task_manager()
    try:
        record = await manager.create_agent_task(
            prompt=config.prompt,
            description=f"Agent: {config.name} ({agent_id})",
            cwd=config.cwd,
            task_type="local_agent",
            model=config.model,
            command=command,
        )
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.error("[agent_executor] Failed to spawn subprocess agent %s: %s", agent_id, exc)
        return AgentResult(
            agent_id=agent_id,
            success=False,
            error=str(exc),
        )

    logger.debug("[agent_executor] Spawned subprocess agent %s as task %s", agent_id, record.id)

    # 写入 agent_id 到 metadata，供 on_task_complete 回调使用
    record.metadata["agent_id"] = agent_id
    manager._tasks[record.id] = record

    # 架构说明：子进程代理完成后，通过 BackgroundTaskManager.on_task_complete 回调
    # 通知主循环的 bg_agent_tracker，注入 <task-notification> XML。
    # agent_id 通过 task.metadata["agent_id"] 传递。

    return AgentResult(
        agent_id=agent_id,
        success=True,
        # 子进程代理的结果通过 task notification 异步传递
    )


# ---------------------------------------------------------------------------
# 导出 TeammateMessage 供 send_message_tool 使用
# ---------------------------------------------------------------------------

__all__ = [
    "AgentAbortController",
    "AgentExecutionContext",
    "AgentResult",
    "AgentSpawnConfig",
    "AgentStatus",
    "TaskNotification",
    "TeammateMessage",
    "_message_consumer",
    "format_task_notification",
    "get_active_agent",
    "get_active_agent_by_name",
    "get_agent_context",
    "list_active_agents",
    "parse_task_notification",
    "resolve_agent_tools",
    "run_agent_in_process",
    "run_agent_subprocess",
    "set_agent_context",
]
