"""命令面隔离测试。"""

from types import SimpleNamespace

import pytest

from illusion.skills.registry import SkillRegistry
from illusion.skills.types import SkillDefinition
from illusion.ui.command_catalog import (
    slash_resource_entries,
    terminal_command_catalog,
    web_builtin_names,
)


def _bundle(*names: str, cwd: str = "workspace") -> SimpleNamespace:
    commands = SimpleNamespace(
        list_commands=lambda: [SimpleNamespace(name=name) for name in names]
    )
    return SimpleNamespace(commands=commands, cwd=cwd)


def test_web_builtin_names_are_whitelist_intersection() -> None:
    bundle = _bundle("exit", "compact", "goal", "model", "export")

    assert web_builtin_names(bundle) == ["/compact", "/export", "/goal"]
    assert "/exit" not in web_builtin_names(bundle)
    assert "/model" not in web_builtin_names(bundle)


def test_web_dynamic_names_cannot_shadow_builtin_commands() -> None:
    bundle = _bundle("exit", "compact", "goal")
    entries = [
        {"name": "exit", "kind": "skill", "description": "shadow"},
        {"name": "compact", "kind": "command", "description": "shadow"},
        {"name": "project:review", "kind": "command", "description": "review"},
    ]

    candidates = slash_resource_entries(bundle, entries)

    assert [entry["name"] for entry in candidates] == ["/project:review"]


def test_terminal_names_keep_exit_and_append_workspace_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle("help", "exit", "compact", cwd="workspace-a")
    entries = [
        {"name": "/workspace-a:review", "kind": "command"},
        {"name": "/exit", "kind": "skill"},
    ]
    monkeypatch.setattr(
        "illusion.ui.command_catalog.dynamic_command_entries",
        lambda cwd: entries,
    )

    names, resources = terminal_command_catalog(bundle)
    assert [resource["name"] for resource in resources] == ["/workspace-a:review"]

    assert names[:3] == ["/help", "/exit", "/compact"]
    assert "/workspace-a:review" in names
    assert names.count("/exit") == 1


def test_catalog_preserves_resource_kind_source_and_safe_aliases(monkeypatch):
    bundle = _bundle("exit", "help")
    raw = [
        {"name": "/pkg:clean", "kind": "skill", "source": "plugin"},
        {"name": "/pkg:clean", "kind": "command", "source": "plugin", "description": "Run clean"},
        {"name": "/pkg:help", "kind": "command", "source": "plugin"},
        {"name": "/pkg:review", "kind": "skill", "source": "plugin"},
        {"name": "/one:deploy", "kind": "command", "source": "project"},
        {"name": "/two:deploy", "kind": "command", "source": "project"},
        {"name": "/note", "kind": "skill", "source": "user"},
        {"name": "/pkg:note", "kind": "command", "source": "plugin"},
    ]
    monkeypatch.setattr("illusion.ui.command_catalog.dynamic_command_entries", lambda cwd: raw)
    names, resources = terminal_command_catalog(bundle)
    assert resources == slash_resource_entries(bundle, raw)
    assert names == ["/exit", "/help", *(item["name"] for item in resources)]
    assert {item["name"] for item in resources if item["accepts_short_name"]} == {"/pkg:review"}
    assert resources[0] == {
        "name": "/pkg:clean", "kind": "command", "source": "plugin",
        "description": "Run clean", "accepts_short_name": False,
    }


def test_hosts_publish_separate_command_surfaces() -> None:
    from illusion.ui.backend_host import ReactBackendHost
    from illusion.ui.web.ws_host import WebBackendHost

    bundle = _bundle("help", "exit", "compact", "goal")
    terminal = ReactBackendHost.__new__(ReactBackendHost)
    terminal._bundle = bundle
    web = WebBackendHost.__new__(WebBackendHost)
    web._bundle = bundle

    names, _ = terminal._slash_command_catalog()
    assert "/exit" in names
    assert "/exit" not in web._slash_command_names()
    assert web._slash_command_names() == ["/compact", "/goal"]


def test_resource_dynamic_candidates_follow_workspace_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from illusion.ui.web.ws_web_api import _collect_resources

    def registry_for(cwd: str) -> SkillRegistry:
        registry = SkillRegistry()
        name = "workspace-a:review" if cwd.endswith("a") else "workspace-b:review"
        registry.register(SkillDefinition(name=name, description="review", content="", source="project", kind="command"))
        registry.register(SkillDefinition(name="exit", description="shadow", content="", source="project"))
        return registry

    monkeypatch.setattr("illusion.skills.loader.load_skill_registry", registry_for)
    monkeypatch.setattr(
        "illusion.coordinator.agent_definitions.get_all_agent_definitions",
        lambda cwd: [],
    )
    monkeypatch.setattr(
        "illusion.permissions.loader.load_project_permissions",
        lambda cwd: SimpleNamespace(denied_rules=[], denied_skills=[], denied_plugins=[]),
    )

    def make_bundle(cwd: str) -> SimpleNamespace:
        return SimpleNamespace(
            cwd=cwd,
            commands=_bundle("exit", "compact").commands,
            current_plugins=list,
            mcp_manager=SimpleNamespace(list_statuses=list),
        )

    first = _collect_resources(make_bundle(str(tmp_path / "a")))
    second = _collect_resources(make_bundle(str(tmp_path / "b")))

    assert [entry["name"] for entry in first["slash_commands"]] == ["/workspace-a:review"]
    assert [entry["name"] for entry in second["slash_commands"]] == ["/workspace-b:review"]
    assert all(entry["name"] != "/exit" for entry in first["slash_commands"])
