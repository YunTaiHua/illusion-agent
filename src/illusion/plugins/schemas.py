"""
插件清单模式模块
================

定义插件清单的数据模型。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PluginManifest(BaseModel):
    """插件清单。"""

    name: str
    version: str = "0.0.0"
    description: str = ""
    enabled_by_default: bool = True
    skills_dir: str = "skills"
    hooks_file: str = "hooks.json"
    mcp_file: str = "mcp.json"
    author: dict[str, Any] | None = None
    commands: str | list[Any] | dict[str, Any] | None = None
    agents: str | list[Any] | None = None
    skills: str | list[Any] | None = None
    hooks: str | dict[str, Any] | list[Any] | None = None
    settings: dict[str, Any] | None = None
    user_config: dict[str, Any] | None = None
