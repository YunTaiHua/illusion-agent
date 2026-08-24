"""
配置工具
========

本模块提供读取和更新 IllusionAgent 配置设置的功能。

主要组件：
    - ConfigTool: 读取或更新配置的工具，使用示例：
    >>> from illusion.tools import ConfigTool
    >>> tool = ConfigTool()
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from illusion.config.settings import load_settings, save_settings
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ConfigToolInput(BaseModel):
    """配置访问参数。

    属性：
        action: 操作类型，"show" 显示全部配置，"set" 设置指定键
        key: 配置键名（仅 action="set" 时有效，仅支持顶层平坦字段）
        value: 配置值（仅 action="set" 时有效，字符串类型）
    """

    action: str = Field(default="show", description="show or set")
    key: str | None = Field(default=None)
    value: str | None = Field(default=None)


class ConfigTool(BaseTool[ConfigToolInput]):
    """读取或更新 IllusionAgent 配置设置。

    用于查看或更改 IllusionAgent 设置。当用户请求配置更改、询问当前设置时使用此工具。
    """

    name = "config"
    description = """Get or set Illusion Agent configuration settings.

View or change Illusion Agent settings (stored in ~/.illusion/settings.json). Use when the user requests configuration changes or asks about current settings.

## Usage
- **Show all settings:** action="show" — dumps the entire config as JSON
- **Set a value:** action="set" with key and value

Only top-level flat fields can be set. Nested objects (permission, sandbox, memory, hooks, mcp_servers, etc.) must be edited directly in the config file.

## Available flat settings
- model: Active model reference in "env_N.model_N" format (e.g., "env_1.model_1")
- ui_language: UI language (e.g., "zh-CN")
- show_thinking: true/false — Show thinking process
- effort: Effort level ("low", "medium", "high")
- max_tokens: Maximum tokens per response (integer)
- max_turns: Maximum conversation turns (integer)
- context_window: Context window size (integer)
- working_directory: Fixed working directory path or null

## Examples
- Show all settings: { "action": "show" }
- Set UI language: { "action": "set", "key": "ui_language", "value": "zh-CN" }
- Set effort level: { "action": "set", "key": "effort", "value": "high" }"""
    input_model = ConfigToolInput

    async def execute(self, arguments: ConfigToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        # 加载当前设置（涉及文件 I/O，委托给线程池避免阻塞事件循环）
        settings = await asyncio.to_thread(load_settings)
        # 显示当前所有配置
        if arguments.action == "show":
            return ToolResult(output=settings.model_dump_json(indent=2))
        # 设置配置值
        if arguments.action == "set" and arguments.key and arguments.value is not None:
            # 检查配置键是否存在
            if not hasattr(settings, arguments.key):
                return ToolResult(output=f"Unknown config key: {arguments.key}", is_error=True)
            # 更新配置值
            setattr(settings, arguments.key, arguments.value)
            # 保存设置
            await asyncio.to_thread(save_settings, settings)
            return ToolResult(output=f"Updated {arguments.key}")
        return ToolResult(output="Usage: action=show or action=set with key/value", is_error=True)
