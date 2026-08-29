"""Tests for /model command: capability display and interactive prompting."""

from __future__ import annotations

import asyncio
import json

import pytest

from illusion.commands.model import (
    _ask_capabilities,
    _ask_yes_no,
    _capabilities_text,
    model_handler,
)
from illusion.commands.types import CommandContext
from illusion.config import paths as paths_module
from illusion.config.capabilities import ModelCapabilities
from illusion.config.settings import load_settings, save_settings


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """构造带对象格式模型的临时 settings.json。"""
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({
        "model": "env_1.model_1",
        "env_1": {
            "api_format": "openai",
            "model_1": {"name": "gpt-4o", "capabilities": ["image"]},
        },
        "env_2": {
            "api_format": "anthropic",
            "model_1": {"name": "claude-x", "capabilities": []},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(paths_module, "get_config_file_path", lambda: cfg)
    return cfg


class _FakeEngine:
    """记录 set_model 调用的假引擎。"""

    def __init__(self) -> None:
        self.model = None

    def set_model(self, model: str) -> None:
        self.model = model


def _ctx(tmp_path, interactive: bool = True) -> CommandContext:
    return CommandContext(
        engine=_FakeEngine(),
        cwd=str(tmp_path),
        interactive=interactive,
    )


class TestCapabilitiesText:
    def test_object_capabilities(self):
        assert _capabilities_text(ModelCapabilities(supports_images=True)) == "image"
        assert _capabilities_text(ModelCapabilities()) == "none"


class TestAskCapabilities:
    def test_yes(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        assert _ask_capabilities() == ["image"]

    def test_no(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        assert _ask_capabilities() == []

    def test_eof_falls_back_to_default(self, monkeypatch):
        def _raise_eof(prompt: str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise_eof)
        assert _ask_yes_no("q") is False


class TestModelHandler:
    def test_show_displays_capabilities(self, settings_file, tmp_path):
        result = asyncio.run(
            model_handler("", _ctx(tmp_path))
        )
        assert "gpt-4o" in result.message
        assert "媒体能力：image" in result.message

    def test_list_displays_capabilities(self, settings_file, tmp_path):
        result = asyncio.run(
            model_handler("list", _ctx(tmp_path))
        )
        assert "gpt-4o [image]" in result.message
        assert "claude-x [none]" in result.message

    def test_set_non_interactive_keeps_declared_capabilities(self, settings_file, tmp_path):
        """Web 会话（interactive=False）不询问，能力沿用已声明值。"""
        result = asyncio.run(
            model_handler(
                "set env_2.model_1",
                _ctx(tmp_path, interactive=False),
            )
        )
        assert "claude-x" in result.message
        assert "媒体能力：none" in result.message
        assert load_settings().model == "env_2.model_1"
        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        assert saved["env_2"]["model_1"]["capabilities"] == []

    def test_set_interactive_prompts_for_capabilities(self, settings_file, tmp_path, monkeypatch):
        """TUI 交互模式：选模型后询问图片/视频并写入。"""
        monkeypatch.setattr(
            "illusion.commands.model._ask_capabilities",
            lambda: ["image"],
        )
        result = asyncio.run(
            model_handler("set env_2.model_1", _ctx(tmp_path, interactive=True))
        )
        assert "媒体能力：image" in result.message
        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        assert saved["env_2"]["model_1"]["capabilities"] == ["image"]

    def test_set_unknown_ref(self, settings_file, tmp_path):
        result = asyncio.run(
            model_handler("set env_1.model_99", _ctx(tmp_path))
        )
        assert result.message  # 非空即可（不崩溃）


class TestSettingsRoundTrip:
    def test_models_persist_as_object(self, settings_file):
        """save_settings 后 model_N 保持对象形态（写入与读取一致）。"""
        s = load_settings()
        save_settings(s)
        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        assert saved["env_1"]["model_1"] == {"name": "gpt-4o", "capabilities": ["image"]}

    def test_capabilities_accessible_via_settings(self, settings_file):
        s = load_settings()
        assert s.get_model_capabilities() == ModelCapabilities(supports_images=True)