"""web 子智能体管理 handler 行为测试（安全边界：目录白名单/模型校验/空工具语义）"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.ui.web.ws_web_api import WebApiDispatcher


def make_dispatcher() -> WebApiDispatcher:
    return WebApiDispatcher.__new__(WebApiDispatcher)


@pytest.fixture()
def isolated_agents_env(monkeypatch, tmp_path):
    """隔离 settings/工作区注册表：用户 agents 目录 + 一个注册工作区。"""
    from illusion.config.settings import Settings

    user_agents = tmp_path / "cfg" / "agents"
    user_agents.mkdir(parents=True)
    ws = tmp_path / "ws1"
    ws.mkdir()
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        "illusion.config.paths.get_config_dir",
        lambda: tmp_path / "cfg",
    )
    monkeypatch.setattr(
        "illusion.config.settings.load_settings",
        lambda *a, **kw: Settings(
            model="env_1.model_1",
            env_1={"api_format": "openai", "api_key": "k",
                   "model_1": {"name": "model-a"}},
        ),
    )
    monkeypatch.setattr(
        "illusion.services.workspace_registry.resolve_workspace_views",
        lambda: [{
            "path": str(ws), "name": "ws1", "is_default": False, "available": True,
        }],
    )
    return user_agents, ws


def _write_agent(agents_dir: Path, name: str = "my-agent") -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: when x\nmodel: inherit\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def test_update_rejects_dir_outside_allowlist(isolated_agents_env, tmp_path):
    """任意目录的 .md（非用户级/工作区 agents 目录）拒绝更新。"""
    dispatcher = make_dispatcher()
    rogue = tmp_path / "elsewhere"
    _write_agent(rogue)
    ok, err = dispatcher._update_agent_on_disk(
        {"name": "my-agent", "base_dir": str(rogue), "model": "env_1.model_1"})
    assert not ok and "不在允许" in err
    # 文件未被修改
    assert "model: env_1.model_1" not in _write_agent(rogue).read_text(encoding="utf-8")


def test_delete_rejects_dir_outside_allowlist(isolated_agents_env, tmp_path):
    """任意目录的 .md 拒绝删除（防任意文件删除）。"""
    dispatcher = make_dispatcher()
    rogue = tmp_path / "elsewhere"
    path = _write_agent(rogue)
    ok, err = dispatcher._delete_agent_on_disk(
        {"name": "my-agent", "base_dir": str(rogue)})
    assert not ok and "不在允许" in err
    assert path.exists()


def test_update_user_agent_ok(isolated_agents_env):
    """用户级 agents 目录内更新通过（模型写引用）。"""
    dispatcher = make_dispatcher()
    user_agents, _ws = isolated_agents_env
    path = _write_agent(user_agents)
    ok, err = dispatcher._update_agent_on_disk(
        {"name": "my-agent", "base_dir": str(user_agents), "model": "env_1.model_1"})
    assert ok, err
    assert "model: env_1.model_1" in path.read_text(encoding="utf-8")


def test_update_project_agent_ok(isolated_agents_env):
    """注册工作区的项目级 agents 目录内更新通过。"""
    dispatcher = make_dispatcher()
    _user, ws = isolated_agents_env
    agents_dir = ws / ".illusion" / "agents"
    path = _write_agent(agents_dir)
    ok, err = dispatcher._update_agent_on_disk(
        {"name": "my-agent", "base_dir": str(agents_dir), "model": "env_1.model_1"})
    assert ok, err
    assert "model: env_1.model_1" in path.read_text(encoding="utf-8")


def test_update_rejects_bare_model_name(isolated_agents_env):
    """磁盘分支同样拒绝裸模型名（与创建/内置分支一致）。"""
    dispatcher = make_dispatcher()
    user_agents, _ws = isolated_agents_env
    _write_agent(user_agents)
    ok, err = dispatcher._update_agent_on_disk(
        {"name": "my-agent", "base_dir": str(user_agents), "model": "model-a"})
    assert not ok and "未知模型" in err


def test_update_tools_null_removes_key(isolated_agents_env):
    """tools 更新为 null 时删除 tools 键（恢复"不限制工具"语义）。"""
    dispatcher = make_dispatcher()
    user_agents, _ws = isolated_agents_env
    path = _write_agent(user_agents)
    # 先写入具体工具列表
    dispatcher._update_agent_on_disk(
        {"name": "my-agent", "base_dir": str(user_agents), "tools": ["bash"]})
    assert "tools: [bash]" in path.read_text(encoding="utf-8")
    # 再置 null → 删键
    ok, err = dispatcher._update_agent_on_disk(
        {"name": "my-agent", "base_dir": str(user_agents), "tools": None})
    assert ok, err
    assert "tools:" not in path.read_text(encoding="utf-8")


@pytest.fixture()
def builtin_host(isolated_agents_env):
    """内置分支测试用 host：捕获 emit 事件。"""
    dispatcher = make_dispatcher()
    dispatcher._host = MagicMock()
    dispatcher._host._bundle = MagicMock()
    dispatcher.events = []

    async def fake_emit(ev, session_id=None):
        dispatcher.events.append(ev)

    dispatcher._emit = fake_emit
    dispatcher._push_agents = AsyncNoop()
    dispatcher._refresh_resources_after_agent_op = AsyncNoop()
    return dispatcher


@pytest.mark.asyncio
async def test_update_builtin_unknown_name_rejected(builtin_host):
    """内置分支拒绝不存在的内置 agent 名（防 settings 污染）。"""
    from illusion.ui.protocol import FrontendRequest

    await builtin_host.handle_web_update_agent(
        FrontendRequest(type="web_update_agent",
                        fields={"name": "ghost-agent", "source": "builtin",
                                "model": "env_1.model_1"}))
    assert builtin_host.events and builtin_host.events[0].type == "web_agent_op_result"
    assert builtin_host.events[0].success is False
    assert "不是内置" in builtin_host.events[0].error


@pytest.mark.asyncio
async def test_update_builtin_invalid_model_rejected(builtin_host):
    """内置分支拒绝裸模型名。"""
    from illusion.ui.protocol import FrontendRequest

    await builtin_host.handle_web_update_agent(
        FrontendRequest(type="web_update_agent",
                        fields={"name": "explore", "source": "builtin",
                                "model": "model-a"}))
    assert builtin_host.events and builtin_host.events[0].type == "web_agent_op_result"
    assert builtin_host.events[0].success is False
    assert "未知模型" in builtin_host.events[0].error


class AsyncNoop:
    async def __call__(self, *a, **k):
        return None


def test_agents_catalog_includes_goal_verifier_builtin(isolated_agents_env, monkeypatch):
    """web_agents 目录的内置组包含 goal-verifier（独立条目、scope=builtin）。"""
    dispatcher = make_dispatcher()
    _user, _ws = isolated_agents_env
    monkeypatch.setattr(
        "illusion.services.workspace_registry.resolve_workspace_views",
        list,
    )
    catalog = dispatcher._collect_agents_catalog()
    builtin_names = {
        e["name"]
        for e in catalog["global"]
        if e.get("scope") == "builtin"
    }
    assert "goal-verifier" in builtin_names
    assert "verification" in builtin_names
    goal_entry = next(
        e for e in catalog["global"] if e["name"] == "goal-verifier")
    assert goal_entry["scope"] == "builtin"
    # 模型配置键读取 agent_models["goal-verifier"]（未配置时为 None）
    assert goal_entry["model"] is None


def test_agents_catalog_no_duplicate_goal_verifier(isolated_agents_env, monkeypatch):
    """用户自建 goal-verifier 时内置组不再附加同名条目。"""
    dispatcher = make_dispatcher()
    user_agents, _ws = isolated_agents_env
    _write_agent(user_agents, name="goal-verifier")
    monkeypatch.setattr(
        "illusion.services.workspace_registry.resolve_workspace_views",
        list,
    )
    catalog = dispatcher._collect_agents_catalog()
    matches = [e for e in catalog["global"] if e["name"] == "goal-verifier"]
    assert len(matches) == 1
    assert matches[0]["scope"] == "user"


def test_goal_verifier_entry_has_goal_specific_flag(isolated_agents_env, monkeypatch):
    """goal-verifier 条目携带 goal_specific=True；其余内置为 False。"""
    dispatcher = make_dispatcher()
    _user, _ws = isolated_agents_env
    monkeypatch.setattr(
        "illusion.services.workspace_registry.resolve_workspace_views",
        lambda: [],
    )
    catalog = dispatcher._collect_agents_catalog()
    entries = {e["name"]: e for e in catalog["global"]}
    assert entries["goal-verifier"]["goal_specific"] is True
    assert entries["verification"]["goal_specific"] is False
    assert entries["explore"]["goal_specific"] is False
