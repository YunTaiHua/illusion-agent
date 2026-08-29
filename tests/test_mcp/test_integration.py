"""Tests for MCP config and tool adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from illusion.config.settings import Settings
from illusion.mcp.config import load_mcp_server_configs
from illusion.mcp.types import McpResourceInfo, McpStdioServerConfig, McpToolInfo
from illusion.plugins.schemas import PluginManifest
from illusion.plugins.types import LoadedPlugin
from illusion.tools import create_default_tool_registry
from illusion.tools.base import ToolExecutionContext


@dataclass
class FakeMcpManager:
    tools: list[McpToolInfo]
    resources: list[McpResourceInfo]

    def list_tools(self):
        return self.tools

    def list_resources(self, *, server_name: str | None = None):
        if server_name is None:
            return self.resources
        return [resource for resource in self.resources if resource.server_name == server_name]

    def list_statuses(self):
        names = sorted(
            {resource.server_name for resource in self.resources}
            | {tool.server_name for tool in self.tools}
        )
        return [SimpleNamespace(name=name, state="connected", detail="") for name in names]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict):
        return f"{server_name}:{tool_name}:{arguments['name']}"

    async def call_tool_detail(self, server_name: str, tool_name: str, arguments: dict):
        from illusion.mcp.types import McpToolCallResult

        return McpToolCallResult(text=f"{server_name}:{tool_name}:{arguments['name']}")

    async def read_resource(self, server_name: str, uri: str):
        return f"{server_name}:{uri}"


def test_load_mcp_server_configs_merges_plugins():
    settings = Settings(
        mcp_servers={"local": McpStdioServerConfig(command="python", args=["server.py"])}
    )
    plugin = LoadedPlugin(
        manifest=PluginManifest(name="demo", version="1.0.0"),
        path=Path("/tmp/demo"),
        enabled=True,
        mcp_servers={"remote": McpStdioServerConfig(command="python", args=["remote.py"])},
    )

    servers = load_mcp_server_configs(settings, [plugin])

    assert "local" in servers
    assert "demo:remote" in servers


async def test_mcp_tools_are_registered():
    manager = FakeMcpManager(
        tools=[
            McpToolInfo(
                server_name="demo",
                name="hello",
                description="Say hello",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            )
        ],
        resources=[McpResourceInfo(server_name="demo", name="Readme", uri="demo://readme")],
    )
    registry = create_default_tool_registry(manager)

    tool = registry.get("mcp__demo__hello")
    assert tool is not None
    parsed = tool.input_model.model_validate({"name": "world"})
    result = await tool.execute(parsed, ToolExecutionContext(cwd=Path(".")))
    assert result.output == "demo:hello:world"

    list_tool = registry.get("list_mcp_resources")
    assert list_tool is not None
    list_result = await list_tool.execute(list_tool.input_model(), ToolExecutionContext(cwd=Path(".")))
    assert "demo://readme" in list_result.output
    filtered_result = await list_tool.execute(
        list_tool.input_model(server="demo"),
        ToolExecutionContext(cwd=Path(".")),
    )
    assert "demo://readme" in filtered_result.output
    missing_result = await list_tool.execute(
        list_tool.input_model(server="missing"),
        ToolExecutionContext(cwd=Path(".")),
    )
    assert missing_result.is_error is True
