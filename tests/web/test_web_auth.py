"""Web 认证层测试（launch token + 签名 cookie + Bearer/query token）

覆盖维度：
    - 单元级：token 校验（常量时间比较）、authority 规范化、签名 cookie
      签发/校验（篡改、过期、authority 绑定）、签名 secret 持久化
    - 集成级：首页 token→cookie 交换、REST 三路凭据校验、WS 握手认证、
      认证层与浏览器信任栅栏的先后关系（401 先于 403）、dev 模式交换
"""

from __future__ import annotations

import time

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from illusion.ui.web.auth import (
    authority_from_host,
    create_web_auth,
    extract_bearer,
)
from illusion.ui.web.server import create_app

LOOPBACK = "http://127.0.0.1"

# cookie 默认有效期（与 auth.py COOKIE_MAX_AGE_SECONDS 对齐）
AUTH_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000


def _mint_cookie_value(auth, authority: str) -> str:
    """签发 ``name=value`` 形态的 cookie 头片段（供 cookie_valid 直接用）。"""
    name, value, _ = auth.mint_cookie(authority)
    return f"{name}={value}"


# ---- 单元级 ----

class TestUnitAuth:
    def test_launch_token_random_and_stable(self, tmp_path) -> None:
        a = create_web_auth(tmp_path / "s1.json")
        b = create_web_auth(tmp_path / "s2.json")
        assert a.launch_token != b.launch_token
        assert len(a.launch_token) >= 32

    def test_secret_persisted_across_instances(self, tmp_path) -> None:
        path = tmp_path / "secret.json"
        a = create_web_auth(path)
        b = create_web_auth(path)  # 同一文件：secret 复用，token 刷新
        assert a.secret == b.secret
        assert a.launch_token != b.launch_token

    def test_secret_file_created_and_readable(self, tmp_path) -> None:
        path = tmp_path / "secret.json"
        create_web_auth(path)
        assert path.read_text(encoding="utf-8").find('"secret"') != -1

    def test_invalid_secret_file_fails_loudly(self, tmp_path) -> None:
        path = tmp_path / "secret.json"
        path.write_text('{"version": 1, "secret": "!!not-base64url!!"}', encoding="utf-8")
        with pytest.raises(RuntimeError):
            create_web_auth(path)

    def test_unsupported_secret_version_fails_loudly(self, tmp_path) -> None:
        path = tmp_path / "secret.json"
        path.write_text('{"version": 99, "secret": "AAAA"}', encoding="utf-8")
        with pytest.raises(RuntimeError):
            create_web_auth(path)

    def test_token_valid_constant_time(self, tmp_path) -> None:
        auth = create_web_auth(tmp_path / "s.json")
        assert auth.token_valid(auth.launch_token)
        assert not auth.token_valid(None)
        assert not auth.token_valid("")
        assert not auth.token_valid(auth.launch_token[:-1] + ("A" if auth.launch_token[-1] != "A" else "B"))

    def test_authority_from_host(self) -> None:
        assert authority_from_host("127.0.0.1:3000") == "127.0.0.1:3000"
        assert authority_from_host("localhost") == "localhost"
        assert authority_from_host("nas.example:8080") == "nas.example:8080"
        assert authority_from_host("[::1]:3000") == "[::1]:3000"
        assert authority_from_host(None) is None
        assert authority_from_host("") is None

    def test_extract_bearer(self) -> None:
        assert extract_bearer("Bearer abc123") == "abc123"
        assert extract_bearer("bearer  xyz ") == "xyz"
        assert extract_bearer("Basic abc") is None
        assert extract_bearer(None) is None
        assert extract_bearer("Bearer") is None

    def test_authenticated_url_shape(self, tmp_path) -> None:
        auth = create_web_auth(tmp_path / "s.json")
        url = auth.authenticated_url("http://127.0.0.1:3000")
        assert url.startswith("http://127.0.0.1:3000/?token=")
        assert auth.launch_token in url


class TestCookieUnit:
    def test_mint_and_validate(self, tmp_path) -> None:
        auth = create_web_auth(tmp_path / "s.json")
        name, value, max_age = auth.mint_cookie("127.0.0.1:3000")
        assert name == "illusion-auth-"  # 固定名：覆盖更新而非按端口累积
        assert max_age > 0
        assert auth.cookie_valid("127.0.0.1:3000", f"{name}={value}; other=x")

    def test_mint_same_name_across_authorities(self, tmp_path) -> None:
        """回归：动态端口场景（桌面版每次启动随机端口）下，不同 authority
        签发的 cookie 必须同名——同名 cookie 被下一次 token 交换覆盖更新，
        若按端口哈希命名会在浏览器 jar 里无限累积，请求头膨胀到服务器
        上限后 WS 握手被 400 拒绝（遮罩层卡死）。"""
        auth = create_web_auth(tmp_path / "s.json")
        name_a, _, _ = auth.mint_cookie("127.0.0.1:3000")
        name_b, _, _ = auth.mint_cookie("127.0.0.1:58392")
        assert name_a == name_b == "illusion-auth-"
        # authority 绑定仍在 payload：跨端口借用同一 cookie 拒绝
        assert not auth.cookie_valid("127.0.0.1:58392", f"{name_a}=x")
        cookie_a = _mint_cookie_value(auth, "127.0.0.1:3000")
        assert auth.cookie_valid("127.0.0.1:3000", cookie_a)
        assert not auth.cookie_valid("127.0.0.1:58392", cookie_a)

    def test_tampered_cookie_rejected(self, tmp_path) -> None:
        auth = create_web_auth(tmp_path / "s.json")
        name, value, _ = auth.mint_cookie("127.0.0.1")
        bad = value[:-1] + ("A" if value[-1] != "A" else "B")
        assert not auth.cookie_valid("127.0.0.1", f"{name}={bad}")

    def test_authority_bound_cookie(self, tmp_path) -> None:
        auth = create_web_auth(tmp_path / "s.json")
        name, value, _ = auth.mint_cookie("127.0.0.1")
        # 同 secret、同名字，但 authority 不同 → 拒绝
        assert not auth.cookie_valid("localhost", f"{name}={value}")
        assert auth.cookie_valid("127.0.0.1", f"{name}={value}")

    def test_expired_cookie_rejected(self, tmp_path) -> None:
        auth = create_web_auth(tmp_path / "s.json")
        past_ms = int(time.time() * 1000) - AUTH_MAX_AGE_MS - 60_000
        name, value, _ = auth.mint_cookie("127.0.0.1", now=past_ms)
        assert not auth.cookie_valid("127.0.0.1", f"{name}={value}")

    def test_different_secret_rejects(self, tmp_path) -> None:
        a = create_web_auth(tmp_path / "s1.json")
        b = create_web_auth(tmp_path / "s2.json")
        name, value, _ = a.mint_cookie("127.0.0.1")
        assert not b.cookie_valid("127.0.0.1", f"{name}={value}")


# ---- 集成级 ----

@pytest.fixture
def auth(tmp_path):
    return create_web_auth(secret_path=tmp_path / "auth-secret.json")


@pytest.fixture
def app(auth):
    return create_app(dev=True, auth=auth)


@pytest.fixture
def client(app):
    return TestClient(app, base_url=LOOPBACK)


@pytest.fixture
def prod_app(auth):
    return create_app(dev=False, auth=auth)


@pytest.fixture
def prod_client(prod_app):
    return TestClient(prod_app, base_url=LOOPBACK)


@pytest.fixture
def launch_token(auth):
    return auth.launch_token


def _exchange_cookie(client, token: str, path="/", host: str | None = None) -> str | None:
    """执行 token→cookie 交换，返回签发的 ``name=value``（或 None）。"""
    headers = {"Host": host} if host else {}
    resp = client.get(f"{path}?token={token}", follow_redirects=False, headers=headers)
    assert resp.status_code in (303, 200)
    sc = resp.headers.get("set-cookie")
    if not sc:
        return None
    return sc.split(";", 1)[0]


class TestIndexExchange:
    def test_swap_token_for_cookie_and_redirect(self, prod_client, launch_token):
        resp = prod_client.get(f"/?token={launch_token}", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        sc = resp.headers["set-cookie"]
        assert sc.startswith("illusion-auth-")
        # 浏览器会话 cookie 的安全属性
        assert "HttpOnly" in sc and "SameSite=Strict" in sc and "Path=/" in sc

    def test_no_token_gets_401(self, prod_client):
        resp = prod_client.get("/", follow_redirects=False)
        assert resp.status_code == 401

    def test_bad_token_gets_401(self, prod_client):
        resp = prod_client.get("/?token=not-the-token", follow_redirects=False)
        assert resp.status_code == 401

    def test_cookie_passes_index_middleware(self, prod_client, launch_token):
        # dev=False 无 dist 时应用层 404；关键是被认证层放行（非 401/303）
        cookie = _exchange_cookie(prod_client, launch_token)
        assert cookie is not None
        resp = prod_client.get("/", headers={"Cookie": cookie})
        assert resp.status_code != 401

    def test_dev_mode_exchange_ok(self, tmp_path):
        auth_dev = create_web_auth(tmp_path / "d.json")
        app_dev = create_app(dev=True, auth=auth_dev)
        c = TestClient(app_dev, base_url=LOOPBACK)
        resp = c.get(f"/?token={auth_dev.launch_token}", follow_redirects=False)
        assert resp.status_code == 200
        assert "set-cookie" in resp.headers

    def test_redirect_flow_followed(self, app, client, launch_token):
        # follow_redirects=True：303 跟随后 cookie 自动携带，dev 下拿到 200 交换页
        resp = client.get(f"/?token={launch_token}")
        assert resp.status_code == 200

    def test_dynamic_port_exchange_keeps_cookie_name(self, client, launch_token):
        """回归：桌面版每次启动随机端口（authority 变化），多次 token 交换
        签发的 cookie 名必须恒定——同名覆盖更新，浏览器 jar 不累积；
        按端口哈希的旧命名会无限堆积 cookie，请求头膨胀至服务器上限后
        WS 握手 400 拒绝、界面卡在遮罩层。"""
        names: list[str] = []
        for port in ("3000", "58392", "61021", "45011"):
            cookie = _exchange_cookie(client, launch_token, host=f"127.0.0.1:{port}")
            assert cookie is not None
            names.append(cookie.split("=", 1)[0])
        assert len(set(names)) == 1
        assert names[0] == "illusion-auth-"


class TestRestAuth:
    def test_bearer_header_passes(self, client, launch_token):
        resp = client.get("/api/envs", headers={"Authorization": f"Bearer {launch_token}"})
        assert resp.status_code == 200

    def test_query_token_passes(self, client, launch_token):
        assert client.get(f"/api/envs?token={launch_token}").status_code == 200

    def test_cookie_passes(self, app, client, auth, launch_token):
        cookie = _exchange_cookie(client, launch_token)
        assert cookie is not None
        resp = client.get("/api/envs", headers={"Cookie": cookie})
        assert resp.status_code == 200

    def test_no_credentials_401(self, client):
        assert client.get("/api/envs").status_code == 401
        assert client.post("/api/envs", json={}).status_code == 401

    def test_wrong_token_401(self, client):
        resp = client.get("/api/envs", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401
        assert client.get("/api/envs?token=nope").status_code == 401

    def test_authorization_request_before_fence(self, client):
        # 无凭据 + 非法 Host：认证失败优先（401），而非栅栏的 403
        resp = client.get("/api/envs", headers={"Host": "evil.example"})
        assert resp.status_code == 401

    def test_fence_still_guards_after_auth(self, client, launch_token):
        # 带有效凭据 + DNS rebinding Host：栅栏兜底 403
        resp = client.get(
            "/api/envs",
            headers={"Host": "evil.example", "Authorization": f"Bearer {launch_token}"},
        )
        assert resp.status_code == 403


class TestWsAuth:
    def test_cookie_handshake_passes(self, app, client, launch_token):
        cookie = _exchange_cookie(client, launch_token)
        assert cookie is not None
        with client.websocket_connect(
            "/ws",
            headers={"Host": "127.0.0.1", "Cookie": cookie},
        ):
            pass

    def test_query_token_handshake_passes(self, client, launch_token):
        with client.websocket_connect(
            f"/ws?token={launch_token}",
            headers={"Host": "127.0.0.1"},
        ):
            pass

    def test_bearer_handshake_passes(self, client, launch_token):
        with client.websocket_connect(
            "/ws",
            headers={"Host": "127.0.0.1", "Authorization": f"Bearer {launch_token}"},
        ):
            pass

    def test_no_credentials_refused(self, client):
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws", headers={"Host": "127.0.0.1"}),
        ):
            pass

    def test_wrong_token_refused(self, client):
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws?token=bogus", headers={"Host": "127.0.0.1"}),
        ):
            pass