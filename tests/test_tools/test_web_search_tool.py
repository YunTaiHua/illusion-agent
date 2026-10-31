"""web_search 工具链路测试：浏览器主路径、失败报错与 search_url 直连覆盖。"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from illusion.browser.base import BrowserCommandError
from illusion.tools.base import ToolExecutionContext
from illusion.tools.web_search_tool import WebSearchTool, WebSearchToolInput


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
        body = (
            "<html><body>"
            '<a class="result__a" href="https://example.com/docs">IllusionAgent Docs</a>'
            f'<div class="result__snippet">Direct search for {query}.</div>'
            "</body></html>"
        )
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        del format


def _local_server() -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}/search"


def _stop_server(server: HTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    with contextlib.suppress(Exception):
        server.server_close()
    thread.join(timeout=1)


def _fake_search(
    results: list[dict[str, str]], engine: str = "bing"
) -> Callable[[str, int], Awaitable[tuple[list[dict[str, str]], str]]]:
    async def _search(query: str, max_results: int) -> tuple[list[dict[str, str]], str]:
        del query, max_results
        return results, engine

    return _search


class TestBrowserPath:
    @pytest.mark.asyncio
    async def test_browser_backend_used_by_default(self, tmp_path) -> None:
        with patch(
            "illusion.browser.search_engine.browser_search",
            _fake_search([{"title": "Browser Hit", "url": "https://example.com/x", "snippet": "snip"}]),
        ):
            result = await WebSearchTool().execute(
                WebSearchToolInput(query="q"), ToolExecutionContext(cwd=tmp_path)
            )
        assert result.is_error is False
        assert "(via bing)" in result.output
        assert "[Browser Hit](https://example.com/x)" in result.output

    @pytest.mark.asyncio
    async def test_search_url_bypasses_browser(self, tmp_path) -> None:
        server, thread, endpoint = _local_server()
        try:

            async def fail_search(query: str, max_results: int) -> tuple[list[dict[str, str]], str]:
                raise AssertionError("browser must not run when search_url is set")

            with patch("illusion.browser.search_engine.browser_search", fail_search):
                result = await WebSearchTool().execute(
                    WebSearchToolInput(query="illusion docs", search_url=endpoint),
                    ToolExecutionContext(cwd=tmp_path),
                )
        finally:
            _stop_server(server, thread)

        assert result.is_error is False
        assert "IllusionAgent Docs" in result.output
        assert "https://example.com/docs" in result.output


class TestBrowserFailure:
    @pytest.mark.asyncio
    async def test_browser_failure_returns_error(self, tmp_path) -> None:
        async def fail_search(query: str, max_results: int) -> tuple[list[dict[str, str]], str]:
            del query, max_results
            raise BrowserCommandError(
                "No browser available: Playwright Chromium is not installed and no "
                "system Chrome/Edge was found. Run `illusion browser setup` once."
            )

        with patch("illusion.browser.search_engine.browser_search", fail_search):
            result = await WebSearchTool().execute(
                WebSearchToolInput(query="q"), ToolExecutionContext(cwd=tmp_path)
            )

        assert result.is_error is True
        assert "illusion browser setup" in result.output

    @pytest.mark.asyncio
    async def test_http_endpoint_error_propagates(self, tmp_path) -> None:
        result = await WebSearchTool().execute(
            WebSearchToolInput(query="q", search_url="http://127.0.0.1:1/search"),
            ToolExecutionContext(cwd=tmp_path),
        )
        assert result.is_error is True
        assert result.output.startswith("web_search failed:")
