"""
OpenAI Codex 订阅客户端模块
==========================

本模块提供基于 chatgpt.com Codex Responses 的 OpenAI Codex 订阅客户端。

主要功能：
    - 使用 OAuth 令牌进行认证
    - 流式文本增量生成
    - 支持工具调用
    - 自动重试 transient 错误

类说明：
    - CodexApiClient: Codex API 客户端类

使用示例：
    >>> from illusion.api.codex_client import CodexApiClient
    >>> client = CodexApiClient(auth_token="gho_...")
    >>> request = ApiMessageRequest(model="gpt-4o", messages=[])
    >>> async for event in client.stream_message(request):
    >>>     print(event)
"""

from __future__ import annotations

import base64
import json
import logging
import platform
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from illusion.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    ApiToolCallStartedEvent,
)
from illusion.api.compat import merge_reasoning_text, parse_tool_arguments, split_thinking_from_text
from illusion.api.errors import (
    AuthenticationFailure,
    IllusionAgentApiError,
    RateLimitFailure,
    RequestFailure,
)
from illusion.api.usage import UsageSnapshot
from illusion.engine.messages import (
    ContentBlock,
    ConversationMessage,
    MediaBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    _media_placeholder,
)
from illusion.utils.http import create_async_client

# 模块级日志记录器
log = logging.getLogger(__name__)

# 常量定义
DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api"  # 默认 Codex 基础 URL
JWT_CLAIM_PATH = "https://api.openai.com/auth"  # JWT 声明路径
MAX_RETRIES = 3  # 最大重试次数
BASE_DELAY_SECONDS = 1.0  # 基础延迟（秒）
MAX_DELAY_SECONDS = 30.0  # 最大延迟（秒）


def _extract_account_id(token: str) -> str:
    """从 JWT token 中提取 chatgpt_account_id

    Args:
        token: JWT 访问令牌

    Returns:
        str: 账户 ID，提取失败时返回空字符串
    """
    parts = token.split(".")
    if len(parts) != 3:
        return ""
    try:
        encoded = parts[1]
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    auth = payload.get(JWT_CLAIM_PATH)
    if isinstance(auth, dict):
        return str(auth.get("chatgpt_account_id", "") or "")
    return ""


def _resolve_codex_url(base_url: str | None) -> str:
    """解析并返回 Codex API URL
    
    Args:
        base_url: 可选的基础 URL
    
    Returns:
        str: 完整的 Codex API URL
    """
    trimmed = (base_url or "").strip()
    if trimmed and "chatgpt.com/backend-api" not in trimmed:
        trimmed = ""
    raw = (trimmed or DEFAULT_CODEX_BASE_URL).rstrip("/")
    if raw.endswith("/codex/responses"):
        return raw
    if raw.endswith("/codex"):
        return f"{raw}/responses"
    return f"{raw}/codex/responses"


def _build_codex_headers(token: str, *, session_id: str | None = None) -> dict[str, str]:
    """构建 Codex API 请求头
    
    Args:
        token: Codex 访问令牌
        session_id: 可选的会话 ID
    
    Returns:
        dict[str, str]: 请求头字典
    """
    account_id = _extract_account_id(token)
    headers = {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "originator": "illusion",
        "User-Agent": f"illusion ({platform.system().lower()} {platform.machine() or 'unknown'})",
        "OpenAI-Beta": "responses=experimental",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    if session_id:
        headers["session_id"] = session_id
    return headers


def _convert_messages_to_codex(messages: list[ConversationMessage]) -> list[dict[str, Any]]:
    """将消息转换为 Codex 格式
    
    Args:
        messages: ConversationMessage 列表
    
    Returns:
        list[dict[str, Any]]: Codex 格式的消息列表
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "user":
            text = "".join(block.text for block in msg.content if isinstance(block, TextBlock))
            media_blocks = [b for b in msg.content if isinstance(b, MediaBlock)]
            if text.strip() or media_blocks:
                parts = []
                if text.strip():
                    parts.append({"type": "input_text", "text": text})
                # Codex 上下文窗口有限（272K token），input_image 的 base64
                # 数据会被计算为海量 token，因此统一用文本描述替代
                for mb in media_blocks:
                    parts.append({
                        "type": "input_text",
                        "text": _media_placeholder(mb),
                    })
                result.append({
                    "role": "user",
                    "content": parts,
                })
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    # Codex function_call_output 只接受字符串，不支持媒体
                    # 始终使用 text_content，不传 base64 数据
                    result.append({
                        "type": "function_call_output",
                        "call_id": block.tool_use_id,
                        "output": block.text_content,
                    })
            continue

        assistant_text = "".join(block.text for block in msg.content if isinstance(block, TextBlock))
        assistant_text, _ = split_thinking_from_text(assistant_text)
        if assistant_text:
            result.append({
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": assistant_text, "annotations": []}],
            })
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                result.append({
                    "type": "function_call",
                    "id": f"fc_{block.id[:58]}",
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": json.dumps(block.input, separators=(",", ":")),
                })
    return result


def _convert_tools_to_codex(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将工具转换为 Codex 格式
    
    Args:
        tools: 工具定义列表
    
    Returns:
        list[dict[str, Any]]: Codex 格式的工具列表
    """
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        }
        for tool in tools
    ]


def _usage_from_response(response: dict[str, Any]) -> UsageSnapshot:
    """从响应中提取使用量信息

    Codex（OpenAI Responses 格式）的 usage.input_tokens 包含缓存命中的
    tokens，非缓存输入 = input_tokens - cached。命中位于嵌套的
    input_tokens_details.cached_tokens（部分服务可能在顶层 cached_tokens）。
    OpenAI 不区分缓存写入，cache_creation_input_tokens 恒为 0。

    Args:
        response: API 响应字典

    Returns:
        UsageSnapshot: 使用量快照
    """
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return UsageSnapshot()
    prompt = int(usage.get("input_tokens") or 0)
    if prompt == 0:
        prompt = int(usage.get("prompt_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)
    if output == 0:
        output = int(usage.get("completion_tokens") or 0)
    # 命中：input_tokens_details.cached_tokens（Responses）/ prompt_tokens_details.cached_tokens（Chat）
    cached = 0
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    if cached == 0:
        cached = int(usage.get("cached_tokens") or 0)
    return UsageSnapshot(
        input_tokens=max(0, prompt - cached),
        output_tokens=output,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,
    )


def _stop_reason_from_response(response: dict[str, Any], *, has_tool_calls: bool) -> str | None:
    """从响应中提取停止原因
    
    Args:
        response: API 响应字典
        has_tool_calls: 是否有工具调用
    
    Returns:
        str | None: 停止原因
    """
    status = response.get("status")
    if has_tool_calls and status == "completed":
        return "tool_use"
    if status == "completed":
        return "stop"
    if status == "incomplete":
        return "length"
    if status in {"failed", "cancelled"}:
        return "error"
    return None


def _format_error_message(
    status_code: int,
    payload: str,
    *,
    provider_label: str = "Codex",
) -> str:
    """格式化错误消息

    Args:
        status_code: HTTP 状态码
        payload: 响应负载
        provider_label: 兜底文案中的提供商名（payload 非 JSON 时使用）

    Returns:
        str: 格式化的错误消息
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail
    text = payload.strip()
    if text:
        return text
    return f"{provider_label} request failed with status {status_code}"


def _translate_status_error(status_code: int, message: str) -> IllusionAgentApiError:
    """转换状态码错误为统一异常类型

    Args:
        status_code: HTTP 状态码
        message: 错误消息

    Returns:
        IllusionAgentApiError: 统一异常类型
    """
    if status_code in {401, 403}:
        return AuthenticationFailure(message)
    if status_code == 429:
        return RateLimitFailure(message)
    return RequestFailure(message)


def _is_effort_unsupported_error(exc: Exception) -> bool:
    """检测是否为 effort 字段不支持导致的错误

    Args:
        exc: 异常对象

    Returns:
        bool: 是否为 effort 不支持错误
    """
    error_msg = str(exc).lower()
    # 检测常见的 effort 不支持错误消息
    effort_keywords = ["effort", "reasoning_effort", "reasoning effort"]
    unsupported_keywords = ["not supported", "unsupported", "invalid", "unknown"]

    # 检查是否包含 effort 相关关键词
    has_effort_keyword = any(keyword in error_msg for keyword in effort_keywords)
    # 检查是否包含不支持相关关键词
    has_unsupported_keyword = any(keyword in error_msg for keyword in unsupported_keywords)

    # 检查特定的错误模式：unknown variant `max`/`xhigh` 等
    has_variant_error = "unknown variant" in error_msg and any(
        level in error_msg for level in ["max", "xhigh", "low", "medium", "high"]
    )

    return (has_effort_keyword and has_unsupported_keyword) or has_variant_error


class CodexApiClient:
    """ChatGPT/Codex 订阅支持的 Codex Responses 客户端

    Attributes:
        _auth_token: 认证令牌
        _auth_token_resolver: 认证令牌解析器（每次请求前调用，自动刷新过期令牌）
        _base_url: 基础 URL
        _url: 解析后的 API URL
    """

    def __init__(
        self,
        auth_token: str = "",
        *,
        base_url: str | None = None,
        auth_token_resolver: Callable[[], str] | None = None,
    ) -> None:
        self._auth_token = auth_token
        self._auth_token_resolver = auth_token_resolver
        self._base_url = base_url
        self._url = _resolve_codex_url(base_url)

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """流式生成文本增量
        
        Args:
            request: API 消息请求
        
        Yields:
            ApiStreamEvent: 流式事件
        """
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async for event in self._stream_once(request):
                    yield event
                return
            except Exception as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not self._is_retryable(exc):
                    raise self._translate_error(exc) from exc
                delay = min(BASE_DELAY_SECONDS * (2 ** attempt), MAX_DELAY_SECONDS)
                import asyncio

                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
        if last_error is not None:
            raise self._translate_error(last_error) from last_error

    def _resolve_auth_token(self) -> str:
        """解析当前认证令牌，若有 resolver 则每次请求前刷新

        Returns:
            str: 有效的认证令牌

        Raises:
            AuthenticationFailure: 未认证或刷新失败
        """
        if self._auth_token_resolver is not None:
            try:
                self._auth_token = self._auth_token_resolver()
            except (RuntimeError, ValueError) as exc:
                raise AuthenticationFailure(str(exc)) from exc
        return self._auth_token

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        body: dict[str, Any] = {
            "model": request.model,
            "store": False,
            "stream": True,
            "instructions": request.system_prompt or "You are illusion.",
            "input": _convert_messages_to_codex(request.messages),
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        if request.tools:
            body["tools"] = _convert_tools_to_codex(request.tools)

        # 添加 effort 字段
        if request.effort is not None:
            body["reasoning"] = {"effort": request.effort.value}

        content: list[ContentBlock] = []
        current_text_parts: list[str] = []
        collected_reasoning = ""
        completed_response: dict[str, Any] | None = None

        headers = _build_codex_headers(self._resolve_auth_token())
        try:
            async with create_async_client(timeout=60.0, follow_redirects=True) as client, client.stream(
                "POST", self._url, headers=headers, json=body
            ) as response:
                if response.status_code >= 400:
                    payload = await response.aread()
                    message = _format_error_message(response.status_code, payload.decode("utf-8", "replace"))
                    raise httpx.HTTPStatusError(message, request=response.request, response=response)

                async for event in self._iter_sse_events(response):
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            current_text_parts.append(delta)
                            yield ApiTextDeltaEvent(text=delta)
                    elif event_type in {
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                        "response.output_text.reasoning.delta",
                    }:
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            collected_reasoning = merge_reasoning_text(collected_reasoning, delta)
                            yield ApiTextDeltaEvent(text="", reasoning=delta)
                    elif event_type == "response.output_item.added":
                        # 工具调用开始：模型刚开始生成工具调用时立即通知
                        item = event.get("item")
                        if isinstance(item, dict) and item.get("type") == "function_call":
                            tool_name = item.get("name", "")
                            tool_use_id = item.get("call_id", "") or item.get("id", "")
                            if tool_name:
                                yield ApiToolCallStartedEvent(
                                    tool_name=tool_name,
                                    tool_use_id=tool_use_id,
                                )
                    elif event_type == "response.output_item.done":
                        item = event.get("item")
                        if not isinstance(item, dict):
                            continue
                        item_type = item.get("type")
                        if item_type == "message":
                            text = ""
                            raw_content = item.get("content")
                            if isinstance(raw_content, list):
                                parts = []
                                for block in raw_content:
                                    if isinstance(block, dict):
                                        if block.get("type") == "output_text":
                                            parts.append(str(block.get("text", "")))
                                        elif block.get("type") == "refusal":
                                            parts.append(str(block.get("refusal", "")))
                                text = "".join(parts)
                            if text:
                                plain_text, tagged_reasoning = split_thinking_from_text(text)
                                if plain_text:
                                    content.append(TextBlock(text=plain_text))
                                if tagged_reasoning:
                                    collected_reasoning = merge_reasoning_text(
                                        collected_reasoning,
                                        tagged_reasoning,
                                    )
                        elif item_type == "function_call":
                            arguments = item.get("arguments")
                            parsed_arguments = parse_tool_arguments(arguments)
                            call_id = item.get("call_id")
                            name = item.get("name")
                            if isinstance(call_id, str) and call_id and isinstance(name, str) and name:
                                content.append(ToolUseBlock(id=call_id, name=name, input=parsed_arguments))
                    elif event_type == "response.completed":
                        response_payload = event.get("response")
                        if isinstance(response_payload, dict):
                            completed_response = response_payload
                    elif event_type == "response.failed":
                        response_payload = event.get("response")
                        if isinstance(response_payload, dict):
                            error = response_payload.get("error")
                            if isinstance(error, dict):
                                message = str(error.get("message") or error.get("code") or "Codex response failed")
                                raise RequestFailure(message)
                        raise RequestFailure("Codex response failed")
                    elif event_type == "error":
                        message = str(event.get("message") or event.get("code") or "Codex error")
                        raise RequestFailure(message)
        except httpx.HTTPStatusError as exc:
            # 检查是否为 effort 不支持错误
            if _is_effort_unsupported_error(exc) and request.effort is not None:
                # 直接向用户反馈错误，不进行降级
                raise RequestFailure(
                    f"当前模型不支持推理强度 '{request.effort.value}'，请尝试使用其他推理强度级别（如 low/medium/high）"
                ) from exc
            raise

        if current_text_parts and not any(isinstance(block, TextBlock) for block in content):
            plain_text, tagged_reasoning = split_thinking_from_text("".join(current_text_parts))
            if plain_text:
                content.insert(0, TextBlock(text=plain_text))
            if tagged_reasoning:
                collected_reasoning = merge_reasoning_text(collected_reasoning, tagged_reasoning)

        if collected_reasoning:
            content.insert(0, ThinkingBlock(thinking=collected_reasoning))

        final_message = ConversationMessage(role="assistant", content=content)
        usage = _usage_from_response(completed_response or {})
        stop_reason = _stop_reason_from_response(
            completed_response or {},
            has_tool_calls=bool(final_message.tool_uses),
        )
        yield ApiMessageCompleteEvent(
            message=final_message,
            usage=usage,
            stop_reason=stop_reason,
        )

    async def _iter_sse_events(self, response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if line == "":
                if data_lines:
                    payload = "\n".join(data_lines).strip()
                    data_lines = []
                    if payload and payload != "[DONE]":
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event, dict):
                            yield event
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            payload = "\n".join(data_lines).strip()
            if payload and payload != "[DONE]":
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    return
                if isinstance(event, dict):
                    yield event

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {429, 500, 502, 503, 504}
        if isinstance(exc, RateLimitFailure):
            return True
        if isinstance(exc, RequestFailure):
            message = str(exc).lower()
            return any(term in message for term in ["timeout", "connect", "network", "rate", "overloaded"])
        return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))

    @staticmethod
    def _translate_error(exc: Exception) -> IllusionAgentApiError:
        if isinstance(exc, IllusionAgentApiError):
            return exc
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            return _translate_status_error(status, str(exc))
        if isinstance(exc, httpx.HTTPError):
            return RequestFailure(str(exc))
        return RequestFailure(str(exc))
