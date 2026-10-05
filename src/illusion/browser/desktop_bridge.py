"""
桌面版浏览器桥接后端
====================

桌面模式的桥接客户端：真实浏览器由
Electron 主进程的 <webview> 承载，本模块是 Python 侧客户端——把命令经
本地 HTTP 控制服务器（browserHost.ts，token 鉴权）转发给 Electron 主进程，
由其对 webContents 执行。桌面模式下 manager 自动选择此后端。
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from illusion.browser.base import (
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    BrowserBackend,
    BrowserCommandError,
    PageState,
    ScreenshotData,
    TabInfo,
    clamp_viewport,
    validate_url,
)

logger = logging.getLogger(__name__)

BRIDGE_URL_ENV = "ILLUSION_DESKTOP_BROWSER_URL"
BRIDGE_TOKEN_ENV = "ILLUSION_DESKTOP_BROWSER_TOKEN"


def desktop_bridge_available() -> bool:
    """当前进程是否运行在桌面壳注入的桥接环境内。"""
    return bool(os.environ.get(BRIDGE_URL_ENV))


class DesktopBridgeBackend(BrowserBackend):
    """经 Electron 控制服务器驱动的 <webview> 浏览器。"""

    def __init__(self) -> None:
        self._url = os.environ.get(BRIDGE_URL_ENV, "").rstrip("/")
        self._token = os.environ.get(BRIDGE_TOKEN_ENV, "")
        self._client: httpx.AsyncClient | None = None
        self._viewport = (DEFAULT_VIEWPORT_WIDTH, DEFAULT_VIEWPORT_HEIGHT)

    @property
    def mode(self) -> str:
        return "desktop"

    async def _post(self, op: str, payload: dict[str, Any] | None = None,
                    timeout: float = 30.0) -> dict[str, Any]:
        if not self._client:
            self._client = httpx.AsyncClient(timeout=60.0)
        try:
            resp = await self._client.post(
                f"{self._url}/command",
                json={"op": op, **(payload or {})},
                # Connection: close：每命令一条连接。Node 默认 keepAliveTimeout
                # 仅 5s，池内空闲连接被掐断后下一个命令直接 ConnectionReset，
                # 表现为假象的 "bridge unreachable"；本机连接建立开销可忽略
                headers={"X-Illusion-Browser-Token": self._token, "Connection": "close"},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise BrowserCommandError(f"Desktop browser bridge unreachable ({op}): {exc}") from exc
        if resp.status_code != 200:
            raise BrowserCommandError(f"Desktop browser bridge error: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("ok"):
            raise BrowserCommandError(str(data.get("error") or "Unknown bridge error"))
        result = data.get("result")
        return dict(result) if isinstance(result, dict) else {}

    async def start(self) -> None:
        await self._post("getState")

    async def close_all(self) -> None:
        """关闭整个浏览器（清空控制服务器 tab 注册表 + 通知渲染层卸载 guest）。"""
        await self._post("closeAll", {})

    async def aclose(self) -> None:
        # 桥接后端不拥有浏览器生命周期（webview 归 Electron 管），仅释放连接。
        # aclose 前先 closeAll：否则控制服务器 tabs 表留下僵尸 tab，下次
        # ensure_started 的 getState 仍回报它们（"关闭后仍显示 tabs"根因）。
        try:
            await self.close_all()
        except Exception:  # 控制服务器已退出时无需清理
            logger.debug("[browser] closeAll skipped (control server gone)", exc_info=True)
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Tab 管理（Electron 侧维护 webview tab）
    # ------------------------------------------------------------------
    def _tab(self, data: dict[str, Any]) -> TabInfo:
        # can_go_back/forward 由 Electron 控制服务器的 navigationHistory 判定
        # （tabInfo）：漏解析会让桌面端工具栏的前进/回退按钮永不置灰
        return TabInfo(
            id=str(data.get("id", "")),
            url=str(data.get("url", "about:blank")),
            title=str(data.get("title", "")),
            active=bool(data.get("active", False)),
            can_go_back=bool(data.get("can_go_back", False)),
            can_go_forward=bool(data.get("can_go_forward", False)),
        )

    async def list_tabs(self) -> list[TabInfo]:
        result = await self._post("listTabs")
        return [self._tab(t) for t in result.get("tabs", [])]

    async def new_tab(self, url: str | None = None, wait_load: bool = False) -> TabInfo:
        result = await self._post("newTab", {
            "url": validate_url(url) if url else None,
            "waitLoad": wait_load,
        })
        return self._tab(result.get("tab", {}))

    async def close_tab(self, tab_id: str) -> None:
        await self._post("closeTab", {"tabId": tab_id})

    async def select_tab(self, tab_id: str) -> TabInfo:
        result = await self._post("selectTab", {"tabId": tab_id})
        return self._tab(result.get("tab", {}))

    async def get_active_tab(self) -> TabInfo:
        result = await self._post("getActiveTab")
        return self._tab(result.get("tab", {}))

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------
    async def navigate(self, tab_id: str, url: str) -> TabInfo:
        result = await self._post("navigate", {"tabId": tab_id, "url": validate_url(url)},
                                  timeout=35.0)
        return self._tab(result.get("tab", {}))

    async def reload(self, tab_id: str) -> TabInfo:
        result = await self._post("reload", {"tabId": tab_id})
        return self._tab(result.get("tab", {}))

    async def go_back(self, tab_id: str) -> TabInfo:
        result = await self._post("goBack", {"tabId": tab_id})
        return self._tab(result.get("tab", {}))

    async def go_forward(self, tab_id: str) -> TabInfo:
        result = await self._post("goForward", {"tabId": tab_id})
        return self._tab(result.get("tab", {}))

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    async def click(self, tab_id: str, ref: str | None = None, x: float | None = None,
                    y: float | None = None, double: bool = False) -> None:
        await self._post("click", {"tabId": tab_id, "ref": ref, "x": x, "y": y,
                                   "double": double})

    async def hover(self, tab_id: str, x: float, y: float) -> None:
        await self._post("hover", {"tabId": tab_id, "x": x, "y": y})

    async def type_text(self, tab_id: str, text: str, ref: str | None = None,
                        submit: bool = False) -> str:
        result = await self._post("type", {"tabId": tab_id, "text": text, "ref": ref, "submit": submit})
        return str(result.get("note", ""))

    async def press_key(self, tab_id: str, key: str) -> None:
        await self._post("pressKey", {"tabId": tab_id, "key": key})

    async def scroll(self, tab_id: str, x: float, y: float, dx: float, dy: float) -> str:
        result = await self._post("scroll", {"tabId": tab_id, "x": x, "y": y, "dx": dx, "dy": dy})
        return str(result.get("note", ""))

    # ------------------------------------------------------------------
    # 观察
    # ------------------------------------------------------------------
    async def screenshot(self, tab_id: str, full_page: bool = False) -> ScreenshotData:
        result = await self._post("screenshot", {"tabId": tab_id, "fullPage": full_page},
                                  timeout=35.0)
        raw = str(result.get("jpegBase64", ""))
        if not raw:
            raise BrowserCommandError("Desktop bridge returned an empty screenshot")
        return ScreenshotData(data=base64.b64decode(raw), mime="image/jpeg")

    async def snapshot(self, tab_id: str) -> dict[str, object]:
        result = await self._post("snapshot", {"tabId": tab_id}, timeout=35.0)
        out = {
            "url": result.get("url", ""),
            "title": result.get("title", ""),
            "yaml": result.get("yaml", ""),
            "viewport_width": result.get("viewport_width", self._viewport[0]),
            "viewport_height": result.get("viewport_height", self._viewport[1]),
        }
        # 防御性快照的脚本异常透传（工具层据此向模型报告真实原因）
        if result.get("error"):
            out["error"] = str(result["error"])
        return out

    async def evaluate(self, tab_id: str, expression: str) -> str:
        result = await self._post("evaluate", {"tabId": tab_id, "expression": expression})
        return str(result.get("text", ""))

    async def open_devtools(self, tab_id: str) -> None:
        """打开 DevTools（桌面 webview 专属）。"""
        await self._post("openDevtools", {"tabId": tab_id})

    async def pick_script(self, tab_id: str, script: str) -> dict[str, object]:
        result = await self._post("pickScript", {"tabId": tab_id, "script": script},
                                  timeout=190.0)
        return dict(result.get("element") or {})

    async def pick_cancel(self, tab_id: str) -> None:
        await self._post("pickCancel", {"tabId": tab_id})

    # 坐标拾取（pick(x, y)）不覆盖：桌面桥不支持，继承基类的
    # "not supported" 语义（拾取统一走前端下发的 pick_script 脚本路径）

    async def resize(self, tab_id: str, width: int, height: int) -> tuple[int, int]:
        w, h = clamp_viewport(width, height)
        result = await self._post("resize", {"tabId": tab_id, "width": w, "height": h})
        self._viewport = (int(result.get("width", w)), int(result.get("height", h)))
        return self._viewport

    async def wait(self, tab_id: str, seconds: float, load: bool = False) -> None:
        # Electron 侧预算为 seconds+5s（load 路径），HTTP 超时必须宽于它，
        # 否则真实的超时错误会被误报为 "bridge unreachable"
        await self._post("wait", {"tabId": tab_id, "seconds": min(max(seconds, 0.0), 30.0),
                                  "load": load}, timeout=seconds + 15.0)

    async def get_state(self) -> PageState:
        result = await self._post("getState")
        self._viewport = (
            int(result.get("viewport_width", self._viewport[0])),
            int(result.get("viewport_height", self._viewport[1])),
        )
        return PageState(
            tabs=[self._tab(t) for t in result.get("tabs", [])],
            viewport_width=self._viewport[0],
            viewport_height=self._viewport[1],
        )
