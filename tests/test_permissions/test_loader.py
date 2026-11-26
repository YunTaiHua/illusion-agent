"""
项目级权限加载测试
================

测试 load_project_permissions 函数的功能。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from illusion.config.paths import get_config_file_path
from illusion.config.settings import Settings
from illusion.permissions.loader import load_project_permissions
from illusion.plugins.loader import (
    get_project_plugins_dir,
    get_user_plugins_dir,
    load_plugins,
    set_plugin_enabled,
)


class TestLoadProjectPermissions:
    """load_project_permissions 函数测试"""

    def test_no_permissions_file(self, tmp_path: Path) -> None:
        """测试没有 permissions.json 文件时返回默认值"""
        perms = load_project_permissions(tmp_path)
        assert perms.denied_skills == []
        assert perms.denied_hooks == []
        assert perms.denied_plugins == []
        assert perms.denied_mcp_servers == []
        assert perms.denied_memory is False
        assert perms.denied_rules == []

    def test_empty_permissions_file(self, tmp_path: Path) -> None:
        """测试空 permissions.json 文件返回默认值"""
        config_dir = tmp_path / ".illusion"
        config_dir.mkdir()
        permissions_file = config_dir / "permissions.json"
        permissions_file.write_text("{}", encoding="utf-8")

        perms = load_project_permissions(tmp_path)
        assert perms.denied_skills == []
        assert perms.denied_hooks == []
        assert perms.denied_plugins == []
        assert perms.denied_mcp_servers == []
        assert perms.denied_memory is False
        assert perms.denied_rules == []

    def test_full_permissions_file(self, tmp_path: Path) -> None:
        """测试完整的 permissions.json 文件"""
        config_dir = tmp_path / ".illusion"
        config_dir.mkdir()
        permissions_file = config_dir / "permissions.json"
        permissions_file.write_text(
            json.dumps({
                "denied_skills": ["skill-a", "skill-b"],
                "denied_hooks": ["PreToolUse"],
                "denied_plugins": ["plugin-a"],
                "denied_mcp_servers": ["server-a"],
                "denied_memory": True,
                "denied_rules": ["rule-a", "rule-b"],
            }),
            encoding="utf-8",
        )

        perms = load_project_permissions(tmp_path)
        assert perms.denied_skills == ["skill-a", "skill-b"]
        assert perms.denied_hooks == ["PreToolUse"]
        assert perms.denied_plugins == ["plugin-a"]
        assert perms.denied_mcp_servers == ["server-a"]
        assert perms.denied_memory is True
        assert perms.denied_rules == ["rule-a", "rule-b"]

    def test_partial_permissions_file(self, tmp_path: Path) -> None:
        """测试部分字段的 permissions.json 文件"""
        config_dir = tmp_path / ".illusion"
        config_dir.mkdir()
        permissions_file = config_dir / "permissions.json"
        permissions_file.write_text(
            json.dumps({
                "denied_skills": ["skill-a"],
                "denied_memory": True,
            }),
            encoding="utf-8",
        )

        perms = load_project_permissions(tmp_path)
        assert perms.denied_skills == ["skill-a"]
        assert perms.denied_hooks == []
        assert perms.denied_plugins == []
        assert perms.denied_mcp_servers == []
        assert perms.denied_memory is True
        assert perms.denied_rules == []

    def test_invalid_json_file(self, tmp_path: Path) -> None:
        """测试无效 JSON 文件返回默认值"""
        config_dir = tmp_path / ".illusion"
        config_dir.mkdir()
        permissions_file = config_dir / "permissions.json"
        permissions_file.write_text("invalid json", encoding="utf-8")

        perms = load_project_permissions(tmp_path)
        assert perms.denied_skills == []
        assert perms.denied_hooks == []
        assert perms.denied_plugins == []
        assert perms.denied_mcp_servers == []
        assert perms.denied_memory is False
        assert perms.denied_rules == []

    def test_wildcard_denied_skills(self, tmp_path: Path) -> None:
        """测试通配符禁用所有 skills"""
        config_dir = tmp_path / ".illusion"
        config_dir.mkdir()
        permissions_file = config_dir / "permissions.json"
        permissions_file.write_text(
            json.dumps({
                "denied_skills": ["*"],
            }),
            encoding="utf-8",
        )

        perms = load_project_permissions(tmp_path)
        assert perms.denied_skills == ["*"]


@pytest.mark.parametrize("content", [b"{", b"[]", b"\xff", b'{"enabled_plugins":[]}', b'{"enabled_plugins":{"demo":"true"}}'])
def test_invalid_policy_is_never_overwritten_or_bypassed(tmp_path: Path, content: bytes) -> None:
    root = get_project_plugins_dir(tmp_path)
    for plugin in (root / "local", get_user_plugins_dir() / "global"):
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text(json.dumps({"name": plugin.name}), encoding="utf-8")
    path = tmp_path / ".illusion" / "permissions.json"
    path.write_bytes(content)

    assert load_plugins(Settings(), tmp_path) == []
    for name in ("local", "global"):
        with pytest.raises(ValueError, match="Cannot read project permissions"):
            set_plugin_enabled(name, True, tmp_path)
    assert path.read_bytes() == content
    assert not get_config_file_path().exists()


@pytest.mark.parametrize("existing", [True, False])
def test_project_config_link_to_another_workspace_cannot_write(tmp_path: Path, existing: bool) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    plugin = get_project_plugins_dir(b) / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    a.mkdir()
    _link_or_skip(a / ".illusion", b / ".illusion", directory=True)
    other_policy = b / ".illusion" / "permissions.json"
    original = b'{"denied_rules":["keep"]}'
    if existing:
        other_policy.write_bytes(original)
    with pytest.raises(ValueError, match="outside workspace"):
        set_plugin_enabled("demo", True, a)
    assert (a / ".illusion").is_symlink()
    assert other_policy.read_bytes() == original if existing else not other_policy.exists()
    assert not (b / ".illusion" / ".permissions.json.lock").exists()


def _link_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


@pytest.mark.parametrize("existing", [True, False])
def test_permission_file_link_is_preserved(tmp_path: Path, existing: bool) -> None:
    plugin = get_project_plugins_dir(tmp_path) / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    policy = tmp_path / ".illusion" / "permissions.json"
    target = tmp_path / "shared-policy.json"
    if existing:
        target.write_bytes(b'{"denied_rules":["keep"]}')
    _link_or_skip(policy, target)
    with pytest.raises(ValueError, match="linked project permissions"):
        set_plugin_enabled("demo", False, tmp_path)
    assert policy.is_symlink()
    assert target.read_bytes() == b'{"denied_rules":["keep"]}' if existing else not target.exists()
    assert not policy.with_name(".permissions.json.lock").exists()


def test_workspace_root_alias_remains_writable(tmp_path: Path) -> None:
    real, alias = tmp_path / "real", tmp_path / "alias"
    plugin = get_project_plugins_dir(real) / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    _link_or_skip(alias, real, directory=True)
    assert set_plugin_enabled("demo", False, alias) == "project"
    assert load_plugins(Settings(), real)[0].enabled is False
    assert alias.is_symlink()


def test_external_policy_change_aborts_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from illusion.permissions import loader

    plugin = get_project_plugins_dir(tmp_path) / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    policy = tmp_path / ".illusion" / "permissions.json"
    policy.write_text('{}', encoding="utf-8")
    original_read = loader._permissions_document
    new_policy = '{"denied_plugins":["*"],"policy_revision":2}'

    def external_edit(path):
        data = original_read(path)
        policy.write_text(new_policy, encoding="utf-8")
        return data

    monkeypatch.setattr(loader, "_permissions_document", external_edit)
    with pytest.raises(ValueError, match="changed during update"):
        set_plugin_enabled("demo", True, tmp_path)
    assert policy.read_text(encoding="utf-8") == new_policy


def test_invalid_plugin_map_preserves_other_permission_filters(tmp_path: Path) -> None:
    path = tmp_path / ".illusion" / "permissions.json"
    path.parent.mkdir()
    path.write_text('{"enabled_plugins":null,"denied_plugins":["*"],"denied_memory":true}', encoding="utf-8")
    permissions = load_project_permissions(tmp_path)
    assert permissions.denied_plugins == ["*"] and permissions.denied_memory
    with pytest.raises(ValueError):
        load_project_permissions(tmp_path, strict=True)


@pytest.mark.parametrize("operation", ["read", "write"])
def test_inaccessible_policy_is_not_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    plugin = get_project_plugins_dir(tmp_path) / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"demo"}', encoding="utf-8")
    path = tmp_path / ".illusion" / "permissions.json"
    content = b'{"future":{"nested":1},"denied_rules":["restricted"]}'
    path.write_bytes(content)
    if operation == "read":
        original_read = Path.read_text

        def read(candidate, *args, **kwargs):
            if candidate == path:
                raise PermissionError("permission denied")
            return original_read(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
    else:
        def fail_write(target, text):
            assert target == path
            raise PermissionError("permission denied")

        monkeypatch.setattr("illusion.permissions.loader.atomic_write_text", fail_write)

    with pytest.raises(ValueError, match=f"Cannot {operation} project permissions"):
        set_plugin_enabled("demo", True, tmp_path)
    assert path.read_bytes() == content
    assert not get_config_file_path().exists()
