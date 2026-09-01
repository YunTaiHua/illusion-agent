""" agent_creator 服务测试 """
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.services.agent_creator import (
    AGENT_CREATION_SYSTEM_PROMPT,
    GeneratedAgent,
    validate_agent_definition,
    write_agent_definition,
)


def test_prompt_no_claude_brand():
    """提示词中品牌名已替换为 illusion agent。"""
    assert "Claude" not in AGENT_CREATION_SYSTEM_PROMPT


def test_validate_name_conflict(monkeypatch):
    """名称与现有 agent 冲突时报错。"""
    existing = MagicMock()
    existing.name = "existing-agent"
    monkeypatch.setattr("illusion.services.agent_creator.get_all_agent_definitions", lambda *a, **k: [existing])
    result = validate_agent_definition({"name": "existing-agent", "system_prompt": "x", "description": "y"}, cwd=".")
    assert "name" in result


def test_validate_valid_definition(monkeypatch):
    """合法定义返回空错误 dict。"""
    monkeypatch.setattr("illusion.services.agent_creator.get_all_agent_definitions", lambda *a, **k: list(a[0]) if a else [])
    result = validate_agent_definition({"name": "new-agent", "system_prompt": "x", "description": "y", "model": "inherit"}, cwd=".")
    assert result == {}


def test_write_agent_definition_user_scope(tmp_path, monkeypatch):
    """user scope 写入 agents 目录。"""
    fake_dir = tmp_path / "agents"
    monkeypatch.setattr("illusion.services.agent_creator._get_agents_dir", lambda scope, cwd: fake_dir)
    fields = {"name": "my-agent", "description": "Use when x", "system_prompt": "You are...", "model": "inherit"}
    path = write_agent_definition(fields, scope="user", cwd=".")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "---" in content
    assert "name: my-agent" in content
    assert "You are..." in content


@pytest.mark.asyncio
async def test_generate_agent_from_description_parses_json(monkeypatch):
    """generate_agent_from_description 解析 LLM 返回的 JSON。"""
    from illusion.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent
    from illusion.engine.messages import ConversationMessage, TextBlock
    from illusion.services import agent_creator

    json_text = '{"identifier":"test-runner","whenToUse":"Use this agent when tests","systemPrompt":"You are a test runner"}'

    async def fake_stream(request):
        yield ApiTextDeltaEvent(text=json_text)
        yield ApiMessageCompleteEvent(message=ConversationMessage(role="assistant", content=[TextBlock(text=json_text)]), usage=None, stop_reason="end_turn")

    api_client = MagicMock()
    api_client.stream_message = MagicMock(return_value=fake_stream(None))
    engine = MagicMock()
    engine.api_client = api_client
    engine.model = "test-model"
    engine.max_tokens = 4096

    result = await agent_creator.generate_agent_from_description("write a test runner", model="test-model", existing_identifiers=[], engine=engine)
    assert isinstance(result, GeneratedAgent)
    assert result.identifier == "test-runner"
    assert result.system_prompt == "You are a test runner"


@pytest.mark.asyncio
async def test_generate_agent_inherit_uses_engine_model():
    """model='inherit' 时应回退到 engine.model，而非将 'inherit' 传给 API。"""
    from illusion.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent
    from illusion.engine.messages import ConversationMessage, TextBlock
    from illusion.services import agent_creator

    json_text = '{"identifier":"test","whenToUse":"use when","systemPrompt":"you are"}'

    captured: dict[str, str] = {}

    async def fake_stream(request):
        # 捕获传给 ApiMessageRequest 的 model，验证回退到 engine.model
        captured["model"] = request.model
        yield ApiTextDeltaEvent(text=json_text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=json_text)]),
            usage=None,
            stop_reason="end_turn",
        )

    api_client = MagicMock()
    api_client.stream_message = MagicMock(side_effect=fake_stream)
    engine = MagicMock()
    engine.api_client = api_client
    engine.model = "gpt-4o"
    engine.max_tokens = 4096

    result = await agent_creator.generate_agent_from_description(
        "test prompt", "inherit", [], engine,
    )

    assert captured["model"] == "gpt-4o"
    assert captured["model"] != "inherit"
    assert result.identifier == "test"


def test_get_agents_dir_user_scope(monkeypatch, tmp_path):
    """user scope 返回 <config_dir>/agents（默认 ~/.illusion/agents）。"""
    from illusion.services import agent_creator

    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path))
    result = agent_creator._get_agents_dir("user", ".")
    assert result == tmp_path / "agents"


def test_get_agents_dir_project_scope(tmp_path):
    """project scope 返回 {cwd}/.illusion/agents。"""
    from illusion.services import agent_creator

    cwd = tmp_path / "proj"
    cwd.mkdir()
    result = agent_creator._get_agents_dir("project", cwd)
    assert result == cwd.resolve() / ".illusion" / "agents"


# ===== 模型引用校验 =====


def _patch_settings(monkeypatch, tmp_path):
    """为 validate 构造带两个 env 模型的隔离 settings（含空 agents 目录）。"""
    from illusion.config.settings import Settings

    settings = Settings(
        model="env_1.model_1",
        env_1={
            "api_format": "openai",
            "api_key": "k",
            "model_1": {"name": "model-a"},
            "model_2": {"name": "model-b", "capabilities": ["image"]},
        },
        env_2={
            "api_format": "openai",
            "api_key": "k",
            "model_1": {"name": "model-b"},
        },
    )
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("illusion.config.settings.load_settings", lambda *a, **kw: settings)
    monkeypatch.setattr("illusion.services.agent_creator.get_all_agent_definitions", lambda *a, **kw: [])
    return settings


def test_validate_model_ref_ok(monkeypatch, tmp_path):
    """合法 env_N.model_M 引用通过校验。"""
    _patch_settings(monkeypatch, tmp_path)
    result = validate_agent_definition(
        {"name": "a1", "description": "d", "system_prompt": "s", "model": "env_1.model_2"}, cwd=".")
    assert result == {}


def test_validate_model_bare_name_rejected(monkeypatch, tmp_path):
    """裸模型名不做兼容，直接被拒。"""
    _patch_settings(monkeypatch, tmp_path)
    result = validate_agent_definition(
        {"name": "a1", "description": "d", "system_prompt": "s", "model": "model-b"}, cwd=".")
    assert "model" in result


def test_validate_model_unknown_rejected(monkeypatch, tmp_path):
    """完全未配置的模型被拒（消灭 404 于创建阶段）。"""
    _patch_settings(monkeypatch, tmp_path)
    result = validate_agent_definition(
        {"name": "a1", "description": "d", "system_prompt": "s", "model": "ghost-model"}, cwd=".")
    assert "model" in result


def test_validate_model_inherit_ok(monkeypatch, tmp_path):
    """inherit 通过校验。"""
    _patch_settings(monkeypatch, tmp_path)
    result = validate_agent_definition(
        {"name": "a1", "description": "d", "system_prompt": "s", "model": "inherit"}, cwd=".")
    assert result == {}


# ===== 外科手术式更新 =====


def _write_tmp_agent(tmp_path, content: str) -> Path:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    file_path = agents_dir / "my-agent.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path


def test_update_agent_model_only(tmp_path):
    """仅改 model：其余 frontmatter 字段与 body 原样保留。"""
    from illusion.coordinator.agent_definitions import load_agents_dir
    from illusion.services.agent_creator import update_agent_definition_file

    path = _write_tmp_agent(tmp_path, (
        "---\nname: my-agent\ndescription: Use when x\nmodel: inherit\n"
        "skills: [review]\ncolor: blue\n---\nYou are a test agent.\n"
    ))
    agent = next(a for a in load_agents_dir(path.parent) if a.name == "my-agent")
    update_agent_definition_file(agent, {"model": "env_1.model_2"})

    content = path.read_text(encoding="utf-8")
    assert "model: env_1.model_2" in content
    assert "skills: [review]" in content  # 未管理字段保留
    assert "color: blue" in content
    assert "You are a test agent." in content  # body 保留
    assert "description: Use when x" in content

    # 重新加载确认生效
    updated = next(a for a in load_agents_dir(path.parent) if a.name == "my-agent")
    assert updated.model == "env_1.model_2"


def test_update_agent_multi_line_description_replaced(tmp_path):
    """多行块标量 description 被完整替换（不留残行）。"""
    from illusion.coordinator.agent_definitions import load_agents_dir
    from illusion.services.agent_creator import update_agent_definition_file

    path = _write_tmp_agent(tmp_path, (
        "---\nname: my-agent\ndescription: >-\n  old line one\n  old line two\n"
        "model: inherit\n---\nbody here\n"
    ))
    agent = next(a for a in load_agents_dir(path.parent) if a.name == "my-agent")
    update_agent_definition_file(agent, {"description": "new description"})

    content = path.read_text(encoding="utf-8")
    assert "description: new description" in content
    assert "old line" not in content
    assert "model: inherit" in content


def test_update_agent_max_turns_and_system_prompt(tmp_path):
    """max_turns 新增与 system_prompt（body）替换。"""
    from illusion.coordinator.agent_definitions import load_agents_dir
    from illusion.services.agent_creator import update_agent_definition_file

    path = _write_tmp_agent(tmp_path, (
        "---\nname: my-agent\ndescription: d\nmodel: inherit\n---\nold prompt\n"
    ))
    agent = next(a for a in load_agents_dir(path.parent) if a.name == "my-agent")
    update_agent_definition_file(agent, {"max_turns": 42, "system_prompt": "new prompt"})

    content = path.read_text(encoding="utf-8")
    assert "max_turns: 42" in content
    assert "new prompt" in content
    assert "old prompt" not in content


def test_update_builtin_agent_rejected():
    """内置 agent 无定义文件，更新报 ValueError。"""
    from illusion.coordinator.agent_definitions import get_builtin_agent_definitions
    from illusion.services.agent_creator import update_agent_definition_file

    builtin = get_builtin_agent_definitions()[0]
    with pytest.raises(ValueError):
        update_agent_definition_file(builtin, {"model": "env_1.model_1"})


def test_delete_agent_file(tmp_path):
    """删除用户 agent 的 .md 文件。"""
    from illusion.coordinator.agent_definitions import load_agents_dir
    from illusion.services.agent_creator import delete_agent_definition_file

    path = _write_tmp_agent(tmp_path, "---\nname: my-agent\ndescription: d\n---\nbody\n")
    agent = next(a for a in load_agents_dir(path.parent) if a.name == "my-agent")
    deleted = delete_agent_definition_file(agent)
    assert deleted == path
    assert not path.exists()


def test_delete_builtin_agent_rejected():
    """内置 agent 不可删除。"""
    from illusion.coordinator.agent_definitions import get_builtin_agent_definitions
    from illusion.services.agent_creator import delete_agent_definition_file

    builtin = get_builtin_agent_definitions()[0]
    with pytest.raises(ValueError):
        delete_agent_definition_file(builtin)


@pytest.mark.asyncio
async def test_generate_cross_env_sends_bare_model_name(monkeypatch):
    """跨 env 生成：请求必须携带该 env 的裸模型名，而非 env_N.model_M 引用串。

    回归：引用串被原样发给 provider 会报
    model_invalid "The model \"env_N.model_N\" does not exist" 404。
    """
    from illusion.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent
    from illusion.config.settings import Settings
    from illusion.engine.messages import ConversationMessage, TextBlock
    from illusion.services import agent_creator

    settings = Settings(
        model="env_1.model_1",
        env_1={"api_format": "openai", "api_key": "k", "model_1": {"name": "model-a"}},
        env_2={"api_format": "openai", "api_key": "k", "model_1": {"name": "model-b"}},
    )
    monkeypatch.setattr("illusion.config.settings.load_settings", lambda *a, **kw: settings)

    fake_client = MagicMock()
    captured: dict[str, str] = {}

    async def fake_stream(request):
        captured["model"] = request.model
        json_text = '{"identifier":"x","whenToUse":"use when","systemPrompt":"you are"}'
        yield ApiTextDeltaEvent(text=json_text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=json_text)]),
            usage=None, stop_reason="end_turn",
        )

    fake_client.stream_message = MagicMock(side_effect=fake_stream)
    monkeypatch.setattr(
        "illusion.api.factory.build_api_client_for_env",
        lambda settings, env_key: fake_client,
    )

    engine = MagicMock()
    engine.api_client = MagicMock()
    engine.model = "model-a"  # 当前活跃 env_1 的模型
    engine.max_tokens = 4096

    await agent_creator.generate_agent_from_description(
        "test prompt", "env_2.model_1", [], engine,
    )
    # 请求发的是 env_2 的裸模型名，不是引用串
    assert captured["model"] == "model-b"


@pytest.mark.asyncio
async def test_generate_same_env_uses_bare_model_name(monkeypatch):
    """同 env 指定引用时发送该 env 的裸模型名。"""
    from illusion.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent
    from illusion.config.settings import Settings
    from illusion.engine.messages import ConversationMessage, TextBlock
    from illusion.services import agent_creator

    settings = Settings(
        model="env_1.model_1",
        env_1={
            "api_format": "openai", "api_key": "k",
            "model_1": {"name": "model-a"}, "model_2": {"name": "model-b"},
        },
    )
    monkeypatch.setattr("illusion.config.settings.load_settings", lambda *a, **kw: settings)

    captured: dict[str, str] = {}

    async def fake_stream(request):
        captured["model"] = request.model
        json_text = '{"identifier":"x","whenToUse":"use when","systemPrompt":"you are"}'
        yield ApiTextDeltaEvent(text=json_text)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=json_text)]),
            usage=None, stop_reason="end_turn",
        )

    engine = MagicMock()
    engine.api_client.stream_message = MagicMock(side_effect=fake_stream)
    engine.model = "model-a"
    engine.max_tokens = 4096

    await agent_creator.generate_agent_from_description(
        "test prompt", "env_1.model_2", [], engine,
    )
    assert captured["model"] == "model-b"
