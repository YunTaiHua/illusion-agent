"""Tests for illusion.config.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from illusion.config.paths import (
    get_config_dir,
    get_config_file_path,
    get_data_dir,
    get_logs_dir,
    get_mcp_config_file_path,
    get_project_config_dir,
    get_project_hooks_dir,
    get_project_mcp_config_file_path,
    get_tasks_dir,
    get_user_hooks_dir,
    resolve_relative_path,
    validate_safe_path,
)


def test_get_config_dir_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ILLUSION_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    config_dir = get_config_dir()
    assert config_dir == tmp_path / ".illusion"
    assert not config_dir.exists()


def test_get_config_dir_env_override(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom_config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(custom))
    config_dir = get_config_dir()
    assert config_dir == custom
    assert not config_dir.exists()


def test_get_config_file_path(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ILLUSION_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = get_config_file_path()
    assert path == tmp_path / ".illusion" / "settings.json"


def test_get_data_dir_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ILLUSION_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ILLUSION_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    data_dir = get_data_dir()
    assert data_dir == tmp_path / ".illusion" / "data"
    assert data_dir.is_dir()


def test_get_data_dir_env_override(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom_data"
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(custom))
    data_dir = get_data_dir()
    assert data_dir == custom
    assert data_dir.is_dir()


def test_get_logs_dir_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ILLUSION_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ILLUSION_LOGS_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    logs_dir = get_logs_dir()
    assert logs_dir == tmp_path / ".illusion" / "logs"
    assert logs_dir.is_dir()


def test_get_logs_dir_env_override(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom_logs"
    monkeypatch.setenv("ILLUSION_LOGS_DIR", str(custom))
    logs_dir = get_logs_dir()
    assert logs_dir == custom
    assert logs_dir.is_dir()


def test_get_tasks_dir_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ILLUSION_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ILLUSION_DATA_DIR", raising=False)
    monkeypatch.delenv("ILLUSION_TASK_LIST_ID", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    tasks_dir = get_tasks_dir()
    assert tasks_dir == tmp_path / ".illusion" / "data" / "tasks"
    assert tasks_dir.is_dir()


def test_get_tasks_dir_with_task_list_id(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ILLUSION_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ILLUSION_DATA_DIR", raising=False)
    monkeypatch.setenv("ILLUSION_TASK_LIST_ID", "Team Alpha/01")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    tasks_dir = get_tasks_dir()
    assert tasks_dir == tmp_path / ".illusion" / "data" / "tasks" / "Team-Alpha-01"
    assert tasks_dir.is_dir()


def test_resource_path_getters_do_not_create_missing_roots(tmp_path: Path, monkeypatch):
    """读取/扫描所用的路径 getter 不应创建用户根或项目 .illusion。"""
    from illusion.plugins.loader import get_project_plugins_dir, get_user_plugins_dir
    from illusion.skills.loader import (
        get_project_commands_dir,
        get_project_rules_dir,
        get_project_skills_dir,
        get_user_commands_dir,
        get_user_skills_dir,
    )

    config_root = tmp_path / "config"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))

    paths = [
        get_user_skills_dir(),
        get_user_commands_dir(),
        get_user_hooks_dir(),
        get_user_plugins_dir(),
        get_mcp_config_file_path(),
        get_project_skills_dir(workspace),
        get_project_commands_dir(workspace),
        get_project_rules_dir(workspace),
        get_project_hooks_dir(workspace),
        get_project_plugins_dir(workspace),
        get_project_mcp_config_file_path(workspace),
        get_project_config_dir(workspace),
    ]

    assert all(not path.exists() for path in paths)
    assert not config_root.exists()
    assert not (workspace / ".illusion").exists()


def test_resource_loaders_do_not_create_missing_roots(tmp_path: Path, monkeypatch):
    """资源读取器在入口缺失时只返回空结果或内置结果，不创建目录。"""
    from types import SimpleNamespace

    from illusion.config.settings import Settings
    from illusion.coordinator.agent_definitions import get_all_agent_definitions
    from illusion.hooks.loader import load_hook_registry
    from illusion.mcp.config import load_mcp_server_configs
    from illusion.permissions.loader import load_project_permissions
    from illusion.plugins.loader import discover_plugin_paths
    from illusion.skills.loader import load_skill_registry

    config_root = tmp_path / "config"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))

    load_skill_registry(workspace)
    load_hook_registry(SimpleNamespace(hooks={}), cwd=workspace)
    load_project_permissions(workspace)
    load_mcp_server_configs(Settings(), [], cwd=workspace)
    get_all_agent_definitions(cwd=str(workspace))
    assert discover_plugin_paths(workspace) == []

    assert not config_root.exists()
    assert not (workspace / ".illusion").exists()


# ---------- validate_safe_path ----------


class TestValidateSafePath:
    """validate_safe_path 路径安全校验测试。"""

    def test_allows_normal_relative_path(self) -> None:
        """普通相对路径应通过校验。"""
        result = validate_safe_path("src/main.py")
        assert result == Path("src/main.py")

    def test_rejects_parent_traversal(self) -> None:
        """包含 .. 的路径应被拒绝。"""
        with pytest.raises(ValueError, match="Path traversal"):
            validate_safe_path("../escape")

    def test_rejects_parent_traversal_in_middle(self) -> None:
        """路径中间包含 .. 也应被拒绝。"""
        with pytest.raises(ValueError, match="Path traversal"):
            validate_safe_path("src/../etc/passwd")

    def test_rejects_home_tilde(self) -> None:
        """以 ~ 开头的路径应被拒绝。"""
        with pytest.raises(ValueError, match="Home directory"):
            validate_safe_path("~/secret")


# ---------- resolve_relative_path ----------


class TestResolveRelativePath:
    """resolve_relative_path 路径解析与安全校验测试。"""

    def test_resolves_relative_path(self, tmp_path: Path) -> None:
        """相对路径应与 base 拼接并 resolve。"""
        result = resolve_relative_path(tmp_path, "subdir/file.py")
        assert result == (tmp_path / "subdir" / "file.py").resolve()

    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        """包含 .. 的路径应被拒绝。"""
        with pytest.raises(ValueError, match="Path traversal"):
            resolve_relative_path(tmp_path, "../escape")

    def test_rejects_home_tilde(self, tmp_path: Path) -> None:
        """以 ~ 开头的路径应被拒绝。"""
        with pytest.raises(ValueError, match="Home directory"):
            resolve_relative_path(tmp_path, "~/secret")
