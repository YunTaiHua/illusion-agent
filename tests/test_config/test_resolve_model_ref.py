"""Settings.resolve_model_ref 模型引用解析测试。"""

from __future__ import annotations

from illusion.config.settings import Settings


def _settings_with_env() -> Settings:
    """构造带 env_1 配置的 Settings。"""
    return Settings.model_validate(
        {
            "env_1": {
                "api_format": "openai",
                "base_url": "https://api.example.com",
                "model_1": {"name": "gpt-5.4", "capabilities": ["image"]},
                "model_2": {"name": "deepseek-v4-flash", "capabilities": []},
            },
            "model": "env_1.model_1",
        }
    )


def test_resolve_model_ref_none():
    """None 应返回 None（调用方回退当前模型）。"""
    s = _settings_with_env()
    assert s.resolve_model_ref(None) is None


def test_resolve_model_ref_empty():
    """空字符串视为未设置。"""
    s = _settings_with_env()
    assert s.resolve_model_ref("") is None


def test_resolve_model_ref_valid():
    """env_N.model_M 格式应解析出模型名。"""
    s = _settings_with_env()
    assert s.resolve_model_ref("env_1.model_1") == "gpt-5.4"
    assert s.resolve_model_ref("env_1.model_2") == "deepseek-v4-flash"


def test_resolve_model_ref_unknown_env():
    """不存在的 env 应返回 None。"""
    s = _settings_with_env()
    assert s.resolve_model_ref("env_9.model_1") is None


def test_resolve_model_ref_unknown_model():
    """不存在的 model key 应返回 None。"""
    s = _settings_with_env()
    assert s.resolve_model_ref("env_1.model_9") is None


def test_resolve_model_ref_invalid_format():
    """非 env_N.model_M 格式（裸模型名）应返回 None。"""
    s = _settings_with_env()
    assert s.resolve_model_ref("gpt-5.4") is None
    assert s.resolve_model_ref("env_1") is None
    assert s.resolve_model_ref("env_1.") is None


def test_resolve_model_ref_with_env():
    """resolve_model_ref_with_env 应返回 (env_key, model_name)。"""
    s = _settings_with_env()
    assert s.resolve_model_ref_with_env("env_1.model_2") == (
        "env_1",
        "deepseek-v4-flash",
    )


def test_resolve_model_ref_with_env_none():
    """ref 为 None / 无效时返回 (None, None)。"""
    s = _settings_with_env()
    assert s.resolve_model_ref_with_env(None) == (None, None)
    assert s.resolve_model_ref_with_env("gpt-5.4") == (None, None)
    assert s.resolve_model_ref_with_env("env_9.model_1") == (None, None)


def test_resolve_auth_for_uses_env_config():
    """resolve_auth_for 应使用指定 env 的凭据而非当前 env。"""
    s = Settings.model_validate(
        {
            "env_1": {
                "api_format": "openai",
                "base_url": "https://api.a.com",
                "api_key": "key-a",
                "model_1": "gpt-5.4",
            },
            "env_2": {
                "api_format": "anthropic",
                "base_url": "https://api.b.com",
                "api_key": "key-b",
                "model_1": "claude-x",
            },
            "model": "env_1.model_1",
        }
    )
    auth1 = s.resolve_auth_for("env_1")
    auth2 = s.resolve_auth_for("env_2")
    assert auth1.value == "key-a"
    assert auth2.value == "key-b"

    # 兼容方法：当前活跃 env
    assert s.resolve_auth().value == "key-a"
    assert s.resolve_api_key() == "key-a"
    assert s.resolve_api_key_for("env_2") == "key-b"


def test_resolve_auth_for_unknown_env():
    """未知 env 应抛 ValueError。"""
    import pytest

    s = _settings_with_env()
    with pytest.raises(ValueError):
        s.resolve_auth_for("env_9")
    with pytest.raises(ValueError):
        s.resolve_api_key_for("env_9")


def test_resolve_model_ref_inherits_config():
    """MemorySettings / 顶层配置字段默认值为 None。"""
    s = Settings()
    assert s.memory.extract_model is None
    assert s.memory.dream_model is None

