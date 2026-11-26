"""Tests for plugin loading."""

from __future__ import annotations

import json
from pathlib import Path

from illusion.config.settings import Settings
from illusion.hooks.loader import load_hook_registry
from illusion.plugins import load_plugins
from illusion.skills import load_skill_registry


def _write_plugin(root: Path) -> None:
    plugin_dir = root / "example-plugin"
    (plugin_dir / "skills").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "example",
                "version": "1.0.0",
                "description": "Example plugin",
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "skills" / "deploy.md").write_text(
        "# Deploy\nDeploy with care\n",
        encoding="utf-8",
    )
    (plugin_dir / "hooks.json").write_text(
        json.dumps(
            {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "printf start"}]}
                ]
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {"type": "stdio", "command": "python", "args": ["demo.py"]}
                }
            }
        ),
        encoding="utf-8",
    )


def test_load_plugins_from_project_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "repo"
    plugins_root = project / ".illusion" / "plugins"
    plugins_root.mkdir(parents=True)
    _write_plugin(plugins_root)

    plugins = load_plugins(Settings(), project)

    assert len(plugins) == 1
    plugin = plugins[0]
    assert plugin.manifest.name == "example"
    assert plugin.skills[0].name == "example:Deploy"
    assert "SessionStart" in plugin.hooks
    assert "demo" in plugin.mcp_servers


def test_plugin_skills_and_hooks_are_merged(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "repo"
    plugins_root = project / ".illusion" / "plugins"
    plugins_root.mkdir(parents=True)
    _write_plugin(plugins_root)

    skills = load_skill_registry(project).list_skills()
    assert any(skill.name == "example:Deploy" and skill.source == "plugin" for skill in skills)

    plugins = load_plugins(Settings(), project)
    hooks = load_hook_registry(Settings(), plugins)
    assert "SessionStart" in hooks.summary()


def _write_categorized_plugin(root: Path) -> None:
    """写入包含 skills/ + commands/ + agents/ 三类内容的插件目录。"""
    plugin_dir = root / "categorized-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "categorized", "version": "1.0.0", "description": "c"}),
        encoding="utf-8",
    )
    (plugin_dir / "skills" / "alpha" / "SKILL.md").parent.mkdir(parents=True)
    (plugin_dir / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Real skill\n---\n\n# Alpha\n",
        encoding="utf-8",
    )
    (plugin_dir / "commands").mkdir()
    (plugin_dir / "commands" / "do-thing.md").write_text(
        "---\nname: do-thing\ndescription: Slash command\n---\n\n# Do Thing\n",
        encoding="utf-8",
    )
    (plugin_dir / "agents").mkdir()
    (plugin_dir / "agents" / "helper_agent.md").write_text(
        "---\nname: helper_agent\ndescription: Plugin agent\ntools: Read, Grep\n---\n\nYou are a helper.\n",
        encoding="utf-8",
    )


def test_plugin_categories_are_separated(tmp_path: Path, monkeypatch):
    """skills/、commands/、agents/ 三类内容分类加载，互不混入。

    插件命令标记 kind="command"（仍注册进技能注册表）；
    代理定义进 LoadedPlugin.agents（source="plugin"），不进技能表。
    """
    from illusion.coordinator.agent_definitions import AgentDefinition

    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "repo"
    plugins_root = project / ".illusion" / "plugins"
    plugins_root.mkdir(parents=True)
    _write_categorized_plugin(plugins_root)

    plugins = load_plugins(Settings(), project)
    assert len(plugins) == 1
    plugin = plugins[0]

    # 真技能：只有 skills/ 目录内容
    assert [s.name for s in plugin.skills] == ["categorized:alpha"]
    assert plugin.skills[0].kind == "skill"

    # 斜杠命令：commands/ 内容，kind="command"
    assert [c.name for c in plugin.commands] == ["categorized:do-thing"]
    assert plugin.commands[0].kind == "command"
    assert all(not c.is_command for c in plugin.skills)

    # 代理定义：agents/ 内容，AgentDefinition 且 source="plugin"，不在技能表
    assert len(plugin.agents) == 1
    agent = plugin.agents[0]
    assert isinstance(agent, AgentDefinition)
    assert agent.name == "helper_agent"
    assert agent.source == "plugin"
    assert not any(s.name.endswith("helper_agent") for s in plugin.skills)

    # 注册表：技能与命令通过独立接口读取
    registry = load_skill_registry(project)
    registry_skills = registry.list_skills()
    registry_commands = registry.list_commands()
    names = {s.name for s in registry_skills}
    assert "categorized:alpha" in names
    assert "categorized:do-thing" not in names
    assert [command.name for command in registry_commands] == ["categorized:do-thing"]
    assert registry_commands[0].kind == "command"


def test_plugin_agents_reach_agent_system(tmp_path: Path, monkeypatch):
    """插件代理定义可被代理系统加载（此前 LoadedPlugin 无 agents 字段，恒为空）。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "repo"
    plugins_root = project / ".illusion" / "plugins"
    plugins_root.mkdir(parents=True)
    _write_categorized_plugin(plugins_root)

    from illusion.coordinator.agent_definitions import get_all_agent_definitions

    monkeypatch.chdir(project)
    agents = get_all_agent_definitions(cwd=str(project))
    plugin_agents = [a for a in agents if a.source == "plugin"]
    assert [a.name for a in plugin_agents] == ["helper_agent"]
