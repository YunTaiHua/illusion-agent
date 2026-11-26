"""
插件运行时类型模块
==================

本模块定义插件运行时使用的类型。

主要组件：
    - LoadedPlugin: 已加载的插件

使用示例：
    >>> from illusion.plugins.types import LoadedPlugin
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from illusion.config.paths import get_config_dir
from illusion.mcp.types import McpServerConfig
from illusion.plugins.schemas import PluginManifest
from illusion.skills.types import SkillDefinition

if TYPE_CHECKING:
    # 仅类型检查期导入：代理系统反向延迟导入本包，运行时引入会成环
    from illusion.coordinator.agent_definitions import AgentDefinition


PluginScope = Literal["user", "project"]


def is_builtin_browser_install(name: str, scope: PluginScope, path: Path) -> bool:
    """按标准用户安装位置识别原生浏览器，与启用状态独立。"""
    if name != "browser-use" or scope != "user":
        return False
    try:
        return path.resolve() == (get_config_dir() / "plugins" / "browser-use").resolve()
    except (OSError, RuntimeError):
        return False


@dataclass(frozen=True)
class LoadedPlugin:
    """已加载的插件及其贡献的内容

    表示一个已加载的插件，包含清单、路径、启用状态、技能、钩子等。

    三类内容按目录分类加载、互不混入：
    - skills: skills/<name>/SKILL.md 技能包
    - commands: commands/*.md 斜杠命令（kind="command"，仍注册进技能注册表）
    - agents: agents/*.md 代理定义（AgentDefinition，供代理系统消费，不是技能）

    Attributes:
        manifest: 插件清单
        path: 插件目录路径
        enabled: 是否启用
        skills: 技能定义列表
        hooks: 钩子字典
        mcp_servers: MCP 服务器配置字典
        commands: 命令技能列表
        agents: 代理定义列表
    """

    manifest: PluginManifest  # 插件清单
    path: Path  # 插件目录路径
    enabled: bool  # 是否启用
    skills: list[SkillDefinition] = field(default_factory=list)  # 技能列表
    hooks: dict[str, list[Any]] = field(default_factory=dict)  # 钩子字典
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)  # MCP 服务器配置
    commands: list[SkillDefinition] = field(default_factory=list)  # 命令列表
    agents: list[AgentDefinition] = field(default_factory=list)  # 代理定义列表
    scope: PluginScope = "user"  # 安装来源（内置插件 seed 到用户目录）

    @property
    def name(self) -> str:
        """获取插件名称"""
        return self.manifest.name

    @property
    def is_builtin_browser(self) -> bool:
        return is_builtin_browser_install(self.name, self.scope, self.path)

    @property
    def description(self) -> str:
        """获取插件描述"""
        return self.manifest.description
