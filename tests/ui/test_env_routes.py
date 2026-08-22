"""env_routes FastAPI 路由测试。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from illusion.ui.web.env_routes import register_env_routes


@pytest.fixture
def app(tmp_path, monkeypatch):
    """创建带 env 路由的 FastAPI 测试 app。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    app = FastAPI()
    register_env_routes(app, host_config=None)
    return app


@pytest.fixture
def client(app):
    # 浏览器信任栅栏要求 Host 为回环地址（TestClient 默认 host 是 testserver）
    return TestClient(app, base_url="http://127.0.0.1")


def test_get_envs_returns_empty_when_no_config(client):
    """无 env 配置时 GET /api/envs 返回空列表。"""
    resp = client.get("/api/envs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["envs"] == []
    assert data["active_env_key"] is None


def test_create_env_returns_env_key(client):
    """POST /api/envs 创建新 env 返回 env_key。"""
    resp = client.post("/api/envs", json={
        "api_format": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
        "model_1": "claude-sonnet-4-6",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["env_key"].startswith("env_")
    assert data["success"] is True


def test_get_envs_after_create(client):
    """创建 env 后 GET /api/envs 返回该 env。"""
    client.post("/api/envs", json={
        "api_format": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
        "model_1": "claude-sonnet-4-6",
    })
    resp = client.get("/api/envs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["envs"]) == 1
    assert data["envs"][0]["api_format"] == "anthropic"
    assert data["envs"][0]["has_credential"] is True


def test_delete_env(client):
    """DELETE /api/envs/{env_key} 删除 env。"""
    create_resp = client.post("/api/envs", json={
        "api_format": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-test2",
        "model_1": "gpt-4",
    })
    env_key = create_resp.json()["env_key"]
    # 创建第二个 env 并激活，以便删除第一个
    create_resp2 = client.post("/api/envs", json={
        "api_format": "anthropic",
        "base_url": "",
        "api_key": "sk-test3",
        "model_1": "claude-sonnet-4-6",
    })
    env_key2 = create_resp2.json()["env_key"]
    client.post(f"/api/envs/{env_key2}/activate")

    resp = client.delete(f"/api/envs/{env_key}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_delete_active_env_rejected(client):
    """DELETE 激活的 env 应被拒绝。"""
    create_resp = client.post("/api/envs", json={
        "api_format": "anthropic",
        "api_key": "sk-test",
        "model_1": "claude-sonnet-4-6",
    })
    env_key = create_resp.json()["env_key"]
    # 第一个 env 自动激活
    resp = client.delete(f"/api/envs/{env_key}")
    assert resp.status_code == 400


def test_activate_env(client):
    """POST /api/envs/{env_key}/activate 切换 active env。"""
    create_resp1 = client.post("/api/envs", json={
        "api_format": "anthropic",
        "api_key": "sk-test1",
        "model_1": "claude-sonnet-4-6",
    })
    create_resp1.json()["env_key"]
    create_resp2 = client.post("/api/envs", json={
        "api_format": "openai",
        "api_key": "sk-test2",
        "model_1": "gpt-4",
    })
    env_key2 = create_resp2.json()["env_key"]

    resp = client.post(f"/api/envs/{env_key2}/activate")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # 验证 active env 已切换
    get_resp = client.get("/api/envs")
    assert get_resp.json()["active_env_key"] == env_key2


def test_update_env(client):
    """PATCH /api/envs/{env_key} 修改 env 字段。"""
    create_resp = client.post("/api/envs", json={
        "api_format": "anthropic",
        "api_key": "sk-old",
        "model_1": "claude-sonnet-4-6",
    })
    env_key = create_resp.json()["env_key"]

    resp = client.patch(f"/api/envs/{env_key}", json={
        "api_key": "sk-new",
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_update_ui_language(client):
    """PATCH /api/settings/ui_language 修改界面语言。"""
    resp = client.patch("/api/settings/ui_language", json={
        "ui_language": "en-US",
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_oauth_start_copilot(client, monkeypatch):
    """POST /api/oauth/copilot/start 启动 device flow。"""
    def _fake_start(self):
        return {
            "device_code": "test-device-code",
            "user_code": "TEST-CODE",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }
    monkeypatch.setattr("illusion.auth.copilot.CopilotAuth.start_device_flow", _fake_start)

    resp = client.post("/api/oauth/copilot/start")
    assert resp.status_code == 200
    data = resp.json()
    assert "device_code" in data
    assert "verification_uri" in data


def test_oauth_poll_copilot_success(client, monkeypatch):
    """POST /api/oauth/copilot/poll 轮询成功。"""
    def _fake_poll(self, device_code):
        return True
    monkeypatch.setattr("illusion.auth.copilot.CopilotAuth.poll_for_token", _fake_poll)

    resp = client.post("/api/oauth/copilot/poll", json={"device_code": "test-code"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_oauth_poll_codex_success(client, monkeypatch):
    """POST /api/oauth/codex/poll 轮询成功。"""
    def _fake_poll(self, device_code):
        return True
    monkeypatch.setattr("illusion.auth.codex_oauth.CodexOAuth.poll_for_token", _fake_poll)

    resp = client.post("/api/oauth/codex/poll", json={"device_code": "test-code"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_oauth_poll_missing_device_code_returns_422(client):
    """POST /api/oauth/copilot/poll 缺少 device_code 返回 422。"""
    resp = client.post("/api/oauth/copilot/poll", json={})
    assert resp.status_code == 422


def test_oauth_start_unknown_provider_returns_400(client):
    """POST /api/oauth/unknown/start 返回 400。"""
    resp = client.post("/api/oauth/unknown/start")
    assert resp.status_code == 400


def test_update_env_add_models(client):
    """PATCH /api/envs/{env_key} 添加模型。"""
    create_resp = client.post("/api/envs", json={
        "api_format": "anthropic",
        "api_key": "sk-test",
        "model_1": "claude-sonnet-4-6",
    })
    env_key = create_resp.json()["env_key"]

    resp = client.patch(f"/api/envs/{env_key}", json={
        "add_models": [{"key": "model_2", "value": "claude-haiku-3-5"}],
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # 验证模型已添加
    get_resp = client.get("/api/envs")
    env = next(e for e in get_resp.json()["envs"] if e["env_key"] == env_key)
    assert "model_2" in env["models"]


def test_update_env_remove_models(client):
    """PATCH /api/envs/{env_key} 删除模型。"""
    create_resp = client.post("/api/envs", json={
        "api_format": "anthropic",
        "api_key": "sk-test",
        "model_1": "claude-sonnet-4-6",
        "model_2": "claude-haiku-3-5",
    })
    env_key = create_resp.json()["env_key"]

    resp = client.patch(f"/api/envs/{env_key}", json={
        "remove_models": ["model_2"],
    })
    assert resp.status_code == 200

    # 验证模型已删除
    get_resp = client.get("/api/envs")
    env = next(e for e in get_resp.json()["envs"] if e["env_key"] == env_key)
    assert "model_2" not in env["models"]


def test_activate_unknown_env_returns_404(client):
    """POST /api/envs/env_99/activate 不存在的 env 返回 404。"""
    resp = client.post("/api/envs/env_99/activate")
    assert resp.status_code == 404


def test_update_ui_language_invalid_returns_422(client):
    """PATCH /api/settings/ui_language 非法值返回 422。"""
    resp = client.patch("/api/settings/ui_language", json={"ui_language": "fr-FR"})
    assert resp.status_code == 422


def test_get_settings_returns_default_theme(client):
    """GET /api/settings 响应含 theme 字段，默认为 light。"""
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["theme"] == "light"


def test_update_theme(client):
    """PATCH /api/settings/theme 合法值修改主题并持久化。"""
    # 修改为 dark
    resp = client.patch("/api/settings/theme", json={"theme": "dark"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # 回读验证
    get_resp = client.get("/api/settings")
    assert get_resp.json()["theme"] == "dark"
    # 修改为 system
    resp = client.patch("/api/settings/theme", json={"theme": "system"})
    assert resp.status_code == 200
    get_resp = client.get("/api/settings")
    assert get_resp.json()["theme"] == "system"


def test_update_theme_invalid_returns_422(client):
    """PATCH /api/settings/theme 非法值返回 422。"""
    resp = client.patch("/api/settings/theme", json={"theme": "blue"})
    assert resp.status_code == 422
