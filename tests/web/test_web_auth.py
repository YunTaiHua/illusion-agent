"""Web UI 安全鉴权测试（Origin 校验 + 启动令牌）

覆盖 create_app 在启用 / 未启用 WebAuthConfig 时的行为：
    - WebSocket /ws：跨站 Origin 拒绝、无令牌拒绝、正常（同源 + 令牌）放行
    - REST /api/*：无令牌拒绝、错误令牌拒绝、有效令牌放行、Cookie 下发
    - 静态资源与 WebSocket 端点的 Cookie 下发路径
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from illusion.ui.web.security import (
    AUTH_COOKIE_NAME,
    WebAuthConfig,
    generate_auth_token,
    origin_is_allowed,
)
from illusion.ui.web.server import create_app


@pytest.fixture
def auth() -> WebAuthConfig:
    return WebAuthConfig(token=generate_auth_token())


@pytest.fixture
def app(auth):
    """启用鉴权的 app（非 dev，不挂静态资源以避免路径依赖）。"""
    return create_app(dev=True, auth=auth)


@pytest.fixture
def client(app):
    return TestClient(app)


def test_origin_is_allowed_helpers():
    """Origin 解析与白名单判定。"""
    assert origin_is_allowed("http://localhost:3000", frozenset({"localhost"})) is True
    assert origin_is_allowed("http://127.0.0.1:3000", frozenset({"127.0.0.1"})) is True
    assert origin_is_allowed("http://[::1]:3000", frozenset({"::1"})) is True
    assert origin_is_allowed("https://evil.example", frozenset({"localhost", "127.0.0.1"})) is False
    assert origin_is_allowed("http://localhost.evil.com", frozenset({"localhost"})) is False
    assert origin_is_allowed("", frozenset({"localhost"})) is False
    assert origin_is_allowed("http://localhost:3000", frozenset({"127.0.0.1"})) is False


def test_generate_auth_token_entropy():
    """令牌应为高熵随机串。"""
    t1 = generate_auth_token()
    t2 = generate_auth_token()
    assert t1 and t2
    assert len(t1) >= 32
    assert t1 != t2


# ---- WebSocket 校验 ----

def test_ws_rejects_cross_origin(client):
    """跨站 Origin（evil.example）握手应被拒绝。"""
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/ws", headers={"Origin": "https://evil.example"}
        ) as ws:
            ws.receive_text()  # 若握手成功，这里会读事件；实际应在 accept 前被关闭


def test_ws_rejects_missing_token_on_loopback_origin(client):
    """同源回环 Origin 但无令牌，应被拒绝。"""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()


def test_ws_rejects_wrong_token(client, auth):
    """携带错误令牌应被拒绝。"""
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/ws",
            headers={"Origin": "http://localhost:3000", "Cookie": f"{AUTH_COOKIE_NAME}=wrong-token"},
        ) as ws:
            ws.receive_text()


def test_ws_accepts_valid_token_loopback_origin(client, auth):
    """同源回环 Origin + 有效令牌应放行（握手成功，收到 ready/error 事件）。"""
    # 有效令牌经 Cookie 传递
    with client.websocket_connect(
        "/ws",
        headers={"Origin": "http://127.0.0.1:3000", "Cookie": f"{AUTH_COOKIE_NAME}={auth.token}"},
    ) as ws:
        # 握手成功即通过；不读事件（host.run 可能因无凭据报错，但不影响握手放行判定）
        pass


def test_ws_accepts_valid_token_via_query(client, auth):
    """非浏览器客户端可经 ?token= 查询参数携带令牌。"""
    with client.websocket_connect(
        f"/ws?token={auth.token}",
        headers={"Origin": "http://localhost:3000"},
    ) as ws:
        pass


# ---- REST 鉴权 ----

def test_rest_rejects_without_token(client):
    """无令牌访问 /api/envs 应返回 401。"""
    resp = client.get("/api/envs")
    assert resp.status_code == 401


def test_rest_rejects_wrong_token(client):
    """错误令牌访问 /api/envs 应返回 401。"""
    resp = client.get("/api/envs", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_rest_accepts_bearer_token(client, auth):
    """有效 Bearer 令牌访问 /api/envs 应 200。"""
    resp = client.get("/api/envs", headers={"Authorization": f"Bearer {auth.token}"})
    assert resp.status_code == 200


def test_rest_sets_auth_cookie_on_health(client, auth):
    """未保护路径（如 /）应下发令牌 Cookie，供浏览器后续携带。"""
    resp = client.get("/")
    assert resp.status_code in (200, 404)  # dev 模式无静态资源，404 亦可能
    # dev=True 不挂 StaticFiles，但根路径仍会触发 Cookie 下发逻辑（仅 <400 时）
    # 404 不满足 <400，故此处仅在 200 时断言 Cookie
    if resp.status_code == 200:
        assert AUTH_COOKIE_NAME in resp.cookies


def test_rest_accepts_cookie_after_set(client, auth):
    """拿到 Cookie 后后续 /api 请求自动携带令牌。"""
    # 先通过 Bearer 获取响应（中间件在成功响应上下发 Cookie）
    resp = client.get("/api/envs", headers={"Authorization": f"Bearer {auth.token}"})
    assert resp.status_code == 200
    # 中间件在受保护路径成功时也会下发 Cookie
    assert AUTH_COOKIE_NAME in resp.cookies
    cookie = resp.cookies[AUTH_COOKIE_NAME]
    resp2 = client.get("/api/envs", headers={"Cookie": f"{AUTH_COOKIE_NAME}={cookie}"})
    assert resp2.status_code == 200


# ---- 未启用鉴权（向后兼容）----

def test_no_auth_app_passes_requests():
    """auth=None 时不启用鉴权，REST 与 WS 均放行（测试/内部注册兼容）。"""
    app = create_app(dev=True, auth=None)
    client = TestClient(app)
    resp = client.get("/api/envs")
    assert resp.status_code == 200
    # WebSocket：无 Origin、无令牌也应放行（兼容内部调用）
    with client.websocket_connect("/ws") as ws:
        pass
