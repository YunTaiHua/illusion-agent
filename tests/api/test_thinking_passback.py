"""思考内容回传（thinking passback）修复测试模块

本模块提供 DeepSeek 思考模式回传校验修复的单元测试，包括：
- repair_thinking_passback 占位补齐（Anthropic 路径）
- _is_thinking_passback_error 错误识别（自愈重试触发条件）
- OpenAI 兼容路径 reasoning_content 占位回放
- redacted_thinking 捕获与回放
- Anthropic 客户端自愈级联（伪造 SDK）
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from anthropic import APIStatusError

import illusion.api.error_log as error_log_module
from illusion.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    repair_thinking_passback,
)
from illusion.api.compat import (
    THINKING_PASSBACK_PLACEHOLDER,
    is_thinking_passback_error,
)
from illusion.api.effort import EffortLevel
from illusion.api.openai_client import _convert_assistant_message
from illusion.engine.messages import (
    ConversationMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    serialize_content_block,
)


@pytest.fixture(autouse=True)
def _isolate_api_error_log(tmp_path, monkeypatch):
    """把 API 错误日志隔离到临时目录。

    自愈级联测试会触发真实的 400 → log_api_error 写入；不隔离的话
    伪造的错误记录会污染开发者的真实 ~/.illusion/logs/api_error.log。
    """
    monkeypatch.setenv("ILLUSION_LOGS_DIR", str(tmp_path))
    # 重置进程级单例，确保本次测试的 handler 指向临时目录
    error_log_module._logger = None
    yield
    error_log_module._logger = None


def _assistant_no_thinking(*, with_tool: bool = False) -> ConversationMessage:
    """构造一条缺 thinking 块的 assistant 消息。"""
    blocks: list = [TextBlock(text="你好")]
    if with_tool:
        blocks.append(ToolUseBlock(id="call_1", name="bash", input={"command": "ls"}))
    return ConversationMessage(role="assistant", content=blocks)


def _assistant_with_thinking() -> ConversationMessage:
    """构造一条带 thinking 块的 assistant 消息。"""
    return ConversationMessage(role="assistant", content=[
        ThinkingBlock(thinking="推理内容", signature=""),
        TextBlock(text="你好"),
    ])


class TestRepairThinkingPassback:
    """repair_thinking_passback 占位补齐测试"""

    def test_adds_placeholder_to_thinkingless_assistant(self):
        """缺 thinking 块的 assistant 消息被补上占位块（位于首位）"""
        fixed = repair_thinking_passback([_assistant_no_thinking(with_tool=True)])
        blocks = fixed[0].content
        assert isinstance(blocks[0], ThinkingBlock)
        assert blocks[0].thinking == THINKING_PASSBACK_PLACEHOLDER
        assert isinstance(blocks[1], TextBlock)
        assert isinstance(blocks[2], ToolUseBlock)

    def test_keeps_existing_thinking_blocks(self):
        """已有 thinking 块的消息原样保留"""
        original = _assistant_with_thinking()
        fixed = repair_thinking_passback([original])
        assert fixed[0].content == original.content

    def test_idempotent(self):
        """重复修复为空操作（幂等）"""
        once = repair_thinking_passback([_assistant_no_thinking()])
        twice = repair_thinking_passback(once)
        assert once == twice

    def test_does_not_mutate_input(self):
        """修复返回新列表，不修改入参消息"""
        original = _assistant_no_thinking(with_tool=True)
        repair_thinking_passback([original])
        assert not any(isinstance(b, ThinkingBlock) for b in original.content)

    def test_user_messages_untouched(self):
        """user 消息不补占位块"""
        user = ConversationMessage.from_user_text("hi")
        fixed = repair_thinking_passback([user])
        assert fixed[0].role == "user"
        assert all(not isinstance(b, ThinkingBlock) for b in fixed[0].content)

    def test_replayed_param_carries_thinking_first(self):
        """修复后 to_api_param 的 content 首块为 thinking"""
        fixed = repair_thinking_passback([_assistant_no_thinking()])
        param = fixed[0].to_api_param(provider_type="anthropic")
        assert param["content"][0]["type"] == "thinking"
        assert param["content"][0]["thinking"] == THINKING_PASSBACK_PLACEHOLDER


class TestThinkingPassbackErrorDetection:
    """_is_thinking_passback_error 错误识别测试"""

    def test_matches_anthropic_style_message(self):
        """匹配 content[].thinking 文案（含 request id 尾巴）"""
        exc = Exception(
            "Error code: 400 - The `content[].thinking` in the thinking mode "
            "must be passed back to the API. (request id: 20260829...)"
        )
        assert is_thinking_passback_error(exc)

    def test_matches_openai_style_message(self):
        """匹配 reasoning_content 文案"""
        exc = Exception("The `reasoning_content` in the thinking mode must be passed back to the API.")
        assert is_thinking_passback_error(exc)

    def test_rejects_unrelated_400(self):
        """其他 400 错误不误判"""
        assert not is_thinking_passback_error(Exception("Extra inputs are not permitted"))
        assert not is_thinking_passback_error(Exception("prompt is too long: 300000 tokens > 262144 maximum"))


class TestOpenAIAssistantReasoningReplay:
    """OpenAI 兼容路径 reasoning_content 回放测试"""

    def test_deepseek_tool_call_gets_placeholder(self):
        """DeepSeek 模型 tool-call 轮缺 thinking 时回放占位 reasoning_content"""
        msg = _assistant_no_thinking(with_tool=True)
        out = _convert_assistant_message(msg, model="deepseek-v4-flash", has_tools=True)
        assert out["reasoning_content"] == THINKING_PASSBACK_PLACEHOLDER

    def test_deepseek_replays_captured_thinking(self):
        """DeepSeek 模型已捕获的 thinking 原文回放"""
        msg = ConversationMessage(role="assistant", content=[
            ThinkingBlock(thinking="真实推理内容"),
            ToolUseBlock(id="call_1", name="bash", input={}),
        ])
        out = _convert_assistant_message(msg, model="deepseek-v4-flash")
        assert out["reasoning_content"] == "真实推理内容"

    def test_non_deepseek_keeps_empty_reasoning(self):
        """非 DeepSeek 模型（如 qwen）保持原有空串行为"""
        msg = _assistant_no_thinking(with_tool=True)
        out = _convert_assistant_message(msg, model="qwen3.8-max", has_tools=True)
        assert out["reasoning_content"] == ""

    def test_deepseek_text_only_turn_gets_placeholder(self):
        """DeepSeek 带工具请求的纯文本轮缺 thinking 时同样补占位"""
        msg = _assistant_no_thinking()
        out = _convert_assistant_message(msg, model="deepseek-v4-flash", has_tools=True)
        assert out["reasoning_content"] == THINKING_PASSBACK_PLACEHOLDER

    def test_deepseek_placeholder_skipped_without_tools(self):
        """纯聊天请求（无 tools）不注入占位（官方文档：reasoning_content 被忽略）"""
        msg = _assistant_no_thinking()
        out = _convert_assistant_message(msg, model="deepseek-v4-flash", has_tools=False)
        assert "reasoning_content" not in out


class TestRedactedThinking:
    """redacted_thinking 捕获与回放测试"""

    def test_serialize_redacted_block(self):
        """redacted 块序列化为 redacted_thinking（data 字段）"""
        block = ThinkingBlock(thinking="", signature="enc-data", redacted=True)
        out = serialize_content_block(block, provider_type="anthropic")
        assert out == {"type": "redacted_thinking", "data": "enc-data"}

    def test_serialize_normal_thinking_unchanged(self):
        """普通 thinking 块序列化行为不变"""
        block = ThinkingBlock(thinking="推理", signature="sig")
        out = serialize_content_block(block, provider_type="anthropic")
        assert out == {"type": "thinking", "thinking": "推理", "signature": "sig"}
        # signature 为空时省略字段（与第三方端点 null signature 兼容）
        out2 = serialize_content_block(ThinkingBlock(thinking="推理"), provider_type="anthropic")
        assert out2 == {"type": "thinking", "thinking": "推理"}

    def test_redacted_defaults_false(self):
        """旧会话数据（无 redacted 字段）加载后默认 False"""
        block = ThinkingBlock.model_validate({"type": "thinking", "thinking": "x"})
        assert block.redacted is False


# ===== Anthropic 客户端自愈级联测试（伪造 SDK 客户端） =====

_PASSBACK_400 = APIStatusError(
    "Error code: 400 - {'error': {'type': 'invalid_request_error', 'message': "
    "'The `content[].thinking` in the thinking mode must be passed back to the API.'}}",
    response=httpx.Response(400, request=httpx.Request("POST", "http://test")),
    body=None,
)


def _ok_final_message():
    """构造 get_final_message 返回的正常消息对象。"""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=2,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        ),
        stop_reason="stop",
    )


class _FakeAnthropic:
    """伪造 AsyncAnthropic：按序抛出错误 / 返回正常流"""

    def __init__(self, behaviors):
        # behaviors: 列表，元素为 Exception（抛出）或 None（正常返回）
        self._behaviors = list(behaviors)
        self.captured_params = []
        self.messages = self  # 伪造 messages 命名空间（self._client.messages.stream）

    def stream(self, **params):
        self.captured_params.append(params)
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeStream(behavior)


class _FakeStream:
    def __init__(self, final_message):
        self._final_message = final_message

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get_final_message(self):
        return self._final_message


def _make_anthropic_request(model, *, effort=EffortLevel.HIGH):
    return ApiMessageRequest(
        model=model,
        messages=[
            ConversationMessage.from_user_text("hi"),
            _assistant_no_thinking(with_tool=True),
            ConversationMessage(role="user", content=[
                ToolResultBlock(tool_use_id="call_1", content="out"),
            ]),
        ],
        max_tokens=1024,
        effort=effort,
    )


@pytest.mark.asyncio
async def test_self_heal_repair_retry_for_non_deepseek(monkeypatch):
    """非 DeepSeek 模型 400 后：补齐占位 thinking 块重试成功"""
    from illusion.api.client import AnthropicApiClient

    fake = _FakeAnthropic([_PASSBACK_400, _ok_final_message()])
    monkeypatch.setattr(AnthropicApiClient, "_create_client", lambda self: fake)
    client = AnthropicApiClient(api_key="sk-test")

    events = []
    async for event in client.stream_message(_make_anthropic_request("my-custom-model")):
        events.append(event)

    completes = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
    assert len(completes) == 1
    # 第二次请求中 assistant 消息的 content 首块为占位 thinking
    messages = fake.captured_params[1]["messages"]
    assistant_params = [m for m in messages if m["role"] == "assistant"]
    assert assistant_params[0]["content"][0]["type"] == "thinking"
    assert assistant_params[0]["content"][0]["thinking"] == THINKING_PASSBACK_PLACEHOLDER


@pytest.mark.asyncio
async def test_self_heal_disables_thinking_after_repair_noop(monkeypatch):
    """DeepSeek 模型主动补齐后仍 400：降级为无 thinking 参数重试成功"""
    from illusion.api.client import AnthropicApiClient

    fake = _FakeAnthropic([_PASSBACK_400, _ok_final_message()])
    monkeypatch.setattr(AnthropicApiClient, "_create_client", lambda self: fake)
    client = AnthropicApiClient(api_key="sk-test")

    events = []
    async for event in client.stream_message(_make_anthropic_request("deepseek-v4-flash")):
        events.append(event)

    completes = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
    assert len(completes) == 1
    # 第一次请求有 thinking 参数（主动补齐生效）；第二次降级后无 thinking 参数
    assert "thinking" in fake.captured_params[0]
    assert "thinking" not in fake.captured_params[1]
    # 降级后无需占位块：thinking 已关闭，历史按原样回放
    messages = fake.captured_params[1]["messages"]
    assistant_params = [m for m in messages if m["role"] == "assistant"]
    assert assistant_params[0]["content"][0]["type"] == "text"


@pytest.mark.asyncio
async def test_unrelated_400_not_self_healed(monkeypatch):
    """非回传类 400 不触发自愈（直接抛出）"""
    from illusion.api.client import AnthropicApiClient

    other_400 = APIStatusError(
        "Error code: 400 - Extra inputs are not permitted",
        response=httpx.Response(400, request=httpx.Request("POST", "http://test")),
        body=None,
    )
    fake = _FakeAnthropic([other_400])
    monkeypatch.setattr(AnthropicApiClient, "_create_client", lambda self: fake)
    client = AnthropicApiClient(api_key="sk-test")

    with pytest.raises(Exception, match="Extra inputs"):
        async for _ in client.stream_message(_make_anthropic_request("deepseek-v4-flash")):
            pass
    assert len(fake.captured_params) == 1


@pytest.mark.asyncio
async def test_repair_memory_applies_proactively_on_next_call(monkeypatch):
    """实例级记忆：reactive 修复触发过一次后，下一次调用直接主动补齐"""
    from illusion.api.client import AnthropicApiClient

    # 第一次调用：400 → 修复重试 → 成功；第二次调用：直接成功
    fake = _FakeAnthropic([_PASSBACK_400, _ok_final_message(), _ok_final_message()])
    monkeypatch.setattr(AnthropicApiClient, "_create_client", lambda self: fake)
    client = AnthropicApiClient(api_key="sk-test")

    async for _ in client.stream_message(_make_anthropic_request("my-custom-model")):
        pass
    assert client._force_passback_repair is True
    # 第一次调用的首个请求未补齐（reactive），第二个请求已补齐
    first_call_first_attempt = fake.captured_params[0]["messages"]
    first_assistant = [m for m in first_call_first_attempt if m["role"] == "assistant"]
    assert first_assistant[0]["content"][0]["type"] != "thinking"

    async for _ in client.stream_message(_make_anthropic_request("my-custom-model")):
        pass
    # 第二次调用的首个请求（captured_params[2]）已主动补齐占位 thinking
    second_call_first_attempt = fake.captured_params[2]["messages"]
    second_assistant = [m for m in second_call_first_attempt if m["role"] == "assistant"]
    assert second_assistant[0]["content"][0]["type"] == "thinking"
    assert second_assistant[0]["content"][0]["thinking"] == THINKING_PASSBACK_PLACEHOLDER


# ===== Kimi 保留式思考线格式（对照官方 kimi-cli） =====

from types import SimpleNamespace as _NS

from illusion.api.client import ApiRetryEvent
from illusion.api.compat import is_reasoning_passback_error
from illusion.api.openai_client import (
    OpenAICompatibleClient,
    _extract_chunk_usage,
    _extract_openai_usage,
)


def _assistant_tool_only() -> ConversationMessage:
    """构造一条只有 tool_use、无任何文本/thinking 的 assistant 消息。"""
    return ConversationMessage(role="assistant", content=[
        ToolUseBlock(id="call_1", name="bash", input={"command": "ls"}),
    ])


class TestKimiPreservedThinking:
    """Kimi 保留式思考线格式测试（官方 kimi-cli 语义）"""

    def test_kimi_text_only_turn_gets_empty_reasoning_field(self):
        """Kimi 纯文本轮无 thinking 时也必须携带 reasoning_content（空串合法）"""
        out = _convert_assistant_message(
            _assistant_no_thinking(), model="kimi-k2.5",
            reasoning_field_all_turns=True, has_tools=True,
        )
        assert out["reasoning_content"] == ""

    def test_kimi_tool_call_turn_gets_empty_reasoning_field(self):
        """Kimi 工具轮无 thinking 时携带空 reasoning_content"""
        out = _convert_assistant_message(
            _assistant_no_thinking(with_tool=True), model="kimi/kimi-k3",
            reasoning_field_all_turns=True, has_tools=True,
        )
        assert out["reasoning_content"] == ""

    def test_kimi_replays_captured_thinking(self):
        """Kimi 已捕获的 thinking 原文回放"""
        msg = ConversationMessage(role="assistant", content=[
            ThinkingBlock(thinking="推理过程"),
            ToolUseBlock(id="call_1", name="bash", input={}),
        ])
        out = _convert_assistant_message(msg, model="kimi-k2.5")
        assert out["reasoning_content"] == "推理过程"

    def test_kimi_empty_tool_content_omits_content_key(self):
        """Kimi 工具轮空文本：省略 content 键（官方 kimi-cli：always accepted）"""
        out = _convert_assistant_message(_assistant_tool_only(), model="kimi-k2.5")
        assert "content" not in out
        assert "tool_calls" in out

    def test_deepseek_empty_tool_content_uses_empty_string(self):
        """DeepSeek 工具轮空文本：content 为 ""（官方示例形态，null 会被部分网关拒绝）"""
        out = _convert_assistant_message(_assistant_tool_only(), model="deepseek-v4-flash")
        assert out["content"] == ""

    def test_other_vendor_empty_tool_content_keeps_null(self):
        """其他供应商工具轮空文本：维持既有 null 行为"""
        out = _convert_assistant_message(_assistant_tool_only(), model="qwen3.8-max")
        assert out["content"] is None

    def test_forced_mode_unknown_vendor_all_turns(self):
        """reactive 自愈强制模式：未知供应商的所有轮补 reasoning_content"""
        out = _convert_assistant_message(
            _assistant_no_thinking(), model="some-alias-model",
            reasoning_field_all_turns=True, has_tools=True,
        )
        assert out["reasoning_content"] == ""


class TestKimiThinkingDetection:
    """_detect_thinking_config Kimi 分支测试"""

    def test_kimi_model_thinking_enabled(self):
        assert OpenAICompatibleClient._detect_thinking_config("kimi-k2.5") == {
            "thinking": {"type": "enabled"}
        }

    def test_kimi_prefixed_model_thinking_enabled(self):
        assert OpenAICompatibleClient._detect_thinking_config("kimi/kimi-k3") == {
            "thinking": {"type": "enabled"}
        }

    def test_non_kimi_unaffected(self):
        assert OpenAICompatibleClient._detect_thinking_config("gpt-5.4") is None


class TestReasoningPassbackDetection:
    """is_reasoning_passback_error 文案变体测试"""

    def test_deepseek_variants(self):
        assert is_reasoning_passback_error(
            "The `content[].thinking` in the thinking mode must be passed back to the API."
        )
        assert is_reasoning_passback_error(
            "The `reasoning_content` in the thinking mode must be passed back to the API."
        )

    def test_generic_required_variant(self):
        assert is_reasoning_passback_error(
            "Assistant message with tool_calls must have reasoning_content"
        )

    def test_unrelated_errors_not_matched(self):
        assert not is_reasoning_passback_error("max_tokens must be at least 1")
        assert not is_reasoning_passback_error("reasoning effort must be low/medium/high")
        assert not is_reasoning_passback_error("Invalid API key")

    def test_false_positive_shapes_not_matched(self):
        """参数校验类错误不误判（误判会触发永久降级自愈，代价高）"""
        assert not is_reasoning_passback_error(
            "reasoning_effort is required when thinking is enabled"
        )
        assert not is_reasoning_passback_error(
            "thinking must be provided inside reasoning config"
        )


class TestUsageExtraction:
    """usage 提取测试（含 Moonshot 非标准位置）"""

    def test_object_usage(self):
        usage = _NS(prompt_tokens=100, completion_tokens=7,
                    prompt_tokens_details=_NS(cached_tokens=40))
        out = _extract_openai_usage(usage)
        assert out == {"input_tokens": 60, "output_tokens": 7,
                       "cache_read_input_tokens": 40, "cache_creation_input_tokens": 0}

    def test_dict_usage(self):
        usage = {"prompt_tokens": 100, "completion_tokens": 7,
                 "prompt_tokens_details": {"cached_tokens": 40}}
        out = _extract_openai_usage(usage)
        assert out["input_tokens"] == 60
        assert out["cache_read_input_tokens"] == 40

    def test_moonshot_usage_inside_choice(self):
        """Moonshot 把 usage 放进 choices[0] 的 model_extra（非标准位置）"""
        chunk = _NS(
            choices=[_NS(delta=_NS(content=None, reasoning_content=None, tool_calls=None),
                         finish_reason=None,
                         model_extra={"usage": {"prompt_tokens": 50, "completion_tokens": 3}})],
            usage=None,
        )
        usage = _extract_chunk_usage(chunk)
        assert usage is not None
        assert _extract_openai_usage(usage)["output_tokens"] == 3

    def test_standard_usage_still_extracted(self):
        chunk = _NS(choices=[], usage=_NS(prompt_tokens=10, completion_tokens=2))
        assert _extract_chunk_usage(chunk) is chunk.usage


# ===== openai 客户端 reactive 自愈级联（伪造 AsyncOpenAI） =====

_REASONING_400 = type("_Fake400", (Exception,), {})(
    "The `reasoning_content` in the thinking mode must be passed back to the API."
)
_REASONING_400.status_code = 400


class _FakeCompletions:
    """伪造 AsyncOpenAI.chat.completions：按序抛错/返回流"""

    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.captured_params = []

    async def create(self, **params):
        self.captured_params.append(params)
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeChunkStream(behavior)


class _FakeChunkStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _ok_chunks():
    delta = _NS(content="ok", reasoning_content=None, tool_calls=None)
    choice = _NS(delta=delta, finish_reason="stop", model_extra=None)
    return [_NS(choices=[choice], usage=None)]


def _make_openai_client(monkeypatch, behaviors):
    """构造注入伪造 SDK 的 OpenAICompatibleClient（绕过真实 AsyncOpenAI 构建）"""
    fake = _FakeCompletions(behaviors)

    def _fake_init(self, api_key, **kw):
        self._client = _NS(chat=_NS(completions=fake))
        self._force_reasoning_field = False
        self._disable_thinking = False

    monkeypatch.setattr(OpenAICompatibleClient, "__init__", _fake_init)
    client = OpenAICompatibleClient(api_key="sk-test")
    return client, fake


@pytest.mark.asyncio
async def test_openai_self_heal_stage1_forces_reasoning_field(monkeypatch):
    """回传 400 → 强制所有 assistant 轮携带 reasoning_content 重试成功"""
    client, fake = _make_openai_client(monkeypatch, [_REASONING_400, _ok_chunks()])
    request = ApiMessageRequest(
        model="some-alias-model",
        messages=[
            ConversationMessage.from_user_text("hi"),
            _assistant_no_thinking(),  # 纯文本轮（无 thinking）
            ConversationMessage.from_user_text("ok?"),
        ],
        max_tokens=64,
        tools=[{"name": "bash", "description": "", "input_schema": {"type": "object"}}],
    )
    events = []
    async for event in client.stream_message(request):
        events.append(event)

    assert client._force_reasoning_field is True
    assert client._disable_thinking is False
    # 第二次请求：所有 assistant 消息都有 reasoning_content
    assistants = [m for m in fake.captured_params[1]["messages"] if m["role"] == "assistant"]
    assert assistants and all("reasoning_content" in m for m in assistants)
    assert any(isinstance(e, ApiRetryEvent) for e in events)


@pytest.mark.asyncio
async def test_openai_self_heal_stage2_disables_thinking(monkeypatch):
    """强制补字段仍 400 → 显式关闭 thinking 重试（thinking.type: disabled）"""
    client, fake = _make_openai_client(
        monkeypatch, [_REASONING_400, _REASONING_400, _ok_chunks()],
    )
    request = ApiMessageRequest(
        model="deepseek-v4-flash",
        messages=[ConversationMessage.from_user_text("hi")],
        max_tokens=64,
        effort=EffortLevel.HIGH,
    )
    async for _ in client.stream_message(request):
        pass

    assert client._disable_thinking is True
    # 第一次：thinking 注入 + reasoning_effort；第二次（stage1）同；
    # 第三次（stage2）：显式 thinking disabled，无 reasoning_effort
    # （DeepSeek 服务端默认开启思考，仅停止注入可能仍在思考模式）
    assert fake.captured_params[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "reasoning_effort" in fake.captured_params[0]
    assert "extra_body" in fake.captured_params[1]
    assert fake.captured_params[2]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in fake.captured_params[2]


@pytest.mark.asyncio
async def test_passback_error_not_mistaken_for_media(monkeypatch):
    """回传 400 优先于 media 降级：带图片的对话不丢图片、直接走自愈"""
    from illusion.engine.messages import MediaBlock

    media = MediaBlock(file_path="a.png", media_type="image/png", data="QUJD")
    user_with_media = ConversationMessage(role="user", content=[
        TextBlock(text="看图"), media,
    ])
    client, fake = _make_openai_client(monkeypatch, [_REASONING_400, _ok_chunks()])
    request = ApiMessageRequest(
        model="some-alias-model",
        messages=[user_with_media, _assistant_no_thinking()],
        max_tokens=64,
    )
    async for _ in client.stream_message(request):
        pass

    assert client._force_reasoning_field is True
    # 图片未被替换为文本占位（media 降级未触发），仍以 image_url 形式回传
    second_messages = fake.captured_params[1]["messages"]
    assert any(
        isinstance(p, dict) and p.get("type") == "image_url"
        for m in second_messages
        for p in (m["content"] if isinstance(m["content"], list) else [])
    )


@pytest.mark.asyncio
async def test_kimi_thinking_effort_gated(monkeypatch):
    """Kimi thinking 注入是 effort 门控的（opt-in，官方语义）"""
    client, fake = _make_openai_client(monkeypatch, [_ok_chunks(), _ok_chunks()])
    no_effort = ApiMessageRequest(
        model="kimi-k2.5",
        messages=[ConversationMessage.from_user_text("hi")],
        max_tokens=64,
    )
    with_effort = ApiMessageRequest(
        model="kimi-k2.5",
        messages=[ConversationMessage.from_user_text("hi")],
        max_tokens=64,
        effort=EffortLevel.HIGH,
    )
    async for _ in client.stream_message(no_effort):
        pass
    async for _ in client.stream_message(with_effort):
        pass

    # 无 effort：不注入 thinking；有 effort：注入
    assert "extra_body" not in fake.captured_params[0]
    assert fake.captured_params[1]["extra_body"] == {"thinking": {"type": "enabled"}}


@pytest.mark.asyncio
async def test_openai_self_heal_memory_skips_failure_on_next_call(monkeypatch):
    """实例级记忆：降级过的端点，下次调用直接 thinking 关闭，无失败请求"""
    client, fake = _make_openai_client(
        monkeypatch, [_REASONING_400, _REASONING_400, _ok_chunks(), _ok_chunks()],
    )
    request = ApiMessageRequest(
        model="deepseek-v4-flash",
        messages=[ConversationMessage.from_user_text("hi")],
        max_tokens=64,
    )
    async for _ in client.stream_message(request):
        pass  # 第一次调用：两级自愈后成功
    async for _ in client.stream_message(request):
        pass  # 第二次调用：直接成功，无失败请求

    assert len(fake.captured_params) == 4  # 无第 5 次请求
    # 降级后显式 thinking disabled
    assert fake.captured_params[3]["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_kimi_stream_params(monkeypatch):
    """Kimi 请求参数：max_completion_tokens 归一化 + prompt_cache_key"""
    client, fake = _make_openai_client(monkeypatch, [_ok_chunks()])
    request = ApiMessageRequest(
        model="kimi-k2.5",
        messages=[ConversationMessage.from_user_text("hi")],
        max_tokens=256,
        prompt_cache_key="sess123",
    )
    async for _ in client.stream_message(request):
        pass
    params = fake.captured_params[0]
    assert "max_completion_tokens" in params and "max_tokens" not in params
    # prompt_cache_key 经 extra_body 合并到请求体顶层（旧版 SDK 兼容）
    assert params["extra_body"] == {"prompt_cache_key": "sess123"}
    assert "stream_options" in params  # 官方行为：始终保留 usage 上报


@pytest.mark.asyncio
async def test_non_kimi_stream_params_unchanged(monkeypatch):
    """非 Kimi 请求参数：max_tokens 保留、无 prompt_cache_key"""
    client, fake = _make_openai_client(monkeypatch, [_ok_chunks()])
    request = ApiMessageRequest(
        model="gpt-5.4",
        messages=[ConversationMessage.from_user_text("hi")],
        max_tokens=256,
        prompt_cache_key="sess123",
    )
    async for _ in client.stream_message(request):
        pass
    params = fake.captured_params[0]
    assert "max_tokens" in params and "max_completion_tokens" not in params
    assert "prompt_cache_key" not in params
