"""goal-verifier 独立内置条目测试（定义/管理视图/独立模型键）"""
from __future__ import annotations

from illusion.coordinator.agent_definitions import (
    GOAL_VERIFIER_AGENT_NAME,
    get_builtin_agent_definitions,
    get_goal_verifier_definition,
    get_managed_agent_definitions,
)


def test_goal_verifier_is_separate_from_verification():
    """goal-verifier 复用验证内容但 name 独立，不出现在派发用内置列表。"""
    goal_def = get_goal_verifier_definition()
    assert goal_def.name == GOAL_VERIFIER_AGENT_NAME
    assert goal_def.source == "builtin"
    assert goal_def.system_prompt == next(
        a for a in get_builtin_agent_definitions() if a.name == "verification"
    ).system_prompt
    # 派发面（agent 工具描述/可派发列表）仍只有三个内置
    assert GOAL_VERIFIER_AGENT_NAME not in {
        a.name for a in get_builtin_agent_definitions()
    }


def test_managed_view_includes_goal_verifier():
    """管理视图 = 全部定义 + goal-verifier（无同名用户项时）。"""
    names = [a.name for a in get_managed_agent_definitions()]
    assert GOAL_VERIFIER_AGENT_NAME in names
    assert "verification" in names


def test_managed_view_dedupes_user_goal_verifier(monkeypatch, tmp_path):
    """用户自建同名 goal-verifier 时以用户定义为准，不再附加内置项。"""
    from illusion.coordinator import agent_definitions as module

    user_agents_dir = tmp_path / "agents"
    user_agents_dir.mkdir(parents=True)
    (user_agents_dir / f"{GOAL_VERIFIER_AGENT_NAME}.md").write_text(
        f"---\nname: {GOAL_VERIFIER_AGENT_NAME}\ndescription: user one\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(module, "_get_user_agents_dir", lambda: user_agents_dir)

    agents = get_managed_agent_definitions()
    matches = [a for a in agents if a.name == GOAL_VERIFIER_AGENT_NAME]
    assert len(matches) == 1
    assert matches[0].source == "user"
    assert matches[0].description == "user one"


def test_goal_verifier_model_override_independent(monkeypatch, tmp_path):
    """goal-verifier 与 verification 的模型覆盖互不影响。"""
    from illusion.config.settings import Settings

    settings = Settings(
        model="env_1.model_1",
        env_1={"api_format": "openai", "api_key": "k",
               "model_1": {"name": "model-a"}},
        agent_models={
            "verification": "env_1.model_1",
            "goal-verifier": "env_1.model_1",
        },
    )
    monkeypatch.setattr("illusion.config.settings.load_settings", lambda *a, **kw: settings)
    assert settings.agent_models["goal-verifier"] == settings.agent_models["verification"]

    # 仅改 goal-verifier 键，verification 不受影响
    settings.agent_models.pop("goal-verifier")
    assert "goal-verifier" not in settings.agent_models
    assert settings.agent_models["verification"] == "env_1.model_1"


def test_verifier_spawn_def_uses_goal_verifier_name(monkeypatch):
    """verifier.py 构造的 spawn 定义为 goal-verifier 名称（独立模型键）。"""
    from illusion.coordinator.agent_definitions import get_agent_definition
    from illusion.goal.verifier import _get_verifier_definition

    verifier_def = _get_verifier_definition()
    assert verifier_def is not None
    assert verifier_def.name == GOAL_VERIFIER_AGENT_NAME
    # 定义内容跟随 verification（含用户覆盖时）
    assert verifier_def.system_prompt == get_agent_definition("verification").system_prompt
