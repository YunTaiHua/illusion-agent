"""
插件清单模式模块
================

定义插件清单的数据模型。
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class _ManifestFields(BaseModel):
    """各版清单共用的字段与校验。"""

    name: str
    version: str = "0.0.0"
    description: str = ""
    skills_dir: str = "skills"
    hooks_file: str = "hooks.json"
    mcp_file: str = "mcp.json"
    author: dict[str, Any] | None = None
    commands: str | list[Any] | dict[str, Any] | None = None
    skills: str | list[Any] | None = None
    hooks: str | dict[str, Any] | list[Any] | None = None
    settings: dict[str, Any] | None = None


class LegacyPluginManifest(_ManifestFields):
    """旧版清单的有效性约定，仅用于兼容已有 denied_plugins 策略。"""

    enabled_by_default: bool = True
    agents: str | list[Any] | None = None
    user_config: dict[str, Any] | None = None


class PluginManifest(_ManifestFields):
    """插件清单。资源路径均相对于插件安装根目录。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    enabled_by_default: bool = Field(
        default=True,
        validation_alias=AliasChoices("enabled_by_default", "enabledByDefault"),
    )
    agents: str | list[Any] | dict[str, Any] | None = None
    mcp_servers: str | list[Any] | dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("mcpServers", "mcp_servers"),
    )
    user_config: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("user_config", "userConfig"),
    )
