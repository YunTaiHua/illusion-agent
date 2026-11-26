"""技能/命令分离和资源路径语义测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from illusion.prompts import context as prompt_context
from illusion.skills.registry import SkillRegistry
from illusion.skills.types import SkillDefinition
from illusion.tools.base import ToolExecutionContext
from illusion.tools.skill_tool import SkillTool, SkillToolInput


def _registry_for_invocation(tmp_path: Path) -> SkillRegistry:
    registry = SkillRegistry()
    skill_path = tmp_path / "plugin" / "skills" / "demo" / "SKILL.md"
    skill = SkillDefinition(
        name="demo",
        description="Demo skill",
        content=(
            "root=${CLAUDE_PLUGIN_ROOT}\n"
            "skill=${CLAUDE_SKILL_DIR}\n"
            "args=$ARGUMENTS\n"
        ),
        source="plugin",
        path=str(skill_path),
        skill_root=str(skill_path.parent),
        plugin_root=str(skill_path.parents[2]),
    )
    registry.register(skill)
    registry.register_command(
        SkillDefinition(
            name="demo",
            description="Demo command",
            content="command",
            source="project",
            path=str(tmp_path / "commands" / "demo.md"),
            skill_root=str(tmp_path / "commands"),
            kind="command",
        )
    )
    registry.register(
        SkillDefinition(
            name="hidden",
            description="Hidden skill",
            content="hidden",
            source="project",
            disable_model_invocation=True,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_skill_tool_uses_skill_and_plugin_roots_without_reexpanding_args(
    tmp_path: Path, monkeypatch
):
    registry = _registry_for_invocation(tmp_path)
    monkeypatch.setattr("illusion.tools.skill_tool.load_skill_registry", lambda cwd: registry)

    result = await SkillTool().execute(
        SkillToolInput(name="demo", args="${CLAUDE_PLUGIN_ROOT}"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert result.output == (
        f"root={tmp_path / 'plugin'}\n"
        f"skill={tmp_path / 'plugin' / 'skills' / 'demo'}\n"
        "args=${CLAUDE_PLUGIN_ROOT}\n"
    )


@pytest.mark.asyncio
async def test_skill_tool_rejects_model_disabled_skill(tmp_path: Path, monkeypatch):
    registry = _registry_for_invocation(tmp_path)
    monkeypatch.setattr("illusion.tools.skill_tool.load_skill_registry", lambda cwd: registry)

    result = await SkillTool().execute(
        SkillToolInput(name="hidden"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "cannot be invoked by the model" in result.output


def test_prompt_skill_section_excludes_commands_and_model_disabled_skills(
    tmp_path: Path, monkeypatch
):
    registry = _registry_for_invocation(tmp_path)
    monkeypatch.setattr(prompt_context, "load_skill_registry", lambda cwd: registry)

    section = prompt_context._build_skills_section(tmp_path)

    assert section is not None
    assert '- **demo**: Demo skill' in section
    assert "pre-run check" in section
    assert "ILLUSION_CONFIG_DIR" not in section and "~/.illusion" not in section
    assert str(tmp_path) not in section
    assert "Demo command" not in section
    assert "hidden" not in section
