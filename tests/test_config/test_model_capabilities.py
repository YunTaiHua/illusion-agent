"""Tests for model capability declarations (settings model object format)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from illusion.config.capabilities import (
    ModelCapabilities,
    flatten_capabilities,
    parse_capabilities,
)
from illusion.config.settings import (
    EnvConfig,
    ModelConfig,
    Settings,
)


class TestParseCapabilities:
    def test_empty_is_none(self):
        caps = parse_capabilities(None)
        assert caps.supports_images is False

    def test_image_only(self):
        caps = parse_capabilities(["image"])
        assert caps.supports_images is True

    def test_unknown_values_ignored(self):
        caps = parse_capabilities(["audio", "foo"])
        assert caps.supports_images is False

    def test_flatten_roundtrip(self):
        assert flatten_capabilities(ModelCapabilities(supports_images=True)) == ["image"]
        assert flatten_capabilities(ModelCapabilities()) == []


class TestModelConfig:
    def test_valid_capabilities(self):
        mc = ModelConfig(name="gpt-4o", capabilities=["image"])
        assert mc.media_capabilities.supports_images is True

    def test_declared_but_empty_capabilities_is_fail_closed(self):
        mc = ModelConfig(name="deepseek-v3")
        assert mc.media_capabilities.supports_images is False

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValidationError, match="unknown model capability"):
            ModelConfig(name="x", capabilities=["audio"])

    def test_duplicate_capabilities_deduped(self):
        mc = ModelConfig(name="x", capabilities=["image", "image"])
        assert mc.capabilities == ["image"]


class TestEnvConfigModelObject:
    def test_get_model_object_format(self):
        env = EnvConfig.model_validate({
            "api_format": "openai",
            "model_1": {"name": "gpt-4o", "capabilities": ["image"]},
        })
        assert env.get_model("model_1") == "gpt-4o"
        config = env.get_model_config("model_1")
        assert config is not None
        assert config.name == "gpt-4o"
        assert config.capabilities == ["image"]

    def test_list_models(self):
        env = EnvConfig.model_validate({
            "api_format": "openai",
            "model_1": {"name": "a", "capabilities": []},
            "model_2": {"name": "b", "capabilities": ["image"]},
        })
        assert env.list_models() == {"model_1": "a", "model_2": "b"}
        assert set(env.list_model_configs()) == {"model_1", "model_2"}

    def test_get_model_config_invalid_object_returns_none(self):
        env = EnvConfig.model_validate({
            "api_format": "openai",
            "model_1": {"capabilities": ["image"]},  # 缺 name
        })
        assert env.get_model_config("model_1") is None


class TestSettingsGetCapabilities:
    def _settings(self) -> Settings:
        return Settings.model_validate({
            "model": "env_1.model_1",
            "env_1": {
                "api_format": "openai",
                "model_1": {"name": "gpt-4o", "capabilities": ["image"]},
                "model_2": {"name": "no-vision"},
            },
            "env_2": {
                "api_format": "anthropic",
                "model_1": {"name": "claude-3-5", "capabilities": ["image"]},
            },
        })

    def test_active_model(self):
        assert self._settings().get_model_capabilities() == ModelCapabilities(
            supports_images=True
        )

    def test_cross_env_ref(self):
        assert self._settings().get_model_capabilities("env_2.model_1") == ModelCapabilities(
            supports_images=True
        )

    def test_declared_without_capabilities_is_fail_closed(self):
        assert self._settings().get_model_capabilities("env_1.model_2") == ModelCapabilities()

    def test_invalid_env_fail_closed(self):
        assert self._settings().get_model_capabilities("env_99.model_1") == ModelCapabilities()

    def test_invalid_ref_falls_back_to_active(self):
        assert self._settings().get_model_capabilities("garbage") == ModelCapabilities(
            supports_images=True
        )