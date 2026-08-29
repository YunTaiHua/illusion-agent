"""MCP 图片内容块支持测试：call_tool_detail 与 McpToolAdapter 媒体映射。"""

from __future__ import annotations

from typing import Any

from illusion.mcp.types import McpImageContent, McpToolInfo
from illusion.tools.mcp_tool import McpToolAdapter


class _FakeManager:
    """McpClientManager 测试替身：返回预置的 call_tool_detail 结果。"""

    def __init__(self, result: Any) -> None:
        self._result = result

    async def call_tool_detail(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        return self._result


def _tool_info() -> McpToolInfo:
    return McpToolInfo(
        server_name="node_repl",
        name="js",
        description="js tool",
        input_schema={"properties": {"code": {"type": "string"}}, "required": ["code"]},
    )


async def test_adapter_maps_first_image_to_media_metadata() -> None:
    """首张图片映射为 media_* 元数据（引擎转换为 MediaBlock）。"""
    from illusion.mcp.types import McpToolCallResult

    manager = _FakeManager(
        McpToolCallResult(
            text="done",
            images=[McpImageContent(data="aGk=", mime_type="image/png")],
        )
    )
    adapter = McpToolAdapter(manager, _tool_info())  # type: ignore[arg-type]
    assert adapter.name == "mcp__node_repl__js"
    result = await adapter.execute(_input(code="tab.screenshot()"), _context())
    assert result.output == "done"
    assert result.is_error is False
    assert result.metadata["media_category"] == "image"
    assert result.metadata["media_type"] == "image/png"
    assert result.metadata["media_data"] == "aGk="


async def test_adapter_lists_extra_images_in_text() -> None:
    """多图时仅首张内联，其余在文本中提示数量与大小。"""
    from illusion.mcp.types import McpToolCallResult

    manager = _FakeManager(
        McpToolCallResult(
            text="ok",
            images=[
                McpImageContent(data="aGk=", mime_type="image/png"),
                McpImageContent(data="eW8=", mime_type="image/jpeg"),
            ],
        )
    )
    adapter = McpToolAdapter(manager, _tool_info())  # type: ignore[arg-type]
    result = await adapter.execute(_input(code="x"), _context())
    assert result.metadata["media_data"] == "aGk="
    assert "additional images discarded: 1" in result.output
    assert "image/jpeg" in result.output


async def test_adapter_without_images_keeps_plain_output() -> None:
    """无图片时输出保持纯文本，元数据为空。"""
    from illusion.mcp.types import McpToolCallResult

    manager = _FakeManager(McpToolCallResult(text="plain result", images=[]))
    adapter = McpToolAdapter(manager, _tool_info())  # type: ignore[arg-type]
    result = await adapter.execute(_input(code="x"), _context())
    assert result.output == "plain result"
    assert result.metadata == {}


async def test_adapter_error_result_propagates() -> None:
    """服务器标记的错误透传 is_error。"""
    from illusion.mcp.types import McpToolCallResult

    manager = _FakeManager(McpToolCallResult(text="boom", is_error=True))
    adapter = McpToolAdapter(manager, _tool_info())  # type: ignore[arg-type]
    result = await adapter.execute(_input(code="x"), _context())
    assert result.is_error is True
    assert result.output == "boom"


def _input(**kwargs: Any):
    from illusion.tools.mcp_tool import _input_model_from_schema

    model = _input_model_from_schema("test", {"properties": {"code": {"type": "string"}}})
    return model(**kwargs)


def _context():
    from pathlib import Path

    from illusion.tools.base import ToolExecutionContext

    return ToolExecutionContext(cwd=Path("."))
