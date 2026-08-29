"""Browser Use Broker（node_repl 内核 ↔ Python 宿主）
====================================================

面向 node_repl MCP 服务器内核的命令代理端点。协议与 browser-use 插件的
nodeReplBroker 消息形状一致，传输层采用 127.0.0.1 回环 TCP + 令牌鉴权
（JSON-lines 分帧；跨平台一致性优于 Unix socket / named pipe）。

请求（内核 → 宿主）::

    {"id": "<uuid>", "token": "<32+ chars>", "runtimeScope": "main",
     "sessionId": "<id>", "op": "list"}
    {"id": "<uuid>", "token": "...", "runtimeScope": "main",
     "op": "execute", "browserId": "cdp", "browserGeneration": 3,
     "command": {"method": "navigate", "url": "..."}}

响应（宿主 → 内核）::

    {"id": "<uuid>", "ok": true, "browsers": [descriptor...]}
    {"id": "<uuid>", "ok": true, "result": {command envelope...}}
    {"id": "<uuid>", "ok": false, "error": "<message>"}

安全边界：
    - 仅绑定回环地址（127.0.0.1），进程外部无法路由到该端口
    - 每条请求必须携带 32+ 字符随机令牌（启动时由 secrets 生成，经环境变量
      只下发给本会话派生的 MCP 服务器子进程）
    - subagent 作用域直接拒绝（browser use 仅主代理可用，与插件一致）
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# 单帧上限（与插件 MAX_RESPONSE_BYTES 一致：截图等 base64 载荷可能很大）
MAX_FRAME_BYTES = 32 * 1024 * 1024
# 令牌最小长度（协议：token.min(32)）
MIN_TOKEN_LENGTH = 32

ListHandler = Callable[[], Awaitable[list[dict[str, Any]]]]
ExecuteHandler = Callable[[str, int, dict[str, Any]], Awaitable[dict[str, Any]]]


class BrowserBroker:
    """回环 TCP broker：接受内核连接，鉴权后转发 list/execute 到宿主处理器。

    Usage:
        broker = BrowserBroker(token, list_handler, execute_handler)
        host, port = await broker.start()
        ...
        await broker.stop()
    """

    def __init__(
        self,
        token: str,
        list_handler: ListHandler,
        execute_handler: ExecuteHandler,
    ) -> None:
        if len(token) < MIN_TOKEN_LENGTH:
            raise ValueError("broker token 必须 >= 32 字符")
        self._token = token
        self._list_handler = list_handler
        self._execute_handler = execute_handler
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.StreamWriter] = set()

    @property
    def endpoint(self) -> tuple[str, int] | None:
        """当前监听端点（未启动时 None）。"""
        if self._server is None or self._server.sockets is None:
            return None
        socket = self._server.sockets[0]
        sockname = socket.getsockname()
        return (str(sockname[0]), int(sockname[1]))

    async def start(self, host: str = "127.0.0.1") -> tuple[str, int]:
        """启动监听（端口 0 = 由内核分配临时端口）。"""
        self._server = await asyncio.start_server(
            self._handle_connection,
            host,
            0,
            limit=MAX_FRAME_BYTES,
        )
        endpoint = self.endpoint
        assert endpoint is not None
        logger.info("browser_use broker 监听 %s:%s", endpoint[0], endpoint[1])
        return endpoint

    async def stop(self) -> None:
        """停止监听并断开全部连接。幂等。"""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                logger.debug("broker 关闭异常", exc_info=True)
        for writer in list(self._connections):
            try:
                writer.close()
            except Exception as exc:
                logger.debug("broker 连接关闭失败", exc_info=exc)
        self._connections.clear()

    # --- 连接处理 ---

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """单连接循环：逐帧读取请求 → 鉴权 → 分发 → 回写响应。"""
        peer = writer.get_extra_info("peername")
        self._connections.add(writer)
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                if len(line) > MAX_FRAME_BYTES:
                    await self._write(writer, {"id": "", "ok": False, "error": "frame too large"})
                    break
                response = await self._process_line(line)
                if response is not None:
                    await self._write(writer, response)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.exception("broker 连接处理异常 peer=%s", peer)
        finally:
            self._connections.discard(writer)
            try:
                writer.close()
            except Exception as exc:
                logger.debug("broker 连接回收失败", exc_info=exc)

    async def _process_line(self, line: bytes) -> dict[str, Any] | None:
        """解析并处理一条请求；格式非法时返回 None（静默丢弃，防注入探测）。"""
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(request, dict):
            return None
        request_id = str(request.get("id", ""))
        # 鉴权：令牌不一致按未授权拒绝（不区分错误细节，避免探测面）
        if not _constant_time_eq(str(request.get("token", "")), self._token):
            return {"id": request_id, "ok": False, "error": "unauthorized"}
        if request.get("runtimeScope") != "main":
            return {"id": request_id, "ok": False, "error": "Browser is not available in subagent"}
        op = request.get("op")
        try:
            if op == "list":
                browsers = await self._list_handler()
                return {"id": request_id, "ok": True, "browsers": browsers}
            if op == "execute":
                result = await self._execute_handler(
                    str(request.get("browserId", "")),
                    int(request.get("browserGeneration", 0)),
                    dict(request.get("command") or {}),
                )
                return {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            logger.exception("broker op=%s 执行失败", op)
            return {"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"id": request_id, "ok": False, "error": f"unknown op: {op}"}

    async def _write(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()


def _constant_time_eq(left: str, right: str) -> bool:
    """常数时间字符串比较（防时序侧信道）。"""
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
