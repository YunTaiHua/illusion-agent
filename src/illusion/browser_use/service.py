"""BrowserUseService —— Browser Use 子系统编排入口
==================================================

会话级单例，负责：

- broker 生命周期（随服务启动；内核按需连接，浏览器惰性拉起）
- 受管浏览器生命周期（首次 browser 命令时启动；空闲自动回收）
- 命令分发（browserId/generation 校验 → CommandExecutor → 实时画面采样）
- 实时画面（Web 端「用量」页签）：订阅者存在时周期采样活动 tab 的 JPEG
  帧 + tab 列表，内容变化才推送（hash 去重 + 最小间隔节流）
- node_repl MCP 服务器配置构建（stdio 启动命令 + broker 连接环境变量）

终端行为约定：终端不订阅实时画面（静默执行）；画面仅经 Web 后端推送。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from typing import Any

from illusion.browser_use.broker import BrowserBroker
from illusion.browser_use.commands import BrowserCommandExecutor
from illusion.browser_use.config import BrowserSettings
from illusion.browser_use.protocol import BrowserCommandFailure
from illusion.browser_use.runtime_assets import build_mcp_launch_command
from illusion.browser_use.session import ManagedBrowser

logger = logging.getLogger(__name__)

# 后端描述符（协议 browserBackendDescriptorSchema）
_BACKEND_ID = "cdp"
_BACKEND_TYPE = "cdp"
_BACKEND_NAME = "IllusionAgent Managed Chromium"

class BrowserUseService:
    """一个会话（RuntimeBundle）的 Browser Use 运行时。

    Usage:
        service = BrowserUseService(settings)
        await service.start()      # 启动 broker（不拉起浏览器）
        config = service.build_mcp_server_config()
        ...
        await service.stop()
    """

    def __init__(self, settings: BrowserSettings, *, config_dir: str | None = None) -> None:
        self._settings = settings
        self._token = secrets.token_urlsafe(32)
        self._browser = ManagedBrowser(settings)
        self._executor = BrowserCommandExecutor(self._browser)
        self._broker = BrowserBroker(
            self._token,
            self._handle_list,
            self._handle_execute,
        )
        self._config_dir = config_dir
        self._last_view: dict[str, Any] | None = None
        self._last_view_hash: bytes = b""
        self._view_streaming = False
        self._view_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._last_activity = time.monotonic()
        self._started = False

    # --- 生命周期 ---

    async def start(self) -> None:
        """启动 broker 与后台巡检任务（不拉起浏览器——首次使用时惰性启动）。"""
        if self._started:
            return
        await self._broker.start()
        self._view_task = asyncio.create_task(self._view_loop(), name="browser-view-loop")
        self._idle_task = asyncio.create_task(self._idle_loop(), name="browser-idle-loop")
        self._started = True
        logger.info(
            "browser_use 服务已启动 profile=%s headless=%s", self._settings.profile, self._settings.headless
        )

    async def stop(self) -> None:
        """停止服务：回收浏览器、broker 与后台任务。幂等。"""
        if not self._started:
            return
        self._started = False
        for task in (self._view_task, self._idle_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug("browser_use 后台任务停止异常", exc_info=exc)
        self._view_task = None
        self._idle_task = None
        await self._browser.stop()
        await self._broker.stop()
        self._last_view = None
        logger.info("browser_use 服务已停止")

    @property
    def is_started(self) -> bool:
        return self._started

    # --- broker 处理器（协议入口） ---

    async def _handle_list(self) -> list[dict[str, Any]]:
        """list：返回受管 Chromium 描述符（首次调用触发浏览器惰性启动）。"""
        self._touch_activity()
        await self._ensure_browser()
        return [self._descriptor()]

    async def _handle_execute(self, browser_id: str, browser_generation: int, command: dict[str, Any]) -> dict[str, Any]:
        """execute：校验 browserId/generation 后分发命令。"""
        self._touch_activity()
        if browser_id != _BACKEND_ID:
            raise BrowserCommandFailure(
                "backend_unavailable",
                f"Browser backend '{browser_id}' is unavailable; available: {_BACKEND_TYPE}:{_BACKEND_ID}",
            )
        if browser_generation != self._browser.generation:
            raise BrowserCommandFailure(
                "backend_unavailable",
                "Browser runtime binding is stale after kernel reset",
            )
        envelope = await self._executor.execute(command)
        if command.get("method") not in ("getState", "list", "listUserTabs"):
            # 状态可能变化：立即采样一次画面（节流由 _capture_view 保证）
            await self._capture_view()
        return envelope

    def _descriptor(self) -> dict[str, Any]:
        profile_label = "用户档案" if self._settings.profile == "user" else "空白档案"
        return {
            "id": _BACKEND_ID,
            "generation": self._browser.generation,
            "type": _BACKEND_TYPE,
            "name": f"{_BACKEND_NAME} ({profile_label})",
            "capabilities": {"browser": [], "tab": []},
            "metadata": {
                "profile": self._settings.profile,
                "headless": str(self._settings.headless).lower(),
            },
        }

    async def _ensure_browser(self) -> None:
        """惰性启动浏览器（幂等；启动失败折叠为 broker error 响应）。"""
        if not self._browser.is_started:
            await self._browser.start()

    def _touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    # --- 实时画面（Web 端用量页签） ---

    def set_view_streaming(self, enabled: bool) -> None:
        """开关画面采样（Web 后端在用户打开浏览器视图卡片时启用）。

        采样仅在浏览器运行时进行；关闭后 _view_loop 空转（无截图开销）。
        终端后端不启用采样，保证终端场景完全静默。
        """
        self._view_streaming = bool(enabled)

    @property
    def view_streaming(self) -> bool:
        return self._view_streaming

    def current_view(self) -> dict[str, Any] | None:
        """最近一次画面快照（未采样/未启动时 None）。"""
        return self._last_view

    async def _view_loop(self) -> None:
        """画面采样循环：流式开启且浏览器已启动时周期采样，内容变化才更新快照。"""
        interval = max(self._settings.stream_interval_ms, 100) / 1000
        while True:
            await asyncio.sleep(interval)
            try:
                if self._view_streaming and self._browser.is_started:
                    await self._capture_view()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("browser_use 画面采样失败", exc_info=True)

    async def _capture_view(self) -> None:
        """采样活动 tab（JPEG 帧 + 元数据），变化时通知订阅者。"""
        owned = self._browser.agent_tabs()
        tabs_meta = [
            {
                "id": entry.tabId,
                "url": str(getattr(entry.page, "url", "") or ""),
                "title": "",
                "active": entry is owned[-1] if owned else False,
            }
            for entry in owned
        ]
        image_b64 = ""
        url = ""
        title = ""
        if owned:
            entry = owned[-1]
            url = str(getattr(entry.page, "url", "") or "")
            try:
                title = await entry.page.title()
            except Exception as exc:
                logger.debug("画面采样 title 读取失败", exc_info=exc)
                title = ""
            try:
                raw = await entry.page.screenshot(
                    type="jpeg", quality=self._settings.screenshot_quality
                )
                image_b64 = base64.b64encode(bytes(raw)).decode("ascii")
            except Exception as exc:
                logger.debug("画面采样截图失败", exc_info=exc)
                image_b64 = (self._last_view or {}).get("image", "")
        tabs_hash = hashlib.sha256(
            repr([(t["id"], t["url"], t.get("active")) for t in tabs_meta]).encode("utf-8")
        ).digest()
        frame_hash = hashlib.sha256(image_b64.encode("ascii")).digest()
        state_hash = hashlib.sha256(tabs_hash + frame_hash + url.encode("utf-8")).digest()
        if state_hash == self._last_view_hash and self._last_view is not None:
            return
        self._last_view_hash = state_hash
        view = {
            "available": True,
            "generation": self._browser.generation,
            "profile": self._settings.profile,
            "headless": self._settings.headless,
            "url": url,
            "title": title,
            "tabs": tabs_meta,
            "image": image_b64,
        }
        self._last_view = view

    # --- 空闲回收 ---

    async def _idle_loop(self) -> None:
        """浏览器空闲回收：keep_alive_minutes=0 表示不回收。"""
        keep_alive = self._settings.keep_alive_minutes
        if keep_alive <= 0:
            return
        while True:
            await asyncio.sleep(30)
            if not self._browser.is_started:
                continue
            idle_seconds = time.monotonic() - self._last_activity
            if idle_seconds >= keep_alive * 60:
                logger.info("browser_use 空闲 %s 分钟，回收浏览器", keep_alive)
                try:
                    await self._browser.stop()
                except Exception as exc:
                    logger.debug("browser_use 空闲回收失败", exc_info=exc)

    # --- MCP 注入 ---

    def build_mcp_server_config(self, *, cwd: str | None = None) -> object | None:
        """构建 node_repl stdio MCP 服务器配置；资产缺失时 None（不注入）。

        Args:
            cwd: 工作区目录（内核 require/import 解析基准；None 继承宿主进程 cwd）
        """
        from illusion.mcp.types import McpStdioServerConfig

        launch = build_mcp_launch_command()
        if launch is None:
            return None
        argv, env = launch
        endpoint = self._broker.endpoint
        if endpoint is None:
            return None
        host, port = endpoint
        return McpStdioServerConfig(
            type="stdio",
            command=argv[0],
            args=argv[1:],
            env={
                **env,
                "ILLUSION_BROWSER_BROKER_HOST": host,
                "ILLUSION_BROWSER_BROKER_PORT": str(port),
                "ILLUSION_BROWSER_BROKER_TOKEN": self._token,
                # 截图等 artifact 落盘根（内核读取后写 artifact 目录）
                "ILLUSION_CONFIG_DIR": self._config_dir or "",
            },
            cwd=cwd,
            enabled=True,
        )

    # --- 状态摘要（诊断 / 设置页） ---

    def status_summary(self) -> dict[str, Any]:
        """面向 UI 的运行状态摘要（不包含 token）。"""
        return {
            "enabled": self._started,
            "profile": self._settings.profile,
            "headless": self._settings.headless,
            "browser_running": self._browser.is_started,
            "generation": self._browser.generation,
            "tab_count": len(self._browser.agent_tabs()),
            "session_name": self._executor.session_name,
        }
