"""记忆目录权限 carve-out 边界测试。

验证主对话 LLM 直接写记忆文件时的权限行为：
    - 记忆目录内 write_file/edit_file 默认模式免确认
    - 记忆目录外写工具仍需确认
    - plan 模式仍拦截记忆写入
    - 用户显式 deny 路径规则优先于 carve-out
    - 自定义记忆目录（memory.directory）同样放行
"""

from __future__ import annotations

import json
from pathlib import Path

from illusion.config.settings import PermissionSettings
from illusion.permissions.checker import PermissionChecker
from illusion.permissions.modes import PermissionMode


def _checker(mode: PermissionMode = PermissionMode.DEFAULT) -> PermissionChecker:
    return PermissionChecker(PermissionSettings(mode=mode))


def _memory_file(tmp_path: Path, monkeypatch, name: str = "user_role.md") -> str:
    """在隔离的记忆目录中生成一个文件路径。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    from illusion.memory.paths import get_memory_dir

    memory_dir = get_memory_dir()
    return str(memory_dir / name)


def test_write_in_memory_dir_allowed_default_mode(tmp_path: Path, monkeypatch):
    """默认模式下写记忆目录内文件应免确认放行。"""
    path = _memory_file(tmp_path, monkeypatch)
    decision = _checker().evaluate("write_file", is_read_only=False, file_path=path)
    assert decision.allowed
    assert "carve-out" in decision.reason


def test_edit_in_memory_dir_allowed_default_mode(tmp_path: Path, monkeypatch):
    """默认模式下编辑记忆目录内文件应免确认放行。"""
    path = _memory_file(tmp_path, monkeypatch)
    decision = _checker().evaluate("edit_file", is_read_only=False, file_path=path)
    assert decision.allowed


def test_write_outside_memory_dir_requires_confirmation(tmp_path: Path, monkeypatch):
    """记忆目录外的写工具仍需要确认。"""
    _memory_file(tmp_path, monkeypatch)  # 初始化隔离环境
    outside = str(tmp_path / "repo" / "main.py")
    decision = _checker().evaluate("write_file", is_read_only=False, file_path=outside)
    assert not decision.allowed
    assert decision.requires_confirmation


def test_write_memory_dir_plan_mode_blocked(tmp_path: Path, monkeypatch):
    """plan 模式下写记忆仍被拦截（carve-out 不豁免 plan 模式）。"""
    path = _memory_file(tmp_path, monkeypatch)
    decision = _checker(PermissionMode.PLAN).evaluate(
        "write_file", is_read_only=False, file_path=path
    )
    assert not decision.allowed
    assert decision.auto_blocked


def test_deny_path_rule_overrides_carve_out(tmp_path: Path, monkeypatch):
    """用户显式 deny 路径规则优先于记忆 carve-out。"""
    path = _memory_file(tmp_path, monkeypatch)
    settings = PermissionSettings(
        mode=PermissionMode.DEFAULT,
        path_rules=[{"pattern": str(tmp_path / "config" / "**"), "allow": False}],
    )
    decision = PermissionChecker(settings).evaluate(
        "write_file", is_read_only=False, file_path=path
    )
    assert not decision.allowed
    assert "deny rule" in decision.reason


def test_read_only_tools_unaffected(tmp_path: Path, monkeypatch):
    """只读工具不受 carve-out 影响（本来就走只读放行）。"""
    path = _memory_file(tmp_path, monkeypatch)
    decision = _checker().evaluate("read_file", is_read_only=True, file_path=path)
    assert decision.allowed


def test_bash_not_carved_out(tmp_path: Path, monkeypatch):
    """bash 等非文件工具不受记忆 carve-out 影响。"""
    path = _memory_file(tmp_path, monkeypatch)
    decision = _checker().evaluate(
        "bash", is_read_only=False, file_path=path, command="echo x >> memory"
    )
    assert not decision.allowed  # 默认模式仍需确认


def test_custom_memory_dir_carve_out(tmp_path: Path, monkeypatch):
    """自定义记忆目录（memory.directory）同样放行。"""
    custom_dir = tmp_path / "custom_mem"
    custom_dir.mkdir(parents=True)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"memory": {"directory": str(custom_dir)}}), encoding="utf-8"
    )

    decision = _checker().evaluate(
        "write_file",
        is_read_only=False,
        file_path=str(custom_dir / "user_role.md"),
    )
    assert decision.allowed
    assert "carve-out" in decision.reason
