"""
项目级权限配置数据类
==================

本模块定义项目级权限配置的数据结构。

主要组件：
    - ProjectPermissions: 项目级权限配置

使用示例：
    >>> from illusion.permissions.schemas import ProjectPermissions
    >>> perms = ProjectPermissions(denied_skills=["skill-a"])
"""

from __future__ import annotations

from pydantic import BaseModel, Field, StrictBool


class ProjectPermissions(BaseModel):
    """项目级权限配置

    用于控制项目级的禁用开关，配置文件位于
    `<project>/.illusion/permissions.json`。

    Attributes:
        denied_skills: 禁用的 skill 名称列表，["*"] 表示全部禁用
        denied_hooks: 禁用的 hook 事件列表，["*"] 表示全部禁用
        denied_plugins: 禁用的插件名称列表，["*"] 表示全部禁用
        enabled_plugins: 仅适用于当前项目安装的插件，不能覆盖 denied_plugins
        denied_mcp_servers: 禁用的 MCP 服务器名称列表，["*"] 表示全部禁用
        denied_memory: 是否禁用 memory 功能
        denied_rules: 禁用的规则名称列表，["*"] 表示全部禁用
    """

    denied_skills: list[str] = Field(default_factory=list)  # 禁用的 skill 名称列表
    denied_hooks: list[str] = Field(default_factory=list)  # 禁用的 hook 事件列表
    denied_plugins: list[str] = Field(default_factory=list)  # 禁用的插件名称列表
    enabled_plugins: dict[str, StrictBool] = Field(default_factory=dict)
    denied_mcp_servers: list[str] = Field(default_factory=list)  # 禁用的 MCP 服务器名称列表
    denied_memory: bool = False  # 是否禁用 memory 功能
    denied_rules: list[str] = Field(default_factory=list)  # 禁用的规则名称列表

    def is_plugin_denied(self, name: str, *, legacy_name: str | None = None) -> bool:
        return "*" in self.denied_plugins or name in self.denied_plugins or (
            legacy_name is not None and legacy_name in self.denied_plugins
        )

    def require_plugin_allowed(self, name: str, *, legacy_name: str | None = None) -> None:
        if self.is_plugin_denied(name, legacy_name=legacy_name):
            detail = f", legacy name '{legacy_name}'" if legacy_name in self.denied_plugins else ""
            raise ValueError(
                f"Plugin '{name}' is denied by project policy (permissions.json: denied_plugins{detail})."
            )
