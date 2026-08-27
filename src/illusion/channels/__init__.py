"""消息渠道模块
================

提供 IllusionAgent 的消息渠道能力（飞书等）。

主要导出：
    - ChannelRunner: 渠道消息接入 agent 的运行器
    - maybe_spawn_channel_daemon: 主程序自动激活渠道守护进程

本模块仅做延迟导入，不顶层依赖任何渠道 SDK。
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from illusion.channels.config import load_channels_config

if TYPE_CHECKING:
    from illusion.channels.base import Channel, InboundMessage
    from illusion.channels.config import ChannelsConfig
    from illusion.channels.feishu.streaming import FeishuStreamingCardController
    from illusion.channels.qq.streaming import QQStreamingController
    from illusion.config.settings import Settings
    from illusion.daemon_ipc import DaemonClient as DaemonClient
    from illusion.daemon_ipc import DaemonClientRef, DaemonType

logger = logging.getLogger(__name__)


def _config_fingerprint(cfg: ChannelsConfig) -> str:
    """计算渠道配置指纹（用于检测配置变更后重启守护进程）

    遍历 ChannelRegistry，对每个已启用渠道调用其 fingerprint_factory 生成标识。

    Args:
        cfg: 渠道配置

    Returns:
        str: 配置指纹（MD5 hex）
    """
    import hashlib
    import json as _json

    from illusion.channels.registry import ChannelRegistry

    # 遍历 registry 调用各渠道的 fingerprint_factory
    enabled_channels = []
    for desc in ChannelRegistry.all_descriptors():
        channel_cfg = getattr(cfg, desc.config_attr, None)
        if channel_cfg is not None and channel_cfg.enabled:
            enabled_channels.append(desc.fingerprint_factory(channel_cfg))
    raw = _json.dumps(sorted(enabled_channels), ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()


def maybe_spawn_channel_daemon(
    *, spawn_if_missing: bool = True,
) -> tuple[subprocess.Popen[bytes] | None, DaemonClientRef | None]:
    """主程序启动时自动拉起渠道守护进程（IPC 版，异步连接）

    通过 DaemonClient 连接 IPC。连接成功且指纹匹配则持有 client；
    指纹不匹配则杀旧进程 spawn 新的；连接失败则 spawn 新的。
    spawn 后立即返回，后台线程轮询连接（不阻塞主程序启动）。

    Args:
        spawn_if_missing: 连接失败时是否 spawn 新进程。backend-only 进程
            传 False（launcher 已负责 spawn），仅连接持有 ref。

    Returns:
        tuple: (Popen 实例或 None, DaemonClientRef 实例或 None)
    """
    from illusion.config.paths import get_channels_data_dir
    from illusion.daemon_ipc import (
        DaemonClient,
        DaemonClientRef,
        DaemonType,
        close_client,
        connect_and_register,
    )

    cfg = load_channels_config()
    if not cfg.has_enabled_channels():
        return None, None

    data_dir = get_channels_data_dir()
    current_fp = _config_fingerprint(cfg)

    # 尝试连接已运行的守护进程
    client = DaemonClient(
        daemon_type=DaemonType.CHANNEL,
        pid=os.getpid(),
        fingerprint=current_fp,
    )
    connected, resp = connect_and_register(client)

    if connected:
        if resp is not None and resp.get("type") == "ok":
            # 指纹匹配：包装到 ref 并返回
            ref = DaemonClientRef()
            ref.set(client)
            return None, ref

        # 指纹不匹配
        close_client(client)
        if not spawn_if_missing:
            # backend-only 模式：不杀旧进程也不 spawn，由 launcher 处理
            # 返回空 ref，后台线程会尝试连接新启动的守护进程
            ref = DaemonClientRef()
            _start_bg_connect(
                daemon_type=DaemonType.CHANNEL,
                fingerprint=current_fp,
                ref=ref,
                name="渠道守护进程",
            )
            return None, ref
        # launcher 模式：杀旧进程后 spawn 新的
        daemon_pid = client.daemon_pid
        if daemon_pid:
            try:
                if os.name == "nt":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x00010000, False, daemon_pid)
                    if handle:
                        kernel32.TerminateProcess(handle, 0)
                        kernel32.CloseHandle(handle)
                else:
                    import signal
                    os.kill(daemon_pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        # 继续向下 spawn 新进程
    else:
        _cleanup_old_channel_files(data_dir)

    # backend-only 模式：不 spawn，只连接（launcher 已负责 spawn）
    # 后台线程轮询连接守护进程，连接成功后持有 ref
    if not spawn_if_missing:
        ref = DaemonClientRef()
        _start_bg_connect(
            daemon_type=DaemonType.CHANNEL,
            fingerprint=current_fp,
            ref=ref,
            name="渠道守护进程",
        )
        return None, ref

    # spawn 子进程。stdout/stderr 重定向到 DEVNULL，避免与守护进程内部的
    # RotatingFileHandler（写 channel_serve.log）形成"双写者"：父进程若把 stdout 指向
    # 同一日志文件，子进程会持有一个绕过轮转的 fd，导致 Windows 上旧的滚动
    # 备份被永久锁定且无法删除、轮转也可能因覆盖被锁文件而失败。
    # 日志统一由守护进程内的 RotatingFileHandler 落盘（裁剪见 serve.py）。
    creation_flags = 0
    if os.name == "nt":
        creation_flags = 0x00000008 | 0x00000200

    try:
        daemon_cwd = str(Path.cwd())
    except (OSError, FileNotFoundError):
        daemon_cwd = str(data_dir)

    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(
            [sys.executable, "-m", "illusion", "channel", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
            env=env,
            cwd=daemon_cwd,
        )
    except OSError as exc:
        logger.warning("启动渠道守护进程失败: %s", exc)
        return None, None

    # 异步连接：后台线程轮询，不阻塞主程序启动
    ref = DaemonClientRef()
    _start_bg_connect(
        daemon_type=DaemonType.CHANNEL,
        fingerprint=current_fp,
        ref=ref,
        name="渠道守护进程",
    )

    return proc, ref


def _start_bg_connect(
    daemon_type: DaemonType,
    fingerprint: str | None,
    ref: DaemonClientRef,
    name: str,
) -> None:
    """启动后台线程轮询连接守护进程（不阻塞主程序）

    Args:
        daemon_type: 守护进程类型
        fingerprint: 配置指纹（channel 用，cron 为 None）
        ref: DaemonClientRef 容器，连接成功后 set
        name: 日志中显示的名称
    """
    import threading
    import time

    from illusion.daemon_ipc import DaemonClient, connect_and_register

    def _bg_connect() -> None:
        for _ in range(20):  # 最多 10s
            client = DaemonClient(
                daemon_type=daemon_type,
                pid=os.getpid(),
                fingerprint=fingerprint,
            )
            ok, _ = connect_and_register(client)
            if ok:
                ref.set(client)
                return
            time.sleep(0.5)
        logger.info("%s spawn 后 10s 内未能连接", name)

    t = threading.Thread(target=_bg_connect, daemon=True)
    t.start()


def _cleanup_old_channel_files(data_dir: Path) -> None:
    """清理旧版 PID/refs/fingerprint 文件"""
    for name in ("daemon.pid", "daemon.refs", "daemon.refs.lock", "daemon.fingerprint"):
        try:
            (data_dir / name).unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("清理旧版文件 %s 失败: %s", name, exc)


def kill_channel_daemon(proc: subprocess.Popen[bytes] | None) -> None:
    """已废弃的渠道守护进程终止函数（noop）

    .. deprecated::
        此函数为向后兼容保留。新方案采用 IPC 连接数管理：
        主程序退出时关闭 IPC 连接，守护进程检测到连接归零后自动退出。
        不再需要主动 kill 守护进程。

    Args:
        proc: 已废弃，忽略不处理
    """
    import warnings
    warnings.warn(
        "kill_channel_daemon() 已废弃，请使用 DaemonClient.close() 关闭 IPC 连接",
        DeprecationWarning,
        stacklevel=2,
    )


def is_channel_daemon_running() -> bool:
    """检查渠道守护进程是否正在运行（通过 IPC ping）

    用于退出时判断是否需要询问用户是否一同退出渠道。

    Note:
        新方案采用 IPC 连接数后，此函数仅用于诊断/查询。
        退出处理已改为关闭 IPC 连接，不再依赖此函数。

    Returns:
        bool: 守护进程在运行返回 True
    """
    from illusion.daemon_ipc import DaemonClient, DaemonType, ping_daemon
    client = DaemonClient(daemon_type=DaemonType.CHANNEL, pid=os.getpid())
    pong = ping_daemon(client, timeout=2.0)
    return pong is not None


def stop_channel_daemon_by_pid() -> bool:
    """已废弃的渠道守护进程停止函数（noop）

    .. deprecated::
        此函数为向后兼容保留。新方案采用 IPC 连接数管理：
        守护进程检测到连接归零后自动退出，
        不再需要通过 PID 主动停止。

    Returns:
        bool: 始终返回 False
    """
    import warnings
    warnings.warn(
        "stop_channel_daemon_by_pid() 已废弃，请使用 DaemonClient.close() 关闭 IPC 连接",
        DeprecationWarning,
        stacklevel=2,
    )
    return False


class ChannelRunner:
    """渠道消息接入 agent 的运行器

    监听渠道入站消息，为每条消息构建临时 runtime 跑 agent，
    流式回复到渠道，并维护渠道会话历史。

    Attributes:
        channel: 渠道实例
        settings: 主设置
        session_store: 渠道会话存储（按渠道类型自动创建）
    """

    def __init__(self, *, channel: Channel, settings: Settings,
                 session_data_dir: Path, group_sessions_per_user: bool = True) -> None:
        """初始化

        Args:
            channel: 渠道实例
            settings: 主设置
            session_data_dir: 会话存储目录
            group_sessions_per_user: 群组会话是否按用户隔离
        """
        self.channel = channel  # 渠道
        self.settings = settings  # 主设置
        # 按渠道类型构造对应的会话存储
        self.session_store = _create_session_store(
            channel=channel,
            data_dir=session_data_dir,
            group_sessions_per_user=group_sessions_per_user,
        )
        self._pending_replies: dict[str, asyncio.Future[str]] = {}  # 权限/询问待回复
        # 按 chat_id 串行化 agent turn，避免并行消息导致会话历史覆盖
        # （同一会话连发多条消息时，M2/M3 排队等 M1 完成后再跑）
        self._chat_locks: dict[str, asyncio.Lock] = {}
        # 当前正在运行的 agent task（按 chat_id 索引），供 /stop 中断
        self._active_agent_tasks: dict[str, asyncio.Task[None]] = {}
        self._stop = False
        # fire-and-forget task 强引用集合，防止 GC 抢收
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    def _get_chat_lock(self, chat_id: str) -> asyncio.Lock:
        """获取指定 chat_id 的串行化锁（懒创建）"""
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    def _create_background_task(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """创建 fire-and-forget task 并保留强引用，防止 GC 抢收。

        Args:
            coro: 要执行的协程

        Returns:
            创建的 task
        """
        task = asyncio.create_task(coro)
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)
        return task

    async def run(self) -> None:
        """启动渠道，监听消息并处理"""
        await self.channel.connect()
        async for msg in self.channel.listen():
            if self._stop:
                break
            # 更新事件看门狗计时器（用于检测渠道僵死）
            watchdog = getattr(self.channel, "_event_watchdog", None)
            if watchdog is not None:
                watchdog.on_event()
            # 每条消息独立处理，加异常日志回调避免静默失败
            task = self._create_background_task(self._handle_message(msg))
            task.add_done_callback(self._log_task_exception)

    @staticmethod
    def _log_task_exception(task: asyncio.Task[None]) -> None:
        """任务完成后记录未捕获异常（避免静默吞掉错误）"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception("处理渠道消息未捕获异常: %s", exc, exc_info=exc)

    async def shutdown(self) -> None:
        """关闭渠道

        先 resolve 所有 _pending_replies Future 为空串，避免 agent turn 卡在
        _wait_reply 的 300s 超时；再关闭渠道触发 listen 协程退出。
        """
        # resolve 所有 pending replies Future（防止 agent turn 卡 300s 超时）
        for fut in self._pending_replies.values():
            if not fut.done():
                fut.set_result("")
        self._pending_replies.clear()

        self._stop = True
        await self.channel.shutdown()

    async def _handle_message(self, msg: InboundMessage) -> None:
        """处理单条入站消息

        优先匹配待回复的权限/询问，其次处理斜杠命令，最后跑 agent。
        同一 chat_id 的消息通过 _chat_locks 串行化，避免并行 agent turn
        导致会话历史覆盖（M2/M3 排队等 M1 完成后再跑）。

        Args:
            msg: 入站消息
        """
        # 1. 待回复的权限/询问——不加锁，让回复立即送达
        # （agent turn 持锁等待回复时，下一条消息作为回复立即 set_result，
        #   不会因锁阻塞导致 300s 超时）
        if msg.chat_id in self._pending_replies:
            fut = self._pending_replies.pop(msg.chat_id)
            if not fut.done():
                fut.set_result(msg.text)
            return

        # /stop 命令：立即中断当前 chat_id 正在运行的 agent task，不排队等锁
        # 必须在 _chat_locks 之前处理，否则会卡在串行队列里等到 agent 完成才生效
        text = msg.text.strip()
        if text.lower() == "/stop":
            await self._handle_stop(msg)
            return

        # 2/3. 斜杠命令 + agent turn：按 chat_id 串行化
        async with self._get_chat_lock(msg.chat_id):
            # 进入锁后再次检查 pending_replies：前一个 agent turn 可能
            # 刚刚设了 future 等待回复，此时新消息应作为回复而非新 turn
            if msg.chat_id in self._pending_replies:
                fut = self._pending_replies.pop(msg.chat_id)
                if not fut.done():
                    fut.set_result(msg.text)
                return

            # 2. 斜杠命令（按渠道类型选择 handler）
            handler = self._get_command_handler()
            if handler is not None and await handler.try_handle(msg):
                return

            # 3. 跑 agent
            # 将当前 task 注册到 _active_agent_tasks，供 /stop 中断
            current_task = asyncio.current_task()
            if current_task is not None:
                self._active_agent_tasks[msg.chat_id] = current_task
            try:
                await self._run_agent(msg)
            except asyncio.CancelledError:
                # /stop 取消：agent 已中断，发提示消息
                from illusion.config.i18n import t as _t
                logger.info("agent 任务被 /stop 中断: chat_id=%s", msg.chat_id)
                try:
                    await self.channel.send_text(
                        msg.chat_id, _t("cmd_stop_done"),
                        reply_to=msg.message_id,
                    )
                except Exception:
                    logger.warning("发送 /stop 中断提示失败: chat_id=%s", msg.chat_id, exc_info=True)
                raise
            except Exception as exc:
                logger.exception("处理渠道消息异常")
                try:
                    await self.channel.send_text(msg.chat_id, f"❌ 处理失败: {str(exc)[:100]}")
                except Exception:
                    logger.warning("发送错误提示失败: chat_id=%s", msg.chat_id, exc_info=True)
            finally:
                # 清理 task 注册（无论正常完成、异常还是取消）
                self._active_agent_tasks.pop(msg.chat_id, None)

    def _get_command_handler(self) -> Any:
        """按渠道类型返回对应的斜杠命令处理器

        遍历 ChannelRegistry，匹配 adapter_class 后调用 command_handler_factory。

        Returns:
            BaseCommandHandler 实例或 None（未知渠道）
        """
        from illusion.channels.registry import ChannelRegistry

        for desc in ChannelRegistry.all_descriptors():
            if isinstance(self.channel, desc.adapter_class):
                return desc.command_handler_factory(self.channel, self.session_store)
        return None

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """处理 /stop 命令：中断当前 chat_id 正在运行的 agent 任务

        不加 _chat_locks 锁，立即取消正在运行的 agent task。
        如果没有正在运行的任务，回复提示"无正在执行的任务"。

        Args:
            msg: /stop 命令消息
        """
        from illusion.config.i18n import t as _t

        task = self._active_agent_tasks.get(msg.chat_id)
        if task is None or task.done():
            # 无正在运行的任务
            await self.channel.send_text(
                msg.chat_id, _t("cmd_stop_no_task"),
                reply_to=msg.message_id,
            )
            return

        # 取消任务：触发 CancelledError，_run_agent 的 except 块清理流式控制器
        # _handle_message 的 except CancelledError 块发送"已中断"提示
        task.cancel()
        logger.info("/stop 已取消 agent 任务: chat_id=%s", msg.chat_id)

    def _build_channel_tools(self, msg: InboundMessage) -> list[Any]:
        """构造渠道内置工具列表

        按渠道类型和 enabled 状态构造工具。
        媒体工具对所有已启用渠道构造（飞书/QQ/微信均支持媒体收发）。

        Args:
            msg: 入站消息（用于获取 chat_id 和 attachments）

        Returns:
            list[Any]: BaseTool 实例列表
        """
        tools: list[Any] = []

        # 媒体工具（所有渠道）
        try:
            from illusion.channels.tools.media import ReceiveMediaTool, SendMediaTool
            tools.append(SendMediaTool(
                self.channel, msg.chat_id, message_id=msg.message_id
            ))
            if msg.attachments:
                tools.append(ReceiveMediaTool(
                    self.channel, msg.chat_id, msg.attachments
                ))
        except (ImportError, AttributeError, TypeError) as exc:
            logger.warning("构造媒体工具失败: %s", exc)

        # 跨渠道文件传输工具（所有渠道）
        try:
            from illusion.channels.config import load_channels_config
            from illusion.channels.tools.cross_channel import (
                ListChannelSessionsTool,
                SendToChannelTool,
            )
            all_cfg = load_channels_config()
            # 仅当有其他 enabled 渠道时才注入（避免单渠道时 LLM 误用）
            other_enabled = [
                n for n in all_cfg.enabled_channel_names()
                if n != self.channel.name
            ]
            if other_enabled:
                tools.append(ListChannelSessionsTool(all_cfg))
                tools.append(SendToChannelTool(all_cfg))
        except (ImportError, OSError, ValueError, AttributeError, TypeError) as exc:
            logger.warning("构造跨渠道工具失败: %s", exc)

        # Cron 工具（注入 origin 信息用于投递）
        try:
            from illusion.tools.cron_tool import CronTool
            tools.append(CronTool(
                origin_channel=self.channel.name,
                chat_id=msg.chat_id,
            ))
        except (ImportError, AttributeError, TypeError) as exc:
            logger.warning("构造 Cron 工具失败: %s", exc)

        return tools

    async def _run_agent(self, msg: InboundMessage) -> None:
        """为单条消息构建 runtime 并跑 agent

        统一流程：发送"思考中"提示 → 收集流式文本 → 一次性渲染/发送。
        飞书通过 edit_message patch 卡片，微信通过 send_text 发送。

        Args:
            msg: 入站消息
        """
        from illusion.engine.stream_events import (
            AssistantTextDelta,
            ErrorEvent,
        )
        from illusion.ui.runtime import build_runtime, close_runtime, handle_line

        key = self.session_store.build_session_key(msg)
        session = self.session_store.get_or_create(key, msg.user_id, msg.chat_type)

        # 渠道 agent 运行目录锚定：优先渠道配置的 working_directory
        # （每条消息动态读取，配置变更即时生效，无需重启守护进程），
        # 缺省回退默认工作区（settings.working_directory / 进程目录）。
        # 此前隐式继承守护进程启动目录，多目录空间下行为不可控。
        # 会话索引已记录 cwd 时以索引为准——历史 context.jsonl 绑定
        # 创建时的目录，配置变更不迁移既有会话。
        from illusion.channels.session_history import resolve_channel_working_directory

        channel_cwd = session.cwd or resolve_channel_working_directory(self.channel.name)
        if session.cwd != channel_cwd:
            session.cwd = channel_cwd

        # 提前落盘会话索引（仅当文件尚不存在时）：确保 session_id 落盘，
        # 这样进程崩溃后下次启动 get_or_create 能命中该会话记录接续，
        # 而非新建会话。注意：绝不覆盖已有索引（否则会丢失 session_id）。
        try:
            self.session_store.ensure_indexed(session)
        except (OSError, AttributeError, TypeError) as exc:
            logger.warning("会话索引提前落盘失败: %s", exc)

        # 加载对话历史（context.jsonl 单一权威）。restore_messages 需要
        # dict 形式供 engine 反序列化；checkpoint_count 传给 build_runtime
        # 对齐新建 store，避免每轮从 id=0 重复写 checkpoint 行。
        # 不做旧格式迁移：旧版映射内嵌的 messages 字段读取时被忽略。
        history_checkpoint_count: int | None
        try:
            history_result = await self.session_store.load_messages(session)
            history_checkpoint_count = history_result.checkpoint_count
        except Exception:
            # 计数传 None 而非 0：瞬时 IO 失败时让 build_runtime 的磁盘
            # 兜底对齐仍可生效，避免从 id=0 重复写 checkpoint 行
            history_result = None
            history_checkpoint_count = None
            logger.warning("渠道会话历史加载失败，按空历史继续", exc_info=True)
        history_dicts = (
            [m.model_dump(mode="json") for m in history_result.messages]
            if history_result is not None
            else []
        )

        # 检测渠道是否支持消息编辑（仅飞书支持卡片 patch）
        from illusion.channels.feishu.adapter import FeishuChannel
        from illusion.channels.qq.adapter import QQChannel
        supports_edit = isinstance(self.channel, FeishuChannel)

        # QQ C2C 流式检测：仅私聊（chat_type="dm"）支持 stream_messages API
        qq_channel = self.channel if isinstance(self.channel, QQChannel) else None
        qq_c2c_streaming = (
            qq_channel is not None
            and msg.chat_type == "dm"
            and bool(getattr(qq_channel, "_session", None))
        )

        # 统一收集流式文本，处理完后一次性发送/渲染
        collected_text: list[str] = []
        streaming_controller: FeishuStreamingCardController | None = None  # 飞书流式卡片控制器
        qq_streaming_controller: QQStreamingController | None = None  # QQ C2C 流式控制器

        # 加载渠道配置获取 show_reasoning 设置
        from illusion.channels.config import load_channels_config
        _channels_cfg = load_channels_config()

        async def render_event(ev: Any) -> None:
            """流式事件收集

            飞书：通过 controller 实时流式更新卡片（含 reasoning）
            QQ C2C：通过 controller 实时流式更新消息（不展示 reasoning）
            微信/QQ 群聊：仅累积文本，处理完后一次性发送
            """
            if isinstance(ev, AssistantTextDelta):
                if supports_edit and streaming_controller:
                    if ev.reasoning:
                        await streaming_controller.on_reasoning(ev.reasoning)
                    if ev.text:
                        await streaming_controller.on_text(ev.text)
                elif qq_streaming_controller:
                    if ev.reasoning:
                        await qq_streaming_controller.on_reasoning(ev.reasoning)
                    if ev.text:
                        await qq_streaming_controller.on_text(ev.text)
                collected_text.append(ev.text)
            elif isinstance(ev, ErrorEvent):
                collected_text.append(f"\n❌ {ev.message}")
                if supports_edit and streaming_controller:
                    await streaming_controller.error(ev.message)
                elif qq_streaming_controller:
                    await qq_streaming_controller.abort(ev.message)

        if supports_edit:
            # 飞书：用 CardKit 流式卡片控制器替代"思考中"卡片
            from illusion.channels.feishu.streaming import FeishuStreamingCardController
            feishu_show_reasoning = getattr(_channels_cfg.feishu, "show_reasoning", True)
            streaming_controller = FeishuStreamingCardController(
                client=cast("FeishuChannel", self.channel)._client,
                chat_id=msg.chat_id,
                reply_to=msg.message_id,
                show_reasoning=feishu_show_reasoning,
            )
            # 仅在 show_reasoning=True 时启动流式会话（立即显示思考指示器）
            if feishu_show_reasoning:
                await streaming_controller.start()
        elif qq_c2c_streaming and qq_channel is not None:
            # QQ C2C：用 stream_messages API 流式
            # 确保 token 已获取（通过 _get_token，重连后自动刷新）
            token = await qq_channel._get_token()
            from illusion.channels.qq.streaming import QQStreamingController
            qq_show_reasoning = getattr(_channels_cfg.qq, "show_reasoning", True)
            qq_streaming_controller = QQStreamingController(
                session=qq_channel._session,
                token=token,
                openid=msg.chat_id,
                msg_id=msg.message_id,
                show_reasoning=qq_show_reasoning,
            )
            # 仅在 show_reasoning=True 时启动流式会话（立即显示思考指示器）
            if qq_show_reasoning:
                await qq_streaming_controller.start()

        async def print_system(text: str) -> None:
            """系统消息转发到渠道"""
            await self.channel.send_text(msg.chat_id, text)

        async def clear_output() -> None:
            """无需清屏，空操作"""

        # 处理前：启动打字状态（微信需要，飞书空操作）
        await self.channel.start_typing(msg.chat_id)
        # 处理期间每 5s 刷新打字状态
        typing_task = asyncio.create_task(self._keep_typing_alive(msg.chat_id))

        logger.info("开始处理渠道消息: chat_id=%s text=%s", msg.chat_id, msg.text[:50])

        # 构建临时 runtime（复用 build_runtime，注入渠道工具）
        # 校验 session model 是否仍与当前活跃环境兼容，避免切格式后发到旧端点
        # 动态读取 settings.json：守护进程启动时快照可能已过期（主程序 /model 切换 env 后）
        from illusion.config.settings import load_settings as _load_settings_dynamic
        resolved_model = None
        if session.model:
            session_env = session.model.split(".")[0] if "." in session.model else ""
            current_env = getattr(_load_settings_dynamic(), "_active_env_key", "") or ""
            if session_env == current_env:
                resolved_model = session.model
            else:
                logger.info("session model %s 与当前环境 %s 不匹配，使用默认模型",
                            session.model, current_env)
        # 获取平台感知提示词（含当前渠道身份 + 其他 enabled 渠道概览）
        from illusion.channels.config import load_channels_config
        from illusion.prompts.channel_hints import (
            get_channel_hint,
            list_active_sessions,
        )
        all_cfg = load_channels_config()
        qq_md = getattr(self.channel.config, "markdown_support", None)
        # 枚举其他 enabled 渠道的活跃会话
        other_names = [
            n for n in all_cfg.enabled_channel_names()
            if n != self.channel.name
        ]
        active_sessions = {
            name: list_active_sessions(name, all_cfg, limit=5)
            for name in other_names
        }
        channel_hint = get_channel_hint(
            current_channel=self.channel.name,
            channels_config=all_cfg,
            qq_markdown_support=qq_md,
            active_sessions=active_sessions,
        )
        # 渠道 agent 运行目录已在函数开头锚定（channel_cwd）
        try:
            bundle = await build_runtime(
                model=resolved_model,
                # 不传 api_key：让 build_runtime 内部 load_settings() 动态解析当前 env 的 key
                # 守护进程启动时对 settings.json 做一次性快照，永不刷新；
                # 若传 self.settings.resolve_api_key() 会用旧 env 的 key，
                # 被 merge_cli_overrides 强行覆盖到新 env 的 EnvConfig 上 → "新端点+旧密钥" → 401
                restore_messages=history_dicts or None,
                restore_session_id=session.session_id,
                restore_checkpoint_count=history_checkpoint_count,
                permission_prompt=self._make_permission_prompt(msg.chat_id),
                ask_user_prompt=self._make_ask_user_prompt(msg.chat_id),
                plan_approval_prompt=self._make_plan_approval_prompt(msg.chat_id),
                # 渠道使用 YOLO 权限模式。
                # evaluate() 在 YOLO 下直接放行（跳过 sandbox_blocked 的
                # ask_user 常规确认分支与 requires_confirmation 分支），
                # 但显式 deny 规则（denied_tools / 路径 deny 规则 /
                # denied_commands）仍优先生效。CLI --permission-mode默认不
                # 影响渠道；如渠道需要恢复确认，改传对应模式即可。
                permission_mode="yolo",
                channel_hint=channel_hint,
                channel_tools=self._build_channel_tools(msg),
                cwd=channel_cwd,
            )
        except Exception as exc:
            logger.exception("构建 runtime 失败")
            await self.channel.send_text(msg.chat_id, f"❌ 启动失败: {str(exc)[:100]}")
            return

        # 拼接附件信息到消息文本前
        prompt_text = msg.text
        if msg.attachments:
            attach_lines = []
            for att in msg.attachments:
                size_str = f"{att.size} bytes" if att.size else "unknown size"
                attach_lines.append(
                    f"[收到附件 {att.id}: {att.filename} ({att.media_type}, {size_str})]"
                )
            prompt_text = "\n".join(attach_lines) + "\n" + msg.text

        try:
            await handle_line(
                bundle, prompt_text,
                print_system=print_system,
                render_event=render_event,
                clear_output=clear_output,
            )
            full_text = "".join(collected_text).strip()
            logger.info("agent 处理完成，回复长度=%d", len(full_text))
            if supports_edit and streaming_controller:
                # 飞书：通知 controller 完成（全卡替换为终态）
                await streaming_controller.complete()
            elif qq_streaming_controller:
                # QQ C2C：发送终结分片（input_state=DONE）
                await qq_streaming_controller.complete()
                # 降级检查：如果从未成功发出分片，走一次性发送
                if qq_streaming_controller.should_fallback_to_static and full_text:
                    await self.channel.send_text(msg.chat_id, full_text,
                                                 reply_to=msg.message_id)
            elif full_text:
                # 微信/QQ 群聊：一次性发送（QQ 群聊需要 reply_to 定位消息）
                await self.channel.send_text(msg.chat_id, full_text,
                                             reply_to=msg.message_id)
            # 同步 build_runtime 生成/恢复的 session_id 到索引，避免下次
            # 仍为空导致每次都生成新会话 ID（同一对话产生多个会话记录）。
            # 对话历史本身无需在此回写——handle_line 期间已由 CheckpointStore
            # 实时写入 context.jsonl（单一权威），此处仅持久化索引字段。
            if bundle.session_id and session.session_id != bundle.session_id:
                session.session_id = bundle.session_id
            self.session_store.save(session)
        except asyncio.CancelledError:
            # /stop 中断：清理流式控制器后重新抛出，让上层 _handle_message 发提示
            logger.info("agent 任务被取消: chat_id=%s", msg.chat_id)
            if supports_edit and streaming_controller:
                try:
                    await streaming_controller.error("已中断")
                except Exception:
                    logger.warning("通知流式控制器中断失败: chat_id=%s", msg.chat_id, exc_info=True)
            elif qq_streaming_controller:
                try:
                    await qq_streaming_controller.abort("已中断")
                except Exception:
                    logger.warning("中止 QQ 流式控制器失败: chat_id=%s", msg.chat_id, exc_info=True)
            raise
        except Exception as exc:
            logger.exception("agent 处理异常")
            if supports_edit and streaming_controller:
                # 飞书：通知 controller 错误终态
                await streaming_controller.error(str(exc))
            elif qq_streaming_controller:
                # QQ C2C：中止流式 + 降级到一次性发送错误消息
                await qq_streaming_controller.abort(str(exc))
                await self.channel.send_text(
                    msg.chat_id, f"❌ 处理失败: {exc}", reply_to=msg.message_id,
                )
            else:
                await self.channel.send_text(
                    msg.chat_id, f"❌ 处理失败: {exc}", reply_to=msg.message_id,
                )
        finally:
            typing_task.cancel()
            await self.channel.stop_typing(msg.chat_id)
            await close_runtime(bundle)

    async def _keep_typing_alive(self, chat_id: str) -> None:
        """每 5s 刷新打字状态（微信用，飞书空操作）

        Args:
            chat_id: 目标会话
        """
        while True:
            await asyncio.sleep(5)
            try:
                await self.channel.start_typing(chat_id)
            except Exception:
                logger.debug("刷新打字状态失败: chat_id=%s", chat_id, exc_info=True)

    def _make_permission_prompt(self, chat_id: str) -> Any:
        """构造权限确认回调（渠道自动放行）

        渠道整体运行在 yolo 权限模式：evaluate 在 YOLO 分支短路返回放行，
        requires_confirmation / sandbox_blocked 分支在渠道上不可达，权限
        回调形同虚设（返回 True 兜底）。渠道端仅显式 ask_user_question
        工具调用会经 _make_ask_user_prompt 发消息征询用户。
        """
        async def _prompt(tool: str, desc: str, high_risk: bool = False) -> bool:
            return True
        return _prompt

    def _make_ask_user_prompt(self, chat_id: str) -> Any:
        """构造用户问答回调（推到飞书等回复）

        签名与 backend_host/ws_host 的 _ask_question 一致：
        (question: str, questions: object = None) -> str | dict[str, str]

        questions 是结构化选项数据（list[dict]），含 question/header/options/
        multiSelect/noCustomInput 字段。

        多问题处理：len(questions) > 1 时逐个询问，每个问题单独发送一条消息，
        收到回复后再发下一个，最后合并为 dict 返回。
        单问题保持原有行为（拍平选项 + 等待回复）。
        多选提示：multiSelect=True 时在问题文本中加"(可多选，用逗号分隔)"提示。
        """
        async def _ask(question: str, questions: object = None) -> Any:
            # 无结构化问题数据：直接发问题文本等待回复
            if not questions or not isinstance(questions, (list, tuple)) or len(questions) == 0:
                text = f"❓ {question}"
                await self.channel.send_text(chat_id, text)
                return await self._wait_reply(chat_id, timeout=300)

            from illusion.ui.terminal_io import format_question_options as _format_question_options

            # 单问题：拍平选项 + 多选提示 + 等待回复
            if len(questions) == 1:
                q = questions[0]
                q_dict = q if isinstance(q, dict) else getattr(q, "model_dump", dict)()
                multi = q_dict.get("multiSelect", False)
                text = f"❓ {question}"
                try:
                    opts_lines = _format_question_options([q_dict])
                    if opts_lines:
                        text = f"{text}\n\n{opts_lines}"
                except Exception:
                    logger.debug("格式化问题选项失败: question=%s", question, exc_info=True)
                if multi:
                    hint = "（可多选，用逗号分隔）" if self._is_zh() else "(multi-select, separate with commas)"
                    text = f"{text}\n{hint}"
                await self.channel.send_text(chat_id, text)
                reply = await self._wait_reply(chat_id, timeout=300)
                if multi:
                    # 多选：拆分为 list，合并为 dict 返回
                    items = [s.strip() for s in reply.split(",") if s.strip()]
                    header = q_dict.get("header") or q_dict.get("question") or "answer"
                    return {header: items}
                return reply

            # 多问题：逐个询问，合并为 dict 返回
            answers: dict[str, str | list[str]] = {}
            for idx, q in enumerate(questions, 1):
                q_dict = q if isinstance(q, dict) else getattr(q, "model_dump", dict)()
                header = q_dict.get("header") or f"Q{idx}"
                sub_q = q_dict.get("question") or ""
                multi = q_dict.get("multiSelect", False)
                # 格式化单个问题
                text = f"❓ [{idx}/{len(questions)}] {header}: {sub_q}"
                try:
                    opts_lines = _format_question_options([q_dict])
                    if opts_lines:
                        text = f"{text}\n\n{opts_lines}"
                except Exception:
                    logger.debug("格式化问题选项失败: header=%s", header, exc_info=True)
                if multi:
                    hint = "（可多选，用逗号分隔）" if self._is_zh() else "(multi-select, separate with commas)"
                    text = f"{text}\n{hint}"
                await self.channel.send_text(chat_id, text)
                reply = await self._wait_reply(chat_id, timeout=300)
                if multi:
                    items = [s.strip() for s in reply.split(",") if s.strip()]
                    answers[header] = items
                else:
                    answers[header] = reply
            return answers
        return _ask

    def _make_plan_approval_prompt(self, chat_id: str) -> Any:
        """构造计划审批回调（发送计划内容到渠道并等待回复）

        签名与 backend_host 的 _ask_plan_approval 一致：
        (plan: str) -> tuple[bool, str]

        回调行为：
            1. 发送计划内容到渠道
            2. 发送审批问题
            3. 等待用户回复
            4. 解析回复：批准关键词 → (True, "")；其他输入 → (False, input)
        """
        from illusion.config.i18n import t as _t

        async def _approve(plan: str) -> tuple[bool, str]:
            # 1. 发送计划内容
            await self.channel.send_text(chat_id, plan)
            # 2. 发送审批问题
            await self.channel.send_text(chat_id, _t("channel_plan_approval_question"))
            # 3. 等待回复
            reply = await self._wait_reply(chat_id, timeout=300)
            # 4. 解析回复
            text = reply.strip().lower()
            approve_keywords = ("批准", "approve", "yes", "y")
            if text in approve_keywords:
                return (True, "")
            return (False, reply.strip() or "User rejected the plan.")

        return _approve

    def _is_zh(self) -> bool:
        """判断当前语言是否为中文（用于多选提示文案）"""
        try:
            from illusion.config import load_settings
            lang = load_settings().ui_language or "zh-CN"
            return lang.lower().startswith("zh")
        except (ImportError, OSError, ValueError, AttributeError):
            return True

    async def _wait_reply(self, chat_id: str, timeout: float) -> str:
        """等待指定 chat_id 的下一条消息作为回复

        Args:
            chat_id: 会话标识
            timeout: 超时秒数

        Returns:
            str: 回复文本

        Raises:
            asyncio.TimeoutError: 超时
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_replies[chat_id] = fut
        return await asyncio.wait_for(fut, timeout=timeout)


def _get_weixin_channel_class() -> Any:
    """延迟获取 WeixinChannel 类（避免循环导入）

    Returns:
        WeixinChannel 类，或 None（模块不可用时）
    """
    try:
        from illusion.channels.weixin.adapter import WeixinChannel
        return WeixinChannel
    except ImportError:
        return None


def _create_session_store(
    *,
    channel: Channel,
    data_dir: Path,
    group_sessions_per_user: bool = True,
) -> Any:
    """根据渠道类型创建对应的 SessionStore

    遍历 ChannelRegistry，匹配 adapter_class 后调用 session_store_factory。
    未知渠道回退到 FeishuSessionStore（向后兼容）。

    Args:
        channel: 渠道实例
        data_dir: 会话数据目录
        group_sessions_per_user: 群组会话是否按用户隔离

    Returns:
        对应渠道的 SessionStore 实例
    """
    from illusion.channels.registry import ChannelRegistry

    for desc in ChannelRegistry.all_descriptors():
        if isinstance(channel, desc.adapter_class):
            return desc.session_store_factory(
                channel, data_dir, group_sessions_per_user
            )
    # 未知渠道回退到飞书（向后兼容）
    from illusion.channels.feishu.session_map import FeishuSessionStore
    return FeishuSessionStore(
        data_dir=data_dir,
        group_sessions_per_user=group_sessions_per_user,
    )
