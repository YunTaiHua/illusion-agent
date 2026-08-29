"""Tests for the OpenAI-compatible API client."""

from __future__ import annotations

import json

from illusion.api.openai_client import (
    _convert_messages_to_openai,
    _convert_tools_to_openai,
    _extract_extra_content,
    _model_consumes_thought_signature,
    _StreamingThoughtTagProcessor,
)
from illusion.engine.messages import (
    ConversationMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class TestConvertToolsToOpenai:
    """Test Anthropic → OpenAI tool schema conversion."""

    def test_basic_tool(self):
        anthropic_tools = [
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                    },
                    "required": ["path"],
                },
            }
        ]
        result = _convert_tools_to_openai(anthropic_tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "read_file"
        assert result[0]["function"]["description"] == "Read a file"
        assert result[0]["function"]["parameters"]["properties"]["path"]["type"] == "string"

    def test_empty_tools(self):
        assert _convert_tools_to_openai([]) == []

    def test_multiple_tools(self):
        tools = [
            {"name": "tool_a", "description": "A", "input_schema": {}},
            {"name": "tool_b", "description": "B", "input_schema": {}},
        ]
        result = _convert_tools_to_openai(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "tool_a"
        assert result[1]["function"]["name"] == "tool_b"


class TestConvertMessagesToOpenai:
    """Test Anthropic → OpenAI message format conversion."""

    def test_system_prompt(self):
        messages: list[ConversationMessage] = []
        result = _convert_messages_to_openai(messages, "You are helpful.")
        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_no_system_prompt(self):
        messages = [ConversationMessage.from_user_text("hi")]
        result = _convert_messages_to_openai(messages, None)
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hi"

    def test_user_text_message(self):
        messages = [ConversationMessage.from_user_text("hello")]
        result = _convert_messages_to_openai(messages, None)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "hello"}

    def test_assistant_text_message(self):
        msg = ConversationMessage(
            role="assistant", content=[TextBlock(text="I'll help you.")]
        )
        result = _convert_messages_to_openai([msg], None)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "I'll help you."
        assert "tool_calls" not in result[0]

    def test_assistant_with_tool_calls(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="Let me read that file."),
                ToolUseBlock(id="call_1", name="read_file", input={"path": "/tmp/x"}),
            ],
        )
        result = _convert_messages_to_openai([msg], None, has_tools=True)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Let me read that file."
        assert len(result[0]["tool_calls"]) == 1
        tc = result[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "read_file"
        assert json.loads(tc["function"]["arguments"]) == {"path": "/tmp/x"}
        # 带 tools 时思维模型的工具轮保持空 reasoning_content（Kimi 类要求）
        assert result[0]["reasoning_content"] == ""

    def test_assistant_with_thinking_and_tool_calls(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(thinking="先确认路径"),
                TextBlock(text="Let me read that file."),
                ToolUseBlock(id="call_1", name="read_file", input={"path": "/tmp/x"}),
            ],
        )
        result = _convert_messages_to_openai([msg], None)
        assert result[0]["role"] == "assistant"
        assert result[0]["reasoning_content"] == "先确认路径"
        assert len(result[0]["tool_calls"]) == 1

    def test_assistant_with_inline_think_tags(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text="<think>先确认路径</think>Answer"),
                ToolUseBlock(id="call_1", name="read_file", input={"path": "/tmp/x"}),
            ],
        )
        result = _convert_messages_to_openai([msg], None)
        assert result[0]["content"] == "Answer"
        assert result[0]["reasoning_content"] == "先确认路径"

    def test_tool_result_messages(self):
        # User message containing tool results
        msg = ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="call_1", content="file contents here", is_error=False
                ),
            ],
        )
        result = _convert_messages_to_openai([msg], None)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"
        assert result[0]["content"] == "file contents here"

    def test_full_conversation_round_trip(self):
        """Test a complete user → assistant(tool_call) → user(tool_result) → assistant flow."""
        messages = [
            ConversationMessage.from_user_text("Read /tmp/test.txt"),
            ConversationMessage(
                role="assistant",
                content=[
                    TextBlock(text="I'll read that."),
                    ToolUseBlock(
                        id="call_abc", name="read_file", input={"path": "/tmp/test.txt"}
                    ),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="call_abc", content="hello world", is_error=False
                    )
                ],
            ),
            ConversationMessage(
                role="assistant",
                content=[TextBlock(text="The file contains: hello world")],
            ),
        ]
        result = _convert_messages_to_openai(messages, "Be helpful")
        assert result[0] == {"role": "system", "content": "Be helpful"}
        assert result[1] == {"role": "user", "content": "Read /tmp/test.txt"}
        assert result[2]["role"] == "assistant"
        assert len(result[2]["tool_calls"]) == 1
        assert result[3]["role"] == "tool"
        assert result[3]["tool_call_id"] == "call_abc"
        assert result[4]["role"] == "assistant"
        assert result[4]["content"] == "The file contains: hello world"

    def test_multiple_tool_results(self):
        msg = ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="c1", content="result1", is_error=False),
                ToolResultBlock(tool_use_id="c2", content="result2", is_error=True),
            ],
        )
        result = _convert_messages_to_openai([msg], None)
        assert len(result) == 2
        assert result[0]["tool_call_id"] == "c1"
        assert result[1]["tool_call_id"] == "c2"


class TestOrphanedToolUseSynthesis:
    """Test synthesis of missing tool_result for orphaned tool_use blocks.

    DeepSeek 等 strict OpenAI 兼容 provider 要求每个 tool_use 在紧接的下一条
    消息中有对应的 tool_result。会话中断、中途切换模型、会话恢复等场景
    可能导致 tool_result 缺失，_convert_messages_to_openai 应自动补齐合成
    错误结果，避免 API 400 错误。
    """

    def test_trailing_orphaned_tool_use(self):
        """assistant 末尾的 tool_use 无后续 tool_result → 末尾补齐合成结果。"""
        messages = [
            ConversationMessage.from_user_text("read /tmp/x"),
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="call_orphan", name="read_file", input={"path": "/tmp/x"}),
                ],
            ),
        ]
        result = _convert_messages_to_openai(messages, None)
        # user → assistant(tool_calls) → tool(synthesized)
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert len(result[1]["tool_calls"]) == 1
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "call_orphan"
        assert result[2]["content"] == "Tool execution interrupted"

    def test_orphaned_tool_use_followed_by_user_text(self):
        """tool_use 后用户直接输入文本（中断后继续）→ 先补齐 tool_result 再附用户文本。"""
        messages = [
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="call_1", name="read_file", input={"path": "/a"}),
                ],
            ),
            ConversationMessage.from_user_text("never mind, do something else"),
        ]
        result = _convert_messages_to_openai(messages, None)
        # assistant(tool_calls) → tool(synthesized) → user(text)
        assert result[0]["role"] == "assistant"
        assert len(result[0]["tool_calls"]) == 1
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "call_1"
        assert result[1]["content"] == "Tool execution interrupted"
        assert result[2]["role"] == "user"
        assert result[2]["content"] == "never mind, do something else"

    def test_partial_tool_results(self):
        """多个 tool_use 但只有部分 tool_result → 为缺失的补齐合成结果。"""
        messages = [
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="call_a", name="read_file", input={"path": "/a"}),
                    ToolUseBlock(id="call_b", name="read_file", input={"path": "/b"}),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="call_a", content="content of /a"),
                    # call_b 的 result 缺失（工具被中断）
                ],
            ),
        ]
        result = _convert_messages_to_openai(messages, None)
        # assistant(tool_calls x2) → tool(synthesized for call_b) → tool(call_a result)
        assert result[0]["role"] == "assistant"
        assert len(result[0]["tool_calls"]) == 2
        # call_b 的合成结果应出现在 call_a 的真实结果之前
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "call_b"
        assert result[1]["content"] == "Tool execution interrupted"
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "call_a"
        assert result[2]["content"] == "content of /a"

    def test_consecutive_assistant_with_tool_calls(self):
        """连续两条 assistant 都含 tool_use 且都无 tool_result → 各自补齐。"""
        messages = [
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="c1", name="t1", input={})],
            ),
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="c2", name="t2", input={})],
            ),
        ]
        result = _convert_messages_to_openai(messages, None)
        # assistant(c1) → tool(synth c1) → assistant(c2) → tool(synth c2)
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "c1"
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "tool"
        assert result[3]["tool_call_id"] == "c2"

    def test_normal_flow_unaffected(self):
        """完整的 user→assistant(tool)→user(tool_result)→assistant 流程不受影响。"""
        messages = [
            ConversationMessage.from_user_text("read /tmp/test.txt"),
            ConversationMessage(
                role="assistant",
                content=[
                    TextBlock(text="I'll read that."),
                    ToolUseBlock(id="call_ok", name="read_file", input={"path": "/tmp/test.txt"}),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="call_ok", content="hello world"),
                ],
            ),
            ConversationMessage(
                role="assistant",
                content=[TextBlock(text="The file contains: hello world")],
            ),
        ]
        result = _convert_messages_to_openai(messages, "Be helpful")
        assert result[0] == {"role": "system", "content": "Be helpful"}
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert len(result[2]["tool_calls"]) == 1
        assert result[3]["role"] == "tool"
        assert result[3]["tool_call_id"] == "call_ok"
        assert result[3]["content"] == "hello world"
        assert result[4]["role"] == "assistant"
        assert result[4]["content"] == "The file contains: hello world"
        # 不应有任何合成结果
        synth = [m for m in result if m.get("content") == "Tool execution interrupted"]
        assert synth == []

    def test_assistant_without_tool_calls_unaffected(self):
        """assistant 不含 tool_use 时不应产生任何合成 tool 消息。"""
        messages = [
            ConversationMessage(
                role="assistant",
                content=[TextBlock(text="Hello!")],
            ),
            ConversationMessage.from_user_text("Hi there"),
        ]
        result = _convert_messages_to_openai(messages, None)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Hello!"
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Hi there"

    def test_empty_message_list(self):
        """空消息列表不应产生任何合成消息。"""
        result = _convert_messages_to_openai([], None)
        assert result == []


class TestModelConsumesThoughtSignature:
    """Test the Gemini thought_signature model gating predicate."""

    def test_gemini_models_return_true(self):
        for model in ("gemini-3-pro", "gemini-3.5-flash", "gemini-flash-latest", "google/gemini-3-pro"):
            assert _model_consumes_thought_signature(model), model

    def test_gemma_models_return_true(self):
        # Gemma is served through the same Gemini API and shares the thought_signature contract
        assert _model_consumes_thought_signature("gemma-4-31b-it")

    def test_non_gemini_models_return_false(self):
        for model in ("deepseek-chat", "claude-sonnet-4", "glm-5.2", "qwen-plus", "", "llama-v3"):
            assert not _model_consumes_thought_signature(model), model


class TestExtractExtraContent:
    """Test extraction of extra_content (thought_signature carrier) from SDK objects."""

    def test_attribute_access(self):
        class FakeDelta:
            def __init__(self) -> None:
                self.extra_content = {"google": {"thought_signature": "sig_123"}}

        assert _extract_extra_content(FakeDelta()) == {"google": {"thought_signature": "sig_123"}}

    def test_model_extra_fallback(self):
        class FakeDelta:
            def __init__(self) -> None:
                self.model_extra = {"extra_content": {"google": {"thought_signature": "sig_abc"}}}

        assert _extract_extra_content(FakeDelta()) == {"google": {"thought_signature": "sig_abc"}}

    def test_returns_none_when_absent(self):
        class FakeDelta:
            pass

        assert _extract_extra_content(FakeDelta()) is None

    def test_pydantic_model_dump(self):
        from pydantic import BaseModel

        class ExtraModel(BaseModel):
            thought_signature: str = "sig_xyz"

        class FakeDelta:
            extra_content = ExtraModel()

        result = _extract_extra_content(FakeDelta())
        assert result == {"thought_signature": "sig_xyz"}


class TestThoughtSignatureReplay:
    """Test that Gemini thought_signature (extra_content) round-trips correctly.

    Gemini 3 thinking models attach a thought_signature to every functionCall.
    This signature MUST be replayed on subsequent requests or the API returns
    HTTP 400 "missing thought_signature". Strict providers (Fireworks, Mistral)
    reject the extra_content field, so it must be model-gated.
    """

    def _msg_with_signature(self) -> ConversationMessage:
        return ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    id="call_1",
                    name="write_file",
                    input={"file_path": "/tmp/x"},
                    provider_data={
                        "extra_content": {"google": {"thought_signature": "SIG_GEMINI_123"}}
                    },
                ),
            ],
        )

    def test_preserves_extra_content_for_gemini(self):
        """Gemini targets keep extra_content so the signature round-trips."""
        msg = self._msg_with_signature()
        result = _convert_messages_to_openai([msg], None, model="gemini-3.5-flash")
        tc = result[0]["tool_calls"][0]
        assert tc["extra_content"] == {"google": {"thought_signature": "SIG_GEMINI_123"}}

    def test_preserves_extra_content_for_gemma(self):
        msg = self._msg_with_signature()
        result = _convert_messages_to_openai([msg], None, model="gemma-4-31b-it")
        assert result[0]["tool_calls"][0]["extra_content"] == {
            "google": {"thought_signature": "SIG_GEMINI_123"}
        }

    def test_strips_extra_content_for_strict_provider(self):
        """Non-Gemini providers reject extra_content with 400 — must be stripped."""
        msg = self._msg_with_signature()
        result = _convert_messages_to_openai([msg], None, model="deepseek-chat")
        assert "extra_content" not in result[0]["tool_calls"][0]

    def test_strips_extra_content_when_no_model(self):
        """Default (no model) is to strip — safe for strict providers."""
        msg = self._msg_with_signature()
        result = _convert_messages_to_openai([msg], None)
        assert "extra_content" not in result[0]["tool_calls"][0]

    def test_no_extra_content_key_when_provider_data_empty(self):
        """When provider_data has no extra_content, the tool call must not carry the key."""
        msg = ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="call_1", name="read_file", input={"path": "/tmp/x"})],
        )
        result = _convert_messages_to_openai([msg], None, model="gemini-3-pro")
        assert "extra_content" not in result[0]["tool_calls"][0]

    def test_multiple_tool_calls_each_keep_their_signature(self):
        """Each tool call carries its own thought_signature — must be preserved independently."""
        msg = ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    id="call_1", name="read_file", input={"path": "/a"},
                    provider_data={"extra_content": {"google": {"thought_signature": "SIG_A"}}},
                ),
                ToolUseBlock(
                    id="call_2", name="write_file", input={"path": "/b"},
                    provider_data={"extra_content": {"google": {"thought_signature": "SIG_B"}}},
                ),
            ],
        )
        result = _convert_messages_to_openai([msg], None, model="gemini-3-pro")
        tcs = result[0]["tool_calls"]
        assert tcs[0]["extra_content"] == {"google": {"thought_signature": "SIG_A"}}
        assert tcs[1]["extra_content"] == {"google": {"thought_signature": "SIG_B"}}

    def test_backward_compat_tooluseblock_without_provider_data(self):
        """Existing ToolUseBlock construction (no provider_data arg) must still work."""
        tu = ToolUseBlock(id="call_1", name="read_file", input={"path": "/tmp/x"})
        assert tu.provider_data == {}
        msg = ConversationMessage(role="assistant", content=[tu])
        # Gemini target, empty provider_data → no extra_content key
        result = _convert_messages_to_openai([msg], None, model="gemini-3-pro")
        assert "extra_content" not in result[0]["tool_calls"][0]


class TestStreamingThoughtTagProcessor:
    """Test real-time separation of <thought> tags from streaming text deltas.

    Gemini sends thinking content inside <thought> tags via delta.content
    instead of the structured reasoning_content field.  The processor must
    split these in real-time so the frontend receives reasoning and text
    as separate events — otherwise thinking leaks into the assistant reply.
    """

    def _collect(self, proc: _StreamingThoughtTagProcessor, chunk: str) -> list[tuple[str, str]]:
        return proc.feed(chunk)

    def _flush(self, proc: _StreamingThoughtTagProcessor) -> list[tuple[str, str]]:
        return proc.flush()

    # ── Basic behaviour ───────────────────────────────────────────

    def test_no_tags_passthrough(self):
        """Plain text with no thought tags passes through as text."""
        proc = _StreamingThoughtTagProcessor()
        assert self._collect(proc, "Hello world") == [("Hello world", "")]
        assert self._flush(proc) == []

    def test_complete_thought_block(self):
        """A single complete <thought>…</thought> block yields reasoning only."""
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, "<thought>thinking deeply</thought>Answer here")
        assert ("", "thinking deeply") in result
        assert ("Answer here", "") in result

    def test_text_before_and_after(self):
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, "Before<thought>inner</thought>After")
        texts = [(t, r) for t, r in result if t]
        reasoning = [(t, r) for t, r in result if r]
        assert texts == [("Before", ""), ("After", "")]
        assert reasoning == [("", "inner")]

    def test_empty_thought_block(self):
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, "<thought></thought>Done")
        # Empty thinking → no reasoning output; "Done" emitted as text
        reasoning = [r for _, r in result if r]
        assert reasoning == []
        texts = [t for t, _ in result if t]
        assert "Done" in texts

    # ── Split across chunks ──────────────────────────────────────

    def test_tag_split_across_chunks(self):
        """The <thought> opening tag split across two chunks."""
        proc = _StreamingThoughtTagProcessor()
        r1 = self._collect(proc, "Hello <thou")
        r2 = self._collect(proc, "ght>deep thinking</thought>Reply")
        all_reasoning = [r for _, r in r1 + r2 if r]
        all_text = [t for t, _ in r1 + r2 if t]
        assert any("deep thinking" in r for r in all_reasoning)
        assert any("Reply" in t for t in all_text)

    def test_close_tag_split(self):
        """The </thought> closing tag split across two chunks."""
        proc = _StreamingThoughtTagProcessor()
        r1 = self._collect(proc, "<thought>some reasoning</tho")
        r2 = self._collect(proc, "ught>Final answer")
        all_reasoning = [r for _, r in r1 + r2 if r]
        all_text = [t for t, _ in r1 + r2 if t]
        assert any("some reasoning" in r for r in all_reasoning)
        assert any("Final answer" in t for t in all_text)

    def test_content_split_inside_thought(self):
        """Content inside <thought> arrives in many small chunks."""
        proc = _StreamingThoughtTagProcessor()
        results: list[tuple[str, str]] = []
        for ch in ["<thought>", "line1", " line2", " line3", "</thought>", "Done"]:
            results.extend(self._collect(proc, ch))
        results.extend(self._flush(proc))
        reasoning = [r for _, r in results if r]
        assert any("line1" in r for r in reasoning)
        assert any("line3" in r for r in reasoning)
        texts = [t for t, _ in results if t]
        assert any("Done" in t for t in texts)

    # ── Multiple blocks ──────────────────────────────────────────

    def test_two_sequential_blocks(self):
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, "<thought>first</thought>A<thought>second</thought>B")
        reasoning = [r for _, r in result if r]
        assert any("first" in r for r in reasoning)
        assert any("second" in r for r in reasoning)
        texts = [t for t, _ in result if t]
        assert any("A" in t for t in texts)
        assert any("B" in t for t in texts)

    # ── Case insensitivity ───────────────────────────────────────

    def test_uppercase_tags(self):
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, "<THOUGHT>deep</THOUGHT>text")
        assert ("", "deep") in result
        assert ("text", "") in result

    def test_mixed_case_tags(self):
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, "<Thought>content</Thought>reply")
        reasoning = [r for _, r in result if r]
        assert any("content" in r for r in reasoning)

    # ── Tag with attributes ──────────────────────────────────────

    def test_tag_with_attributes(self):
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, '<thought model="gemini">inner</thought>out')
        assert ("", "inner") in result
        assert ("out", "") in result

    # ── Flush behaviour ──────────────────────────────────────────

    def test_flush_residues_open_thought(self):
        """Unclosed <thought> with trailing '<' — the '<' is held back during feed()
        (could be start of </thought) and flushed as reasoning on stream end."""
        proc = _StreamingThoughtTagProcessor()
        feed_result = self._collect(proc, "<thought>some reasoning<")
        # "some reasoning" was output during feed (no tag prefix in it)
        assert ("", "some reasoning") in feed_result
        # trailing "<" was buffered; flushed at stream end
        flush_result = self._flush(proc)
        assert ("", "<") in flush_result

    def test_flush_residues_trailing_text(self):
        """Trailing text with no tag prefix → already emitted during feed, flush is empty."""
        proc = _StreamingThoughtTagProcessor()
        feed_result = self._collect(proc, "answer text")
        assert ("answer text", "") in feed_result
        assert self._flush(proc) == []

    def test_flush_empty_buffer(self):
        proc = _StreamingThoughtTagProcessor()
        assert self._flush(proc) == []

    # ── No cross-contamination ───────────────────────────────────

    def test_reasoning_never_in_text(self):
        """Content inside <thought> must never appear in the text output."""
        proc = _StreamingThoughtTagProcessor()
        chunks = ["<thought>", "secret ", "reasoning", "</thought>", "public answer"]
        results: list[tuple[str, str]] = []
        for ch in chunks:
            results.extend(self._collect(proc, ch))
        results.extend(self._flush(proc))
        for text, reasoning in results:
            if text:
                assert "secret" not in text
                assert "reasoning" not in text
            if reasoning:
                assert "public answer" not in reasoning

    def test_text_never_in_reasoning(self):
        """Content outside <thought> must never appear in the reasoning output."""
        proc = _StreamingThoughtTagProcessor()
        result = self._collect(proc, "visible<thought>inner</thought>visible2")
        for text, reasoning in result:
            if reasoning:
                assert "visible" not in reasoning
            if text:
                assert "inner" not in text
