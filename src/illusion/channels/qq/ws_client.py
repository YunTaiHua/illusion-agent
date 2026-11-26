"""QQ Bot WebSocket 网关客户端
==============================

实现 QQ Bot API v2 的 WebSocket 协议：连接、鉴权、心跳、重连、事件分发。

协议要点：
    - op 10 (Hello): 服务端发送心跳间隔
    - op 2 (Identify): 客户端鉴权（首次连接）
    - op 6 (Resume): 客户端恢复会话（断线重连）
    - op 0 (Dispatch): 业务事件分发
    - op 1 (Heartbeat): 客户端定期心跳
    - op 7 (Server Reconnect): 服务端要求重连
    - op 9 (Invalid Session): 会话无效
    - op 11 (Heartbeat ACK): 心跳确认

参考：https://bot.q.qq.com/wiki/develop/api-v2/
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import aiohttp

from illusion.channels.qq.api import (
    RATE_LIMIT_DELAY,
    RECONNECT_BACKOFF,
    ensure_token,
    get_gateway_url,
)

logger = logging.getLogger(__name__)

# ── 意图位 ────────────────────────────────────────────────────

INTENT_DIRECT_MESSAGES = 1 << 12  # C2C 私聊
INTENT_GROUP_MESSAGES = 1 << 25   # 群聊 @消息

# ── 关闭码 ────────────────────────────────────────────────────

FATAL_CLOSE_CODES = {4001, 4002, 4010, 4011, 4012, 4013, 4014, 4914, 4915}
RECONNECT_CLOSE_CODES = {4006, 4007, 4009}
RATE_LIMIT_CODE = 4008
INVALID_TOKEN_CODE = 4004

QUICK_DISCONNECT_THRESHOLD = 5.0
MAX_QUICK_DISCONNECTS = 3


class QQCloseError(Exception):
    """WebSocket 关闭异常，携带关闭码和原因"""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class QQWSClient:
    """QQ Bot WebSocket 网关客户端

    管理与 QQ Bot 网关的 WebSocket 连接，处理协议层逻辑，
    将业务事件通过回调传递给上层（QQChannel adapter）。

    Attributes:
        app_id: 应用 ID
        client_secret: 应用密钥
    """

    def __init__(
        self,
        app_id: str,
        client_secret: str,
        on_event: EventCallback,
    ) -> None:
        """初始化 WS 客户端

        Args:
            app_id: QQ 应用 ID
            client_secret: QQ 应用密钥
            on_event: 事件回调，接收 (event_type, data)
        """
        self.app_id = app_id
        self.client_secret = client_secret
        self._on_event = on_event

        # 连接状态
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False

        # 协议状态
        self._heartbeat_interval: float = 30.0
        self._last_seq: int | None = None
        self._session_id: str = ""
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._listen_task: asyncio.Task[None] | None = None
        # fire-and-forget task 强引用集合，防止 GC 抢收
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    @property
    def is_connected(self) -> bool:
        """WS 是否处于连接状态"""
        return self._running and self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """建立连接：获取 token → 获取网关 → 打开 WS → 启动监听

        监听和心跳任务仅在首次连接时创建一次（长期运行），
        断线重连由 _listen_loop 内部循环处理，避免重复创建 task
        导致多个 listen_loop 并发 receive（concurrent receive）。
        """
        self._session = aiohttp.ClientSession(trust_env=True)
        self._running = True

        try:
            await self._open_ws()
        except Exception:
            self._running = False
            await self._cleanup()
            raise

        # 仅首次连接创建一次监听/心跳任务，重连时不重建
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _open_ws(self) -> None:
        """获取网关地址并打开 WebSocket（仅建立连接，不创建监听任务）

        供 connect() 和 _reconnect() 复用。重连时 _listen_loop 已在运行，
        只需替换 self._ws 即可，绝不能再创建新的 listen/heartbeat task。
        """
        assert self._session is not None
        token = await ensure_token(self._session, self.app_id, self.client_secret)
        gateway_url = await get_gateway_url(self._session, token)

        self._ws = await self._session.ws_connect(gateway_url)
        logger.info("QQ WS 已连接: %s", gateway_url)

    async def close(self) -> None:
        """关闭连接

        取消 task 后 await 等待 task 退出（带 timeout），避免旧 task
        在 _supervise 重启时仍在运行。与 Feishu adapter 的 await ws_future
        保持一致的"等待线程退出"语义。
        """
        self._running = False
        # 取消 task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._listen_task:
            self._listen_task.cancel()
        # 等待 task 退出（带 timeout，避免卡死）
        for task in (self._listen_task, self._heartbeat_task):
            if task is None:
                continue
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                logger.debug("等待 QQ task 退出时超时或被取消")
            except Exception:
                logger.debug("等待 QQ task 退出时出现异常", exc_info=True)
        self._listen_task = None
        self._heartbeat_task = None
        await self._cleanup()

    async def _cleanup(self) -> None:
        """清理资源"""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None

    # ── 心跳 ──────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """定期发送心跳（op 1）

        循环条件只检查 _running，不检查 is_connected——重连时 ws 会短暂
        为 None/closed，心跳应跳过该轮而非永久退出，否则重连后无心跳
        导致 QQ 服务端因超时断开，形成 connect→短暂工作→断连→重连 振荡。
        """
        try:
            while self._running:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._running:
                    break
                if not self.is_connected:
                    continue  # ws 重建中，跳过本轮心跳
                payload = {"op": 1, "d": self._last_seq}
                await self._send_json(payload)
                logger.debug("QQ 心跳已发送, seq=%s", self._last_seq)
        except asyncio.CancelledError:
            return
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as exc:
            logger.warning("QQ 心跳异常: %s", exc)

    # ── 监听 ──────────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """读取 WS 帧并分发，断线后自动重连

        - 永远循环，不退出（除非 CancelledError 或 _running=False）
        - 快速断连次数过多：sleep 长时间后重置计数器继续循环（不放弃）
        - 致命错误码：sleep 长时间后继续循环（网络恢复后可能就好了）
        - 重连失败达上限：sleep 长时间后重置 backoff 继续
        - _reconnect 返回 bool：成功重置 backoff，失败递增

        关键：不依赖外部 _supervise 重启，_listen_loop 自己永远活着。
        若 raise 异常让 _supervise 重启 runner，会导致 session 泄漏
        且重启后 _listen_task 从头开始（丢失退避状态）。
        """
        backoff_idx = 0
        connect_time = 0.0
        quick_disconnects = 0
        # 长退避：快速断连/重连失败达上限时用，避免疯狂重连
        LONG_BACKOFF_SECONDS = 60

        while self._running:
            try:
                connect_time = time.monotonic()
                await self._read_events()
                backoff_idx = 0
                quick_disconnects = 0
            except asyncio.CancelledError:
                return
            except QQCloseError as exc:
                if not self._running:
                    return

                # 快速断连检测
                duration = time.monotonic() - connect_time
                if duration < QUICK_DISCONNECT_THRESHOLD and connect_time > 0:
                    quick_disconnects += 1
                    if quick_disconnects >= MAX_QUICK_DISCONNECTS:
                        # 不退出，sleep 长时间后重置继续
                        logger.warning(
                            "QQ 快速断连 %d 次，等待 %ds 后重置计数继续重连",
                            quick_disconnects, LONG_BACKOFF_SECONDS,
                        )
                        await asyncio.sleep(LONG_BACKOFF_SECONDS)
                        quick_disconnects = 0
                        backoff_idx = 0
                        continue
                else:
                    quick_disconnects = 0

                # 致命错误码：sleep 长时间后继续（不放弃，网络恢复后可能就好了）
                # 当前没有独立的不可恢复错误通知机制。直接退出会让 _listen_task 静默退出，
                # _EventWatchdog 300s 后才触发 supervisor 重启，重启后又命中相同
                # fatal code，造成周期性重启。当前 sleep+retry 是折中方案，长期可引入
                # fatal error 通知机制（如 adapter 标记 disabled + 通知用户）。
                if exc.code in FATAL_CLOSE_CODES:
                    logger.error(
                        "QQ WS 致命错误 code=%s，等待 %ds 后继续重连",
                        exc.code, LONG_BACKOFF_SECONDS,
                    )
                    await asyncio.sleep(LONG_BACKOFF_SECONDS)
                    continue

                # Token 无效
                if exc.code == INVALID_TOKEN_CODE:
                    logger.warning("QQ token 无效，刷新后重连")
                    from illusion.channels.qq.api import _token_cache
                    _token_cache.pop(self.app_id, None)

                # 限流
                if exc.code == RATE_LIMIT_CODE:
                    logger.warning("QQ WS 限流，等待 %ds", RATE_LIMIT_DELAY)
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # 会话无效
                if exc.code in RECONNECT_CLOSE_CODES:
                    self._session_id = ""
                    self._last_seq = None

                # 重连（返回 bool：成功重置 backoff，失败递增）
                if await self._reconnect(backoff_idx):
                    backoff_idx = 0
                    quick_disconnects = 0
                else:
                    backoff_idx = min(backoff_idx + 1, len(RECONNECT_BACKOFF) - 1)
                    # 重连失败达上限，sleep 长时间后重置退避继续
                    if backoff_idx >= len(RECONNECT_BACKOFF) - 1:
                        logger.warning(
                            "QQ WS 重连失败达上限，等待 %ds 后重置退避继续",
                            LONG_BACKOFF_SECONDS,
                        )
                        await asyncio.sleep(LONG_BACKOFF_SECONDS)
                        backoff_idx = 0

            except (aiohttp.ClientError, json.JSONDecodeError, RuntimeError, OSError) as exc:
                if not self._running:
                    return
                logger.warning("QQ WS 监听异常: %s", exc)
                if await self._reconnect(backoff_idx):
                    backoff_idx = 0
                else:
                    backoff_idx = min(backoff_idx + 1, len(RECONNECT_BACKOFF) - 1)

    async def _reconnect(self, backoff_idx: int) -> bool:
        """指数退避重连（仅替换 ws，不重建监听任务）

        成功返回 True（重置 backoff），失败返回 False（递增 backoff）。

        关键：重连在 _listen_loop 内部调用，listen_loop 自己会接着循环
        调 _read_events。这里只负责关旧 ws、建新 ws，绝不能创建新的
        listen/heartbeat task（否则多个 listen_loop 并发 receive）。

        Args:
            backoff_idx: 当前退避索引

        Returns:
            bool: 重连成功返回 True，失败返回 False
        """
        if not self._running:
            return False

        delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
        logger.info("QQ WS 将在 %ds 后重连 (attempt %d)", delay, backoff_idx + 1)
        await asyncio.sleep(delay)

        try:
            # 仅关闭旧 ws（不关 session，复用以建新连接）
            if self._ws and not self._ws.closed:
                await self._ws.close()
            self._ws = None
            # session 失效时重建
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(trust_env=True)
            await self._open_ws()
            return True
        except (aiohttp.ClientError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            logger.warning("QQ WS 重连失败: %s", exc)
            return False

    async def _read_events(self) -> None:
        """读取 WS 帧并分发

        用带超时的 receive() 替代无超时的 `async for msg in self._ws`。
        原因：aiohttp WS 在连接半开/对端静默时，__anext__ 协程会永久阻塞
        在 IO 等待上，无法探活也无法触发重连，导致 daemon 表现为僵死。
        加超时后，读取能定期挣脱阻塞，发现连接异常即抛 QQCloseError
        触发 _listen_loop 重连，避免 daemon 僵死。

        循环条件检查 self._ws.closed：重连后旧 ws 已关闭，循环自然退出，
        避免对已关闭的旧 ws 继续调用 receive。

        注意：self._ws 为 None 时必须抛 QQCloseError 而非直接 return。
        若直接 return，_listen_loop 会认为读取正常完成 → 重置 backoff_idx →
        立即再次调用 _read_events → 又 return → 形成不 yield 的忙循环，
        饿死整个事件循环（IPC 不响应、其他渠道停摆）。
        """
        if not self._ws:
            raise QQCloseError(0, "WS 连接为空（上次重连失败）")
        # 连续探活失败计数：超过阈值认定连接已死，强制重连
        idle_probes = 0
        max_idle_probes = 3  # 3 次 ping 无响应（约 90s）判定连接已死
        while self._running and self._ws and not self._ws.closed:
            try:
                # receive 带 30s 超时：正常有消息时立即返回，无消息时
                # 每 30s 超时一次去发 ping 探活，不会永久阻塞
                msg = await self._ws.receive(timeout=30)
            except TimeoutError:
                # 超时未必是异常（QQ 可能长时间无消息），发 ping 探活
                if not self._ws or self._ws.closed:
                    raise QQCloseError(0, "连接已关闭")
                try:
                    await self._ws.ping()
                except (aiohttp.ClientError, ConnectionError, RuntimeError, OSError) as exc:
                    raise QQCloseError(0, f"ping 探活失败: {exc}") from exc
                idle_probes += 1
                if idle_probes >= max_idle_probes:
                    raise QQCloseError(0, f"连续 {idle_probes} 次探活无消息，疑似连接僵死")
                continue
            if msg.type == aiohttp.WSMsgType.TEXT:
                idle_probes = 0  # 收到正常消息，重置探活计数
                payload = json.loads(msg.data)
                self._dispatch_payload(payload)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise QQCloseError(0, str(self._ws.exception()))
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSE):
                code = self._ws.close_code or 0
                raise QQCloseError(code, "连接已关闭")

    # ── 事件分发 ──────────────────────────────────────────────

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

    def _dispatch_payload(self, payload: dict[str, Any]) -> None:
        """路由入站 WS 帧"""
        op = payload.get("op")
        s = payload.get("s")
        d = payload.get("d")

        # 更新序列号
        if isinstance(s, int) and (self._last_seq is None or s > self._last_seq):
            self._last_seq = s

        # op 10 = Hello
        if op == 10:
            d_data = d if isinstance(d, dict) else {}
            interval_ms = d_data.get("heartbeat_interval", 30000)
            self._heartbeat_interval = interval_ms / 1000.0 * 0.8
            logger.info("QQ 心跳间隔: %.1fs", self._heartbeat_interval)

            # 首次连接发 Identify，断线重连发 Resume
            if self._session_id:
                self._create_background_task(self._send_resume())
            else:
                self._create_background_task(self._send_identify())
            return

        # op 0 = Dispatch
        if op == 0:
            t = payload.get("t", "")
            if t == "READY":
                d_data = d if isinstance(d, dict) else {}
                self._session_id = d_data.get("session_id", "")
                logger.info("QQ READY, session_id=%s", self._session_id)
                return

            # 业务事件交给 adapter
            if d and isinstance(d, dict):
                self._create_background_task(self._on_event(t, d))  # type: ignore[arg-type]
            return

        # op 7 = Server Reconnect
        if op == 7:
            logger.info("QQ 服务端要求重连")
            if self._ws and not self._ws.closed:
                ws = self._ws
                async def _close_ws() -> None:
                    await ws.close()
                self._create_background_task(_close_ws())
            return

        # op 9 = Invalid Session
        if op == 9:
            logger.warning("QQ 会话无效，清除 session 状态")
            self._session_id = ""
            self._last_seq = None
            return

        # op 11 = Heartbeat ACK（忽略）
        if op == 11:
            return

    # ── 鉴权 ──────────────────────────────────────────────────

    async def _send_identify(self) -> None:
        """发送 op 2 Identify"""
        assert self._session is not None
        token = await ensure_token(self._session, self.app_id, self.client_secret)
        payload = {
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": INTENT_DIRECT_MESSAGES | INTENT_GROUP_MESSAGES,
                "shard": [0, 1],
                "properties": {
                    "$os": "windows",
                    "$browser": "illusion-agent",
                    "$device": "illusion-agent",
                },
            },
        }
        await self._send_json(payload)
        logger.info("QQ Identify 已发送")

    async def _send_resume(self) -> None:
        """发送 op 6 Resume"""
        assert self._session is not None
        token = await ensure_token(self._session, self.app_id, self.client_secret)
        payload = {
            "op": 6,
            "d": {
                "token": f"QQBot {token}",
                "session_id": self._session_id,
                "seq": self._last_seq,
            },
        }
        await self._send_json(payload)
        logger.info("QQ Resume 已发送, session_id=%s", self._session_id)

    # ── 底层发送 ──────────────────────────────────────────────

    async def _send_json(self, data: dict[str, Any]) -> None:
        """发送 JSON 帧"""
        if self._ws and not self._ws.closed:
            await self._ws.send_json(data)
