"""
LSP 客户端
==========

使用 JSON-RPC 双向通信模式。
使用 subprocess.Popen[Any]（同步）+ 线程读写，避免 Windows 上 asyncio 子进程的问题。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess as sp
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


def _uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        path_str = unquote(parsed.path)
        if len(path_str) >= 2 and path_str[0] == "/" and path_str[2] == ":":
            path_str = path_str[1:]
        return path_str
    return unquote(uri)


def _encode(msg: dict[str, Any]) -> bytes:
    payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload


class LspClient:
    """LSP 客户端，使用 subprocess.Popen[Any] + 线程读写。"""

    def __init__(self) -> None:
        self._proc: sp.Popen[Any] | None = None
        self._read_thread: threading.Thread | None = None
        self._write_thread: threading.Thread | None = None
        self._write_q: Any = None  # queue.Queue[bytes | None]
        self._pending: dict[int, asyncio.Future[Any]] = {}
        # 保护 _pending 字典：reader 线程遍历 / 主线程增删 / 超时 pop 三方并发
        self._pending_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._notify_handlers: dict[str, list[Any]] = {}
        self._request_handlers: dict[str, Any] = {}
        self._buf = b""
        self._next_id = 1
        self._connected = False
        self.capabilities: dict[str, Any] | None = None
        self.is_initialized = False

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def start(self, command: str, args: list[str], options: dict[str, Any] | None = None) -> None:
        """异步启动 LSP 服务器。

        ``sp.Popen`` 在 Windows 上可能阻塞数百毫秒（创建控制台、加载 .cmd 包装器），
        因此通过 ``asyncio.to_thread`` 让出事件循环。
        """
        if self._proc is not None:
            return

        import queue
        self._write_q = queue.Queue()
        self._loop = asyncio.get_running_loop()

        kw: dict[str, Any] = {
            "stdin": sp.PIPE, "stdout": sp.PIPE, "stderr": sp.PIPE,
        }
        if options and options.get("env"):
            kw["env"] = options["env"]
        if options and options.get("cwd"):
            kw["cwd"] = options["cwd"]
        if sys.platform == "win32":
            si = sp.STARTUPINFO()
            si.dwFlags |= sp.STARTF_USESHOWWINDOW
            si.wShowWindow = sp.SW_HIDE
            kw["startupinfo"] = si
            kw["creationflags"] = sp.CREATE_NO_WINDOW

        if sys.platform == "win32":
            # Windows: shell=True 让 cmd.exe 解析 .cmd 包装器
            kw["shell"] = True
            cmd_str = command + " " + " ".join(args) if args else command

            def _spawn_win(c: str, **k: Any) -> sp.Popen[str]:
                return sp.Popen(c, **k)

            self._proc = await asyncio.to_thread(_spawn_win, cmd_str, **kw)
        else:
            self._proc = await asyncio.to_thread(sp.Popen[Any], [command] + args, **kw)  # type: ignore[arg-type]
        self._connected = True

        # 读取线程
        self._read_thread = threading.Thread(target=self._reader, daemon=True)
        self._read_thread.start()

        # 写入线程
        self._write_thread = threading.Thread(target=self._writer, daemon=True)
        self._write_thread.start()

        # stderr 线程
        threading.Thread(target=self._stderr_drain, daemon=True).start()

    async def initialize(
        self, root_uri: str, capabilities: dict[str, Any] | None = None,
        root_path: str | None = None, process_id: int | None = None,
        initialization_options: Any = None, workspace_folders: list[Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "processId": process_id or os.getpid(),
            "rootUri": root_uri,
            "capabilities": capabilities or {},
        }
        if root_path:
            params["rootPath"] = root_path
        elif root_uri.startswith("file://"):
            params["rootPath"] = _uri_to_path(root_uri)
        if initialization_options is not None:
            params["initializationOptions"] = initialization_options
        if workspace_folders:
            params["workspaceFolders"] = workspace_folders
        else:
            params["workspaceFolders"] = [{
                "uri": root_uri,
                "name": Path(params.get("rootPath", "")).name or "workspace",
            }]

        result = await self.request("initialize", params, timeout=45)
        self.capabilities = result.get("capabilities", {})
        self.is_initialized = True
        await self.notify("initialized", {})
        return result  # type: ignore[no-any-return]

    async def request(self, method: str, params: Any = None, timeout: float = 30, **kw: Any) -> Any:
        if not self._connected:
            raise RuntimeError("LSP client not connected")
        if self._proc and self._proc.poll() is not None:
            raise RuntimeError(f"LSP process exited with code {self._proc.returncode}")
        if params is None:
            params = kw or {}

        msg_id = self._next_id
        self._next_id += 1
        assert self._loop is not None
        fut: asyncio.Future[Any] = self._loop.create_future()
        with self._pending_lock:
            self._pending[msg_id] = fut

        self._write_q.put(_encode({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}))

        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            # 超时处理：用锁保护 pop，并 cancel Future（而非仅 pop），
            # 否则 reader 线程后续 set_result 会触发 InvalidStateError
            with self._pending_lock:
                popped = self._pending.pop(msg_id, None)
            if popped is not None and not popped.done():
                popped.cancel()
            raise TimeoutError(f"LSP '{method}' timed out after {timeout}s")
        except asyncio.CancelledError:
            # 取消时清理 pending Future，防止 reader 线程后续 set_result 触发 InvalidStateError
            with self._pending_lock:
                popped = self._pending.pop(msg_id, None)
            if popped is not None and not popped.done():
                popped.cancel()
            raise

        if "error" in resp:
            raise RuntimeError(f"LSP error: {resp['error'].get('message', resp['error'])}")
        return resp.get("result")

    async def notify(self, method: str, params: Any = None, **kw: Any) -> None:
        if not self._connected:
            raise RuntimeError("LSP client not connected")
        if params is None:
            params = kw or {}
        self._write_q.put(_encode({"jsonrpc": "2.0", "method": method, "params": params}))

    def on_notification(self, method: str, handler: Any) -> None:
        self._notify_handlers.setdefault(method, []).append(handler)

    def on_request(self, method: str, handler: Any) -> None:
        self._request_handlers[method] = handler

    async def stop(self) -> None:
        """异步停止 LSP 服务器。

        ``proc.wait(timeout=5)`` 阻塞 5 秒，必须通过 ``asyncio.to_thread`` 让出事件循环。
        同时清理 ``_pending`` Future，避免 reader 线程后续 ``set_result`` 触发 ``InvalidStateError``。
        """
        self._connected = False
        if self._write_q:
            self._write_q.put(None)  # 停止写入线程

        # 清理 _pending Future：用锁保护，避免与 reader 线程并发访问
        with self._pending_lock:
            snapshot = list(self._pending.items())
            for _req_id, fut in snapshot:
                if not fut.done():
                    fut.set_exception(ConnectionError("LSP 服务器关闭"))
            self._pending.clear()

        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.to_thread(self._proc.wait, timeout=5)
            except (sp.TimeoutExpired, OSError) as exc:
                logger.debug("LSP graceful shutdown failed, force killing: %s", exc)
                self._proc.kill()
            self._proc = None

        # join daemon 读取线程：proc.wait 返回后 stdout 已 EOF，reader 应立即退出
        if self._read_thread is not None:
            self._read_thread.join(timeout=2)
            self._read_thread = None
        self.is_initialized = False

    # --- 线程实现 ---

    def _writer(self) -> None:
        """专用写入线程。"""
        try:
            while True:
                data = self._write_q.get()
                if data is None:
                    break
                try:
                    assert self._proc is not None and self._proc.stdin is not None
                    self._proc.stdin.write(data)
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    self._connected = False
                    break
        except (OSError, RuntimeError) as exc:
            logger.debug("LSP writer thread exited unexpectedly: %s", exc)
            self._connected = False

    def _reader(self) -> None:
        """专用读取线程。Windows 上 read(n>1) 会阻塞直到读满 n 字节，必须用 read(1)。"""
        try:
            while self._connected:
                assert self._proc is not None and self._proc.stdout is not None
                chunk = self._proc.stdout.read(1)
                if not chunk:
                    break
                self._buf += chunk
                while self._buf:
                    idx = self._buf.find(b"Content-Length: ")
                    if idx != 0:
                        break
                    hdr_end = self._buf.find(b"\r\n\r\n")
                    if hdr_end == -1:
                        break
                    try:
                        cl = int(self._buf[15:hdr_end])
                    except ValueError:
                        break
                    body_start = hdr_end + 4
                    if len(self._buf) < body_start + cl:
                        break
                    body = self._buf[body_start:body_start + cl]
                    self._buf = self._buf[body_start + cl:]
                    try:
                        msg = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch(msg)
        except (OSError, RuntimeError) as exc:
            logger.debug("LSP reader thread exited unexpectedly: %s", exc)
        finally:
            self._connected = False
            if self._loop:
                # 用锁保护：snapshot 后再迭代，避免与主线程并发修改触发 RuntimeError
                with self._pending_lock:
                    snapshot = list(self._pending.values())
                    self._pending.clear()
                for f in snapshot:
                    if not f.done():
                        self._loop.call_soon_threadsafe(f.set_exception, RuntimeError("LSP connection lost"))

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg and "method" not in msg:
            # 响应 — 通知 asyncio 事件循环
            with self._pending_lock:
                fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                assert self._loop is not None
                self._loop.call_soon_threadsafe(fut.set_result, msg)
        elif "id" in msg and "method" in msg:
            # 服务器->客户端请求 — 同步处理，通过写入队列回复
            handler = self._request_handlers.get(msg["method"])
            if handler:
                try:
                    result = handler(msg.get("params", {}))
                except (TypeError, KeyError, AttributeError) as exc:
                    # 处理器访问 params 字段失败时回退到 None，避免拖垮读取线程
                    logger.debug("LSP request handler %s failed: %s", msg["method"], exc)
                    result = None
            else:
                result = None
            resp = _encode({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            self._write_q.put(resp)
        elif "method" in msg:
            # 通知
            for h in self._notify_handlers.get(msg["method"], []):
                try:
                    h(msg.get("params"))
                except (TypeError, KeyError, AttributeError) as exc:
                    logger.debug("LSP notification handler %s failed: %s", msg["method"], exc)

    def _stderr_drain(self) -> None:
        try:
            while True:
                assert self._proc is not None and self._proc.stderr is not None
                line = self._proc.stderr.readline()
                if not line:
                    break
        except OSError as exc:
            logger.debug("LSP stderr drain thread exited: %s", exc)
