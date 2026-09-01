"""agent 模型引用解析测试（Settings.resolve_agent_model_spec / agent_models）"""
from __future__ import annotations

from illusion.config.settings import Settings


def make_settings() -> Settings:
    """构造带两个 env 的测试 Settings（env_1 含多模态模型）。"""
    return Settings(
        model="env_1.model_1",
        env_1={
            "api_format": "openai",
            "base_url": "https://a.example.com/v1",
            "api_key": "sk-a",
            "model_1": {"name": "claude-sonnet", "capabilities": ["image"]},
            "model_2": {"name": "step-3.7-flash"},
        },
        env_2={
            "api_format": "openai",
            "base_url": "https://b.example.com/v1",
            "api_key": "sk-b",
            "model_1": {"name": "step-3.7-flash"},
        },
    )


class TestResolveAgentModelSpec:
    def test_none_and_inherit(self):
        """None / inherit / 空串 → 继承（全 None）。"""
        s = make_settings()
        assert s.resolve_agent_model_spec(None) == (None, None, None)
        assert s.resolve_agent_model_spec("inherit") == (None, None, None)
        assert s.resolve_agent_model_spec("  ") == (None, None, None)

    def test_ref_direct(self):
        """env_N.model_M 引用直接解析。"""
        s = make_settings()
        env_key, model_name, ref = s.resolve_agent_model_spec("env_2.model_1")
        assert (env_key, model_name, ref) == ("env_2", "step-3.7-flash", "env_2.model_1")

    def test_bare_name_rejected(self):
        """裸模型名不做兼容反查，视为非法（(None, None, 原值)）。"""
        s = make_settings()
        env_key, model_name, ref = s.resolve_agent_model_spec("step-3.7-flash")
        assert env_key is None and model_name is None
        assert ref == "step-3.7-flash"

    def test_unknown_returns_raw(self):
        """无法解析的值返回 (None, None, 原值)，由调用方拒绝或回退并告警。"""
        s = make_settings()
        env_key, model_name, ref = s.resolve_agent_model_spec("ghost-model")
        assert env_key is None and model_name is None
        assert ref == "ghost-model"


class TestAgentModelsField:
    def test_default_empty(self):
        """agent_models 默认空 dict。"""
        assert Settings().agent_models == {}

    def test_roundtrip_dump(self):
        """agent_models 参与序列化（save_settings 持久化依据）。"""
        s = make_settings()
        s.agent_models["explore"] = "env_2.model_1"
        data = s.model_dump()
        assert data["agent_models"] == {"explore": "env_2.model_1"}

    def test_get_model_capabilities_by_ref(self):
        """按引用查询多模态能力：声明 image 的模型支持，未声明不支持。"""
        s = make_settings()
        assert s.get_model_capabilities("env_1.model_1").supports_images is True
        assert s.get_model_capabilities("env_1.model_2").supports_images is False
