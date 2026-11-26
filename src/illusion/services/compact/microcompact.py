"""
微压缩 — 图片剥离与旧工具结果清除。
"""

from __future__ import annotations

import logging

from illusion.engine.messages import (
    ContentBlock,
    ConversationMessage,
    MediaBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from illusion.services.compact.constants import (
    COMPACTABLE_TOOLS,
    DEFAULT_KEEP_RECENT,
    TIME_BASED_MC_CLEARED_MESSAGE,
)
from illusion.services.token_estimation import estimate_tokens

log = logging.getLogger(__name__)


def strip_images_from_messages(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """将消息中的图片和文档替换为文本占位符。"""
    result: list[ConversationMessage] = []
    for msg in messages:
        new_blocks: list[ContentBlock] = []
        for block in msg.content:
            if isinstance(block, MediaBlock):
                new_blocks.append(TextBlock(
                    text=f"[image: {block.file_path}, {block.media_type}]"
                ))
            elif isinstance(block, ToolResultBlock):
                if isinstance(block.content, list):
                    stripped: list[ContentBlock] = []
                    for inner in block.content:
                        if isinstance(inner, MediaBlock):
                            stripped.append(TextBlock(
                                text=f"[image: {inner.file_path}, {inner.media_type}]"
                            ))
                        else:
                            stripped.append(inner)
                    new_blocks.append(ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=stripped,
                        is_error=block.is_error,
                    ))
                else:
                    new_blocks.append(block)
            else:
                new_blocks.append(block)
        result.append(msg.model_copy(update={"content": new_blocks}))
    return result


def _collect_compactable_tool_ids(messages: list[ConversationMessage]) -> list[str]:
    """遍历消息并收集可压缩的工具使用 ID。"""
    ids: list[str] = []
    for msg in messages:
        if msg.role != "assistant":
            continue
        for block in msg.content:
            if isinstance(block, ToolUseBlock) and block.name in COMPACTABLE_TOOLS:
                ids.append(block.id)
    return ids


def microcompact_messages(
    messages: list[ConversationMessage],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[ConversationMessage], int]:
    """清除旧的可压缩工具结果，保留最近的 keep_recent 个。"""
    keep_recent = max(1, keep_recent)
    all_ids = _collect_compactable_tool_ids(messages)

    if len(all_ids) <= keep_recent:
        return messages, 0

    keep_set = set(all_ids[-keep_recent:])
    clear_set = set(all_ids) - keep_set

    tokens_saved = 0
    for msg in messages:
        if msg.role != "user":
            continue
        new_content: list[ContentBlock] = []
        for block in msg.content:
            if (
                isinstance(block, ToolResultBlock)
                and block.tool_use_id in clear_set
            ):
                old_content = block.content
                if isinstance(old_content, str) and old_content == TIME_BASED_MC_CLEARED_MESSAGE:
                    new_content.append(block)
                    continue
                if isinstance(old_content, str):
                    tokens_saved += estimate_tokens(old_content)
                elif isinstance(old_content, list):
                    for inner in old_content:
                        if isinstance(inner, TextBlock):
                            tokens_saved += estimate_tokens(inner.text)
                        elif isinstance(inner, MediaBlock):
                            tokens_saved += 2000
                new_content.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=TIME_BASED_MC_CLEARED_MESSAGE,
                        is_error=block.is_error,
                    )
                )
            else:
                new_content.append(block)
        msg.content = new_content

    if tokens_saved > 0:
        log.info("Microcompact cleared %d tool results, saved ~%d tokens", len(clear_set), tokens_saved)

    return messages, tokens_saved
