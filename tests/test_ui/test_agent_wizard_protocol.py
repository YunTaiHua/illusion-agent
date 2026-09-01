""" agent 向导协议模型测试 """
from __future__ import annotations

from illusion.ui.protocol import BackendEvent, FrontendRequest


def test_agent_wizard_init_request():
    req = FrontendRequest(type="agent_wizard_init")
    assert req.type == "agent_wizard_init"


def test_agent_wizard_init_response():
    ev = BackendEvent(type="agent_wizard_init_response", tools=[{"name": "read"}], models=[{"value": "inherit"}])
    assert ev.type == "agent_wizard_init_response"
    assert ev.tools == [{"name": "read"}]
    assert ev.models == [{"value": "inherit"}]


def test_agent_generate_request():
    req = FrontendRequest(type="agent_generate_request", prompt="test runner", model="inherit", request_id="g1")
    assert req.prompt == "test runner"
    assert req.request_id == "g1"


def test_agent_generate_response():
    ev = BackendEvent(type="agent_generate_response", request_id="g1", agent={"identifier": "x", "when_to_use": "y", "system_prompt": "z"})
    assert ev.agent["identifier"] == "x"


def test_agent_wizard_submit():
    req = FrontendRequest(type="agent_wizard_submit", fields={"name": "foo"}, scope="user")
    assert req.fields == {"name": "foo"}
    assert req.scope == "user"


def test_agent_wizard_result():
    ev = BackendEvent(type="agent_wizard_result", success=True, path="/tmp/a.md")
    assert ev.success is True
    assert ev.path == "/tmp/a.md"


from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_agent_wizard_init_returns_tools_models(monkeypatch, tmp_path):
    from illusion.ui.backend_host import ReactBackendHost

    host = ReactBackendHost.__new__(ReactBackendHost)
    host._bundle = MagicMock()
    host._bundle.cwd = str(tmp_path)
    host._bundle.tool_registry = MagicMock()
    captured = {}
    async def fake_emit(ev):
        captured["ev"] = ev
    host._emit = fake_emit

    monkeypatch.setattr("illusion.ui.backend_host.list_available_tools", lambda tr: [{"value": "read"}])
    monkeypatch.setattr("illusion.ui.backend_host.list_available_models", lambda app_state=None: [{"value": "inherit"}])

    await host._handle_agent_wizard_init(FrontendRequest(type="agent_wizard_init"))
    assert captured["ev"].type == "agent_wizard_init_response"
    assert captured["ev"].tools == [{"value": "read"}]
    assert captured["ev"].models == [{"value": "inherit"}]


@pytest.mark.asyncio
async def test_agent_wizard_submit_validates_and_writes(monkeypatch, tmp_path):
    from illusion.ui.backend_host import ReactBackendHost

    host = ReactBackendHost.__new__(ReactBackendHost)
    host._bundle = MagicMock()
    host._bundle.cwd = str(tmp_path)
    captured = {}
    async def fake_emit(ev):
        captured["ev"] = ev
    host._emit = fake_emit

    monkeypatch.setattr("illusion.ui.backend_host.validate_agent_definition", lambda f, cwd: {})
    monkeypatch.setattr("illusion.ui.backend_host.write_agent_definition", lambda f, scope, cwd: tmp_path / "a.md")

    req = FrontendRequest(type="agent_wizard_submit", fields={"name": "x", "description": "y", "system_prompt": "z", "model": "inherit"}, scope="user")
    await host._handle_agent_wizard_submit(req)
    assert captured["ev"].type == "agent_wizard_result"
    assert captured["ev"].success is True


@pytest.mark.asyncio
async def test_agent_generate_returns_generated_agent(monkeypatch, tmp_path):
    from illusion.services.agent_creator import GeneratedAgent
    from illusion.ui.backend_host import ReactBackendHost

    host = ReactBackendHost.__new__(ReactBackendHost)
    host._bundle = MagicMock()
    host._bundle.cwd = str(tmp_path)
    host._bundle.engine = MagicMock()
    captured = {}
    async def fake_emit(ev):
        captured["ev"] = ev
    host._emit = fake_emit

    async def fake_gen(prompt, model, existing, engine, abort_signal=None):
        return GeneratedAgent(identifier="x", when_to_use="y", system_prompt="z")
    monkeypatch.setattr("illusion.ui.backend_host.generate_agent_from_description", fake_gen)

    req = FrontendRequest(type="agent_generate_request", prompt="test", model="inherit", request_id="g1")
    await host._handle_agent_generate_request(req)
    assert captured["ev"].type == "agent_generate_response"
    assert captured["ev"].agent["identifier"] == "x"


# ===== agent 管理（web 设置表单 AgentsTab）协议 =====


def test_web_request_agents_type():
    req = FrontendRequest(type="web_request_agents")
    assert req.type == "web_request_agents"


def test_web_update_agent_request():
    req = FrontendRequest(type="web_update_agent", fields={"name": "explore", "source": "builtin", "model": "env_1.model_1"})
    assert req.fields["source"] == "builtin"
    assert req.fields["model"] == "env_1.model_1"


def test_web_delete_agent_request():
    req = FrontendRequest(type="web_delete_agent", fields={"name": "my-agent", "base_dir": "/tmp/agents"})
    assert req.fields["base_dir"] == "/tmp/agents"


def test_web_agents_event_payload():
    ev = BackendEvent(type="web_agents", web_agents={
        "global": [{"name": "explore", "source": "builtin", "scope": "builtin", "model": "env_1.model_1"}],
        "projects": [{"workspace": "/ws", "name": "ws", "is_default": True, "available": True, "agents": []}],
    })
    assert ev.web_agents["global"][0]["name"] == "explore"
    assert ev.web_agents["projects"][0]["is_default"] is True


def test_web_agent_op_result_event():
    ev = BackendEvent(type="web_agent_op_result", web_agent_op="update", success=False, error="boom")
    assert ev.web_agent_op == "update"
    assert ev.success is False
    assert ev.error == "boom"


def test_agent_wizard_submit_cwd():
    """项目级创建向导提交可指定目标工作区 cwd。"""
    req = FrontendRequest(type="agent_wizard_submit", fields={"name": "p1"}, scope="project", cwd="/ws/project-a")
    assert req.cwd == "/ws/project-a"
