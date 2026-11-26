"""
消息操作 — 分组、分割、孤立清理、交替修复、边界标记。
"""

from __future__ import annotations

import logging

from illusion.engine.messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from illusion.services.compact.constants import COMPACT_BOUNDARY_PREFIX

log = logging.getLogger(__name__)


def _group_messages_by_turn(
    messages: list[ConversationMessage],
) -> list[list[ConversationMessage]]:
    """将消息按 API 轮次分组。"""
    groups: list[list[ConversationMessage]] = []
    current_group: list[ConversationMessage] = []

    for msg in messages:
        if msg.role == "assistant" and current_group:
            groups.append(current_group)
            current_group = [msg]
        else:
            current_group.append(msg)

    if current_group:
        groups.append(current_group)

    return groups


def _find_safe_split_index(
    messages: list[ConversationMessage],
    preserve_recent: int,
) -> int:
    """找到安全的分割索引，确保 tool_use/tool_result 对不被切断。"""
    n = len(messages)
    if n <= preserve_recent:
        return 0

    split = n - preserve_recent

    newer_tool_result_ids: set[str] = set()
    for msg in messages[split:]:
        if msg.role == "user":
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    newer_tool_result_ids.add(block.tool_use_id)

    if not newer_tool_result_ids:
        return split

    for i in range(split - 1, -1, -1):
        msg = messages[i]
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.id in newer_tool_result_ids:
                    newer_tool_result_ids.discard(block.id)
                    if not newer_tool_result_ids:
                        return i

    return 0


def _remove_orphaned_tool_results(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """移除没有对应 tool_use 的孤立 tool_result 块。"""
    tool_use_ids: set[str] = set()
    for msg in messages:
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    tool_use_ids.add(block.id)

    result: list[ConversationMessage] = []
    for msg in messages:
        if msg.role != "user":
            result.append(msg)
            continue

        has_orphan = False
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id not in tool_use_ids:
                has_orphan = True
                break

        if not has_orphan:
            result.append(msg)
            continue

        new_blocks: list[ContentBlock] = []
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id not in tool_use_ids:
                log.warning(
                    "Removing orphaned tool_result (tool_use_id=%s) — "
                    "corresponding tool_use was compacted away",
                    block.tool_use_id,
                )
                continue
            new_blocks.append(block)

        if new_blocks:
            result.append(msg.model_copy(update={"content": new_blocks}))
        else:
            log.warning("Dropping user message that contained only orphaned tool_results")

    return result


def _ensure_message_alternation(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """确保消息列表中 user/assistant 角色正确交替。"""
    if not messages:
        return messages

    result: list[ConversationMessage] = []

    if messages[0].role != "user":
        from illusion.config.i18n import t
        result.append(ConversationMessage.from_user_text(t("compact_conversation_start")))

    for i, msg in enumerate(messages):
        if not result:
            result.append(msg)
            continue

        last_role = result[-1].role
        current_role = msg.role

        if last_role == current_role:
            if current_role == "user":
                result.append(ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="")],
                ))
            else:
                result.append(ConversationMessage.from_user_text(""))
        elif last_role == "assistant" and current_role == "user":
            pass

        result.append(msg)

    return result


def create_compact_boundary_marker() -> ConversationMessage:
    """创建压缩边界标记消息。"""
    return ConversationMessage(
        role="assistant",
        content=[TextBlock(text=COMPACT_BOUNDARY_PREFIX)],
    )


def is_compact_boundary_marker(msg: ConversationMessage) -> bool:
    """检查消息是否为压缩边界标记。"""
    return (
        msg.role == "assistant"
        and len(msg.content) == 1
        and isinstance(msg.content[0], TextBlock)
        and msg.content[0].text.strip() == COMPACT_BOUNDARY_PREFIX
    )


def get_messages_after_compact_boundary(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """获取最后一个压缩边界标记之后的消息。"""
    last_boundary = -1
    for i, msg in enumerate(messages):
        if is_compact_boundary_marker(msg):
            last_boundary = i
    if last_boundary >= 0:
        return messages[last_boundary + 1:]
    return messages
