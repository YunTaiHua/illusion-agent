"""
MCP 工具适配器
=============

本模块提供将 MCP（Model Context Protocol）工具暴露为普通 IllusionAgent 工具的功能。

主要组件：
    - McpToolAdapter: 将一个 MCP 工具作为普通工具暴露

使用示例：
    >>> from illusion.tools.mcp_tool import McpToolAdapter
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, create_model

from illusion.mcp.client import McpClientManager
from illusion.mcp.types import MCP_TOOL_EXCEPTIONS, McpToolInfo
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult


class McpToolAdapter(BaseTool[Any]):
    """将一个 MCP 工具作为普通 IllusionAgent 工具暴露。

    用于集成 MCP 服务器提供的工具。
    """

    def __init__(self, manager: McpClientManager, tool_info: McpToolInfo) -> None:
        self._manager = manager
        self._tool_info = tool_info
        # 清理服务器和工具名称以形成有效的工具名
        server_segment = _sanitize_tool_segment(tool_info.server_name)
        tool_segment = _sanitize_tool_segment(tool_info.name)
        self.name = f"mcp__{server_segment}__{tool_segment}"
        self.description = tool_info.description or f"MCP tool {tool_info.name}"
        self.input_model = _input_model_from_schema(self.name, tool_info.input_schema)

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        del context
        # 调用 MCP 工具（保留内容块细节：图片映射为模型可见的媒体块）
        try:
            result = await self._manager.call_tool_detail(
                self._tool_info.server_name,
                self._tool_info.name,
                arguments.model_dump(mode="json"),
            )
        except MCP_TOOL_EXCEPTIONS as exc:
            return ToolResult(output=str(exc), is_error=True)
        output = result.text
        metadata: dict[str, Any] = {}
        if result.images:
            # 第一张图片内联为媒体块（引擎 _build_tool_result_content 消费）；
            # 其余以路径/序号在文本中提示，模型可经后续调用取用
            first = result.images[0]
            metadata["media_category"] = "image"
            metadata["media_type"] = first.mime_type
            metadata["media_data"] = first.data
            metadata["media_size"] = len(first.data) * 3 // 4
            if len(result.images) > 1:
                extra = "\n".join(
                    f"- image {index + 1}: {image.mime_type}, {len(image.data) * 3 // 4} bytes"
                    for index, image in enumerate(result.images[1:])
                )
                output = f"{output}\n\n[additional images discarded: {len(result.images) - 1}]\n{extra}"
        return ToolResult(
            output=output,
            is_error=result.is_error,
            metadata=metadata,
        )


def _input_model_from_schema(tool_name: str, schema: dict[str, object]) -> type[BaseModel]:
    """从 JSON schema 创建 Pydantic 输入模型。"""
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return create_model(f"{tool_name.title()}Input")

    fields: dict[str, Any] = {}
    required_raw = schema.get("required", [])
    required: set[str] = set(required_raw) if isinstance(required_raw, list) else set()
    for key in properties:
        default = ... if key in required else None
        fields[key] = (object | None, Field(default=default))
    result: type[BaseModel] = create_model(f"{tool_name.title().replace('-', '_')}Input", **fields)
    return result


def _sanitize_tool_segment(value: str) -> str:
    """清理工具段以形成有效的标识符。"""
    # 移除非字母数字字符
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", value)
    if not sanitized:
        return "tool"
    # 确保以字母开头
    if not sanitized[0].isalpha():
        return f"mcp_{sanitized}"
    return sanitized
