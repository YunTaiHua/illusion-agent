"""Tests for MCP config and tool adapters."""

from __future__ import annotations

import json
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


def test_user_and_target_project_mcp_discovery_preserves_workspace_cwd(tmp_path, monkeypatch):
    other_workspace = tmp_path / "other"
    target_workspace = tmp_path / "target"
    other_workspace.mkdir()
    target_workspace.mkdir()
    monkeypatch.chdir(other_workspace)

    config_root = tmp_path / "config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
    (config_root / "mcp.json").parent.mkdir(parents=True, exist_ok=True)
    (config_root / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "user-only": {"command": "user-server"},
                    "shared": {"command": "user-shared"},
                    "disabled": {"command": "disabled", "enabled": False},
                    "broken": {"type": "invalid", "command": "bad"},
                }
            }
        ),
        encoding="utf-8",
    )
    (target_workspace / ".illusion" / "mcp.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    (target_workspace / ".illusion" / "mcp.json").write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "shared": {"command": "project-shared"},
                    "project-only": {"command": "project-server"},
                    "invalid-project": {"type": "invalid"},
                }
            }
        ),
        encoding="utf-8",
    )
    (other_workspace / ".illusion" / "mcp.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    (other_workspace / ".illusion" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"wrong-workspace": {"command": "wrong"}}}),
        encoding="utf-8",
    )

    settings = Settings(
        mcp_servers={"settings-only": McpStdioServerConfig(command="settings-server")}
    )
    servers = load_mcp_server_configs(settings, [], cwd=target_workspace)

    assert set(servers) == {"settings-only", "user-only", "shared", "project-only"}
    assert servers["shared"].command == "project-shared"
    assert "wrong-workspace" not in servers


def test_legacy_mcp_directories_are_not_scanned(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_root = tmp_path / "config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
    (config_root / "mcp").mkdir(parents=True)
    (config_root / "mcp" / "legacy.json").write_text(
        json.dumps({"mcpServers": {"legacy-user": {"command": "legacy"}}}),
        encoding="utf-8",
    )
    (workspace / ".illusion" / "mcp").mkdir(parents=True)
    (workspace / ".illusion" / "mcp" / "legacy.json").write_text(
        json.dumps({"mcpServers": {"legacy-project": {"command": "legacy"}}}),
        encoding="utf-8",
    )

    servers = load_mcp_server_configs(Settings(), [], cwd=workspace)

    assert servers == {}


def test_mcp_file_getters_do_not_create_config_roots(tmp_path, monkeypatch):
    from illusion.config.paths import (
        get_mcp_config_file_path,
        get_project_mcp_config_file_path,
    )

    config_root = tmp_path / "config"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))

    assert get_mcp_config_file_path() == config_root / "mcp.json"
    assert get_project_mcp_config_file_path(workspace) == workspace / ".illusion" / "mcp.json"
    assert not config_root.exists()
    assert not (workspace / ".illusion").exists()


def test_mcp_config_keeps_valid_server_when_one_entry_is_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    config_file = tmp_path / "config" / "mcp.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "good": {"command": "good-server"},
                        "bad": {"type": "unknown", "command": "bad-server"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    from illusion.mcp.config import load_user_mcp_configs

    servers = load_user_mcp_configs()

    assert set(servers) == {"good"}
    assert servers["good"].command == "good-server"


def test_same_user_project_mcp_path_is_read_once(tmp_path, monkeypatch, caplog):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_root = workspace / ".illusion"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
    mcp_file = config_root / "mcp.json"
    mcp_file.parent.mkdir(parents=True)
    mcp_file.write_text("{broken", encoding="utf-8")

    load_mcp_server_configs(Settings(), [], cwd=workspace)

    messages = [record.getMessage() for record in caplog.records]
    assert sum(f"{mcp_file}" in message for message in messages) == 1


def test_disabled_plugin_mcp_is_not_consumed():
    enabled_plugin = LoadedPlugin(
        manifest=PluginManifest(name="enabled", version="1.0.0"),
        path=Path("/tmp/enabled"),
        enabled=True,
        mcp_servers={"server": McpStdioServerConfig(command="enabled-server")},
    )
    disabled_plugin = LoadedPlugin(
        manifest=PluginManifest(name="disabled", version="1.0.0"),
        path=Path("/tmp/disabled"),
        enabled=False,
        mcp_servers={"server": McpStdioServerConfig(command="disabled-server")},
    )

    servers = load_mcp_server_configs(Settings(), [enabled_plugin, disabled_plugin])

    assert "enabled:server" in servers
    assert "disabled:server" not in servers


def test_project_dot_mcp_json_is_supported_when_primary_is_absent(tmp_path, monkeypatch):
    """项目 .illusion/.mcp.json 是 mcp.json 缺失时的同作用域入口。"""
    workspace = tmp_path / "workspace"
    config_root = tmp_path / "config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
    dot_file = workspace / ".illusion" / ".mcp.json"
    dot_file.parent.mkdir(parents=True)
    dot_file.write_text(
        json.dumps({"mcpServers": {"dot-server": {"command": "dot-server"}}}),
        encoding="utf-8",
    )

    servers = load_mcp_server_configs(Settings(), [], cwd=workspace)

    assert set(servers) == {"dot-server"}


def test_project_mcp_json_takes_precedence_over_dot_file(tmp_path):
    """同一项目同时存在两个入口时，mcp.json 优先且不合并旧入口。"""
    workspace = tmp_path / "workspace"
    illusion_dir = workspace / ".illusion"
    illusion_dir.mkdir(parents=True)
    (illusion_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"primary": {"command": "primary"}}}),
        encoding="utf-8",
    )
    (illusion_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"dot": {"command": "dot"}}}),
        encoding="utf-8",
    )

    servers = load_mcp_server_configs(Settings(), [], cwd=workspace)

    assert set(servers) == {"primary"}
