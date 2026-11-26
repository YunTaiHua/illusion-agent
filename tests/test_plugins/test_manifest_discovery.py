"""插件清单发现、资源路径和安装边界测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from illusion.config.settings import Settings
from illusion.plugins.installer import install_plugin_from_path, uninstall_plugin
from illusion.plugins.loader import _find_manifest, load_plugin, load_plugins


def _write_manifest(plugin_root: Path, data: dict, location: str = "plugin.json") -> None:
    manifest = plugin_root / location
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(data), encoding="utf-8")


def _write_skill(root: Path, name: str, text: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        text or f"---\nname: {name}\ndescription: {name} skill\n---\n\n{name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_manifest_priority_and_first_invalid_manifest_wins(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    _write_manifest(plugin, {"name": "legacy"}, "plugin.json")
    _write_manifest(plugin, {"name": "zcode"}, ".zcode-plugin/plugin.json")
    _write_manifest(plugin, {"name": "codex"}, ".codex-plugin/plugin.json")
    _write_manifest(plugin, {"name": "claude"}, ".claude-plugin/plugin.json")
    _write_manifest(plugin, {"name": "native"}, ".illusion-plugin/plugin.json")

    assert _find_manifest(plugin) == plugin / ".illusion-plugin" / "plugin.json"
    loaded = load_plugin(plugin, {})
    assert loaded is not None
    assert loaded.name == "native"

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / ".illusion-plugin").mkdir()
    (invalid / ".illusion-plugin" / "plugin.json").write_text("{", encoding="utf-8")
    _write_manifest(invalid, {"name": "fallback"}, ".claude-plugin/plugin.json")

    assert _find_manifest(invalid) == invalid / ".illusion-plugin" / "plugin.json"
    assert load_plugin(invalid, {}) is None


def test_discovery_skips_hidden_plugin_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "project"
    plugins_root = project / ".illusion" / "plugins"
    (plugins_root / ".hidden-plugin").mkdir(parents=True)
    _write_manifest(plugins_root / ".hidden-plugin", {"name": "hidden"})
    (plugins_root / "visible-plugin").mkdir()
    _write_manifest(plugins_root / "visible-plugin", {"name": "visible"})

    plugins = load_plugins(Settings(), project)
    assert [plugin.name for plugin in plugins] == ["visible"]


def test_default_component_locations_and_skill_roots(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    _write_manifest(plugin, {"name": "defaults"}, ".illusion-plugin/plugin.json")
    skill_dir = _write_skill(plugin / "skills", "default-skill")
    (plugin / "commands").mkdir()
    (plugin / "commands" / "run.md").write_text(
        "---\nname: run\ndescription: run command\n---\nRun it.\n",
        encoding="utf-8",
    )
    (plugin / "agents").mkdir()
    (plugin / "agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: helper\nhooks:\n  SessionStart: []\n---\nPrompt\n",
        encoding="utf-8",
    )
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "${CLAUDE_PLUGIN_ROOT}/start.sh",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (plugin / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"demo": {"command": "python", "args": []}}}),
        encoding="utf-8",
    )

    loaded = load_plugin(plugin, {})
    assert loaded is not None
    assert [skill.name for skill in loaded.skills] == ["defaults:default-skill"]
    assert loaded.skills[0].skill_root == str(skill_dir)
    plugin_root = getattr(loaded.skills[0], "plugin_root", None)
    if plugin_root is not None:
        assert plugin_root == str(plugin.resolve())
    assert [command.name for command in loaded.commands] == ["defaults:run"]
    assert loaded.commands[0].skill_root == str((plugin / "commands").resolve())
    command_plugin_root = getattr(loaded.commands[0], "plugin_root", None)
    if command_plugin_root is not None:
        assert command_plugin_root == str(plugin.resolve())
    assert loaded.agents[0].source == "plugin"
    assert loaded.agents[0].hooks is None
    assert loaded.hooks["SessionStart"][0]["hooks"][0]["command"].startswith(
        str(plugin.resolve()).replace("\\", "/")
    )
    assert "demo" in loaded.mcp_servers


def test_explicit_resource_paths_mappings_and_local_failures(tmp_path: Path, caplog) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    _write_manifest(
        plugin,
        {
            "name": "explicit",
            "skills": "custom-skills",
            "commands": ["custom-commands", "custom-commands"],
            "agents": "custom-agents",
            "hooks": ["custom-hooks.json", "custom-hooks.json", {"SessionEnd": []}],
            "mcpServers": [
                "custom-mcp.json",
                "custom-mcp.json",
                {"inline": {"command": "python"}},
            ],
        },
        ".claude-plugin/plugin.json",
    )
    _write_skill(plugin / "skills", "ignored-default")
    custom_skill = _write_skill(plugin / "custom-skills", "custom")
    (plugin / "custom-commands").mkdir()
    (plugin / "custom-commands" / "custom.md").write_text(
        "---\nname: custom\ndescription: custom command\n---\nDo custom work.\n",
        encoding="utf-8",
    )
    (plugin / "custom-agents").mkdir()
    (plugin / "custom-agents" / "custom-agent.md").write_text(
        "---\nname: custom-agent\ndescription: custom\n---\nPrompt\n", encoding="utf-8"
    )
    (plugin / "custom-hooks.json").write_text(
        json.dumps({"SessionEnd": []}), encoding="utf-8"
    )
    (plugin / "custom-mcp.json").write_text(
        json.dumps({"mcpServers": {"custom": {"command": "python"}}}), encoding="utf-8"
    )
    (plugin / "bad-mcp.json").write_text("[", encoding="utf-8")

    loaded = load_plugin(plugin, {})
    assert loaded is not None
    assert [skill.name for skill in loaded.skills] == ["explicit:custom"]
    assert loaded.skills[0].skill_root == str(custom_skill)
    assert [command.name for command in loaded.commands] == ["explicit:custom"]
    assert loaded.agents[0].name == "custom-agent"
    assert "SessionEnd" in loaded.hooks
    assert "custom" in loaded.mcp_servers
    assert "inline" in loaded.mcp_servers

    # 所有资源路径均受安装根边界约束，越界声明不会读取工作目录外文件。
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\n", encoding="utf-8")
    _write_manifest(
        plugin,
        {"name": "boundary", "skills": ["../outside.md"]},
        ".illusion-plugin/plugin.json",
    )
    boundary = load_plugin(plugin, {})
    assert boundary is not None
    assert boundary.skills == []
    assert "越出安装根" in caplog.text


def test_install_uses_manifest_priority_and_rejects_unvalidated_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    source = tmp_path / "source" / "folder-name"
    source.mkdir(parents=True)
    _write_manifest(source, {"name": "zcode-source"}, ".zcode-plugin/plugin.json")
    _write_manifest(source, {"name": "native-source"}, ".illusion-plugin/plugin.json")

    installed = install_plugin_from_path(source)
    assert installed.name == "folder-name"
    assert load_plugin(installed, {}).name == "native-source"

    invalid = tmp_path / "invalid-source"
    invalid.mkdir()
    with pytest.raises(ValueError, match="manifest"):
        install_plugin_from_path(invalid)

    assert uninstall_plugin("../folder-name") is False
    assert uninstall_plugin("folder-name") is True


def test_command_manifest_mapping_supports_source_and_inline_content(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    _write_manifest(
        plugin,
        {
            "name": "mapped",
            "commands": {
                "mapped-run": {
                    "source": "extra/run.md",
                    "description": "Mapped description",
                },
                "inline-run": {
                    "content": "# Inline\nInline body.\n",
                    "description": "Inline description",
                },
            },
        },
        ".zcode-plugin/plugin.json",
    )
    (plugin / "extra").mkdir()
    (plugin / "extra" / "run.md").write_text("# Source\nSource body.\n", encoding="utf-8")

    loaded = load_plugin(plugin, {})
    assert loaded is not None
    commands = {command.name: command for command in loaded.commands}
    assert commands["mapped:mapped-run"].description == "Mapped description"
    assert commands["mapped:inline-run"].description == "Inline description"
    assert commands["mapped:mapped-run"].skill_root == str((plugin / "extra").resolve())
