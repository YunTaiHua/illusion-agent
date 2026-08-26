"""
React 终端后端主机模块
====================

本模块实现 JSON-lines 协议的后端主机，用于与 React 终端前端通信。

主要功能：
    - 基于 stdin/stdout 的 JSON-lines 协议通信
    - 命令处理（/env, /resume, /permissions 等）
    - 权限确认和工作流管理
    - 会话状态快照
    - 任务管理快照
    - MCP 服务器状态管理

类说明：
    - BackendHostConfig: 后端主机配置数据类
    - ReactBackendHost: 后端主机实现类

使用示例：
    >>> from illusion.ui.backend_host import run_backend_host
    >>> await run_backend_host(model="claude-sonnet-4-20250514")
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from illusion.api.client import SupportsStreamingMessages
from illusion.auth.manager import AuthManager
from illusion.config.settings import load_settings, save_settings
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
from illusion.tasks import TaskRecord, get_task_manager
from illusion.tasks.types import is_task_notification
from illusion.ui.protocol import (
    BackendEvent,
    FrontendRequest,
    TranscriptItem,
    format_permission_mode,
)
from illusion.ui.runtime import (
    RuntimeBundle,
    build_runtime,
    close_runtime,
    handle_background_completions,
    handle_line,
    start_runtime,
    sync_app_state,
)
from illusion.utils.aioqueue import Queue, QueueShutDown
from illusion.utils.signals import install_sigint_handler
from illusion.utils.stderr_redirect import StderrRedirector

# 配置模块级日志记录器
log = logging.getLogger(__name__)


def _now_local() -> datetime:
    """返回本地时间（无时区信息）。"""
    return datetime.now(UTC).astimezone().replace(tzinfo=None, microsecond=0)

# 协议前缀 - 用于标识 JSON-lines 协议
_PROTOCOL_PREFIX = "OHJSON:"


def _strip_tool_previews(text: str, tool_uses: list[Any] | None) -> str:
    """从助手文本中移除工具预览行。

    使用实际工具名称精确匹配，不依赖前导空格数量。
    """
    if not tool_uses:
        return text
    names = [re.escape(tu.name) for tu in tool_uses]
    pattern = re.compile(rf'^\s*(?:{"|".join(names)})\s*\(', re.IGNORECASE)
    lines = text.split('\n')
    filtered = [line for line in lines if not pattern.match(line)]
    return '\n'.join(filtered) if filtered else text


@dataclass(frozen=True)
class BackendHostConfig:
    """后端主机配置数据类。

    Attributes:
        model: 使用的模型名称
        max_turns: 最大对话轮次
        api_client: 流式 API 客户端实例
        restore_messages: 恢复的会话消息列表
        enforce_max_turns: 是否强制限制最大轮次
        effort: 推理强度级别（low/medium/high/xhigh/max）
        channel_hint: 渠道感知提示词（PC 终端或渠道端注入系统提示词）
        channel_tools: 跨渠道工具列表（如 SendToChannelTool）
        permission_mode: 权限模式
        name: 会话名称
        continue_session: 继续上一会话
        resume: 恢复指定会话
    """

    model: str | None = None
    max_turns: int | None = None
    api_client: SupportsStreamingMessages | None = None
    restore_messages: list[dict[str, Any]] | None = None
    restore_session_id: str | None = None
    enforce_max_turns: bool = True
    effort: str | None = None
    channel_hint: str | None = None
    channel_tools: list[Any] | None = None
    permission_mode: str | None = None
    name: str | None = None
    continue_session: bool = False
    resume: str | None = None


class ReactBackendHost:
    """React 终端后端主机。

    通过 JSON-lines 协议与 React 前端通信，驱动 IllusionAgent 运行时。
    处理所有前端请求并发送后端事件。

    Attributes:
        _config: 后端配置
        _bundle: 运行时数据bundle
        _write_queue: 写入事件队列（替代 _write_lock，串行化所有 stdout 写入）
        _write_task: 单一消费者写循环 Task
        _dispatch_tasks: fire-and-forget task 强引用集合
        _request_queue: 请求队列
        _permission_requests: 权限请求字典（request_id -> Future[Any]）
        _question_requests: 用户问答请求字典
        _session_allowed_tools: 本会话内允许的工具集合（不持久化）
        _busy: 当前是否正在处理请求
        _running: 是否正在运行
        _active_line_task: 当前活动的行处理任务
        _last_tool_inputs: 每个工具名称的最后输入（用于富事件发射）
    """

    def __init__(self, config: BackendHostConfig) -> None:
        self._config = config
        self._bundle: RuntimeBundle | None = None
        self._write_queue: Queue[BackendEvent] = Queue()       # 替代 _write_lock，串行化所有 stdout 写入
        self._write_task: asyncio.Task[None] | None = None     # 单一消费者写循环 Task
        self._dispatch_tasks: set[asyncio.Task[None]] = set()  # fire-and-forget 强引用集合
        self._request_queue: asyncio.Queue[FrontendRequest] = asyncio.Queue()
        self._permission_requests: dict[str, asyncio.Future[bool]] = {}  # 权限请求
        self._question_requests: dict[str, asyncio.Future[str | dict[Any, Any]]] = {}      # 用户问答
        self._session_allowed_tools: set[str] = set()          # 本会话内允许的工具（不持久化）
        self._busy = False            # 忙碌状态
        self._running = True           # 运行状态
        self._active_line_task: asyncio.Task[bool] | None = None    # 当前任务
        # 跟踪每个工具名称的最后输入，用于富事件发射
        self._last_tool_inputs: dict[str, dict[str, Any]] = {}
        # 跟踪已发送 tool_started 事件的工具调用ID，避免重复显示
        self._emitted_tool_started_ids: set[str] = set()
        # 跟踪助手简要文本（用于流式更新）
        self._brief_assistant_text: str | None = None
        self._read_thread: threading.Thread | None = None      # daemon stdin 读取线程
        self._read_thread_cancel: threading.Event = threading.Event()  # stdin 线程取消信号
        self._periodic_task: asyncio.Task[None] | None = None  # 周期状态更新 Task
        self._sigint_remove: Callable[[], None] | None = None   # SIGINT handler 卸载函数
        self._stderr_redirector: StderrRedirector | None = None  # stderr 重定向器
        # cron 委托拉取循环（周期领取指定会话执行任务，在本地会话中执行）
        self._cron_poll_task: asyncio.Task[None] | None = None
        # modal 串行化锁：前端 modal 是单例，并发 modal_request 会互相覆盖导致
        # 第一个 future 永不 resolve。所有 modal 请求（permission/question/plan）
        # 必须串行执行，前一个完成释放锁后下一个才能发送 modal_request。
        self._modal_lock: asyncio.Lock = asyncio.Lock()

    async def run(self) -> int:
        """运行后端主机主循环。

        启动三任务并发模型：stdin 读取（daemon 线程）+ 写循环（单一消费者 Task）+
        周期状态更新 Task。安装 stderr 重定向和 SIGINT 处理后进入主分发循环，
        收到 shutdown 请求或异常时通过 _shutdown() 优雅关闭。

        Returns:
            0 表示正常退出，非 0 表示启动失败
        """
        loop = asyncio.get_running_loop()

        # 1. 安装 stderr 重定向
        self._stderr_redirector = StderrRedirector()
        self._stderr_redirector.install()

        try:
            # 2. 构建运行时环境
            self._bundle = await build_runtime(
                model=self._config.model,
                max_turns=self._config.max_turns,
                api_client=self._config.api_client,
                restore_messages=self._config.restore_messages,
                restore_session_id=self._config.restore_session_id,
                permission_prompt=self._ask_permission,
                ask_user_prompt=self._ask_question,  # type: ignore[arg-type]
                plan_approval_prompt=self._ask_plan_approval,
                effort=self._config.effort,
                channel_hint=self._config.channel_hint,
                channel_tools=self._config.channel_tools,
                permission_mode=self._config.permission_mode,
                name=self._config.name,
            )
            assert self._bundle is not None
            await start_runtime(self._bundle)
            # 首次进入主动 sync，避免 context_window 为 0
            sync_app_state(self._bundle)

            # 包装 on_task_complete：后台任务完成后发送 tasks_snapshot，
            # 确保前端 statusBar 的 task 计数及时更新（后台任务不触发 ToolExecutionCompleted）
            _task_manager = get_task_manager()
            _original_on_task_complete = _task_manager.on_task_complete

            def _wrapped_on_task_complete(task_id: str, task: TaskRecord) -> None:
                # 先调用原回调（通知 bg_agent_tracker）
                if _original_on_task_complete is not None:
                    _original_on_task_complete(task_id, task)
                # 异步发送 tasks_snapshot，让前端 statusBar 立即更新
                self._create_background_task(
                    self._emit(BackendEvent.tasks_snapshot(_task_manager.list_tasks()))
                )
                # 后台任务完成且主循环空闲 → 自动进入 busy 处理积压通知。
                # 修复：idle 超时/用户退出 busy 后，通知只发前端提示但无人消费，
                # 只能等手动输入。此处自动调度 _auto_resume_bg 恢复处理。
                if not self._busy and self._bundle is not None:
                    tracker = self._bundle.engine._bg_agent_tracker
                    if tracker is not None and tracker.has_completions():
                        self._create_background_task(self._auto_resume_bg())

            _task_manager.on_task_complete = _wrapped_on_task_complete

            # 3. 启动写循环（单一消费者）
            self._write_task = asyncio.create_task(
                self._write_loop(), name="backend-write-loop"
            )

            # 4. 启动 stdin 读取（daemon 线程，不占用默认线程池）
            self._read_thread = threading.Thread(
                target=self._read_stdin_loop,
                args=(loop,),
                name="backend-stdin-reader",
                daemon=True,
            )
            self._read_thread.start()

            # 5. 启动周期状态更新
            self._periodic_task = asyncio.create_task(
                self._periodic_status_update(), name="backend-periodic-status"
            )

            # 5'. 启动 cron 委托拉取循环（指定会话执行的 cron 任务由本地会话执行，
            #     busy 转化与会话状态天然同步；守护进程未运行时循环静默跳过）
            self._cron_poll_task = asyncio.create_task(
                self._cron_delegation_poll(), name="cron-delegation-poll"
            )

            # 6. 安装 SIGINT 处理（收到 Ctrl+C 时入队 shutdown 请求）
            self._sigint_remove = install_sigint_handler(loop, self._enqueue_shutdown)

            # 7. 发送就绪事件 + 状态快照
            await self._emit(
                BackendEvent.ready(
                    self._bundle.app_state.get(),
                    get_task_manager().list_tasks(),
                    [f"/{command.name}" for command in self._bundle.commands.list_commands()],
                )
            )
            await self._emit(self._status_snapshot())

            # 8. 主分发循环
            self._running = True
            while self._running:
                req = await self._request_queue.get()
                if req.type == "shutdown":
                    break
                try:
                    await self._process_request(req)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # 请求级异常不应拖垮后端进程：此前无保护时异常冒泡到
                    # typer except_hook → 进程退出 → 解释器 shutdown 期间
                    # daemon stdin 读取线程竞争 stdio 缓冲锁 → 原生崩溃
                    # （0xC0000005 / Fatal Python error: _enter_buffered_busy）。
                    # 记录日志并通知前端，进程继续服务后续请求。
                    log.exception("处理请求异常: type=%s", req.type)
                    try:
                        await self._emit(
                            BackendEvent(type="error", message="Internal error, please retry")
                        )
                        await self._emit(BackendEvent(type="line_complete"))
                    except Exception:
                        log.exception("发送错误事件失败")

        finally:
            # 9. 优雅关闭（9 步严格顺序）
            await self._shutdown()

        return 0

    def _enqueue_shutdown(self) -> None:
        """SIGINT 回调：入队 shutdown 请求，让主循环优雅退出。"""
        self._request_queue.put_nowait(FrontendRequest(type="shutdown"))

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
        """优雅关闭，按严格顺序释放资源。"""
        # 1. resolve 所有 pending permission requests（拒绝 → False）
        self._resolve_pending_futures()

        # 2. 信号 stdin 读取线程停止
        self._read_thread_cancel.set()

        # 3. 取消周期状态更新 task
        if self._periodic_task is not None and not self._periodic_task.done():
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("周期状态更新 task 关闭异常")

        # 3'. 取消 cron 委托拉取循环
        if self._cron_poll_task is not None and not self._cron_poll_task.done():
            self._cron_poll_task.cancel()
            try:
                await self._cron_poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("cron 委托拉取循环关闭异常")

        # 4. 取消活跃行处理 task
        if self._active_line_task is not None and not self._active_line_task.done():
            self._active_line_task.cancel()
            try:
                await self._active_line_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("活跃行处理 task 关闭异常")

        # 5. gather 所有 dispatch tasks（return_exceptions=True 不抛异常）
        if self._dispatch_tasks:
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
            self._dispatch_tasks.clear()

        # 6. 关闭写队列 + 等写循环排空（_write_queue.shutdown() 哨兵唤醒 _write_loop）
        self._write_queue.shutdown()
        if self._write_task is not None and not self._write_task.done():
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("写循环 task 关闭异常")

        # 7. 移除 SIGINT handler
        if self._sigint_remove is not None:
            self._sigint_remove()
            self._sigint_remove = None

        # 8. 关闭运行时 + 卸载 stderr 重定向
        if self._bundle is not None:
            try:
                await close_runtime(self._bundle)
            except Exception:
                log.exception("运行时关闭异常")
            self._bundle = None
        if self._stderr_redirector is not None:
            self._stderr_redirector.uninstall()
            self._stderr_redirector = None

        # 9. 标记停止
        self._running = False

    async def _periodic_status_update(self) -> None:
        """周期状态更新 Task：每秒刷新一次状态快照（用于 agent 计数等实时状态）。"""
        while self._running:
            await asyncio.sleep(1.0)
            if self._running and self._bundle is not None:
                # goal 状态从引擎实时刷新（goal 轮次期间 rounds/phase 持续变化，
                # 仅靠行结束后的 sync_app_state 会停留在运行前的旧值）
                bundle = self._bundle
                goal = bundle.engine.goal_status_payload()
                if bundle.app_state.get().goal != goal:
                    bundle.app_state.set(goal=goal)
                await self._emit(self._status_snapshot())

    async def _process_request(self, req: FrontendRequest) -> None:
        """处理单个前端请求（shutdown 已由主循环处理）。

        Args:
            req: 前端请求（非 shutdown 类型）
        """
        # 停止当前任务
        if req.type == "stop":
            await self._stop_active_line()
            return
        # 注意：permission_response 由 _dispatch_stdin_line → _resolve_permission
        # 即时处理，不会入队 _request_queue，因此不会进入此方法。
        # 会话级允许逻辑在 _resolve_permission 中实现。
        # 用户问答响应
        if req.type == "question_response":
            if req.request_id in self._question_requests:
                answer: str | dict[Any, Any] = req.answer or ""
                # 尝试解析 JSON 格式的多选答案
                try:
                    parsed = json.loads(answer) if isinstance(answer, str) else answer
                    if isinstance(parsed, dict):
                        answer = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
                question_future = self._question_requests.pop(req.request_id, None)
                if question_future is not None and not question_future.done():
                    question_future.set_result(answer)
            await self._emit(BackendEvent(type="modal_request", modal=None))
            return
        # 列出会话
        if req.type == "list_sessions":
            await self._handle_list_sessions()
            return
        # 选择命令
        if req.type == "select_command":
            await self._handle_select_command(req.command or "")
            return
        # 应用选择命令
        if req.type == "apply_select_command":
            if self._busy:
                await self._emit(BackendEvent(type="error", message="Session is busy"))
                return
            self._busy = True
            try:
                self._active_line_task = asyncio.create_task(
                    self._apply_select_command(
                        req.command or "",
                        req.value or "",
                    )
                )
                should_continue = await self._active_line_task
            except asyncio.CancelledError:
                should_continue = True
            finally:
                self._active_line_task = None
                self._busy = False
                self._create_background_task(self._check_post_idle_bg())
            if not should_continue:
                await self._emit(BackendEvent(type="shutdown"))
                self._running = False
            return
        if req.type == "agent_wizard_init":
            await self._handle_agent_wizard_init(req)
            return
        if req.type == "agent_wizard_submit":
            await self._handle_agent_wizard_submit(req)
            return
        if req.type == "agent_generate_request":
            await self._handle_agent_generate_request(req)
            return
        if req.type == "agent_generate_cancel":
            await self._handle_agent_generate_cancel(req)
            return
        # @ 提及补全（terminal 与 web 对称，候选收集复用同一共享模块）
        if req.type == "web_request_file_mentions":
            await self._handle_file_mentions(req)
            return
        # 未知请求类型
        if req.type != "submit_line":
            await self._emit(
                BackendEvent(type="error", message=f"Unknown request type: {req.type}")
            )
            return
        # 忙碌中
        if self._busy:
            await self._emit(BackendEvent(type="error", message="Session is busy"))
            return
        # 处理提交的行
        line = (req.line or "").strip()
        if not line:
            return
        self._busy = True
        try:
            self._active_line_task = asyncio.create_task(self._process_line(line))
            should_continue = await self._active_line_task
        except asyncio.CancelledError:
            should_continue = True
        finally:
            self._active_line_task = None
            self._busy = False
            self._create_background_task(self._check_post_idle_bg())
        if not should_continue:
            await self._emit(BackendEvent(type="shutdown"))
            self._running = False

    def _read_stdin_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Daemon 线程：读取 stdin，通过 call_soon_threadsafe 桥接到事件循环。

        不占用默认 ThreadPoolExecutor。permission_response / question_response / stop
        必须绕过 _request_queue 直接处理，因为主循环可能在 _process_line 中 await
        这些请求对应的 future。
        """
        while not self._read_thread_cancel.is_set():
            try:
                raw = sys.stdin.buffer.readline()
            except (OSError, ValueError):
                break  # stdin 已关闭
            if not raw:
                break  # EOF
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not line:
                continue
            # 即时请求绕过 _request_queue 直接在事件循环线程处理
            loop.call_soon_threadsafe(self._dispatch_stdin_line, line)

    def _dispatch_stdin_line(self, line: str) -> None:
        """在事件循环线程中分发 stdin 行。

        即时请求（permission_response / question_response / stop）直接处理，
        其他请求入队 _request_queue 供主循环处理。
        """
        from pydantic import ValidationError

        try:
            req = FrontendRequest.model_validate_json(line)
        except ValidationError:
            log.warning("无法解析 stdin 行: %s", line[:100])
            return

        # 即时请求直接处理
        if req.type == "permission_response":
            self._resolve_permission(req)
            return
        if req.type == "question_response":
            if req.request_id is not None:
                self._resolve_question(req.request_id, req.answer or "")
            return
        if req.type == "stop":
            # stop 必须即时处理：主循环正阻塞在 await self._active_line_task，
            # 无法从 _request_queue 取 stop 请求。用 create_task 调度 _stop_active_line，
            # 它会 cancel self._active_line_task 解除主循环阻塞（原版 _read_requests 也是
            # 在独立 task 中直接 await self._stop_active_line）。
            self._create_background_task(self._stop_active_line())
            return
        if req.type == "goal_action":
            # goal 快捷键操作（Ctrl+G 两段式）也必须即时处理：goal 自动续跑
            # 期间主循环阻塞在行任务，入队会被扣到全部轮次结束才消费，
            # 无法实现 busy 中暂停/编辑/清除
            self._create_background_task(self._handle_goal_action(req))
            return

        # 其他请求入队
        self._request_queue.put_nowait(req)

    def _resolve_permission(self, req: FrontendRequest) -> None:
        """resolve 权限请求 Future，并通知前端关闭模态框。

        同步方法（由 _dispatch_stdin_line 在事件循环线程调用）：
        先 set_result 唤醒主循环中 await future 的 _ask_permission，
        再 put_nowait 发 modal_request modal=None 让前端 setModal(null)。
        原 _handle_request 路径不会被执行（请求在此即时处理），所以必须在此处补发事件，
        否则前端模态框永远不消失。

        若 req.session_allow 为真，将工具名加入会话级允许集合（不持久化），
        本会话内下次调用同一工具时直接放行，不再弹模态框。
        """
        request_id = req.request_id
        if request_id is None:
            return
        allowed = bool(req.allowed)
        future = self._permission_requests.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(allowed)
        # 会话级允许：加入本会话工具集合（不持久化，重启后清除）
        if req.session_allow and req.tool_name:
            self._session_allowed_tools.add(req.tool_name)
        # 通知前端关闭模态框（put_nowait 非阻塞，无需 await）
        try:
            self._write_queue.put_nowait(BackendEvent(type="modal_request", modal=None))
        except QueueShutDown:
            pass  # 正在关闭，丢弃

    def _resolve_question(self, request_id: str, answer: str) -> None:
        """resolve 问答请求 Future，并通知前端关闭模态框。"""
        future = self._question_requests.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(answer)
        try:
            self._write_queue.put_nowait(BackendEvent(type="modal_request", modal=None))
        except QueueShutDown:
            pass  # 正在关闭，丢弃

    async def _make_render_event(self) -> Callable[[StreamEvent], Awaitable[None]]:
        """构建事件渲染器（含 TodoWrite/plan_mode_change 处理）。

        与 ws_host._make_render_event 结构对齐，供 _process_line 与
        _process_bg_completions 复用，避免闭包重复。

        Returns:
            Callable[[StreamEvent], Awaitable[None]]: 事件渲染函数
        """

        async def _render_event(event: StreamEvent) -> None:
            """渲染流式事件。"""
            # 助手文本增量
            if isinstance(event, AssistantTextDelta):
                reasoning = getattr(event, "reasoning", None)
                await self._emit(BackendEvent(
                    type="assistant_delta",
                    message=event.text,
                    reasoning=reasoning if reasoning else None,
                ))
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
                        )
                    )
                self._brief_assistant_text = None
                await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
                # 透传最新累积用量与反推值到前端
                if self._bundle is not None:
                    sync_app_state(self._bundle)
                    # 更新会话 meta（CheckpointStore 已在 query_engine 内每轮 append）
                    from illusion.ui.runtime import _update_session_meta
                    _update_session_meta(self._bundle)
                return
            # 工具链开始
            if isinstance(event, ToolChainStarted):
                await self._update_phase("tool_executing")
                await self._emit(
                    BackendEvent(
                        type="tool_chain_started",
                        tool_count=event.tool_count,
                    )
                )
                return
            # 工具链完成
            if isinstance(event, ToolChainCompleted):
                await self._update_phase("thinking")
                await self._emit(
                    BackendEvent(
                        type="tool_chain_completed",
                        phase="thinking",
                    )
                )
                return
            # 工具开始执行
            if isinstance(event, ToolExecutionStarted):
                tool_use_id = getattr(event, "tool_use_id", "") or ""
                # 始终更新 _last_tool_inputs（即使已提前通知，也需要完整参数用于后续逻辑）
                if event.tool_input:
                    self._last_tool_inputs[event.tool_name] = event.tool_input
                # 通过 tool_use_id 去重：如果已发送过 tool_started 事件，则发送 tool_input_updated 更新参数
                if tool_use_id and tool_use_id in self._emitted_tool_started_ids:
                    # 已提前通知过，发送参数更新事件让前端显示实际操作
                    if event.tool_input:
                        await self._emit(
                            BackendEvent(
                                type="tool_input_updated",
                                tool_name=event.tool_name,
                                tool_input=event.tool_input,
                                tool_use_id=tool_use_id,
                            )
                        )
                    return
                if tool_use_id:
                    self._emitted_tool_started_ids.add(tool_use_id)
                await self._emit(
                    BackendEvent(
                        type="tool_started",
                        tool_name=event.tool_name,
                        tool_input=event.tool_input,
                        item=TranscriptItem(
                            role="tool",
                            text=f"{event.tool_name} {json.dumps(event.tool_input, ensure_ascii=True)}" if event.tool_input else event.tool_name,
                            tool_name=event.tool_name,
                            tool_input=event.tool_input if event.tool_input else None,
                            tool_use_id=tool_use_id or None,
                        ),
                    )
                )
                return
            # 工具进度消息
            if isinstance(event, ToolProgressEvent):
                await self._emit(
                    BackendEvent(
                        type="tool_progress",
                        tool_use_id=event.tool_use_id or None,
                        message=event.message,
                        progress_type=event.progress_type,
                    )
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
                        structured_output=event.structured_output,
                        output_type=event.output_type,
                        tool_metadata=event.tool_metadata,
                        item=TranscriptItem(
                            role="tool_result",
                            text=event.output,
                            tool_name=event.tool_name,
                            is_error=event.is_error,
                            tool_use_id=tool_use_id or None,
                        ),
                    )
                )
                # === Task/Todo 双向同步 ===
                # 仅 in_process_teammate 类型参与互通；同步后再发射快照保证前端看到一致状态
                _manager = get_task_manager()
                if event.tool_name in ("TodoWrite", "todo_write"):
                    tool_input = self._last_tool_inputs.get(event.tool_name, {})
                    todos = tool_input.get("todos") or []
                    if isinstance(todos, list):
                        todo_items = []
                        for item in todos:
                            if isinstance(item, dict):
                                todo_items.append({
                                    "content": item.get("content", ""),
                                    "status": item.get("status", "pending"),
                                    "activeForm": item.get("activeForm", item.get("content", "")),
                                })
                        if all(t.get("status") == "completed" for t in todo_items) and len(todo_items) >= 1:
                            todo_items = []
                        await self._emit(BackendEvent(type="todo_update", todo_items=todo_items))
                await self._emit(BackendEvent.tasks_snapshot(_manager.list_tasks()))
                await self._emit(self._status_snapshot())
                # 计划相关工具完成时发送 plan_mode_change 事件
                # （仅 enter_plan_mode / exit_plan_mode 两个工具存在）
                if event.tool_name in ("enter_plan_mode", "exit_plan_mode"):
                    assert self._bundle is not None
                    # 从设置中读取最新模式（app_state 可能尚未同步）
                    raw_mode = self._bundle.current_settings().permission.mode.value
                    formatted_mode = format_permission_mode(raw_mode)
                    # 同步 app_state 以保持一致
                    self._bundle.app_state.set(permission_mode=raw_mode)
                    await self._emit(BackendEvent(type="plan_mode_change", plan_mode=formatted_mode))
                    await self._emit(self._status_snapshot())
                return
            # 错误事件
            if isinstance(event, ErrorEvent):
                await self._emit(
                    BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=event.message))
                )
                return
            # 状态事件
            if isinstance(event, StatusEvent):
                if event.bg_agent:
                    # 后台代理状态事件：发送到前端 shimmer 区域，不注入 UI
                    await self._emit(
                        BackendEvent(type="bg_agent_status", message=event.message)
                    )
                else:
                    await self._emit(
                        BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=event.message))
                    )
                return
            # goal 轮次生命周期：结构化事件（TUI 的轮次经 StatusBar/Shimmer
            # 状态呈现，此处仅转发；前端按需忽略）
            if isinstance(event, GoalStatusEvent):
                await self._emit(
                    BackendEvent(
                        type="goal_status",
                        goal_status={
                            "kind": event.kind,
                            "round": event.round,
                            "max_rounds": event.max_rounds,
                            "phase": event.phase,
                        },
                    )
                )
                return

        return _render_event

    async def _auto_resume_bg(self) -> None:
        """后台完成通知到达且主循环空闲时，自动进入 busy 处理通知。

        修复：idle 超时/用户退出 busy 后，通知只发前端 bg_agent_status 提示
        但无人消费，只能等手动输入。此方法由 on_task_complete 包装回调调度，
        自动恢复主循环处理积压通知。
        """
        if self._busy or self._bundle is None:
            return
        tracker = self._bundle.engine._bg_agent_tracker
        # 仅在有实际完成通知时才恢复处理，避免任务未完成时误触发 LLM 调用
        if tracker is None or not tracker.has_completions():
            return
        self._busy = True
        try:
            self._active_line_task = asyncio.create_task(self._process_bg_completions())
            await self._active_line_task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("处理后台完成通知时出错")
            # 确保前端 busy 状态释放，避免异常路径卡死输入框
            await self._emit(BackendEvent(type="line_complete"))
        finally:
            self._active_line_task = None
            self._busy = False

    async def _check_post_idle_bg(self) -> None:
        """_busy 变为 False 后检查是否有后台完成通知需要自动恢复。

        弥补斜杠命令执行期间后台完成通知被跳过的缺口：命令执行完后
        _busy=False，但后台在命令期间完成的通知未被消费，用此方法
        触发 _auto_resume_bg 恢复处理。
        """
        if self._bundle is not None:
            tracker = self._bundle.engine._bg_agent_tracker
            if tracker is not None and tracker.has_completions():
                self._create_background_task(self._auto_resume_bg())

    async def _process_bg_completions(self) -> bool:
        """处理积压的后台完成通知（自动进入 busy），不新增用户输入。"""
        assert self._bundle is not None
        # 清除上一轮的工具调用去重记录
        self._emitted_tool_started_ids.clear()
        # 更新会话阶段为思考中
        await self._update_phase("thinking")

        async def _print_system(message: str) -> None:
            """打印系统消息。"""
            await self._emit(
                BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=message))
            )

        # 复用共享的事件渲染器（含 TodoWrite/plan_mode_change 处理）
        _render_event = await self._make_render_event()

        should_continue = await handle_background_completions(
            self._bundle,
            print_system=_print_system,
            render_event=_render_event,
        )

        # 更新会话阶段为空闲
        await self._update_phase("idle")
        await self._emit(self._status_snapshot())
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
        await self._emit(BackendEvent(type="line_complete"))
        return should_continue

    async def _handle_goal_action(self, req: FrontendRequest) -> None:
        """处理 goal 快捷键操作（Ctrl+G 两段式的 pause/resume/edit/clear）。

        与 /goal 命令同源的 human 权威操作，但经 stdin 即时分发绕过行任务
        串行路径，busy 中（goal 自动续跑期间）立即生效：pause/clear 使
        当前轮结束后停止续跑，edit 对下一轮生效，resume 空闲时立即驱动。

        Args:
            req: goal_action 请求（带 CAS goal_id/revision）
        """
        from illusion.config.i18n import t as _t
        from illusion.goal.types import GoalError

        _RESULT_KEYS = {
            "pause": "goal_action_paused",
            "resume": "goal_action_resumed",
            "edit": "goal_action_edited",
            "clear": "goal_action_cleared",
        }

        action = req.goal_action or ""
        bundle = self._bundle
        if bundle is None:
            await self._emit(BackendEvent(
                type="goal_action_result",
                success=False,
                goal_action=action,
                goal_error={"code": "goal-disabled", "message": "goal feature is disabled"},
            ))
            return
        manager = bundle.engine.goal_manager
        if manager is None:
            await self._emit(BackendEvent(
                type="goal_action_result",
                success=False,
                goal_action=action,
                goal_error={"code": "goal-disabled", "message": "goal feature is disabled"},
            ))
            return
        # 快捷键是人类操作：权威来源切换为 human
        manager.current_source = "human"
        try:
            if action == "clear":
                manager.clear(req.goal_id, req.revision)
            else:
                # pause/resume/edit 需要精确 CAS（goal_id + revision）；缺失时拒绝
                if (
                    not req.goal_id
                    or not isinstance(req.revision, int)
                    or isinstance(req.revision, bool)
                    or req.revision < 1
                ):
                    raise GoalError(
                        "goal_id/revision are required for this goal action",
                        code="GOAL_TOOL_INVALID_UPDATE",
                    )
                gid: str = req.goal_id
                rev: int = req.revision
                if action == "pause":
                    manager.pause(gid, rev)
                elif action == "resume":
                    manager.resume(gid, rev)
                elif action == "edit":
                    objective = (req.objective or "").strip()
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
            ))
            await self._emit(BackendEvent(
                type="command_result",
                command_result_data={
                    "message": _t("goal_action_failed", message=exc.message),
                    "type": "error",
                },
            ))
            return
        # 成功：goal 状态落盘并立即推送（不等行任务边界）
        store = bundle.engine.checkpoint_store
        if store is not None and manager.dirty:
            await store.append_goal(manager.persisted_state())
        await self._emit(BackendEvent(
            type="goal_action_result",
            success=True,
            goal_action=action,
        ))
        await self._emit(BackendEvent(
            type="command_result",
            command_result_data={
                "message": _t(_RESULT_KEYS.get(action, "goal_action_edited")),
                "type": "success",
            },
        ))
        await self._emit(self._status_snapshot())
        # resume 且空闲：立即驱动续跑（与 /goal resume 的 drive_goal 路径一致；
        # busy 时当前轮结束的空闲边界自然续跑，不重复驱动）
        # 已有活跃行任务时跳过：防止快速连按两次 resume 覆盖 _active_line_task
        # 造成孤儿任务与 _busy 状态不一致
        if (
            action == "resume"
            and not self._busy
            and (self._active_line_task is None or self._active_line_task.done())
        ):
            self._busy = True
            try:
                self._active_line_task = asyncio.create_task(self._drive_goal_line())
                await self._active_line_task
            except asyncio.CancelledError:
                pass
            finally:
                self._active_line_task = None
                self._busy = False
                self._create_background_task(self._check_post_idle_bg())

    async def _drive_goal_line(self) -> bool:
        """goal 快捷键 resume 后的驱动行任务。

        与 runtime.handle_line 的 drive_goal 路径等价：消费引擎
        drive_goal_rounds 事件流并渲染，结束后按 _process_line 同款收尾。

        Returns:
            bool: 是否继续会话（始终 True）
        """
        from illusion.engine.query import MaxTurnsExceeded

        assert self._bundle is not None
        await self._update_phase("thinking")
        _render_event = await self._make_render_event()
        try:
            async for event in self._bundle.engine.drive_goal_rounds():
                await _render_event(event)
        except MaxTurnsExceeded as exc:
            await self._emit(BackendEvent(
                type="transcript_item",
                item=TranscriptItem(
                    role="system",
                    text=f"Stopped after {exc.max_turns} turns (max_turns).",
                ),
            ))
        await self._update_phase("idle")
        await self._emit(self._status_snapshot())
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
        await self._emit(BackendEvent(type="line_complete"))
        return True

    async def _process_line(
        self,
        line: str,
        *,
        transcript_line: str | None = None,
        collect_output: list[str] | None = None,
    ) -> bool:
        """处理用户输入的行内容。

        Args:
            line: 用户输入的行
            transcript_line: 非 None 时发送该文本为用户消息转录（静默场景传 None）
            collect_output: 非 None 时收集最终助手文本（cron 委托执行回传用）
        """
        assert self._bundle is not None
        # 清除上一轮的工具调用去重记录
        self._emitted_tool_started_ids.clear()
        # 更新会话阶段为思考中
        await self._update_phase("thinking")
        # 发送用户消息
        # /goal 创建命令（非 clear/edit/pause/resume 子命令、非空参数）原文会作为
        # 真实 user 消息入库（record_goal_command），其转录不打命令产物标记，
        # 前端按普通用户消息渲染；其余命令仍标记 is_command 由前端过滤
        parsed_cmd = self._bundle.commands.lookup(line)
        is_command = parsed_cmd is not None
        if parsed_cmd is not None and parsed_cmd[0].name == "goal":
            from illusion.commands.goal import is_goal_create_args

            is_command = not is_goal_create_args(parsed_cmd[1])
        await self._emit(
            BackendEvent(
                type="transcript_item",
                item=TranscriptItem(
                    role="user",
                    text=transcript_line or line,
                    is_command=is_command,
                ),
            )
        )

        async def _print_system(message: str) -> None:
            """打印系统消息。"""
            await self._emit(
                BackendEvent(type="transcript_item", item=TranscriptItem(role="system", text=message))
            )

        # 复用共享的事件渲染器（含 TodoWrite/plan_mode_change 处理）
        _render_event = await self._make_render_event()
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
            await self._emit(BackendEvent(type="transcript_item", item=TranscriptItem(**item)))

        async def _clear_output() -> None:
            """清空输出。"""
            await self._emit(BackendEvent(type="clear_transcript"))

        async def _command_result_emitter(message: str, result_type: str) -> None:
            """发射指令结果事件。"""
            await self._emit(BackendEvent(
                type="command_result",
                command_result_data={
                    "message": message,
                    "type": result_type,
                },
            ))

        async def _replace_transcript_items(items: list[dict[str, Any]]) -> None:
            """替换转录项列表（一次性清空并替换，避免 Ink Static 重复渲染）。"""
            transcript_items = [TranscriptItem(**item) for item in items]
            await self._emit(BackendEvent(type="replace_transcript", items=transcript_items))

        async def _rewind_restored(text: str) -> None:
            """rewind 被回退的 user 消息：通知前端回填输入框（重新编辑）。"""
            await self._emit(BackendEvent(type="session_rewind", restored_text=text))

        should_continue = await handle_line(
            self._bundle,
            line,
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
            replay_transcript_item=_replay_transcript_item,
            command_result_emitter=_command_result_emitter,
            replace_transcript_items=_replace_transcript_items,
            rewind_restored_emitter=_rewind_restored,
        )

        # 更新会话阶段为空闲
        await self._update_phase("idle")
        await self._emit(self._status_snapshot())
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
        await self._emit(BackendEvent(type="line_complete"))
        return should_continue

    # === cron 委托执行（指定会话执行的 cron 任务由本地会话接管） ===

    async def _cron_delegation_poll(self) -> None:
        """周期领取 cron 委托任务并在本地会话中执行。

        与 cron 守护进程的轮询拉取协议：每 3s 领取一次，领取到任务后
        在本地会话中执行（busy 转化、会话状态天然同步），执行完上报结果。
        守护进程未运行时静默跳过（任务由守护进程回退为子进程执行）。
        """
        from illusion.services.cron_delegation import claim_delegated_job

        while self._running:
            try:
                job = await claim_delegated_job()
                if job is not None:
                    await self._run_delegated_cron_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cron 委托拉取/执行异常")
            await asyncio.sleep(3.0)

    async def _run_delegated_cron_job(self, job: dict[str, Any]) -> None:
        """在本地会话中执行委托的 cron 任务。

        TUI 为单会话：仅当任务 cwd 与当前工作目录一致、目标会话 ID 与
        当前会话一致时才接管执行；否则回报 not_supported，让守护进程
        重新入队（由 Web 端或其他主程序接管）或回退子进程。

        Args:
            job: 委托任务字典（含 id/session_id/prompt/cwd）
        """
        from illusion.services.cron_delegation import report_delegated_result

        bundle = self._bundle
        job_id = str(job.get("id", ""))
        if bundle is None:
            return
        job_cwd = os.path.normcase(os.path.normpath(str(job.get("cwd") or "")))
        local_cwd = os.path.normcase(os.path.normpath(bundle.cwd))
        target_sid = str(job.get("session_id") or "").strip()
        prompt = str(job.get("prompt") or "").strip()
        started_at = _now_local()
        if job_cwd != local_cwd or target_sid != bundle.session_id:
            log.info(
                "cron 委托任务与 TUI 会话不匹配，回报 not_supported: id=%s target=%s/%s local=%s/%s",
                job_id, target_sid, job_cwd, bundle.session_id, local_cwd,
            )
            await report_delegated_result(job_id, {"status": "not_supported"})
            return

        # 等待会话空闲（用户正在使用当前会话时排队，上限 60s）
        waited = 0
        while self._busy and waited < 60:
            await asyncio.sleep(1.0)
            waited += 1
        if self._busy:
            log.warning("cron 委托任务等待会话空闲超时: id=%s", job_id)
            await report_delegated_result(job_id, {
                "status": "error",
                "returncode": -1,
                "stdout": "",
                "stderr": "Session busy timeout",
            })
            return

        # 执行：进入 busy（用户输入被拒），执行完释放（异常路径兜底发 line_complete）
        self._busy = True
        collected: list[str] = []
        try:
            display = f"[cron] {prompt[:60]}"
            await self._process_line(prompt, transcript_line=display, collect_output=collected)
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
            await self._emit(BackendEvent(type="line_complete"))
        finally:
            self._busy = False
        result["started_at"] = started_at.isoformat()
        result["ended_at"] = _now_local().isoformat()
        await report_delegated_result(job_id, result)
        # 委托执行期间到达的后台完成通知此时无人消费（用户输入路径在行任务
        # 结束后调用 _check_post_idle_bg），补一次检查避免通知滞留到下次输入
        await self._check_post_idle_bg()

    # rewind 两步选择的中间状态
    _rewind_target_idx: int | None = None

    async def _handle_rewind_message_selected(self, value: str) -> bool:
        """rewind 第一步：用户选择了要回退的消息，弹出模式选择。"""
        if self._bundle is None:
            return True
        try:
            target_idx = int(value)
        except ValueError:
            return True
        self._rewind_target_idx = target_idx
        state = self._bundle.app_state.get()
        zh = str(state.ui_language or "zh-CN").lower().startswith("zh")
        options = [
            {
                "value": "both",
                "label": "回退代码与对话" if zh else "Rewind code & conversation",
                "description": "撤销文件修改并移除对话" if zh else "Revert files and remove conversation",
            },
            {
                "value": "conversation",
                "label": "仅回退对话" if zh else "Rewind conversation only",
                "description": "只移除对话，保留文件修改" if zh else "Remove conversation, keep files",
            },
        ]
        await self._emit(BackendEvent(
            type="select_request",
            modal={"kind": "select", "title": "回退方式" if zh else "Rewind mode", "command": "rewind_mode"},
            select_options=options,
        ))
        return True

    async def _handle_rewind_mode_selected(self, value: str) -> bool:
        """rewind 第二步：用户选择了回退模式，执行回退。"""
        if self._bundle is None or self._rewind_target_idx is None:
            return True
        target_idx = self._rewind_target_idx
        self._rewind_target_idx = None
        mode = value.strip()
        if mode not in ("both", "conversation"):
            return True
        messages = self._bundle.engine.messages
        # 计算 target 之后需回退的真实用户轮次（排除后台任务完成通知与 goal 注入消息；
        # 命令不会进入 engine.messages，真实 / 前缀消息须计入）
        turns = sum(
            1 for i, msg in enumerate(messages)
            if i >= target_idx and msg.role == "user" and msg.text.strip()
            and not is_task_notification(msg.text)
            and not is_goal_system_message(msg.text)
        )
        if turns <= 0:
            return True
        return await self._process_line(f"/rewind {turns} {mode}", transcript_line="/rewind")

    async def _emit_result(self, message: str) -> None:
        """发射指令结果 toast（terminal 前端 3s 自动消失）并释放 busy。"""
        await self._emit(
            BackendEvent(
                type="command_result",
                command_result_data={"message": message, "type": "info"},
            )
        )
        await self._emit(BackendEvent(type="line_complete"))

    def _is_zh(self) -> bool:
        """当前是否中文界面（后端 UI 语言判断）。"""
        assert self._bundle is not None
        locale = str(
            self._bundle.app_state.get().ui_language
            or self._bundle.current_settings().ui_language
        )
        return locale.lower().startswith("zh")

    async def _apply_select_command(self, command_name: str, value: str) -> bool:
        """应用选择的命令值。"""
        assert self._bundle is not None
        command = command_name.strip().lstrip("/").lower()
        selected = value.strip()
        # 特殊路由：context → change window 时弹出子选择器
        if command == "context" and selected == "__change_window__":
            await self._handle_select_command("context-window")
            return True
        # context-window → __custom__ 由前端 CustomInputModal 接管，此处不应到达
        if command == "context-window" and selected == "__custom__":
            await self._emit(BackendEvent(type="error", message="custom input must be handled by frontend"))
            await self._emit(BackendEvent(type="line_complete"))
            return True
        # rewind 两步选择：第一步（选消息）→ 存储目标，弹出模式选择
        if command == "rewind":
            return await self._handle_rewind_message_selected(selected)
        # rewind 两步选择：第二步（选模式）→ 执行回退
        if command == "rewind_mode":
            return await self._handle_rewind_mode_selected(selected)
        # rename 多步选择：步1 选择 session/title；其余 value（<session_id> <name>）
        # 落到 _build_select_command_line 转 /rename 执行
        if command == "rename":
            if selected == "session":
                await self._handle_select_command("rename_session")
                return True
            if selected == "title":
                await self._handle_select_command("rename_title")
                return True
        # rename title → 步2：自动标题开关
        if command == "rename_title":
            settings = load_settings()
            zh = self._is_zh()
            if selected == "off":
                settings.title.enabled = False
                save_settings(settings)
                await self._emit_result("自动标题已关闭" if zh else "Auto title disabled")
            else:
                settings.title.enabled = True
                save_settings(settings)
                await self._handle_select_command("rename_title_model")
            return True
        # rename title → 步3：设置标题生成模型
        if command == "rename_title_model":
            settings = load_settings()
            zh = self._is_zh()
            settings.title.model = selected or None
            save_settings(settings)
            label = selected or ("继承当前" if zh else "Inherit current")
            await self._emit_result(
                ("标题模型已设置为 " + label) if zh else f"Title model set to {label}"
            )
            return True
        # memory 步1：记忆功能或后台自动提取
        if command == "memory":
            if selected == "mem":
                await self._handle_select_command("memory_enable")
            else:
                await self._handle_select_command("memory_auto")
            return True
        # memory 步2：记忆功能开关
        if command == "memory_enable":
            settings = load_settings()
            zh = self._is_zh()
            usage = "用法: /memory [on|off|toggle|status|auto on|auto off]" if zh else "Usage: /memory [on|off|toggle|status|auto on|auto off]"
            if selected == "off":
                settings.memory.enabled = False
                # 记忆关闭连带关闭后台自动提取/整合
                settings.memory.auto_extract = False
                save_settings(settings)
                await self._emit_result(
                    ("记忆功能已禁用（后台自动提取已一并关闭）\n" + usage)
                    if zh
                    else ("Memory disabled (auto extract also disabled)\n" + usage)
                )
            else:
                settings.memory.enabled = True
                save_settings(settings)
                await self._handle_select_command("memory_auto")
            return True
        # memory 步3（记忆分支与 auto 分支共用）：后台自动提取与整合开关
        if command == "memory_auto":
            settings = load_settings()
            zh = self._is_zh()
            usage = "用法: /memory [on|off|toggle|status|auto on|auto off]" if zh else "Usage: /memory [on|off|toggle|status|auto on|auto off]"
            if selected == "off":
                settings.memory.auto_extract = False
                save_settings(settings)
                await self._emit_result("后台自动提取与整合已禁用" if zh else "Auto extract & dream disabled")
            elif not settings.memory.enabled:
                # 记忆关闭时不得单独开启后台提取/整合，避免出现 enabled=false + auto_extract=true 的非法态
                await self._emit_result(
                    ("需先开启记忆功能，后台自动提取才可启用\n" + usage)
                    if zh
                    else ("Enable memory first before enabling auto extract\n" + usage)
                )
            else:
                settings.memory.auto_extract = True
                save_settings(settings)
                await self._handle_select_command("memory_extract_model")
            return True
        # memory 步4：设置提取子代理模型
        if command == "memory_extract_model":
            settings = load_settings()
            settings.memory.extract_model = selected or None
            save_settings(settings)
            await self._handle_select_command("memory_dream_model")
            return True
        # memory 步5：设置整合子代理模型
        if command == "memory_dream_model":
            settings = load_settings()
            zh = self._is_zh()
            settings.memory.dream_model = selected or None
            save_settings(settings)
            label = selected or ("继承当前" if zh else "Inherit current")
            await self._emit_result(
                ("整合模型已设置为 " + label) if zh else f"Dream model set to {label}"
            )
            return True
        line = self._build_select_command_line(command, selected)
        if line is None:
            await self._emit(BackendEvent(type="error", message=f"Unknown select command: {command_name}"))
            await self._emit(BackendEvent(type="line_complete"))
            return True
        return await self._process_line(line, transcript_line=f"/{command}")

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
            # custom 由前端转为数字字符串
            return f"/max-tokens {value}"
        if command == "turns":
            return f"/turns {value}"
        if command == "language":
            return f"/language {value}"
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
        if command == "agent":
            return f"/agent {value}"
        if command == "context":
            if value == "__usage__":
                return "/context __usage__"
            return None
        if command == "context-window":
            return f"/context set {value}"
        if command == "rename":
            return f"/rename {value}"
        return None

    def _status_snapshot(self) -> BackendEvent:
        """生成状态快照事件。"""
        assert self._bundle is not None
        return BackendEvent.status_snapshot(
            state=self._bundle.app_state.get(),
            mcp_servers=self._bundle.mcp_manager.list_statuses(),
        )

    def _emit_swarm_status(self, teammates: list[dict[str, Any]], notifications: list[dict[str, Any]] | None = None) -> None:
        """同步发送 swarm_status 事件（调度为协程）。"""
        self._create_background_task(
            self._emit(BackendEvent(type="swarm_status", swarm_teammates=teammates, swarm_notifications=notifications))
        )

    async def _handle_list_sessions(self) -> None:
        """处理列出会话请求。"""
        import time as _time

        from illusion.services.session_storage import list_session_snapshots

        try:
            assert self._bundle is not None
            locale = str(self._bundle.app_state.get().ui_language or self._bundle.current_settings().ui_language)
            zh = locale.lower().startswith("zh")
            sessions = list_session_snapshots(self._bundle.cwd, limit=10)
            if not sessions:
                await self._emit(BackendEvent(
                    type="command_result",
                    command_result_data={
                        "message": "没有已保存的会话。" if zh else "No saved sessions found.",
                        "type": "info",
                    },
                ))
                return
            options: list[dict[str, Any]] = []
            for s in sessions:
                ts = _time.strftime("%m/%d %H:%M", _time.localtime(s["created_at"]))
                display = s.get("title") or s.get("summary", "")[:50] or ("（无摘要）" if zh else "(no summary)")
                turn_count = s.get("turn_count", 0)
                options.append({
                    "value": s["session_id"],
                    "label": f"#{len(options)+1}  {ts}  {turn_count}轮  {display}",
                })
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "恢复会话" if zh else "Resume Session", "command": "resume"},
                    select_options=options,
                )
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Error listing sessions")
            await self._emit(
                BackendEvent(
                    type="command_result",
                    command_result_data={
                        "message": f"Error listing sessions: {exc}",
                        "type": "error",
                    },
                )
            )

    async def _handle_select_command(self, command_name: str) -> None:
        """处理选择命令请求。"""
        assert self._bundle is not None
        command = command_name.strip().lstrip("/").lower()
        if command == "resume":
            await self._handle_list_sessions()
            return

        settings = self._bundle.current_settings()
        state = self._bundle.app_state.get()
        locale = str(state.ui_language or settings.ui_language)
        zh = locale.lower().startswith("zh")
        current_model = settings.active_model_name

        if command == "env":
            statuses = AuthManager(settings).get_env_credential_statuses()
            options = [
                {
                    "value": env_key,
                    "label": f"{env_key} ({info['api_format']})",
                    "description": f"{info['api_format']} / {info['model']}" + (" [active]" if info["active"] else ""),
                    "active": info["active"],
                }
                for env_key, info in statuses.items()
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "环境配置" if zh else "Env Config", "command": "env"},
                    select_options=options,
                )
            )
            return

        if command == "permissions":
            options = [
                {
                    "value": "default",
                    "label": "默认" if zh else "Default",
                    "description": "写入/执行前询问" if zh else "Ask before write/execute operations",
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
                    modal={"kind": "select", "title": "权限模式" if zh else "Permission Mode", "command": "permissions"},
                    select_options=options,
                )
            )
            return

        if command == "effort":
            options = [
                {"value": "low", "label": "低" if zh else "Low", "description": "最快响应" if zh else "Fastest responses", "active": settings.effort == "low"},
                {"value": "medium", "label": "中" if zh else "Medium", "description": "平衡推理" if zh else "Balanced reasoning", "active": settings.effort == "medium"},
                {"value": "high", "label": "高" if zh else "High", "description": "最深推理" if zh else "Deepest reasoning", "active": settings.effort == "high"},
                {"value": "xhigh", "label": "超高" if zh else "XHigh", "description": "超深推理" if zh else "Extra deep reasoning", "active": settings.effort == "xhigh"},
                {"value": "max", "label": "最大" if zh else "Max", "description": "最大推理深度" if zh else "Maximum reasoning depth", "active": settings.effort == "max"},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "推理强度" if zh else "Reasoning Effort", "command": "effort"},
                    select_options=options,
                )
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
                    modal={"kind": "select", "title": "最大令牌数" if zh else "Max Tokens", "command": "max-tokens"},
                    select_options=options,
                )
            )
            return

        if command == "turns":
            current_turns: int | None = self._bundle.engine.max_turns
            values = {32, 64, 128, 200, 256, 512}
            if isinstance(current_turns, int):
                values.add(current_turns)
            options = [{"value": "unlimited", "label": "无限" if zh else "Unlimited", "description": "不对本会话硬性停止" if zh else "Do not hard-stop this session", "active": current_turns is None}]
            options.extend(
                {"value": str(value), "label": (f"{value} 轮" if zh else f"{value} turns"), "active": value == current_turns}
                for value in sorted(values)
            )
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "最大轮数" if zh else "Max Turns", "command": "turns"},
                    select_options=options,
                )
            )
            return

        if command == "language":
            current_lang = str(state.ui_language or "zh-CN")
            options = [
                {"value": "set zh-CN", "label": "简体中文", "description": "中文界面", "active": current_lang == "zh-CN"},
                {"value": "set en", "label": "English", "description": "English UI", "active": current_lang == "en"},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "语言" if zh else "Language", "command": "language"},
                    select_options=options,
                )
            )
            return

        if command == "model":
            options = self._model_select_options(current_model)
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "模型" if zh else "Model", "command": "model"},
                    select_options=options,
                )
            )
            return

        if command == "rewind":
            messages = self._bundle.engine.messages
            # 过滤后台任务完成通知与 goal harness 注入消息，它们不应出现在回退选项中
            user_msgs = [
                (i, msg) for i, msg in enumerate(messages)
                if msg.role == "user" and msg.text.strip()
                and not is_task_notification(msg.text)
                and not is_goal_system_message(msg.text)
            ]
            if not user_msgs:
                await self._emit(BackendEvent(
                    type="command_result",
                    command_result_data={
                        "message": "没有可回退的消息。" if zh else "No messages to rewind to.",
                        "type": "info",
                    },
                ))
                return
            options = []
            total = len(user_msgs)
            for k, (idx, msg) in enumerate(reversed(user_msgs)):
                text = msg.text.strip()
                label = text[:80] + ("…" if len(text) > 80 else "")
                options.append({
                    "value": str(idx),
                    "label": label,
                    "description": f"#{total - k}",
                })
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "回退到" if zh else "Rewind to", "command": "rewind"},
                    select_options=options,
                )
            )
            return

        if command == "delete":
            import time as _time

            from illusion.services.session_storage import list_session_snapshots

            try:
                sessions = list_session_snapshots(self._bundle.cwd, limit=10)
                if not sessions:
                    await self._emit(BackendEvent(
                        type="command_result",
                        command_result_data={
                            "message": "没有已保存的会话。" if zh else "No saved sessions found.",
                            "type": "info",
                        },
                    ))
                    return
                options = []
                for i, s in enumerate(sessions, 1):
                    ts = _time.strftime("%m/%d %H:%M", _time.localtime(s["created_at"]))
                    display = s.get("title") or s.get("summary", "")[:50] or ("（无摘要）" if zh else "(no summary)")
                    turn_count = s.get("turn_count", 0)
                    options.append({
                        "value": s["session_id"],
                        "label": f"#{i}  {ts}  {turn_count}轮  {display}",
                    })
                options.append({
                    "value": "__all__",
                    "label": ("清除所有会话" if zh else "Delete all sessions"),
                    "description": ("删除全部已保存的会话快照" if zh else "Remove all saved session snapshots"),
                })
                await self._emit(
                    BackendEvent(
                        type="select_request",
                        modal={"kind": "select", "title": "删除会话" if zh else "Delete Session", "command": "delete"},
                        select_options=options,
                    )
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("Error listing sessions for delete")
                await self._emit(
                    BackendEvent(
                        type="command_result",
                        command_result_data={
                            "message": f"Error listing sessions: {exc}",
                            "type": "error",
                        },
                    )
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
            project_permissions = load_project_permissions(self._bundle.cwd)

            # 检查是否禁用所有 rules
            if is_rules_disabled(project_permissions):
                await self._emit(BackendEvent(type="error", message=("所有规则已被禁用" if zh else "All rules are disabled")))
                return

            rules_dir = get_project_rules_dir(self._bundle.cwd)
            all_rule_files = sorted(rules_dir.glob("*.md"))
            if not all_rule_files:
                await self._emit(BackendEvent(type="error", message=(f"没有找到规则文件：{rules_dir}" if zh else f"No rules found in {rules_dir}")))
                return

            # 过滤掉被禁用的 rules
            rule_files = filter_rules_by_permissions(all_rule_files, project_permissions)

            options = []
            for path in rule_files:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                first_line = content.split("\n", 1)[0][:60] if content else ("（空）" if zh else "(empty)")
                options.append({
                    "value": path.stem,
                    "label": path.stem,
                    "description": first_line,
                })
            if not options:
                await self._emit(BackendEvent(type="error", message=("没有可用的规则文件" if zh else "No available rules files")))
                return
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "查看规则" if zh else "View Rules", "command": "rules"},
                    select_options=options,
                )
            )
            return

        if command == "skills":
            from illusion.skills.loader import load_skill_registry

            skill_registry = load_skill_registry(self._bundle.cwd)
            skills = skill_registry.list_skills()

            if not skills:
                await self._emit(BackendEvent(type="error", message="No skills available."))
                return

            options = []
            for skill in skills:
                source = f" [{skill.source}]"
                first_line = skill.description.split("\n", 1)[0][:60] if skill.description else ("（空）" if zh else "(empty)")
                options.append({
                    "value": skill.name,
                    "label": f"{skill.name}{source}",
                    "description": first_line,
                })

            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "查看技能" if zh else "View Skills", "command": "skills"},
                    select_options=options,
                )
            )
            return

        if command == "agent":
            # 双数据源列出已完成任务：
            #   1. 前台 agent：从 transcript 提取 tool_result（tool_name='agent'，且非后台启动）
            #   2. 后台任务（agent / bash / powershell 等）：从 transcript 的 task-notification 提取
            #      直接用 <task-name> 标签获取展示名，摆脱 task_id → tool_use_id 映射依赖
            #      若 <result> 为空，从 tasks 目录的 .log 文件提取实际输出
            from illusion.config.paths import get_tasks_dir
            from illusion.engine.messages import TextBlock, ToolResultBlock
            from illusion.swarm.agent_executor import agent_type_display
            from illusion.tasks.types import TASK_NOTIFICATION_RE

            task_options: list[dict[str, Any]] = []
            order = 0

            # 1. 前台 agent：从 transcript 提取 tool_result（tool_name='agent'，且非后台启动）
            #    跳过 tool_result 内容为"launched in background/as subprocess"的启动通知（非摘要）
            pending_labels: dict[str, str] = {}  # tool_use_id -> label（暂存 assistant 的 agent 调用标签）
            for msg in self._bundle.engine.messages:
                if msg.role == "assistant":
                    for use_block in msg.tool_uses:
                        if use_block.name == "agent":
                            inp = use_block.input or {}
                            task_name = str(inp.get("description") or inp.get("name") or "agent")[:30]
                            # agent 类型：input 完全未到达时显示 "Agent"；
                            # 到达后转 PascalCase，无 subagent_type 则默认 "GeneralPurpose"
                            # （与后台 agent 的 task_name 类型段共用同一转换）
                            if not inp:
                                agent_type = "Agent"
                            else:
                                agent_type = agent_type_display(
                                    str(sub) if (sub := inp.get("subagent_type")) is not None else None
                                )
                            label_name = f"{task_name} · {agent_type}"
                            pending_labels[use_block.id] = label_name
                elif msg.role == "user":
                    for result_block in msg.content:
                        if isinstance(result_block, ToolResultBlock) and result_block.tool_use_id in pending_labels:
                            text = result_block.text_content
                            if text and ("launched in background" in text or "launched as subprocess" in text):
                                continue  # 后台启动通知，其结果从 task-notification 提取
                            order += 1
                            label_name = pending_labels[result_block.tool_use_id]
                            first_line = text.split("\n", 1)[0][:60] if text else ("（无摘要）" if zh else "(no summary)")
                            task_options.append({
                                "value": result_block.tool_use_id,
                                "label": f"#{order} {label_name}",
                                "description": first_line,
                            })

            # 2. 后台任务（agent / bash / powershell 等）：从 transcript 的 task-notification 提取
            #    直接用 <task-name> 作为 label，不再需要 task_id → tool_use_id 映射
            #    若 <result> 为空，从 tasks 目录的 {task_id}.log 文件提取实际输出
            tasks_dir = get_tasks_dir()
            for msg in self._bundle.engine.messages:
                if msg.role != "user":
                    continue
                for text_block in msg.content:
                    if not isinstance(text_block, TextBlock):
                        continue
                    match = TASK_NOTIFICATION_RE.search(text_block.text)
                    if not match:
                        continue
                    status = match.group("status").strip()
                    if status != "completed":
                        continue
                    task_id = match.group("task_id").strip()
                    task_name = (match.group("task_name") or "").strip()
                    summary_tag = match.group("summary").strip()
                    result_text = match.group("result").strip()
                    # 若 <result> 为空，从 tasks 目录的 .log 文件提取实际输出
                    if not result_text:
                        try:
                            log_file = tasks_dir / f"{task_id}.log"
                            if log_file.exists():
                                content = log_file.read_text(encoding="utf-8", errors="replace")
                                result_text = content[-12000:] if len(content) > 12000 else content
                        except OSError:
                            pass
                    order += 1
                    # label 优先用 task_name，回退到 summary 提取的名称（兼容旧通知）。
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
                await self._emit(BackendEvent(type="error", message=("没有已完成的 agent" if zh else "No completed agents")))
                return
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": ("已完成任务摘要" if zh else "Completed Task Summary"), "command": "agent"},
                    select_options=task_options,
                )
            )
            return

        if command == "context":
            current_window = settings.context_window
            # 上下文占用：最后一次 API 调用的真实值 + 新增消息估算
            estimated = self._bundle.engine.current_context_tokens()
            percentage = round(estimated * 100 / current_window) if current_window > 0 else 0
            options = [
                {
                    "value": "__change_window__",
                    "label": "修改上下文窗口大小" if zh else "Change context window size",
                    "description": f"当前: {current_window:,} tokens" if zh else f"Current: {current_window:,} tokens",
                },
                {
                    "value": "__usage__",
                    "label": "查看上下文使用情况" if zh else "View context usage",
                    "description": f"已用: ~{estimated:,} / {current_window:,} tokens ({percentage}%)" if zh else f"Used: ~{estimated:,} / {current_window:,} tokens ({percentage}%)",
                },
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "上下文管理" if zh else "Context Management", "command": "context"},
                    select_options=options,
                )
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
            options.append({
                "value": "__custom__",
                "label": "其他（自定义输入）" if zh else "Other (custom)",
            })
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "上下文窗口大小" if zh else "Context Window Size", "command": "context-window"},
                    select_options=options,
                )
            )
            return

        if command == "rename":
            # /rename 无参数 → 步1：选择重命名会话或自动标题
            options = [
                {"value": "session", "label": "重命名会话" if zh else "Rename session", "description": "给会话设置自定义名称" if zh else "Set a custom session name"},
                {"value": "title", "label": "自动标题" if zh else "Auto title", "description": "配置自动标题开关" if zh else "Configure auto title", "active": settings.title.enabled},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "重命名" if zh else "Rename", "command": "rename"},
                    select_options=options,
                )
            )
            return

        if command == "rename_session":
            # /rename session → 步2：选择要重命名的会话
            import time as _time

            from illusion.services.session_storage import list_session_snapshots

            sessions = list_session_snapshots(self._bundle.cwd, limit=20)
            if not sessions:
                # 空列表也要释放 busy：command_result 不释放，必须补 line_complete
                await self._emit_result("没有已保存的会话。" if zh else "No saved sessions found.")
                return
            options = []
            for i, s in enumerate(sessions, 1):
                ts = _time.strftime("%m/%d %H:%M", _time.localtime(s.get("updated_at", s.get("created_at", 0))))
                display = s.get("title") or s.get("summary", "")[:50] or ("（无摘要）" if zh else "(no summary)")
                turn_count = s.get("turn_count", 0)
                options.append({
                    "value": s["session_id"],
                    "label": f"#{i}  {ts}  {turn_count}轮  {display}",
                })
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "重命名会话" if zh else "Rename Session", "command": "rename_session"},
                    select_options=options,
                )
            )
            return

        if command == "rename_title":
            # /rename title → 步2：自动标题开关
            options = [
                {"value": "on", "label": "开启自动标题" if zh else "Enable auto title", "active": settings.title.enabled},
                {"value": "off", "label": "关闭自动标题" if zh else "Disable auto title", "active": not settings.title.enabled},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "自动标题" if zh else "Auto Title", "command": "rename_title"},
                    select_options=options,
                )
            )
            return

        if command == "rename_title_model":
            # /rename title → 步3：选择标题生成模型
            options = self._model_selector_options(settings.title.model)
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "标题模型" if zh else "Title Model", "command": "rename_title_model"},
                    select_options=options,
                )
            )
            return

        if command == "memory":
            # /memory 无参数 → 步1：选择记忆功能或后台自动提取
            options = [
                {"value": "mem", "label": "记忆" if zh else "Memory", "description": "管理记忆功能开关" if zh else "Manage the memory feature", "active": settings.memory.enabled},
                {"value": "auto", "label": "后台自动提取" if zh else "Auto extract", "description": "管理后台自动提取与整合" if zh else "Manage background auto extract & dream", "active": settings.memory.auto_extract},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "记忆功能" if zh else "Memory", "command": "memory"},
                    select_options=options,
                )
            )
            return

        if command == "memory_enable":
            # /memory → 步2：记忆功能开关
            options = [
                {"value": "on", "label": "开启记忆" if zh else "Enable memory", "active": settings.memory.enabled},
                {"value": "off", "label": "关闭记忆" if zh else "Disable memory", "active": not settings.memory.enabled},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "记忆功能" if zh else "Memory", "command": "memory_enable"},
                    select_options=options,
                )
            )
            return

        if command == "memory_auto":
            # /memory → 步3（记忆分支与 auto 分支共用）：后台自动提取与整合开关
            options = [
                {"value": "on", "label": "开启提取与整合" if zh else "Enable extract & dream", "active": settings.memory.auto_extract},
                {"value": "off", "label": "关闭提取与整合" if zh else "Disable extract & dream", "active": not settings.memory.auto_extract},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "后台自动提取" if zh else "Auto Extract", "command": "memory_auto"},
                    select_options=options,
                )
            )
            return

        if command == "memory_extract_model":
            # /memory → 步4：选择提取子代理模型
            options = self._model_selector_options(settings.memory.extract_model)
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "提取模型" if zh else "Extract Model", "command": "memory_extract_model"},
                    select_options=options,
                )
            )
            return

        if command == "memory_dream_model":
            # /memory → 步5：选择整合子代理模型
            options = self._model_selector_options(settings.memory.dream_model)
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "整合模型" if zh else "Dream Model", "command": "memory_dream_model"},
                    select_options=options,
                )
            )
            return

        await self._emit(BackendEvent(type="error", message=(f"/{command} 暂无可选项" if zh else f"No selector available for /{command}")))

    def _model_select_options(self, current_model: str) -> list[dict[str, object]]:
        """从 settings.json 的 env_N 配置中提取所有实际可用的模型。"""
        assert self._bundle is not None
        settings = self._bundle.current_settings()
        envs = settings.list_envs()

        seen: set[str] = set()
        options: list[dict[str, object]] = []

        # 当前模型排第一位
        if current_model:
            seen.add(current_model)
            options.append({
                "value": current_model,
                "label": current_model,
                "description": "Current",
                "active": True,
            })

        # 遍历所有 env，提取 model_N
        for env_key, env in envs.items():
            for model_key, model_name in env.list_models().items():
                ref = f"{env_key}.{model_key}"
                if ref in seen:
                    continue
                seen.add(ref)
                is_current = ref == settings.model
                options.append({
                    "value": ref,
                    "label": model_name,
                    "description": f"{env_key} ({env.api_format})",
                    "active": is_current,
                })

        return options

    def _model_selector_options(self, current_ref: str | None) -> list[dict[str, object]]:
        """构建子代理模型选择选项：[继承当前] + 各 env 模型的引用列表。

        用于标题生成、记忆提取/整合等可指定模型的子系统（None 继承当前）。
        值取 ``env_N.model_M`` 引用，label 为模型名。
        """
        assert self._bundle is not None
        locale = str(
            self._bundle.app_state.get().ui_language
            or self._bundle.current_settings().ui_language
        )
        zh = locale.lower().startswith("zh")
        options: list[dict[str, object]] = [{
            "value": "",
            "label": "继承当前" if zh else "Inherit current",
            "description": "使用当前会话上下文模型" if zh else "Use the current conversational model",
            "active": not current_ref,
        }]
        settings = self._bundle.current_settings()
        for env_key, env in settings.list_envs().items():
            for model_key, model_name in env.list_models().items():
                ref = f"{env_key}.{model_key}"
                options.append({
                    "value": ref,
                    "label": model_name,
                    "description": f"{env_key} ({env.api_format})",
                    "active": ref == current_ref,
                })
        return options

    @contextlib.asynccontextmanager
    async def _acquire_modal_lock(self) -> AsyncIterator[None]:
        """获取 modal 串行锁（同会话排队：前一个完成后自然后续继续）。"""
        async with self._modal_lock:
            yield

    async def _ask_permission(self, tool_name: str, reason: str, high_risk: bool = False) -> bool:
        # 如果工具在本会话内已获允许，则直接允许（不持久化）。
        # 高危操作（high_risk）不可被会话级豁免：即使工具名已放行，仍须重新确认，
        # 防止"本次会话允许"被用作高危命令（如 rm -rf）的通行证。
        if not high_risk and tool_name in self._session_allowed_tools:
            return True
        # 同会话 modal 串行排队：前端 modal 是单例，前一个完成后自然后续继续
        async with self._acquire_modal_lock():
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
                )
            )
            try:
                # 等待用户响应：所有会话的权限请求统一 285s 超时（超时按
                # "超时拒绝"处理，错误回流为工具结果且清理弹窗，防止
                # "未确认权限 + 超时"导致弹窗遗留）。
                from illusion.engine.query import wait_for_permission_decision

                return await wait_for_permission_decision(future, tool_name)
            finally:
                # 兜底：请求被放弃（超时/取消）时确保 future 不被悬挂
                if not future.done():
                    future.set_result(False)
                self._permission_requests.pop(request_id, None)
                # 清理前端的权限弹窗：正常响应路径前端已自行关闭（重复发送
                # modal=None 无害），超时/取消路径必须显式关闭，防止弹窗残留
                await self._emit(BackendEvent(type="modal_request", modal=None))

    async def _ask_question(self, question: str, questions: object = None) -> str | dict[Any, Any]:
        # 同会话 modal 串行排队：前端 modal 是单例，前一个完成后自然后续继续
        async with self._acquire_modal_lock():
            request_id = uuid4().hex
            future: asyncio.Future[str | dict[Any, Any]] = asyncio.get_running_loop().create_future()
            self._question_requests[request_id] = future
            # 优先使用显式传入的结构化问题数据，回退到 _last_tool_inputs
            questions_data = questions
            if questions_data is None:
                tool_input = self._last_tool_inputs.get("ask_user_question", {})
                questions_data = tool_input.get("questions")
            # 如果是 pydantic 模型列表，转为 dict[str, Any]
            if questions_data is not None and isinstance(questions_data, list):
                questions_data = [
                    q.model_dump() if hasattr(q, "model_dump") else q
                    for q in questions_data
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
                )
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
                # 清理前端的提问/确认弹窗：超时/取消路径必须显式关闭，防残留
                await self._emit(BackendEvent(type="modal_request", modal=None))

    async def _ask_plan_approval(self, plan: str) -> tuple[bool, str]:
        """向用户展示计划并等待审批。

        先将计划内容作为 plan 消息写入对话流，再复用 question 模态让用户选择批准或拒绝。
        用户可通过"其他"选项输入反馈文字。

        Args:
            plan: 计划内容（Markdown 格式）

        Returns:
            tuple[bool, str]: (是否批准, 用户反馈)
        """
        # 将计划写入对话流
        await self._emit(
            BackendEvent(
                type="transcript_item",
                item=TranscriptItem(role="plan", text=plan),
            )
        )
        # 复用 question 模态，提供批准/拒绝选项
        from illusion.config.i18n import t as _t
        # 同会话 modal 串行排队：前端 modal 是单例，前一个完成后自然后续继续
        async with self._acquire_modal_lock():
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
                )
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
                # 与 _ask_permission/_ask_question 一致：超时/取消/异常路径
                # 显式关闭计划审批弹窗，防残留
                try:
                    self._write_queue.put_nowait(
                        BackendEvent(type="modal_request", modal=None)
                    )
                except QueueShutDown:
                    pass

    async def _stop_active_line(self) -> None:
        task = self._active_line_task
        # 检查是否有运行中的后台任务：主循环空闲（后台 agent 在跑）时
        # _active_line_task 为 None，但 Ctrl+X 仍应终止 agent 进程
        has_running_tasks = False
        if self._bundle is not None:
            has_running_tasks = any(
                t.status in ("running", "pending")
                for t in get_task_manager().list_tasks()
            )
        if (task is None or task.done()) and not has_running_tasks:
            from illusion.config.i18n import t as _t
            await self._emit(BackendEvent(
                type="command_result",
                command_result_data={"message": _t("no_active_task"), "type": "info"},
            ))
            return
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # 停止所有正在运行的后台任务（agent / bash / powershell 等）
        if self._bundle is not None:
            from illusion.ui.runtime import stop_all_tasks
            await stop_all_tasks(self._bundle)
        self._busy = False
        await self._update_phase("idle")
        await self._emit(BackendEvent(type="modal_request", modal=None))
        from illusion.config.i18n import t as _t
        stopped_message = _t("task_stopped")
        await self._emit(
            BackendEvent(
                type="transcript_item",
                item=TranscriptItem(role="system", text=stopped_message),
            )
        )
        await self._emit(BackendEvent(
            type="command_result",
            command_result_data={"message": stopped_message, "type": "info"},
        ))
        await self._emit(self._status_snapshot())
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
        await self._emit(BackendEvent(type="line_complete"))

    async def _handle_agent_wizard_init(self, req: FrontendRequest) -> None:
        """处理 agent_wizard_init：返回可用工具/模型列表。"""
        assert self._bundle is not None
        tools = list_available_tools(self._bundle.tool_registry)
        models = list_available_models(self._bundle.app_state)
        await self._emit(BackendEvent(type="agent_wizard_init_response", tools=tools, models=models))

    async def _handle_agent_wizard_submit(self, req: FrontendRequest) -> None:
        """处理 agent_wizard_submit：校验并写入 agent 定义文件。"""
        assert self._bundle is not None
        fields = req.fields or {}
        scope = req.scope or "user"
        errors = validate_agent_definition(fields, self._bundle.cwd)
        if errors:
            await self._emit(BackendEvent(type="agent_wizard_result", success=False, errors=errors))
            return
        try:
            path = write_agent_definition(fields, scope, self._bundle.cwd)
        except OSError as exc:
            await self._emit(BackendEvent(type="agent_wizard_result", success=False, errors={"_": str(exc)}))
            return
        await self._emit(BackendEvent(type="agent_wizard_result", success=True, path=str(path)))

    async def _handle_agent_generate_request(self, req: FrontendRequest) -> None:
        """处理 agent_generate_request：LLM 辅助生成 agent 配置。"""
        assert self._bundle is not None
        request_id = req.request_id or ""
        engine = self._bundle.engine
        from illusion.coordinator.agent_definitions import get_all_agent_definitions
        existing = [a.name for a in get_all_agent_definitions()]
        try:
            generated = await generate_agent_from_description(
                req.prompt or "", req.model or "inherit", existing, engine,
            )
            await self._emit(BackendEvent(
                type="agent_generate_response",
                request_id=request_id,
                agent={"identifier": generated.identifier, "when_to_use": generated.when_to_use, "system_prompt": generated.system_prompt},
            ))
        except Exception as exc:  # noqa: BLE001
            await self._emit(BackendEvent(type="agent_generate_response", request_id=request_id, error=str(exc)))

    async def _handle_agent_generate_cancel(self, req: FrontendRequest) -> None:
        """处理 agent_generate_cancel：取消进行中的生成。"""
        # 当前 generate 为同步 await，取消依赖前端忽略响应；预留扩展点
        request_id = req.request_id or ""
        await self._emit(BackendEvent(type="agent_generate_response", request_id=request_id, error="cancelled"))

    async def _handle_file_mentions(self, req: FrontendRequest) -> None:
        """收集 @ 提及补全候选并推送 web_file_mentions 事件。

        与 web 端 handle_web_request_file_mentions 对称：仅返回工作区内
        路径与技能名候选（不读内容），选中后的提及文本保持普通 prompt
        文本。request_id 原样回显供前端丢弃过期响应。

        Args:
            req: 前端请求（query 为 @ 后的路径片段；request_id 回显键）
        """
        from illusion.ui.file_mentions import (
            file_mention_candidates,
            normalize_mention_query,
            skill_mention_candidates,
        )

        assert self._bundle is not None
        cwd = self._bundle.cwd
        query = normalize_mention_query(req.query)
        candidates, truncated = await asyncio.to_thread(file_mention_candidates, cwd, query)
        skills = await asyncio.to_thread(skill_mention_candidates, cwd, query)
        await self._emit(BackendEvent(
            type="web_file_mentions",
            cwd=cwd,
            request_id=req.request_id,
            web_file_mentions={
                "query": query,
                "candidates": candidates,
                "skills": skills,
                "truncated": truncated,
            },
        ))

    async def _update_phase(self, phase: str) -> None:
        """更新会话阶段。"""
        assert self._bundle is not None
        self._bundle.app_state.set(phase=phase)

    async def _write_loop(self) -> None:
        """单一消费者：串行化所有 stdout 写入。

        所有 _emit() 调用通过 _write_queue，确保 FIFO 排序和无并发 stdout 访问。
        不依赖线程池。收到 QueueShutDown 后退出循环。
        """
        while True:
            try:
                event = await self._write_queue.get()
            except QueueShutDown:
                break
            try:
                payload = _PROTOCOL_PREFIX + event.model_dump_json() + "\n"
                data = payload.encode("utf-8")
                # stdout 写入仍需 to_thread（阻塞 I/O），但单一消费者不争抢
                await asyncio.to_thread(sys.stdout.buffer.write, data)
                await asyncio.to_thread(sys.stdout.buffer.flush)
            except Exception:
                log.exception("写入 stdout 失败")

    async def _emit(self, event: BackendEvent) -> None:
        """入队事件给写循环。非阻塞。"""
        try:
            self._write_queue.put_nowait(event)
        except QueueShutDown:
            pass  # 正在关闭，丢弃事件

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


async def run_backend_host(
    *,
    model: str | None = None,
    max_turns: int | None = None,
    cwd: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    restore_messages: list[dict[str, Any]] | None = None,
    restore_session_id: str | None = None,
    enforce_max_turns: bool = True,
    effort: str | None = None,
    channel_hint: str | None = None,
    channel_tools: list[Any] | None = None,
    permission_mode: str | None = None,
    name: str | None = None,
    continue_session: bool = False,
    resume: str | None = None,
) -> int:
    """Run the structured React backend host."""
    if cwd:
        os.chdir(cwd)
    host = ReactBackendHost(
        BackendHostConfig(
            model=model,
            max_turns=max_turns,
            api_client=api_client,
            restore_messages=restore_messages,
            restore_session_id=restore_session_id,
            enforce_max_turns=enforce_max_turns,
            effort=effort,
            channel_hint=channel_hint,
            channel_tools=channel_tools,
            permission_mode=permission_mode,
            name=name,
            continue_session=continue_session,
            resume=resume,
        )
    )
    return await host.run()


__all__ = ["BackendHostConfig", "ReactBackendHost", "run_backend_host"]
