"""
Anthropic API 客户端模块
=======================

本模块提供 Anthropic API 客户端封装，带有重试逻辑。

主要功能：
    - 流式文本增量生成
    - 自动重试 transient 错误
    - 错误转换

类说明：
    - AnthropicApiClient: Anthropic 异步 SDK 封装类
    - ApiMessageRequest: 模型调用输入参数
    - ApiTextDeltaEvent: 增量文本事件
    - ApiMessageCompleteEvent: 完整消息事件
    - ApiRetryEvent: 重试事件

使用示例：
    >>> from illusion.api.client import AnthropicApiClient, ApiMessageRequest
    >>> client = AnthropicApiClient(api_key="sk-...")
    >>> request = ApiMessageRequest(model="claude-3-sonnet", messages=[])
    >>> async for event in client.stream_message(request):
    >>>     print(event)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from anthropic import APIError, APIStatusError, AsyncAnthropic
from anthropic.types import ThinkingBlock as _SDKThinkingBlock

# 兼容第三方 API（如 MiMo）返回 "signature": null 的情况
# anthropic SDK 的 ThinkingBlock 要求 signature 为 str，但部分提供商返回 null
_sdk_sig_field = _SDKThinkingBlock.model_fields["signature"]
_sdk_sig_field.annotation = str | None  # type: ignore[assignment]
_SDKThinkingBlock.model_rebuild()

from illusion.api.compat import (
    THINKING_PASSBACK_PLACEHOLDER,
    is_thinking_passback_error,
    model_consumes_thinking_passback,
)
from illusion.api.effort import EffortLevel
from illusion.api.error_log import log_api_error
from illusion.api.errors import (
    AuthenticationFailure,
    IllusionAgentApiError,
    RateLimitFailure,
    RequestFailure,
)
from illusion.api.usage import UsageSnapshot
from illusion.config.capabilities import ModelCapabilities
from illusion.engine.messages import (
    ConversationMessage,
    ThinkingBlock,
    _messages_have_media,
    _strip_media_from_messages,
    assistant_message_from_api,
    strip_media_if_unsupported,
)

# 模块级日志记录器
log = logging.getLogger(__name__)

# 重试配置常量
MAX_RETRIES = 3  # 最大重试次数
BASE_DELAY = 1.0  # 基础延迟（秒）
MAX_DELAY = 30.0  # 最大延迟（秒）
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}  # 可重试的状态码集合


@dataclass(frozen=True)
class ApiMessageRequest:
    """模型调用输入参数

    包含调用模型所需的所有参数。

    Attributes:
        model: 模型名称
        messages: 对话消息列表
        system_prompt: 系统提示词（可选）
        max_tokens: 最大令牌数（默认 4096）
        tools: 工具定义列表（默认空列表）
        effort: 推理强度级别（可选，支持 low/medium/high/xhigh/max）
        prompt_cache_key: 提示缓存路由键（可选，通常为会话 ID）。仅 Kimi
            （Moonshot）消费该字段——稳定同一会话的前缀缓存命中
        capabilities: 当前模型的媒体能力。None 表示未声明（fail-closed，
            视为无任何媒体能力）；发送前按此决定媒体块转文本占位
    """

    model: str
    messages: list[ConversationMessage]
    system_prompt: str | None = None
    max_tokens: int = 4096
    tools: list[dict[str, Any]] = field(default_factory=list[Any])
    effort: EffortLevel | None = None
    extra_body: dict[str, Any] | None = None
    prompt_cache_key: str | None = None
    capabilities: ModelCapabilities | None = None


@dataclass(frozen=True)
class ApiTextDeltaEvent:
    """增量文本事件
    
    模型产生的增量文本输出。
    
    Attributes:
        text: 增量文本内容
        reasoning: 增量思考内容（可选）
    """

    text: str
    reasoning: str | None = None


@dataclass(frozen=True)
class ApiMessageCompleteEvent:
    """完整消息事件
    
    包含最终助手消息和完整使用量信息的事件。
    
    Attributes:
        message: 对话消息对象
        usage: 使用量快照
        stop_reason: 停止原因
    """

    message: ConversationMessage
    usage: UsageSnapshot
    stop_reason: str | None = None


@dataclass(frozen=True)
class ApiRetryEvent:
    """重试事件
    
    表示可恢复的上游错误，将自动重试。
    
    Attributes:
        message: 错误消息
        attempt: 当前尝试次数
        max_attempts: 最大尝试次数
        delay_seconds: 延迟秒数
    """

    message: str
    attempt: int
    max_attempts: int
    delay_seconds: float


@dataclass(frozen=True)
class ApiToolCallStartedEvent:
    """工具调用开始生成事件

    当模型开始生成工具调用时产生，包含工具名称和调用ID。
    此事件在模型开始输出工具参数之前发出，使前端能够
    立即显示工具调用指示器，而不必等待整个工具参数生成完毕。

    Attributes:
        tool_name: 工具名称
        tool_use_id: 工具调用ID（可选）
    """

    tool_name: str
    tool_use_id: str = ""


# 流事件联合类型
ApiStreamEvent = ApiTextDeltaEvent | ApiMessageCompleteEvent | ApiRetryEvent | ApiToolCallStartedEvent


class SupportsStreamingMessages(Protocol):
    """流式消息协议
    
    查询引擎在测试和生产中使用的协议。
    """

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:  # pyright: ignore[reportReturnType]
        """为请求产生流式事件"""


def repair_thinking_passback(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    """为缺少 thinking 块的 assistant 消息合成占位思考块。

    DeepSeek v4 思考模式要求请求历史中每条 assistant 消息都携带思考内容
    （content[].thinking），缺失时返回 400。历史里缺块的轮次无法找回原文
    （上游当时未返回、或被网关丢弃），用占位文本补齐以满足存在性校验；
    上游将其作为该轮 reasoning 拼入上下文，无其他副作用。

    已有 thinking 块（含 redacted_thinking）的消息原样保留，因此本函数
    幂等，可重复调用。

    Args:
        messages: 对话消息列表

    Returns:
        list[ConversationMessage]: 修复后的新消息列表（不修改入参）
    """
    repaired: list[ConversationMessage] = []
    for msg in messages:
        if msg.role == "assistant" and not any(
            isinstance(block, ThinkingBlock) for block in msg.content
        ):
            new_msg = msg.model_copy(deep=True)
            new_msg.content.insert(0, ThinkingBlock(
                thinking=THINKING_PASSBACK_PLACEHOLDER,
                signature="",
            ))
            repaired.append(new_msg)
        else:
            repaired.append(msg)
    return repaired


def _is_media_related_error(exc: Exception) -> bool:
    """检查错误是否可能由图片内容导致

    Anthropic API 在不支持图片时可能返回：
    - 400 invalid_request_error
    - 404 "No endpoints found that support image input"

    注意：错误可能已被 _translate_api_error 转为 IllusionAgentApiError，
    此时 status_code 属性丢失，需从消息字符串中判断。
    """
    error_msg = str(exc).lower()
    status = getattr(exc, "status_code", None)

    # 从错误消息字符串中提取状态码（适配已翻译的异常）
    if status is None:
        for code in (404, 400):
            if f"error code: {code}" in error_msg:
                status = code
                break

    # 明确的图片不支持错误（404 或 400）
    if status in {400, 404} and any(
        kw in error_msg for kw in ("image", "media", "unsupported")
    ):
        return True

    # 某些提供商返回的通用错误
    return "does not support" in error_msg and "image" in error_msg


def _is_retryable(exc: Exception) -> bool:
    """检查异常是否可重试
    
    Args:
        exc: 待检查的异常
    
    Returns:
        bool: 是否可重试
    """
    # API 状态错误：检查状态码
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    # API 错误：网络错误可重试
    if isinstance(exc, APIError):
        return True
    # 连接错误可重试
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _get_retry_delay(attempt: int, exc: Exception | None = None) -> float:
    """计算指数退避延迟（带抖动）
    
    Args:
        attempt: 当前尝试次数
        exc: 异常对象（可选）
    
    Returns:
        float: 延迟秒数
    """
    import random

    # 检查 Retry-After 头
    if isinstance(exc, APIStatusError):
        retry_after = getattr(exc, "headers", {})
        if hasattr(retry_after, "get"):
            val = retry_after.get("retry-after")
            if val:
                try:
                    return min(float(val), MAX_DELAY)
                except (ValueError, TypeError):
                    pass

    # 指数退避计算
    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
    # 添加随机抖动（0-25%）
    jitter = random.uniform(0, delay * 0.25)
    return float(delay + jitter)


class AnthropicApiClient:
    """Anthropic 异步 SDK 封装类

    带重试逻辑的 Anthropic API 薄封装。

    Attributes:
        _api_key: API 密钥
        _base_url: 基础 URL
        _client: AsyncAnthropic 客户端实例
        _force_passback_repair: 思考回传修复记忆（实例级）。一旦某端点在
            请求中出现过回传校验 400（reactive 修复触发），后续所有请求
            直接主动补齐占位 thinking 块，避免每轮重复"失败一次再修复"。
            客户端实例与 env 一一对应且随会话存活，实例级状态即可覆盖。
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._auth_token = auth_token
        self._force_passback_repair = False
        self._client = self._create_client()

    def _create_client(self) -> AsyncAnthropic:
        """创建 Anthropic 客户端

        Returns:
            AsyncAnthropic: 配置好的客户端实例
        """
        kwargs: dict[str, Any] = {}
        # 优先使用 auth_token（Bearer Token），否则使用 api_key（x-api-key）
        if self._auth_token:
            kwargs["auth_token"] = self._auth_token
        elif self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return AsyncAnthropic(**kwargs)

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """流式生成文本增量并在 transient 错误时自动重试

        当消息中包含图片但模型不支持时，自动降级为文本描述并重试。

        Args:
            request: API 消息请求

        Yields:
            ApiStreamEvent: 流式事件（文本增量或完整消息）
        """
        # 事前降级：按当前模型能力将不支持的媒体块转为文本占位。
        # 切换模型（含同 env 内 model_1→model_2）后历史中的图片由
        # request.capabilities 驱动，一次请求成功，无需等 API 报错重试。
        stripped = strip_media_if_unsupported(request.messages, request.capabilities)
        if stripped is not None:
            log.info(
                "Model %s lacks media capability; sending text placeholders instead of media.",
                request.model,
            )
            request = replace(request, messages=stripped)

        last_error: Exception | None = None
        media_stripped = False
        # 思考回传自愈状态：先补齐占位 thinking 块重试，仍失败则降级关闭思考
        thinking_repaired = False
        thinking_disabled = False

        for attempt in range(MAX_RETRIES + 1):
            try:
                async for event in self._stream_once(request):
                    yield event
                return  # 成功
            except IllusionAgentApiError as exc:
                last_error = exc
                # 思考回传校验失败（DeepSeek 思考模式 400）：先补齐占位
                # thinking 块重试；若补齐为空操作或上游已主动补齐（DeepSeek
                # 家族在 _stream_once 中处理），则降级为非思考模式，避免会话
                # 被历史缺块永久卡死（详见 repair_thinking_passback）。
                # 注意：必须先于 media 检查——回传错误文案含 "content"，会被
                # _is_media_related_error 误判而丢图片。
                is_passback_error = is_thinking_passback_error(exc)
                if is_passback_error and request.effort is not None:
                    if (
                        not thinking_repaired
                        and not model_consumes_thinking_passback(request.model)
                    ):
                        thinking_repaired = True
                        repaired_messages = repair_thinking_passback(request.messages)
                        if repaired_messages != request.messages:
                            log.warning(
                                "Thinking passback validation failed; "
                                "retrying with placeholder thinking blocks.",
                            )
                            # 记住该端点需要回传修复：后续请求直接主动补齐，
                            # 不再每轮先失败一次再进入 reactive 修复
                            self._force_passback_repair = True
                            yield ApiRetryEvent(
                                message=str(exc),
                                attempt=attempt + 1,
                                max_attempts=MAX_RETRIES + 1,
                                delay_seconds=0.0,
                            )
                            request = replace(request, messages=repaired_messages)
                            continue
                    if not thinking_disabled:
                        thinking_disabled = True
                        log.warning(
                            "Thinking passback validation still failing; "
                            "retrying with thinking disabled.",
                        )
                        yield ApiRetryEvent(
                            message=str(exc),
                            attempt=attempt + 1,
                            max_attempts=MAX_RETRIES + 1,
                            delay_seconds=0.0,
                        )
                        request = replace(request, effort=None)
                        continue
                    # 两级自愈都已生效仍失败：直接抛出
                    raise
                # 如果消息包含图片且错误可能是模型不支持图片导致的，尝试降级
                if (
                    not is_passback_error
                    and not media_stripped
                    and _messages_have_media(request.messages)
                    and _is_media_related_error(exc)
                ):
                    log.warning(
                        "Request failed, possibly due to unsupported image content. "
                        "Retrying with text descriptions instead of images.",
                    )
                    request = replace(
                        request,
                        messages=_strip_media_from_messages(request.messages),
                    )
                    media_stripped = True
                    continue
                raise
            except Exception as exc:
                last_error = exc
                # 如果消息包含图片且错误可能是模型不支持图片导致的，尝试降级
                if (
                    not media_stripped
                    and _messages_have_media(request.messages)
                    and _is_media_related_error(exc)
                ):
                    log.warning(
                        "Request failed, possibly due to unsupported image content. "
                        "Retrying with text descriptions instead of images.",
                    )
                    request = replace(
                        request,
                        messages=_strip_media_from_messages(request.messages),
                    )
                    media_stripped = True
                    continue

                # 超过最大重试次数或不可重试
                if attempt >= MAX_RETRIES or not _is_retryable(exc):
                    if isinstance(exc, APIError):
                        raise _translate_api_error(exc) from exc
                    raise RequestFailure(str(exc)) from exc

                # 计算延迟并发送重试事件
                delay = _get_retry_delay(attempt, exc)
                status = getattr(exc, "status_code", "?")
                log.warning(
                    "API request failed (attempt %d/%d, status=%s), retrying in %.1fs: %s",
                    attempt + 1, MAX_RETRIES + 1, status, delay, exc,
                )
                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

        # 最终错误处理
        if last_error is not None:
            if isinstance(last_error, APIError):
                raise _translate_api_error(last_error) from last_error
            raise RequestFailure(str(last_error)) from last_error

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """单次尝试流式消息
        
        Args:
            request: API 消息请求
        
        Yields:
            ApiStreamEvent: 流式事件
        """
        # 构建请求参数
        # DeepSeek 思考模型：thinking 启用时主动补齐历史中缺失的 thinking 块，
        # 避免上游对回传校验（content[].thinking must be passed back）返回 400。
        # 非 DeepSeek 命名端点一旦出现过回传 400（_force_passback_repair 记忆），
        # 同样主动补齐，避免每轮重复 reactive 修复。
        api_messages = request.messages
        if request.effort is not None and (
            model_consumes_thinking_passback(request.model) or self._force_passback_repair
        ):
            api_messages = repair_thinking_passback(api_messages)
        params: dict[str, Any] = {
            "model": request.model,
            "messages": [message.to_api_param(provider_type="anthropic") for message in api_messages],
            "max_tokens": request.max_tokens,
        }
        # 添加系统提示词
        if request.system_prompt:
            params["system"] = request.system_prompt
        # 添加工具定义
        if request.tools:
            params["tools"] = request.tools
        # Anthropic thinking 参数映射
        if request.effort is not None:
            effort_val = request.effort.value
            # 启用思考模式
            params["thinking"] = {"type": "enabled"}
            # 通过 output_config.effort 控制推理深度（兼容 Claude、DeepSeek、StepFun 等）
            params["output_config"] = {"effort": effort_val}

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    # 处理工具调用开始事件：模型开始生成工具调用时立即通知
                    if event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if getattr(block, "type", None) == "tool_use":
                            yield ApiToolCallStartedEvent(
                                tool_name=getattr(block, "name", ""),
                                tool_use_id=getattr(block, "id", ""),
                            )
                        continue
                    # 处理文本/思考增量事件
                    if event_type != "content_block_delta":
                        continue
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "text_delta":
                        text = getattr(delta, "text", "")
                        if text:
                            yield ApiTextDeltaEvent(text=text)
                        continue
                    if delta_type == "thinking_delta":
                        thinking = getattr(delta, "thinking", "") or getattr(delta, "text", "")
                        if thinking:
                            yield ApiTextDeltaEvent(text="", reasoning=thinking)

                # 获取最终消息
                final_message = await stream.get_final_message()
        except APIError as exc:
            # 400 格式类错误体落盘（如思考回传校验失败），便于事后取证
            log_api_error(exc, provider="anthropic", model=request.model)
            # 检查是否为 effort 不支持错误
            if _is_effort_unsupported_error(exc) and request.effort is not None:
                # 直接向用户反馈错误，不进行降级
                raise RequestFailure(
                    f"当前模型不支持推理强度 '{request.effort.value}'，请尝试使用其他推理强度级别（如 low/medium/high）"
                ) from exc
            # 可重试状态码直接抛出，让重试逻辑处理
            if isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS_CODES:
                raise
            raise _translate_api_error(exc) from exc

        # 提取使用量并发送完成事件
        usage = getattr(final_message, "usage", None)
        yield ApiMessageCompleteEvent(
            message=assistant_message_from_api(final_message),
            usage=UsageSnapshot(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                cache_read_input_tokens=int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0
                ),
                cache_creation_input_tokens=int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                ),
            ),
            stop_reason=getattr(final_message, "stop_reason", None),
        )


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


def _translate_api_error(exc: APIError) -> IllusionAgentApiError:
    """转换 API 错误为统一异常类型

    Args:
        exc: Anthropic API 错误

    Returns:
        IllusionAgentApiError: 统一异常类型
    """
    name = exc.__class__.__name__
    # 认证错误
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return AuthenticationFailure(str(exc))
    # 速率限制错误
    if name == "RateLimitError":
        return RateLimitFailure(str(exc))
    # 请求失败
    return RequestFailure(str(exc))
