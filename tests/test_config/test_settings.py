"""Tests for illusion.config.settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from illusion.config.capabilities import ModelCapabilities
from illusion.config.settings import (
    Settings,
    load_settings,
    save_settings,
)


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.api_key == ""
        assert s.model == "env_1.model_1"
        assert s.active_model_name == "claude-sonnet-4-6"
        assert s.max_tokens == 16384
        assert s.max_turns == 200
        assert s.permission.mode == "default"
        assert s.sandbox.filesystem.allow_write == ["."]

    def test_resolve_api_key_from_env_config(self):
        s = Settings(env_1={"api_format": "anthropic", "api_key": "sk-test-123"})
        assert s.resolve_api_key() == "sk-test-123"

    def test_resolve_api_key_missing_raises(self, monkeypatch):
        monkeypatch.setattr("illusion.auth.storage.load_env_credential", lambda *a, **kw: None)
        s = Settings()
        with pytest.raises(ValueError, match="未找到 API 密钥"):
            s.resolve_api_key()

    def test_merge_cli_overrides(self):
        s = Settings()
        updated = s.merge_cli_overrides(model="env_2.model_1", api_key=None)
        assert updated.model == "env_2.model_1"

    def test_merge_cli_overrides_returns_new_instance(self):
        s = Settings()
        updated = s.merge_cli_overrides(model="env_2.model_1")
        assert s.model != updated.model
        assert s is not updated

    def test_merge_cli_overrides_env_fields(self):
        """merge_cli_overrides 正确覆盖 env 级字段（api_key/base_url/api_format）。"""
        s = Settings(env_1={"api_format": "anthropic", "api_key": "sk-old", "base_url": "https://old.com"})
        updated = s.merge_cli_overrides(api_key="sk-new", base_url="https://new.com", api_format="openai")
        assert updated.api_key == "sk-new"
        assert updated.base_url == "https://new.com"
        assert updated.api_format == "openai"

    def test_merge_cli_overrides_does_not_mutate_original(self):
        """merge_cli_overrides 永远返回新实例，不修改原始 Settings。"""
        s = Settings(env_1={"api_format": "anthropic", "api_key": "sk-original"})
        _ = s.merge_cli_overrides(api_key="sk-changed")
        assert s.api_key == "sk-original"

    def test_active_env_properties(self):
        s = Settings(
            env_1={"api_format": "openai", "api_key": "sk-test", "base_url": "https://api.example.com"},
        )
        assert s.api_format == "openai"
        assert s.api_key == "sk-test"
        assert s.base_url == "https://api.example.com"

    def test_active_model_name_from_env(self):
        s = Settings(
            model="env_1.model_1",
            env_1={"api_format": "anthropic", "model_1": {"name": "claude-opus-4-20250514", "capabilities": []}},
        )
        assert s.active_model_name == "claude-opus-4-20250514"

    def test_active_model_name_fallback(self):
        """When no env config is set, active_model_name falls back to claude-sonnet-4-6"""
        s = Settings()
        assert s.active_model_name == "claude-sonnet-4-6"


class TestLoadSaveSettings:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        s = load_settings(path)
        assert s.model == "env_1.model_1"
        assert s.active_model_name == "claude-sonnet-4-6"
        assert s.max_tokens == 16384

    def test_load_existing_file(self, tmp_path: Path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "model": "env_1.model_1",
            "env_1": {"api_format": "anthropic", "model_1": {"name": "claude-opus-4-20250514", "capabilities": []}},
        }))
        s = load_settings(path)
        assert s.active_model_name == "claude-opus-4-20250514"
        assert s.api_key == ""

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "settings.json"
        original = Settings(
            env_1={"api_format": "anthropic", "api_key": "sk-roundtrip", "model_1": {"name": "claude-opus-4-20250514", "capabilities": []}},
        )
        save_settings(original, path)
        loaded = load_settings(path)
        assert loaded.api_key == "sk-roundtrip"
        assert loaded.active_model_name == "claude-opus-4-20250514"

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / "settings.json"
        save_settings(Settings(), path)
        assert path.exists()

    def test_load_with_permission_settings(self, tmp_path: Path):
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "permission": {
                        "mode": "full_auto",
                        "allowed_tools": ["Bash", "Read"],
                    }
                }
            )
        )
        s = load_settings(path)
        assert s.permission.mode == "full_auto"
        assert s.permission.allowed_tools == ["Bash", "Read"]

    def test_load_with_sandbox_settings(self, tmp_path: Path):
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "sandbox": {
                        "enabled_platforms": ["linux", "wsl"],
                        "network": {"allowed_domains": ["github.com"]},
                        "filesystem": {"allow_write": [".", "/tmp"], "deny_write": [".env"]},
                    }
                }
            )
        )

        s = load_settings(path)

        assert s.sandbox.enabled_platforms == ["linux", "wsl"]
        assert s.sandbox.network.allowed_domains == ["github.com"]
        assert s.sandbox.filesystem.allow_write == [".", "/tmp"]
        assert s.sandbox.filesystem.deny_write == [".env"]

    def test_load_with_env_config(self, tmp_path: Path):
        """Test loading a file that uses the new env_N format."""
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "model": "env_1.model_1",
                    "env_1": {
                        "api_format": "anthropic",
                        "api_key": "sk-test",
                        "model_1": {"name": "claude-sonnet-4-6", "capabilities": []},
                        "model_2": {"name": "claude-opus-4-6", "capabilities": []},
                    },
                }
            )
        )

        s = load_settings(path)
        assert s.api_key == "sk-test"
        assert s.active_model_name == "claude-sonnet-4-6"

    def test_save_preserves_env_config(self, tmp_path: Path):
        """Test that save/load roundtrip preserves env_N config."""
        path = tmp_path / "settings.json"
        original = Settings(
            model="env_1.model_2",
            env_1={
                "api_format": "openai",
                "api_key": "sk-test",
                "model_1": {"name": "gpt-4", "capabilities": []},
                "model_2": {"name": "gpt-5.4", "capabilities": ["image"]},
            },
        )
        save_settings(original, path)
        loaded = load_settings(path)
        assert loaded.active_model_name == "gpt-5.4"
        assert loaded.api_key == "sk-test"
        # 能力随模型声明持久化
        assert loaded.get_model_capabilities() == ModelCapabilities(supports_images=True)


