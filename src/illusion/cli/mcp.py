"""
MCP 服务器管理子命令
====================

提供 MCP 服务器的列表、添加和删除功能。

子命令:
    - list: 列出所有 MCP 服务器配置
    - add: 添加新的 MCP 服务器配置
    - remove: 删除指定的 MCP 服务器配置
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from illusion.cli import mcp_app
from illusion.config.i18n import t as _t


@mcp_app.command("list")
def mcp_list() -> None:
    """列出已配置的 MCP 服务器

    加载当前设置和插件，列出所有已配置的 MCP 服务器及其传输类型。
    """
    from illusion.config import load_settings
    from illusion.mcp.config import load_mcp_server_configs
    from illusion.plugins.loader import load_plugins

    settings = load_settings()
    cwd = str(Path.cwd())
    plugins = load_plugins(settings, cwd)
    configs = load_mcp_server_configs(settings, plugins, cwd)
    if not configs:
        print(_t("mcp_none"))
        return
    for name, cfg in configs.items():
        if hasattr(cfg, "type"):
            transport = getattr(cfg, "type", "unknown")
            if transport == "stdio":
                cmd = getattr(cfg, "command", "")
                detail = f" ({cmd})" if cmd else ""
            elif transport in ("http", "ws"):
                url = getattr(cfg, "url", "")
                detail = f" ({url})" if url else ""
            else:
                detail = ""
        else:
            transport = "unknown"
            detail = ""
        print(f"  {name}: {transport}{detail}")


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="Server name"),
    config_json: str = typer.Argument(..., help="Server config as JSON string"),
) -> None:
    """添加 MCP 服务器配置

    Args:
        name: 服务器名称
        config_json: 服务器配置的 JSON 字符串
    """
    from pydantic import TypeAdapter, ValidationError

    from illusion.config import load_settings, save_settings
    from illusion.mcp.types import McpServerConfig, _normalize_server_config_type

    settings = load_settings()
    try:
        raw = json.loads(config_json)
    except json.JSONDecodeError as exc:
        print(_t("mcp_invalid_json", exc=exc), file=sys.stderr)
        raise typer.Exit(1)
    try:
        cfg: McpServerConfig = TypeAdapter(McpServerConfig).validate_python(
            _normalize_server_config_type(raw)
        )
    except ValidationError as exc:
        print(_t("mcp_invalid_config", exc=exc), file=sys.stderr)
        raise typer.Exit(1)
    if not isinstance(settings.mcp_servers, dict):
        settings.mcp_servers = {}
    settings.mcp_servers[name] = cfg
    save_settings(settings)
    print(_t("mcp_added", name=name))


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="Server name to remove"),
) -> None:
    """移除 MCP 服务器配置

    Args:
        name: 要移除的服务器名称
    """
    from illusion.config import load_settings, save_settings

    settings = load_settings()
    if not isinstance(settings.mcp_servers, dict) or name not in settings.mcp_servers:
        print(_t("mcp_not_found", name=name), file=sys.stderr)
        raise typer.Exit(1)
    del settings.mcp_servers[name]
    save_settings(settings)
    print(_t("mcp_removed", name=name))
