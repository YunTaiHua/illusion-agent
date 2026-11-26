"""技能定义、注册表和发现函数的公共导出。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from illusion.skills.loader import (
        get_project_commands_dir,
        get_project_rules_dir,
        get_project_skills_dir,
        get_user_commands_dir,
        get_user_skills_dir,
        load_project_commands,
        load_project_skills,
        load_skill_registry,
        load_user_commands,
        load_user_skills,
    )
    from illusion.skills.registry import SkillRegistry
    from illusion.skills.types import SkillDefinition

__all__ = [
    "SkillDefinition",
    "SkillRegistry",
    "get_project_commands_dir",
    "get_project_rules_dir",
    "get_project_skills_dir",
    "get_user_commands_dir",
    "get_user_skills_dir",
    "load_project_commands",
    "load_project_skills",
    "load_skill_registry",
    "load_user_commands",
    "load_user_skills",
]


def __getattr__(name: str) -> object:
    if name in {
        "get_user_skills_dir",
        "get_user_commands_dir",
        "get_project_skills_dir",
        "get_project_commands_dir",
        "get_project_rules_dir",
        "load_user_skills",
        "load_user_commands",
        "load_project_skills",
        "load_project_commands",
        "load_skill_registry",
    }:
        from illusion.skills.loader import (
            get_project_commands_dir,
            get_project_rules_dir,
            get_project_skills_dir,
            get_user_commands_dir,
            get_user_skills_dir,
            load_project_commands,
            load_project_skills,
            load_skill_registry,
            load_user_commands,
            load_user_skills,
        )

        return {
            "get_user_skills_dir": get_user_skills_dir,
            "get_user_commands_dir": get_user_commands_dir,
            "get_project_skills_dir": get_project_skills_dir,
            "get_project_commands_dir": get_project_commands_dir,
            "get_project_rules_dir": get_project_rules_dir,
            "load_user_skills": load_user_skills,
            "load_user_commands": load_user_commands,
            "load_project_skills": load_project_skills,
            "load_project_commands": load_project_commands,
            "load_skill_registry": load_skill_registry,
        }[name]
    if name == "SkillRegistry":
        from illusion.skills.registry import SkillRegistry

        return SkillRegistry
    if name == "SkillDefinition":
        from illusion.skills.types import SkillDefinition

        return SkillDefinition
    raise AttributeError(name)
