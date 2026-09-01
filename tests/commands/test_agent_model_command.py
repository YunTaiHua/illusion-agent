"""/agent model 子命令测试"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.commands.types import CommandContext


def _make_context(cwd: Path) -> CommandContext:
    return CommandContext(engine=MagicMock(), cwd=str(cwd))


def _write_agent(agents_dir: Path, name: str = "my-agent") -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: when x\nmodel: inherit\n---\nbody\n", encoding="utf-8")
    return path


@pytest.fixture()
def isolated_settings(monkeypatch, tmp_path):
    """隔离的 settings（两个 env，各含模型）。"""
    from illusion.config.settings import Settings

    settings = Settings(
        model="env_1.model_1",
        env_1={
            "api_format": "openai",
            "api_key": "k",
            "model_1": {"name": "model-a"},
        },
        env_2={
            "api_format": "openai",
            "api_key": "k",
            "model_1": {"name": "model-b"},
        },
    )
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr("illusion.config.settings.load_settings", lambda *a, **kw: settings)
    return settings


@pytest.mark.asyncio
async def test_agent_model_sets_user_agent_md(isolated_settings, tmp_path):
    """用户级 agent：模型写入 .md 文件（不固化 settings）。"""
    from illusion.commands.agent import agent_handler
    from illusion.config.paths import get_config_dir

    agents_dir = get_config_dir() / "agents"
    _write_agent(agents_dir)
    context = _make_context(tmp_path)

    result = await agent_handler("model my-agent env_2.model_1", context)
    assert "env_2.model_1" in result.message
    assert ".md" in result.message
    # settings 未被写入
    assert "my-agent" not in isolated_settings.agent_models
    # 文件已更新
    content = (agents_dir / "my-agent.md").read_text(encoding="utf-8")
    assert "model: env_2.model_1" in content


@pytest.mark.asyncio
async def test_agent_model_builtin_goes_to_settings(isolated_settings, tmp_path):
    """内置 agent：模型固化到 settings.json（agent_models）。"""
    from illusion.commands.agent import agent_handler

    context = _make_context(tmp_path)
    result = await agent_handler("model explore env_2.model_1", context)
    assert "settings.json" in result.message
    assert isolated_settings.agent_models.get("explore") == "env_2.model_1"


@pytest.mark.asyncio
async def test_agent_model_builtin_inherit_clears_override(isolated_settings, tmp_path):
    """内置 agent 设为 inherit 时清除覆盖。"""
    from illusion.commands.agent import agent_handler

    isolated_settings.agent_models["explore"] = "env_2.model_1"
    context = _make_context(tmp_path)
    await agent_handler("model explore inherit", context)
    assert "explore" not in isolated_settings.agent_models


@pytest.mark.asyncio
async def test_agent_model_unknown_agent(isolated_settings, tmp_path):
    """未知 agent 名返回可用列表。"""
    from illusion.commands.agent import agent_handler

    context = _make_context(tmp_path)
    result = await agent_handler("model no-such-agent env_1.model_1", context)
    assert "not found" in result.message
    assert "explore" in result.message  # 内置 agent 出现在可用列表


@pytest.mark.asyncio
async def test_agent_model_unknown_model_rejected(isolated_settings, tmp_path):
    """未配置的模型被拒（不再裸名直发导致 404）。"""
    from illusion.commands.agent import agent_handler

    context = _make_context(tmp_path)
    result = await agent_handler("model explore ghost-model", context)
    assert "Unknown model" in result.message
    assert "explore" not in isolated_settings.agent_models


@pytest.mark.asyncio
async def test_agent_model_bare_name_rejected(isolated_settings, tmp_path):
    """裸模型名不做兼容，直接拒绝（不写入 settings）。"""
    from illusion.commands.agent import agent_handler

    context = _make_context(tmp_path)
    result = await agent_handler("model explore model-b", context)
    assert "Unknown model" in result.message
    assert "explore" not in isolated_settings.agent_models


@pytest.mark.asyncio
async def test_agent_model_goal_verifier_independent_key(isolated_settings, tmp_path):
    """goal-verifier 视作内置条目：模型固化到 settings.agent_models 独立键。"""
    from illusion.commands.agent import agent_handler

    context = _make_context(tmp_path)
    result = await agent_handler("model goal-verifier env_2.model_1", context)
    assert "settings.json" in result.message
    assert isolated_settings.agent_models.get("goal-verifier") == "env_2.model_1"
    # verification 不受影响（未配置）
    assert "verification" not in isolated_settings.agent_models


@pytest.mark.asyncio
async def test_agent_model_list_includes_goal_verifier(isolated_settings, tmp_path):
    """/agent model 无参列表包含 goal-verifier 内置条目。"""
    from illusion.commands.agent import agent_handler

    context = _make_context(tmp_path)
    result = await agent_handler("model", context)
    assert "goal-verifier" in result.message
    assert "verification" in result.message
