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


class TestUnsupportedMediaTypeStripping:
    """模型具备图片能力时，线格式不支持的媒体类型仍需转文本占位。

    API 网关只接受 JPEG/PNG/GIF/WebP 位图；SVG 等其他 MIME 一旦作为
    图片块进入历史，后续所有请求都会被 422 拒绝（毒化历史），
    必须在发送前过滤。
    """

    def _msg(self, *blocks):
        return ConversationMessage(role="user", content=list(blocks))

    def _svg(self) -> MediaBlock:
        return MediaBlock(
            file_path="/tmp/a.svg",
            media_type="image/svg+xml",
            data="PHN2Zy8+",
            metadata={"size": 42},
        )

    def test_capability_strips_unsupported_media_type(self):
        msgs = [self._msg(self._svg())]
        stripped = strip_media_if_unsupported(
            msgs, ModelCapabilities(supports_images=True)
        )
        assert stripped is not None
        assert all(isinstance(b, TextBlock) for b in stripped[0].content)
        assert "image/svg+xml" in stripped[0].content[0].text

    def test_capability_keeps_supported_media_type(self):
        msgs = [self._msg(_media())]
        assert strip_media_if_unsupported(
            msgs, ModelCapabilities(supports_images=True)
        ) is None

    def test_capability_mixed_types_strips_only_unsupported(self):
        msgs = [self._msg(_media(), self._svg())]
        stripped = strip_media_if_unsupported(
            msgs, ModelCapabilities(supports_images=True)
        )
        assert stripped is not None
        assert isinstance(stripped[0].content[0], MediaBlock)  # png 保留
        assert isinstance(stripped[0].content[1], TextBlock)  # svg 转占位

    def test_capability_tool_result_nested_unsupported_media(self):
        tr = ToolResultBlock(tool_use_id="t1", content=[self._svg()])
        msgs = [self._msg(tr)]
        stripped = strip_media_if_unsupported(
            msgs, ModelCapabilities(supports_images=True)
        )
        assert stripped is not None
        inner = stripped[0].content[0].content
        assert isinstance(inner, list)
        assert isinstance(inner[0], TextBlock)
        assert "image/svg+xml" in inner[0].text

    def test_original_messages_not_mutated_with_capability(self):
        msgs = [self._msg(self._svg())]
        strip_media_if_unsupported(msgs, ModelCapabilities(supports_images=True))
        assert isinstance(msgs[0].content[0], MediaBlock)


class TestMediaPlaceholder:
    def test_image_placeholder(self):
        text = _media_placeholder(_media())
        assert text.startswith("[image file: /tmp/a.png (42 bytes)")
        assert "does not support image input" in text

    def test_unsupported_format_placeholder_suggests_text_read(self):
        text = _media_placeholder(
            MediaBlock(
                file_path="/tmp/a.svg",
                media_type="image/svg+xml",
                data="PHN2Zy8+",
                metadata={"size": 42},
            ),
            unsupported_format=True,
        )
        assert "image/svg+xml" in text
        assert "read_file" in text


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
