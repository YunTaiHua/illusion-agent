"""Messages 层媒体能力处理测试：能力过滤、占位文案与线格式序列化。"""

from __future__ import annotations

from illusion.config.capabilities import ModelCapabilities
from illusion.engine.messages import (
    ConversationMessage,
    MediaBlock,
    TextBlock,
    ToolResultBlock,
    _build_tool_result_content,
    _media_placeholder,
    _serialize_media_block,
    strip_media_if_unsupported,
)


def _media(name: str = "a.png") -> MediaBlock:
    return MediaBlock(
        file_path=f"/tmp/{name}",
        media_type=f"image/{name.rsplit('.', 1)[-1]}",
        data="b64data",
        metadata={"size": 42},
    )


class TestStripMediaIfUnsupported:
    def _msg(self, *blocks):
        return ConversationMessage(role="user", content=list(blocks))

    def test_none_capabilities_strips_all(self):
        msgs = [self._msg(_media())]
        stripped = strip_media_if_unsupported(msgs, None)
        assert stripped is not None
        assert all(isinstance(b, TextBlock) for b in stripped[0].content)

    def test_no_capabilities_strips_all(self):
        msgs = [self._msg(_media())]
        stripped = strip_media_if_unsupported(msgs, ModelCapabilities())
        assert stripped is not None
        assert stripped[0].content[0].text.startswith("[image file:")

    def test_image_capability_keeps_media(self):
        msgs = [self._msg(_media())]
        assert strip_media_if_unsupported(
            msgs, ModelCapabilities(supports_images=True)
        ) is None

    def test_no_media_returns_none(self):
        msgs = [self._msg(TextBlock(text="hi"))]
        assert strip_media_if_unsupported(msgs, None) is None

    def test_tool_result_nested_media(self):
        tr = ToolResultBlock(tool_use_id="t1", content=[_media()])
        msgs = [self._msg(tr)]
        stripped = strip_media_if_unsupported(msgs, ModelCapabilities())
        assert stripped is not None
        inner = stripped[0].content[0].content
        assert isinstance(inner, list)
        assert isinstance(inner[0], TextBlock)
        assert "does not support image input" in inner[0].text

    def test_original_messages_not_mutated(self):
        msgs = [self._msg(_media())]
        strip_media_if_unsupported(msgs, ModelCapabilities())
        assert isinstance(msgs[0].content[0], MediaBlock)


class TestMediaPlaceholder:
    def test_image_placeholder(self):
        text = _media_placeholder(_media())
        assert text.startswith("[image file: /tmp/a.png (42 bytes)")
        assert "does not support image input" in text


class TestSerializeMediaBlock:
    def test_image_anthropic_stays_image(self):
        part = _serialize_media_block(_media(), "anthropic")
        assert part["type"] == "image"
        assert part["source"]["type"] == "base64"

    def test_image_openai_compat_stays_image_url(self):
        part = _serialize_media_block(_media(), "openai_compat")
        assert part["type"] == "image_url"

    def test_image_codex_stays_input_image(self):
        part = _serialize_media_block(_media(), "openai_codex")
        assert part["type"] == "input_image"


class TestBuildToolResultContent:
    def test_media_metadata_builds_media_block(self):
        content = _build_tool_result_content(
            "[image file: /tmp/a.png]",
            {
                "media_category": "image",
                "media_path": "/tmp/a.png",
                "media_type": "image/png",
                "media_data": "b64",
                "media_size": 42,
            },
        )
        assert isinstance(content, list)
        assert content[1].file_path == "/tmp/a.png"
        assert content[1].metadata["size"] == 42

    def test_no_media_metadata_returns_text(self):
        content = _build_tool_result_content("plain output", {})
        assert content == "plain output"
