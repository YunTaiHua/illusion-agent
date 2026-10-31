"""
简单网页搜索工具
================

本模块提供执行网络搜索并返回紧凑顶部结果的功能。

搜索链路：
    1. 默认：内置无头浏览器（illusion.browser.search_engine）——真实
       Chromium 渲染，Bing 主搜 + 百度兜底，返回还原后的真实网址。国内
       直连可用，并自动继承 settings.browser 的内核与代理解析（含 Windows
       系统代理）。浏览器级失败直接报错（含恢复指引），不做静默跨引擎回退。
    2. search_url 显式指定：httpx 直连该端点（DuckDuckGo HTML 兼容格式），
       供私有搜索后端与离线测试使用。

主要组件：
    - WebSearchTool: 执行网页搜索的工具

使用示例：
    >>> from illusion.tools import WebSearchTool
    >>> tool = WebSearchTool()
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import BaseModel, Field

from illusion.browser import search_engine
from illusion.browser.base import BrowserCommandError
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult
from illusion.utils.http import create_async_client


class WebSearchToolInput(BaseModel):
    """网页搜索参数。

    属性：
        query: 搜索查询
        max_results: 最大结果数量（1-10）
        search_url: 可选的 http 直连搜索端点覆盖（DuckDuckGo HTML 兼容
            格式）；设置后跳过内置浏览器，适合私有搜索后端与离线测试
    """

    query: str = Field(description="Search query")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum number of results")
    search_url: str | None = Field(
        default=None,
        description=(
            "Optional override for a direct-HTTP search endpoint (DuckDuckGo HTML "
            "compatible). Bypasses the built-in browser; useful for private search "
            "backends or testing."
        ),
    )


class WebSearchTool(BaseTool[WebSearchToolInput]):
    """运行网络搜索并返回紧凑的顶部结果。

    用于获取超出 Illusion 知识截止日期的最新信息。
    """

    name = "web_search"
    description = """- Allows Illusion to search the web and use the results to inform responses
- Runs on the built-in headless browser (Bing first, Baidu fallback) and returns real result URLs as markdown hyperlinks
- Works on a direct connection without a VPN in regions like mainland China; automatically routes through the system proxy when one is configured
- Returns search result information formatted as search result blocks, including links as markdown hyperlinks
- Use this tool for accessing information beyond Illusion's knowledge cutoff
- Searches are performed automatically within a single API call

CRITICAL REQUIREMENT - You MUST follow this:
  - After answering the user's question, you MUST include a "Sources:" section at the end of your response
  - In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)
  - This is MANDATORY - never skip including sources in your response
  - Example format:

    [Your answer here]

    Sources:
    - [Source Title 1](https://example.com/1)
    - [Source Title 2](https://example.com/2)

Usage notes:
  - search_url switches to a direct-HTTP DuckDuckGo-compatible endpoint instead of the browser (private search backends or testing only)

IMPORTANT - Use the correct year in search queries:
  - The current month is <currentMonthYear>. You MUST use this year when searching for recent information, documentation, or current events.
  - Example: If the user asks for "latest React docs", search for "React documentation" with the current year, NOT last year"""
    input_model = WebSearchToolInput

    def is_read_only(self, arguments: WebSearchToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self,
        arguments: WebSearchToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        # search_url 显式指定：http 直连覆盖（私有搜索后端 / 离线测试）
        if arguments.search_url is not None:
            return await self._http_search(arguments.search_url, arguments)

        # 默认路径：内置无头浏览器（Bing → 百度）。失败直接报错而非静默
        # 换引擎——隐藏回退难排查；错误信息含内核缺失时的恢复指引
        try:
            results, engine = await search_engine.browser_search(
                arguments.query, max_results=arguments.max_results
            )
        except BrowserCommandError as exc:
            return ToolResult(output=f"web_search failed: {exc}", is_error=True)
        return ToolResult(output=_format_results(arguments.query, results, engine))

    async def _http_search(self, endpoint: str, arguments: WebSearchToolInput) -> ToolResult:
        try:
            # 发起搜索请求
            async with create_async_client(follow_redirects=True, timeout=20.0) as client:
                response = await client.get(
                    endpoint,
                    params={"q": arguments.query},
                    headers={"User-Agent": "IllusionAgent/0.1"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(output=f"web_search failed: {exc}", is_error=True)

        # 解析搜索结果
        results = _parse_search_results(response.text, limit=arguments.max_results)
        if not results:
            return ToolResult(output="No search results found.", is_error=True)

        engine = urlparse(endpoint).netloc or "http"
        return ToolResult(output=_format_results(arguments.query, results, engine))


def _format_results(
    query: str,
    results: list[dict[str, str]],
    engine: str,
) -> str:
    """构建输出 — 使用 Markdown 超链接格式。"""
    lines = [f"Search results for: {query} (via {engine})"]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. [{result['title']}]({result['url']})")
        if result.get("snippet"):
            lines.append(f"   {result['snippet']}")
    return "\n".join(lines)


def _parse_search_results(body: str, *, limit: int) -> list[dict[str, str]]:
    """解析搜索结果页面。

    参数：
        body: 搜索结果页面的 HTML 内容
        limit: 最大返回结果数

    返回：
        搜索结果字典列表
    """
    # 提取摘要片段
    snippets = [
        _clean_html(match.group("snippet"))
        for match in re.finditer(
            r'<(?:a|div|span)[^>]+class="[^"]*(?:result__snippet|result-snippet)[^"]*"[^>]*>(?P<snippet>.*?)</(?:a|div|span)>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]

    results: list[dict[str, str]] = []
    # 查找所有链接
    anchor_matches = re.finditer(
        r"<a(?P<attrs>[^>]+)>(?P<title>.*?)</a>",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for index, match in enumerate(anchor_matches):
        attrs = match.group("attrs")
        # 检查是否为结果链接
        class_match = re.search(r'class="(?P<class>[^"]+)"', attrs, flags=re.IGNORECASE)
        if class_match is None:
            continue
        class_names = class_match.group("class")
        if "result__a" not in class_names and "result-link" not in class_names:
            continue
        # 提取 href
        href_match = re.search(r'href="(?P<href>[^"]+)"', attrs, flags=re.IGNORECASE)
        if href_match is None:
            continue
        # 解析标题和 URL
        title = _clean_html(match.group("title"))
        url = _normalize_result_url(href_match.group("href"))
        snippet = snippets[index] if index < len(snippets) else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _normalize_result_url(raw_url: str) -> str:
    """规范化 DuckDuckGo 重定向 URL。

    将 /l/ 路径下的重定向 URL 解析为目标 URL。
    """
    parsed = urlparse(raw_url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target else raw_url
    return raw_url


def _clean_html(fragment: str) -> str:
    """清理 HTML 片段，提取纯文本。"""
    # 移除 HTML 标签
    text = re.sub(r"(?s)<[^>]+>", " ", fragment)
    # 解码 HTML 实体
    text = html.unescape(text)
    # 规范化空白
    text = re.sub(r"\s+", " ", text).strip()
    return text
