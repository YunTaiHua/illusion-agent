"""
自动压缩与响应式压缩。
"""

from __future__ import annotations

import logging
from typing import Any

from illusion.api.errors import IllusionAgentApiError
from illusion.engine.messages import ConversationMessage
from illusion.services.compact.compact_core import compact_conversation
from illusion.services.compact.constants import (
    DEFAULT_KEEP_RECENT,
    DEFAULT_PRESERVE_RECENT,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
)
from illusion.services.compact.microcompact import (
    microcompact_messages,
    strip_images_from_messages,
)
from illusion.services.compact.models import AutoCompactState
from illusion.services.compact.token_utils import should_autocompact

log = logging.getLogger(__name__)


async def reactive_compact(
    messages: list[ConversationMessage],
    *,
    api_client: Any,
    model: str,
    system_prompt: str = "",
    preserve_recent: int = DEFAULT_PRESERVE_RECENT,
    strip_media: bool = False,
) -> tuple[list[ConversationMessage], bool]:
    """当 API 返回 prompt-too-long / 请求体过大（413）错误时，尝试压缩并重试。

    Args:
        strip_media: 剥离全部图片媒体（base64 是请求体字节大头）。
            413 是网关对请求体字节大小的硬限制（而非模型 token 上限），
            仅按 token 估算压缩可能不减小真实字节，必须剥离媒体。
    """
    log.info("Reactive compact triggered (prompt-too-long or payload-too-large)")

    if strip_media:
        messages = strip_images_from_messages(messages)
    messages, tokens_freed = microcompact_messages(messages, keep_recent=DEFAULT_KEEP_RECENT)
    if tokens_freed > 0:
        log.info("Reactive microcompact freed ~%d tokens", tokens_freed)
        return messages, True

    try:
        result = await compact_conversation(
            messages,
            api_client=api_client,
            model=model,
            system_prompt=system_prompt,
            preserve_recent=preserve_recent,
            suppress_follow_up=True,
        )
        return result, True
    except IllusionAgentApiError as exc:
        log.error("Reactive compact failed: %s", exc)
        return messages, False


async def auto_compact_if_needed(
    messages: list[ConversationMessage],
    *,
    api_client: Any,
    model: str,
    system_prompt: str = "",
    state: AutoCompactState,
    preserve_recent: int = DEFAULT_PRESERVE_RECENT,
    context_tokens: int | None = None,
) -> tuple[list[ConversationMessage], bool]:
    """检查是否应该自动压缩，如果是则执行压缩。

    Args:
        context_tokens: 外部提供的上下文占用（真实值），
                        与 /context usage 的 "已用上下文" 保持一致
    """
    if not should_autocompact(messages, model, state, context_tokens=context_tokens):
        return messages, False

    log.info("Auto-compact triggered (failures=%d)", state.consecutive_failures)

    messages, tokens_freed = microcompact_messages(messages)
    if tokens_freed > 0:
        # microcompact 已释放 token：从 context_tokens 中减去释放量再判断
        adjusted = (
            None if context_tokens is None else max(0, context_tokens - tokens_freed)
        )
        if not should_autocompact(messages, model, state, context_tokens=adjusted):
            log.info("Microcompact freed ~%d tokens, auto-compact no longer needed", tokens_freed)
            state.warning_suppressed = True
            return messages, True

    try:
        result = await compact_conversation(
            messages,
            api_client=api_client,
            model=model,
            system_prompt=system_prompt,
            preserve_recent=preserve_recent,
            suppress_follow_up=True,
        )
        state.compacted = True
        state.turn_counter += 1
        state.last_compacted_at_turn = state.turn_counter
        state.consecutive_failures = 0
        state.warning_suppressed = True
        return result, True
    except IllusionAgentApiError as exc:
        state.consecutive_failures += 1
        log.error(
            "Auto-compact failed (attempt %d/%d): %s",
            state.consecutive_failures,
            MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
            exc,
        )
        return messages, False
