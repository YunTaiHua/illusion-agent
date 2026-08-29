"""Client 层媒体能力守卫测试：发送前按能力把不支持的图片块转文本占位。

覆盖：
- Anthropic / OpenAI 兼容 / Responses 三客户端的 stream_message 事前降级
"""

from __future__ import annotations

from typing import Any

import pytest

from illusion.api.client import ApiMessageRequest
from illusion.api.openai_client import _serialize_media_for_openai
from illusion.config.capabilities import ModelCapabilities
from illusion.engine.messages import (
    ConversationMessage,
    MediaBlock,
    TextBlock,
)


def _make_image() -> MediaBlock:
    return MediaBlock(
        file_path="/tmp/a.png",
        media_type="image/png",
        data="aGVsbG8=",
        metadata={"size": 5},
    )


def _request(caps: ModelCapabilities | None, blocks: list[Any]) -> ApiMessageRequest:
    return ApiMessageRequest(
        model="test-model",
        messages=[ConversationMessage(role="user", content=blocks)],
        capabilities=caps,
    )


def _capture_stream_once(collect: list[ApiMessageRequest]):
    """构造记录请求的 _stream_once mock（返回空流）。

    monkeypatch.setattr 挂到实例属性上的函数不会绑定 self（Python 仅对
    类属性做描述符绑定），故同时兼容已绑定（第一参 self）与未绑定形态。
    """

    async def _fake_stream_once(*args, **kwargs):
        if args and isinstance(args[0], ApiMessageRequest):
            collect.append(args[0])
        elif len(args) >= 2:
            collect.append(args[1])
        return
        yield  # pragma: no cover

    return _fake_stream_once


async def _drain(client, request: ApiMessageRequest) -> None:
    async for _ in client.stream_message(request):
        pass


class TestStreamPreGuard:
    @pytest.mark.asyncio
    async def test_anthropic_strips_media_without_capability(self, monkeypatch):
        from illusion.api.client import AnthropicApiClient

        collected: list[ApiMessageRequest] = []
        client = AnthropicApiClient(api_key="k")
        monkeypatch.setattr(client, "_stream_once", _capture_stream_once(collected))

        request = _request(None, [_make_image(), TextBlock(text="hi")])
        await _drain(client, request)

        assert len(collected) == 1
        sent = collected[0]
        assert not any(isinstance(b, MediaBlock) for b in sent.messages[0].content)
        assert sent.messages[0].content[0].text.startswith("[image file:")
        # capabilities 空 = fail-closed：图片全部转文本
        assert all(isinstance(b, TextBlock) for b in sent.messages[0].content)

    @pytest.mark.asyncio
    async def test_anthropic_keeps_media_with_capability(self, monkeypatch):
        from illusion.api.client import AnthropicApiClient

        collected: list[ApiMessageRequest] = []
        client = AnthropicApiClient(api_key="k")
        monkeypatch.setattr(client, "_stream_once", _capture_stream_once(collected))

        request = _request(
            ModelCapabilities(supports_images=True),
            [_make_image()],
        )
        await _drain(client, request)

        sent = collected[0]
        blocks = sent.messages[0].content
        assert len([b for b in blocks if isinstance(b, MediaBlock)]) == 1

    @pytest.mark.asyncio
    async def test_responses_strips_media_without_capability(self, monkeypatch):
        from illusion.api.responses_client import ResponsesApiClient

        collected: list[ApiMessageRequest] = []
        client = ResponsesApiClient(api_key="k")
        monkeypatch.setattr(client, "_stream_once", _capture_stream_once(collected))

        request = _request(ModelCapabilities(), [_make_image()])
        await _drain(client, request)

        sent = collected[0]
        assert sent.messages[0].content[0].text.startswith("[image file:")

    @pytest.mark.asyncio
    async def test_no_capability_strips_before_send(self, monkeypatch):
        """未注入 capabilities（None）视同无能力：发送前全部转文本。"""
        from illusion.api.responses_client import ResponsesApiClient

        collected: list[ApiMessageRequest] = []
        client = ResponsesApiClient(api_key="k")
        monkeypatch.setattr(client, "_stream_once", _capture_stream_once(collected))

        request = _request(None, [_make_image()])
        await _drain(client, request)

        blocks = collected[0].messages[0].content
        assert not any(isinstance(b, MediaBlock) for b in blocks)


class TestOpenaiImageSerialization:
    def test_image_serialized_as_image_url(self):
        part = _serialize_media_for_openai(_make_image())
        assert part["type"] == "image_url"
        assert part["image_url"]["url"].startswith("data:image/png;base64,")
