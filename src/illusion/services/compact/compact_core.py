"""
核心压缩执行 — 调用 LLM 生成摘要。
"""

from __future__ import annotations

import logging
from typing import Any

from illusion.engine.messages import ConversationMessage, TextBlock
from illusion.services.compact.compact_prompt import (
    build_compact_summary_message,
    get_compact_prompt,
)
from illusion.services.compact.constants import (
    DEFAULT_KEEP_RECENT,
    DEFAULT_PRESERVE_RECENT,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    MAX_PTL_RETRIES,
)
from illusion.services.compact.message_ops import (
    _ensure_message_alternation,
    _find_safe_split_index,
    _group_messages_by_turn,
    _remove_orphaned_tool_results,
    create_compact_boundary_marker,
)
from illusion.services.compact.microcompact import microcompact_messages, strip_images_from_messages
from illusion.services.compact.token_utils import estimate_message_tokens

log = logging.getLogger(__name__)


async def compact_conversation(
    messages: list[ConversationMessage],
    *,
    api_client: Any,
    model: str,
    system_prompt: str = "",
    preserve_recent: int = DEFAULT_PRESERVE_RECENT,
    custom_instructions: str | None = None,
    suppress_follow_up: bool = True,
) -> list[ConversationMessage]:
    """通过调用 LLM 生成摘要来压缩消息。"""
    from illusion.api.client import ApiMessageCompleteEvent, ApiMessageRequest

    if len(messages) <= preserve_recent:
        return list(messages)

    messages, _ = microcompact_messages(messages, keep_recent=DEFAULT_KEEP_RECENT)
    messages = strip_images_from_messages(messages)

    pre_compact_tokens = estimate_message_tokens(messages)
    log.info("Compacting conversation: %d messages, ~%d tokens", len(messages), pre_compact_tokens)

    split_index = _find_safe_split_index(messages, preserve_recent)
    older = messages[:split_index]
    newer = messages[split_index:]

    compact_prompt = get_compact_prompt(custom_instructions)
    compact_messages_list = list(older) + [ConversationMessage.from_user_text(compact_prompt)]

    summary_text = ""
    ptl_retries = 0

    while ptl_retries <= MAX_PTL_RETRIES:
        try:
            async for event in api_client.stream_message(
                ApiMessageRequest(
                    model=model,
                    messages=[message.for_model() for message in compact_messages_list],
                    system_prompt=system_prompt or "You are a conversation summarizer.",
                    max_tokens=MAX_OUTPUT_TOKENS_FOR_SUMMARY,
                    tools=[],
                )
            ):
                if isinstance(event, ApiMessageCompleteEvent):
                    summary_text = event.message.text
            break
        except Exception as exc:
            error_msg = str(exc).lower()
            is_ptl = "prompt" in error_msg and "long" in error_msg
            if is_ptl and ptl_retries < MAX_PTL_RETRIES:
                ptl_retries += 1
                log.warning(
                    "Compact summary hit prompt-too-long, truncating head (retry %d/%d)",
                    ptl_retries, MAX_PTL_RETRIES,
                )
                groups = _group_messages_by_turn(compact_messages_list)
                if len(groups) > 2:
                    compact_messages_list = []
                    for g in groups[1:]:
                        compact_messages_list.extend(g)
                else:
                    log.error("Cannot truncate further for PTL retry")
                    break
            else:
                raise

    if not summary_text:
        log.warning("Compact summary was empty — returning original messages")
        return messages

    summary_content = build_compact_summary_message(
        summary_text,
        suppress_follow_up=suppress_follow_up,
        recent_preserved=len(newer) > 0,
    )
    summary_msg = ConversationMessage.from_user_text(summary_content)
    boundary_marker = create_compact_boundary_marker()

    result = [summary_msg, boundary_marker, *newer]
    result = _remove_orphaned_tool_results(result)
    result = _ensure_message_alternation(result)

    post_compact_tokens = estimate_message_tokens(result)
    log.info(
        "Compaction done: %d -> %d messages, ~%d -> ~%d tokens (saved ~%d)",
        len(messages), len(result),
        pre_compact_tokens, post_compact_tokens,
        max(0, pre_compact_tokens - post_compact_tokens),
    )
    return result


def summarize_messages(
    messages: list[ConversationMessage],
    *,
    max_messages: int = 8,
) -> str:
    """生成最近消息的紧凑文本摘要（传统方法，仅用于 /summary 命令）。"""
    selected = messages[-max_messages:]
    lines: list[str] = []
    for message in selected:
        text = message.text.strip()
        if not text:
            continue
        lines.append(f"{message.role}: {text[:300]}")
    return "\n".join(lines)


def compact_messages(
    messages: list[ConversationMessage],
    *,
    preserve_recent: int = DEFAULT_PRESERVE_RECENT,
) -> list[ConversationMessage]:
    """用合成摘要替换旧的会话历史（传统方法，仅作为后备）。"""
    if len(messages) <= preserve_recent:
        return list(messages)
    split_index = _find_safe_split_index(messages, preserve_recent)
    older = messages[:split_index]
    newer = messages[split_index:]
    summary = summarize_messages(older)
    if not summary:
        return list(newer)
    result = [
        ConversationMessage(
            role="user",
            content=[TextBlock(text=f"[conversation summary]\n{summary}")],
        ),
        create_compact_boundary_marker(),
        *newer,
    ]
    result = _remove_orphaned_tool_results(result)
    return _ensure_message_alternation(result)
