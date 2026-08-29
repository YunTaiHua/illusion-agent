"""Responses API 客户端测试模块

本模块提供通用 Responses API 客户端的单元测试，包括：
- URL 解析与工具转换
- 消息转换（reasoning item 回传、孤儿 tool_use 补齐、媒体）
- reasoning item 错误识别（自愈重试触发条件）
- 流式事件解析（reasoning item 捕获、function_call provider_data）
- 自愈降级（store: true 记忆、降级仍失败的可行动错误）
"""

from __future__ import annotations

import json

import pytest

import illusion.api.error_log as error_log_module
from illusion.api.client import ApiMessageCompleteEvent, ApiMessageRequest, ApiTextDeltaEvent
from illusion.api.compat import is_reasoning_item_passback_error
from illusion.api.effort import EffortLevel
from illusion.api.responses_client import (
    ResponsesApiClient,
    _convert_messages_to_responses_input,
    _convert_tools_to_responses,
    _resolve_responses_url,
)
from illusion.engine.messages import (
    ConversationMessage,
    MediaBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


@pytest.fixture(autouse=True)
def _isolate_api_error_log(tmp_path, monkeypatch):
    """把 API 错误日志隔离到临时目录。

    降级测试会触发真实的 400 → log_api_error 写入；不隔离的话伪造的
    错误记录会污染开发者的真实 ~/.illusion/logs/api_error.log。
    """
    monkeypatch.setenv("ILLUSION_LOGS_DIR", str(tmp_path))
    error_log_module._logger = None
    yield
    error_log_module._logger = None


class TestResolveResponsesUrl:
    """_resolve_responses_url 测试"""

    def test_default_base_url(self):
        assert _resolve_responses_url(None) == "https://api.openai.com/v1/responses"

    def test_appends_responses_to_v1(self):
        assert _resolve_responses_url("https://api.openai.com/v1") == "https://api.openai.com/v1/responses"

    def test_keeps_existing_responses_suffix(self):
        assert _resolve_responses_url("https://x.example/v1/responses/") == "https://x.example/v1/responses"

    def test_strips_trailing_slash(self):
        assert _resolve_responses_url("https://x.example/v1/") == "https://x.example/v1/responses"


class TestConvertToolsToResponses:
    """_convert_tools_to_responses 测试"""

    def test_flat_function_format(self):
        out = _convert_tools_to_responses([
            {"name": "bash", "description": "run", "input_schema": {"type": "object"}},
        ])
        assert out == [{
            "type": "function",
            "name": "bash",
            "description": "run",
            "parameters": {"type": "object"},
        }]


def _reasoning_item(*, encrypted: str = "enc-123") -> dict:
    """构造捕获形态的 reasoning item。"""
    item = {"type": "reasoning", "id": "rs_1", "summary": []}
    if encrypted:
        item["encrypted_content"] = encrypted
    return item


class TestConvertMessagesToResponsesInput:
    """_convert_messages_to_responses_input 测试"""

    def test_user_text_becomes_user_message(self):
        items = _convert_messages_to_responses_input([ConversationMessage.from_user_text("hi")])
        assert items == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]

    def test_reasoning_item_replayed_before_function_call(self):
        """配对的 reasoning item 在 function_call 之前回传"""
        tool_use = ToolUseBlock(
            id="call_1", name="bash", input={"command": "ls"},
            provider_data={"reasoning_item": _reasoning_item(), "item_id": "fc_1"},
        )
        user_result = ConversationMessage(role="user", content=[
            ToolResultBlock(tool_use_id="call_1", content="out"),
        ])
        items = _convert_messages_to_responses_input([
            ConversationMessage.from_user_text("run"),
            ConversationMessage(role="assistant", content=[tool_use]),
            user_result,
        ])
        assert items[0]["role"] == "user"
        assert items[1] == _reasoning_item()
        assert items[2]["type"] == "function_call"
        assert items[2]["call_id"] == "call_1"
        assert items[2]["id"] == "fc_1"
        assert items[3] == {"type": "function_call_output", "call_id": "call_1", "output": "out"}

    def test_shared_reasoning_item_emitted_once(self):
        """同轮多个 function_call 共享的 reasoning item 只回传一次"""
        item = _reasoning_item()
        blocks = [
            ToolUseBlock(id="call_1", name="a", input={}, provider_data={"reasoning_item": item}),
            ToolUseBlock(id="call_2", name="b", input={}, provider_data={"reasoning_item": item}),
        ]
        items = _convert_messages_to_responses_input([
            ConversationMessage(role="assistant", content=blocks),
        ])
        reasoning_items = [i for i in items if i.get("type") == "reasoning"]
        assert len(reasoning_items) == 1
        assert items[0]["type"] == "reasoning"
        assert items[1]["call_id"] == "call_1"
        assert items[2]["call_id"] == "call_2"

    def test_reasoning_item_without_encrypted_content_not_replayed(self):
        """无 encrypted_content 的 reasoning item 不回传（store:false 下会 400）"""
        tool_use = ToolUseBlock(
            id="call_1", name="bash", input={},
            provider_data={"reasoning_item": _reasoning_item(encrypted="")},
        )
        items = _convert_messages_to_responses_input([
            ConversationMessage(role="assistant", content=[tool_use]),
        ])
        assert not any(i.get("type") == "reasoning" for i in items)
        assert items[0]["type"] == "function_call"

    def test_orphan_tool_use_synthesized(self):
        """孤儿 tool_use（assistant 后无结果）补齐合成输出"""
        tool_use = ToolUseBlock(id="call_9", name="bash", input={})
        items = _convert_messages_to_responses_input([
            ConversationMessage(role="assistant", content=[tool_use]),
            ConversationMessage.from_user_text("继续"),
        ])
        outputs = [i for i in items if i.get("type") == "function_call_output"]
        assert outputs == [{"type": "function_call_output", "call_id": "call_9", "output": "Tool execution interrupted"}]

    def test_tool_result_media_follows_as_user_image_message(self):
        """工具结果中的媒体通过独立 user 消息（input_image）传递"""
        media = MediaBlock(file_path="a.png", media_type="image/png", data="QUJD")
        result = ConversationMessage(role="user", content=[
            ToolResultBlock(tool_use_id="call_1", content=[media, TextBlock(text="图")]),
        ])
        items = _convert_messages_to_responses_input([result])
        assert items[0]["type"] == "function_call_output"
        assert items[1] == {
            "role": "user",
            "content": [{"type": "input_image", "image_url": "data:image/png;base64,QUJD"}],
        }

    def test_assistant_text_replayed_as_message(self):
        """assistant 文本回放为 message item"""
        items = _convert_messages_to_responses_input([
            ConversationMessage(role="assistant", content=[TextBlock(text="答案")]),
        ])
        assert items == [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "答案"}],
        }]

    def test_mixed_turn_text_before_tool_keeps_order(self):
        """文本+工具混合轮：文本在前时回放顺序 [message, reasoning, function_call]"""
        tool_use = ToolUseBlock(
            id="call_1", name="bash", input={},
            provider_data={"reasoning_item": _reasoning_item(), "item_id": "fc_1"},
        )
        items = _convert_messages_to_responses_input([
            ConversationMessage(role="assistant", content=[
                TextBlock(text="先说结论"), tool_use,
            ]),
        ])
        # 末尾的 function_call_output 是孤儿 tool_use 合成（测试未给结果）
        assert [i["type"] for i in items] == [
            "message", "reasoning", "function_call", "function_call_output",
        ]
        assert items[0]["content"][0]["text"] == "先说结论"
        # reasoning item 仍紧邻其配对的 function_call
        assert items[1]["id"] == "rs_1"
        assert items[2]["call_id"] == "call_1"

    def test_mixed_turn_text_after_tool_keeps_order(self):
        """文本+工具混合轮：文本在后时回放顺序 [reasoning, function_call, message]"""
        tool_use = ToolUseBlock(
            id="call_1", name="bash", input={},
            provider_data={"reasoning_item": _reasoning_item(), "item_id": "fc_1"},
        )
        items = _convert_messages_to_responses_input([
            ConversationMessage(role="assistant", content=[
                tool_use, TextBlock(text="工具结果说明"),
            ]),
        ])
        assert [i["type"] for i in items] == [
            "reasoning", "function_call", "message", "function_call_output",
        ]
        assert items[2]["content"][0]["text"] == "工具结果说明"

    def test_mixed_turn_text_split_around_tools(self):
        """文本被工具调用分隔为两段时，按原顺序输出两个 message item"""
        tool_use = ToolUseBlock(
            id="call_1", name="bash", input={},
            provider_data={"reasoning_item": _reasoning_item()},
        )
        items = _convert_messages_to_responses_input([
            ConversationMessage(role="assistant", content=[
                TextBlock(text="第一段"), tool_use, TextBlock(text="第二段"),
            ]),
        ])
        assert [i["type"] for i in items] == [
            "message", "reasoning", "function_call", "message", "function_call_output",
        ]
        assert items[0]["content"][0]["text"] == "第一段"
        assert items[3]["content"][0]["text"] == "第二段"


class TestReasoningItemErrorDetection:
    """is_reasoning_item_passback_error 错误识别测试"""

    def test_matches_reasoning_without_follower(self):
        assert is_reasoning_item_passback_error(
            "Item 'rs_1' of type 'reasoning' was provided without its required following item."
        )

    def test_matches_function_call_without_reasoning(self):
        assert is_reasoning_item_passback_error(
            "Item 'fc_1' of type 'function_call' was provided without its required 'rs_2' item."
        )

    def test_rejects_unrelated_errors(self):
        assert not is_reasoning_item_passback_error("prompt is too long")
        assert not is_reasoning_item_passback_error("Invalid API key provided")


# ===== 流式解析测试（伪造 httpx 客户端） =====

SSE_OK = [
    "data: " + json.dumps({
        "type": "response.output_item.done",
        "item": {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc-123"},
    }),
    "",
    "data: " + json.dumps({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "call_id": "call_1", "name": "bash"},
    }),
    "",
    "data: " + json.dumps({
        "type": "response.output_item.done",
        "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "bash", "arguments": "{\"command\":\"ls\"}"},
    }),
    "",
    "data: " + json.dumps({
        "type": "response.output_text.delta",
        "delta": "done",
    }),
    "",
    "data: " + json.dumps({
        "type": "response.completed",
        "response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    }),
    "",
    "data: [DONE]",
    "",
]


class _FakeResponse:
    def __init__(self, lines, *, status_code=200, body=b""):
        self._lines = lines
        self.status_code = status_code
        self._body = body
        self.request = None  # httpx.HTTPStatusError 构造需要

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._body


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return False


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.captured_bodies = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, json=None):
        self.captured_bodies.append(json)
        return _FakeStreamCtx(_FakeResponse(self._responses.pop(0)))


def _make_request(messages=None, *, effort=None):
    return ApiMessageRequest(
        model="gpt-5.4",
        messages=messages if messages is not None else [ConversationMessage.from_user_text("hi")],
        max_tokens=1024,
        effort=effort,
    )


@pytest.mark.asyncio
async def test_stream_captures_reasoning_item_and_function_call(monkeypatch):
    """流式解析：捕获 reasoning item / item id 到 provider_data 并组装最终消息"""
    fake = _FakeAsyncClient([SSE_OK])
    monkeypatch.setattr("illusion.api.responses_client.create_async_client", lambda **kw: fake)
    client = ResponsesApiClient(api_key="sk-test")

    events = []
    async for event in client.stream_message(_make_request()):
        events.append(event)

    complete = next(e for e in events if isinstance(e, ApiMessageCompleteEvent))
    tool_uses = complete.message.tool_uses
    assert len(tool_uses) == 1
    assert tool_uses[0].id == "call_1"
    assert tool_uses[0].name == "bash"
    assert tool_uses[0].input == {"command": "ls"}
    assert tool_uses[0].provider_data["reasoning_item"]["id"] == "rs_1"
    assert tool_uses[0].provider_data["item_id"] == "fc_1"
    assert complete.usage.output_tokens == 5
    assert complete.stop_reason == "tool_use"
    assert any(isinstance(e, ApiTextDeltaEvent) and e.text == "done" for e in events)
    # 请求体：store=false + 加密思考 include
    assert fake.captured_bodies[0]["store"] is False
    assert fake.captured_bodies[0]["include"] == ["reasoning.encrypted_content"]


@pytest.mark.asyncio
async def test_reasoning_passback_error_retries_with_store(monkeypatch):
    """reasoning 回传校验 400 后降级 store:true 重试一次"""
    error_payload = json.dumps({
        "error": {"message": "Item 'fc_1' of type 'function_call' was provided without its required 'rs_1' item."},
    }).encode()
    ok_response = _FakeResponse(SSE_OK)
    error_response = _FakeResponse([], status_code=400, body=error_payload)
    responses = [error_response, ok_response]
    captured_bodies = []

    class _QueueClient(_FakeAsyncClient):
        def stream(self, method, url, headers=None, json=None):
            captured_bodies.append(json)
            return _FakeStreamCtx(responses.pop(0))

    fake = _QueueClient([])
    monkeypatch.setattr(
        "illusion.api.responses_client.create_async_client", lambda **kw: fake,
    )
    client = ResponsesApiClient(api_key="sk-test")

    events = []
    async for event in client.stream_message(_make_request()):
        events.append(event)

    # 第一次 store=false（400 自愈触发），第二次 store=true（降级成功）
    assert captured_bodies[0]["store"] is False
    assert captured_bodies[1]["store"] is True
    # 降级请求不再携带加密思考 include（store:true 下无意义）
    assert "include" not in captured_bodies[1]
    assert client._prefer_store is True
    completes = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
    assert len(completes) == 1


@pytest.mark.asyncio
async def test_prefer_store_memory_skips_failing_first_attempt(monkeypatch):
    """实例级记忆：降级过一次的端点，后续请求直接 store:true 发起"""
    error_payload = json.dumps({
        "error": {"message": "Item 'fc_1' of type 'function_call' was provided without its required 'rs_1' item."},
    }).encode()
    ok_response = _FakeResponse(SSE_OK)
    error_response = _FakeResponse([], status_code=400, body=error_payload)
    responses = [error_response, ok_response, ok_response]
    captured_bodies = []

    class _QueueClient(_FakeAsyncClient):
        def stream(self, method, url, headers=None, json=None):
            captured_bodies.append(json)
            return _FakeStreamCtx(responses.pop(0))

    fake = _QueueClient([])
    monkeypatch.setattr(
        "illusion.api.responses_client.create_async_client", lambda **kw: fake,
    )
    client = ResponsesApiClient(api_key="sk-test")

    async for _ in client.stream_message(_make_request()):
        pass  # 第一次调用：失败一次 + 降级成功
    async for _ in client.stream_message(_make_request()):
        pass  # 第二次调用：应直接 store:true，无失败请求

    assert len(captured_bodies) == 3  # 无第 4 次请求（未重复失败）
    assert captured_bodies[2]["store"] is True


@pytest.mark.asyncio
async def test_reasoning_passback_fallback_failure_gives_actionable_error(monkeypatch):
    """降级 store:true 后仍回传校验失败：抛出带处置建议的错误"""
    error_payload = json.dumps({
        "error": {"message": "Item 'fc_1' of type 'function_call' was provided without its required 'rs_1' item."},
    }).encode()
    responses = [
        _FakeResponse([], status_code=400, body=error_payload),
        _FakeResponse([], status_code=400, body=error_payload),
    ]
    captured_bodies = []

    class _QueueClient(_FakeAsyncClient):
        def stream(self, method, url, headers=None, json=None):
            captured_bodies.append(json)
            return _FakeStreamCtx(responses.pop(0))

    fake = _QueueClient([])
    monkeypatch.setattr(
        "illusion.api.responses_client.create_async_client", lambda **kw: fake,
    )
    client = ResponsesApiClient(api_key="sk-test")

    from illusion.api.errors import RequestFailure

    with pytest.raises(RequestFailure, match="reasoning item"):
        async for _ in client.stream_message(_make_request()):
            pass
    assert len(captured_bodies) == 2  # 降级重试一次后放弃，不无限重试


@pytest.mark.asyncio
async def test_effort_passed_as_reasoning_param(monkeypatch):
    """effort 通过 reasoning.effort 传递"""
    fake = _FakeAsyncClient([SSE_OK])
    monkeypatch.setattr("illusion.api.responses_client.create_async_client", lambda **kw: fake)
    client = ResponsesApiClient(api_key="sk-test")
    async for _ in client.stream_message(_make_request(effort=EffortLevel.HIGH)):
        pass
    assert fake.captured_bodies[0]["reasoning"] == {"effort": "high"}


# ===== 流式边界行为（reasoning delta / failed / 截断流） =====

SSE_WITH_REASONING = [
    "data: " + json.dumps({
        "type": "response.reasoning_text.delta",
        "delta": "思考中",
    }),
    "",
    "data: " + json.dumps({
        "type": "response.output_item.done",
        "item": {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc-9"},
    }),
    "",
    "data: " + json.dumps({
        "type": "response.output_text.delta",
        "delta": "答案",
    }),
    "",
    "data: " + json.dumps({
        "type": "response.completed",
        "response": {"status": "completed", "usage": {"input_tokens": 8, "output_tokens": 4}},
    }),
    "",
    "data: [DONE]",
    "",
]

SSE_TRUNCATED = [
    "data: " + json.dumps({
        "type": "response.output_text.delta",
        "delta": "半截",
    }),
    "",
    "data: [DONE]",
    "",
]

SSE_FAILED = [
    "data: " + json.dumps({
        "type": "response.failed",
        "response": {"status": "failed", "error": {"message": "tool use not allowed"}},
    }),
    "",
    "data: [DONE]",
    "",
]

SSE_ERROR_EVENT = [
    "data: " + json.dumps({"type": "error", "message": "bad gateway"}),
    "",
    "data: [DONE]",
    "",
]


@pytest.mark.asyncio
async def test_reasoning_delta_becomes_thinking_block(monkeypatch):
    """reasoning delta 聚合为 ThinkingBlock，同时以 reasoning 事件流出"""
    fake = _FakeAsyncClient([SSE_WITH_REASONING])
    monkeypatch.setattr("illusion.api.responses_client.create_async_client", lambda **kw: fake)
    client = ResponsesApiClient(api_key="sk-test")

    reasoning_deltas = []
    complete = None
    async for event in client.stream_message(_make_request()):
        if isinstance(event, ApiTextDeltaEvent) and event.reasoning:
            reasoning_deltas.append(event.reasoning)
        if isinstance(event, ApiMessageCompleteEvent):
            complete = event

    assert reasoning_deltas == ["思考中"]
    assert complete is not None
    thinking = [b for b in complete.message.content if b.type == "thinking"]
    assert thinking and thinking[0].thinking == "思考中"
    assert any(b.type == "text" and b.text == "答案" for b in complete.message.content)


@pytest.mark.asyncio
async def test_truncated_stream_raises_instead_of_silent_success(monkeypatch):
    """流中断无 response.completed：报错交给重试，而非静默零 usage 成功"""
    from illusion.api.errors import RequestFailure

    fake = _FakeAsyncClient([SSE_TRUNCATED])
    monkeypatch.setattr("illusion.api.responses_client.create_async_client", lambda **kw: fake)
    client = ResponsesApiClient(api_key="sk-test")

    with pytest.raises(RequestFailure, match="response.completed"):
        async for _ in client.stream_message(_make_request()):
            pass


@pytest.mark.asyncio
async def test_response_failed_event_raises(monkeypatch):
    """response.failed 事件转换为 RequestFailure"""
    from illusion.api.errors import RequestFailure

    fake = _FakeAsyncClient([SSE_FAILED])
    monkeypatch.setattr("illusion.api.responses_client.create_async_client", lambda **kw: fake)
    client = ResponsesApiClient(api_key="sk-test")

    with pytest.raises(RequestFailure, match="tool use not allowed"):
        async for _ in client.stream_message(_make_request()):
            pass


@pytest.mark.asyncio
async def test_error_event_raises(monkeypatch):
    """SSE error 事件转换为 RequestFailure"""
    from illusion.api.errors import RequestFailure

    fake = _FakeAsyncClient([SSE_ERROR_EVENT])
    monkeypatch.setattr("illusion.api.responses_client.create_async_client", lambda **kw: fake)
    client = ResponsesApiClient(api_key="sk-test")

    with pytest.raises(RequestFailure, match="bad gateway"):
        async for _ in client.stream_message(_make_request()):
            pass
