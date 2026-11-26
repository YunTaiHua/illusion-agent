"""
Token 估算与上下文窗口工具函数。
"""

from __future__ import annotations

import logging

from illusion.engine.messages import (
    ConversationMessage,
    MediaBlock,
    SlashResourceBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from illusion.services.compact.constants import (
    _DEFAULT_CONTEXT_WINDOW,
    AUTOCOMPACT_BUFFER_TOKENS,
    MANUAL_COMPACT_BUFFER_TOKENS,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    TOKEN_ESTIMATION_PADDING,
    WARNING_THRESHOLD_BUFFER_TOKENS,
)
from illusion.services.compact.models import AutoCompactState, TokenWarningState
from illusion.services.token_estimation import estimate_tokens

log = logging.getLogger(__name__)


def estimate_message_tokens(messages: list[ConversationMessage]) -> int:
    """估算会话消息的总 Token 数，包含 4/3 padding。"""
    total = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, (TextBlock, SlashResourceBlock)):
                total += estimate_tokens(block.text)
            elif isinstance(block, ToolResultBlock):
                if isinstance(block.content, str):
                    total += estimate_tokens(block.content)
                elif isinstance(block.content, list):
                    for inner in block.content:
                        if isinstance(inner, TextBlock):
                            total += estimate_tokens(inner.text)
                        elif isinstance(inner, MediaBlock):
                            total += 2000
            elif isinstance(block, ToolUseBlock):
                total += estimate_tokens(block.name)
                total += estimate_tokens(str(block.input))
            elif isinstance(block, ThinkingBlock):
                total += estimate_tokens(block.thinking)
                if block.signature:
                    total += estimate_tokens(block.signature)
            elif isinstance(block, MediaBlock):
                total += 2000
    return int(total * TOKEN_ESTIMATION_PADDING)


def estimate_conversation_tokens(messages: list[ConversationMessage]) -> int:
    """保持向后兼容性的别名。"""
    return estimate_message_tokens(messages)


def get_context_window() -> int:
    """返回当前配置的上下文窗口大小。

    Returns:
        int: 上下文窗口 token 数
    """
    from illusion.config.settings import load_settings

    settings = load_settings()
    if settings.context_window and settings.context_window > 0:
        return settings.context_window

    return _DEFAULT_CONTEXT_WINDOW


def get_autocompact_threshold(model: str) -> int:
    """计算触发自动压缩的 Token 数量阈值。"""
    context_window = get_context_window()
    reserved = min(MAX_OUTPUT_TOKENS_FOR_SUMMARY, 20_000)
    effective = context_window - reserved
    return effective - AUTOCOMPACT_BUFFER_TOKENS


def calculate_token_warning_state(
    messages: list[ConversationMessage],
    model: str,
    *,
    auto_compact_enabled: bool = True,
    context_tokens: int | None = None,
) -> TokenWarningState:
    """计算当前上下文使用量的警告状态。

    Args:
        messages: 会话消息列表
        model: 模型名称
        auto_compact_enabled: 是否启用自动压缩
        context_tokens: 外部提供的上下文占用（真实值），None 时回退到本地估算
    """
    estimated = (
        context_tokens
        if context_tokens is not None
        else estimate_message_tokens(messages)
    )
    context_window = get_context_window()
    threshold = get_autocompact_threshold(model)

    is_above_autocompact = estimated >= threshold
    is_above_warning = estimated >= (threshold - WARNING_THRESHOLD_BUFFER_TOKENS)
    is_at_blocking = (
        not auto_compact_enabled
        and estimated >= (context_window - MANUAL_COMPACT_BUFFER_TOKENS)
    )

    return TokenWarningState(
        is_above_warning_threshold=is_above_warning,
        is_above_autocompact_threshold=is_above_autocompact,
        is_at_blocking_limit=is_at_blocking,
        estimated_tokens=estimated,
        threshold=threshold,
        context_window=context_window,
    )


def should_autocompact(
    messages: list[ConversationMessage],
    model: str,
    state: AutoCompactState,
    context_tokens: int | None = None,
) -> bool:
    """返回是否应该自动压缩会话。

    使用与 /context usage 相同的计算方式：
    Estimated Used = 最后一次 API 调用的真实 context_size + 新增消息估算
    （无 API 数据时回退到本地消息估算）

    Args:
        messages: 会话消息列表
        model: 模型名称
        state: 自动压缩状态
        context_tokens: 外部提供的上下文占用（真实值），None 时回退到本地估算
    """
    if state.consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        return False
    token_count = (
        context_tokens
        if context_tokens is not None
        else estimate_message_tokens(messages)
    )
    threshold = get_autocompact_threshold(model)
    return token_count >= threshold
