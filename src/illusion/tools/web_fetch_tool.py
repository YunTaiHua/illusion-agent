"""
网页抓取和摘要工具
==================

本模块提供获取和摘要远程网页内容的功能。

主要组件：
    - WebFetchTool: 抓取并摘要网页的工具

使用示例：
    >>> from illusion.tools import WebFetchTool
    >>> tool = WebFetchTool()
"""

from __future__ import annotations

import html as _html_module
import logging
import re
import time
from contextvars import ContextVar
from urllib.parse import urlparse

import httpx
from openai import OpenAIError
from pydantic import BaseModel, Field

from illusion.config.settings import load_settings
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult
from illusion.utils.http import create_async_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 15-minute TTL cache（使用 ContextVar 实现会话隔离，避免跨会话泄漏）
# ---------------------------------------------------------------------------
_cache_var: ContextVar[dict[str, tuple[float, str]]] = ContextVar("_cache_var")
_CACHE_TTL = 15 * 60  # 15 minutes in seconds
_CACHE_MAX_SIZE = 256  # 最大缓存条目数，防止内存无限增长


def _get_cache() -> dict[str, tuple[float, str]]:
    """获取当前会话的缓存字典（懒初始化）"""
    try:
        return _cache_var.get()
    except LookupError:
        c: dict[str, tuple[float, str]] = {}
        _cache_var.set(c)
        return c


def _cache_key(url: str, prompt: str, max_chars: int) -> str:
    return f"{url}|{prompt}|{max_chars}"


def _cache_get(key: str) -> str | None:
    cache = _get_cache()
    entry = cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del cache[key]
        return None
    return value


def _cache_set(key: str, value: str) -> None:
    cache = _get_cache()
    # 超过最大容量时清理过期条目
    if len(cache) >= _CACHE_MAX_SIZE:
        now = time.time()
        expired = [k for k, (ts, _) in cache.items() if now - ts > _CACHE_TTL]
        for k in expired:
            del cache[k]
        # 仍超容量则清理最旧的
        if len(cache) >= _CACHE_MAX_SIZE:
            oldest_key = min(cache, key=lambda k: cache[k][0])
            del cache[oldest_key]
    cache[key] = (time.time(), value)


class WebFetchToolInput(BaseModel):
    """网页抓取参数。

    属性：
        url: 要抓取的 HTTP 或 HTTPS URL
        prompt: 描述你想从页面中提取什么信息
        max_chars: 最大返回字符数（500-50000）
    """

    url: str = Field(description="HTTP or HTTPS URL to fetch")
    prompt: str = Field(
        default="Summarize the key content of this page.",
        description="Describes what information you want to extract from the page",
    )
    max_chars: int = Field(default=12000, ge=500, le=50000)


class WebFetchTool(BaseTool[WebFetchToolInput]):
    """抓取一个网页并使用 AI 模型处理内容。

    用于获取和分析网络内容。
    """

    name = "web_fetch"
    description = """- Fetches content from a specified URL and processes it using an AI model
- Takes a URL and a prompt as input
- Fetches the URL content, converts HTML to markdown
- Processes the content with the prompt using a small, fast model
- Returns the model's response about the content
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using that tool instead of this one, as it may have fewer restrictions.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - The prompt should describe what information you want to extract from the page
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large
  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL
  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new WebFetch request with the redirect URL to fetch the content.
  - For GitHub URLs, prefer using the gh CLI via Bash instead (e.g., gh pr view, gh issue view, gh api)."""
    input_model = WebFetchToolInput

    async def execute(self, arguments: WebFetchToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        url = arguments.url

        # 自动升级 HTTP 到 HTTPS
        parsed = urlparse(url)
        if parsed.scheme == "http":
            url = url.replace("http://", "https://", 1)

        # 检查缓存
        ck = _cache_key(url, arguments.prompt, arguments.max_chars)
        cached = _cache_get(ck)
        if cached is not None:
            return ToolResult(output=cached)

        # 发起 HTTP 请求（手动处理重定向以检测跨主机跳转）
        try:
            async with create_async_client(follow_redirects=False, timeout=20.0) as client:
                response = await client.get(url, headers={"User-Agent": "IllusionAgent/0.1"})
                # 检测跨主机重定向（限制最大次数，防止恶意服务器触发无限循环）
                max_redirects = 10
                redirect_count = 0
                while response.is_redirect and redirect_count < max_redirects:
                    location = response.headers.get("location", "")
                    if not location:
                        break
                    redirect_parsed = urlparse(location)
                    current_parsed = urlparse(url)
                    # 如果是相对路径或同主机，跟随
                    if not redirect_parsed.netloc or redirect_parsed.netloc == current_parsed.netloc:
                        url = location if redirect_parsed.netloc else f"{current_parsed.scheme}://{current_parsed.netloc}{location}"
                        response = await client.get(url, headers={"User-Agent": "IllusionAgent/0.1"})
                        redirect_count += 1
                    else:
                        return ToolResult(
                            output=(
                                f"Redirect detected to a different host. The URL {arguments.url} "
                                f"redirects to:\n\n{location}\n\n"
                                f"Please make a new WebFetch request with the redirect URL."
                            )
                        )
                if redirect_count >= max_redirects:
                    return ToolResult(
                        output=f"Too many redirects (>{max_redirects}) for {arguments.url}. Possible redirect loop.",
                        is_error=True,
                    )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(output=f"web_fetch failed: {exc}", is_error=True)

        # 处理响应内容
        content_type = response.headers.get("content-type", "")
        body = response.text
        # 如果是 HTML，转换为 Markdown
        if "html" in content_type:
            body = _html_to_markdown(body)
        body = body.strip()
        # 截断过长的内容
        if len(body) > arguments.max_chars:
            body = body[: arguments.max_chars].rstrip() + "\n...[truncated]"

        # 使用 AI 模型处理内容
        try:
            ai_response = await _process_with_model(body, arguments.prompt)
        except (RuntimeError, OSError, ValueError, httpx.HTTPError, OpenAIError):
            # 模型调用失败时回退到直接返回内容
            logger.debug("[web_fetch_tool] AI model processing failed, falling back to raw content", exc_info=True)
            result = (
                f"URL: {response.url}\n"
                f"Status: {response.status_code}\n"
                f"Content-Type: {content_type or '(unknown)'}\n\n"
                f"{body}"
            )
            return ToolResult(output=result)

        _cache_set(ck, ai_response)
        return ToolResult(output=ai_response)

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True


async def _process_with_model(content: str, prompt: str) -> str:
    """使用 AI 模型处理内容。

    走统一的 API 客户端工厂（build_api_client_for_env），兼容全部
    api_format（anthropic/openai/response/copilot/codex）与凭据形态
    （api_key/auth_token/OAuth）。原实现手搓 AsyncOpenAI 且只解析
    api_key：response 格式 env 会错误落到 chat/completions，anthropic
    的 base_url 拼接也不符官方约定。
    """
    from illusion.api.client import ApiMessageCompleteEvent, ApiMessageRequest
    from illusion.api.factory import build_api_client_for_env
    from illusion.engine.messages import ConversationMessage

    settings = load_settings()
    client = build_api_client_for_env(settings, settings._active_env_key)
    request = ApiMessageRequest(
        model=settings._active_model_name,
        system_prompt=(
            "You are a web content summarizer. Analyze the provided web page content and respond "
            "to the user's prompt. Be concise and accurate. Only use information from the provided content."
        ),
        messages=[ConversationMessage.from_user_text(
            f"Web page content:\n\n{content}\n\nUser prompt: {prompt}"
        )],
        max_tokens=4096,
    )
    summary = ""
    async for event in client.stream_message(request):  # type: ignore[attr-defined]
        if isinstance(event, ApiMessageCompleteEvent):
            summary = event.message.text
    if not summary.strip():
        raise RuntimeError("Model returned an empty summary")
    return summary


def _html_to_markdown(html_text: str) -> str:
    """将 HTML 转换为 Markdown。"""
    text = html_text

    # 移除 script、style、nav、footer、header 标签及其内容
    text = re.sub(r"(?is)<(script|style|nav|footer|header|noscript).*?>.*?</\1>", " ", text)

    # 标题 h1-h6
    for i in range(6, 0, -1):
        text = re.sub(
            rf"(?is)<h{i}[^>]*>\s*(.*?)\s*</h{i}>",
            lambda m, n=i: "#" * n + " " + _strip_html(m.group(1)).strip() + "\n\n",  # type: ignore[misc]
            text,
        )

    # 粗体 / 斜体
    text = re.sub(r"(?is)<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>", r"**\1**", text)
    text = re.sub(r"(?is)<(?:i|em)[^>]*>(.*?)</(?:i|em)>", r"*\1*", text)

    # 链接（优先处理有 href 的 <a>）
    text = re.sub(r'(?is)<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"[\2](\1)", text)

    # 图片
    text = re.sub(r'(?is)<img[^>]*src=["\']([^"\']+)["\'][^>]*/?>', r"![](\1)", text)

    # 段落
    text = re.sub(r"(?is)<p[^>]*>\s*(.*?)\s*</p>", r"\1\n\n", text)

    # 换行
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)

    # 无序列表项
    text = re.sub(r"(?is)<li[^>]*>\s*(.*?)\s*</li>", r"- \1\n", text)

    # 代码块
    text = re.sub(r"(?is)<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", text)
    text = re.sub(r"(?is)<code[^>]*>(.*?)</code>", r"`\1`", text)

    # 删除所有剩余 HTML 标签
    text = re.sub(r"(?s)<[^>]+>", " ", text)

    # 解码 HTML 实体
    text = _html_module.unescape(text)

    # 规范化空白和多余空行
    text = re.sub(r"[ \t\f\r]+", " ", text)
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _strip_html(fragment: str) -> str:
    """移除 HTML 标签，保留纯文本。"""
    text = re.sub(r"(?s)<[^>]+>", " ", fragment)
    text = _html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
