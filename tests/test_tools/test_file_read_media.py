"""Tests for FileReadTool image file handling and capability guards."""
from __future__ import annotations

from pathlib import Path

import pytest

from illusion.config.capabilities import ModelCapabilities
from illusion.tools.base import ToolExecutionContext
from illusion.tools.file_read_tool import FileReadTool, FileReadToolInput


def _media_context(
    tmp_path: Path,
    *,
    capabilities: ModelCapabilities | None = None,
) -> ToolExecutionContext:
    """构造带指定模型能力上下文的工具上下文（None = 未声明能力）。"""
    ctx = ToolExecutionContext(cwd=tmp_path)
    if capabilities is not None:
        ctx.metadata["model_capabilities"] = capabilities
    return ctx


def _write_image(tmp_path: Path, name: str = "test.png") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return p


@pytest.mark.asyncio
async def test_read_image_png(tmp_path: Path):
    context = _media_context(tmp_path, capabilities=ModelCapabilities(supports_images=True))
    result = await FileReadTool().execute(
        FileReadToolInput(path=str(_write_image(tmp_path))),
        context,
    )
    assert result.is_error is False
    assert result.metadata.get("media_category") == "image"
    assert result.metadata.get("media_type") == "image/png"
    assert "media_data" in result.metadata
    assert "media_path" in result.metadata


@pytest.mark.asyncio
async def test_read_image_jpg(tmp_path: Path):
    context = _media_context(tmp_path, capabilities=ModelCapabilities(supports_images=True))
    img_path = tmp_path / "photo.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
    result = await FileReadTool().execute(
        FileReadToolInput(path=str(img_path)),
        context,
    )
    assert result.is_error is False
    assert result.metadata.get("media_category") == "image"
    assert result.metadata.get("media_type") == "image/jpeg"


@pytest.mark.asyncio
async def test_read_image_without_capability_rejected(tmp_path: Path):
    """无图片能力（含能力未声明）读取图片 → 明确报错，不产生媒体块。"""
    for caps in (None, ModelCapabilities()):
        context = _media_context(tmp_path, capabilities=caps)
        result = await FileReadTool().execute(
            FileReadToolInput(path=str(_write_image(tmp_path))),
            context,
        )
        assert result.is_error is True
        assert "does not support image input" in result.output
        assert "model" in result.output
        assert result.metadata.get("media_category") is None


@pytest.mark.asyncio
async def test_read_image_oversized(tmp_path: Path):
    context = _media_context(tmp_path, capabilities=ModelCapabilities(supports_images=True))
    img_path = tmp_path / "big.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (21 * 1024 * 1024))
    result = await FileReadTool().execute(
        FileReadToolInput(path=str(img_path)),
        context,
    )
    assert result.is_error is True
    assert "too large" in result.output.lower()


@pytest.mark.asyncio
async def test_read_text_file_unchanged(tmp_path: Path):
    context = ToolExecutionContext(cwd=tmp_path)
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("hello world\n", encoding="utf-8")
    result = await FileReadTool().execute(
        FileReadToolInput(path=str(txt_path)),
        context,
    )
    assert result.is_error is False
    assert "hello world" in result.output
    assert not result.metadata.get("media_category")


@pytest.mark.asyncio
async def test_read_text_file_unaffected_by_capabilities(tmp_path: Path):
    """文本读取不受媒体能力影响（无能力的模型仍可正常读文本）。"""
    context = _media_context(tmp_path, capabilities=ModelCapabilities())
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("hello world\n", encoding="utf-8")
    result = await FileReadTool().execute(
        FileReadToolInput(path=str(txt_path)),
        context,
    )
    assert result.is_error is False
    assert "hello world" in result.output
