"""插件来源选择、硬拒绝与开关持久化的回归。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from illusion.config.paths import get_config_file_path
from illusion.config.settings import Settings, load_settings, save_settings
from illusion.plugins.loader import (
    get_project_plugins_dir,
    get_user_plugins_dir,
    load_plugins,
    native_browser_enabled,
    set_plugin_enabled,
)


def _install(root: Path, dirname: str, name: str, *, default: bool = True) -> Path:
    path = root / dirname
    manifest = path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": name, "enabledByDefault": default}), encoding="utf-8")
    return path


def test_project_switch_isolated_by_workspace_and_preserves_document(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for cwd in (a, b):
        _install(get_project_plugins_dir(cwd), "directory-name", "shared", default=False)
    save_settings(Settings(enabled_plugins={"shared": True, "global-only": False}))
    before_settings = get_config_file_path().read_bytes()
    path = a / ".illusion" / "permissions.json"
    before = {
        "denied_plugins": ["other"], "denied_rules": ["rule"], "denied_memory": True,
        "denied_skills": ["skill"], "denied_hooks": ["Stop"], "denied_mcp_servers": ["mcp"],
        "future": {"nested": [1, 2]}, "enabled_plugins": {"other": False},
    }
    path.write_text(json.dumps(before), encoding="utf-8")

    for cwd in (a, b):
        (plugin,) = load_plugins(load_settings(), cwd)
        assert (plugin.name, plugin.scope, plugin.enabled) == ("shared", "project", False)

    for enabled in (True, False, True):
        assert set_plugin_enabled("shared", enabled, a) == "project"
        (project_a,) = load_plugins(load_settings(), a)
        (project_b,) = load_plugins(load_settings(), b)
        assert project_a.enabled is enabled and project_b.enabled is False
        assert json.loads(path.read_text(encoding="utf-8")) == {
            **before, "enabled_plugins": {"other": False, "shared": enabled},
        }
    assert get_config_file_path().read_bytes() == before_settings
    assert not (b / ".illusion" / "permissions.json").exists()


def test_shadowing_uses_the_selected_install_even_when_disabled(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    user_path = _install(get_user_plugins_dir(), "user-copy", "demo")
    project_path = _install(get_project_plugins_dir(a), "project-copy", "demo", default=False)
    b.mkdir()
    save_settings(Settings(enabled_plugins={"demo": True}))
    settings_path = get_config_file_path()
    before = settings_path.read_bytes()
    (local,) = load_plugins(load_settings(), a)
    (user,) = load_plugins(load_settings(), b)
    assert (local.path, local.scope, local.enabled) == (project_path, "project", False)
    assert (user.path, user.scope, user.enabled) == (user_path, "user", True)

    with pytest.raises(ValueError, match="scope 'project'"):
        set_plugin_enabled("demo", False, a, scope="user")
    assert settings_path.read_bytes() == before
    assert not (a / ".illusion" / "permissions.json").exists()
    assert set_plugin_enabled("demo", True, a, scope="project") == "project"
    assert set_plugin_enabled("demo", False, b) == "user"
    assert load_settings().enabled_plugins == {"demo": False}
    assert not (b / ".illusion").exists()
    assert load_plugins(load_settings(), a)[0].enabled
    assert not load_plugins(load_settings(), b)[0].enabled

    # 已打开的项目列表项在安装移除后必须拒绝，而不能写入同名用户副本。
    (project_path / ".claude-plugin" / "plugin.json").unlink()
    with pytest.raises(ValueError, match="scope 'user'"):
        set_plugin_enabled("demo", True, a, scope="project")
    assert load_settings().enabled_plugins == {"demo": False}


def test_unknown_names_and_unrelated_paths_cannot_create_settings(tmp_path: Path) -> None:
    outside = _install(tmp_path, "outside", "not-installed")
    a = tmp_path / "a"
    for name in ("missing", str(outside), "not-installed", "../outside"):
        with pytest.raises(ValueError, match="not found"):
            set_plugin_enabled(name, True, a)
    assert load_plugins(Settings(), a) == []
    assert not get_user_plugins_dir().parent.exists()
    assert not (a / ".illusion").exists()


@pytest.mark.parametrize("deny", [["global", "local"], ["*"]])
def test_policy_wins_over_both_scopes_and_rejects_both_switches(tmp_path: Path, deny: list[str]) -> None:
    _install(get_user_plugins_dir(), "global", "global")
    _install(get_project_plugins_dir(tmp_path), "local", "local")
    save_settings(Settings(enabled_plugins={"global": True}))
    path = tmp_path / ".illusion" / "permissions.json"
    path.write_text(json.dumps({"denied_plugins": deny, "enabled_plugins": {"local": True}}), encoding="utf-8")
    before_policy, before_settings = path.read_bytes(), get_config_file_path().read_bytes()
    assert load_plugins(load_settings(), tmp_path) == []
    for name in ("global", "local"):
        for enabled in (True, False):
            with pytest.raises(ValueError, match="denied by project policy"):
                set_plugin_enabled(name, enabled, tmp_path)
    assert path.read_bytes() == before_policy
    assert get_config_file_path().read_bytes() == before_settings


def test_coincident_roots_stay_user_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / ".illusion"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(root))
    _install(get_user_plugins_dir(), "coincident", "demo", default=False)
    path = root / "permissions.json"
    path.write_text('{"enabled_plugins":{"demo":true}}', encoding="utf-8")
    before = path.read_bytes()
    (plugin,) = load_plugins(Settings(), tmp_path)
    assert (plugin.scope, plugin.enabled) == ("user", False)
    assert set_plugin_enabled("demo", True, tmp_path) == "user"
    assert load_settings().enabled_plugins["demo"]
    assert path.read_bytes() == before


def test_seeded_browser_precedes_all_namesakes(tmp_path: Path) -> None:
    from illusion.browser.seeding import seed_builtin_browser_plugin

    _install(get_user_plugins_dir(), "000-first", "browser-use")
    _install(get_project_plugins_dir(tmp_path), "project", "browser-use")
    (project_browser,) = load_plugins(Settings(), tmp_path)
    assert (project_browser.scope, project_browser.enabled) == ("project", True)
    assert project_browser.is_builtin_browser is False
    assert not native_browser_enabled([project_browser])
    assert not native_browser_enabled(load_plugins(Settings(), tmp_path / "other"))

    target = seed_builtin_browser_plugin()
    (seeded,) = load_plugins(Settings(), tmp_path)
    assert (seeded.path, seeded.scope, seeded.enabled) == (target, "user", False)
    assert seeded.is_builtin_browser is True
    assert set_plugin_enabled("browser-use", True, tmp_path) == "user"
    assert native_browser_enabled(load_plugins(load_settings(), tmp_path))
    assert not (tmp_path / ".illusion" / "permissions.json").exists()


@pytest.mark.parametrize("scope", ["user", "project"])
@pytest.mark.parametrize("legacy_path", ["plugin.json", ".claude-plugin/plugin.json"])
def test_old_effective_manifest_identity_preserves_deny(tmp_path: Path, scope: str, legacy_path: str) -> None:
    root = get_user_plugins_dir() if scope == "user" else get_project_plugins_dir(tmp_path)
    install = root / "different-folder"
    primary = install / ".illusion-plugin" / "plugin.json"
    primary.parent.mkdir(parents=True)
    primary.write_text('{"name":"modern"}', encoding="utf-8")
    old = install / legacy_path
    old.parent.mkdir(exist_ok=True)
    old.write_text('{"name":"legacy"}', encoding="utf-8")
    (loaded,) = load_plugins(Settings(), tmp_path)
    assert loaded.name == "modern" and loaded.scope == scope
    policy = tmp_path / ".illusion" / "permissions.json"
    policy.parent.mkdir(exist_ok=True)
    deny = '{"denied_plugins":["legacy"]}'
    policy.write_text(deny, encoding="utf-8")
    assert load_plugins(Settings(), tmp_path) == []
    with pytest.raises(ValueError, match="denied by project policy.*legacy"):
        set_plugin_enabled("modern", True, tmp_path)
    assert policy.read_text(encoding="utf-8") == deny
    assert not get_config_file_path().exists()

    # 无效旧清单（或从未被旧版选中的同名候选）不改变新版优先级。
    old.write_text('{"name":"legacy","agents":{}}', encoding="utf-8")
    if legacy_path == "plugin.json":
        fallback = install / ".claude-plugin" / "plugin.json"
        fallback.parent.mkdir()
        fallback.write_text('{"name":"legacy"}', encoding="utf-8")
    assert [p.name for p in load_plugins(Settings(), tmp_path)] == ["modern"]
    assert set_plugin_enabled("modern", False, tmp_path) == scope
