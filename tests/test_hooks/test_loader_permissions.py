"""
Hooks 加载器权限过滤测试
========================

测试 load_hook_registry 函数的权限过滤功能。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from illusion.hooks.events import HookEvent
from illusion.hooks.loader import load_hook_registry


class TestLoadHookRegistryPermissions:
    """load_hook_registry 函数权限过滤测试"""

    def _create_settings(self) -> SimpleNamespace:
        """创建模拟设置对象"""
        return SimpleNamespace(
            hooks={
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo test",
                            }
                        ],
                    }
                ],
            }
        )

    def test_no_permissions(self, tmp_path: Path) -> None:
        """测试没有权限配置时加载所有 hooks"""
        settings = self._create_settings()
        registry = load_hook_registry(settings, cwd=tmp_path)
        hooks = registry.get(HookEvent.PRE_TOOL_USE)
        assert len(hooks) > 0

    def test_denied_specific_event(self, tmp_path: Path) -> None:
        """测试禁用特定事件的 hooks"""
        # 创建权限配置
        config_dir = tmp_path / ".illusion"
        config_dir.mkdir()
        permissions_file = config_dir / "permissions.json"
        permissions_file.write_text(
            json.dumps({
                "denied_hooks": ["PreToolUse"],
            }),
            encoding="utf-8",
        )

        settings = self._create_settings()
        registry = load_hook_registry(settings, cwd=tmp_path)
        hooks = registry.get(HookEvent.PRE_TOOL_USE)
        assert len(hooks) == 0

    def test_denied_all_hooks(self, tmp_path: Path) -> None:
        """测试禁用所有 hooks"""
        # 创建权限配置
        config_dir = tmp_path / ".illusion"
        config_dir.mkdir()
        permissions_file = config_dir / "permissions.json"
        permissions_file.write_text(
            json.dumps({
                "denied_hooks": ["*"],
            }),
            encoding="utf-8",
        )

        settings = self._create_settings()
        registry = load_hook_registry(settings, cwd=tmp_path)

        # 检查所有事件都没有 hooks
        for event in HookEvent:
            hooks = registry.get(event)
            assert len(hooks) == 0


class TestLocalHookDiscovery:
    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(hooks={})

    def test_loads_user_and_target_project_files_and_skips_one_bad_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        other_workspace = tmp_path / "other"
        target_workspace = tmp_path / "target"
        other_workspace.mkdir()
        target_workspace.mkdir()
        monkeypatch.chdir(other_workspace)

        config_root = tmp_path / "config"
        monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
        self._write_json(
            config_root / "hooks.json",
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "UserTool", "hooks": [{"type": "command", "command": "user"}]},
                    ]
                }
            },
        )
        self._write_json(
            config_root / "hooks" / "broken.json",
            {"PreToolUse": [{"hooks": [{"type": "command"}]}]},
        )
        self._write_json(
            config_root / "hooks" / "valid.json",
            {
                "PreToolUse": [
                    {
                        "matcher": "DisabledTool",
                        "hooks": [{"type": "command", "command": "disabled", "enabled": False}],
                    },
                    {"matcher": "ValidTool", "hooks": [{"type": "command", "command": "valid"}]},
                ]
            },
        )
        self._write_json(
            target_workspace / ".illusion" / "hooks" / "project.json",
            {
                "PreToolUse": [
                    {"matcher": "TargetTool", "hooks": [{"type": "command", "command": "target"}]},
                ]
            },
        )
        self._write_json(
            other_workspace / ".illusion" / "hooks" / "wrong.json",
            {
                "PreToolUse": [
                    {"matcher": "WrongTool", "hooks": [{"type": "command", "command": "wrong"}]},
                ]
            },
        )

        registry = load_hook_registry(self._settings(), cwd=target_workspace)
        matchers = registry.get(HookEvent.PRE_TOOL_USE)
        by_matcher = {matcher.matcher: matcher for matcher in matchers}

        assert set(by_matcher) == {"UserTool", "ValidTool", "TargetTool"}
        assert "WrongTool" not in by_matcher
        assert by_matcher["ValidTool"].hooks[0].command == "valid"

    def test_duplicate_user_and_project_path_is_registered_once(self, tmp_path: Path, monkeypatch) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config_root = workspace / ".illusion"
        monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
        self._write_json(
            config_root / "hooks.json",
            {
                "PreToolUse": [
                    {"matcher": "SameTool", "hooks": [{"type": "command", "command": "same"}]},
                ]
            },
        )

        registry = load_hook_registry(self._settings(), cwd=workspace)

        matchers = registry.get(HookEvent.PRE_TOOL_USE)
        assert [matcher.matcher for matcher in matchers] == ["SameTool"]

    def test_discovery_does_not_execute_declared_commands(self, tmp_path: Path, monkeypatch) -> None:
        marker = tmp_path / "executed.txt"
        config_root = tmp_path / "config"
        monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
        self._write_json(
            config_root / "hooks" / "declared.json",
            {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \\\"from pathlib import Path; Path(r'{marker}').write_text('ran')\\\"",
                            }
                        ],
                    }
                ]
            },
        )

        registry = load_hook_registry(self._settings(), cwd=tmp_path)

        assert registry.get(HookEvent.PRE_TOOL_USE)
        assert not marker.exists()

    def test_disabled_plugin_hook_is_not_consumed(self, tmp_path: Path) -> None:
        enabled_plugin = SimpleNamespace(
            enabled=True,
            hooks={
                "PreToolUse": [
                    {"matcher": "Enabled", "hooks": [{"type": "command", "command": "enabled"}]},
                ]
            },
        )
        disabled_plugin = SimpleNamespace(
            enabled=False,
            hooks={
                "PreToolUse": [
                    {"matcher": "Disabled", "hooks": [{"type": "command", "command": "disabled"}]},
                ]
            },
        )

        registry = load_hook_registry(self._settings(), [enabled_plugin, disabled_plugin], tmp_path)

        assert [matcher.matcher for matcher in registry.get(HookEvent.PRE_TOOL_USE)] == ["Enabled"]
