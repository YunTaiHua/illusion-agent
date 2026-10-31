"""
无头浏览器搜索引擎
==================

web_search 工具的默认搜索后端：用托管的 Playwright 无头浏览器执行搜索
（Bing 主搜，百度兜底），真实 Chromium 渲染绕过纯 HTTP 抓取的风控页与
JS 壳问题，并返回解码后的真实网址。国内直连可用（Bing/百度不被墙），
配置了梯子时自动经 settings.browser.proxy 接入（含 Windows 系统代理
注册表探测）。

独立于 browser-use 插件与用户可见的 BrowserManager：搜索持有模块级
专用实例（内核复用 executable.py 的解析链：Playwright Chromium → 系统
Chrome → Edge），懒启动、空闲 5 分钟自动关闭。事件循环更换（新会话/
测试）时旧实例直接弃用——Playwright 连接随旧循环消亡，无法跨循环复用。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from illusion.browser.base import BrowserCommandError

logger = logging.getLogger(__name__)

# 操作超时预算（导航放宽到 12s；结果选择器等待 6s，超时视为该引擎无结果）
_NAV_TIMEOUT_MS = 12000
_RESULT_WAIT_MS = 6000

# 空闲 5 分钟后关闭无头实例（每次搜索刷新计时）
_IDLE_CLOSE_SECONDS = 300.0

# 各引擎的页内结果提取脚本。返回 [{title, url, snippet, mu}]，mu 为
# 百度结果容器上的真实网址属性（缺失时回退到跳转包装链接本身）。
_BING_EXTRACT = """() => {
  const out = [];
  const push = (a, scope) => {
    if (!a || !a.href) return;
    const snip = scope.querySelector('.b_caption p, .b_algoSlug, [data-snippet], p');
    out.push({ title: a.textContent.trim(), url: a.href,
               snippet: snip ? snip.textContent.trim() : '', mu: '' });
  };
  for (const li of document.querySelectorAll('li.b_algo')) {
    if (li.closest('.b_ad, .b_adlt')) continue;
    push(li.querySelector('h2 a'), li);
  }
  if (out.length === 0) {
    for (const a of document.querySelectorAll('#b_results h2 a')) push(a, a.closest('li') || a);
  }
  return out;
}"""

_BAIDU_EXTRACT = """() => {
  const out = [];
  for (const d of document.querySelectorAll('#content_left div.c-container')) {
    const a = d.querySelector('h3 a');
    if (!a || !a.href) continue;
    const snip = d.querySelector('.c-abstract, [class*="content-right"], [class*="abstract"]');
    out.push({ title: a.textContent.trim(), url: a.href,
               snippet: snip ? snip.textContent.trim() : '',
               mu: d.getAttribute('mu') || (d.querySelector('[mu]') ? d.querySelector('[mu]').getAttribute('mu') : '') });
  }
  return out;
}"""


class _SearchBrowserState:
    """模块级搜索浏览器状态（单实例 + 所属事件循环）。

    lock 在构造时创建（Python 3.10+ 的 asyncio.Lock 构造不绑定事件循环，
    首次 await 时才取当前 loop）；事件循环更换时整体替换，避免旧循环的
    锁对象跨循环使用。
    """

    def __init__(self) -> None:
        self.pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lock: asyncio.Lock = asyncio.Lock()
        self.timer: asyncio.TimerHandle | None = None
        self.last_used: float = 0.0


_state = _SearchBrowserState()


def _current_state() -> _SearchBrowserState:
    """取当前事件循环绑定的状态；循环更换时弃用旧实例。"""
    loop = asyncio.get_running_loop()
    if _state.loop is not loop:
        _state.pw = _state.browser = _state.context = None
        _state.timer = None
        _state.lock = asyncio.Lock()
        _state.loop = loop
    return _state


def _schedule_idle_close(state: _SearchBrowserState) -> None:
    """每次搜索后重排空闲关闭计时器（状态在所属循环上销毁）。"""
    if state.timer is not None:
        state.timer.cancel()

    async def _close_if_idle() -> None:
        if state.browser is not None and time.monotonic() - state.last_used >= _IDLE_CLOSE_SECONDS:
            async with state.lock:
                if state.browser is not None and time.monotonic() - state.last_used >= _IDLE_CLOSE_SECONDS:
                    logger.debug("[web_search] closing idle search browser")
                    await _teardown(state)

    loop = state.loop
    assert loop is not None
    state.timer = loop.call_later(
        _IDLE_CLOSE_SECONDS + 1, lambda: loop.create_task(_close_if_idle())
    )


async def _ensure_browser(state: _SearchBrowserState) -> None:
    """懒启动搜索浏览器（调用方须持有 state.lock）。"""
    if state.browser is not None:
        return
    from playwright.async_api import async_playwright

    from illusion.browser.executable import resolve_launch_kwargs, resolve_proxy
    from illusion.config.settings import load_settings

    settings = load_settings()
    try:
        state.pw = await async_playwright().start()
        kwargs = await resolve_launch_kwargs(state.pw, settings.browser.kernel)
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": ["--no-first-run", "--no-default-browser-check"],
        }
        launch_kwargs.update(kwargs)
        # 与托管浏览器后端同口径：无头 Chromium 不继承 Windows 系统代理，
        # auto 时显式接入（env → 注册表），失败时静默直连
        proxy = resolve_proxy(settings.browser.proxy)
        if proxy:
            launch_kwargs["proxy"] = proxy
        state.browser = await state.pw.chromium.launch(**launch_kwargs)

        # 无头 UA 自带 HeadlessChrome 标记，易触发风控；用 browser.version
        # 重建常规 Chrome UA（平台段固定 Windows，仅用于搜索抓取）
        version = state.browser.version
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36"
        )
        # start/launch/new_context 必须都在此 try 内归一化为
        # BrowserCommandError：裸 playwright Error 逃出 browser_search 会
        # 中止整个对话回合，且 browser 已置位而 context 为 None 时，后续
        # 每次搜索都会在 new_page 处 AttributeError 卡死
        state.context = await state.browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
            user_agent=user_agent,
        )
    except BrowserCommandError:
        await _teardown(state)
        raise
    except Exception as exc:
        await _teardown(state)
        raise BrowserCommandError(f"Search browser setup failed: {exc}") from exc


async def _teardown(state: _SearchBrowserState) -> None:
    """释放浏览器实例（幂等，异常静默——只用于清理路径）。"""
    closers: list[Callable[[], Awaitable[object] | None]] = [
        lambda: state.context.close() if state.context else None,
        lambda: state.browser.close() if state.browser else None,
        lambda: state.pw.stop() if state.pw else None,
    ]
    for closer in closers:
        try:
            result = closer()
            if result is not None:
                await result
        except Exception:
            logger.debug("[web_search] teardown close failed", exc_info=True)
    state.context = state.browser = state.pw = None


async def aclose_search_browser() -> None:
    """显式关闭搜索浏览器实例（供测试与优雅退出使用）。"""
    state = _current_state()
    if state.timer is not None:
        state.timer.cancel()
        state.timer = None
    async with state.lock:
        await _teardown(state)


async def _run_engine(state: _SearchBrowserState, url: str, extract_js: str,
                      wait_selector: str) -> list[dict[str, str]]:
    """导航到搜索页并提取结果；选择器超时视为无结果（触发下一引擎）。"""
    page = await state.context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        try:
            await page.wait_for_selector(wait_selector, timeout=_RESULT_WAIT_MS)
        except Exception:
            logger.debug("[web_search] result selector timeout: %s", wait_selector, exc_info=True)
        items = await page.evaluate(extract_js)
        results: list[dict[str, str]] = []
        for item in items or []:
            title = str(item.get("title", "")).strip()
            url_value = str(item.get("url", "")).strip()
            if title and url_value.startswith("http"):
                results.append({
                    "title": title,
                    "url": url_value,
                    "snippet": str(item.get("snippet", "")).strip(),
                    "mu": str(item.get("mu", "")).strip(),
                })
        return results
    finally:
        try:
            await page.close()
        except Exception:
            logger.debug("[web_search] page close failed", exc_info=True)


def _decode_bing_redirect(url: str) -> str:
    """解码 Bing /ck/a 跳转链接：u 参数为 a1 前缀 + base64url 真实网址。"""
    parsed = urlparse(url)
    if (parsed.netloc == "bing.com" or parsed.netloc.endswith(".bing.com")) and (
        parsed.path.startswith("/ck/")
    ):
        raw = parse_qs(parsed.query).get("u", [""])[0]
        if raw.startswith("a1"):
            body = raw[2:]
            try:
                decoded = base64.urlsafe_b64decode(
                    body + "=" * (-len(body) % 4)
                ).decode("utf-8", "replace")
            except (ValueError, UnicodeDecodeError):
                return url
            if decoded.startswith("http"):
                return decoded
    return url


def _resolve_result_url(url: str, mu: str) -> str:
    """把提取到的链接还原为真实网址。

    Bing 走 /ck/a 解码；百度优先取容器 mu 属性（真实网址），缺失时保留
    baidu.com/link 跳转包装（仍可访问，web_fetch 会跟随重定向）。
    """
    if mu.startswith("http"):
        return mu
    return _decode_bing_redirect(url)


async def _search_bing(state: _SearchBrowserState, query: str,
                       max_results: int) -> list[dict[str, str]]:
    page_url = f"https://www.bing.com/search?q={quote(query)}"
    items = await _run_engine(state, page_url, _BING_EXTRACT, "li.b_algo")
    results = [
        {"title": item["title"], "url": _resolve_result_url(item["url"], ""),
         "snippet": item["snippet"]}
        for item in items
    ]
    return results[:max_results]


async def _search_baidu(state: _SearchBrowserState, query: str,
                        max_results: int) -> list[dict[str, str]]:
    page_url = f"https://www.baidu.com/s?wd={quote(query)}&rn={min(max_results + 5, 20)}"
    items = await _run_engine(state, page_url, _BAIDU_EXTRACT, "#content_left div.c-container")
    results = [
        {"title": item["title"], "url": _resolve_result_url(item["url"], item["mu"]),
         "snippet": item["snippet"]}
        for item in items
    ]
    return results[:max_results]


async def browser_search(query: str, *, max_results: int = 5) -> tuple[list[dict[str, str]], str]:
    """用内置无头浏览器执行搜索。

    引擎链：Bing → 百度；单引擎页面级失败（超时/改版）尝试下一引擎，
    全部失败抛 BrowserCommandError 由调用方报错——没有任何静默回退路径。

    Returns:
        (结果列表, 引擎名)；结果项含 title / url（已还原真实网址）/ snippet。
    """
    state = _current_state()
    async with state.lock:
        state = _current_state()
        await _ensure_browser(state)
        failures: list[str] = []
        for name, engine in (
            ("bing", _search_bing),
            ("baidu", _search_baidu),
        ):
            try:
                results = await engine(state, query, max_results)
            except Exception as exc:  # 单引擎失败换下一引擎
                logger.debug("[web_search] engine %s failed", name, exc_info=True)
                failures.append(f"{name}: {exc}")
                continue
            if results:
                state.last_used = time.monotonic()
                _schedule_idle_close(state)
                return results, name
            failures.append(f"{name}: no results")
        _schedule_idle_close(state)
        raise BrowserCommandError(
            "All search engines failed: " + "; ".join(failures)
        )
