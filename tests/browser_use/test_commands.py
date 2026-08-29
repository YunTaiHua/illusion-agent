"""Browser Use 协议与命令执行器测试。

使用 FakePage / FakeLocator 模拟 Playwright 页面原语，逐命令验证协议信封：
成功载荷字段、错误码（ref_not_found / navigation_blocked / capability_unsupported）
与 elapsedMs 存在性——不启动真实浏览器。
"""

from __future__ import annotations

from typing import Any

import pytest

from illusion.browser_use.commands import BrowserCommandExecutor
from illusion.browser_use.protocol import BrowserCommandFailure
from illusion.browser_use.session import ManagedBrowser, TabEntry


class FakeLocator:
    """模拟 Playwright Locator（记录调用并返回可配置结果）。"""

    def __init__(self, page: FakePage, selector: str) -> None:
        self.page = page
        self.selector = selector
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def count(self) -> int:
        ref = self.selector.split('"')[1] if '"' in self.selector else None
        return 1 if ref is None or ref in self.page.refs else 0

    async def click(self, **kwargs: Any) -> None:
        self.calls.append(("click", kwargs))

    async def fill(self, value: str, **kwargs: Any) -> None:
        self.calls.append(("fill", {"value": value, **kwargs}))

    async def focus(self, **kwargs: Any) -> None:
        self.calls.append(("focus", kwargs))

    async def hover(self, **kwargs: Any) -> None:
        self.calls.append(("hover", kwargs))

    async def scroll_into_view_if_needed(self, **kwargs: Any) -> None:
        self.calls.append(("scroll_into_view_if_needed", kwargs))

    async def set_checked(self, checked: bool, **kwargs: Any) -> None:
        self.calls.append(("set_checked", {"checked": checked, **kwargs}))

    async def select_option(self, **kwargs: Any) -> None:
        self.calls.append(("select_option", kwargs))

    async def screenshot(self, **kwargs: Any) -> bytes:
        self.calls.append(("screenshot", kwargs))
        return b"png-bytes"

    async def bounding_box(self) -> dict[str, float]:
        return {"x": 10, "y": 20, "width": 30, "height": 40}

    @property
    def page_ref(self) -> FakePage:
        return self.page


class FakeKeyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def type(self, text: str) -> None:
        self.page.calls.append(("keyboard.type", text))

    async def press(self, key: str) -> None:
        self.page.calls.append(("keyboard.press", key))

    async def down(self, key: str) -> None:
        self.page.calls.append(("keyboard.down", key))

    async def up(self, key: str) -> None:
        self.page.calls.append(("keyboard.up", key))


class FakeMouse:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def click(self, x: float, y: float, **kwargs: Any) -> None:
        self.page.calls.append(("mouse.click", {"x": x, "y": y, **kwargs}))

    async def move(self, x: float, y: float, **kwargs: Any) -> None:
        self.page.calls.append(("mouse.move", {"x": x, "y": y, **kwargs}))

    async def wheel(self, scroll_x: float, scroll_y: float) -> None:
        self.page.calls.append(("mouse.wheel", {"x": scroll_x, "y": scroll_y}))

    async def down(self) -> None:
        self.page.calls.append(("mouse.down", {}))

    async def up(self) -> None:
        self.page.calls.append(("mouse.up", {}))


class FakePage:
    """模拟 Playwright Page（覆盖 executor 测试所需的最小原语集）。"""

    def __init__(self, url: str = "https://example.com/") -> None:
        self._url = url
        self.title_value = "Example"
        self.calls: list[tuple[str, Any]] = []
        self.refs: set[str] = set()
        self.evaluate_results: dict[str, Any] = {}
        self.locators: dict[str, FakeLocator] = {}
        self.viewport = {"width": 1280, "height": 720}
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)
        self.closed = False

    @property
    def url(self) -> str:
        return self._url

    def on(self, event: str, handler: Any) -> None:
        self.calls.append(("on", event))

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.calls.append(("goto", url))
        self._url = url

    async def reload(self, **kwargs: Any) -> None:
        self.calls.append(("reload", kwargs))

    async def go_back(self, **kwargs: Any) -> None:
        self.calls.append(("go_back", kwargs))

    async def go_forward(self, **kwargs: Any) -> None:
        self.calls.append(("go_forward", kwargs))

    async def bring_to_front(self) -> None:
        self.calls.append(("bring_to_front", None))

    async def title(self) -> str:
        return self.title_value

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.calls.append(("evaluate", script[:32]))
        for key, value in self.evaluate_results.items():
            if key in script:
                return value
        return None

    def locator(self, selector: str) -> FakeLocator:
        if selector not in self.locators:
            self.locators[selector] = FakeLocator(self, selector)
        return self.locators[selector]

    async def screenshot(self, **kwargs: Any) -> bytes:
        self.calls.append(("screenshot", kwargs))
        return b"png-bytes"

    async def set_viewport_size(self, size: dict[str, int]) -> None:
        self.viewport = dict(size)

    def viewport_size(self) -> dict[str, int]:
        return dict(self.viewport)

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        self.calls.append(("wait_for_selector", selector))

    def get_by_text(self, text: str) -> Any:
        class _TextLocator:
            async def wait_for(self, *, state: str, timeout: int) -> None:
                pass

        return _TextLocator()

    async def wait_for_url(self, url: str, **kwargs: Any) -> None:
        self.calls.append(("wait_for_url", url))

    async def wait_for_load_state(self, **kwargs: Any) -> None:
        self.calls.append(("wait_for_load_state", kwargs))

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    def on(self, event: str, handler: Any) -> None:
        pass

    async def new_page(self) -> FakePage:
        page = FakePage(url="")
        self.pages.append(page)
        return page

    async def new_cdp_session(self, page: Any) -> Any:
        class _Cdp:
            async def send(self, method: str, params: Any = None) -> dict[str, Any]:
                if method == "Page.getNavigationHistory":
                    return {"currentIndex": 1, "entries": [{}, {}, {}]}
                return {}

        return _Cdp()


class FakeManagedBrowser(ManagedBrowser):
    """注入 FakeContext 的受管浏览器（跳过真实 Playwright 启动）。"""

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._context = FakeContext()
        self._started = True

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


@pytest.fixture()
def browser_and_executor() -> tuple[FakeManagedBrowser, BrowserCommandExecutor]:
    from illusion.browser_use.config import BrowserSettings

    browser = FakeManagedBrowser(BrowserSettings(enabled=True))
    executor = BrowserCommandExecutor(browser)
    return browser, executor


def _make_tab(browser: ManagedBrowser, page: FakePage) -> TabEntry:
    entry = TabEntry(
        tabId="tab-1",
        page=page,
        viewport=browser.default_viewport,
        agent_owned=True,
    )
    browser.registry[entry.tabId] = entry
    return entry


# --- 信封基础 ---

async def test_envelope_always_has_ok_and_elapsed_ms(browser_and_executor) -> None:
    _, executor = browser_and_executor
    result = await executor.execute({"method": "list"})
    assert result["ok"] is True
    assert "elapsedMs" in result
    assert result["tabs"] == []


async def test_unknown_method_is_capability_unsupported(browser_and_executor) -> None:
    _, executor = browser_and_executor
    result = await executor.execute({"method": "teleport"})
    assert result["ok"] is False
    assert result["error"]["code"] == "capability_unsupported"


# --- Tab 生命周期 ---

async def test_new_tab_and_list(browser_and_executor) -> None:
    _browser, executor = browser_and_executor
    created = await executor.execute({"method": "newTab"})
    assert created["ok"] is True
    tab = created["tab"]
    assert tab["tabId"]
    assert tab["viewport"] == {"width": 1280, "height": 720}
    listing = await executor.execute({"method": "list"})
    assert [t["tabId"] for t in listing["tabs"]] == [tab["tabId"]]
    assert listing["tabs"][0]["active"] is True


async def test_activate_tab_unknown_id(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    result = await executor.execute({"method": "activateTab", "tabId": "missing"})
    assert result["ok"] is False
    assert result["error"]["code"] == "ref_not_found"


# --- 导航 ---

async def test_navigate_blocked_scheme(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    page = FakePage()
    _make_tab(browser, page)
    result = await executor.execute({"method": "navigate", "url": "file:///etc/passwd"})
    assert result["ok"] is False
    assert result["error"]["code"] == "navigation_blocked"
    assert not any(call[0] == "goto" for call in page.calls)


async def test_navigate_allows_http_and_about_blank(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    page = FakePage()
    _make_tab(browser, page)
    ok_http = await executor.execute({"method": "navigate", "url": "https://example.com/"})
    ok_blank = await executor.execute({"method": "navigate", "url": "about:blank"})
    assert ok_http["ok"] is True and ok_blank["ok"] is True


async def test_get_state_payload(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    page = FakePage(url="https://example.com/page")
    _make_tab(browser, page)
    result = await executor.execute({"method": "getState"})
    state = result["state"]
    assert state["url"] == "https://example.com/page"
    assert state["title"] == "Example"
    assert state["canGoBack"] is True
    assert state["canGoForward"] is True
    assert state["viewportWidth"] == 1280


# --- evaluate / screenshot / ref 交互 ---

async def test_evaluate_omits_undefined_value(browser_and_executor) -> None:
    """页面表达式返回 undefined/None 时信封省略 value（客户端得到 JS undefined）。"""
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    result = await executor.execute({"method": "evaluate", "expression": "1 + 1"})
    assert result["ok"] is True
    assert result.get("value") is None


async def test_evaluate_returns_value(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    page = FakePage()
    page.evaluate_results["answer"] = 42
    _make_tab(browser, page)
    result = await executor.execute({"method": "evaluate", "expression": "return answer"})
    assert result["ok"] is True
    assert result["value"] == 42


async def test_screenshot_payload_shape(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    result = await executor.execute({"method": "screenshot"})
    assert result["ok"] is True
    assert result["image"]["mimeType"] == "image/png"
    assert result["image"]["base64"]


async def test_ref_command_without_snapshot(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    result = await executor.execute({"method": "click", "ref": "n1"})
    assert result["ok"] is False
    assert result["error"]["code"] == "ref_not_found"


async def test_click_by_coordinates(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    page = FakePage()
    _make_tab(browser, page)
    result = await executor.execute({"method": "click", "x": 12, "y": 34})
    assert result["ok"] is True
    assert (
        "mouse.click",
        {"x": 12.0, "y": 34.0, "button": "left", "click_count": 1},
    ) in page.calls


async def test_click_double_click(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    page = FakePage()
    _make_tab(browser, page)
    result = await executor.execute({"method": "click", "x": 1, "y": 2, "doubleClick": True})
    assert result["ok"] is True
    assert page.calls[-1][1]["click_count"] == 2


# --- playwright 表面 ---

async def test_playwright_dom_snapshot(browser_and_executor) -> None:
    browser, executor = browser_and_executor

    class _AriaLocator:
        async def aria_snapshot(self) -> str:
            return "- document"

    page = FakePage()

    def locator(selector: str) -> _AriaLocator:
        return _AriaLocator()

    page.locator = locator  # type: ignore[method-assign]
    _make_tab(browser, page)
    result = await executor.execute(
        {"method": "playwright", "action": {"name": "domSnapshot"}}
    )
    assert result["ok"] is True
    assert result["value"] == "- document"


async def test_playwright_locator_operations(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    page = FakePage()
    _make_tab(browser, page)

    count = await executor.execute(
        {
            "method": "playwright",
            "action": {"name": "locator", "selector": "button", "operation": "count"},
        }
    )
    assert count["ok"] is True and count["value"] == 1

    click = await executor.execute(
        {
            "method": "playwright",
            "action": {"name": "locator", "selector": "button", "operation": "click"},
        }
    )
    assert click["ok"] is True

    unsupported = await executor.execute(
        {
            "method": "playwright",
            "action": {"name": "locator", "selector": "button", "operation": "setProperty"},
        }
    )
    assert unsupported["ok"] is False
    assert unsupported["error"]["code"] == "capability_unsupported"


async def test_playwright_wait_for_url_requires_url(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    result = await executor.execute(
        {"method": "playwright", "action": {"name": "waitForURL", "url": ""}}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "execution_error"


# --- 视口 / 录制 / 弹窗 ---

async def test_viewport_set_validates_bounds(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    too_small = await executor.execute(
        {"method": "browserViewportSet", "width": 10, "height": 10}
    )
    assert too_small["ok"] is False
    ok = await executor.execute(
        {"method": "browserViewportSet", "width": 800, "height": 600}
    )
    assert ok["ok"] is True


async def test_recording_unsupported_for_managed_chromium(browser_and_executor) -> None:
    _, executor = browser_and_executor
    result = await executor.execute({"method": "recordingStart"})
    assert result["ok"] is False
    assert result["error"]["code"] == "capability_unsupported"


async def test_get_dialog_null_when_pending(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    result = await executor.execute({"method": "getDialog"})
    assert result["ok"] is True
    assert "dialog" not in result  # 客户端 result.dialog ?? null


async def test_handle_dialog_without_pending(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    result = await executor.execute({"method": "handleDialog", "accept": True})
    assert result["ok"] is False
    assert result["error"]["code"] == "execution_error"


# --- 生命周期标记 ---

async def test_finalize_and_summary_shape(browser_and_executor) -> None:
    browser, executor = browser_and_executor
    _make_tab(browser, FakePage())
    await executor.execute({"method": "markDeliverable", "tabId": "tab-1"})
    result = await executor.execute({"method": "list"})
    assert result["tabs"][0]["lifecycle"] == "deliverable"


async def test_execution_error_folded_into_envelope(browser_and_executor) -> None:
    """命令内部异常 → execution_error 信封而非向外抛异常。"""
    browser, executor = browser_and_executor

    class _ExplodingPage:
        url = "https://example.com/"

        async def goto(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

    _make_tab(browser, _ExplodingPage())  # type: ignore[arg-type]
    result = await executor.execute({"method": "navigate", "url": "https://example.com/"})
    assert result["ok"] is False
    assert result["error"]["code"] == "execution_error"
    assert "boom" in result["error"]["message"]


def test_browser_command_failure_attributes() -> None:
    failure = BrowserCommandFailure("ref_not_found", "gone")
    assert failure.code == "ref_not_found"
    assert failure.side_effect_uncertain is False
