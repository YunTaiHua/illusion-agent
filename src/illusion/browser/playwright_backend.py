"""
Playwright 托管浏览器后端
=========================

CLI/Web 模式的托管浏览器实现：
用 Playwright 启动托管 Chromium（或系统 Chrome/Edge 兜底），每会话一个
browser + context，tabs 即 pages。Web 版与 CLI 模式使用此后端。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

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
from illusion.browser.executable import resolve_launch_kwargs

logger = logging.getLogger(__name__)

# 操作超时预算（常规 3000ms，导航放宽）
ACTION_TIMEOUT_MS = 3000
NAVIGATE_TIMEOUT_MS = 15000


class PlaywrightBackend(BrowserBackend):
    """基于 Playwright 的托管浏览器。"""

    def __init__(self, kernel: str = "auto", headless: bool = True,
                 viewport_width: int = DEFAULT_VIEWPORT_WIDTH,
                 viewport_height: int = DEFAULT_VIEWPORT_HEIGHT,
                 proxy: str = "auto") -> None:
        self._kernel = kernel
        self._headless = headless
        self._viewport = (viewport_width, viewport_height)
        self._proxy_setting = proxy
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._pages: dict[str, Any] = {}
        self._pick_pages: dict[str, Any] = {}
        self._counter = 0
        self._active_id: str | None = None
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> str:
        return "managed"

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise BrowserCommandError(
                    "Playwright import failed (it is a required dependency; reinstall with "
                    "`pip install illusion-agent`). Chromium kernel missing? "
                    "Run `illusion browser setup` once."
                ) from exc
            self._pw = await async_playwright().start()
            kwargs = await resolve_launch_kwargs(self._pw, self._kernel)
            launch_kwargs: dict[str, Any] = {
                "headless": self._headless,
                "args": ["--no-first-run", "--no-default-browser-check"],
            }
            launch_kwargs.update(kwargs)
            # 无头 Chromium 不继承 Windows 系统代理，按配置显式接入
            # （auto=系统代理/env 自动探测；失败时静默直连）
            from illusion.browser.executable import resolve_proxy
            proxy = resolve_proxy(self._proxy_setting)
            if proxy:
                launch_kwargs["proxy"] = proxy
            try:
                self._browser = await self._pw.chromium.launch(**launch_kwargs)
            except Exception as exc:
                await self._teardown()
                raise BrowserCommandError(f"Failed to launch browser: {exc}") from exc
            self._context = await self._browser.new_context(
                viewport={"width": self._viewport[0], "height": self._viewport[1]},
                device_scale_factor=1,
            )
            # popup 收编挂在逐页 "popup" 事件上（new_tab 建页时注册）：
            # 不用 context.on("page")——它对 new_page() 创建的页面同样触发，
            # 与 new_tab 的注册存在竞态，会导致同一页面双重编号

    async def _teardown(self) -> None:
        # 显式标注可调用签名：lambda 在该上下文中获得类型，调用点不再是
        # untyped call（Playwright close/stop 返回 coroutine 或 None）
        closers: list[Callable[[], Awaitable[object] | None]] = [
            lambda: self._context.close() if self._context else None,
            lambda: self._browser.close() if self._browser else None,
            lambda: self._pw.stop() if self._pw else None,
        ]
        for closer in closers:
            try:
                result = closer()
                if result is not None:
                    await result
            except Exception:
                logger.debug("[browser] teardown close failed", exc_info=True)
        self._context = None
        self._browser = None
        self._pw = None
        self._pages.clear()
        self._active_id = None

    async def aclose(self) -> None:
        async with self._lock:
            await self._teardown()

    async def _adopt_page(self, page: Any) -> None:
        # 双保险：已被 new_tab 注册的页面跳过（防御未来重新引入 context 级订阅）
        if any(existing is page for existing in self._pages.values()):
            return
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
        except Exception:
            logger.debug("[browser] adopt page load-wait failed", exc_info=True)
        self._counter += 1
        tab_id = f"t{self._counter}"
        self._pages[tab_id] = page
        self._active_id = tab_id
        page.on("dialog", lambda d: asyncio.ensure_future(self._auto_dismiss(d)))
        # 嵌套 popup 继续收编
        page.on("popup", lambda p: asyncio.ensure_future(self._adopt_page(p)))

    async def _auto_dismiss(self, dialog: Any) -> None:
        """自动关闭原生对话框（alert/confirm/prompt），避免卡死页面。"""
        try:
            await dialog.dismiss()
        except Exception:
            logger.debug("[browser] dialog dismiss failed", exc_info=True)

    # ------------------------------------------------------------------
    # Tab 管理
    # ------------------------------------------------------------------
    def _tab_info(self, tab_id: str, active: bool) -> TabInfo:
        page = self._pages[tab_id]
        return TabInfo(
            id=tab_id,
            url=page.url or "about:blank",
            title="",  # 标题按需异步获取，list 中统一补齐
            active=active,
        )

    async def _tab_info_async(self, tab_id: str, active: bool) -> TabInfo:
        info = self._tab_info(tab_id, active)
        page = self._pages[tab_id]
        try:
            info.title = await page.title()
        except Exception:  # noqa: BLE001
            info.title = ""
        # 导航边界（CDP Page.getNavigationHistory，精确到当前条目位置）
        try:
            cdp = await page.context.new_cdp_session(page)
            nav = await cdp.send("Page.getNavigationHistory")
            await cdp.detach()
            idx = nav.get("currentIndex", 0)
            entries = nav.get("entries", [])
            info.can_go_back = idx > 0
            info.can_go_forward = idx < len(entries) - 1
        except Exception:
            logger.debug("[browser] navigation history query failed", exc_info=True)
        return info

    async def list_tabs(self) -> list[TabInfo]:
        return [await self._tab_info_async(tid, tid == self._active_id) for tid in self._pages]

    async def new_tab(self, url: str | None = None, wait_load: bool = True) -> TabInfo:
        await self.start()
        page = await self._context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(self._auto_dismiss(d)))
        # popup → 自动收编为受控 tab
        page.on("popup", lambda p: asyncio.ensure_future(self._adopt_page(p)))
        self._counter += 1
        tab_id = f"t{self._counter}"
        self._pages[tab_id] = page
        self._active_id = tab_id
        if url and wait_load:
            await self.navigate(tab_id, url)
        return await self._tab_info_async(tab_id, True)

    async def close_tab(self, tab_id: str) -> None:
        page = self._pages.get(tab_id)
        if page is None:
            raise BrowserCommandError(f"Unknown tab: {tab_id}")
        self._pages.pop(tab_id, None)
        try:
            await page.close()
        except Exception:
            logger.debug("[browser] page close failed", exc_info=True)
        if self._active_id == tab_id:
            self._active_id = next(iter(self._pages), None)

    async def select_tab(self, tab_id: str) -> TabInfo:
        if tab_id not in self._pages:
            raise BrowserCommandError(f"Unknown tab: {tab_id}")
        self._active_id = tab_id
        try:
            await self._pages[tab_id].bring_to_front()
        except Exception:
            logger.debug("[browser] bring_to_front failed", exc_info=True)
        return await self._tab_info_async(tab_id, True)

    async def get_active_tab(self) -> TabInfo:
        await self.start()
        if not self._pages:
            # 零 tab 不再自动建空白（报告：自动启动闪 about:blank）——
            # 导航类入口（browser_navigate / 面板 URL 栏）会带 URL 直建 tab
            raise BrowserCommandError(
                "No open tabs. Use browser_navigate to open a page first "
                "(it creates a tab with the URL directly)."
            )
        if self._active_id is None or self._active_id not in self._pages:
            self._active_id = next(iter(self._pages))
        return await self._tab_info_async(self._active_id, True)

    def _page(self, tab_id: str) -> Any:
        page = self._pages.get(tab_id)
        if page is None:
            raise BrowserCommandError(f"Unknown tab: {tab_id}")
        return page

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------
    async def _navigate_page(self, page: Any, url: str) -> TabInfo:
        clean = validate_url(url)
        try:
            await page.goto(clean, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
        except Exception as exc:
            raise BrowserCommandError(f"Navigation failed: {exc}") from exc
        return TabInfo(
            id=self._active_id or "",
            url=page.url or clean,
            title=await self._safe_title(page),
            active=True,
        )

    async def _safe_title(self, page: Any) -> str:
        try:
            return str(await page.title())
        except Exception:  # noqa: BLE001
            return ""

    async def navigate(self, tab_id: str, url: str) -> TabInfo:
        page = self._page(tab_id)
        self._active_id = tab_id
        info = await self._navigate_page(page, url)
        info.id = tab_id
        return info

    async def reload(self, tab_id: str) -> TabInfo:
        page = self._page(tab_id)
        try:
            await page.reload(wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
        except Exception as exc:
            raise BrowserCommandError(f"Reload failed: {exc}") from exc
        return TabInfo(id=tab_id, url=page.url, title=await self._safe_title(page), active=True)

    async def go_back(self, tab_id: str) -> TabInfo:
        page = self._page(tab_id)
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
        except Exception as exc:
            raise BrowserCommandError(f"Back failed: {exc}") from exc
        return TabInfo(id=tab_id, url=page.url, title=await self._safe_title(page), active=True)

    async def go_forward(self, tab_id: str) -> TabInfo:
        page = self._page(tab_id)
        try:
            await page.go_forward(wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
        except Exception as exc:
            raise BrowserCommandError(f"Forward failed: {exc}") from exc
        return TabInfo(id=tab_id, url=page.url, title=await self._safe_title(page), active=True)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _ref_locator(self, page: Any, ref: str) -> Any:
        clean = (ref or "").strip()
        if not clean.startswith("e") or not clean[1:].isdigit():
            raise BrowserCommandError(
                f"Invalid ref: {ref!r}. Run browser_snapshot and use a [ref=eN] from it."
            )
        return page.locator(f'[data-illusion-ref="{clean}"]')

    async def _resolve_ref_target(self, page: Any, ref: str | None) -> tuple[Any | None, Any | None]:
        """把 ref 解析为 locator；ref 无效/过期时返回 (None, 错误)。"""
        if not ref:
            return None, None
        locator = self._ref_locator(page, ref)
        try:
            count = await locator.count()
        except Exception as exc:
            raise BrowserCommandError(f"Ref lookup failed: {exc}") from exc
        if count == 0:
            raise BrowserCommandError(
                f"Ref {ref} not found (stale snapshot). Run browser_snapshot again and use a current ref."
            )
        if count > 1:
            raise BrowserCommandError(
                f"Ref {ref} matched {count} elements. Run browser_snapshot again."
            )
        return locator, None

    async def click(self, tab_id: str, ref: str | None = None, x: float | None = None,
                    y: float | None = None, double: bool = False) -> None:
        page = self._page(tab_id)
        try:
            if ref:
                locator, _ = await self._resolve_ref_target(page, ref)
                assert locator is not None
                await locator.click(timeout=ACTION_TIMEOUT_MS, click_count=2 if double else 1)
            elif x is not None and y is not None:
                # 坐标路径（视觉兜底：canvas/自绘控件）
                if double:
                    await page.mouse.dblclick(x, y)
                else:
                    await page.mouse.click(x, y)
            else:
                raise BrowserCommandError("click requires either ref or x/y coordinates")
        except BrowserCommandError:
            raise
        except Exception as exc:
            raise BrowserCommandError(f"Click failed: {exc}") from exc

    async def hover(self, tab_id: str, x: float, y: float) -> None:
        """把鼠标移到页面坐标 (x, y)。

        拾取模式先悬停再点击：页面选择器脚本依赖 mousemove 记录 hovered
        元素，缺少悬停时首击会被判为取消（拾取"点一次就没反应"的根因之一）。
        """
        page = self._page(tab_id)
        try:
            await page.mouse.move(x, y)
        except Exception as exc:
            raise BrowserCommandError(f"Hover failed: {exc}") from exc

    async def _assert_typeable(self, page: Any, ref: str) -> None:
        """校验 ref 指向可输入元素，防止"Typed N chars"假成功。

        对 link/button 等非可输入元素：聚焦点击可能触发导航，文本静默
        丢失——直接拒绝并指引改用 browser_click。
        """
        info = await page.evaluate(
            """(sel) => {
              const el = document.querySelector(sel);
              if (!el) return { exists: false };
              const tag = el.tagName.toLowerCase();
              const editable = el.isContentEditable
                || tag === 'textarea' || tag === 'select'
                || (tag === 'input'
                    && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden');
              const disabled = el.disabled === true
                || el.getAttribute('aria-disabled') === 'true';
              return { exists: true, tag, editable, disabled,
                       type: el.getAttribute('type') || '' };
            }""",
            f'[data-illusion-ref="{ref}"]',
        )
        if not isinstance(info, dict) or not info.get("exists"):
            return  # 元素消失：交给后续 click/insert 自然失败
        if info.get("disabled"):
            raise BrowserCommandError(
                f"Ref {ref} points to a disabled {info.get('tag')} element — typing is not possible. "
                "Check the page state with browser_snapshot."
            )
        if not info.get("editable"):
            tag_name = str(info.get("tag", "element"))
            type_attr = str(info.get("type") or "")
            raise BrowserCommandError(
                f"Ref {ref} points to a non-typeable <{tag_name}> element"
                + (f" (type={type_attr})" if type_attr else "")
                + ". browser_type only accepts input/textarea/select/contenteditable targets; "
                  "use browser_click for links and buttons."
            )

    async def type_text(self, tab_id: str, text: str, ref: str | None = None,
                        submit: bool = False) -> str:
        """输入文本，返回人类可读的效果说明（供工具输出展示语义）。

        用 keyboard.type（真实逐键事件）而非 insert_text：真实按键与用户
        输入行为一致——存在选区时**替换**选中文本（报告 P1-1：insert_text
        不消费选区，全选后输入仍追加）。返回 note 描述发生的是替换、
        键入还是追加（报告 P2-1）。
        """
        page = self._page(tab_id)
        try:
            if ref:
                locator, _ = await self._resolve_ref_target(page, ref)
                assert locator is not None
                await self._assert_typeable(page, (ref or "").strip())
                # 已聚焦则跳过聚焦（保住既有选区——select-all 后输入=替换）；
                # 未聚焦用 focus() 而非 click()：点击会折叠选区（报告 P1-1
                # 第二轮：选区 selStart:0/selEnd:11 正确但点击后仍追加）。
                # （focus 不折叠选区，点击会）
                focus_state = await page.evaluate(
                    """(selQ) => {
                      const el = document.querySelector(selQ);
                      if (!el) return { focused: false, hasSel: false };
                      const active = document.activeElement;
                      const focused = active === el;
                      let hasSel = false;
                      if (focused) {
                        if (typeof el.selectionStart === 'number') {
                          hasSel = el.selectionStart !== el.selectionEnd;
                        } else {
                          const s = window.getSelection();
                          hasSel = !!s && !s.isCollapsed;
                        }
                      }
                      return { focused, hasSel };
                    }""",
                    f'[data-illusion-ref="{ref}"]',
                )
                if not (isinstance(focus_state, dict) and focus_state.get("focused")):
                    await locator.focus(timeout=ACTION_TIMEOUT_MS)
            else:
                # 无 ref：追加到当前聚焦元素（若有）
                focused = await page.evaluate(
                    "() => { const el = document.activeElement;"
                    " return el ? (el.tagName.toLowerCase() + '|' +"
                    " (el.isContentEditable || el.tagName.toLowerCase() === 'textarea'"
                    "  || el.tagName.toLowerCase() === 'select'"
                    "  || (el.tagName.toLowerCase() === 'input'"
                    "      && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden')))"
                    " : 'none|false'; }"
                )
                tag, editable = (focused.split("|", 1) + ["false"])[:2]
                if editable != "true":
                    raise BrowserCommandError(
                        f"No focused editable element (active element is <{tag}>). "
                        "Pass a ref from browser_snapshot to focus a textbox first "
                        "(note: typing without ref APPENDS to the current value)."
                    )
            # 键入前状态：选区是否非折叠 / 现有值长度（用于 note）
            pre = await page.evaluate(
                """() => {
                  const el = document.activeElement;
                  if (!el) return { sel: false, len: -1, tag: 'none' };
                  const tag = el.tagName.toLowerCase();
                  if (typeof el.selectionStart === 'number') {
                    return { sel: el.selectionStart !== el.selectionEnd, len: el.value.length, tag };
                  }
                  const s = window.getSelection();
                  return { sel: !!s && !s.isCollapsed, len: -1, tag };
                }"""
            )
            await page.keyboard.type(text)
            if submit:
                await page.keyboard.press("Enter")
            if pre.get("sel"):
                return f"replaced selection with {len(text)} chars"
            if not ref:
                length = pre.get("len", -1)
                if isinstance(length, int) and length > 0:
                    return (
                        f"appended {len(text)} chars to existing value ({length} chars) — "
                        "to replace the whole value: focus the field (browser_type with ref), "
                        "press Control+a (browser_press_key), then type again"
                    )
                return f"typed {len(text)} chars into focused <{pre.get('tag', 'element')}>"
            return f"typed {len(text)} chars into <{pre.get('tag', 'element')}>"
        except BrowserCommandError:
            raise
        except Exception as exc:
            raise BrowserCommandError(f"Type failed: {exc}") from exc

    async def press_key(self, tab_id: str, key: str) -> None:
        page = self._page(tab_id)
        # select-all 语义键：必须在可聚焦目标上才有全选效果；页面 body 上
        # 按 Control+a 在多数站点静默无效（报告 P1.1）。这里先聚焦 body，
        # 若目标元素已聚焦则不动——由工具层校验聚焦状态并给出告警。
        if key.lower() in ("control+a", "meta+a", "control+a.", "ctrl+a"):
            focused_editable = await page.evaluate(
                "() => { const el = document.activeElement;"
                " if (!el) return false;"
                " const tag = el.tagName.toLowerCase();"
                " return el.isContentEditable || tag === 'textarea'"
                "  || (tag === 'input'"
                "      && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden'); }"
            )
            if not focused_editable:
                raise BrowserCommandError(
                    "select-all (Control+a) requires a focused editable element "
                    "(textbox/textarea/contenteditable); the active element is not editable, "
                    "so the selection would be silently lost. Focus an element first "
                    "(browser_type with ref, or browser_click on the field)."
                )
        try:
            await page.keyboard.press(key)
        except Exception as exc:
            raise BrowserCommandError(f"Press key failed: {exc}") from exc

    async def scroll(self, tab_id: str, x: float, y: float, dx: float, dy: float) -> str:
        """滚动页面，返回效果说明；完全无效时报错（不留假成功）。

        路径：wheel（原生滚轮，可作用于光标下的内嵌滚动容器）→ 验证文档
        是否位移 → 未动则 scrollBy 兜底（文档级滚动；无头 Chromium 的
        水平滚轮常被忽略，报告 P1-3）→ 再不动则报错说明该方向不可滚。
        """
        page = self._page(tab_id)
        try:
            before = await page.evaluate(
                "() => ({ x: window.scrollX, y: window.scrollY })"
            )
            await page.mouse.move(x, y)
            await page.mouse.wheel(dx, dy)
            await page.wait_for_timeout(150)
            after = await page.evaluate(
                "() => ({ x: window.scrollX, y: window.scrollY })"
            )
            moved = (abs(after.get("x", 0) - before.get("x", 0)) > 0.5
                     or abs(after.get("y", 0) - before.get("y", 0)) > 0.5)
            if not moved:
                # 兜底：文档级滚动（wheel 对无头 Chromium 的水平分量常静默无效）
                await page.evaluate(
                    "(d) => window.scrollBy(d.dx, d.dy)", {"dx": dx, "dy": dy}
                )
                await page.wait_for_timeout(100)
                after = await page.evaluate(
                    "() => ({ x: window.scrollX, y: window.scrollY })"
                )
                moved = (abs(after.get("x", 0) - before.get("x", 0)) > 0.5
                         or abs(after.get("y", 0) - before.get("y", 0)) > 0.5)
            if not moved:
                direction = "horizontal" if abs(dx) >= abs(dy) else "vertical"
                raise BrowserCommandError(
                    f"Scroll had no effect: the page did not move in the {direction} "
                    f"direction (dx={dx}, dy={dy}); there is likely nothing to scroll "
                    f"here. Verify the page state with browser_snapshot instead of retrying."
                )
            return f"scrolled to ({after.get('x', 0):.0f}, {after.get('y', 0):.0f})"
        except BrowserCommandError:
            raise
        except Exception as exc:
            raise BrowserCommandError(f"Scroll failed: {exc}") from exc

    # ------------------------------------------------------------------
    # 观察
    # ------------------------------------------------------------------
    async def screenshot(self, tab_id: str, full_page: bool = False) -> ScreenshotData:
        page = self._page(tab_id)
        try:
            if full_page:
                # full_page 截图会把视口临时拉到整页高度且不自动还原——
                # 之后所有坐标点击/悬停全部错位（视口跟随断掉根因）。
                # 截完立即恢复原视口尺寸。
                vp = page.viewport_size
                data = await page.screenshot(type="jpeg", quality=80, full_page=True)
                if vp:
                    await page.set_viewport_size(vp)
            else:
                data = await page.screenshot(type="jpeg", quality=80)
        except Exception as exc:
            raise BrowserCommandError(f"Screenshot failed: {exc}") from exc
        return ScreenshotData(data=data, mime="image/jpeg")

    async def snapshot(self, tab_id: str) -> dict[str, object]:
        from illusion.browser.snapshot import SNAPSHOT_SCRIPT
        page = self._page(tab_id)
        try:
            result = await page.evaluate(SNAPSHOT_SCRIPT)
        except Exception as exc:
            raise BrowserCommandError(f"Snapshot failed: {exc}") from exc
        return dict(result)

    async def evaluate(self, tab_id: str, expression: str) -> str:
        page = self._page(tab_id)
        try:
            # undefined 语义保留（报告 P1.4，含第二轮的嵌套场景）：
            # page.evaluate 经 CDP/JSON 序列化，undefined 顶层变 None、
            # 对象内属性被静默丢弃——与 null 无法区分。方案：JS 侧递归把
            # undefined 值（含嵌套）包装为哨兵对象，Python 侧把哨兵还原为
            # 字面量 `undefined` 文本。Promise.resolve 兼容同步值与异步结果。
            result = await page.evaluate(
                "(async () => {"
                " const U = '__illusion_undef_7f3a__';"
                " const ser = (v) => {"
                "  if (v === undefined) { const o = {}; o[U] = 1; return o; }"
                "  if (v === null || typeof v !== 'object') return v;"
                "  if (typeof v.toJSON === 'function') return ser(v.toJSON());"
                "  if (Array.isArray(v)) return v.map(ser);"
                "  const o = {};"
                "  for (const k of Object.keys(v)) o[k] = ser(v[k]);"
                "  return o;"
                " };"
                " const __v = await Promise.resolve(" + _wrap_expression(expression) + ");"
                " return ser(__v);"
                " })()"
            )
        except Exception as exc:
            raise BrowserCommandError(f"Evaluate failed: {exc}") from exc
        return _render_eval_result(result)

    async def resize(self, tab_id: str, width: int, height: int) -> tuple[int, int]:
        page = self._page(tab_id)
        w, h = clamp_viewport(width, height)
        try:
            await page.set_viewport_size({"width": w, "height": h})
        except Exception as exc:
            raise BrowserCommandError(f"Resize failed: {exc}") from exc
        self._viewport = (w, h)
        return (w, h)

    async def wait(self, tab_id: str, seconds: float, load: bool = False) -> None:
        page = self._page(tab_id)
        seconds = max(0.0, min(seconds, 30.0))
        try:
            if load:
                # load 等待以 seconds 为超时上限；到达后若还显式给了 seconds
                # 延时需求（工具层 seconds_load 语义）由调用方二次调用。
                await page.wait_for_load_state("domcontentloaded", timeout=int(seconds * 1000))
            else:
                await page.wait_for_timeout(int(seconds * 1000))
        except Exception as exc:
            raise BrowserCommandError(f"Wait failed: {exc}") from exc

    async def pick_script(self, tab_id: str, script: str) -> dict[str, object]:
        """注入元素拾取脚本并等待用户操作完成（最长 180s，可经 pick_cancel 取消）。"""
        page = self._page(tab_id)
        self._pick_pages[tab_id] = page
        try:
            result = await asyncio.wait_for(
                page.evaluate(script), timeout=180.0,
            )
        except asyncio.TimeoutError as exc:
            raise BrowserCommandError(
                "Element picking timed out (180s). Use browser_snapshot instead."
            ) from exc
        finally:
            self._pick_pages.pop(tab_id, None)
        if not isinstance(result, dict):
            raise BrowserCommandError("Element picking returned an unexpected result.")
        return result

    async def pick_cancel(self, tab_id: str) -> None:
        from illusion.browser.snapshot import PICK_CANCEL_PAGE_SCRIPT
        page = self._pick_pages.get(tab_id) or self._page(tab_id)
        try:
            await page.evaluate(PICK_CANCEL_PAGE_SCRIPT)
        except Exception:
            logger.debug("[browser] pick cancel ignored", exc_info=True)

    async def pick(self, tab_id: str, x: float, y: float) -> dict[str, object]:
        """拾取页面坐标处的元素（网页元素选择器）。

        返回 tag/role/name/selector/ref；ref 写入 data-illusion-ref，
        agent 后续可直接用 browser_click/browser_type 操作该元素。
        """
        page = self._page(tab_id)
        try:
            import json as _json

            from illusion.browser.snapshot import PICK_SCRIPT_TEMPLATE
            # evaluate 的字符串表达式不透传 arg：模板尾部的 IIFE 调用参数以
            # __PICK_XY_ARGS__ 占位，此处内联真实坐标 JSON
            args = _json.dumps({"x": float(x), "y": float(y)})
            script = PICK_SCRIPT_TEMPLATE.replace("__PICK_XY_ARGS__", args)
            result = await page.evaluate(script)
        except Exception as exc:
            raise BrowserCommandError(f"Pick failed: {exc}") from exc
        if not isinstance(result, dict) or not result:
            raise BrowserCommandError(
                f"No element at viewport point ({x:.0f}, {y:.0f})."
            )
        return result

    async def get_state(self) -> PageState:
        state = PageState(
            tabs=[await self._tab_info_async(tid, tid == self._active_id) for tid in self._pages],
            viewport_width=self._viewport[0],
            viewport_height=self._viewport[1],
        )
        return state


_IIFE_RE = re.compile(
    r"^\(\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>[\s\S]*\)\s*\(\s*\)\s*$"
)


def _wrap_expression(expression: str) -> str:
    """把用户表达式包成可 await 的形式。

    支持 `() => ...` 函数与裸表达式两种形态；裸表达式若以语句开头
    （return/const/let 等）则包 IIFE；已是 IIFE 的直通（避免二次包裹成
    `(() => ((() => x)()))()` 这类坏代码；桌面端同规则）。
    """
    expr = expression.strip()
    if _IIFE_RE.match(expr):
        return expr
    if expr.startswith(("()", "(async", "async")):
        return f"({expr})()"
    if re.match(r"^(return|const|let|var|if|for|while|try)\b", expr):
        return f"(() => {{ {expr} }})()"
    return f"(() => ({expr}))()"


def _render_eval_result(result: Any) -> str:
    """把 evaluate 的返回值文本化，哨兵对象还原为字面量 undefined。

    JS 侧 ser() 已把 undefined（含嵌套）包装为 {"__illusion_undef_7f3a__": 1}，
    此处 json.dumps 后把哨兵模式替换回 `undefined` 字面量——嵌套 undefined
    在文本输出中得以保留（JSON.stringify/CDP 会静默丢弃，报告 P1.4）。
    """
    if isinstance(result, str):
        return result
    import json

    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)
    return re.sub(r'\{\s*"__illusion_undef_7f3a__"\s*:\s*1\s*\}', "undefined", text)
