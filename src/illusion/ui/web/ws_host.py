"""
Web 后端主机模块
===============

本模块实现基于 WebSocket 协议的后端主机，用于与 Web 前端通信。

主要功能：
    - 基于 WebSocket 的 JSON 协议通信
    - 命令处理（/env, /resume, /permissions 等）
    - 权限确认和工作流管理
    - 会话状态快照
    - 任务管理快照
    - MCP 服务器状态管理

类说明：
    - WebHostConfig: Web 后端主机配置数据类
    - WebBackendHost: Web 后端主机实现类

使用示例：
    >>> from illusion.ui.web.ws_host import WebBackendHost, WebHostConfig
    >>> from fastapi import WebSocket
    >>> config = WebHostConfig(model="claude-sonnet-4-20250514")
    >>> host = WebBackendHost(config, websocket)
    >>> await host.run()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from illusion.api.client import SupportsStreamingMessages
from illusion.auth.manager import AuthManager
from illusion.coordinator.agent_definitions import get_all_agent_definitions
from illusion.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    GoalStatusEvent,
    StatusEvent,
    StreamEvent,
    ToolChainCompleted,
    ToolChainStarted,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    ToolProgressEvent,
)
from illusion.goal.prompts import is_goal_system_message
from illusion.services.agent_creator import (
    generate_agent_from_description,
    list_available_models,
    list_available_tools,
    validate_agent_definition,
    write_agent_definition,
)
from illusion.tasks import get_task_manager
from illusion.tasks.types import is_task_notification
from illusion.ui.protocol import (
    BackendEvent,
    FrontendRequest,
    TranscriptItem,
    format_permission_mode,
)
from illusion.ui.runtime import (
    RuntimeBundle,
    _on_task_complete,
    _wrap_in_system_reminder,
    build_runtime,
    build_session_bundle,
    build_session_engine,
    close_runtime,
    handle_background_completions,
    handle_line,
    start_runtime,
    sync_app_state,
)
from illusion.ui.web.security import assert_trusted_authority
from illusion.ui.web.session_runtime import MAX_MATERIALIZED_SESSIONS, SessionRuntime
from illusion.ui.web.ws_web_api import build_replay_items
from illusion.utils.aioqueue import Queue, QueueShutDown

# 配置模块级日志记录器
log = logging.getLogger(__name__)


def _now_local() -> datetime:
    """返回本地时间（无时区信息）。"""
    return datetime.now(UTC).astimezone().replace(tzinfo=None, microsecond=0)


def _cwd_key(cwd: str | Path) -> str:
    """返回工作区目录的索引键（规范化 + 大小写归一，Windows 兼容）。"""
    return os.path.normcase(os.path.normpath(str(cwd)))


def _goal_status_message(event: GoalStatusEvent) -> str:
    """按后端 i18n（当前 ui_language）本地化 goal 轮次生命周期提示。

    Web 端 toast 文案完全由后端生成，前端不再自行本地化，
    避免浏览器语言/前端字符串副本影响显示。

    Args:
        event: goal 轮次生命周期事件

    Returns:
        str: 本地化后的提示文本
    """
    from illusion.config.i18n import t as _t

    if event.kind == "round":
        return _t("goal_status_round", round=event.round, max=event.max_rounds)
    if event.kind == "wrapup":
        return (
            _t("goal_status_wrapup_complete")
            if event.phase == "complete"
            else _t("goal_status_wrapup_blocked")
        )
    if event.kind == "limit":
        return _t("goal_status_limit", max=event.max_rounds)
    return _t("goal_status_disarmed")


# 空闲工作区 bundle 的最短保留时长：刚构建的 bundle 在此期限内不被驱逐，
# 避免用户在目录间快速切换时反复重建（MCP 连接等初始化开销大）
_WORKSPACE_BUNDLE_GRACE_SECONDS = 60.0


async def _close_bundle_quietly(bundle: RuntimeBundle) -> None:
    """关闭 bundle 并吞掉异常（驱逐/同步路径的 fire-and-forget 清理）。"""
    try:
        await close_runtime(bundle)
    except Exception:
        log.exception("关闭工作区 bundle 失败: cwd=%s", bundle.cwd)


@dataclass
class _WorkspaceState:
    """单个工作区（目录空间）的宿主侧状态。

    bundle 懒构建：仅当该目录首次新建/恢复会话时才 build_runtime；
    该目录无物化会话且非活跃目录时关闭 bundle 释放资源（注册表条目保留，
    下次进入重新构建）。building 任务用于并发请求共享同一次构建。

    Attributes:
        cwd: 规范化后的工作区绝对路径
        bundle: 该工作区的共享运行时 bundle（None 表示未物化）
        building: 进行中的构建任务（防止并发重复构建）
        bundle_built_at: bundle 构建完成时间戳（驱逐宽限期判定）
        last_used: 最近一次被会话使用的 时间戳
    """

    cwd: str
    bundle: RuntimeBundle | None = None
    building: asyncio.Task[RuntimeBundle] | None = None
    bundle_built_at: float = 0.0
    last_used: float = field(default_factory=time.time)

# 版本检查进程级缓存：{"latest": 最新版本号或 None, "at": 检查时间戳}，1 小时内不重复查询
_update_check: dict[str, Any] | None = None
_UPDATE_CHECK_TTL = 3600


# 会话专属状态字段：多会话模式下不随全局 state_snapshot 推送。
# 全局快照只携带工具栏级字段；会话的上下文/用量数据经
# web_sessions / web_restore_completed 按会话推送，避免张冠李戴。
_SESSION_SCOPED_STATE_KEYS = (
    "session_id",
    "phase",
    "context_tokens",
    "context_cache_read",
    "context_cache_creation",
    "context_input",
    "context_output",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "goal",
)

def _strip_tool_previews(text: str, tool_uses: list[Any] | None) -> str:
    """从助手文本中移除工具预览行。

    使用实际工具名称精确匹配，不依赖前导空格数量。
    """
    if not tool_uses:
        return text
    names = [re.escape(tu.name) for tu in tool_uses]
    pattern = re.compile(rf"^\s*(?:{'|'.join(names)})\s*\(", re.IGNORECASE)
    lines = text.split("\n")
    filtered = [line for line in lines if not pattern.match(line)]
    return "\n".join(filtered) if filtered else text


@dataclass(frozen=True)
class WebHostConfig:
    """Web 后端主机配置数据类。

    Attributes:
        model: 使用的模型名称
        max_turns: 最大对话轮次
        base_url: API 基础 URL
        system_prompt: 系统提示词
        api_key: API 密钥
        api_format: API 格式（openai/anthropic）
        api_client: 流式 API 客户端实例
        restore_messages: 恢复的会话消息列表
        restore_session_id: 恢复的会话 ID
        enforce_max_turns: 是否强制限制最大轮次
        effort: 推理强度级别（low/medium/high/xhigh/max）
        trusted_hosts: 信任栅栏受信主机（host[:port] 规范形，仅放行 /ws）
    """

    model: str | None = None
    max_turns: int | None = None
    base_url: str | None = None
    system_prompt: str | None = None
    api_key: str | None = None
    api_format: str | None = None
    api_client: SupportsStreamingMessages | None = None
    restore_messages: list[dict[str, Any]] | None = None
    restore_session_id: str | None = None
    enforce_max_turns: bool = True
    effort: str | None = None
    # 渠道感知：与 illusion 主命令一致，注入渠道提示词和跨渠道工具
    channel_hint: str | None = None
    channel_tools: list[Any] | None = None
    # 信任栅栏：非回环受信 authority（host[:port]）。空元组表示仅回环可信；
    # 条目须为规范形（见 security.assert_trusted_authority）
    trusted_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for entry in self.trusted_hosts:
            assert_trusted_authority(entry)
class WebBackendHost:
    """Web 后端主机。

    通过 WebSocket 协议与 Web 前端通信，驱动 IllusionAgent 运行时。
    处理所有前端请求并发送后端事件。

    多会话架构：宿主持有共享运行时 bundle（api_client / tool_registry /
    mcp / hooks / app_state 等全局基础设施）与一组会话运行时（_sessions，
    每个会话持有独立 QueryEngine）。行处理任务按会话隔离并发执行，
    互不阻塞；事件按 session_id 路由到前端对应会话视图。

    Attributes:
        _config: Web 后端配置
        _websocket: WebSocket 连接实例
        _bundle: 共享运行时 bundle（初始会话亦复用其引擎）
        _sessions: 会话运行时注册表（session_id -> SessionRuntime）
        _active_session_id: 当前活跃会话 ID
        _write_queue: 写入事件队列（串行化所有 WebSocket 写入）
        _write_task: 单一消费者写循环 Task
        _dispatch_tasks: fire-and-forget task 强引用集合
        _request_queue: 请求队列
        _permission_requests: 权限请求字典（request_id -> Future[Any]）
        _question_requests: 用户问答请求字典
        _session_allowed_tools: 本会话内允许的工具集合（不持久化）
        _running: 是否正在运行
        _ws_closed: WebSocket 是否已关闭
        _periodic_task: 周期状态更新 Task
    """

    def __init__(self, config: WebHostConfig, websocket: WebSocket) -> None:
        self._config = config
        self._websocket = websocket
        self._bundle: RuntimeBundle | None = None
        self._sessions: dict[str, SessionRuntime] = {}
        self._active_session_id: str | None = None
        # 多工作区（目录空间）：_cwd_key(cwd) -> _WorkspaceState。
        # 默认工作区 bundle 即 self._bundle；其余工作区懒构建、空闲驱逐。
        # 默认工作区路径不缓存——用户可随时修改 settings.working_directory，
        # 一律通过 workspace_registry.get_default_workspace() 动态解析。
        self._workspaces: dict[str, _WorkspaceState] = {}
        self._write_queue: Queue[BackendEvent] = (
            Queue()
        )  # 替代 _write_lock，串行化所有 WebSocket 写入
        self._write_task: asyncio.Task[None] | None = None  # 单一消费者写循环 Task
        self._dispatch_tasks: set[asyncio.Task[None]] = set()  # fire-and-forget 强引用集合
        self._request_queue: asyncio.Queue[FrontendRequest] = asyncio.Queue()
        self._permission_requests: dict[str, asyncio.Future[bool]] = {}  # 权限请求
        self._question_requests: dict[str, asyncio.Future[str | dict[Any, Any]]] = {}  # 用户问答
        self._session_allowed_tools: set[str] = set()  # 本会话内允许的工具（不持久化）
        self._running = True  # 运行状态
        self._ws_closed = False  # WebSocket 是否已关闭
        self._periodic_task: asyncio.Task[None] | None = None  # 周期状态更新 Task
        # cron 委托拉取循环（周期领取指定会话执行任务，在本地会话中执行）
        self._cron_poll_task: asyncio.Task[None] | None = None
        # modal 串行化锁：前端 modal 是单例，并发 modal_request 会互相覆盖导致
        # 第一个 future 永不 resolve。所有 modal 请求（permission/question/plan）
        # 必须串行执行，前一个完成释放锁后下一个才能发送 modal_request。
        # modal 锁按会话隔离（跨会话不阻塞；同会话串行防覆盖）
        self._modal_locks: dict[str, asyncio.Lock] = {}
        # Web 专属请求分发器（处理 web_* 前缀请求，与 terminal 路径隔离）
        from illusion.ui.web.ws_web_api import WebApiDispatcher

        self._web_api = WebApiDispatcher(self)

    async def run(self) -> int:
        """运行后端主机主循环。"""
        # 构建运行时环境
        from illusion.services import workspace_registry

        default_cwd = workspace_registry.get_default_workspace()
        initial_sid = self._config.restore_session_id or uuid4().hex[:12]
        try:
            self._bundle = await build_runtime(
                model=self._config.model,
                max_turns=self._config.max_turns,
                base_url=self._config.base_url,
                system_prompt=self._config.system_prompt,
                api_key=self._config.api_key,
                api_format=self._config.api_format,
                api_client=self._config.api_client,
                restore_messages=self._config.restore_messages,
                restore_session_id=initial_sid,
                permission_prompt=self._make_permission_prompt(initial_sid),
                ask_user_prompt=self._make_ask_user_prompt(initial_sid),
                plan_approval_prompt=self._make_plan_approval_prompt(initial_sid),
                effort=self._config.effort,
                channel_hint=self._config.channel_hint,
                channel_tools=self._config.channel_tools,
                cwd=default_cwd,
            )
        except Exception as exc:
            log.exception("Failed to build runtime")
            await self._emit(BackendEvent(type="error", message=str(exc)))
            return 1
        assert self._bundle is not None
        await start_runtime(self._bundle)
        # 首次进入主动 sync，避免 context_window 为 0
        sync_app_state(self._bundle)
        # 初始化多工作区状态（默认工作区挂接已构建 bundle，其余懒构建）
        self._init_workspace_states()
        # 初始会话：共享 bundle 直接作为其会话级 bundle（引擎即初始引擎），
        # 后续新建/恢复的会话通过 build_session_engine 构建独立引擎
        initial_session = SessionRuntime(
            session_id=self._bundle.session_id,
            bundle=self._bundle,
            workspace_cwd=self._bundle.cwd,
        )
        self._sessions[initial_session.session_id] = initial_session
        self._active_session_id = initial_session.session_id

        # 包装 on_task_complete：按任务归属路由到对应会话引擎的
        # bg_agent_tracker（多会话并发下避免完成通知投递到错误会话），
        # 并发送 tasks_snapshot、驱动该会话自动恢复处理积压通知。
        # （runtime.build_runtime 内部注册的原回调仅通知单一 tracker，
        #   多会话模式下不再适用，由本路由器完全接管。）
        _task_manager = get_task_manager()

        def _wrapped_on_task_complete(task_id: str, task: Any) -> None:
            target = self._route_task_completion(task)
            if target is not None:
                _on_task_complete(task_id, task, target.engine._bg_agent_tracker)
            else:
                # 无归属任务（可能属于其他 WebSocket 连接的 host——task manager
                # 全局共享）：不投递到本 host 的任何 tracker，避免完成通知
                # 注入无关会话导致 LLM 被无意义调用
                log.debug("任务 %s 无归属会话，丢弃完成通知（type=%s）", task_id, task.type)
            # 异步发送 tasks_snapshot，让前端 statusBar 立即更新
            self._create_background_task(
                self._emit(BackendEvent.tasks_snapshot(_task_manager.list_tasks()))
            )
            # 后台任务完成且归属会话空闲 → 自动恢复处理积压通知
            if target is not None and not target.busy:
                tracker = target.engine._bg_agent_tracker
                if tracker is not None and tracker.has_completions():
                    self._create_background_task(self._auto_resume_bg(target))

        _task_manager.on_task_complete = _wrapped_on_task_complete

        # 启动写循环（单一消费者，串行化所有 WebSocket 写入）
        self._write_task = asyncio.create_task(self._write_loop())
        # 发送就绪事件
        # 计算首次登录标识（无 env_N 且无 working_directory），前端据此自动弹出配置表单
        from illusion.cli.workspace import is_first_login
        from illusion.config.settings import load_settings
        _first_login = is_first_login(load_settings())
        await self._emit(
            BackendEvent.ready(
                self._bundle.app_state.get(),
                get_task_manager().list_tasks(),
                [f"/{command.name}" for command in self._bundle.commands.list_commands()],
                first_login=_first_login,
            )
        )
        # 发送状态快照
        await self._emit(self._status_snapshot())
        # Web 前端专属：ready 后推送会话列表（含内存会话与活跃标记）
        await self._push_sessions()
        # Web 前端专属：ready 后推送工作区列表（默认 + 注册目录）
        await self._push_workspaces()
        # Web 前端专属：ready 后推送活跃会话的转录与状态（前端据此
        # materialize 当前会话视图；若为全新会话则为空转录）
        assert self._active_session_id is not None
        active_session = self._sessions[self._active_session_id]
        await self._emit(
            BackendEvent(
                type="web_restore_completed",
                session_id=active_session.session_id,
                items=build_replay_items(active_session.engine.messages),  # type: ignore[arg-type]
                state=self._session_state_payload(active_session),
            ),
            session_id=active_session.session_id,
        )
        # Web 前端专属：ready 后推送资源与模型选项（替代旧 setTimeout 串行发指令 hack）；
        # 资源随活跃会话所属工作区（初始为默认工作区）
        _ready_bundle = self._active_bundle() or self._bundle
        await self._web_api._push_resources(_ready_bundle)
        await self._web_api._push_models(_ready_bundle)

        # 版本更新检查：ready 后异步查询 PyPI（to_thread 不阻塞连接流程），
        # 有新版本则通过 update_available 事件推送
        self._create_background_task(self._push_update_notice())

        # 创建请求读取任务
        reader = asyncio.create_task(self._read_requests())

        # 创建定期状态更新任务（每秒刷新一次，用于 agent 计数等实时状态）
        async def _periodic_status_update() -> None:
            while self._running and not self._ws_closed:
                await asyncio.sleep(1.0)
                if self._running and not self._ws_closed and self._bundle is not None:
                    await self._emit(self._status_snapshot())

        self._periodic_task = asyncio.create_task(_periodic_status_update())

        # 创建 cron 委托拉取循环：周期领取指定会话执行的 cron 任务并在
        # 本地会话中执行（busy 转化、会话列表刷新天然同步）。守护进程
        # 未运行时循环静默跳过（任务由守护进程回退为子进程执行）。
        self._cron_poll_task = asyncio.create_task(
            self._cron_delegation_poll(), name="cron-delegation-poll"
        )

        try:
            # 主循环：处理请求
            while self._running:
                request = await self._request_queue.get()
                try:
                    should_continue = await self._dispatch_request(request)
                except asyncio.CancelledError:
                    # 主循环自身被取消（如 uvicorn shutdown / disconnect 关闭路径）：
                    # 必须重新抛出。asyncio 取消是"一次性"的，吞掉后下一次
                    # _request_queue.get() 会永久阻塞，后端无法退出。
                    # （行任务取消由 reader 的 stop 分支直接处理，不会到达此处。）
                    raise
                except Exception:
                    # 请求级异常不应拖垮后端进程（与 backend_host 主循环同理）：
                    # 未捕获异常会让进程异常退出，Windows 上解释器 shutdown 期间
                    # daemon 线程竞争 stdio 缓冲锁会触发原生崩溃（0xC0000005）。
                    log.exception("处理请求异常: type=%s", request.type)
                    should_continue = True
                    try:
                        await self._emit(BackendEvent(type="error", message="Internal error, please retry"))
                        await self._emit(BackendEvent(type="line_complete"))
                    except Exception:
                        log.exception("发送错误事件失败")
                if not should_continue:
                    break
        finally:
            # 清理资源：取消 reader，_shutdown 处理其余 task/队列，最后关闭运行时
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("读取任务关闭异常")
            await self._shutdown()
            # 关闭所有工作区 bundle（含默认 bundle，去重后逐个 close_runtime）
            await self._close_workspace_bundles()
        return 0

    async def _dispatch_request(self, request: FrontendRequest) -> bool:
        """处理单个前端请求。

        Args:
            request: 前端请求

        Returns:
            bool: 是否继续主循环（False 表示请求要求关闭后端）
        """
        # Web 前端专属请求：委托给 WebApiDispatcher（与 terminal 路径隔离）
        if request.type.startswith("web_"):
            await self._web_api.handle(request)
            return True
        # 关闭请求
        if request.type == "shutdown":
            await self._emit(BackendEvent(type="shutdown"))
            return False
        # 停止指定会话的任务（request.session_id 缺省时回退到活跃会话）
        if request.type == "stop":
            await self._stop_active_line(request.session_id)
            return True
        # 权限响应
        if request.type == "permission_response":
            future = (
                self._permission_requests.pop(request.request_id, None)
                if request.request_id
                else None
            )
            if future is not None and not future.done():
                future.set_result(bool(request.allowed))
            # 会话级允许：加入本会话工具集合（不持久化）
            if request.session_allow and request.tool_name:
                self._session_allowed_tools.add(request.tool_name)
            await self._emit(BackendEvent(type="modal_request", modal=None))
            return True
        # 用户问答响应
        if request.type == "question_response":
            if request.request_id in self._question_requests:
                answer: str | dict[Any, Any] = request.answer or ""
                # 尝试解析 JSON 格式的多选答案
                try:
                    parsed = json.loads(answer) if isinstance(answer, str) else answer
                    if isinstance(parsed, dict):
                        answer = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
                question_future = (
                    self._question_requests.pop(request.request_id, None)
                    if request.request_id
                    else None
                )
                if question_future is not None and not question_future.done():
                    question_future.set_result(answer)
            await self._emit(BackendEvent(type="modal_request", modal=None))
            return True
        # 列出会话
        if request.type == "list_sessions":
            await self._handle_list_sessions()
            return True
        # 选择命令
        if request.type == "select_command":
            session = self._resolve_session(request.session_id)
            if session is not None:
                await self._handle_select_command(request.command or "", session)
            return True
        # 应用选择命令（按会话隔离，fire-and-forget，不阻塞主循环）
        if request.type == "apply_select_command":
            session = self._resolve_session(request.session_id)
            if session is None:
                return True
            if session.busy:
                await self._emit(
                    BackendEvent(type="error", message="Session is busy"),
                    session_id=session.session_id,
                )
                return True
            session.busy = True
            self._spawn_session_line(
                session,
                self._apply_select_command(
                    session,
                    request.command or "",
                    request.value or "",
                    request_id=getattr(request, "request_id", None),
                ),
            )
            return True
        # agent 向导
        if request.type == "agent_wizard_init":
            await self._handle_agent_wizard_init(request)
            return True
        if request.type == "agent_wizard_submit":
            await self._handle_agent_wizard_submit(request)
            return True
        if request.type == "agent_generate_request":
            await self._handle_agent_generate_request(request)
            return True
        # GoalBar 状态栏操作（pause/resume/edit/clear；terminal 走 /goal 命令）
        if request.type == "goal_action":
            await self._handle_goal_action(request)
            return True
        # 未知请求类型
        if request.type != "submit_line":
            await self._emit(
                BackendEvent(type="error", message=f"Unknown request type: {request.type}")
            )
            return True
        # 提交行（按会话隔离，fire-and-forget，不阻塞主循环）
        session = self._resolve_session(request.session_id)
        if session is None:
            return True
        if session.busy:
            await self._emit(
                BackendEvent(type="error", message="Session is busy"),
                session_id=session.session_id,
            )
            return True
        line = (request.line or "").strip()
        if not line:
            return True
        session.busy = True
        if request.treat_as_text:
            # treat_as_text=True 时跳过命令注册表，直接当 user 消息提交给 LLM
            # （前端非指定命令如 /resume、/model 走此路径，不被当作命令执行）
            self._spawn_session_line(session, self._submit_line_as_text(session, line))
        else:
            self._spawn_session_line(session, self._process_line(session, line))
        return True

    async def _read_requests(self) -> None:
        """从 WebSocket 读取请求。"""
        while self._running:
            try:
                payload = await self._websocket.receive_text()
            except WebSocketDisconnect:
                self._ws_closed = True
                # 入队 shutdown 请求以唤醒主循环（可能正阻塞在 _request_queue.get()）
                await self._request_queue.put(FrontendRequest(type="shutdown"))
                await self._shutdown()
                return
            except (RuntimeError, OSError):
                self._ws_closed = True
                self._running = False
                log.warning("WebSocket read error, shutting down")
                await self._request_queue.put(FrontendRequest(type="shutdown"))
                return
            payload = payload.strip()
            if not payload:
                continue
            try:
                request = FrontendRequest.model_validate_json(payload)
                log.info("_read_requests: 解析请求 type=%s", request.type)
            except ValidationError as exc:  # 防御性协议处理
                log.warning("_read_requests: 请求解析失败: %s, payload=%s", exc, payload[:200])
                await self._emit(BackendEvent(type="error", message=f"Invalid request: {exc}"))
                continue

            # 立即解析模态对话框交互以避免死锁
            # 主循环在 _process_line() 中等待用户输入
            if request.type == "permission_response":
                if request.request_id in self._permission_requests:
                    self._permission_requests[request.request_id].set_result(bool(request.allowed))
                if request.session_allow and request.tool_name:
                    self._session_allowed_tools.add(request.tool_name)
                await self._emit(BackendEvent(type="modal_request", modal=None))
                continue
            if request.type == "stop":
                await self._stop_active_line(request.session_id)
                continue
            if request.type == "question_response":
                if request.request_id in self._question_requests:
                    self._question_requests[request.request_id].set_result(request.answer or "")
                await self._emit(BackendEvent(type="modal_request", modal=None))
                continue

            await self._request_queue.put(request)

    async def _make_render_event(self, session: SessionRuntime) -> Callable[[StreamEvent], Awaitable[None]]:
        """创建会话级流式事件渲染器。

        返回一个 _render_event 闭包，供 _process_line / _submit_line_as_text /
        _process_bg_completions 共用，消除重复代码并确保 TodoWrite/
        plan_mode_change 等事件处理一致。所有事件携带会话 ID，供前端
        按会话路由。

        Args:
            session: 目标会话运行时

        Returns:
            异步事件渲染函数
        """
        session_id = session.session_id

        async def _render_event(event: StreamEvent) -> None:
            """渲染流式事件。"""
            # 助手文本增量
            if isinstance(event, AssistantTextDelta):
                reasoning = getattr(event, "reasoning", None)
                await self._emit(
                    BackendEvent(
                        type="assistant_delta",
                        message=event.text,
                        reasoning=reasoning if reasoning else None,
                    ),
                    session_id=session_id,
                )
                return
            # 助手回合完成
            if isinstance(event, AssistantTurnComplete):
                reasoning = event.message.thinking_text
                cleaned = _strip_tool_previews(event.message.text.strip(), event.message.tool_uses)
                await self._emit(
                    BackendEvent(
                        type="assistant_complete",
                        message=cleaned,
                        reasoning=reasoning if reasoning else None,
                        # 该回合后是否跟随工具链：有 tool_use 则为中间步骤（true），
                        # 无 tool_use 则为最终答案（false，前端据此立即退出 busy）
                        tool_chain_follows=bool(event.message.tool_uses),
                        item=TranscriptItem(
                            role="assistant",
                            text=cleaned,
                            reasoning=reasoning if reasoning else None,
                        ),
                    ),
                    session_id=session_id,
                )
                await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
                # 透传最新累积用量与反推值到前端
                if self._bundle is not None:
                    sync_app_state(session.bundle)
                    # 更新会话 meta（CheckpointStore 已在 query_engine 内每轮 append）
                    from illusion.ui.runtime import _update_session_meta
                    _update_session_meta(session.bundle)
                    # 刷新列表展示字段并推送：每轮回复完成后右栏的
                    # 上下文用量/输入输出/缓存分项随 web_sessions 即时更新，
                    # 不必等整轮行任务结束
                    self._refresh_session_display(session)
                    await self._push_sessions()
                return
            # 工具链开始
            if isinstance(event, ToolChainStarted):
                await self._update_phase(session, "tool_executing")
                await self._emit(
                    BackendEvent(type="tool_chain_started", tool_count=event.tool_count),
                    session_id=session_id,
                )
                return
            # 工具链完成
            if isinstance(event, ToolChainCompleted):
                await self._update_phase(session, "thinking")
                await self._emit(
                    BackendEvent(type="tool_chain_completed", phase="thinking"),
                    session_id=session_id,
                )
                return
            # 工具开始执行
            if isinstance(event, ToolExecutionStarted):
                tool_use_id = getattr(event, "tool_use_id", "") or ""
                if event.tool_input:
                    session.last_tool_inputs[event.tool_name] = event.tool_input
                if tool_use_id and tool_use_id in session.emitted_tool_started_ids:
                    if event.tool_input:
                        await self._emit(
                            BackendEvent(
                                type="tool_input_updated",
                                tool_name=event.tool_name,
                                tool_input=event.tool_input,
                                tool_use_id=tool_use_id,
                            ),
                            session_id=session_id,
                        )
                    return
                if tool_use_id:
                    session.emitted_tool_started_ids.add(tool_use_id)
                await self._emit(
                    BackendEvent(
                        type="tool_started",
                        tool_name=event.tool_name,
                        tool_input=event.tool_input,
                        item=TranscriptItem(
                            role="tool",
                            tool_name=event.tool_name,
                            tool_input=event.tool_input if event.tool_input else None,
                            tool_use_id=tool_use_id or None,
                            text=f"{event.tool_name} {json.dumps(event.tool_input, ensure_ascii=True)}"
                            if event.tool_input
                            else event.tool_name,
                        ),
                    ),
                    session_id=session_id,
                )
                return
            # 工具进度消息（对称于 backend_host，转发为 tool_progress 事件）
            if isinstance(event, ToolProgressEvent):
                await self._emit(
                    BackendEvent(
                        type="tool_progress",
                        tool_use_id=event.tool_use_id or None,
                        message=event.message,
                        progress_type=event.progress_type,
                    ),
                    session_id=session_id,
                )
                return
            # 工具执行完成
            if isinstance(event, ToolExecutionCompleted):
                tool_use_id = getattr(event, "tool_use_id", "") or ""
                await self._emit(
                    BackendEvent(
                        type="tool_completed",
                        tool_name=event.tool_name,
                        output=event.output,
                        is_error=event.is_error,
                        tool_use_id=tool_use_id or None,
                        item=TranscriptItem(
                            role="tool_result",
                            text=event.output,
                            tool_name=event.tool_name,
                            is_error=event.is_error,
                            tool_use_id=tool_use_id or None,
                        ),
                    ),
                    session_id=session_id,
                )
                # === Task/Todo 双向同步 ===
                # 仅 in_process_teammate 类型参与互通；同步后再发射快照保证前端看到一致状态
                _manager = get_task_manager()
                if event.tool_name in ("TodoWrite", "todo_write"):
                    tool_input = session.last_tool_inputs.get(event.tool_name, {})
                    todos = tool_input.get("todos") or []
                    if isinstance(todos, list):
                        todo_items = []
                        for item in todos:
                            if isinstance(item, dict):
                                todo_items.append(
                                    {
                                        "content": item.get("content", ""),
                                        "status": item.get("status", "pending"),
                                        "activeForm": item.get(
                                            "activeForm", item.get("content", "")
                                        ),
                                    }
                                )
                        if (
                            all(t.get("status") == "completed" for t in todo_items)
                            and len(todo_items) >= 1
                        ):
                            todo_items = []
                        await self._emit(
                            BackendEvent(type="todo_update", todo_items=todo_items),
                            session_id=session_id,
                        )
                await self._emit(BackendEvent.tasks_snapshot(_manager.list_tasks()))
                await self._emit(self._status_snapshot())
                # 计划相关工具完成时发送 plan_mode_change 事件
                # （仅 enter_plan_mode / exit_plan_mode 两个工具存在）
                if event.tool_name in ("enter_plan_mode", "exit_plan_mode"):
                    raw_mode = session.bundle.current_settings().permission.mode.value
                    formatted_mode = format_permission_mode(raw_mode)
                    session.bundle.app_state.set(permission_mode=raw_mode)
                    await self._emit(
                        BackendEvent(type="plan_mode_change", plan_mode=formatted_mode),
                        session_id=session_id,
                    )
                    await self._emit(self._status_snapshot())
                return
            # 错误事件
            if isinstance(event, ErrorEvent):
                await self._emit(
                    BackendEvent(
                        type="transcript_item",
                        item=TranscriptItem(role="system", text=event.message),
                    ),
                    session_id=session_id,
                )
                return
            # 状态事件
            if isinstance(event, StatusEvent):
                if event.bg_agent:
                    await self._emit(
                        BackendEvent(type="bg_agent_status", message=event.message),
                        session_id=session_id,
                    )
                else:
                    await self._emit(
                        BackendEvent(
                            type="transcript_item",
                            item=TranscriptItem(role="system", text=event.message),
                        ),
                        session_id=session_id,
                    )
                return
            # goal 轮次生命周期：结构化事件（web 端 toast 呈现，不进转录）。
            # 附带后端本地化的 message：toast 文案完全由后端 i18n 生成，
            # 前端直接展示，避免浏览器语言/前端字符串副本影响显示。
            if isinstance(event, GoalStatusEvent):
                await self._emit(
                    BackendEvent(
                        type="goal_status",
                        goal_status={
                            "kind": event.kind,
                            "round": event.round,
                            "max_rounds": event.max_rounds,
                            "phase": event.phase,
                            "message": _goal_status_message(event),
                        },
                    ),
                    session_id=session_id,
                )
                return

        return _render_event

    async def _auto_resume_bg(self, session: SessionRuntime) -> None:
        """后台完成通知到达且会话空闲时，自动进入 busy 处理通知。

        修复：idle 超时/用户退出 busy 后，通知只发前端 bg_agent_status 提示
        但无人消费，只能等手动输入。此方法由 on_task_complete 包装回调调度，
        自动恢复处理该会话积压的通知。

        Args:
            session: 目标会话运行时
        """
        if session.busy or self._bundle is None:
            return
        tracker = session.engine._bg_agent_tracker
        # 仅在有实际完成通知时才恢复处理，避免任务未完成时误触发 LLM 调用
        if tracker is None or not tracker.has_completions():
            return
        session.busy = True
        try:
            await self._process_bg_completions(session)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("处理后台完成通知时出错")
            # 确保前端 busy 状态释放，避免异常路径卡死输入框
            await self._emit(BackendEvent(type="line_complete"), session_id=session.session_id)
        finally:
            session.busy = False

    async def _process_bg_completions(self, session: SessionRuntime) -> None:
        """处理积压的后台完成通知（自动进入 busy），不新增用户输入。

        Args:
            session: 目标会话运行时
        """
        assert self._bundle is not None
        # 清除上一轮的工具调用去重记录
        session.emitted_tool_started_ids.clear()
        # 更新会话阶段为思考中
        await self._update_phase(session, "thinking")

        async def _print_system(message: str) -> None:
            """打印系统消息。"""
            await self._emit(
                BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=message)),
                session_id=session.session_id,
            )

        # 复用会话级的事件渲染器（含 TodoWrite/plan_mode_change 处理）
        _render_event = await self._make_render_event(session)

        await handle_background_completions(
            session.bundle,
            print_system=_print_system,
            render_event=_render_event,
        )

        await self._finish_session_line(session)

    async def _check_post_idle_bg(self, session: SessionRuntime) -> None:
        """会话行任务结束后检查是否有后台完成通知需要自动恢复。

        弥补斜杠命令执行期间后台完成通知被跳过的缺口：命令执行完后
        会话不再 busy，但后台在命令期间完成的通知未被消费，用此方法
        触发 _auto_resume_bg 恢复处理。

        Args:
            session: 目标会话运行时
        """
        tracker = session.engine._bg_agent_tracker
        if tracker is not None and tracker.has_completions():
            self._create_background_task(self._auto_resume_bg(session))

    async def _finish_session_line(self, session: SessionRuntime) -> None:
        """收尾一轮会话行处理（状态快照 + 列表刷新 + line_complete）。

        先复位 session.busy 再发 line_complete——前端收到 line_complete 后
        可能立即发起下一次请求（如重新生成的自动重发、连续撤销），若
        busy 尚未复位会被 submit_line 的忙碌检查拒绝（"Session is busy"）。
        line_complete 提前后 busy 在回合结束瞬间释放，其余收尾事件
        （列表刷新/状态快照）随后发送，顺序无副作用。
        """
        # 绑定自动标题完成回调：_process_line 与 _submit_line_as_text 都经由
        # 本方法收尾，故在此统一绑定；后台标题任务约数秒后才完成，远晚于
        # 本方法执行，保证标题一生成即刷新侧边栏，不必等下一轮。
        if getattr(session.engine, "_title_on_generated", None) is None:
            async def _on_title_generated(_title: str) -> None:
                sr = self._sessions.get(session.session_id)
                if sr is None:
                    return
                if _title:
                    self._refresh_session_display(sr)
                await self._push_sessions()
            session.engine._title_on_generated = _on_title_generated
        # 清 busy 再推送：避免列表推送携带过期的 busy=true，
        # （前端以本地事件为准实时更新运行态，此处推送仅作兜底同步）
        session.busy = False
        await self._emit(BackendEvent(type="line_complete"), session_id=session.session_id)
        await self._update_phase(session, "idle")
        self._refresh_session_display(session)
        await self._push_sessions()
        await self._emit(self._status_snapshot())
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))

    def _refresh_session_after_submit(self, session: SessionRuntime, line: str) -> None:
        """提交消息后立即刷新会话列表显示字段并推送。

        引擎在 submit_message 时才把 user 消息写入 messages，而列表推送
        需要发生在这之前（新会话提交后应立刻出现在侧栏）。此时
        _refresh_session_display 因引擎无消息会判定为空会话，故对尚无
        摘要的新会话手动写入显示字段；已有内容的会话由后续
        assistant_complete / 行结束的常规刷新接管。

        Args:
            session: 目标会话运行时
            line: 用户提交的文本
        """
        if not session.summary:
            session.summary = line.strip()[:80]
            session.turn_count = 1
            session.message_count = 1
            ts = time.strftime("%m/%d %H:%M", time.localtime(session.created_at))
            session.label = f"{ts}  1轮  {session.summary}"
        self._create_background_task(self._push_sessions())

    async def _process_line(
        self,
        session: SessionRuntime,
        line: str,
        *,
        transcript_line: str | None = None,
        collect_output: list[str] | None = None,
    ) -> bool:
        """处理用户输入的行内容（会话隔离）。

        Args:
            session: 目标会话运行时
            line: 用户输入的行
            transcript_line: 非 None 时发送该文本为用户消息转录（静默场景传 None）
            collect_output: 非 None 时收集最终助手文本（cron 委托执行回传用）

        Returns:
            bool: 是否继续会话（始终 True，web 端退出由 shutdown 请求控制）
        """
        assert session.bundle is not None
        # 清除上一轮的工具调用去重记录
        session.emitted_tool_started_ids.clear()
        # 更新会话阶段为思考中
        await self._update_phase(session, "thinking")
        # 发送用户消息（transcript_line 为 None 时不发送转录，用于左侧栏操作等静默场景；
        # /goal 创建命令走 submit_line 时 transcript_line 为 None，但命令原文需
        # 作为真实 user 消息渲染，故例外地发送转录）
        parsed_cmd = session.bundle.commands.lookup(line)
        is_goal_create = False
        if parsed_cmd is not None and parsed_cmd[0].name == "goal":
            from illusion.commands.goal import is_goal_create_args

            is_goal_create = is_goal_create_args(parsed_cmd[1])
        if transcript_line is not None or is_goal_create:
            # 命令产物标记：按命令注册表判定而非文本前缀——用户消息也可能以
            # / 开头，前缀判断会误吞真实消息。cron 委托场景 line 为任务 prompt
            # （非命令），因此 is_command=False。/goal 创建命令原文作为真实
            # user 消息入库（record_goal_command），转录不打命令产物标记
            await self._emit(
                BackendEvent(
                    type="transcript_item",
                    item=TranscriptItem(
                        role="user",
                        text=transcript_line or line,
                        is_command=parsed_cmd is not None and not is_goal_create,
                    ),
                ),
                session_id=session.session_id,
            )
            # 提交即刷新列表：新会话立即出现在侧栏（label 带摘要），
            # 不必等行任务结束才推送
            self._refresh_session_after_submit(session, line)

        async def _print_system(message: str) -> None:
            """打印系统消息。"""
            await self._emit(
                BackendEvent(
                    type="transcript_item", item=TranscriptItem(role="system", text=message)
                ),
                session_id=session.session_id,
            )

        # 复用会话级的事件渲染器（含 TodoWrite/plan_mode_change 处理）
        _render_event = await self._make_render_event(session)
        # cron 委托执行时收集最终助手文本作为回传的 stdout
        if collect_output is not None:
            _inner_render = _render_event

            async def _render_with_collect(ev: Any) -> None:
                if isinstance(ev, AssistantTextDelta):
                    collect_output.append(ev.text or "")
                await _inner_render(ev)

            _render_event = _render_with_collect

        async def _replay_transcript_item(item: dict[str, Any]) -> None:
            """重播 transcript_item。"""
            await self._emit(
                BackendEvent(type="transcript_item", item=TranscriptItem(**item)),
                session_id=session.session_id,
            )

        async def _clear_output() -> None:
            """清空输出。"""
            await self._emit(BackendEvent(type="clear_transcript"), session_id=session.session_id)

        async def _command_result_emitter(message: str, result_type: str) -> None:
            """发射指令结果事件。"""
            data: dict[str, Any] = {
                "message": message,
                "type": result_type,
            }
            # 回传当前请求的 ID（用于前端精确匹配响应）
            req_id = session.current_request_id
            if req_id:
                data["request_id"] = req_id
                session.current_request_id = None  # 消费后清除，避免泄漏到后续事件
            await self._emit(
                BackendEvent(
                    type="command_result",
                    command_result_data=data,
                ),
                session_id=session.session_id,
            )

        async def _replace_transcript_items(items: list[dict[str, Any]]) -> None:
            """替换转录项列表（一次性清空并替换，避免 Ink Static 重复渲染）。"""
            transcript_items = [TranscriptItem(**item) for item in items]
            await self._emit(
                BackendEvent(type="replace_transcript", items=transcript_items),
                session_id=session.session_id,
            )

        async def _rewind_restored(text: str) -> None:
            """rewind 被回退的 user 消息：通知前端回填输入框（重新编辑）。"""
            await self._emit(
                BackendEvent(type="session_rewind", restored_text=text),
                session_id=session.session_id,
            )

        await handle_line(
            session.bundle,
            line,
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
            replay_transcript_item=_replay_transcript_item,
            command_result_emitter=_command_result_emitter,
            replace_transcript_items=_replace_transcript_items,
            rewind_restored_emitter=_rewind_restored,
        )

        await self._finish_session_line(session)
        return True

    # === cron 委托执行（指定会话执行的 cron 任务由本地会话接管） ===

    async def _cron_delegation_poll(self) -> None:
        """周期领取 cron 委托任务并在本地会话中执行。

        与 cron 守护进程的轮询拉取协议：每 3s 领取一次，领取到任务后在
        目标会话中执行（busy 转化、会话列表刷新天然同步），执行完上报结果。
        守护进程未运行时静默跳过（任务由守护进程回退为子进程执行）。
        """
        from illusion.services.cron_delegation import claim_delegated_job

        while self._running and not self._ws_closed:
            try:
                job = await claim_delegated_job()
                if job is not None:
                    await self._run_delegated_cron_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cron 委托拉取/执行异常")
            await asyncio.sleep(3.0)

    async def _materialize_session(self, session_id: str, cwd: str | None = None) -> SessionRuntime | None:
        """从磁盘惰性恢复会话运行时（web_restore_session 同一创建路径）。

        会话在内存中则直接返回；磁盘上不存在（meta 缺失）返回 None。
        目标工作区由 cwd 指定（前端会话条目携带），缺省时按内存/注册表
        扫描定位；定位到的工作区 bundle 不存在时懒构建。

        Args:
            session_id: 目标会话 ID
            cwd: 会话所属工作区目录（可选，优先信任前端携带值）

        Returns:
            SessionRuntime | None: 会话运行时；不存在时返回 None
        """
        cached = self._sessions.get(session_id)
        if cached is not None:
            return cached
        from illusion.services.session_storage import read_meta

        target_cwd = cwd or self._locate_session_workspace(session_id)
        if target_cwd is None:
            return None
        meta = read_meta(target_cwd, session_id)
        if not meta:
            return None
        bundle = await self._get_or_build_bundle(target_cwd)
        engine = build_session_engine(
            bundle,
            session_id,
            permission_prompt=self._make_permission_prompt(session_id),
            ask_user_prompt=self._make_ask_user_prompt(session_id),
            plan_approval_prompt=self._make_plan_approval_prompt(session_id),
        )
        from illusion.ui.runtime import build_session_bundle

        session = SessionRuntime(
            session_id=session_id,
            bundle=build_session_bundle(bundle, session_id, engine),
            workspace_cwd=bundle.cwd,
        )
        self._sessions[session_id] = session
        self._maybe_evict_sessions()
        return session

    async def _run_delegated_cron_job(self, job: dict[str, Any]) -> None:
        """在目标会话中执行委托的 cron 任务。

        - 目标会话所属工作区与 job.cwd 不一致时回报 not_supported（守护
          进程重新入队或回退子进程）；多目录空间下按会话实际所属工作区
          匹配，而非 Web 进程的全局工作目录
        - 目标会话不在内存时从磁盘惰性恢复（跨工作区定位）；不存在回报 error
        - 会话 busy（用户正在使用）时等待空闲（上限 60s）
        - 执行走 _run_session_line（busy 转化 + 完成清理 + _push_sessions
          列表刷新），执行完上报结果

        Args:
            job: 委托任务字典（含 id/session_id/prompt/cwd）
        """
        from illusion.services.cron_delegation import report_delegated_result

        if self._bundle is None:
            return
        job_id = str(job.get("id", ""))
        job_cwd = os.path.normcase(os.path.normpath(str(job.get("cwd") or "")))
        target_sid = str(job.get("session_id") or "").strip()
        prompt = str(job.get("prompt") or "").strip()
        started_at = _now_local()

        # 定位目标会话所属工作区（内存优先，注册表磁盘扫描兜底）
        owner_cwd = self._locate_session_workspace(target_sid)
        if owner_cwd is None:
            log.warning("cron 委托目标会话不存在: id=%s session=%s", job_id, target_sid)
            await report_delegated_result(job_id, {
                "status": "error",
                "returncode": -1,
                "stdout": "",
                "stderr": f"Session not found: {target_sid}",
            })
            return
        if job_cwd != os.path.normcase(os.path.normpath(owner_cwd)):
            log.info(
                "cron 委托任务与会话所属工作区不匹配，回报 not_supported: id=%s target=%s owner=%s",
                job_id, job_cwd, owner_cwd,
            )
            await report_delegated_result(job_id, {"status": "not_supported"})
            return

        session = await self._materialize_session(target_sid, owner_cwd)
        if session is None:
            log.warning("cron 委托目标会话物化失败: id=%s session=%s", job_id, target_sid)
            await report_delegated_result(job_id, {
                "status": "error",
                "returncode": -1,
                "stdout": "",
                "stderr": f"Session not found: {target_sid}",
            })
            return

        # 等待会话空闲（用户正在使用时排队，上限 60s）
        waited = 0
        while session.busy and waited < 60:
            await asyncio.sleep(1.0)
            waited += 1
        if session.busy:
            log.warning("cron 委托任务等待会话空闲超时: id=%s", job_id)
            await report_delegated_result(job_id, {
                "status": "error",
                "returncode": -1,
                "stdout": "",
                "stderr": "Session busy timeout",
            })
            return

        # 执行：走 _run_session_line（busy 转化 + 异常兜底 + 完成清理 +
        # _push_sessions 列表刷新），收集助手文本作为回传的 stdout
        session.busy = True
        collected: list[str] = []
        try:
            display = f"[cron] {prompt[:60]}"
            await self._run_session_line(
                session,
                self._process_line(
                    session,
                    prompt,
                    transcript_line=display,
                    collect_output=collected,
                ),
            )
            result: dict[str, Any] = {
                "status": "success",
                "returncode": 0,
                "stdout": "".join(collected),
                "stderr": "",
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("cron 委托任务执行异常: id=%s", job_id)
            result = {
                "status": "error",
                "returncode": -1,
                "stdout": "".join(collected),
                "stderr": "Internal error while executing delegated cron job",
            }
        finally:
            session.busy = False
            self._create_background_task(self._push_sessions())
        result["started_at"] = started_at.isoformat()
        result["ended_at"] = _now_local().isoformat()
        await report_delegated_result(job_id, result)

    async def _submit_line_as_text(self, session: SessionRuntime, line: str) -> bool:
        """直接将用户输入当文本提交给 LLM，跳过命令注册表。

        用于前端 treat_as_text=True 的 submit_line 请求（非指定命令如
        /resume、/model 等），确保输入不被 commands.lookup 匹配为命令执行，
        而是作为普通 user 消息发给 LLM。

        Args:
            session: 目标会话运行时
            line: 用户输入的文本

        Returns:
            bool: 是否继续会话（始终返回 True）
        """
        assert session.bundle is not None
        session.emitted_tool_started_ids.clear()
        await self._update_phase(session, "thinking")
        # 发送 user 消息到转录
        await self._emit(
            BackendEvent(type="transcript_item", item=TranscriptItem(role="user", text=line)),
            session_id=session.session_id,
        )
        # 提交即刷新列表：新会话立即出现在侧栏（label 带摘要），
        # 不必等行任务结束才推送
        self._refresh_session_after_submit(session, line)

        # 复用会话级的事件渲染器（含 TodoWrite/plan_mode_change 处理）
        _render_event = await self._make_render_event(session)

        # 直接调用 engine.submit_message，跳过 handle_line 的命令注册表
        from illusion.engine.query import MaxTurnsExceeded

        bundle = session.bundle
        settings = bundle.current_settings()
        bundle.engine.set_max_turns(settings.max_turns)
        from illusion.prompts import build_runtime_system_prompt

        system_prompt = build_runtime_system_prompt(
            settings,
            cwd=bundle.cwd,
            latest_user_prompt=line,
            channel_hint=bundle.channel_hint,
        )
        for ctx in bundle.hook_additional_contexts:
            if ctx:
                system_prompt = system_prompt + "\n\n" + _wrap_in_system_reminder(ctx)
        bundle.engine.set_system_prompt(system_prompt)
        try:
            async for event in bundle.engine.submit_message(line):
                await _render_event(event)
        except MaxTurnsExceeded as exc:
            await self._emit(
                BackendEvent(
                    type="transcript_item",
                    item=TranscriptItem(
                        role="system", text=f"Stopped after {exc.max_turns} turns (max_turns)."
                    ),
                ),
                session_id=session.session_id,
            )
        # 更新会话 meta（替代旧 save_session_snapshot）
        from illusion.ui.runtime import _update_session_meta
        _update_session_meta(bundle)
        sync_app_state(bundle)
        await self._finish_session_line(session)
        return True

    async def _handle_rewind_message_selected(self, session: SessionRuntime, value: str) -> bool:
        """rewind 第一步：用户选择了要回退的消息，弹出模式选择。"""
        if self._bundle is None:
            return True
        try:
            target_idx = int(value)
        except ValueError:
            return True
        session.rewind_target_idx = target_idx
        state = session.bundle.app_state.get()
        zh = str(state.ui_language or "zh-CN").lower().startswith("zh")
        options = [
            {
                "value": "both",
                "label": "回退代码与对话" if zh else "Rewind code & conversation",
                "description": "撤销文件修改并移除对话"
                if zh
                else "Revert files and remove conversation",
            },
            {
                "value": "conversation",
                "label": "仅回退对话" if zh else "Rewind conversation only",
                "description": "只移除对话，保留文件修改"
                if zh
                else "Remove conversation, keep files",
            },
            {
                "value": "code",
                "label": "仅回退代码" if zh else "Rewind code only",
                "description": "只撤销文件修改，保留对话"
                if zh
                else "Revert files, keep conversation",
            },
        ]
        await self._emit(
            BackendEvent(
                type="select_request",
                modal={
                    "kind": "select",
                    "title": "回退方式" if zh else "Rewind mode",
                    "command": "rewind_mode",
                },
                select_options=options,
            ),
            session_id=session.session_id,
        )
        return True

    async def _handle_rewind_mode_selected(self, session: SessionRuntime, value: str) -> bool:
        """rewind 第二步：用户选择了回退模式，执行回退。"""
        if self._bundle is None or session.rewind_target_idx is None:
            return True
        target_idx = session.rewind_target_idx
        session.rewind_target_idx = None
        mode = value.strip()
        if mode not in ("both", "conversation", "code"):
            return True
        messages = session.engine.messages
        # 计算 target 之后需回退的真实用户轮次（排除后台任务完成通知与 goal 注入消息；
        # 命令不会进入 engine.messages，真实 / 前缀消息须计入）
        turns = sum(
            1
            for i, msg in enumerate(messages)
            if i >= target_idx
            and msg.role == "user"
            and msg.text.strip()
            and not is_task_notification(msg.text)
            and not is_goal_system_message(msg.text)
        )
        if turns <= 0:
            return True
        return await self._process_line(session, f"/rewind {turns} {mode}", transcript_line="/rewind")

    async def _apply_select_command(
        self, session: SessionRuntime, command_name: str, value: str, request_id: str | None = None
    ) -> bool:
        """应用选择的命令值（会话隔离）。"""
        # 存储当前请求 ID，供 _command_result_emitter 回传
        session.current_request_id = request_id
        command = command_name.strip().lstrip("/").lower()
        selected = value.strip()
        # 特殊路由：context → change window 时弹出子选择器
        if command == "context" and selected == "__change_window__":
            await self._handle_select_command("context-window", session)
            return True
        # context-window → __custom__ 由前端 CustomInputModal 接管，此处不应到达
        # 防御性处理：静默忽略并提示前端关闭选择框
        if command == "context-window" and selected == "__custom__":
            await self._emit(BackendEvent(type="line_complete"), session_id=session.session_id)
            return True
        # rewind 两步选择：第一步（选消息）→ 存储目标，弹出模式选择
        if command == "rewind":
            return await self._handle_rewind_message_selected(session, selected)
        # rewind 两步选择：第二步（选模式）→ 执行回退
        if command == "rewind_mode":
            return await self._handle_rewind_mode_selected(session, selected)
        # resume 命令：独立处理，不通过 _process_line，避免触发输入框命令交互
        if command == "resume":
            return await self._restore_session(session, selected)
        line = self._build_select_command_line(command, selected)
        if line is None:
            await self._emit(
                BackendEvent(type="error", message=f"Unknown select command: {command_name}"),
                session_id=session.session_id,
            )
            await self._emit(BackendEvent(type="line_complete"), session_id=session.session_id)
            return True
        return await self._process_line(session, line, transcript_line=f"/{command}")

    async def _restore_session(self, session: SessionRuntime, session_id: str) -> bool:
        """恢复会话（apply_select_command 的 resume 分支）。

        多会话架构下恢复操作作用于目标会话的独立运行时（与
        web_restore_session 同一流程），不污染当前会话的引擎。

        Args:
            session: 当前会话运行时（兼容旧签名，恢复目标由 session_id 指定）
            session_id: 要恢复的会话 ID

        Returns:
            bool: 是否继续会话（始终 True）
        """
        await self._web_api.handle_web_restore_session(
            FrontendRequest(type="web_restore_session", session_id=session_id)
        )
        return True

    def _build_select_command_line(self, command: str, value: str) -> str | None:
        """构建选择命令的实际命令字符串。"""
        if command == "env":
            return f"/env {value}"
        if command == "resume":
            return f"/resume {value}" if value else "/resume"
        if command == "permissions":
            return f"/permissions {value}"
        if command == "language":
            return f"/language {value}"
        if command == "effort":
            return f"/effort {value}"
        if command == "max-tokens":
            # custom 由前端转为数字字符串，直接透传
            return f"/max-tokens {value}"
        if command == "turns":
            return f"/turns {value}"
        if command == "agent":
            return f"/agent {value}"
        if command == "model":
            return f"/model set {value}"
        if command == "delete":
            if value == "__all__":
                return "/delete all"
            return f"/delete {value}"
        if command == "rules":
            return f"/rules {value}"
        if command == "skills":
            return f"/skills {value}"
        if command == "context":
            if value == "__usage__":
                return "/context __usage__"
            return None
        if command == "context-window":
            return f"/context set {value}"
        return None

    def _status_snapshot(self) -> BackendEvent:
        """生成全局状态快照事件（工具栏级：model/effort/language 等）。

        多工作区：快照取自当前活跃会话所在 bundle（cwd/mcp 状态等随
        活跃目录切换），无活跃会话时回退默认 bundle。

        会话专属字段（上下文用量/输入输出/缓存分项）从全局快照中剔除，
        由 web_sessions / web_restore_completed 按会话推送，避免多会话
        并发时把某会话的上下文数据张冠李戴到其他会话。
        """
        assert self._bundle is not None
        bundle = self._active_bundle() or self._bundle
        from illusion.ui.protocol import _state_payload

        payload = _state_payload(bundle.app_state.get())
        for key in _SESSION_SCOPED_STATE_KEYS:
            payload.pop(key, None)
        return BackendEvent(
            type="state_snapshot",
            state=payload,
            mcp_servers=[
                {
                    "name": server.name,
                    "state": server.state,
                    "detail": server.detail,
                    "transport": server.transport,
                    "auth_configured": server.auth_configured,
                    "tool_count": len(server.tools),
                    "resource_count": len(server.resources),
                }
                for server in bundle.mcp_manager.list_statuses()
            ],
        )

    def _session_state_payload(self, session: SessionRuntime) -> dict[str, Any]:
        """生成会话级状态载荷。

        在会话所在 bundle 的 app_state 载荷基础上，用会话引擎的实时数据
        覆盖 session_id / phase / context_tokens / usage 等会话专属字段，
        避免多会话并发时共享 app_state 的字段互相污染。

        Args:
            session: 目标会话运行时

        Returns:
            dict[str, Any]: 会话状态载荷
        """
        from illusion.ui.protocol import _state_payload

        payload = _state_payload(session.bundle.app_state.get())
        payload["session_id"] = session.session_id
        payload["phase"] = session.phase
        engine = session.engine
        payload["goal"] = engine.goal_status_payload()
        payload["context_tokens"] = engine.current_context_tokens()
        # 最后一次 API 调用的真实分项：引擎无调用记录时显式置 0。
        # 不能依赖 app_state 的值——全局 app_state 可能残留其他会话 sync 的
        # 分项数据，新建会话（无 last_api_usage）时会显示上一会话的旧值。
        last_usage = engine.last_api_usage
        payload["context_cache_read"] = last_usage.cache_read_input_tokens if last_usage else 0
        payload["context_cache_creation"] = last_usage.cache_creation_input_tokens if last_usage else 0
        payload["context_input"] = last_usage.input_tokens if last_usage else 0
        payload["context_output"] = last_usage.output_tokens if last_usage else 0
        total = engine.total_usage
        payload["input_tokens"] = total.input_tokens
        payload["output_tokens"] = total.output_tokens
        payload["cache_read_input_tokens"] = total.cache_read_input_tokens
        payload["cache_creation_input_tokens"] = total.cache_creation_input_tokens
        return payload

    def _refresh_session_display(self, session: SessionRuntime) -> None:
        """刷新会话列表展示字段（label/summary/title/turn_count/message_count）。

        以会话引擎实时数据为准（对话进行中摘要/轮数即时可见），
        引擎无消息时回退到磁盘 meta。
        title（自定义名称）优先于 summary 用于 label 显示。

        Args:
            session: 目标会话运行时
        """
        assert self._bundle is not None
        from illusion.engine.messages import ToolResultBlock
        from illusion.services.session_storage import read_meta

        zh = str(
            session.bundle.app_state.get().ui_language or session.bundle.current_settings().ui_language
        ).lower().startswith("zh")
        engine = session.engine
        messages = engine.messages
        # 摘要：第一条真实用户消息（排除后台任务完成通知与 goal 注入消息；命令
        # 不会进入 engine.messages，真实 / 前缀消息须计入摘要）
        summary = ""
        for msg in messages:
            if msg.role == "user" and msg.text.strip():
                if is_task_notification(msg.text) or is_goal_system_message(msg.text):
                    continue
                summary = msg.text.strip()[:80]
                break
        # 回退：首条真实用户消息不存在（如 goal 注入消息开局）时摘要为空，
        # 用当前 goal 的 objective 兜底，避免会话标题显示"新会话"
        if not summary:
            goal_manager = engine.goal_manager
            if goal_manager is not None and goal_manager.snapshot is not None:
                summary = goal_manager.snapshot.objective.strip()[:80]
        # 轮数：真正由用户输入的消息数（与 _update_session_meta 口径一致）。
        # /goal 命令原文已作为真实 user 消息入库（record_goal_command），计入轮次；
        # goal 自动续跑的 <goal_round> 注入消息非用户输入，不再单独加成
        turn_count = sum(
            1
            for m in messages
            if m.role == "user"
            and not any(isinstance(b, ToolResultBlock) for b in m.content)
            and m.text.strip()
            and not is_task_notification(m.text)
            and not is_goal_system_message(m.text)
        )
        message_count = len(messages)
        # 读取磁盘 meta 获取自定义 title（rename 写入的名称）——
        # 按会话所属工作区读取（多目录空间下各目录会话分区存储）
        meta = None
        try:
            meta = read_meta(session.bundle.cwd, session.session_id)
        except (OSError, ValueError):
            meta = None
        title = (meta or {}).get("title") or ""
        session.summary = summary
        session.title = title
        session.turn_count = turn_count
        session.message_count = message_count
        session.context_tokens = engine.current_context_tokens()
        # label：title 优先于 summary
        display = title or summary
        if display:
            ts = time.strftime("%m/%d %H:%M", time.localtime(session.created_at))
            session.label = f"{ts}  {turn_count}轮  {display}"
        elif meta and meta.get("summary"):
            ts = time.strftime("%m/%d %H:%M", time.localtime(meta.get("created_at") or session.created_at))
            session.label = f"{ts}  {meta.get('turn_count', 0)}轮  {meta.get('summary', '')}"
        else:
            session.label = "新会话" if zh else "New session"

    # === 工作区（目录空间）管理 ===

    def _init_workspace_states(self) -> None:
        """按注册表初始化工作区状态（默认工作区挂接已构建的 bundle）。"""
        from illusion.services import workspace_registry

        self._workspaces.clear()
        default_key = _cwd_key(self._bundle.cwd) if self._bundle is not None else ""
        for view in workspace_registry.resolve_workspace_views():
            cwd = view["path"]
            state = _WorkspaceState(cwd=cwd)
            if default_key and _cwd_key(cwd) == default_key:
                state.bundle = self._bundle
                state.bundle_built_at = time.time()
            self._workspaces[_cwd_key(cwd)] = state

    def _sync_workspace_states_from_registry(self) -> None:
        """注册表变更后同步工作区状态。

        新增条目懒建（bundle=None）；被移除的条目丢弃状态并异步关闭其
        bundle（若已构建）。默认工作区恒在注册表视图中，不会被移除。
        """
        from illusion.services import workspace_registry

        views = workspace_registry.resolve_workspace_views()
        valid_keys: set[str] = set()
        for view in views:
            cwd = view["path"]
            key = _cwd_key(cwd)
            valid_keys.add(key)
            if key not in self._workspaces:
                self._workspaces[key] = _WorkspaceState(cwd=cwd)
        for key in list(self._workspaces):
            if key in valid_keys:
                continue
            state = self._workspaces.pop(key)
            if state.bundle is not None and state.bundle is not self._bundle:
                self._create_background_task(_close_bundle_quietly(state.bundle))

    def _resolve_workspace_cwd(self, cwd: str | None) -> str:
        """把请求携带的 cwd 归一化到已知工作区；未知/缺省回退默认工作区。

        默认工作区动态解析（不缓存）：用户可在设置中随时修改
        working_directory（PATCH /api/settings/working_directory 不通知
        host），缓存会与实际配置分叉。
        """
        if cwd:
            state = self._workspaces.get(_cwd_key(cwd))
            if state is not None:
                return state.cwd
        from illusion.services import workspace_registry

        default = workspace_registry.get_default_workspace()
        if default:
            return default
        if self._bundle is not None:
            return self._bundle.cwd
        return os.getcwd()

    def _workspace_bundle_for(self, cwd: str | None) -> RuntimeBundle | None:
        """返回指定工作区已构建的 bundle（未构建/未知返回 None）。"""
        if not cwd:
            return None
        state = self._workspaces.get(_cwd_key(cwd))
        return state.bundle if state is not None else None

    def _locate_session_workspace(self, session_id: str) -> str | None:
        """定位会话所属的工作区目录（内存会话优先，磁盘 meta 兜底扫描）。"""
        session = self._sessions.get(session_id)
        if session is not None:
            return session.bundle.cwd
        from illusion.services.session_storage import read_meta

        for state in self._workspaces.values():
            if read_meta(state.cwd, session_id):
                return state.cwd
        return None

    async def _get_or_build_bundle(self, cwd: str) -> RuntimeBundle:
        """获取工作区 bundle，未构建时懒构建（并发调用共享同一构建任务）。"""
        key = _cwd_key(cwd)
        state = self._workspaces.get(key)
        if state is None:
            state = _WorkspaceState(cwd=cwd)
            self._workspaces[key] = state
        state.last_used = time.time()
        if state.bundle is not None:
            return state.bundle
        if state.building is not None and not state.building.done():
            return await asyncio.shield(state.building)

        async def _build() -> RuntimeBundle:
            bundle = await build_runtime(
                model=self._config.model,
                max_turns=self._config.max_turns,
                base_url=self._config.base_url,
                system_prompt=self._config.system_prompt,
                api_key=self._config.api_key,
                api_format=self._config.api_format,
                api_client=self._config.api_client,
                effort=self._config.effort,
                channel_hint=self._config.channel_hint,
                channel_tools=self._config.channel_tools,
                cwd=state.cwd,
            )
            await start_runtime(bundle)
            sync_app_state(bundle)
            # 同步 UI 语言：全局设置在所有 bundle 的 app_state 间保持一致
            if self._bundle is not None:
                lang = self._bundle.app_state.get().ui_language
                if lang:
                    bundle.app_state.set(ui_language=lang)
            return bundle

        state.building = asyncio.create_task(_build(), name="workspace-bundle-build")
        try:
            bundle = await state.building
        finally:
            state.building = None
        state.bundle = bundle
        state.bundle_built_at = time.time()
        return bundle

    def _active_bundle(self) -> RuntimeBundle | None:
        """当前活跃会话所在的 bundle（无活跃会话时回退默认 bundle）。"""
        session = self._active_session()
        if session is not None:
            return session.bundle
        return self._bundle

    def _workspace_bundles(self) -> list[RuntimeBundle]:
        """所有已构建的工作区 bundle（按对象去重）。"""
        bundles: list[RuntimeBundle] = []
        seen: set[int] = set()
        for state in self._workspaces.values():
            if state.bundle is not None and id(state.bundle) not in seen:
                seen.add(id(state.bundle))
                bundles.append(state.bundle)
        if self._bundle is not None and id(self._bundle) not in seen:
            seen.add(id(self._bundle))
            bundles.append(self._bundle)
        return bundles

    async def _close_workspace_bundles(self) -> None:
        """关闭所有已构建的工作区 bundle（连接生命周期结束时调用）。"""
        bundles = self._workspace_bundles()
        for state in self._workspaces.values():
            state.bundle = None
        if bundles:
            await asyncio.gather(*(_close_bundle_quietly(b) for b in bundles))

    def _evict_idle_workspace_bundles(self) -> None:
        """关闭空闲工作区的 bundle（无物化会话且非活跃目录且过宽限期）。"""
        active_key: str | None = None
        session = self._active_session()
        if session is not None:
            active_key = _cwd_key(session.bundle.cwd)
        now = time.time()
        used_keys = {_cwd_key(sr.bundle.cwd) for sr in self._sessions.values()}
        for state in list(self._workspaces.values()):
            bundle = state.bundle
            if bundle is None or state.building is not None:
                continue
            if bundle is self._bundle:
                continue  # 默认 bundle 由 run() 生命周期管理
            key = _cwd_key(state.cwd)
            if key == active_key or key in used_keys:
                continue
            if now - state.bundle_built_at < _WORKSPACE_BUNDLE_GRACE_SECONDS:
                continue
            state.bundle = None
            self._create_background_task(_close_bundle_quietly(bundle))

    async def _push_workspaces(self) -> None:
        """推送工作区列表（默认 + 注册目录，含可用性与默认标记）。

        推送前重新同步工作区状态：默认工作区可能已被设置修改
        （settings.working_directory PATCH），新默认目录需建立 state，
        被替换的旧默认目录（未注册）需移除并关闭其 bundle。
        """
        from illusion.services import workspace_registry

        self._sync_workspace_states_from_registry()
        views = await asyncio.to_thread(workspace_registry.resolve_workspace_views)
        await self._emit(BackendEvent(type="web_workspaces", web_workspaces=views))

    async def _push_sessions(self) -> None:
        """推送会话列表（全部工作区：磁盘快照 + 内存运行时合并）。

        内存会话（含空会话）始终显示且携带 busy/phase/active 等实时状态；
        未 materialized 的磁盘会话标记 in_memory=False，由前端惰性恢复。
        每个条目携带 cwd（所属工作区目录），供前端按目录分组渲染。
        """
        bundle = self._bundle
        if bundle is None:
            return
        from illusion.services.session_storage import list_session_snapshots

        lang_bundle = self._active_bundle() or bundle
        locale = str(lang_bundle.app_state.get().ui_language or lang_bundle.current_settings().ui_language)
        zh = locale.lower().startswith("zh")
        # 磁盘扫描（iterdir + 解析 meta.json）移到线程池，避免阻塞事件循环
        # （_push_sessions 在每次行任务结束/模态等待时调用，频率较高）；
        # 多工作区并行扫描，任一目录缺失/损坏只影响该目录（返回空列表）
        cwds = [state.cwd for state in self._workspaces.values()] or [bundle.cwd]
        snapshots_lists = await asyncio.gather(
            *(asyncio.to_thread(list_session_snapshots, cwd, 50) for cwd in cwds)
        )
        disk: dict[str, dict[str, Any]] = {}
        disk_cwd: dict[str, str] = {}
        for cwd, snapshots in zip(cwds, snapshots_lists):
            for s in snapshots:
                sid = s["session_id"]
                if sid not in disk:
                    disk[sid] = s
                    disk_cwd[sid] = cwd
        options: list[dict[str, Any]] = []
        seen: set[str] = set()
        # 已移除工作区的内存会话不再展示：其目录不在 _workspaces 中，
        # 会话列表应随之消失（磁盘会话本就只按 _workspaces 扫描）
        known_keys = set(self._workspaces.keys())
        for sr in sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True):
            if _cwd_key(sr.bundle.cwd) not in known_keys:
                continue
            # 跳过无内容的纯内存空会话：列表只展示有内容的会话，
            # 空会话（未发消息）由主区域承载，避免出现"新会话"占位条目
            if sr.message_count == 0 and sr.session_id not in disk:
                continue
            seen.add(sr.session_id)
            if not sr.label:
                self._refresh_session_display(sr)
            # 会话级上下文/用量实时数据（供右栏展示，行任务结束时随列表刷新）
            total = sr.engine.total_usage
            last_usage = sr.engine.last_api_usage
            options.append({
                "id": sr.session_id,
                "label": sr.label or ("新会话" if zh else "New session"),
                "created_at": sr.created_at,
                "message_count": sr.message_count,
                "turn_count": sr.turn_count,
                "summary": sr.summary,
                "title": sr.title,
                "busy": sr.busy,
                "phase": sr.phase,
                "active": sr.session_id == self._active_session_id,
                "in_memory": True,
                "cwd": sr.bundle.cwd,
                "context_tokens": sr.context_tokens,
                "input_tokens": total.input_tokens,
                "output_tokens": total.output_tokens,
                "cache_read_input_tokens": total.cache_read_input_tokens,
                "cache_creation_input_tokens": total.cache_creation_input_tokens,
                "context_cache_read": last_usage.cache_read_input_tokens if last_usage else 0,
                "context_cache_creation": last_usage.cache_creation_input_tokens if last_usage else 0,
                "context_input": last_usage.input_tokens if last_usage else 0,
                "context_output": last_usage.output_tokens if last_usage else 0,
                "goal": sr.engine.goal_status_payload(),
            })
        # 磁盘上存在但未 materialized 的会话
        for sid, meta in disk.items():
            if sid in seen:
                continue
            ts = time.strftime("%m/%d %H:%M", time.localtime(meta.get("created_at", 0)))
            title = meta.get("title") or ""
            summary = (meta.get("summary", "") or ("（无摘要）" if zh else "(no summary)"))[:50]
            display = title or summary
            options.append({
                "id": sid,
                "label": f"{ts}  {meta.get('turn_count', 0)}轮  {display}",
                "created_at": meta.get("created_at", 0),
                "message_count": meta.get("message_count", 0),
                "turn_count": meta.get("turn_count", 0),
                "summary": meta.get("summary", ""),
                "title": title,
                "busy": False,
                "phase": "idle",
                "active": False,
                "in_memory": False,
                "cwd": disk_cwd.get(sid, ""),
                "context_tokens": 0,
            })
        await self._emit(BackendEvent(
            type="web_sessions",
            web_sessions=options,
            active_session_id=self._active_session_id,
        ))

    # === 会话运行时管理 ===

    def _resolve_session(self, session_id: str | None) -> SessionRuntime | None:
        """按 ID 解析会话运行时，缺省回退到活跃会话。

        Args:
            session_id: 会话 ID（可为 None）

        Returns:
            SessionRuntime | None: 会话运行时；无活跃会话时返回 None
        """
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        if self._active_session_id and self._active_session_id in self._sessions:
            return self._sessions[self._active_session_id]
        return None

    def _active_session(self) -> SessionRuntime | None:
        """返回当前活跃会话运行时。"""
        if self._active_session_id and self._active_session_id in self._sessions:
            return self._sessions[self._active_session_id]
        return None

    async def _handle_goal_action(self, request: FrontendRequest) -> None:
        """处理 GoalBar 的 pause/resume/edit/clear 操作。

        这些是人类操作（human 权威），带 CAS（goal_id + revision，前端从
        当前会话 goal 状态调用时读取）。成功后立即推送会话级 state_snapshot
        与 goal_action_result；失败行内回执。成功/失败均补发 command_result
        toast（文案与 terminal 的 goal_action_* 一致，明确不打断当前轮语义）。

        Args:
            request: goal_action 请求
        """
        from illusion.config.i18n import t as _t
        from illusion.goal.types import GoalError

        _RESULT_KEYS = {
            "pause": "goal_action_paused",
            "resume": "goal_action_resumed",
            "edit": "goal_action_edited",
            "clear": "goal_action_cleared",
        }

        action = request.goal_action or ""
        session = self._resolve_session(request.session_id)
        if session is None:
            await self._emit(BackendEvent(
                type="goal_action_result",
                success=False,
                goal_action=action,
                goal_error={"code": "no-session", "message": "no active session"},
            ))
            return
        engine = session.engine
        manager = engine.goal_manager
        if manager is None:
            await self._emit(BackendEvent(
                type="goal_action_result",
                success=False,
                goal_action=action,
                goal_error={"code": "goal-disabled", "message": "goal feature is disabled"},
            ), session_id=session.session_id)
            return
        # GoalBar 按钮是人类操作：权威来源切换为 human
        manager.current_source = "human"
        try:
            if action == "clear":
                manager.clear(request.goal_id, request.revision)
            else:
                # pause/resume/edit 需要精确 CAS（goal_id + revision）；缺失时拒绝
                if (
                    not request.goal_id
                    or not isinstance(request.revision, int)
                    or isinstance(request.revision, bool)
                    or request.revision < 1
                ):
                    raise GoalError(
                        "goal_id/revision are required for this goal action",
                        code="GOAL_TOOL_INVALID_UPDATE",
                    )
                gid: str = request.goal_id
                rev: int = request.revision
                if action == "pause":
                    manager.pause(gid, rev)
                elif action == "resume":
                    manager.resume(gid, rev)
                elif action == "edit":
                    objective = (request.objective or "").strip()
                    if not objective:
                        raise GoalError(
                            "objective must be a non-empty string",
                            code="GOAL_TOOL_INVALID_UPDATE",
                        )
                    manager.edit(gid, rev, objective=objective)
                else:
                    raise GoalError(
                        f"unknown goal action: {action}",
                        code="GOAL_TOOL_INVALID_UPDATE",
                    )
        except GoalError as exc:
            await self._emit(BackendEvent(
                type="goal_action_result",
                success=False,
                goal_action=action,
                goal_error={"code": exc.code, "message": exc.message},
            ), session_id=session.session_id)
            await self._emit(BackendEvent(
                type="command_result",
                command_result_data={
                    "message": _t("goal_action_failed", message=exc.message),
                    "type": "error",
                },
            ), session_id=session.session_id)
            return
        # 成功：goal 状态落盘并立即推送（不等行任务边界）
        store = engine.checkpoint_store
        if store is not None and manager.dirty:
            await store.append_goal(manager.persisted_state())
        await self._emit(BackendEvent(
            type="goal_action_result",
            success=True,
            goal_action=action,
        ), session_id=session.session_id)
        await self._emit(BackendEvent(
            type="command_result",
            command_result_data={
                "message": _t(_RESULT_KEYS.get(action, "goal_action_edited")),
                "type": "success",
            },
        ), session_id=session.session_id)
        await self._emit(BackendEvent(
            type="state_snapshot",
            state=self._session_state_payload(session),
        ), session_id=session.session_id)
        # resume 且会话空闲：立即驱动续跑（与 /goal 命令的 drive_goal 路径
        # 一致；busy 时当前轮结束的空闲边界自然续跑，不重复驱动）
        # 已有活跃行任务时跳过：防止快速连按两次 resume 覆盖 active_line_task
        # 造成孤儿任务与 busy 状态不一致
        if (
            action == "resume"
            and not session.busy
            and (session.active_line_task is None or session.active_line_task.done())
        ):
            session.busy = True
            self._spawn_session_line(session, self._drive_goal_after_resume(session))

    async def _drive_goal_after_resume(self, session: SessionRuntime) -> bool:
        """GoalBar resume 后驱动 goal 轮次（fire-and-forget 行任务）。

        与 /goal resume 的 drive_goal 路径（runtime.handle_line）等价：
        消费引擎 drive_goal_rounds 事件流并渲染，结束后常规收尾。

        Args:
            session: 目标会话运行时

        Returns:
            bool: 是否继续会话（始终 True）
        """
        from illusion.engine.query import MaxTurnsExceeded

        render_event = await self._make_render_event(session)
        try:
            async for event in session.engine.drive_goal_rounds():
                await render_event(event)
        except MaxTurnsExceeded as exc:
            await self._emit(
                BackendEvent(
                    type="transcript_item",
                    item=TranscriptItem(
                        role="system",
                        text=f"Stopped after {exc.max_turns} turns (max_turns).",
                    ),
                ),
                session_id=session.session_id,
            )
        await self._finish_session_line(session)
        return True

    def _route_task_completion(self, task: Any) -> SessionRuntime | None:
        """按任务归属路由后台任务完成通知到对应会话。

        任务创建时经 ContextVar stamp 了 owner_session_id（见 tasks.manager），
        多会话并发下据此把完成通知投递到发起会话的 tracker；无归属的任务
        （启动期/terminal 遗留）回退 None，由调用方投递到初始引擎。

        Args:
            task: 任务记录

        Returns:
            SessionRuntime | None: 归属会话运行时；无归属时返回 None
        """
        owner = str(task.metadata.get("owner_session_id", "") if task.metadata else "")
        return self._sessions.get(owner)

    def _set_active_session(self, session_id: str) -> None:
        """切换活跃会话（仅改指针，不触碰任何引擎状态）。"""
        self._active_session_id = session_id
        # 同步会话所在 bundle 的 app_state session_id（状态快照来源 bundle）；
        # 同时清除共享 session_name：切换后上一会话的重命名名称不再属于当前会话，
        # 残留会在 _update_session_meta 兜底时污染"已创建但未输入首条消息"的空会话
        session = self._sessions.get(session_id)
        if session is not None:
            session.bundle.app_state.set(session_id=session_id, session_name="")
            state = self._workspaces.get(_cwd_key(session.bundle.cwd))
            if state is not None:
                state.last_used = time.time()
        elif self._bundle is not None:
            self._bundle.app_state.set(session_id=session_id, session_name="")

    async def _create_session(self, cwd: str | None = None) -> SessionRuntime:
        """创建一个全新的会话运行时（独立引擎 + 独立 CheckpointStore）。

        Args:
            cwd: 目标工作区目录（None 时使用默认工作区）；工作区 bundle
                不存在时懒构建（MCP/插件按该目录的项目级配置初始化）

        Returns:
            SessionRuntime: 新建的会话运行时
        """
        target_cwd = self._resolve_workspace_cwd(cwd)
        ws_bundle = await self._get_or_build_bundle(target_cwd)
        session_id = uuid4().hex[:12]
        engine = build_session_engine(
            ws_bundle,
            session_id,
            permission_prompt=self._make_permission_prompt(session_id),
            ask_user_prompt=self._make_ask_user_prompt(session_id),
            plan_approval_prompt=self._make_plan_approval_prompt(session_id),
        )
        session = SessionRuntime(
            session_id=session_id,
            bundle=build_session_bundle(ws_bundle, session_id, engine),
            workspace_cwd=ws_bundle.cwd,
        )
        # 清除工作区共享 app_state 中残留的重命名名称：session_name 是
        # 工作区级共享字段，被删除/已切换会话的重命名残留值会在新会话
        # 首条消息落盘时被写入新会话的 meta.title（_save_session_snapshot
        # 的兜底逻辑），导致新会话继承上一次的会话名称。新建会话即清除。
        if ws_bundle.app_state.get().session_name:
            ws_bundle.app_state.set(session_name="")
        self._sessions[session_id] = session
        self._maybe_evict_sessions()
        return session

    async def _dispose_session(self, session_id: str) -> None:
        """释放会话运行时（取消行任务并关闭引擎）。

        Args:
            session_id: 会话 ID
        """
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        # 清理会话级 modal 锁（避免无效会话的锁占用 dict 空间）
        self._modal_locks.pop(session_id, None)
        if session.active_line_task is not None and not session.active_line_task.done():
            session.active_line_task.cancel()
        # 停止该会话发起的后台任务（agent/bash/powershell）：
        # 否则任务被孤立，完成通知会因归属会话已不存在而污染其他会话
        from illusion.ui.runtime import stop_all_tasks
        await stop_all_tasks(session.bundle, session_ids=[session_id])
        if self._active_session_id == session_id:
            self._active_session_id = None
        engine = session.engine
        try:
            await engine.aclose()
        except Exception:
            log.exception("关闭会话 %s 引擎失败", session_id)
        # 会话删除可能腾空其工作区：驱动空闲 bundle 驱逐
        self._evict_idle_workspace_bundles()

    def _maybe_evict_sessions(self) -> None:
        """超过内存上限时淘汰最旧的非 busy 非 active 会话运行时。

        防止长时间使用导致会话引擎无限累积（内存膨胀）。被淘汰的会话
        在 _push_sessions 中标记 in_memory=False，前端下次点击时重新
        走 web_restore_session 恢复。
        """
        if len(self._sessions) <= MAX_MATERIALIZED_SESSIONS:
            return
        idle = [
            sr for sr in self._sessions.values()
            if sr.session_id != self._active_session_id and not sr.busy
        ]
        idle.sort(key=lambda s: s.created_at)
        excess = len(self._sessions) - MAX_MATERIALIZED_SESSIONS
        for sr in idle[:excess]:
            self._create_background_task(self._dispose_session(sr.session_id))
        # 淘汰后立即推送列表：前端据此把被淘汰会话标记为需重新恢复，
        # 避免用户点击时走纯本地切换导致提交请求静默丢失
        if excess > 0:
            self._create_background_task(self._push_sessions())
        # 会话淘汰可能腾空某个工作区：顺带驱逐空闲工作区 bundle
        self._evict_idle_workspace_bundles()

    def _spawn_session_line(self, session: SessionRuntime, coro: Coroutine[Any, Any, bool]) -> None:
        """以独立任务启动会话行处理（fire-and-forget，不阻塞主循环）。

        会话行任务并发执行：主循环只做请求分发，任一会话的行任务
        不再阻塞其他会话的请求处理（含新建/切换会话）。

        Args:
            session: 目标会话运行时
            coro: 行处理协程
        """
        task = asyncio.create_task(
            self._run_session_line(session, coro), name=f"session-line-{session.session_id}"
        )
        session.active_line_task = task
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)

    async def _run_session_line(self, session: SessionRuntime, coro: Coroutine[Any, Any, bool]) -> None:
        """运行会话行任务：设置任务归属上下文、执行、清理 busy 状态。

        Args:
            session: 目标会话运行时
            coro: 行处理协程
        """
        from illusion.tasks.manager import session_owner_ctx

        token = session_owner_ctx.set(session.session_id)
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("会话 %s 行任务异常", session.session_id)
            await self._emit(
                BackendEvent(type="error", message="Internal error, please retry"),
                session_id=session.session_id,
            )
            await self._emit(BackendEvent(type="line_complete"), session_id=session.session_id)
            await self._update_phase(session, "idle")
            await self._push_sessions()
        finally:
            session_owner_ctx.reset(token)
            if session.active_line_task is asyncio.current_task():
                session.active_line_task = None
            session.busy = False
            self._create_background_task(self._check_post_idle_bg(session))

    # === 会话绑定的 modal 回调 ===

    def _make_permission_prompt(self, session_id: str) -> Any:
        """为指定会话构造权限确认回调（modal 事件携带会话 ID）。"""

        async def _ask_permission(tool_name: str, reason: str, high_risk: bool = False) -> bool:
            return await self._ask_permission(session_id, tool_name, reason, high_risk)

        return _ask_permission

    def _make_ask_user_prompt(self, session_id: str) -> Any:
        """为指定会话构造用户问答回调（modal 事件携带会话 ID）。"""

        async def _ask_question(question: str, questions: object = None) -> str | dict[Any, Any]:
            return await self._ask_question(session_id, question, questions)

        return _ask_question

    def _make_plan_approval_prompt(self, session_id: str) -> Any:
        """为指定会话构造计划审批回调（modal 事件携带会话 ID）。"""

        async def _ask_plan_approval(plan: str) -> tuple[bool, str]:
            return await self._ask_plan_approval(session_id, plan)

        return _ask_plan_approval

    def _emit_swarm_status(
        self, teammates: list[dict[str, Any]], notifications: list[dict[str, Any]] | None = None
    ) -> None:
        """同步发送 swarm_status 事件（调度为协程）。"""
        self._create_background_task(
            self._emit(
                BackendEvent(
                    type="swarm_status",
                    swarm_teammates=teammates,
                    swarm_notifications=notifications,
                )
            )
        )

    async def _handle_list_sessions(self, session: SessionRuntime | None = None) -> None:
        """处理列出会话请求（多工作区：按发起会话所属目录列出）。"""
        import time as _time

        from illusion.services.session_storage import list_session_snapshots

        assert self._bundle is not None
        scope_bundle = session.bundle if session is not None else (self._active_bundle() or self._bundle)
        locale = str(
            scope_bundle.app_state.get().ui_language or scope_bundle.current_settings().ui_language
        )
        zh = locale.lower().startswith("zh")
        sessions = list_session_snapshots(scope_bundle.cwd, limit=10)
        options = []
        for s in sessions:
            ts = _time.strftime("%m/%d %H:%M", _time.localtime(s["created_at"]))
            summary = s.get("summary", "")[:50] or ("（无摘要）" if zh else "(no summary)")
            options.append(
                {
                    "value": s["session_id"],
                    "label": f"{ts}  {s['message_count']}msg  {summary}",
                }
            )
        await self._emit(
            BackendEvent(
                type="select_request",
                modal={
                    "kind": "select",
                    "title": "恢复会话" if zh else "Resume Session",
                    "command": "resume",
                },
                select_options=options,
            )
        )

    async def _handle_select_command(self, command_name: str, session: SessionRuntime) -> None:
        """处理选择命令请求。"""
        assert self._bundle is not None
        command = command_name.strip().lstrip("/").lower()
        if command == "resume":
            await self._handle_list_sessions(session)
            return

        settings = session.bundle.current_settings()
        state = session.bundle.app_state.get()
        locale = str(state.ui_language or settings.ui_language)
        zh = locale.lower().startswith("zh")
        current_model = settings.active_model_name

        if command == "env":
            statuses = AuthManager(settings).get_env_credential_statuses()
            options = [
                {
                    "value": env_key,
                    "label": f"{env_key} ({info['api_format']})",
                    "description": f"{info['api_format']} / {info['model']}"
                    + (" [active]" if info["active"] else ""),
                    "active": info["active"],
                }
                for env_key, info in statuses.items()
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "环境配置" if zh else "Env Config",
                        "command": "env",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "permissions":
            options = [
                {
                    "value": "default",
                    "label": "默认" if zh else "Default",
                    "description": "写入/执行前询问"
                    if zh
                    else "Ask before write/execute operations",
                    "active": settings.permission.mode.value == "default",
                },
                {
                    "value": "full_auto",
                    "label": "自动" if zh else "Auto",
                    "description": "自动允许所有工具（仍受沙箱限制）" if zh else "Allow all tools automatically (still sandboxed)",
                    "active": settings.permission.mode.value == "full_auto",
                },
                {
                    "value": "yolo",
                    "label": "YOLO",
                    "description": "绕过沙箱完全运行" if zh else "Bypass sandbox and run fully",
                    "active": settings.permission.mode.value == "yolo",
                },
                {
                    "value": "plan",
                    "label": "计划模式" if zh else "Plan Mode",
                    "description": "阻止所有写入操作" if zh else "Block all write operations",
                    "active": settings.permission.mode.value == "plan",
                },
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "权限模式" if zh else "Permission Mode",
                        "command": "permissions",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "effort":
            options = [
                {
                    "value": "low",
                    "label": "低" if zh else "Low",
                    "description": "最快响应" if zh else "Fastest responses",
                    "active": settings.effort == "low",
                },
                {
                    "value": "medium",
                    "label": "中" if zh else "Medium",
                    "description": "平衡推理" if zh else "Balanced reasoning",
                    "active": settings.effort == "medium",
                },
                {
                    "value": "high",
                    "label": "高" if zh else "High",
                    "description": "最深推理" if zh else "Deepest reasoning",
                    "active": settings.effort == "high",
                },
                {
                    "value": "xhigh",
                    "label": "超高" if zh else "XHigh",
                    "description": "超深推理" if zh else "Extra deep reasoning",
                    "active": settings.effort == "xhigh",
                },
                {
                    "value": "max",
                    "label": "最大" if zh else "Max",
                    "description": "最大推理深度" if zh else "Maximum reasoning depth",
                    "active": settings.effort == "max",
                },
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "推理强度" if zh else "Reasoning Effort",
                        "command": "effort",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "max-tokens":
            current = int(state.max_tokens or settings.max_tokens)
            presets = [
                ("8k", 8192),
                ("16k", 16384),
                ("32k", 32768),
                ("64k", 65536),
                ("128k", 131072),
            ]
            options = [
                {
                    "value": key,
                    "label": key.upper(),
                    "description": f"{tokens} tokens",
                    "active": tokens == current,
                }
                for key, tokens in presets
            ]
            # 自定义档位
            options.append({
                "value": "custom",
                "label": "自定义" if zh else "Custom",
                "description": "手动输入数字" if zh else "Enter custom number",
                "active": current not in {tokens for _, tokens in presets},
            })
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "最大令牌数" if zh else "Max Tokens",
                        "command": "max-tokens",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "turns":
            current_turns: int | None = session.engine.max_turns
            values = {32, 64, 128, 200, 256, 512}
            if isinstance(current_turns, int):
                values.add(current_turns)
            options = [
                {
                    "value": "unlimited",
                    "label": "无限" if zh else "Unlimited",
                    "description": "不对本会话硬性停止" if zh else "Do not hard-stop this session",
                    "active": current_turns is None,
                }
            ]
            options.extend(
                {
                    "value": str(value),
                    "label": (f"{value} 轮" if zh else f"{value} turns"),
                    "active": value == current_turns,
                }
                for value in sorted(values)
            )
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "最大轮数" if zh else "Max Turns",
                        "command": "turns",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "agent":
            # 列出已完成 agent 任务摘要（前台 tool_result + 后台 task-notification）
            from illusion.config.paths import get_tasks_dir
            from illusion.engine.messages import TextBlock, ToolResultBlock
            from illusion.swarm.agent_executor import agent_type_display
            from illusion.tasks.types import TASK_NOTIFICATION_RE

            task_options: list[dict[str, Any]] = []
            order = 0

            # 1. 前台 agent：从 transcript 提取 tool_result（跳过后台启动通知）
            pending_labels: dict[str, str] = {}
            for msg in session.engine.messages:
                if msg.role == "assistant":
                    for use_block in msg.tool_uses:
                        if use_block.name == "agent":
                            inp = use_block.input or {}
                            task_name = str(inp.get("description") or inp.get("name") or "agent")[:30]
                            # input 完全未到达时显示 "Agent"；到达后转 PascalCase
                            # （与后台 task_name 类型段共用同一共享转换）
                            if not inp:
                                agent_type = "Agent"
                            else:
                                agent_type = agent_type_display(
                                    str(sub) if (sub := inp.get("subagent_type")) is not None else None
                                )
                            pending_labels[use_block.id] = f"{task_name} · {agent_type}"
                elif msg.role == "user":
                    for result_block in msg.content:
                        if isinstance(result_block, ToolResultBlock) and result_block.tool_use_id in pending_labels:
                            text = result_block.text_content
                            if text and ("launched in background" in text or "launched as subprocess" in text):
                                continue
                            order += 1
                            first_line = text.split("\n", 1)[0][:60] if text else ("（无摘要）" if zh else "(no summary)")
                            task_options.append({
                                "value": result_block.tool_use_id,
                                "label": f"#{order} {pending_labels[result_block.tool_use_id]}",
                                "description": first_line,
                            })

            # 2. 后台任务：从 transcript 的 task-notification 提取
            tasks_dir = get_tasks_dir()
            for msg in session.engine.messages:
                if msg.role != "user":
                    continue
                for text_block in msg.content:
                    if not isinstance(text_block, TextBlock):
                        continue
                    match = TASK_NOTIFICATION_RE.search(text_block.text)
                    if not match:
                        continue
                    if match.group("status").strip() != "completed":
                        continue
                    task_id = match.group("task_id").strip()
                    task_name = (match.group("task_name") or "").strip()
                    summary_tag = match.group("summary").strip()
                    result_text = match.group("result").strip()
                    if not result_text:
                        try:
                            log_file = tasks_dir / f"{task_id}.log"
                            if log_file.exists():
                                content = log_file.read_text(encoding="utf-8", errors="replace")
                                result_text = content[-12000:] if len(content) > 12000 else content
                        except OSError:
                            pass
                    order += 1
                    # 旧通知的类型段可能是未转换的原始 subagent_type（如
                    # "general-purpose"），展示时统一规范化为 PascalCase——
                    # agent_type_display 对已是驼峰的输入幂等，新旧数据一致
                    if task_name:
                        if " · " in task_name:
                            name_part, _, type_part = task_name.rpartition(" · ")
                            label_name = f"{name_part} · {agent_type_display(type_part)}"
                        else:
                            label_name = task_name
                    else:
                        name_match = re.match(r"Agent '([^']+)'", summary_tag)
                        label_name = name_match.group(1) if name_match else (summary_tag or "agent")
                    first_line = result_text.split("\n", 1)[0][:60] if result_text else ("（无摘要）" if zh else "(no summary)")
                    task_options.append({
                        "value": task_id,
                        "label": f"#{order} {label_name}",
                        "description": first_line,
                    })

            if not task_options:
                await self._emit(
    BackendEvent(type="error", message=("没有已完成的 agent" if zh else "No completed agents"),
                    session_id=session.session_id,
                ))
                return
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": ("已完成任务摘要" if zh else "Completed Task Summary"), "command": "agent"},
                    select_options=task_options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "language":
            current_lang = str(state.ui_language or "zh-CN")
            options = [
                {
                    "value": "set zh-CN",
                    "label": "简体中文",
                    "description": "中文界面",
                    "active": current_lang == "zh-CN",
                },
                {
                    "value": "set en",
                    "label": "English",
                    "description": "English UI",
                    "active": current_lang == "en",
                },
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "语言" if zh else "Language",
                        "command": "language",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "model":
            options = self._model_select_options(current_model)
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "模型" if zh else "Model",
                        "command": "model",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "rewind":
            messages = session.engine.messages
            # 过滤后台任务完成通知与 goal harness 注入消息，它们不应出现在回退选项中
            user_msgs = [
                (i, msg)
                for i, msg in enumerate(messages)
                if msg.role == "user" and msg.text.strip()
                and not is_task_notification(msg.text)
                and not is_goal_system_message(msg.text)
            ]
            if not user_msgs:
                await self._emit(
                    BackendEvent(
                        type="error",
                        message=("没有可回退的消息。" if zh else "No messages to rewind to."),
                    ),
                    session_id=session.session_id,
                )
                return
            options = []
            total = len(user_msgs)
            for k, (idx, msg) in enumerate(reversed(user_msgs)):
                text = msg.text.strip()
                label = text[:80] + ("…" if len(text) > 80 else "")
                options.append(
                    {
                        "value": str(idx),
                        "label": label,
                        "description": f"#{total - k}",
                    }
                )
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "回退到" if zh else "Rewind to",
                        "command": "rewind",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "delete":
            import time as _time

            from illusion.services.session_storage import list_session_snapshots

            sessions = list_session_snapshots(session.bundle.cwd, limit=10)
            if not sessions:
                await self._emit(
                    BackendEvent(
                        type="error",
                        message=("没有已保存的会话。" if zh else "No saved sessions found."),
                    ),
                    session_id=session.session_id,
                )
                return
            options = []
            for s in sessions:
                ts = _time.strftime("%m/%d %H:%M", _time.localtime(s["created_at"]))
                summary = s.get("summary", "")[:50] or ("（无摘要）" if zh else "(no summary)")
                options.append(
                    {
                        "value": s["session_id"],
                        "label": f"{ts}  {s['message_count']}msg  {summary}",
                    }
                )
            options.append(
                {
                    "value": "__all__",
                    "label": ("清除所有会话" if zh else "Delete all sessions"),
                    "description": (
                        "删除全部已保存的会话快照" if zh else "Remove all saved session snapshots"
                    ),
                }
            )
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "删除会话" if zh else "Delete Session",
                        "command": "delete",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "rules":
            # 加载项目级权限配置
            from illusion.permissions.loader import (
                filter_rules_by_permissions,
                is_rules_disabled,
                load_project_permissions,
            )
            from illusion.skills.loader import get_project_rules_dir

            project_permissions = load_project_permissions(session.bundle.cwd)

            # 检查是否禁用所有 rules
            if is_rules_disabled(project_permissions):
                await self._emit(
                    BackendEvent(
                        type="error",
                        message=("所有规则已被禁用" if zh else "All rules are disabled"),
                    ),
                    session_id=session.session_id,
                )
                return

            rules_dir = get_project_rules_dir(session.bundle.cwd)
            all_rule_files = sorted(rules_dir.glob("*.md"))
            if not all_rule_files:
                await self._emit(
                    BackendEvent(
                        type="error",
                        message=(
                            f"没有找到规则文件：{rules_dir}"
                            if zh
                            else f"No rules found in {rules_dir}"
                        ),
                    ),
                    session_id=session.session_id,
                )
                return

            # 过滤掉被禁用的 rules
            rule_files = filter_rules_by_permissions(all_rule_files, project_permissions)

            options = []
            for path in rule_files:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                first_line = (
                    content.split("\n", 1)[0][:60] if content else ("（空）" if zh else "(empty)")
                )
                options.append(
                    {
                        "value": path.stem,
                        "label": path.stem,
                        "description": first_line,
                    }
                )
            if not options:
                await self._emit(
                    BackendEvent(
                        type="error",
                        message=("没有可用的规则文件" if zh else "No available rules files"),
                    ),
                    session_id=session.session_id,
                )
                return
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "查看规则" if zh else "View Rules",
                        "command": "rules",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "skills":
            from illusion.skills.loader import load_skill_registry

            skill_registry = load_skill_registry(session.bundle.cwd)
            skills = skill_registry.list_skills()

            if not skills:
                await self._emit(
                    BackendEvent(type="error", message="No skills available."),
                    session_id=session.session_id,
                )
                return

            options = []
            for skill in skills:
                source = f" [{skill.source}]"
                first_line = (
                    skill.description.split("\n", 1)[0][:60]
                    if skill.description
                    else ("（空）" if zh else "(empty)")
                )
                options.append(
                    {
                        "value": skill.name,
                        "label": f"{skill.name}{source}",
                        "description": first_line,
                    }
                )

            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "查看技能" if zh else "View Skills",
                        "command": "skills",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "context":
            current_window = settings.context_window
            # 上下文占用：最后一次 API 调用的真实值 + 新增消息估算
            estimated = session.engine.current_context_tokens()
            percentage = round(estimated * 100 / current_window) if current_window > 0 else 0
            options = [
                {
                    "value": "__change_window__",
                    "label": "修改上下文窗口大小" if zh else "Change context window size",
                    "description": f"当前: {current_window:,} tokens"
                    if zh
                    else f"Current: {current_window:,} tokens",
                },
                {
                    "value": "__usage__",
                    "label": "查看上下文使用情况" if zh else "View context usage",
                    "description": f"已用: ~{estimated:,} / {current_window:,} tokens ({percentage}%)"
                    if zh
                    else f"Used: ~{estimated:,} / {current_window:,} tokens ({percentage}%)",
                },
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "上下文管理" if zh else "Context Management",
                        "command": "context",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        if command == "context-window":
            current = settings.context_window
            preset_values = [128_000, 200_000, 512_000, 1_000_000]
            if current not in preset_values:
                preset_values.append(current)
            preset_values.sort()
            options = [
                {
                    "value": str(v),
                    "label": f"{v:,} tokens",
                    "active": v == current,
                }
                for v in preset_values
            ]
            options.append(
                {
                    "value": "__custom__",
                    "label": "其他（自定义输入）" if zh else "Other (custom)",
                }
            )
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={
                        "kind": "select",
                        "title": "上下文窗口大小" if zh else "Context Window Size",
                        "command": "context-window",
                    },
                    select_options=options,
                ),
                session_id=session.session_id,
            )
            return

        await self._emit(
            BackendEvent(
                type="error",
                message=(
                    f"/{command} 暂无可选项" if zh else f"No selector available for /{command}"
                ),
            ),
            session_id=session.session_id,
        )

    def _model_select_options(self, current_model: str) -> list[dict[str, object]]:
        """从 settings.json 的 env_N 配置中提取所有实际可用的模型。"""
        assert self._bundle is not None
        settings = self._bundle.current_settings()
        envs = settings.list_envs()

        seen: set[str] = set()
        options: list[dict[str, object]] = []

        # 当前模型排第一位（value 用 model 引用，label 用显示名）
        if settings.model:
            seen.add(settings.model)
            options.append(
                {
                    "value": settings.model,
                    "label": current_model,
                    "description": "Current",
                    "active": True,
                }
            )

        # 遍历所有 env，提取 model_N
        for env_key, env in envs.items():
            for model_key, model_name in env.list_models().items():
                ref = f"{env_key}.{model_key}"
                if ref in seen:
                    continue
                seen.add(ref)
                is_current = ref == settings.model
                options.append(
                    {
                        "value": ref,
                        "label": model_name,
                        "description": f"{env_key} ({env.api_format})",
                        "active": is_current,
                    }
                )

        return options

    @contextlib.asynccontextmanager
    async def _acquire_modal_lock(self, session_id: str) -> AsyncIterator[None]:
        """获取指定会话的 modal 串行锁（同会话排队，跨会话不阻塞）。

        前端 modal 按会话路由（patchView(sid)），不同会话的 modal 可同时
        显示——全局串行会让一个会话的长等待（如 ask_user_question 15 分钟
        等待）阻塞其他所有会话的确认。同会话内 modal 是单例仍需串行，前
        一个完成后自然后续继续。
        """
        lock = self._modal_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._modal_locks[session_id] = lock
        async with lock:
            yield

    async def _ask_permission(
        self, session_id: str, tool_name: str, reason: str, high_risk: bool = False
    ) -> bool:
        """请求用户权限确认。

        如果工具在本会话内已获允许，则直接允许。
        否则通过 WebSocket 发送权限请求模态框，等待用户响应。

        Args:
            session_id: 发起请求的会话 ID（modal 事件携带，前端按会话展示）
            tool_name: 工具名称
            reason: 权限请求原因
            high_risk: 是否为高危操作（如 rm / git reset --hard），
                高危只提供两选项（允许一次 / 拒绝），不可会话级豁免

        Returns:
            bool: 用户是否允许
        """
        # 如果工具在本会话内已获允许，则直接允许（不持久化）。
        # 高危操作（high_risk）不可被会话级豁免：即使工具名已放行，仍须重新确认，
        # 防止"本次会话允许"被用作高危命令（如 rm -rf）的通行证。
        if not high_risk and tool_name in self._session_allowed_tools:
            return True
        session = self._sessions.get(session_id)
        if session is not None:
            session.awaiting_input = True
            await self._update_phase(session, "awaiting_input")
            # 实时推送列表：侧栏 phase=awaiting_input 立即可见（不等到行结束）
            self._create_background_task(self._push_sessions())
        # 同会话 modal 串行排队：前端 modal 是单例，前一个完成后自然后续
        # 继续；跨会话互不阻塞（锁按会话隔离）
        async with self._acquire_modal_lock(session_id):
            request_id = uuid4().hex
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._permission_requests[request_id] = future
            await self._emit(
                BackendEvent(
                    type="modal_request",
                    modal={
                        "kind": "permission",
                        "request_id": request_id,
                        "tool_name": tool_name,
                        "reason": reason,
                        "high_risk": high_risk,
                    },
                ),
                session_id=session_id,
            )
            try:
                # 等待用户响应：所有会话的权限请求统一 285s 超时（超时按
                # "超时拒绝"处理，错误回流为工具结果且清理弹窗，防止
                # "未确认权限 + 超时"导致弹窗遗留）。
                from illusion.engine.query import wait_for_permission_decision

                return await wait_for_permission_decision(future, tool_name)
            finally:
                # 兜底：请求被放弃（超时/取消）时确保 future 不被悬挂，
                # 防止仍持有引用的等待路径永久阻塞
                if not future.done():
                    future.set_result(False)
                self._permission_requests.pop(request_id, None)
                if session is not None:
                    session.awaiting_input = False
                # 清理前端的权限弹窗：正常响应路径前端已自行关闭（重复发送
                # modal=None 无害），超时/取消路径必须显式关闭，防止弹窗残留
                await self._emit(
                    BackendEvent(type="modal_request", modal=None),
                    session_id=session_id,
                )

    async def _ask_question(
        self, session_id: str, question: str, questions: object = None
    ) -> str | dict[Any, Any]:
        """向用户提问并等待回答。

        Args:
            session_id: 发起请求的会话 ID（modal 事件携带，前端按会话展示）
            question: 提问内容
            questions: 结构化问题数据（可选）

        Returns:
            str | dict[str, Any]: 用户回答
        """
        session = self._sessions.get(session_id)
        if session is not None:
            session.awaiting_input = True
            await self._update_phase(session, "awaiting_input")
            self._create_background_task(self._push_sessions())
        # 同会话 modal 串行排队：前端 modal 是单例，前一个完成后自然后续
        # 继续；跨会话互不阻塞（锁按会话隔离）
        async with self._acquire_modal_lock(session_id):
            request_id = uuid4().hex
            future: asyncio.Future[str | dict[Any, Any]] = asyncio.get_running_loop().create_future()
            self._question_requests[request_id] = future
            # 优先使用显式传入的结构化问题数据，回退到会话最后工具输入
            questions_data = questions
            if questions_data is None and session is not None:
                tool_input = session.last_tool_inputs.get("ask_user_question", {})
                questions_data = tool_input.get("questions")
            # 如果是 pydantic 模型列表，转为 dict[str, Any]
            if questions_data is not None and isinstance(questions_data, list):
                questions_data = [
                    q.model_dump() if hasattr(q, "model_dump") else q for q in questions_data
                ]
            modal_payload: dict[str, Any] = {
                "kind": "question",
                "request_id": request_id,
                "question": question,
            }
            if questions_data:
                modal_payload["questions"] = questions_data
            await self._emit(
                BackendEvent(
                    type="modal_request",
                    modal=modal_payload,
                ),
                session_id=session_id,
            )
            try:
                # 等待用户回答：提问/沙箱确认统一超时（沙箱确认 285s 超时
                # 拒绝并清理弹窗；ask_user_question 普通问答 15 分钟超时返回
                # 占位答案由 agent 自行决策）。
                from illusion.engine.query import wait_for_ask_user_decision

                # 沙箱确认复用本回调。双条件判定避免误判：questions header 固定
                # "沙箱"/"Sandbox"（query.py 写死），且 question 文本以沙箱分支
                # 的固定前缀开头——用户自定义 header="沙箱" 的 ask_user_question
                # 不会被归类为沙箱确认
                sandbox_confirm = bool(
                    questions_data
                    and isinstance(questions_data, list)
                    and all(
                        isinstance(q, dict) and q.get("header") in ("沙箱", "Sandbox")
                        for q in questions_data
                    )
                    and question.startswith(("沙箱限制：「", "Sandbox restriction:"))
                )
                return await wait_for_ask_user_decision(
                    future, "sandbox confirmation" if sandbox_confirm else "ask_user_question"
                )
            finally:
                # 兜底：请求被放弃（超时/取消）时确保 future 不被悬挂
                if not future.done():
                    future.set_result("")
                self._question_requests.pop(request_id, None)
                if session is not None:
                    session.awaiting_input = False
                # 清理前端的提问/确认弹窗：超时/取消路径必须显式关闭，防残留
                await self._emit(
                    BackendEvent(type="modal_request", modal=None),
                    session_id=session_id,
                )

    async def _ask_plan_approval(self, session_id: str, plan: str) -> tuple[bool, str]:
        """向用户展示计划并等待审批。

        先将计划内容作为 plan 消息写入对话流，再复用 question 模态让用户选择批准或拒绝。
        用户可通过"其他"选项输入反馈文字。

        Args:
            session_id: 发起请求的会话 ID（modal 事件携带，前端按会话展示）
            plan: 计划内容（Markdown 格式）

        Returns:
            tuple[bool, str]: (是否批准, 用户反馈)
        """
        # 将计划写入对话流
        await self._emit(
            BackendEvent(
                type="transcript_item",
                item=TranscriptItem(role="plan", text=plan),
            ),
            session_id=session_id,
        )
        # 复用 question 模态，提供批准/拒绝选项
        from illusion.config.i18n import t as _t

        session = self._sessions.get(session_id)
        if session is not None:
            session.awaiting_input = True
            await self._update_phase(session, "awaiting_input")
            self._create_background_task(self._push_sessions())
        # 同会话 modal 串行排队：前端 modal 是单例，前一个完成后自然后续
        # 继续；跨会话互不阻塞（锁按会话隔离）
        async with self._acquire_modal_lock(session_id):
            request_id = uuid4().hex
            future: asyncio.Future[str | dict[Any, Any]] = asyncio.get_running_loop().create_future()
            self._question_requests[request_id] = future
            approve_label = _t("plan_approve")
            reject_label = _t("plan_reject")
            modal_payload: dict[str, Any] = {
                "kind": "question",
                "request_id": request_id,
                "question": _t("plan_approval"),
                "plan": plan,
                "questions": [
                    {
                        "question": _t("plan_approve_question"),
                        "header": "approval",
                        "options": [
                            {"label": approve_label, "description": _t("plan_start_impl")},
                            {"label": reject_label, "description": _t("plan_return_mode")},
                        ],
                        "multiSelect": False,
                    }
                ],
            }
            await self._emit(
                BackendEvent(
                    type="modal_request",
                    modal=modal_payload,
                ),
                session_id=session_id,
            )
            try:
                # 计划审批是安全闸门：285s 有界等待，超时按拒绝处理
                # （与权限确认一致，避免无限阻塞的孤儿 modal）
                from illusion.engine.query import AGENT_PERMISSION_TIMEOUT_SECONDS

                try:
                    answer = await asyncio.wait_for(
                        future, timeout=AGENT_PERMISSION_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    return False, "Plan approval timed out"
                # 解析用户回答
                answer = str(answer).strip()
                if answer == f"1. {approve_label}" or answer == approve_label:
                    return True, ""
                elif answer == f"2. {reject_label}" or answer == reject_label:
                    return False, ""
                else:
                    # 用户通过"其他"输入的反馈文字
                    return False, answer
            finally:
                self._question_requests.pop(request_id, None)
                if session is not None:
                    session.awaiting_input = False
                # 与 _ask_permission/_ask_question 一致：超时/取消/异常路径
                # 显式关闭计划审批弹窗，防残留
                await self._emit(
                    BackendEvent(type="modal_request", modal=None),
                    session_id=session_id,
                )

    async def _stop_active_line(self, session_id: str | None = None) -> None:
        """停止指定会话的活动行任务及其归属的后台任务。

        多会话模式下 stop 只作用于目标会话：取消该会话的行处理任务，
        并停止该会话发起的后台任务（按任务归属会话 ID 过滤，避免
        误杀其他会话正在运行的任务）。

        Args:
            session_id: 目标会话 ID（缺省回退到活跃会话）
        """
        session = self._resolve_session(session_id)
        if session is None:
            return
        task = session.active_line_task
        # 检查该会话是否还有运行中的后台任务：行任务已结束（后台 agent 在跑）时
        # active_line_task 为 None，但 stop 仍应终止这些 agent 进程
        owned_tasks = [
            t for t in get_task_manager().list_tasks()
            if t.status in ("running", "pending")
            and t.metadata.get("owner_session_id", "") == session.session_id
        ]
        if (task is None or task.done()) and not owned_tasks:
            from illusion.config.i18n import t as _t

            await self._emit(
                BackendEvent(
                    type="command_result",
                    command_result_data={"message": _t("no_active_task"), "type": "info"},
                ),
                session_id=session.session_id,
            )
            return
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("停止行处理任务异常")
        # 停止该会话发起的后台任务（agent / bash / powershell 等）
        if self._bundle is not None:
            from illusion.ui.runtime import stop_all_tasks
            await stop_all_tasks(self._bundle, session_ids=[session.session_id])
        # 清空该会话 tracker 的积压完成通知：stop 后行任务 finally 会触发
        # _check_post_idle_bg，若不清空，已在停止前完成的后台任务通知会
        # 驱动 auto_resume 再次调用 LLM（对话"无法终止"的偶发根因）。
        # （stop_all_tasks 内部 clear 的是共享 bundle 的 tracker，与目标
        #   会话的 tracker 不是同一个，必须在此处按会话清理。）
        session_tracker = session.engine._bg_agent_tracker
        if session_tracker is not None:
            session_tracker.clear()
        session.busy = False
        await self._update_phase(session, "idle")
        await self._emit(BackendEvent(type="modal_request", modal=None), session_id=session.session_id)
        from illusion.config.i18n import t as _t

        stopped_message = _t("task_stopped")
        await self._emit(
            BackendEvent(
                type="transcript_item",
                item=TranscriptItem(role="system", text=stopped_message),
            ),
            session_id=session.session_id,
        )
        await self._emit(
            BackendEvent(
                type="command_result",
                command_result_data={"message": stopped_message, "type": "info"},
            ),
            session_id=session.session_id,
        )
        await self._emit(self._status_snapshot())
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
        await self._emit(BackendEvent(type="line_complete"), session_id=session.session_id)
        await self._push_sessions()

    async def _update_phase(self, session: SessionRuntime, phase: str) -> None:
        """更新会话阶段。

        Args:
            session: 目标会话运行时
            phase: 新的会话阶段（idle/thinking/tool_executing/awaiting_input）
        """
        assert self._bundle is not None
        session.phase = phase
        # 阶段写入会话所在 bundle 的 app_state（多工作区各 bundle 独立）
        session.bundle.app_state.set(phase=phase)

    async def _write_loop(self) -> None:
        """单一消费者：串行化所有 WebSocket 写入。

        所有 _emit() 调用通过 _write_queue，确保 FIFO 排序和无并发 WebSocket 写入。
        收到 QueueShutDown 后退出循环；写入异常时只记录日志，不退出（与原版
        asyncio.Lock 实现一致），避免瞬态错误导致写循环永久退出、后续所有事件
        （如 modal_request modal=None、task_stopped、line_complete）丢失，
        进而引发权限模态框不消失、Ctrl+X 看似无效等连锁问题。
        """
        while True:
            try:
                event = await self._write_queue.get()
            except QueueShutDown:
                break
            try:
                payload = event.model_dump_json()
                await self._websocket.send_text(payload)
            except (WebSocketDisconnect, RuntimeError, OSError, ValueError, TypeError):
                # 不 break：瞬态写入失败不应终止写循环，否则后续事件全部丢失。
                # 真正的连接断开由 _read_requests 的 WebSocketDisconnect 处理，
                # 它会入队 shutdown 请求并调用 _shutdown 关闭队列。
                log.debug("WebSocket 写入失败，跳过本次发送")

    async def _emit(self, event: BackendEvent, *, session_id: str | None = None) -> None:
        """入队事件给写循环。非阻塞。

        Args:
            event: 要发送的后端事件
            session_id: 可选：标记事件归属会话（前端按此路由到会话视图）
        """
        if session_id:
            event.session_id = session_id
        try:
            self._write_queue.put_nowait(event)
        except QueueShutDown:
            pass  # 正在关闭，丢弃事件

    async def _handle_agent_wizard_init(self, req: FrontendRequest) -> None:
        """处理 agent_wizard_init：返回可用工具/模型列表。"""
        assert self._bundle is not None
        bundle = self._active_bundle() or self._bundle
        tools = list_available_tools(bundle.tool_registry)
        models = list_available_models(bundle.app_state)
        await self._emit(BackendEvent(type="agent_wizard_init_response", tools=tools, models=models))

    async def _handle_agent_wizard_submit(self, req: FrontendRequest) -> None:
        """处理 agent_wizard_submit：校验并写入 agent 定义文件。"""
        assert self._bundle is not None
        bundle = self._active_bundle() or self._bundle
        fields = req.fields or {}
        scope = req.scope or "user"
        errors = validate_agent_definition(fields, bundle.cwd)
        if errors:
            await self._emit(BackendEvent(type="agent_wizard_result", success=False, errors=errors))
            return
        try:
            path = write_agent_definition(fields, scope, bundle.cwd)
        except OSError as exc:
            await self._emit(BackendEvent(type="agent_wizard_result", success=False, errors={"_": str(exc)}))
            return
        await self._emit(BackendEvent(type="agent_wizard_result", success=True, path=str(path)))

    async def _handle_agent_generate_request(self, req: FrontendRequest) -> None:
        """处理 agent_generate_request：LLM 辅助生成 agent 配置。

        使用发起会话的引擎生成草稿，响应事件携带会话 ID。
        """
        assert self._bundle is not None
        request_id = req.request_id or ""
        session = self._resolve_session(req.session_id)
        if session is None:
            return
        engine = session.engine
        existing = [a.name for a in get_all_agent_definitions()]
        try:
            generated = await generate_agent_from_description(
                req.prompt or "", req.model or "inherit", existing, engine,
            )
            await self._emit(BackendEvent(
                type="agent_generate_response",
                request_id=request_id,
                agent={"identifier": generated.identifier, "when_to_use": generated.when_to_use, "system_prompt": generated.system_prompt},
            ), session_id=session.session_id)
        except Exception as exc:  # noqa: BLE001
            await self._emit(BackendEvent(
                type="agent_generate_response",
                request_id=request_id,
                error=str(exc),
            ), session_id=session.session_id)

    async def _push_update_notice(self) -> None:
        """异步查询 PyPI 最新版本，若有新版本则推送 update_available 事件。

        带进程级缓存（1 小时 TTL），避免每次连接都查询 PyPI；
        检查失败（网络等）静默跳过，不打扰用户。
        """
        global _update_check
        try:
            if self._ws_closed or not self._running:
                return
            from illusion.commands.misc import _check_pypi_latest, _get_current_version

            now = time.time()
            if _update_check is None or now - _update_check["at"] > _UPDATE_CHECK_TTL:
                latest = await asyncio.to_thread(_check_pypi_latest)
                _update_check = {"latest": latest, "at": now}
            latest = _update_check.get("latest")
            if not latest:
                return
            from packaging.version import Version

            current = _get_current_version()
            if Version(latest) > Version(current):
                await self._emit(
                    BackendEvent(type="update_available", latest_version=latest)
                )
        except Exception:
            log.debug("版本检查失败，静默跳过", exc_info=True)

    def _create_background_task(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """创建 fire-and-forget task 并保留强引用，防止 GC 回收未完成 task。

        Args:
            coro: 要执行的协程

        Returns:
            asyncio.Task: 创建的 task，完成后自动从 _dispatch_tasks 移除
        """
        task = asyncio.create_task(coro)
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)
        return task

    def _resolve_pending_futures(self) -> None:
        """resolve 所有 pending permission/question futures，防止永久阻塞。"""
        for fut in self._permission_requests.values():
            if not fut.done():
                fut.set_result(False)  # 默认拒绝
        self._permission_requests.clear()

        for quest_fut in self._question_requests.values():
            if not quest_fut.done():
                quest_fut.set_result("")  # 默认空答
        self._question_requests.clear()

    async def _shutdown(self) -> None:
        """优雅关闭，按严格顺序释放资源。

        不包含 stderr 卸载、SIGINT 移除、stdin 线程停止、runtime 关闭
        （runtime 由 run() finally 块关闭）。
        """
        # 1. resolve 所有 pending permission/question futures
        self._resolve_pending_futures()

        # 2. 取消周期状态更新 task
        if self._periodic_task is not None and not self._periodic_task.done():
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("周期状态更新 task 关闭异常")

        # 2'. 取消 cron 委托拉取循环
        if self._cron_poll_task is not None and not self._cron_poll_task.done():
            self._cron_poll_task.cancel()
            try:
                await self._cron_poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("cron 委托拉取循环关闭异常")

        # 3. 取消所有会话行任务并关闭会话引擎（初始引擎由 run() finally 的
        #    close_runtime 负责，此处跳过避免双重关闭）
        for session in list(self._sessions.values()):
            if session.bundle is self._bundle:
                continue  # 初始会话：共享 bundle，由 close_runtime 统一关闭
            if session.active_line_task is not None and not session.active_line_task.done():
                session.active_line_task.cancel()
            try:
                await session.engine.aclose()
            except Exception:
                log.exception("关闭会话 %s 引擎失败", session.session_id)
        self._sessions.clear()

        # 4. gather 所有 dispatch tasks（return_exceptions=True 不抛异常）
        if self._dispatch_tasks:
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
            self._dispatch_tasks.clear()

        # 5. 关闭写队列 + 等写循环排空（_write_queue.shutdown() 唤醒 _write_loop）
        self._write_queue.shutdown()
        if self._write_task is not None and not self._write_task.done():
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("写循环 task 关闭异常")

        # 6. 标记停止
        self._running = False


__all__ = ["WebBackendHost", "WebHostConfig"]
