"""渠道守护进程入口
==================

实现 'illusion channel serve' 命令：读取 channels.json，
为每个 enabled 渠道启动 Channel，监听消息并接入 agent。

函数说明：
    - run_channel_serve: serve 命令主入口
"""
from __future__ import annotations

import asyncio  # 异步
import logging  # 日志
import os  # 进程强制退出
import signal  # 信号处理
import sys  # excepthook（未捕获异常回写日志）
import time  # 时间戳（_EventWatchdog 用）
from typing import Any  # 类型

from illusion.channels.config import ChannelsConfig, load_channels_config  # 配置

logger = logging.getLogger(__name__)  # 日志器

# 渠道守护进程看门狗退避：runner 异常退出后自动重启的间隔
SUPERVISOR_BACKOFF_SECONDS = (5.0, 10.0, 30.0)

# 渠道守护进程日志保留天数
_SERVE_LOG_TTL_DAYS = 7
# 渠道守护进程日志体积兜底阈值（10MB）：RotatingFileHandler 已限制单文件
# 10MB × 5 备份约 60MB，此阈值用于兜底清理孤儿滚动备份
_SERVE_LOG_MAX_SIZE_BYTES = 10 * 1024 * 1024

# 模块级 runner 注册表：name -> {runner, task, stop_event}
# 支持运行时动态启停单渠道 runner（通过 IPC start_channel/stop_channel 消息）
_runner_registry: dict[str, dict[str, Any]] = {}
# 守护进程事件循环引用（供 IPC 回调跨线程调度协程到守护进程事件循环）
_serve_loop: asyncio.AbstractEventLoop | None = None
# 守护进程启动时加载的配置和设置快照（供动态 start_channel 创建 runner 使用）
_serve_cfg: Any = None
_serve_settings: Any = None


def get_channel_status() -> dict[str, Any]:
    """获取渠道运行状态（用于 pong 响应）

    返回当前正在运行的渠道（_runner_registry 中的条目）。
    前端可对比 channels.json 的 enabled 与此处的运行状态，判断某渠道
    是"已启用但未运行"还是"正在运行"。

    Returns:
        dict: {渠道名: {healthy: bool, running: bool}}
    """
    status: dict[str, Any] = {}
    for name, entry in _runner_registry.items():
        runner = entry.get("runner")
        channel = getattr(runner, "channel", None)
        if channel is None:
            continue
        status[name] = {"healthy": True, "running": True}
    return status


def _check_channel_dependencies(cfg: ChannelsConfig) -> bool:
    """检查所有已启用渠道的依赖是否已安装

    遍历 ChannelRegistry 检查每个已启用渠道的依赖包。

    Args:
        cfg: 渠道配置

    Returns:
        bool: 所有依赖已安装返回 True，有缺失返回 False
    """
    from illusion.channels.registry import ChannelRegistry
    from illusion.config.i18n import t

    for desc in ChannelRegistry.all_descriptors():
        channel_cfg = getattr(cfg, desc.config_attr, None)
        if channel_cfg is None or not channel_cfg.enabled:
            continue
        for dep in desc.dependencies:
            try:
                __import__(dep)
            except ImportError:
                print(t("channel_deps_missing",
                        deps=", ".join(desc.dependencies), channel=desc.name))
                return False
    return True


def run_channel_serve() -> None:
    """渠道守护进程主入口（IPC 版）

    读取 channels.json，启动 DaemonServer 和所有 enabled 渠道。
    连接归零时自动退出。
    """
    from illusion.config.i18n import t
    from illusion.config.paths import get_channels_data_dir, get_logs_dir

    cfg = load_channels_config()
    settings = _load_settings_safely()

    if not cfg.has_enabled_channels():
        print(t("channel_none_configured"))
        return

    if not _check_channel_dependencies(cfg):
        return

    # 配置日志：RotatingFileHandler 写文件（大小轮转） + StreamHandler 控制台输出
    # 父进程 spawn 时 stdout/stderr 已重定向到 DEVNULL，避免"双写者"问题
    # （见 channels/__init__.py 的 DEVNULL 说明），日志统一由本 handler 落盘。
    # 轮转策略：单文件最大 10MB，保留 5 个备份（总计约 60MB），避免无限增长
    from logging.handlers import RotatingFileHandler

    from illusion.utils.log_cleanup import cleanup_old_files
    # 先清理超龄/超大的旧渠道守护进程日志（顺序在创建 handler 之前：
    # Windows 上被打开的文件无法删除，若 handler 先打开文件则清理会失败）。
    # glob 用 "channel_serve.log*" 以一并覆盖 RotatingFileHandler 的滚动备份
    # （channel_serve.log.1/.2/.3/.4/.5），并叠加体积阈值兜底。
    cleanup_old_files(
        get_logs_dir(),
        "channel_serve.log*",
        max_age_days=_SERVE_LOG_TTL_DAYS,
        max_size_bytes=_SERVE_LOG_MAX_SIZE_BYTES,
    )
    log_path = get_logs_dir() / "channel_serve.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
    # 文件 handler（可靠写盘 + 大小轮转：10MB × 5 备份）
    file_handler = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    # stdout handler（前台运行时可见；detached 时可能缓冲但不影响文件）
    # 注：守护进程通过 PYTHONIOENCODING=utf-8 启动，避免 Windows GBK 编码问题
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)
    logger.info("渠道守护进程启动，日志文件: %s", log_path)

    # stdout 已不重定向到日志文件（见 channels/__init__.py 的 DEVNULL 说明），
    # 补一个 excepthook 把未捕获异常写入日志，保留崩溃可追溯性。
    def _handle_uncaught(exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger().error(
            "渠道守护进程未捕获异常", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _handle_uncaught

    # 清理旧文件
    from illusion.channels import _cleanup_old_channel_files, _config_fingerprint
    _cleanup_old_channel_files(get_channels_data_dir())

    # 启动 IPC 服务端
    from illusion.daemon_ipc import DaemonServer, DaemonType
    fingerprint = _config_fingerprint(cfg)

    # 渠道动态启停回调：IPC handler 线程调用，通过 call_soon_threadsafe
    # 调度到守护进程事件循环执行协程（创建/取消 asyncio task 必须在 loop 线程）
    def _on_start_channel(name: str) -> None:
        loop_ref = _serve_loop
        if loop_ref is not None:
            def _schedule(n: str = name) -> None:
                asyncio.ensure_future(_start_channel_internal(n))
            loop_ref.call_soon_threadsafe(_schedule)

    def _on_stop_channel(name: str) -> None:
        loop_ref = _serve_loop
        if loop_ref is not None:
            def _schedule(n: str = name) -> None:
                asyncio.ensure_future(_stop_channel_internal(n))
            loop_ref.call_soon_threadsafe(_schedule)

    server = DaemonServer(
        daemon_type=DaemonType.CHANNEL,
        daemon_pid=os.getpid(),
        fingerprint=fingerprint,
        on_reload=_on_settings_reload,
        on_start_channel=_on_start_channel,
        on_stop_channel=_on_stop_channel,
    )

    global _serve_loop
    loop: asyncio.AbstractEventLoop | None = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # 暴露 loop 引用供 IPC 回调跨线程调度
        _serve_loop = loop
        loop.run_until_complete(server.start())
        loop.run_until_complete(_serve_async(cfg, settings, server))
    except KeyboardInterrupt:
        pass
    finally:
        _serve_loop = None
        if loop is not None:
            loop.run_until_complete(server.stop())
            loop.close()


def _load_settings_safely() -> Any:
    """安全加载主设置，失败时返回 None

    Returns:
        Settings 实例或 None
    """
    try:
        from illusion.config import load_settings
        return load_settings()
    except (ImportError, OSError, ValueError, AttributeError) as exc:
        logger.warning("加载主设置失败: %s", exc)
        return None


def _on_settings_reload() -> None:
    """reload 回调：重新加载 settings.json 和 channels.json 并刷新所有 runner/channel 引用

    主程序 /model 切换 env 后通过 IPC reload 消息触发。
    前端修改 channels.json 的 enabled 等字段后也通过 reload 刷新守护进程配置快照，
    否则 _serve_cfg 仍为旧值，_create_runner 检查 enabled 时会误判"配置缺失或未启用"。
    """
    fresh_settings = _load_settings_safely()
    if fresh_settings is None:
        logger.warning("reload 失败：无法加载 settings.json")
        return
    global _serve_settings, _serve_cfg
    _serve_settings = fresh_settings
    # 同时重新加载 channels.json，确保 enabled 等字段为最新
    try:
        _serve_cfg = load_channels_config()
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("reload channels.json 失败: %s", exc)
    for entry in _runner_registry.values():
        runner = entry.get("runner")
        if runner is None:
            continue
        runner.settings = fresh_settings
        channel = getattr(runner, "channel", None)
        if channel is not None:
            channel.settings = fresh_settings
    logger.info("守护进程配置已重新加载（model=%s, env=%s）",
                fresh_settings.model, getattr(fresh_settings, "_active_env_key", ""))


def _create_runner(name: str) -> Any | None:
    """根据渠道名创建 runner 实例（复用 _serve_cfg / _serve_settings 快照）

    Args:
        name: 渠道名（feishu/weixin/qq）

    Returns:
        ChannelRunner 实例，或 None（配置缺失/未启用/描述符不存在）
    """
    from illusion.channels import ChannelRunner
    from illusion.channels.base import Channel
    from illusion.channels.registry import ChannelRegistry
    from illusion.config.paths import get_channels_data_dir

    if _serve_cfg is None or _serve_settings is None:
        return None
    desc = ChannelRegistry.get(name)
    if desc is None:
        return None
    channel_cfg = getattr(_serve_cfg, desc.config_attr, None)
    if channel_cfg is None or not channel_cfg.enabled:
        return None
    # 创建渠道适配器实例
    channel: Channel = desc.adapter_class(channel_cfg, _serve_settings)
    # 确保渠道会话目录存在
    channel_data_dir = get_channels_data_dir() / desc.name / "sessions"
    channel_data_dir.mkdir(parents=True, exist_ok=True)
    # 群组会话隔离：微信只私聊固定 False，其他渠道从配置读取
    group_sessions_per_user = getattr(channel_cfg, "group_sessions_per_user", False)
    runner = ChannelRunner(
        channel=channel,
        settings=_serve_settings,
        session_data_dir=channel_data_dir,
        group_sessions_per_user=group_sessions_per_user,
    )
    return runner


async def _start_channel_internal(name: str) -> bool:
    """启动指定渠道 runner（动态，供 IPC start_channel 调度）

    若该渠道已在运行则跳过。创建 runner 并用 _supervise 看门狗监督，
    存入 _runner_registry。

    每次启动前重新加载 channels.json，确保 _serve_cfg 为最新配置
    （前端可能刚修改 enabled 字段，守护进程旧快照会误判"未启用"）。

    Args:
        name: 渠道名

    Returns:
        bool: 启动成功返回 True，已在运行或配置缺失返回 False
    """
    if name in _runner_registry:
        return False  # 已在运行
    # 重新加载 channels.json 确保配置最新
    global _serve_cfg
    try:
        _serve_cfg = load_channels_config()
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("启动渠道 %s 前重新加载 channels.json 失败: %s", name, exc)
    runner = _create_runner(name)
    if runner is None:
        logger.warning("启动渠道 %s 失败：配置缺失或未启用", name)
        return False
    stop_event = asyncio.Event()
    task = asyncio.create_task(_supervise(runner, stop_event), name=f"channel-{name}")
    _runner_registry[name] = {"runner": runner, "task": task, "stop_event": stop_event}
    logger.info("渠道 %s 已启动", name)
    return True


async def _stop_channel_internal(name: str) -> bool:
    """停止指定渠道 runner（动态，供 IPC stop_channel 调度）

    设置 stop_event、取消 task、关闭 runner，从注册表移除。

    Args:
        name: 渠道名

    Returns:
        bool: 停止成功返回 True，渠道未运行返回 False
    """
    entry = _runner_registry.pop(name, None)
    if entry is None:
        return False  # 未运行
    stop_event: asyncio.Event = entry["stop_event"]
    task: asyncio.Task[None] = entry["task"]
    runner = entry["runner"]
    stop_event.set()
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (TimeoutError, asyncio.CancelledError):
        pass
    try:
        await asyncio.wait_for(runner.shutdown(), timeout=5.0)
    except (TimeoutError, OSError, RuntimeError, AttributeError, TypeError, ValueError) as exc:
        logger.warning("关闭渠道 %s 异常: %s", name, exc)
    logger.info("渠道 %s 已停止", name)
    return True


async def _supervise(runner: Any, stop_event: asyncio.Event) -> None:
    """看门狗：监督单个渠道 runner，异常退出后带退避自动重启

    渠道 task（runner.run）可能因微信长轮询连续失败、飞书 SDK 抛错、
    QQ WS 断开等异常退出。本协程捕获异常后重新调 runner.run() 重建
    连接（run 内部会 channel.connect()），而非让渠道静默死掉。

    退避在持续失败时递增（5s/10s/30s 封顶），避免疯狂重连；
    成功运行一轮后（run 正常返回）重置退避。stop_event 触发后停止。

    每轮 runner.run() 期间同步启动 _EventWatchdog 监控事件超时。
    看门狗判定僵死后会调用 channel.shutdown() 打断 listen()，
    使 runner.run() 正常返回，从而触发本协程重启 runner。

    Args:
        runner: ChannelRunner 实例
        stop_event: 停止事件
    """
    backoff_idx = 0
    while not stop_event.is_set():
        # 每轮启动事件看门狗，runner.run() 返回或异常时取消
        watchdog = _EventWatchdog(runner, stop_event)
        watchdog_task = asyncio.create_task(watchdog.run())
        try:
            await runner.run()
            # run 正常返回（不应发生，run 是无限循环）——重置退避
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("渠道 runner 异常退出，将重启: %s", exc, exc_info=exc)
            delay = SUPERVISOR_BACKOFF_SECONDS[
                min(backoff_idx, len(SUPERVISOR_BACKOFF_SECONDS) - 1)
            ]
            backoff_idx = min(backoff_idx + 1, len(SUPERVISOR_BACKOFF_SECONDS) - 1)
            # 退避期间检查 stop_event，避免关闭时还要等满退避
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                return  # stop_event 触发
            except TimeoutError:
                continue  # 退避结束，重启 runner
        else:
            backoff_idx = 0
        finally:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                logger.debug("watchdog task 被取消")
            except Exception:
                logger.debug("watchdog task 退出时出现异常", exc_info=True)


class _EventWatchdog:
    """事件超时看门狗

    监控渠道最后事件时间，超时且 health_probe 失败时
    调用 channel.shutdown() 打断 listen()，使 runner.run() 返回，
    从而让 _supervise 重启 runner 重建连接。
    """

    def __init__(self, runner: Any, stop_event: asyncio.Event, timeout: float = 300.0) -> None:
        self._runner = runner
        self._stop_event = stop_event
        self._timeout = timeout
        self._last_event_time = time.monotonic()

    def on_event(self) -> None:
        """收到渠道事件时调用，重置计时器"""
        self._last_event_time = time.monotonic()

    async def run(self) -> None:
        """看门狗主循环，每 30s 检查一次"""
        channel = self._runner.channel
        # 设置回调，让 runner 在收到消息时更新计时器
        channel._event_watchdog = self

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                return  # stop_event 触发
            except TimeoutError:
                pass

            # 检查事件超时
            elapsed = time.monotonic() - self._last_event_time
            if elapsed < self._timeout:
                continue  # 未超时

            # 超时，检查渠道健康
            try:
                healthy = await channel.health_probe()
            except (AttributeError, TypeError, RuntimeError, OSError, ValueError):
                healthy = False

            if not healthy:
                logger.warning(
                    "渠道 %s 事件超时（%ds）且 health_probe 失败，判定僵死，触发重启",
                    channel.name, int(elapsed),
                )
                # 调用 channel.shutdown() 打断 listen()，使 runner.run() 返回
                # _supervise 检测到 run 返回后会重启 runner 重建连接
                try:
                    await channel.shutdown()
                except (OSError, RuntimeError, AttributeError, TypeError) as exc:
                    logger.warning("看门狗调用 channel.shutdown() 失败: %s", exc)
                return  # 退出看门狗
            else:
                # health_probe 成功，可能只是无消息，重置计时器
                self._last_event_time = time.monotonic()


async def _serve_async(cfg: ChannelsConfig, settings: Any, server: Any) -> None:
    """异步 serve 所有启用渠道（IPC 版）

    Args:
        cfg: 渠道配置
        settings: 主设置
        server: DaemonServer 实例
    """
    from illusion.channels.registry import ChannelRegistry
    from illusion.config.i18n import t

    global _serve_cfg, _serve_settings
    _serve_cfg = cfg
    _serve_settings = settings

    # 启动所有 enabled 渠道（通过 _start_channel_internal 创建 runner 并注册到 _runner_registry）
    for desc in ChannelRegistry.all_descriptors():
        channel_cfg = getattr(cfg, desc.config_attr, None)
        if channel_cfg is None or not channel_cfg.enabled:
            continue
        if settings is None:
            continue
        # 启动文案：从 descriptor 读取 i18n key 和是否需要 {channel} 参数
        if desc.start_msg_needs_channel_name:
            print(t(desc.start_msg_key, channel=desc.name))
        else:
            print(t(desc.start_msg_key))
        await _start_channel_internal(desc.name)

    if not _runner_registry:
        print(t("channel_none_configured"))
        return

    # 信号处理：Unix 下注册信号，Windows 下依赖 KeyboardInterrupt
    stop_event = asyncio.Event()

    def _on_signal(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    except (NotImplementedError, RuntimeError, AttributeError):
        # Windows 不支持 add_signal_handler，Ctrl+C 会触发 KeyboardInterrupt
        # 在 asyncio.run 层捕获
        pass

    print(t("channel_press_exit"))

    # 启动连接监控
    async def _monitor() -> None:
        await server.wait_for_no_connections(grace_seconds=3.0)
        stop_event.set()
    monitor_task = asyncio.create_task(_monitor(), name="channel-connection-monitor")

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass

    monitor_task.cancel()

    # 优雅关闭：停止所有运行中的 runner
    # _stop_channel_internal 内部会 set stop_event + cancel task + runner.shutdown()
    for name in list(_runner_registry.keys()):
        await _stop_channel_internal(name)

    # 关键：_serve_async 正常返回后，asyncio.run 会调用
    # loop.shutdown_default_executor() 等待所有 executor 线程完成。
    # 但飞书 WS 客户端通过 run_in_executor 在默认线程池中阻塞运行，
    # 不会响应 cancel → shutdown_default_executor 挂起 → asyncio.run 永不返回。
    # 因此必须在协程内部强制退出，跳过 executor 清理。
    # 强制退出，跳过 asyncio.run 的 executor 清理（会挂起）
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            logger.debug("flush logging handler 失败", exc_info=True)
    os._exit(0)
