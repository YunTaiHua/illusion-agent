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
        # computer use 工具：结果末尾追加 skill 提示，告知 LLM 有 skill 指导。
        is_computer = _is_computer_tool(self._tool_info.server_name)
        # 调用 MCP 工具
        try:
            output = await self._manager.call_tool(
                self._tool_info.server_name,
                self._tool_info.name,
                # exclude_unset：只发送 LLM 明确提供的参数，避免把未提供的
                # 可选字段（如 cua-driver 的 target）以 null 发出导致
                # 服务器校验冲突（如 "target cannot be combined with legacy ..."）
                arguments.model_dump(mode="json", exclude_unset=True),
            )
        except MCP_TOOL_EXCEPTIONS as exc:
            return ToolResult(output=str(exc), is_error=True)
        if is_computer:
            hint = computer_use_hint(self._tool_info.server_name)
            if hint and output:
                output = f"{output}\n\n{hint}"
        return ToolResult(output=output)


def _is_computer_tool(server_name: str) -> bool:
    """判断工具是否来自 computer use 服务器。"""
    from illusion.computer.hint import is_computer_use_server

    return is_computer_use_server(server_name)


def computer_use_hint(server_name: str) -> str:
    """返回 computer use 工具结果末尾的 skill 提示（非 computer 服务器返回空串）。"""
    from illusion.computer.hint import computer_use_hint as _hint

    return _hint(server_name)


def is_mcp_tool_exposed(tool_info: McpToolInfo) -> bool:
    """判断 MCP 工具是否应注册进工具注册表。

    computer 服务器按白名单过滤（cua-driver 自带 50+ 工具，仅暴露核心集，
    避免撑爆 LLM 上下文）；其他服务器全部暴露。

    Args:
        tool_info: MCP 工具信息

    Returns:
        bool: 是否暴露
    """
    from illusion.computer.hint import is_computer_tool_exposed

    return is_computer_tool_exposed(tool_info.server_name, tool_info.name)


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
