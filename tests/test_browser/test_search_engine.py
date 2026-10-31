"""无头浏览器搜索引擎（web_search 后端）单元测试。"""

from __future__ import annotations

import asyncio
import base64
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from illusion.browser import search_engine
from illusion.browser.base import BrowserCommandError
from illusion.browser.search_engine import (
    _current_state,
    _decode_bing_redirect,
    _resolve_result_url,
    _schedule_idle_close,
    aclose_search_browser,
    browser_search,
)

# ---------------------------------------------------------------------------
# Bing /ck/a 跳转解码
# ---------------------------------------------------------------------------


def _bing_ck_url(target: str) -> str:
    body = base64.urlsafe_b64encode(target.encode("utf-8")).decode("ascii").rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc&u=a1{body}&ntb=1"


class TestDecodeBingRedirect:
    def test_decodes_a1_base64url(self) -> None:
        target = "https://playwright.dev/python/docs/intro"
        assert _decode_bing_redirect(_bing_ck_url(target)) == target

    def test_plain_url_passthrough(self) -> None:
        assert _decode_bing_redirect("https://example.com/a") == "https://example.com/a"

    def test_non_ck_bing_url_passthrough(self) -> None:
        assert _decode_bing_redirect("https://www.bing.com/search?q=x") == (
            "https://www.bing.com/search?q=x"
        )

    def test_malformed_u_param_keeps_original(self) -> None:
        assert _decode_bing_redirect("https://www.bing.com/ck/a?u=a1%%%bad") == (
            "https://www.bing.com/ck/a?u=a1%%%bad"
        )

    def test_decoded_non_http_keeps_original(self) -> None:
        body = base64.urlsafe_b64encode(b"not a url").decode("ascii").rstrip("=")
        raw = f"https://www.bing.com/ck/a?u=a1{body}"
        assert _decode_bing_redirect(raw) == raw


class TestResolveResultUrl:
    def test_baidu_mu_attribute_wins(self) -> None:
        wrapper = "https://www.baidu.com/link?url=abc"
        assert _resolve_result_url(wrapper, "https://playwright.dev/") == "https://playwright.dev/"

    def test_baidu_wrapper_without_mu_keeps_wrapper(self) -> None:
        wrapper = "https://www.baidu.com/link?url=abc"
        assert _resolve_result_url(wrapper, "") == wrapper

    def test_bing_url_decoded_when_no_mu(self) -> None:
        target = "https://example.com/docs"
        assert _resolve_result_url(_bing_ck_url(target), "") == target


# ---------------------------------------------------------------------------
# 引擎链（bing → baidu）：mock 浏览器层，只测链路逻辑
# ---------------------------------------------------------------------------


def _result(title: str) -> dict[str, str]:
    return {"title": title, "url": "https://example.com", "snippet": ""}


class TestEngineChain:
    async def _run(self, bing: Any, baidu: Any) -> Any:
        async def _no_ensure(_state: Any) -> None:
            return None

        with (
            patch("illusion.browser.search_engine._ensure_browser", _no_ensure),
            patch("illusion.browser.search_engine._search_bing", bing),
            patch("illusion.browser.search_engine._search_baidu", baidu),
        ):
            return await browser_search("query", max_results=3)

    async def test_bing_success_short_circuits(self) -> None:
        async def bing(_state: Any, _q: str, _n: int) -> list[dict[str, str]]:
            return [_result("bing hit")]

        async def baidu(_state: Any, _q: str, _n: int) -> list[dict[str, str]]:
            raise AssertionError("baidu must not run when bing succeeds")

        results, engine = await self._run(bing, baidu)
        assert engine == "bing"
        assert results == [_result("bing hit")]

    async def test_baidu_fallback_on_bing_failure(self) -> None:
        async def bing(_state: Any, _q: str, _n: int) -> list[dict[str, str]]:
            raise RuntimeError("page timeout")

        async def baidu(_state: Any, _q: str, _n: int) -> list[dict[str, str]]:
            return [_result("baidu hit")]

        results, engine = await self._run(bing, baidu)
        assert engine == "baidu"
        assert results == [_result("baidu hit")]

    async def test_all_engines_fail_raises(self) -> None:
        async def fail(name: str) -> Any:
            async def _engine(_state: Any, _q: str, _n: int) -> list[dict[str, str]]:
                raise RuntimeError(name)

            return _engine

        with pytest.raises(BrowserCommandError, match="bing.*baidu|All search engines"):
            _, _engine = await self._run(await fail("bing-down"), await fail("baidu-down"))

    async def test_empty_results_treated_as_failure(self) -> None:
        async def bing(_state: Any, _q: str, _n: int) -> list[dict[str, str]]:
            return []

        async def baidu(_state: Any, _q: str, _n: int) -> list[dict[str, str]]:
            return [_result("baidu hit")]

        _results, engine = await self._run(bing, baidu)
        assert engine == "baidu"

    async def test_aclose_noop_without_browser(self) -> None:
        # 从未启动过浏览器时显式关闭应为安全空操作
        await aclose_search_browser()


# ---------------------------------------------------------------------------
# 生命周期：事件循环更换重置 / 空闲自动关闭
# ---------------------------------------------------------------------------


class _FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestStateLifecycle:
    async def test_loop_change_resets_state(self) -> None:
        # 旧循环上持有实例状态后，另一循环上的 _current_state 必须弃用旧
        # 实例（Playwright 连接绑定旧循环，无法跨循环复用）
        state = _current_state()
        state.browser = _FakeBrowser()
        state.context = object()
        old_loop = state.loop
        assert old_loop is not None

        # 不能在运行中的循环里嵌套 run_until_complete，换独立线程跑新循环
        result: list[Any] = []

        def _run_other() -> None:
            async def _touch() -> Any:
                return _current_state()

            loop = asyncio.new_event_loop()
            try:
                result.append(loop.run_until_complete(_touch()))
            finally:
                loop.close()

        thread = threading.Thread(target=_run_other)
        thread.start()
        thread.join(timeout=5)
        other = result[0]

        assert other.loop is not old_loop
        assert other.browser is None
        assert other.context is None
        # 回到当前循环再次进入：同样识别为循环变更并重置
        fresh = _current_state()
        assert fresh.loop is old_loop
        assert fresh.browser is None

    async def test_idle_browser_torn_down(self, monkeypatch) -> None:
        monkeypatch.setattr(search_engine, "_IDLE_CLOSE_SECONDS", 0.05)
        state = _current_state()
        browser = _FakeBrowser()
        state.browser = browser
        state.context = None
        state.last_used = time.monotonic() - 10
        _schedule_idle_close(state)
        try:
            # 计时器延迟 = idle + 1s，留足余量
            await asyncio.sleep(1.5)
        finally:
            if state.timer is not None:
                state.timer.cancel()
        assert state.browser is None
        assert browser.closed is True

    async def test_refreshed_last_use_keeps_browser_alive(self, monkeypatch) -> None:
        # 已触发但阻塞在锁上的旧计时器，拿到锁时若 last_used 已被新搜索
        # 刷新（未超时），必须跳过关闭——这是 _close_if_idle 二次检查的用途。
        # 时序：计时器在 idle+1s 触发；在触发前刷新 last_used 并重排计时器
        # （生产路径 = 新搜索完成），旧协程随后检查到"状态新鲜"
        monkeypatch.setattr(search_engine, "_IDLE_CLOSE_SECONDS", 2.0)
        state = _current_state()
        browser = _FakeBrowser()
        state.browser = browser
        state.context = None
        state.last_used = time.monotonic()
        _schedule_idle_close(state)
        await asyncio.sleep(2.0)
        state.last_used = time.monotonic()
        _schedule_idle_close(state)
        try:
            await asyncio.sleep(1.5)
        finally:
            if state.timer is not None:
                state.timer.cancel()
        assert state.browser is browser
        assert browser.closed is False
