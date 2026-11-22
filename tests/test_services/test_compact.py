"""Tests for compaction and token estimation helpers."""

from __future__ import annotations

from illusion.engine.messages import (
    ConversationMessage,
    MediaBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from illusion.services import (
    compact_messages,
    create_compact_boundary_marker,
    estimate_conversation_tokens,
    estimate_message_tokens,
    estimate_tokens,
    get_messages_after_compact_boundary,
    is_compact_boundary_marker,
    strip_images_from_messages,
    summarize_messages,
)
from illusion.services.compact import (
    COMPACT_BOUNDARY_PREFIX,
    _ensure_message_alternation,
)


def test_token_estimation_helpers():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_message_tokens(["abcd", "abcdefgh"]) == 3


def test_token_estimation_cjk():
    """CJK 文字应该使用更密集的估算。"""
    cjk_tokens = estimate_tokens("你好世界")
    assert cjk_tokens >= 2  # 4 个 CJK 字符，/2 = 2
    eng_tokens = estimate_tokens("abcd")
    assert eng_tokens == 1  # 4 个英文字符，/4 = 1


def test_compact_and_summarize_messages():
    messages = [
        ConversationMessage(role="user", content=[TextBlock(text="first question")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="first answer")]),
        ConversationMessage(role="user", content=[TextBlock(text="second question")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="second answer")]),
    ]

    summary = summarize_messages(messages, max_messages=2)
    assert "user: second question" in summary
    assert "assistant: second answer" in summary

    compacted = compact_messages(messages, preserve_recent=2)
    # compacted 应包含：summary(user) + boundary(assistant) + 2 preserved
    assert len(compacted) >= 3
    assert "[conversation summary]" in compacted[0].text
    assert estimate_conversation_tokens(compacted) >= 1


def test_compact_boundary_marker():
    """测试压缩边界标记的创建和识别。"""
    marker = create_compact_boundary_marker()
    assert marker.role == "assistant"
    assert is_compact_boundary_marker(marker)
    assert marker.text.strip() == COMPACT_BOUNDARY_PREFIX

    # 普通消息不应被识别为边界标记
    normal = ConversationMessage(role="assistant", content=[TextBlock(text="hello")])
    assert not is_compact_boundary_marker(normal)


def test_get_messages_after_compact_boundary():
    """测试获取边界标记后的消息。"""
    messages = [
        ConversationMessage.from_user_text("before1"),
        create_compact_boundary_marker(),
        ConversationMessage.from_user_text("after1"),
        ConversationMessage(role="assistant", content=[TextBlock(text="after2")]),
    ]

    after = get_messages_after_compact_boundary(messages)
    assert len(after) == 2
    assert after[0].text == "after1"

    # 没有边界标记时返回所有消息
    no_boundary = [
        ConversationMessage.from_user_text("msg1"),
        ConversationMessage(role="assistant", content=[TextBlock(text="msg2")]),
    ]
    assert get_messages_after_compact_boundary(no_boundary) == no_boundary


def test_ensure_message_alternation():
    """测试消息角色交替修复。"""
    # 连续两条 user 消息
    messages = [
        ConversationMessage.from_user_text("msg1"),
        ConversationMessage.from_user_text("msg2"),
    ]
    fixed = _ensure_message_alternation(messages)
    roles = [m.role for m in fixed]
    # 应该在两条 user 之间插入 assistant
    assert roles == ["user", "assistant", "user"]

    # 连续两条 assistant 消息
    messages2 = [
        ConversationMessage(role="assistant", content=[TextBlock(text="a1")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="a2")]),
    ]
    fixed2 = _ensure_message_alternation(messages2)
    roles2 = [m.role for m in fixed2]
    # 第一条 assistant 前面应插入 user，两条 assistant 之间也应插入 user
    assert roles2[0] == "user"  # 插入的前置 user
    assert "assistant" in roles2
    assert "user" in roles2

    # 正常交替不需要修复
    messages3 = [
        ConversationMessage.from_user_text("q"),
        ConversationMessage(role="assistant", content=[TextBlock(text="a")]),
    ]
    fixed3 = _ensure_message_alternation(messages3)
    assert len(fixed3) == 2
    assert fixed3[0].role == "user"
    assert fixed3[1].role == "assistant"


def test_strip_images_from_messages():
    """测试图片剥离功能。"""
    messages = [
        ConversationMessage(
            role="user",
            content=[
                TextBlock(text="Here is an image:"),
                MediaBlock(
                    file_path="/test/image.png",
                    media_type="image/png",
                    data="base64data",
                ),
            ],
        ),
    ]

    stripped = strip_images_from_messages(messages)
    assert len(stripped) == 1
    # MediaBlock 应被替换为 TextBlock
    assert len(stripped[0].content) == 2
    assert isinstance(stripped[0].content[0], TextBlock)
    assert isinstance(stripped[0].content[1], TextBlock)
    assert "image" in stripped[0].content[1].text.lower()
    # 原始消息不应被修改
    assert isinstance(messages[0].content[1], MediaBlock)


def test_estimate_message_tokens_with_tool_results():
    """测试包含工具结果的消息 Token 估算。"""
    from illusion.services.compact import estimate_message_tokens as estimate_msg_tokens

    messages = [
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="tool_1",
                    content="This is a long tool result content that should be counted",
                    is_error=False,
                ),
            ],
        ),
    ]
    tokens = estimate_msg_tokens(messages)
    assert tokens > 0


def test_compact_messages_preserves_alternation():
    """测试 compact_messages 输出的消息角色交替正确。"""
    messages = [
        ConversationMessage.from_user_text("q1"),
        ConversationMessage(role="assistant", content=[TextBlock(text="a1")]),
        ConversationMessage.from_user_text("q2"),
        ConversationMessage(role="assistant", content=[TextBlock(text="a2")]),
        ConversationMessage.from_user_text("q3"),
        ConversationMessage(role="assistant", content=[TextBlock(text="a3")]),
        ConversationMessage.from_user_text("q4"),
        ConversationMessage(role="assistant", content=[TextBlock(text="a4")]),
    ]

    compacted = compact_messages(messages, preserve_recent=2)
    # 验证没有连续相同角色
    for i in range(1, len(compacted)):
        if compacted[i - 1].role == compacted[i].role:
            # 边界标记可以是空的 assistant，这是允许的
            assert is_compact_boundary_marker(compacted[i - 1]) or is_compact_boundary_marker(compacted[i])


def test_safe_split_preserves_tool_pairs():
    """测试安全分割不会切断 tool_use/tool_result 对。"""
    from illusion.services.compact import _find_safe_split_index

    # 构造消息：assistant 含 tool_use → user 含 tool_result
    messages = [
        ConversationMessage.from_user_text("q1"),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="let me read that"),
                ToolUseBlock(id="tool_1", name="read_file", input={"path": "/a.py"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tool_1", content="file content of a.py", is_error=False),
            ],
        ),
        ConversationMessage.from_user_text("q2"),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="now I'll edit"),
                ToolUseBlock(id="tool_2", name="edit_file", input={"path": "/b.py"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tool_2", content="edit applied", is_error=False),
            ],
        ),
        ConversationMessage.from_user_text("q3"),
        ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
    ]

    # preserve_recent=4 会从后往前数 4 条消息（tool_2 result, q3, done）
    # 但 tool_2 result 需要对应的 tool_use，所以 split 应该前移
    split = _find_safe_split_index(messages, preserve_recent=4)
    # split 应该包含 tool_2 的 assistant 消息
    newer = messages[split:]
    # 收集 newer 中所有 tool_result 的 id
    newer_result_ids = set()
    for msg in newer:
        if msg.role == "user":
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    newer_result_ids.add(block.tool_use_id)
    # 收集 newer 中所有 tool_use 的 id
    newer_use_ids = set()
    for msg in newer:
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    newer_use_ids.add(block.id)
    # 每个 tool_result 都应该有对应的 tool_use
    assert newer_result_ids.issubset(newer_use_ids), (
        f"Orphaned tool_results: {newer_result_ids - newer_use_ids}"
    )


def test_remove_orphaned_tool_results():
    """测试孤立 tool_result 的清理。"""
    from illusion.services.compact import _remove_orphaned_tool_results

    # 构造消息：tool_result 没有对应的 tool_use
    messages = [
        ConversationMessage.from_user_text("q1"),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="orphan_id", content="orphan result", is_error=False),
                TextBlock(text="some text"),
            ],
        ),
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(id="valid_id", name="read_file", input={"path": "/x.py"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="valid_id", content="valid result", is_error=False),
            ],
        ),
    ]

    cleaned = _remove_orphaned_tool_results(messages)
    # orphan_id 的 tool_result 应该被移除
    all_result_ids = set()
    for msg in cleaned:
        if msg.role == "user":
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    all_result_ids.add(block.tool_use_id)
    assert "orphan_id" not in all_result_ids
    assert "valid_id" in all_result_ids


def test_compact_with_tool_calls_no_orphans():
    """测试包含工具调用的消息压缩后不会产生孤立的 tool_result。"""
    messages = [
        ConversationMessage.from_user_text("read the file"),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="reading file"),
                ToolUseBlock(id="tool_1", name="read_file", input={"path": "/test.py"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tool_1", content="file content here", is_error=False),
            ],
        ),
        ConversationMessage.from_user_text("now edit it"),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="editing"),
                ToolUseBlock(id="tool_2", name="edit_file", input={"path": "/test.py"}),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tool_2", content="edit applied", is_error=False),
            ],
        ),
        ConversationMessage.from_user_text("thanks"),
        ConversationMessage(role="assistant", content=[TextBlock(text="you're welcome")]),
    ]

    compacted = compact_messages(messages, preserve_recent=2)

    # 验证没有孤立的 tool_result
    tool_use_ids = set()
    for msg in compacted:
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    tool_use_ids.add(block.id)

    for msg in compacted:
        if msg.role == "user":
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    assert block.tool_use_id in tool_use_ids, (
                        f"Orphaned tool_result: {block.tool_use_id} not found in tool_use_ids {tool_use_ids}"
                    )


def test_reactive_compact_strip_media_for_payload_too_large():
    """413 路径：strip_media=True 先剥离图片字节，再走微压缩重试。

    413 是网关对请求体字节大小的硬限制，仅按 token 压缩可能不减小
    真实字节；剥离图片（base64 大头）是必要前置步骤。
    """
    import asyncio

    from illusion.services.compact import reactive_compact

    def _build():
        messages: list[ConversationMessage] = [
            ConversationMessage.from_user_text("please inspect the page"),
        ]
        # 6 个可微压缩的旧工具结果（> DEFAULT_KEEP_RECENT=5 触发释放）
        for i in range(6):
            messages.append(ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=f"tu-{i}", name="read_file", input={})],
            ))
            messages.append(ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=f"tu-{i}",
                        content=[
                            MediaBlock(
                                file_path=f"/tmp/shot-{i}.png",
                                media_type="image/png",
                                data="ZmFrZWJhc2U2NA==",
                            ),
                            TextBlock(text="file body " + "x" * 500),
                        ],
                        is_error=False,
                    ),
                ],
            ))
        return messages

    messages = _build()
    compacted, was_compacted = asyncio.run(reactive_compact(
        messages,
        api_client=None,
        model="demo-model",
        system_prompt="",
        strip_media=True,
    ))
    assert was_compacted is True
    # 全部消息中不再有 MediaBlock（base64 字节已被占位文本替换）
    for msg in compacted:
        for block in msg.content:
            inner = block.content if isinstance(block, ToolResultBlock) else [block]
            if not isinstance(inner, list):
                inner = [inner]
            for piece in inner:
                assert not isinstance(piece, MediaBlock)
