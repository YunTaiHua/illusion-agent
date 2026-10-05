"""
浏览器会话管理器
================

会话级浏览器管理：RuntimeBundle 持有一个
BrowserManager，负责后端选择（桌面桥接优先，否则托管 Playwright）、
懒启动（首次使用时自动装配 runtime）、当前 tab 状态、最后一帧截图缓存，
以及向 WS 宿主推送状态/画面事件的回调。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from illusion.browser.base import (
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    BrowserBackend,
    BrowserCommandError,
    TabInfo,
)
from illusion.browser.desktop_bridge import DesktopBridgeBackend, desktop_bridge_available
from illusion.browser.playwright_backend import PlaywrightBackend

logger = logging.getLogger(__name__)

# 状态/画面事件回调：ws_host 注入，用于向右栏推送 browser_state / browser_frame
StateCallback = Callable[[dict[str, Any]], Awaitable[None]]
FrameCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class BrowserConfig:
    """浏览器配置（来自 settings.browser）。"""

    kernel: str = "auto"  # auto | chromium | chrome | msedge（桌面模式忽略）
    headless: bool = True
    viewport_width: int = DEFAULT_VIEWPORT_WIDTH
    viewport_height: int = DEFAULT_VIEWPORT_HEIGHT
    proxy: str = "auto"  # auto（系统代理/env 自动探测）| off | 显式代理 URL


class BrowserManager:
    """会话级浏览器管理器。

    生命周期与 RuntimeBundle 一致：懒启动（首次命令时），bundle aclose 时关闭。
    """

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig()
        self._backend: BrowserBackend | None = None
        self._open = False
        self._start_lock = asyncio.Lock()
        self.last_frame: dict[str, Any] | None = None
        self.on_state_change: StateCallback | None = None
        self.on_frame: FrameCallback | None = None
        self.on_progress: Callable[[str], Awaitable[None]] | None = None

    # ------------------------------------------------------------------
    # 后端与生命周期
    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._backend.mode if self._backend else (
            "desktop" if desktop_bridge_available() else "managed"
        )

    @property
    def is_open(self) -> bool:
        return self._open

    def _create_backend(self) -> BrowserBackend:
        if desktop_bridge_available():
            return DesktopBridgeBackend()
        return PlaywrightBackend(
            kernel=self.config.kernel,
            headless=self.config.headless,
            viewport_width=self.config.viewport_width,
            viewport_height=self.config.viewport_height,
            proxy=self.config.proxy,
        )

    async def ensure_started(self) -> BrowserBackend:
        """懒启动：首次使用时装配 runtime 并启动后端（并发调用合并为一次）。"""
        if self._backend is not None:
            return self._backend
        async with self._start_lock:
            if self._backend is not None:
                return self._backend
            backend = self._create_backend()
            await backend.start()
            self._backend = backend
        self._open = True
        await self.notify_state()
        return self._backend

    async def aclose(self) -> None:
        if self._backend is not None:
            try:
                await self._backend.aclose()
            except Exception:
                logger.debug("[browser] backend close failed", exc_info=True)
        self._backend = None
        self._open = False
        self.last_frame = None

    def _require_backend(self) -> BrowserBackend:
        if self._backend is None:
            raise BrowserCommandError("Browser is not open. Call browser_tabs new first.")
        return self._backend

    # ------------------------------------------------------------------
    # 事件推送
    # ------------------------------------------------------------------
    async def notify_state(self) -> None:
        """把当前状态推给前端（工具层与面板共用；未启动时推 closed 态）。"""
        if self.on_state_change is None:
            return
        try:
            backend = self._require_backend()
        except BrowserCommandError:
            await self.on_state_change({"open": False, "mode": self.mode, "tabs": []})
            return
        try:
            state = await backend.get_state()
        except BrowserCommandError:
            return
        payload: dict[str, Any] = {
            "open": True,
            "mode": backend.mode,
            "tabs": [t.to_dict() for t in state.tabs],
            "viewport_width": state.viewport_width,
            "viewport_height": state.viewport_height,
        }
        try:
            await self.on_state_change(payload)
        except Exception:
            logger.debug("[browser] state push failed", exc_info=True)

    async def capture_frame(self, tab_id: str) -> None:
        """捕获一帧并缓存 + 推送（右栏可视化数据源）。

        pending tab（guest 未 attach）拍不到帧：跳过而非抛出——panel_open
        等路径对新建 tab 补拍，抛错会把"打开浏览器"整体变成失败。
        """
        backend = self._require_backend()
        try:
            shot = await backend.capture_panel_frame(tab_id)
        except BrowserCommandError:
            logger.debug("[browser] capture skipped (tab not ready)", exc_info=True)
            return
        if shot is None or not shot.data:
            return
        try:
            tabs = await backend.list_tabs()
        except BrowserCommandError:
            tabs = []
        active = next((t for t in tabs if t.id == tab_id), None)
        frame: dict[str, Any] = {
            "tab_id": tab_id,
            "url": active.url if active else "",
            "title": active.title if active else "",
            "jpeg_base64": base64.b64encode(shot.data).decode("ascii"),
        }
        self.last_frame = frame
        if self.on_frame is not None:
            try:
                await self.on_frame(frame)
            except Exception:
                logger.debug("[browser] frame push failed", exc_info=True)

    # ------------------------------------------------------------------
    # 面板（右栏可视化）控制
    # ------------------------------------------------------------------
    async def panel_open(self) -> dict[str, Any]:
        """打开浏览器（右栏「打开浏览器」按钮 / agent 首次建 tab 时调用）。

        不强建空白 tab（面板可为零 tab；前端空白引导层承接
        "输入网址开始浏览"），避免每次激活先闪一个 about:blank。
        """
        backend = await self.ensure_started()
        await self.notify_state()
        state = await backend.get_state()
        if state.tabs:
            active = next((t for t in state.tabs if t.active), state.tabs[0])
            await self.capture_frame(active.id)
        return {"open": True, "mode": backend.mode}

    async def panel_close(self) -> dict[str, Any]:
        """关闭右栏面板。托管模式同时关闭浏览器进程。"""
        if self._backend is not None:
            await self._backend.aclose()
        self._backend = None
        self._open = False
        self.last_frame = None
        await self._emit_closed()
        return {"open": False}

    async def _emit_closed(self) -> None:
        if self.on_state_change is not None:
            try:
                await self.on_state_change({"open": False, "mode": self.mode, "tabs": []})
            except Exception:
                logger.debug("[browser] closed-state push failed", exc_info=True)

    async def panel_capture(self, tab_id: str | None = None) -> dict[str, Any]:
        """面板打开/刷新时主动拉一帧。"""
        backend = self._require_backend()
        if tab_id is None:
            state = await backend.get_state()
            active = next((t for t in state.tabs if t.active), None)
            tab_id = active.id if active else (state.tabs[0].id if state.tabs else None)
        if tab_id is None:
            raise BrowserCommandError("No browser tab available")
        await self.capture_frame(tab_id)
        return self.last_frame or {}

    # ------------------------------------------------------------------
    # Web 面板交互转发（web 版截图流上的点击/滚动/键盘）
    # ------------------------------------------------------------------
    async def interact(self, kind: str, x: float = 0.0, y: float = 0.0, dx: float = 0.0,
                       dy: float = 0.0, button: str = "left", key: str = "",
                       text: str = "") -> None:
        """把右栏画面上的用户交互转发到真实页面。

        kind: move（悬停，坐标类）/ click / dblclick / scroll（坐标类）；
        key（按键，Playwright 键名）；input（向当前聚焦元素追加文本——
        先点击聚焦再键入）。move 用于拾取模式：让页面内的选择器脚本
        先获得 hovered 元素，随后 click 才能命中（否则首击被判取消）。
        """
        backend = self._require_backend()
        state = await backend.get_state()
        active = next((t for t in state.tabs if t.active), None)
        if active is None:
            raise BrowserCommandError("No active browser tab")
        tab_id = active.id
        if kind == "move":
            await backend.hover(tab_id, x, y)
            return
        if kind == "click":
            await backend.click(tab_id, x=x, y=y)
        elif kind == "dblclick":
            await backend.click(tab_id, x=x, y=y, double=True)
        elif kind == "scroll":
            await backend.scroll(tab_id, x, y, dx, dy)  # 无效时抛错（面板报错提示）
        elif kind == "key":
            if not key:
                raise BrowserCommandError("key interaction requires a key name")
            await backend.press_key(tab_id, key)
        elif kind == "input":
            if not text:
                return
            await backend.type_text(tab_id, text)
        else:
            raise BrowserCommandError(f"Unknown interaction: {kind}")
        if kind in ("click", "dblclick", "scroll"):
            await self.capture_frame(tab_id)

    # ------------------------------------------------------------------
    # 供工具层使用的便捷封装（带事件推送）
    # ------------------------------------------------------------------
    async def command(self, coro_factory: Callable[[BrowserBackend], Any],
                      push_frame_tab: str | None = None) -> Any:
        """执行一个后端操作并推送状态/画面事件。"""
        backend = await self.ensure_started()
        result = await coro_factory(backend)
        await self.notify_state()
        if push_frame_tab:
            try:
                await self.capture_frame(push_frame_tab)
            except BrowserCommandError:
                pass
        return result

    async def active_tab(self) -> TabInfo:
        backend = await self.ensure_started()
        return await backend.get_active_tab()
