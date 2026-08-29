"""
OpenAI Responses API 客户端模块
==============================

本模块提供通用 OpenAI Responses API（``{base}/responses``）客户端，
对应 env 配置 ``api_format="response"``。与 codex_client（ChatGPT 订阅
专用端点）不同，本客户端面向标准 API Key 认证的 Responses 端点
（OpenAI、Azure、各类 Responses 兼容网关）。

主要功能：
    - 流式文本/思考/工具调用事件
    - reasoning item 捕获与回传（思考回传校验，见下）
    - 孤儿 tool_use 自动补齐合成 tool 结果
    - reasoning 回传校验失败自愈重试
    - 400 格式类错误体落盘（error_log）
    - 自动重试 transient 错误

思考回传（reasoning item passback）：
    Responses API 的推理模型在 ``store: false`` 下要求：历史中每个
    ``function_call`` item 前必须紧邻其配对的 ``reasoning`` item（含
    ``encrypted_content``），缺失返回 400 "Item 'fc_...' of type
    'function_call' was provided without its required 'rs_...' item"；
    反之孤立的 reasoning item（后面没有跟随项）同样 400。这与 DeepSeek
    思考模式的 reasoning_content/content[].thinking 回传是同一类校验。

    处理方式：请求带 ``include: ["reasoning.encrypted_content"]``，流式
    捕获 reasoning item（存入每个 ToolUseBlock 的 provider_data），回放时
    在其配对的第一个 function_call 前原样插回（同轮多个工具调用共享一个
    reasoning item，只插一次）。若端点不支持加密思考内容导致回传校验仍
    失败，则降级重试（store: true，尝试让服务端按 item id 解析配对——
    该行为依赖端点实现，未在所有兼容端点上验证）；降级仍失败时抛出带
    处置建议的错误。降级状态记忆在实例上（_prefer_store）。

类说明：
    - ResponsesApiClient: OpenAI Responses API 客户端类

使用示例：
    >>> from illusion.api.responses_client import ResponsesApiClient
    >>> client = ResponsesApiClient(api_key="sk-...")
    >>> request = ApiMessageRequest(model="gpt-5.4", messages=[])
    >>> async for event in client.stream_message(request):
    >>>     print(event)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
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
from illusion.api.codex_client import (
    _format_error_message,
    _is_effort_unsupported_error,
    _stop_reason_from_response,
    _usage_from_response,
)
from illusion.api.compat import (
    is_reasoning_item_passback_error,
    merge_reasoning_text,
    parse_tool_arguments,
    split_thinking_from_text,
)
from illusion.api.error_log import log_api_error
from illusion.api.errors import (
    AuthenticationFailure,
    IllusionAgentApiError,
    RateLimitFailure,
    RequestFailure,
)
from illusion.engine.messages import (
    ContentBlock,
    ConversationMessage,
    MediaBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from illusion.utils.http import create_async_client

# 模块级日志记录器
log = logging.getLogger(__name__)

# 常量定义
DEFAULT_RESPONSES_BASE_URL = "https://api.openai.com/v1"  # 默认基础 URL
MAX_RETRIES = 3  # 最大重试次数
BASE_DELAY_SECONDS = 1.0  # 基础延迟（秒）
MAX_DELAY_SECONDS = 30.0  # 最大延迟（秒）
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}  # 可重试的状态码集合


def _resolve_responses_url(base_url: str | None) -> str:
    """解析并返回 Responses API URL

    Args:
        base_url: 可选的基础 URL（如 https://api.openai.com/v1）

    Returns:
        str: 完整的 Responses API URL（{base}/responses）
    """
    raw = (base_url or "").strip().rstrip("/") or DEFAULT_RESPONSES_BASE_URL
    if raw.endswith("/responses"):
        return raw
    return f"{raw}/responses"


def _image_part(block: MediaBlock) -> dict[str, Any]:
    """将图片 MediaBlock 转换为 Responses API input_image 内容部分。"""
    return {
        "type": "input_image",
        "image_url": f"data:{block.media_type};base64,{block.data}",
    }


def _reasoning_item_of(block: ToolUseBlock) -> dict[str, Any] | None:
    """读取 ToolUseBlock 上捕获的配对 reasoning item（无则 None）。"""
    item = block.provider_data.get("reasoning_item")
    return item if isinstance(item, dict) else None


def _function_call_item_id(block: ToolUseBlock) -> str:
    """读取 ToolUseBlock 上捕获的 function_call item id（无则空串）。"""
    item_id = block.provider_data.get("item_id")
    return item_id if isinstance(item_id, str) else ""


def _convert_messages_to_responses_input(
    messages: list[ConversationMessage],
) -> list[dict[str, Any]]:
    """将对话消息转换为 Responses API input items

    主要差异（相对 chat/completions）：
    - assistant 文本 → ``{"type": "message", "role": "assistant"}`` item
    - tool_use → ``{"type": "function_call"}`` item，其捕获的配对
      reasoning item 紧邻其前回传（思考回传校验，见模块 docstring）
    - tool_result → ``{"type": "function_call_output"}`` item
    - 会话中断等导致的孤儿 tool_use 自动补齐合成 tool 结果，
      避免 API 400（与 openai_client 同一语义）

    Args:
        messages: 对话消息列表

    Returns:
        list[dict[str, Any]]: Responses input items
    """
    items: list[dict[str, Any]] = []

    # 跟踪上一条 assistant 消息中尚未收到 tool_result 的 tool_use ID
    pending_tool_use_ids: list[str] = []

    def _flush_pending() -> None:
        """为所有未收到结果的 tool_use 合成错误 tool 输出。"""
        for call_id in pending_tool_use_ids:
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": "Tool execution interrupted",
            })
        pending_tool_use_ids.clear()

    for msg in messages:
        if msg.role == "assistant":
            _flush_pending()
            # 本条 assistant 消息中已回传的 reasoning item id
            # （同轮多个 function_call 共享一个 reasoning item，只插一次）
            emitted_reasoning_ids: set[str] = set()
            text_parts: list[str] = []

            def _flush_text(parts: list[str]) -> None:
                """把累积的文本块作为 message item 输出（保持原始顺序）。"""
                if not parts:
                    return
                plain_text, _tagged = split_thinking_from_text("".join(parts))
                parts.clear()
                if plain_text:
                    items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": plain_text}],
                    })

            # 按内容块顺序回放（与原始 output item 到达顺序一致）：文本
            # message 与 function_call 的相对位置保持原样，reasoning item
            # 惰性插在其配对的第一个 function_call 之前（紧邻性校验要求）
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                    continue
                if not isinstance(block, ToolUseBlock):
                    continue
                _flush_text(text_parts)
                reasoning_item = _reasoning_item_of(block)
                if reasoning_item is not None and reasoning_item.get("encrypted_content"):
                    item_id = reasoning_item.get("id")
                    if not isinstance(item_id, str) or item_id not in emitted_reasoning_ids:
                        if isinstance(item_id, str):
                            emitted_reasoning_ids.add(item_id)
                        items.append(reasoning_item)
                function_call: dict[str, Any] = {
                    "type": "function_call",
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": json.dumps(block.input, separators=(",", ":")),
                }
                captured_id = _function_call_item_id(block)
                if captured_id:
                    function_call["id"] = captured_id
                items.append(function_call)
                pending_tool_use_ids.append(block.id)
            _flush_text(text_parts)
            continue

        # user 消息：文本 / tool_result / 媒体
        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        text_blocks = [b for b in msg.content if isinstance(b, TextBlock)]
        media_blocks = [b for b in msg.content if isinstance(b, MediaBlock)]

        if tool_results:
            # 检查缺失的 tool_result，合成错误输出补齐
            provided_ids = {tr.tool_use_id for tr in tool_results}
            for call_id in pending_tool_use_ids:
                if call_id not in provided_ids:
                    items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": "Tool execution interrupted",
                    })
            pending_tool_use_ids.clear()

            for tr in tool_results:
                items.append({
                    "type": "function_call_output",
                    "call_id": tr.tool_use_id,
                    "output": tr.text_content,
                })
                # 工具结果中的媒体通过独立的 user 消息传递
                if isinstance(tr.content, list):
                    tr_media = [b for b in tr.content if isinstance(b, MediaBlock)]
                    if tr_media:
                        items.append({
                            "role": "user",
                            "content": [_image_part(mb) for mb in tr_media],
                        })
        else:
            _flush_pending()

        text = "".join(block.text for block in text_blocks)
        if text.strip() or media_blocks:
            parts: list[dict[str, Any]] = []
            if text.strip():
                parts.append({"type": "input_text", "text": text})
            for mb in media_blocks:
                parts.append(_image_part(mb))
            items.append({"role": "user", "content": parts})
        if not tool_results and not text_blocks and not media_blocks:
            # 空用户消息（不应发生，但需优雅处理）
            items.append({"role": "user", "content": [{"type": "input_text", "text": ""}]})

    _flush_pending()
    return items


def _convert_tools_to_responses(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 Anthropic 风格工具模式转换为 Responses 扁平 function 工具

    Args:
        tools: Anthropic 格式的工具定义列表

    Returns:
        list[dict[str, Any]]: Responses 格式的工具定义列表
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


class ResponsesApiClient:
    """通用 OpenAI Responses API 客户端（api_format="response"）

    Attributes:
        _api_key: API 密钥（Bearer）
        _auth_token: Bearer Token（优先于 api_key）
        _base_url: 基础 URL
        _url: 解析后的 Responses API URL
        _prefer_store: 实例级降级记忆——端点出现过 reasoning item 回传
            校验 400 后置 True，后续请求直接以 store: true 发起，避免
            每轮重复"失败一次再降级"
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._api_key = api_key or ""
        self._auth_token = auth_token or ""
        self._base_url = base_url
        self._url = _resolve_responses_url(base_url)
        self._prefer_store = False

    def _resolve_auth_token(self) -> str:
        """解析当前 Bearer 令牌（auth_token 优先，其次 api_key）

        Returns:
            str: Bearer 令牌

        Raises:
            AuthenticationFailure: 未配置任何凭据
        """
        token = self._auth_token or self._api_key
        if not token:
            raise AuthenticationFailure("Responses API credential is missing")
        return token

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """流式生成文本增量并在 transient 错误时自动重试

        reasoning 回传校验失败时自愈重试一次（store: true，尝试让服务端
        按 item id 解析 reasoning 配对——该行为依赖端点实现，未在所有
        Responses 兼容端点上验证）；仍失败则抛出带处置建议的错误。降级
        状态记忆在实例上（_prefer_store）：已证明不支持加密思考回传的
        端点，后续请求直接以 store: true 发起，避免每轮先失败一次。

        Args:
            request: API 消息请求

        Yields:
            ApiStreamEvent: 流式事件
        """
        last_error: Exception | None = None
        # 思考回传自愈状态：降级 store: true 重试一次
        reasoning_fallback_tried = False
        # 实例级记忆：该端点曾出现回传校验 400 → 后续请求直接 store: true
        store_flag = self._prefer_store

        for attempt in range(MAX_RETRIES + 1):
            try:
                async for event in self._stream_once(request, store=store_flag):
                    yield event
                return
            except Exception as exc:
                last_error = exc
                # 400 格式类错误体落盘（如 reasoning 回传校验失败），便于事后取证
                log_api_error(exc, provider="responses", model=request.model)
                # reasoning item 回传校验失败：首次降级 store: true 重试；
                # 降级后仍失败则抛出可行动的错误（端点不支持加密思考回传）
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code == 400
                    and is_reasoning_item_passback_error(str(exc))
                ):
                    if not reasoning_fallback_tried and not store_flag:
                        reasoning_fallback_tried = True
                        store_flag = True
                        self._prefer_store = True
                        log.warning(
                            "Responses reasoning-item passback validation failed; "
                            "retrying with store=true.",
                        )
                        yield ApiRetryEvent(
                            message=str(exc),
                            attempt=attempt + 1,
                            max_attempts=MAX_RETRIES + 1,
                            delay_seconds=0.0,
                        )
                        continue
                    raise RequestFailure(
                        "该 Responses 端点无法完成 reasoning item 回传校验："
                        "历史中缺少可回传的加密思考内容（端点可能不支持 "
                        "include: reasoning.encrypted_content），且 store: true "
                        "降级也被拒绝。请检查端点是否完整支持 Responses API "
                        "的推理模型，或改用 openai（chat/completions）格式接入。"
                    ) from exc
                if attempt >= MAX_RETRIES or not self._is_retryable(exc):
                    raise self._translate_error(exc) from exc
                delay = min(BASE_DELAY_SECONDS * (2 ** attempt), MAX_DELAY_SECONDS)
                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
        if last_error is not None:
            raise self._translate_error(last_error) from last_error

    async def _stream_once(
        self,
        request: ApiMessageRequest,
        *,
        store: bool = False,
    ) -> AsyncIterator[ApiStreamEvent]:
        """单次尝试流式消息

        Args:
            request: API 消息请求
            store: 是否让服务端存储响应 items（自愈降级时为 True）

        Yields:
            ApiStreamEvent: 流式事件
        """
        body: dict[str, Any] = {
            "model": request.model,
            "input": _convert_messages_to_responses_input(request.messages),
            "stream": True,
            "store": store,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        if request.system_prompt:
            body["instructions"] = request.system_prompt
        if request.max_tokens:
            body["max_output_tokens"] = request.max_tokens
        # 加密思考内容仅对 store:false 有意义（store:true 时服务端持有
        # 原始 items，部分端点拒绝该 include 组合）
        if not store:
            body["include"] = ["reasoning.encrypted_content"]
        if request.tools:
            body["tools"] = _convert_tools_to_responses(request.tools)
        # 添加 effort 字段（推理深度）
        if request.effort is not None:
            body["reasoning"] = {"effort": request.effort.value}

        content: list[ContentBlock] = []
        current_text_parts: list[str] = []
        collected_reasoning = ""
        # 最近一个已完成的 reasoning item（配对给其后的 function_call）
        pending_reasoning_item: dict[str, Any] | None = None
        completed_response: dict[str, Any] | None = None

        headers = {
            "Authorization": f"Bearer {self._resolve_auth_token()}",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }
        try:
            async with create_async_client(timeout=60.0, follow_redirects=True) as client, client.stream(
                "POST", self._url, headers=headers, json=body
            ) as response:
                if response.status_code >= 400:
                    payload = await response.aread()
                    message = _format_error_message(
                        response.status_code,
                        payload.decode("utf-8", "replace"),
                        provider_label="Responses",
                    )
                    raise httpx.HTTPStatusError(message, request=response.request, response=response)

                async for event in _iter_sse_events(response):
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
                            collected_reasoning = _merge_reasoning(collected_reasoning, delta)
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
                        if item_type == "reasoning":
                            # 捕获完整 reasoning item（含 encrypted_content），
                            # 配对给其后的 function_call 用于回传
                            pending_reasoning_item = item
                        elif item_type == "message":
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
                                    collected_reasoning = _merge_reasoning(
                                        collected_reasoning, tagged_reasoning,
                                    )
                        elif item_type == "function_call":
                            arguments = item.get("arguments")
                            parsed_arguments = parse_tool_arguments(arguments)
                            call_id = item.get("call_id")
                            name = item.get("name")
                            if isinstance(call_id, str) and call_id and isinstance(name, str) and name:
                                # 保留配对的 reasoning item 与 item id 供回放
                                provider_data: dict[str, Any] = {}
                                if isinstance(pending_reasoning_item, dict):
                                    provider_data["reasoning_item"] = pending_reasoning_item
                                item_id = item.get("id")
                                if isinstance(item_id, str) and item_id:
                                    provider_data["item_id"] = item_id
                                content.append(ToolUseBlock(
                                    id=call_id,
                                    name=name,
                                    input=parsed_arguments,
                                    provider_data=provider_data,
                                ))
                    elif event_type == "response.completed":
                        response_payload = event.get("response")
                        if isinstance(response_payload, dict):
                            completed_response = response_payload
                    elif event_type == "response.failed":
                        response_payload = event.get("response")
                        if isinstance(response_payload, dict):
                            error = response_payload.get("error")
                            if isinstance(error, dict):
                                message = str(error.get("message") or error.get("code") or "Responses request failed")
                                raise RequestFailure(message)
                        raise RequestFailure("Responses request failed")
                    elif event_type == "error":
                        message = str(event.get("message") or event.get("code") or "Responses error")
                        raise RequestFailure(message)
        except httpx.HTTPStatusError as exc:
            # 检查是否为 effort 不支持错误
            if _is_effort_unsupported_error(exc) and request.effort is not None:
                # 直接向用户反馈错误，不进行降级
                raise RequestFailure(
                    f"当前模型不支持推理强度 '{request.effort.value}'，请尝试使用其他推理强度级别（如 low/medium/high）"
                ) from exc
            raise

        if completed_response is None:
            # SSE 流中断且未收到 response.completed：视为失败交给重试机制
            # 处理，而非以零 usage / 无终止原因静默"成功"
            raise RequestFailure("Responses stream ended without response.completed")

        if current_text_parts and not any(isinstance(block, TextBlock) for block in content):
            plain_text, tagged_reasoning = split_thinking_from_text("".join(current_text_parts))
            if plain_text:
                content.insert(0, TextBlock(text=plain_text))
            if tagged_reasoning:
                collected_reasoning = _merge_reasoning(collected_reasoning, tagged_reasoning)

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

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """检查异常是否可重试

        Args:
            exc: 待检查的异常

        Returns:
            bool: 是否可重试
        """
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in RETRYABLE_STATUS_CODES
        if isinstance(exc, RateLimitFailure):
            return True
        if isinstance(exc, RequestFailure):
            message = str(exc).lower()
            return any(term in message for term in ["timeout", "connect", "network", "rate", "overloaded"])
        return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))

    @staticmethod
    def _translate_error(exc: Exception) -> IllusionAgentApiError:
        """转换错误为统一异常类型

        Args:
            exc: 原始异常

        Returns:
            IllusionAgentApiError: 统一异常类型
        """
        if isinstance(exc, IllusionAgentApiError):
            return exc
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            message = str(exc)
            if status in {401, 403}:
                return AuthenticationFailure(message)
            if status == 429:
                return RateLimitFailure(message)
            return RequestFailure(message)
        return RequestFailure(str(exc))


async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """解析 Responses API SSE 流为事件字典

    Args:
        response: httpx 流式响应

    Yields:
        dict[str, Any]: SSE data 载荷（JSON 对象）
    """
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


def _merge_reasoning(collected: str, delta: str) -> str:
    """合并推理文本片段（去除跨片段重复）。"""
    return merge_reasoning_text(collected, delta)
