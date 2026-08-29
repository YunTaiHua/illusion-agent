"""Browser Use 命令执行器
========================

把 browser-client 命令信封（method + 参数）翻译为 Playwright 操作。覆盖协议
全量方法（与 browser-use 插件 shared/browser-use/commands.ts 的 zod union 一致）：

- Tab 生命周期：list / newTab / activateTab / close / claimTab / finalizeTabs /
  finalize / markDeliverable / markHandoff / nameSession
- 页面状态：getState / navigate / back / forward / reload / waitFor /
  browserViewportSet / browserViewportReset / screenshot / elementInfo / evaluate
- 快照交互（ref 语义）：snapshot / click / fill / type / press / hover /
  select / check / drag / scroll / domCuaScroll
- 视觉交互（坐标语义）：cuaKeypress / cuaScroll / cuaDrag
- JS 弹窗：getDialog / handleDialog
- Playwright 表面：playwright（locator 操作、domSnapshot、evaluate、
  waitForURL/LoadState/Event、elementScreenshot、downloadPath、
  fileChooserSetFiles）与 playwrightWaitForTimeout
- 其余协议方法（recording*、visibility、turnEnded 等）按能力声明处理

并发说明：命令之间不加全局锁——Playwright 驱动内部按页串行化原语操作，
而 waitForEvent/downloadPath 等长等待命令必须与触发命令并发，加锁会死锁。
ref 注入与 ref 使用之间的竞态仅在内核代码同 cell 并发时可能出现（协议文档
已约定顺序调用），由调用方语义保证。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from illusion.browser_use.protocol import (
    DOWNLOAD_TIMEOUT_MS,
    NAVIGATION_TIMEOUT_MS,
    ROUTINE_TIMEOUT_MS,
    VIEWPORT_MAX_HEIGHT,
    VIEWPORT_MAX_WIDTH,
    VIEWPORT_MIN_HEIGHT,
    VIEWPORT_MIN_WIDTH,
    BrowserCommandFailure,
    BrowserViewportSize,
    PageState,
    Snapshot,
    SnapshotDomNode,
    SnapshotElement,
    TabSummary,
    error_result,
    failure_to_result,
    ok_result,
)
from illusion.browser_use.session import (
    REF_LOCATOR_TEMPLATE,
    ManagedBrowser,
    TabEntry,
)

logger = logging.getLogger(__name__)

# 允许导航的 URL scheme（与 docs/api.json navigationUrl 语义一致）
_NAVIGABLE_SCHEMES = ("http", "https")
_ABOUT_BLANK = "about:blank"

# snapshot 交互元素选择器（主文档；与 Codex dom_cua 语义对齐的可交互集合）
_SNAPSHOT_SELECTOR = (
    "a[href], button, input, select, textarea, summary, details, option, label, "
    "[role], [onclick], [contenteditable=\"true\"], [tabindex]"
)
# 快照语义骨架（dom 字段）采集的元素集合
_SNAPSHOT_DOM_TAGS = "h1,h2,h3,h4,h5,h6,main,nav,header,footer,aside,section,article,form,table,ul,ol,dialog"

# 快照元素结构中注入到页面脚本的有界稳定属性白名单
# （协议约定：不返回 class/style/src 等高噪声字段）
_STABLE_ATTRIBUTE_LIST = ["id", "name", "type", "href", "placeholder", "aria-label", "title", "value"]

# CUA 修饰键集合（协议枚举）
_CUA_MODIFIERS = ("Alt", "Control", "ControlOrMeta", "Meta", "Shift")


def _normalize_modifier(modifier: str) -> str:
    """ControlOrMeta 按平台展开（macOS → Meta，其余 → Control）。"""
    if modifier == "ControlOrMeta":
        return "Meta" if sys.platform == "darwin" else "Control"
    return modifier


def _validate_viewport(width: Any, height: Any) -> BrowserViewportSize:
    """视口自由尺寸安全边界校验（与 browser-client BROWSER_VIEWPORT_LIMITS 一致）。"""
    if not isinstance(width, int) or not isinstance(height, int):
        raise BrowserCommandFailure("execution_error", "viewport requires integer width and height")
    if not VIEWPORT_MIN_WIDTH <= width <= VIEWPORT_MAX_WIDTH:
        raise BrowserCommandFailure(
            "execution_error",
            f"viewport width must be {VIEWPORT_MIN_WIDTH}..{VIEWPORT_MAX_WIDTH}",
        )
    if not VIEWPORT_MIN_HEIGHT <= height <= VIEWPORT_MAX_HEIGHT:
        raise BrowserCommandFailure(
            "execution_error",
            f"viewport height must be {VIEWPORT_MIN_HEIGHT}..{VIEWPORT_MAX_HEIGHT}",
        )
    return BrowserViewportSize(width=width, height=height)


class BrowserCommandExecutor:
    """命令信封执行器。

    Usage:
        executor = BrowserCommandExecutor(browser)
        envelope = await executor.execute({"method": "list"})
    """

    def __init__(self, browser: ManagedBrowser) -> None:
        self._browser = browser
        self._waiters: dict[str, _EventWaiter] = {}
        self.session_name: str = ""
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "list": self._cmd_list,
            "newTab": self._cmd_new_tab,
            "activateTab": self._cmd_activate_tab,
            "claimTab": self._cmd_claim_tab,
            "listUserTabs": self._cmd_list_user_tabs,
            "finalizeTabs": self._cmd_finalize_tabs,
            "nameSession": self._cmd_name_session,
            "closeSession": self._cmd_noop,
            "turnEnded": self._cmd_noop,
            "cancelRequest": self._cmd_noop,
            "navigate": self._cmd_navigate,
            "back": self._cmd_back,
            "forward": self._cmd_forward,
            "reload": self._cmd_reload,
            "getState": self._cmd_get_state,
            "waitFor": self._cmd_wait_for,
            "screenshot": self._cmd_screenshot,
            "elementInfo": self._cmd_element_info,
            "evaluate": self._cmd_evaluate,
            "click": self._cmd_click,
            "fill": self._cmd_fill,
            "type": self._cmd_type,
            "press": self._cmd_press,
            "hover": self._cmd_hover,
            "select": self._cmd_select,
            "check": self._cmd_check,
            "drag": self._cmd_drag,
            "scroll": self._cmd_scroll,
            "snapshot": self._cmd_snapshot,
            "domCuaScroll": self._cmd_dom_cua_scroll,
            "cuaKeypress": self._cmd_cua_keypress,
            "cuaScroll": self._cmd_cua_scroll,
            "cuaDrag": self._cmd_cua_drag,
            "getDialog": self._cmd_get_dialog,
            "handleDialog": self._cmd_handle_dialog,
            "close": self._cmd_close_tab,
            "finalize": self._cmd_finalize_tab,
            "markDeliverable": self._cmd_mark_deliverable,
            "markHandoff": self._cmd_mark_handoff,
            "browserViewportSet": self._cmd_viewport_set,
            "browserViewportReset": self._cmd_viewport_reset,
            "browserVisibilityGet": self._cmd_visibility_get,
            "browserVisibilitySet": self._cmd_visibility_set,
            "capabilities": self._cmd_capabilities,
            "playwright": self._cmd_playwright,
            "playwrightWaitForTimeout": self._cmd_playwright_wait_for_timeout,
            "recordingStart": self._cmd_recording_unsupported,
            "recordingStatus": self._cmd_recording_unsupported,
            "recordingCancel": self._cmd_recording_unsupported,
        }

    # --- 入口 ---

    async def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        """执行一条命令并返回结果信封（任何路径都不向外抛异常）。"""
        started = time.perf_counter()
        method = str(command.get("method"))
        handler = self._handlers.get(method)
        if handler is None:
            return error_result(
                started,
                "capability_unsupported",
                f"Browser command '{method}' is not supported",
            )
        try:
            result: dict[str, Any] = await handler(command)
            return result
        except BrowserCommandFailure as failure:
            return failure_to_result(started, failure)
        except Exception as exc:  # noqa: BLE001 — 协议边界必须折叠一切异常
            return error_result(
                started,
                "timeout" if _is_playwright_timeout(exc) else "execution_error",
                f"Browser command '{method}' failed: {type(exc).__name__}: {exc}",
                side_effect_uncertain=not _is_playwright_timeout(exc),
            )

    # --- Tab 生命周期 ---

    async def _cmd_list(self, _command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        active_id = self._active_tab_id()
        tabs = [self._tab_summary(entry, active_tab_id=active_id) for entry in self._browser.agent_tabs()]
        return ok_result(started, tabs=tabs)

    def _active_tab_id(self) -> str | None:
        owned = self._browser.agent_tabs()
        return owned[-1].tabId if owned else None

    def _tab_summary(self, entry: TabEntry, *, active_tab_id: str | None = None) -> TabSummary:
        return TabSummary(
            tabId=entry.tabId,
            url=str(getattr(entry.page, "url", "") or ""),
            title="",
            viewport=entry.viewport,
            active=(entry.tabId == active_tab_id) or None,
            lifecycle=entry.lifecycle if entry.lifecycle != "active" else None,
        )

    async def _cmd_new_tab(self, _command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = await self._browser.create_tab()
        return ok_result(started, tab=self._tab_summary(entry, active_tab_id=entry.tabId))

    async def _cmd_activate_tab(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        # bring_to_front 让 headed 模式用户立即看到该 tab；headless 下为无感操作
        await entry.page.bring_to_front()
        return ok_result(started, tab=self._tab_summary(entry, active_tab_id=entry.tabId))

    async def _cmd_claim_tab(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = await self._browser.claim_tab(str(command.get("tabId", "")))
        return ok_result(started, tab=self._tab_summary(entry, active_tab_id=entry.tabId))

    async def _cmd_list_user_tabs(self, _command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        user_tabs = [
            {
                "id": entry.tabId,
                "url": str(getattr(entry.page, "url", "") or ""),
                "title": "",
            }
            for entry in self._browser.user_tabs()
        ]
        return ok_result(started, userTabs=user_tabs)

    async def _cmd_finalize_tabs(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        for item in command.get("keep", []):
            entry = self._browser.registry.get(str(item.get("tabId", "")))
            status = item.get("status")
            if entry is not None and status in ("deliverable", "handoff"):
                entry.lifecycle = status
        return ok_result(started)

    async def _cmd_name_session(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        self.session_name = str(command.get("name", "")).strip()
        return ok_result(started)

    async def _cmd_noop(self, _command: dict[str, Any]) -> dict[str, Any]:
        """协议保留的生命周期方法（宿主无需副作用，返回 ok 维持握手）。"""
        return ok_result(time.perf_counter())

    # --- 导航与页面状态 ---

    async def _cmd_navigate(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        url = str(command.get("url", ""))
        parsed = urlparse(url)
        if parsed.scheme not in _NAVIGABLE_SCHEMES and url != _ABOUT_BLANK:
            raise BrowserCommandFailure(
                "navigation_blocked",
                f"goto() accepts http:, https: and exact about:blank; got '{url}'",
            )
        await entry.page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        # 导航使旧 ref/弹窗全部失效
        entry.pending_dialog = None
        return ok_result(started)

    async def _cmd_back(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        await entry.page.go_back(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        return ok_result(started)

    async def _cmd_forward(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        await entry.page.go_forward(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        return ok_result(started)

    async def _cmd_reload(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        await entry.page.reload(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        entry.pending_dialog = None
        return ok_result(started)

    async def _cmd_get_state(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        page = entry.page
        can_back, can_forward, scroll_x, scroll_y = await self._navigation_and_scroll(entry)
        try:
            title = await page.title()
        except Exception as exc:
            logger.debug("page.title() 读取失败", exc_info=exc)
            title = ""
        state = PageState(
            url=str(getattr(page, "url", "") or ""),
            title=title or "",
            canGoBack=can_back,
            canGoForward=can_forward,
            scrollX=scroll_x,
            scrollY=scroll_y,
            viewportWidth=entry.viewport.width,
            viewportHeight=entry.viewport.height,
        )
        return ok_result(started, state=state)

    async def _navigation_and_scroll(self, entry: TabEntry) -> tuple[bool, bool, float | None, float | None]:
        """导航历史与滚动位置（CDP 优先，失败回退 only-scroll 探测）。"""
        page = entry.page
        scroll_x: float | None = None
        scroll_y: float | None = None
        try:
            scroll = await page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
            scroll_x = float(scroll.get("x", 0.0))
            scroll_y = float(scroll.get("y", 0.0))
        except Exception as exc:
            logger.debug("滚动位置读取失败", exc_info=exc)
        try:
            cdp = await self._browser.cdp_session_for(entry)
            history = await cdp.send("Page.getNavigationHistory")
            index = int(history.get("currentIndex", 0))
            entries = history.get("entries", [])
            return index > 0, index < len(entries) - 1, scroll_x, scroll_y
        except Exception as exc:
            logger.debug("Page.getNavigationHistory 读取失败", exc_info=exc)
            return False, False, scroll_x, scroll_y

    async def _cmd_wait_for(self, command: dict[str, Any]) -> dict[str, Any]:
        """waitFor：selector / text 出现，或 text 消失（Codex 语义）。"""
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        selector = command.get("selector")
        text = command.get("text")
        text_gone = command.get("textGone")
        timeout = int(command.get("timeoutMs") or ROUTINE_TIMEOUT_MS)
        if selector:
            await entry.page.wait_for_selector(str(selector), state="visible", timeout=timeout)
        elif text:
            await entry.page.get_by_text(str(text)).wait_for(state="visible", timeout=timeout)
        elif text_gone:
            await entry.page.get_by_text(str(text_gone)).wait_for(state="hidden", timeout=timeout)
        else:
            raise BrowserCommandFailure("execution_error", "waitFor requires selector, text, or textGone")
        return ok_result(started)

    async def _cmd_screenshot(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        ref = command.get("ref")
        clip = command.get("clip")
        full_page = bool(command.get("fullPage"))
        if ref:
            locator = await self._locator_for_ref(entry, str(ref))
            raw = await locator.screenshot(type="png")
        else:
            options: dict[str, Any] = {"type": "png"}
            if full_page:
                options["full_page"] = True
            if clip:
                options["clip"] = {
                    "x": float(clip["x"]),
                    "y": float(clip["y"]),
                    "width": float(clip["width"]),
                    "height": float(clip["height"]),
                }
            raw = await entry.page.screenshot(**options)
        return ok_result(started, image={"base64": _to_base64(raw), "mimeType": "image/png"})

    async def _cmd_evaluate(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        expression = str(command.get("expression", ""))
        if not expression:
            raise BrowserCommandFailure("execution_error", "evaluate requires expression")
        value = await entry.page.evaluate(expression)
        return ok_result(started, value=value)

    # --- ref / 坐标交互 ---

    async def _cmd_click(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        button = command.get("button") or "left"
        modifiers = [_normalize_modifier(m) for m in command.get("modifiers", [])]
        click_count = 2 if command.get("doubleClick") else 1
        ref = command.get("ref")
        if ref:
            locator = await self._locator_for_ref(entry, str(ref))
            await locator.click(
                button=button,
                click_count=click_count,
                modifiers=modifiers or None,
                timeout=ROUTINE_TIMEOUT_MS,
            )
            return ok_result(started)
        x, y = _require_point(command)
        await entry.page.mouse.click(x, y, button=button, click_count=click_count)
        return ok_result(started)

    async def _cmd_fill(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        locator = await self._locator_for_ref(entry, str(command.get("ref", "")))
        await locator.fill(str(command.get("value", "")), timeout=ROUTINE_TIMEOUT_MS)
        return ok_result(started)

    async def _cmd_type(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        text = str(command.get("text", ""))
        ref = command.get("ref")
        if ref:
            locator = await self._locator_for_ref(entry, str(ref))
            await locator.click(timeout=ROUTINE_TIMEOUT_MS)
        await entry.page.keyboard.type(text)
        return ok_result(started)

    async def _cmd_press(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        key = str(command.get("key", ""))
        if not key:
            raise BrowserCommandFailure("execution_error", "press requires key")
        ref = command.get("ref")
        if ref:
            locator = await self._locator_for_ref(entry, str(ref))
            await locator.focus(timeout=ROUTINE_TIMEOUT_MS)
        await _press_with_modifiers(entry.page.keyboard, key, command.get("modifiers"))
        return ok_result(started)

    async def _cmd_hover(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        modifiers = [_normalize_modifier(m) for m in command.get("modifiers", [])]
        ref = command.get("ref")
        if ref:
            locator = await self._locator_for_ref(entry, str(ref))
            await locator.hover(modifiers=modifiers or None, timeout=ROUTINE_TIMEOUT_MS)
            return ok_result(started)
        x, y = _require_point(command)
        await entry.page.mouse.move(x, y)
        return ok_result(started)

    async def _cmd_select(self, command: dict[str, Any]) -> dict[str, Any]:
        """select：按 value 优先、可见文本兜底匹配 <option>。"""
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        locator = await self._locator_for_ref(entry, str(command.get("ref", "")))
        values = [str(v) for v in command.get("values", [])]
        if not values:
            raise BrowserCommandFailure("execution_error", "select requires at least one value")
        try:
            await locator.select_option(value=values, timeout=ROUTINE_TIMEOUT_MS)
            return ok_result(started)
        except Exception as exc:  # noqa: BLE001 - select 的 value 匹配失败转 label 兜底
            logger.debug("select_option(value=...) 兜底 label: %s", exc)
        try:
            await locator.select_option(label=values, timeout=ROUTINE_TIMEOUT_MS)
            return ok_result(started)
        except Exception as exc:  # noqa: BLE001 - label 整体匹配失败转逐个混合匹配
            logger.debug("select_option(label=...) 兜底逐项: %s", exc)
        # 混合匹配：逐个 value 优先、label 兜底
        for value in values:
            try:
                await locator.select_option(value=value, timeout=ROUTINE_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - 单项 value 失败即转 label
                await locator.select_option(label=value, timeout=ROUTINE_TIMEOUT_MS)
        return ok_result(started)

    async def _cmd_check(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        locator = await self._locator_for_ref(entry, str(command.get("ref", "")))
        checked = bool(command.get("checked", True))
        await locator.set_checked(checked, timeout=ROUTINE_TIMEOUT_MS)
        return ok_result(started)

    async def _cmd_drag(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        start = await self._resolve_point(entry, ref=command.get("fromRef"), point=command.get("from"))
        end = await self._resolve_point(entry, ref=command.get("toRef"), point=command.get("to"))
        await _mouse_drag(entry.page, [start, end], command.get("modifiers"))
        return ok_result(started)

    async def _cmd_scroll(self, command: dict[str, Any]) -> dict[str, Any]:
        """scroll：ref → 元素滚动入视野；坐标 → 绝对滚动定位。

        协议中 scroll 无 delta 字段（delta 语义在 cuaScroll/domCuaScroll 上），
        故坐标语义取 window.scrollTo 绝对定位。
        """
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        ref = command.get("ref")
        if ref:
            locator = await self._locator_for_ref(entry, str(ref))
            await locator.scroll_into_view_if_needed(timeout=ROUTINE_TIMEOUT_MS)
            return ok_result(started)
        x = float(command.get("x", 0))
        y = float(command.get("y", 0))
        await entry.page.evaluate(f"() => window.scrollTo({x}, {y})")
        return ok_result(started)

    async def _cmd_snapshot(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        max_elements = int(command.get("maxElements") or 300)
        include_hidden = bool(command.get("includeHidden"))
        snapshot = await self.build_snapshot(entry, max_elements=max_elements, include_hidden=include_hidden)
        return ok_result(started, snapshot=snapshot)

    async def _cmd_dom_cua_scroll(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        scroll_x = float(command.get("scrollX", 0))
        scroll_y = float(command.get("scrollY", 0))
        ref = command.get("nodeId")
        if ref:
            point = await self._resolve_point(entry, ref=str(ref), point=None)
        else:
            viewport = entry.page.viewport_size or {"width": 1280, "height": 720}
            point = (float(viewport["width"]) / 2, float(viewport["height"]) / 2)
        await entry.page.mouse.move(point[0], point[1])
        await entry.page.mouse.wheel(scroll_x, scroll_y)
        return ok_result(started)

    async def _cmd_cua_keypress(self, command: dict[str, Any]) -> dict[str, Any]:
        """CUA keypress：keys 是组合键序列，必须保留逐键 down/up 顺序。"""
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        keys = [str(k) for k in command.get("keys", [])]
        if not keys:
            raise BrowserCommandFailure("execution_error", "cuaKeypress requires keys")
        keyboard = entry.page.keyboard
        modifiers = [k for k in keys if k in _CUA_MODIFIERS]
        plain = [k for k in keys if k not in _CUA_MODIFIERS]
        for modifier in modifiers:
            await keyboard.down(_normalize_modifier(modifier))
        try:
            if plain:
                for key in plain:
                    await keyboard.press(_normalize_modifier(key))
            elif modifiers:
                # 纯修饰键组合：按下并释放最后一个修饰键本身（如 Shift → Shift 单击）
                await keyboard.press(_normalize_modifier(modifiers[-1]))
        finally:
            for modifier in reversed(modifiers):
                await keyboard.up(_normalize_modifier(modifier))
        return ok_result(started)

    async def _cmd_cua_scroll(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        modifiers = [_normalize_modifier(m) for m in command.get("modifiers", [])]
        keyboard = entry.page.keyboard
        for modifier in modifiers:
            await keyboard.down(modifier)
        try:
            await entry.page.mouse.move(float(command["x"]), float(command["y"]))
            await entry.page.mouse.wheel(float(command["scrollX"]), float(command["scrollY"]))
        finally:
            for modifier in reversed(modifiers):
                await keyboard.up(modifier)
        return ok_result(started)

    async def _cmd_cua_drag(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        path = [(float(p["x"]), float(p["y"])) for p in command.get("path", [])]
        if not path:
            raise BrowserCommandFailure("execution_error", "cuaDrag requires a non-empty path")
        await _mouse_drag(entry.page, path, command.get("modifiers"))
        return ok_result(started)

    # --- JS 弹窗 ---

    async def _cmd_get_dialog(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        pending = entry.pending_dialog
        if pending is None:
            # 协议：无弹窗时 dialog=null（信封缺省字段由 _dump_result 剔除，
            # 客户端 result.dialog ?? null 得到 null）
            return ok_result(started, dialog=None)
        return ok_result(started, dialog=pending.info)

    async def _cmd_handle_dialog(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        pending = entry.pending_dialog
        if pending is None:
            raise BrowserCommandFailure("execution_error", "当前没有待处置的 JS 弹窗")
        entry.pending_dialog = None
        accept = bool(command.get("accept"))
        prompt_text = command.get("promptText")
        if accept:
            await pending.dialog.accept(prompt_text)
        else:
            await pending.dialog.dismiss()
        return ok_result(started)

    # --- Tab 收尾 / 视口 / 可见性 ---

    async def _cmd_close_tab(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        self._browser.registry.pop(entry.tabId, None)
        await entry.page.close()
        return ok_result(started)

    async def _cmd_finalize_tab(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        entry.lifecycle = "deliverable" if command.get("deliverable") else "handoff"
        return ok_result(started)

    async def _cmd_mark_deliverable(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        entry.lifecycle = "deliverable"
        return ok_result(started)

    async def _cmd_mark_handoff(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        entry.lifecycle = "handoff"
        return ok_result(started)

    async def _cmd_viewport_set(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        size = _validate_viewport(command.get("width"), command.get("height"))
        await entry.page.set_viewport_size({"width": size.width, "height": size.height})
        entry.viewport = size
        return ok_result(started)

    async def _cmd_viewport_reset(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        default = self._browser.default_viewport
        await entry.page.set_viewport_size({"width": default.width, "height": default.height})
        entry.viewport = BrowserViewportSize(width=default.width, height=default.height)
        return ok_result(started)

    async def _cmd_visibility_get(self, _command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        return ok_result(started, value=not self._browser.headless)

    async def _cmd_visibility_set(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        if self._browser.headless:
            raise BrowserCommandFailure("capability_unsupported", "无头浏览器窗口不可见，无法切换可见性")
        await _set_window_minimized(self._browser, minimized=not bool(command.get("visible")))
        return ok_result(started)

    async def _cmd_capabilities(self, _command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        return ok_result(started, value={"browser": [], "tab": []})

    async def _cmd_recording_unsupported(self, _command: dict[str, Any]) -> dict[str, Any]:
        raise BrowserCommandFailure(
            "capability_unsupported",
            "视频录制是 IAB（桌面内置浏览器）专属能力，受管 Chromium 不支持",
        )

    async def _cmd_playwright_wait_for_timeout(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        timeout_ms = int(command.get("timeoutMs", 0))
        await asyncio.sleep(timeout_ms / 1000)
        return ok_result(started)

    # --- playwright 表面 ---

    async def _cmd_playwright(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        action = command.get("action") or {}
        name = str(action.get("name"))
        if name == "locator":
            return await self._playwright_locator(started, entry, action)
        if name == "domSnapshot":
            return await self._pw_dom_snapshot(started, entry)
        if name == "evaluate":
            return await self._pw_evaluate(started, entry, action)
        if name == "waitForLoadState":
            return await self._pw_wait_for_load_state(started, entry, action)
        if name == "waitForURL":
            return await self._pw_wait_for_url(started, entry, action)
        if name == "waitForEvent":
            return await self._pw_wait_for_event(started, entry, action)
        if name == "downloadPath":
            return await self._pw_download_path(started, action)
        if name == "fileChooserSetFiles":
            return await self._pw_file_chooser_set_files(started, action)
        if name == "elementInfo":
            return await self._element_info_with_started(started, entry, action)
        if name == "elementScreenshot":
            return await self._pw_element_screenshot(started, entry, action)
        raise BrowserCommandFailure("capability_unsupported", f"playwright action '{name}' is not supported")

    async def _pw_dom_snapshot(self, started: float, entry: TabEntry) -> dict[str, Any]:
        page = entry.page
        try:
            snapshot = await page.locator("html").aria_snapshot()
        except Exception:  # noqa: BLE001 - 无 html 元素（纯片段页面）时回退 body
            snapshot = await page.locator("body").aria_snapshot()
        return ok_result(started, value=snapshot)

    async def _pw_evaluate(self, started: float, entry: TabEntry, action: dict[str, Any]) -> dict[str, Any]:
        expression = str(action.get("expression", ""))
        if not expression:
            raise BrowserCommandFailure("execution_error", "playwright.evaluate requires pageFunction")
        timeout_ms = action.get("timeoutMs") or ROUTINE_TIMEOUT_MS
        value = await asyncio.wait_for(
            entry.page.evaluate(expression, action.get("arg")), timeout=timeout_ms / 1000
        )
        return ok_result(started, value=value)

    async def _pw_wait_for_load_state(self, started: float, entry: TabEntry, action: dict[str, Any]) -> dict[str, Any]:
        state = action.get("state") or "load"
        timeout_ms = action.get("timeoutMs") or ROUTINE_TIMEOUT_MS
        await entry.page.wait_for_load_state(state=state, timeout=timeout_ms)
        return ok_result(started)

    async def _pw_wait_for_url(self, started: float, entry: TabEntry, action: dict[str, Any]) -> dict[str, Any]:
        url = str(action.get("url", ""))
        if not url:
            raise BrowserCommandFailure("execution_error", "playwright.waitForURL requires a url")
        timeout_ms = action.get("timeoutMs") or ROUTINE_TIMEOUT_MS
        await entry.page.wait_for_url(url, wait_until=action.get("waitUntil") or "load", timeout=timeout_ms)
        return ok_result(started)

    async def _pw_wait_for_event(self, started: float, entry: TabEntry, action: dict[str, Any]) -> dict[str, Any]:
        event = str(action.get("event"))
        default_timeout = DOWNLOAD_TIMEOUT_MS if event == "download" else ROUTINE_TIMEOUT_MS
        timeout_ms = int(action.get("timeoutMs") or default_timeout)
        waiter = _EventWaiter(waiter_id=uuid.uuid4().hex, kind=event)
        self._waiters[waiter.waiter_id] = waiter
        waiter.task = asyncio.create_task(self._run_event_waiter(entry, waiter, timeout_ms))
        try:
            await asyncio.wait_for(waiter.entered.wait(), timeout=10)
        except asyncio.TimeoutError:
            self._waiters.pop(waiter.waiter_id, None)
            raise BrowserCommandFailure("timeout", "等待页面事件注册超时") from None
        if waiter.error is not None:
            self._waiters.pop(waiter.waiter_id, None)
            raise BrowserCommandFailure("execution_error", str(waiter.error))
        payload: dict[str, Any] = {"id": waiter.waiter_id}
        if event == "filechooser":
            # isMultiple 仅在事件触发后可知；此处返回保守值，setFiles 不受影响
            payload["isMultiple"] = waiter.is_multiple
        return ok_result(started, value=payload)

    async def _run_event_waiter(self, entry: TabEntry, waiter: _EventWaiter, timeout_ms: int) -> None:
        """后台持有 expect_download/expect_file_chooser 上下文直到事件或超时。"""
        try:
            if waiter.kind == "download":
                async with entry.page.expect_download(timeout=timeout_ms) as cm:
                    waiter.entered.set()
                    await asyncio.wait_for(waiter.release.wait(), timeout=timeout_ms / 1000 + 5)
                waiter.payload = cm.value
            else:
                async with entry.page.expect_file_chooser(timeout=timeout_ms) as cm:
                    waiter.entered.set()
                    await asyncio.wait_for(waiter.release.wait(), timeout=timeout_ms / 1000 + 5)
                waiter.payload = cm.value
                waiter.is_multiple = bool(getattr(waiter.payload, "is_multiple", False))
        except Exception as exc:  # noqa: BLE001 — 等待任务折叠一切异常
            waiter.error = exc
        finally:
            waiter.entered.set()
            waiter.event.set()

    async def _pw_download_path(self, started: float, action: dict[str, Any]) -> dict[str, Any]:
        waiter = self._pop_waiter(str(action.get("downloadId", "")))
        timeout_ms = int(action.get("timeoutMs") or DOWNLOAD_TIMEOUT_MS)
        try:
            await asyncio.wait_for(waiter.event.wait(), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise BrowserCommandFailure("timeout", "等待下载完成超时") from None
        if waiter.error is not None or waiter.payload is None:
            raise BrowserCommandFailure("execution_error", f"下载失败：{waiter.error}")
        path = await waiter.payload.path()
        return ok_result(started, value=str(path) if path else None)

    async def _pw_file_chooser_set_files(self, started: float, action: dict[str, Any]) -> dict[str, Any]:
        waiter = self._pop_waiter(str(action.get("fileChooserId", "")))
        timeout_ms = int(action.get("timeoutMs") or ROUTINE_TIMEOUT_MS)
        try:
            await asyncio.wait_for(waiter.event.wait(), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise BrowserCommandFailure("timeout", "等待文件选择器超时") from None
        if waiter.error is not None or waiter.payload is None:
            raise BrowserCommandFailure("execution_error", f"文件选择器失败：{waiter.error}")
        files = [str(f) for f in action.get("files", [])]
        await waiter.payload.set_files(files)
        return ok_result(started)

    def _pop_waiter(self, waiter_id: str) -> _EventWaiter:
        waiter = self._waiters.pop(waiter_id, None)
        if waiter is None:
            raise BrowserCommandFailure("ref_not_found", f"等待句柄 '{waiter_id}' 不存在或已消费")
        return waiter

    async def _pw_element_screenshot(self, started: float, entry: TabEntry, action: dict[str, Any]) -> dict[str, Any]:
        ref = await self._inject_ref_at_point(entry, float(action["x"]), float(action["y"]))
        locator = await self._locator_for_ref(entry, ref)
        raw = await locator.screenshot(type="png")
        return ok_result(started, image={"base64": _to_base64(raw), "mimeType": "image/png"})

    async def _playwright_locator(self, started: float, entry: TabEntry, action: dict[str, Any]) -> dict[str, Any]:
        selector = str(action.get("selector", ""))
        if not selector:
            raise BrowserCommandFailure("execution_error", "locator requires a selector")
        locator = entry.page.locator(selector)
        operation = str(action.get("operation", ""))
        timeout_raw = action.get("timeoutMs")
        timeout = int(timeout_raw) if timeout_raw else ROUTINE_TIMEOUT_MS
        handler = self._locator_operation_handler(operation)
        if handler is None:
            raise BrowserCommandFailure("capability_unsupported", f"locator operation '{operation}' is not supported")
        envelope: dict[str, Any] = await handler(started, locator, action, timeout)
        return envelope

    def _locator_operation_handler(
        self, operation: str
    ) -> Callable[[float, Any, dict[str, Any], int], Any] | None:
        """locator 操作名 → 执行器（统一签名 (started, locator, action, timeout)）。"""
        return {
            "allTextContents": self._loc_all_text_contents,
            "click": self._loc_click,
            "count": self._loc_count,
            "dblclick": self._loc_dblclick,
            "downloadMedia": self._loc_download_media,
            "evaluate": self._loc_evaluate,
            "fill": self._loc_fill,
            "getAttribute": self._loc_get_attribute,
            "innerText": self._loc_inner_text,
            "isEnabled": self._loc_is_enabled,
            "isVisible": self._loc_is_visible,
            "press": self._loc_press,
            "selectOption": self._loc_select_option,
            "setChecked": self._loc_set_checked,
            "textContent": self._loc_text_content,
            "waitFor": self._loc_wait_for,
        }.get(operation)

    async def _loc_all_text_contents(self, started: float, locator: Any, _action: dict[str, Any], _timeout: int) -> dict[str, Any]:
        return ok_result(started, value=await locator.all_text_contents())

    async def _loc_click(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        modifiers = [_normalize_modifier(m) for m in action.get("modifiers", [])]
        await locator.click(
            button=action.get("button") or "left",
            force=bool(action.get("force")),
            modifiers=modifiers or None,
            timeout=timeout,
        )
        return ok_result(started)

    async def _loc_count(self, started: float, locator: Any, _action: dict[str, Any], _timeout: int) -> dict[str, Any]:
        return ok_result(started, value=await locator.count())

    async def _loc_dblclick(self, started: float, locator: Any, _action: dict[str, Any], timeout: int) -> dict[str, Any]:
        await locator.dblclick(timeout=timeout)
        return ok_result(started)

    async def _loc_download_media(self, started: float, locator: Any, _action: dict[str, Any], timeout: int) -> dict[str, Any]:
        """点击元素并捕获其触发的下载，落盘到浏览器下载目录。"""
        from illusion.config.paths import get_config_dir

        downloads_dir = get_config_dir() / "browser" / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        async with locator.page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            await locator.click(timeout=timeout)
        download = download_info.value
        target = downloads_dir / (download.suggested_filename or f"download-{uuid.uuid4().hex[:8]}")
        await download.save_as(str(target))
        return ok_result(started, value=str(target))

    async def _loc_evaluate(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        expression = str(action.get("expression", ""))
        if not expression:
            raise BrowserCommandFailure("execution_error", "locator.evaluate requires pageFunction")
        value = await asyncio.wait_for(
            locator.evaluate(expression, action.get("arg")), timeout=timeout / 1000
        )
        return ok_result(started, value=value)

    async def _loc_fill(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        value = action.get("value")
        if value is None:
            raise BrowserCommandFailure("execution_error", "locator.fill requires a value")
        if action.get("replace", True):
            await locator.fill(str(value), timeout=timeout)
        else:
            await locator.press_sequentially(str(value), timeout=timeout)
        return ok_result(started)

    async def _loc_get_attribute(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        attribute = str(action.get("attribute", ""))
        if not attribute:
            raise BrowserCommandFailure("execution_error", "locator.getAttribute requires a name")
        return ok_result(started, value=await locator.get_attribute(attribute, timeout=timeout))

    async def _loc_inner_text(self, started: float, locator: Any, _action: dict[str, Any], timeout: int) -> dict[str, Any]:
        return ok_result(started, value=await locator.inner_text(timeout=timeout))

    async def _loc_is_enabled(self, started: float, locator: Any, _action: dict[str, Any], _timeout: int) -> dict[str, Any]:
        return ok_result(started, value=await locator.is_enabled())

    async def _loc_is_visible(self, started: float, locator: Any, _action: dict[str, Any], _timeout: int) -> dict[str, Any]:
        return ok_result(started, value=await locator.is_visible())

    async def _loc_press(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        value = action.get("value")
        if not value:
            raise BrowserCommandFailure("execution_error", "locator.press requires a value")
        await locator.press(str(value), timeout=timeout)
        return ok_result(started)

    async def _loc_select_option(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        selections = action.get("selections") or []
        kwargs: dict[str, Any] = {"timeout": timeout}
        values = [s["value"] for s in selections if s.get("value") is not None]
        labels = [s["label"] for s in selections if s.get("label") is not None]
        indexes = [int(s["index"]) for s in selections if s.get("index") is not None]
        if values:
            kwargs["value"] = values
        if labels:
            kwargs["label"] = labels
        if indexes:
            kwargs["index"] = indexes
        await locator.select_option(**kwargs)
        return ok_result(started)

    async def _loc_set_checked(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        checked = action.get("checked")
        if not isinstance(checked, bool):
            raise BrowserCommandFailure("execution_error", "locator.setChecked requires a boolean")
        await locator.set_checked(checked, timeout=timeout)
        return ok_result(started)

    async def _loc_text_content(self, started: float, locator: Any, _action: dict[str, Any], timeout: int) -> dict[str, Any]:
        return ok_result(started, value=await locator.text_content(timeout=timeout))

    async def _loc_wait_for(self, started: float, locator: Any, action: dict[str, Any], timeout: int) -> dict[str, Any]:
        state = action.get("state")
        if not state:
            raise BrowserCommandFailure("execution_error", "locator.waitFor requires a state")
        await locator.wait_for(state=state, timeout=timeout)
        return ok_result(started)

    # --- elementInfo / 快照构建 ---

    async def _cmd_element_info(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        entry = self._browser.resolve_tab(command.get("tabId"))
        return await self._element_info_with_started(started, entry, command)

    async def _element_info_with_started(self, started: float, entry: TabEntry, action: dict[str, Any]) -> dict[str, Any]:
        """坐标 → 命中元素信息（打通视觉↔结构；tab 级与 playwright 级共用）。"""
        x = float(action["x"])
        y = float(action["y"])
        include_non_interactable = bool(action.get("includeNonInteractable"))
        payload = await self._element_payload_at(entry, x, y, include_non_interactable)
        if payload is None:
            return ok_result(started, element=None)
        return ok_result(started, element=SnapshotElement(**payload))

    async def build_snapshot(
        self, entry: TabEntry, *, max_elements: int, include_hidden: bool
    ) -> Snapshot:
        """构建可见元素快照：清旧 ref → JS 采集 → 注入新 ref → 协议结构。"""
        await entry.clear_refs()
        script = _SNAPSHOT_SCRIPT.replace("__SELECTOR__", _SNAPSHOT_SELECTOR).replace(
            "__DOM_SELECTOR__", _SNAPSHOT_DOM_TAGS
        )
        raw = await entry.page.evaluate(script, {"maxElements": max_elements, "includeHidden": include_hidden})
        elements = [SnapshotElement(**item) for item in raw.get("elements", [])]
        dom = [SnapshotDomNode(**item) for item in raw.get("dom", [])]
        return Snapshot(
            url=str(getattr(entry.page, "url", "") or ""),
            title=str(raw.get("title", "") or ""),
            dom=dom or None,
            domTruncated=bool(raw.get("domTruncated")),
            elements=elements,
            truncated=bool(raw.get("truncated")),
        )

    async def _locator_for_ref(self, entry: TabEntry, ref: str) -> Any:
        """ref → locator（快照句柄定位；失效时抛 ref_not_found）。"""
        if not ref:
            raise BrowserCommandFailure("execution_error", "ref 操作需要非空 ref")
        locator = entry.page.locator(REF_LOCATOR_TEMPLATE.format(ref=ref))
        count = await locator.count()
        if count == 0:
            raise BrowserCommandFailure(
                "ref_not_found",
                f"快照句柄 '{ref}' 已失效（页面可能已刷新）。请重新执行 snapshot / get_visible_dom。",
            )
        return locator

    async def _resolve_point(
        self, entry: TabEntry, *, ref: str | None, point: dict[str, Any] | None
    ) -> tuple[float, float]:
        """ref 或坐标 → 视口坐标点（ref 取元素包围盒中心）。"""
        if ref:
            locator = await self._locator_for_ref(entry, str(ref))
            box = await locator.bounding_box()
            if box is None:
                raise BrowserCommandFailure("execution_error", f"元素 '{ref}' 不可见，无法定位坐标")
            return float(box["x"] + box["width"] / 2), float(box["y"] + box["height"] / 2)
        if point is None:
            raise BrowserCommandFailure("execution_error", "需要 ref 或坐标点 (x, y)")
        return float(point["x"]), float(point["y"])

    async def _element_payload_at(
        self, entry: TabEntry, x: float, y: float, include_non_interactable: bool
    ) -> dict[str, Any] | None:
        """查询坐标命中的元素并注入 ref；无命中时 None。"""
        script = _ELEMENT_AT_SCRIPT.replace("__SELECTOR__", _SNAPSHOT_SELECTOR)
        payload: dict[str, Any] | None = await entry.page.evaluate(
            script, {"x": x, "y": y, "includeNonInteractable": include_non_interactable}
        )
        return payload

    async def _inject_ref_at_point(self, entry: TabEntry, x: float, y: float) -> str:
        payload = await self._element_payload_at(entry, x, y, include_non_interactable=True)
        if payload is None:
            raise BrowserCommandFailure("execution_error", f"坐标 ({x}, {y}) 未命中任何元素")
        return str(payload["ref"])


class _EventWaiter:
    """waitForEvent 等待句柄（download / filechooser 共用）。"""

    def __init__(self, waiter_id: str, kind: str) -> None:
        self.waiter_id = waiter_id
        self.kind = kind
        self.entered: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()
        self.event: asyncio.Event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.payload: Any = None
        self.error: Exception | None = None
        self.is_multiple: bool = False


# --- 模块级辅助 ---


def _is_playwright_timeout(exc: Exception) -> bool:
    """判定是否为 Playwright 超时异常（模块导入失败时按非超时处理）。"""
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        return isinstance(exc, PlaywrightTimeoutError)
    except ImportError:
        return False


def _require_point(command: dict[str, Any]) -> tuple[float, float]:
    """命令级坐标参数抽取（click/hover 等的 ref 与 (x,y) 二选一语义）。"""
    x = command.get("x")
    y = command.get("y")
    if x is None or y is None:
        raise BrowserCommandFailure("execution_error", "需要 ref 或坐标 (x, y)")
    return float(x), float(y)


async def _press_with_modifiers(keyboard: Any, key: str, modifiers: list[str] | None) -> None:
    """带修饰键的按键：修饰键 down → press → up（保持顺序）。"""
    normalized = [_normalize_modifier(m) for m in (modifiers or [])]
    for modifier in normalized:
        await keyboard.down(modifier)
    try:
        await keyboard.press(_normalize_modifier(key))
    finally:
        for modifier in reversed(normalized):
            await keyboard.up(modifier)


async def _mouse_drag(
    page: Any, path: list[tuple[float, float]], modifiers: list[str] | None
) -> None:
    """合成鼠标拖拽：逐点移动（协议要求 backend 必须逐点发送而不是只取首尾）。"""
    if not path:
        raise BrowserCommandFailure("execution_error", "drag requires a non-empty path")
    normalized = [_normalize_modifier(m) for m in (modifiers or [])]
    keyboard = page.keyboard
    for modifier in normalized:
        await keyboard.down(modifier)
    try:
        await page.mouse.move(path[0][0], path[0][1])
        await page.mouse.down()
        for x, y in path[1:]:
            await page.mouse.move(x, y, steps=4)
        await page.mouse.up()
    finally:
        for modifier in reversed(normalized):
            await keyboard.up(modifier)


async def _set_window_minimized(browser: ManagedBrowser, *, minimized: bool) -> None:
    """headed 模式下最小化/恢复浏览器窗口（CDP Browser.setWindowBounds）。"""
    if browser.driver_browser is None:
        return
    cdp = await browser.driver_browser.new_browser_cdp_session()
    window = await cdp.send("Browser.getWindowForTarget")
    await cdp.send(
        "Browser.setWindowBounds",
        {"windowId": window["windowId"], "bounds": {"windowState": "minimized" if minimized else "normal"}},
    )


def _to_base64(raw: bytes | bytearray) -> str:
    return base64.b64encode(bytes(raw)).decode("ascii")


# --- 页面注入脚本 ---
# 说明：脚本以字符串常量维护（保持宿主单包分发；不引入前端构建链），
# 参数经 evaluate 的 arg 注入，避免拼接用户输入。ref 属性名与 session.REF_ATTRIBUTE
# 保持一致（脚本内无法跨进程共享常量，需人工对齐）。

_SNAPSHOT_SCRIPT = """
(arg) => {
  const MAX_ELEMENTS = arg.maxElements || 300;
  const INCLUDE_HIDDEN = arg.includeHidden || false;
  const ATTRS = ['id', 'name', 'type', 'href', 'placeholder', 'aria-label', 'title', 'value'];
  const REF_ATTR = 'data-illusion-ref';
  document.querySelectorAll('[' + REF_ATTR + ']').forEach((el) => el.removeAttribute(REF_ATTR));
  const nodes = Array.from(document.querySelectorAll('__SELECTOR__'));
  const elements = [];
  let counter = 0;
  let truncated = false;
  const vw = window.innerWidth, vh = window.innerHeight;
  const bestSelector = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const testId = el.getAttribute('data-testid');
    if (testId) return '[data-testid="' + testId + '"]';
    const name = el.getAttribute('name');
    if (name) return el.tagName.toLowerCase() + '[name="' + name + '"]';
    return el.tagName.toLowerCase();
  };
  const bestXpath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement && parts.length < 12) {
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) { if (sibling.tagName === node.tagName) index++; sibling = sibling.previousElementSibling; }
      parts.unshift(node.tagName.toLowerCase() + '[' + index + ']');
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  };
  const inView = (rect) => rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw;
  for (const el of nodes) {
    if (elements.length >= MAX_ELEMENTS) { truncated = true; break; }
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 && rect.height <= 0) continue;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (!INCLUDE_HIDDEN && parseFloat(style.opacity || '1') === 0) continue;
    const ref = 'n' + (++counter);
    el.setAttribute(REF_ATTR, ref);
    const text = (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
    const attributes = {};
    for (const attr of ATTRS) {
      const v = el.getAttribute(attr);
      if (v) attributes[attr] = v;
    }
    const entry = {
      ref: ref,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      name: (el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').slice(0, 160) || undefined,
      text: text || undefined,
      value: (typeof el.value === 'string') ? el.value.slice(0, 160) : undefined,
      disabled: el.disabled === true ? true : undefined,
      checked: typeof el.checked === 'boolean' ? el.checked : undefined,
      selector: bestSelector(el),
      xpath: bestXpath(el),
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      inViewport: inView(rect),
      attributes: Object.keys(attributes).length ? attributes : undefined
    };
    elements.push(entry);
  }
  // 语义 DOM 骨架（有界）：landmark/标题等，供模型先读页面语义再看动作细节
  const domNodes = [];
  let domTruncated = false;
  for (const el of Array.from(document.querySelectorAll('__DOM_SELECTOR__'))) {
    if (domNodes.length >= 150) { domTruncated = true; break; }
    const rect = el.getBoundingClientRect();
    let depth = 0;
    let node = el;
    while (node && node !== document.body) { depth++; node = node.parentElement; }
    const headingText = /^H[1-6]$/.test(el.tagName)
      ? (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200) : undefined;
    domNodes.push({
      tag: el.tagName.toLowerCase(),
      depth: Math.min(24, depth),
      inViewport: inView(rect),
      role: el.getAttribute('role') || undefined,
      name: (el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 120) || undefined,
      text: headingText
    });
  }
  return {
    title: document.title || '',
    elements: elements,
    truncated: truncated,
    dom: domNodes,
    domTruncated: domTruncated
  };
}
"""

_ELEMENT_AT_SCRIPT = """
(arg) => {
  const ATTRS = ['id', 'name', 'type', 'href', 'placeholder', 'aria-label', 'title', 'value'];
  const REF_ATTR = 'data-illusion-ref';
  const INTERACTIVE = '__SELECTOR__';
  let el = document.elementFromPoint(arg.x, arg.y);
  if (!el) return null;
  if (!arg.includeNonInteractable) {
    let current = el;
    while (current && current !== document.body) {
      if (current.matches && current.matches(INTERACTIVE)) { el = current; break; }
      current = current.parentElement;
    }
  }
  const rect = el.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const existing = el.getAttribute ? el.getAttribute(REF_ATTR) : null;
  const ref = existing || ('n' + Math.random().toString(36).slice(2, 10));
  el.setAttribute(REF_ATTR, ref);
  const attributes = {};
  for (const attr of ATTRS) {
    const v = el.getAttribute(attr);
    if (v) attributes[attr] = v;
  }
  const text = (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
  return {
    ref: ref,
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role') || el.tagName.toLowerCase(),
    name: (el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').slice(0, 160) || undefined,
    text: text || undefined,
    value: (typeof el.value === 'string') ? el.value.slice(0, 160) : undefined,
    disabled: el.disabled === true ? true : undefined,
    checked: typeof el.checked === 'boolean' ? el.checked : undefined,
    selector: el.id ? '#' + CSS.escape(el.id) : el.tagName.toLowerCase(),
    xpath: '/' + el.tagName.toLowerCase(),
    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    inViewport: rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw,
    attributes: Object.keys(attributes).length ? attributes : undefined
  };
}
"""
