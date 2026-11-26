"""
Skill 数据模型模块
================

SKILL.md frontmatter 字段数据模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SlashReferences = dict[str, Literal["skill", "command"]]


@dataclass(frozen=True)
class SkillDefinition:
    """已加载的 Skill。"""

    name: str
    description: str
    content: str
    source: str
    path: str | None = None

    allowed_tools: list[str] | None = None
    model: str | None = None
    hooks: dict[str, list[Any]] | None = None
    context: str | None = None  # "inline" | "fork"
    agent: str | None = None
    disable_model_invocation: bool | None = None
    effort: str | None = None
    skill_root: str | None = None
    # 插件安装根目录。插件变量按此路径展开，技能资源变量按 skill_root 展开。
    plugin_root: str | None = None
    # 条目类别："skill"（真技能）| "command"（斜杠命令）。
    kind: str = "skill"

    @property
    def is_command(self) -> bool:
        """是否为斜杠命令。"""
        return self.kind == "command"
