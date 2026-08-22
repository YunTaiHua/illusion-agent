"""浏览器信任栅栏测试

覆盖维度：
    - 单元级：loopback 分类、authority 解析/规范化/断言、三栅栏判定矩阵
    - 集成级：REST /api/* 栅栏中间件与 /ws 握手栅栏
      （DNS rebinding Host 拒绝、Sec-Fetch-Site 跨站拒绝、Origin 严格同源、
       受信主机声明、REST 特权平面钉死回环、静态资源不设防）
"""

from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from illusion.ui.web.security import (
    assert_trusted_authority,
    canonical_authority,
    derive_lan_hosts,
    is_loopback_hostname,
    is_trusted_authority,
    is_trusted_request,
    parse_authority,
)
from illusion.ui.web.server import create_app
from illusion.ui.web.ws_host import WebHostConfig

# 回环 base_url：TestClient 默认 Host 是 testserver，会被 Host 栅栏拒绝；
# 测试作为「非浏览器回环客户端」访问
LOOPBACK = "http://127.0.0.1"


# ---- 单元级：loopback 分类 ----


class TestLoopbackHostname:
    @pytest.mark.parametrize(
        ("hostname", "expected"),
        [
            ("localhost", True),
            ("::1", True),
            ("127.0.0.1", True),
            ("127.255.0.9", True),  # 127/8 全段
            ("evil.example", False),
            ("localhost.example", False),  # 前缀混淆不是子串匹配能放行的
            ("192.168.1.5", False),
            ("::2", False),
        ],
    )
    def test_classification(self, hostname: str, expected: bool) -> None:
        assert is_loopback_hostname(hostname) is expected


# ---- 单元级：authority 解析与规范化 ----


class TestParseAuthority:
    def test_ipv6_bracketed(self) -> None:
        assert parse_authority("[::1]:3000") == ("::1", 3000)

    def test_case_normalized(self) -> None:
        assert parse_authority("LOCALHOST:3000") == ("localhost", 3000)

    def test_no_port(self) -> None:
        assert parse_authority("mynas.example") == ("mynas.example", None)

    def test_zero_padded_port_kept_literal(self) -> None:
        # 显式端口按字面 int 解析，规范化比较阶段负责拒绝非规范书写
        assert parse_authority("h:081") == ("h", 81)

    @pytest.mark.parametrize("bad", ["", ":"])
    def test_unparsable(self, bad: str) -> None:
        assert parse_authority(bad) is None


class TestCanonicalAndAssert:
    @pytest.mark.parametrize(
        "entry",
        [
            "mynas.example",
            "mynas.example:8080",
            "192.168.1.5",
            "[::1]",
            "LOCALHOST",
            # 含数字的普通域名：WHATWG 只在末段形如 IPv4 时才解析为 IP
            "nas1.example.com",
            "pi4.lan",
            "server2022.corp.example",
        ],
    )
    def test_accepts_canonical_entries(self, entry: str) -> None:
        assert canonical_authority(entry) is not None
        assert_trusted_authority(entry)

    @pytest.mark.parametrize(
        "entry",
        [
            "0x7f.0.0.1",  # 十六进制 IP 变形（WHATWG 会规范化）
            "127.1",  # 缩写段（WHATWG 会展开为 127.0.0.1）
            "127.000.000.001",  # 前导零
            "::ffff:127.0.0.1",  # IPv4-mapped 非规范书写
            "user@mynas.example",  # userinfo 注入
            "mynas.example/path",  # 附带路径
            "mynas.example:081",  # 零填充端口
            " mynas.example",  # 首部空白
            "mynas.example ",  # 尾部空白
            "",  # 空
            "example.1",  # 末段全数字 → WHATWG 解析为 0.0.0.1
            "1.2.3.04",  # 前导零末段
            "0x7f000001",  # 纯十六进制无点 → WHATWG 解析为 127.0.0.1
        ],
    )
    def test_rejects_non_canonical_entries(self, entry: str) -> None:
        with pytest.raises(ValueError):
            assert_trusted_authority(entry)


class TestTrustedAuthorityMatch:
    def test_portless_entry_matches_any_port(self) -> None:
        assert is_trusted_authority(("mynas.example", 8080), ("mynas.example",))
        assert is_trusted_authority(("mynas.example", None), ("mynas.example",))

    def test_ported_entry_requires_exact_port(self) -> None:
        assert is_trusted_authority(("mynas.example", 8080), ("mynas.example:8080",))
        assert not is_trusted_authority(("mynas.example", 9090), ("mynas.example:8080",))

    def test_hostname_must_match_exactly(self) -> None:
        assert not is_trusted_authority(("evil.example", 80), ("mynas.example",))


# ---- 单元级：三栅栏判定矩阵 ----


class TestTrustedRequestMatrix:
    def test_loopback_host_without_markers_passes(self) -> None:
        assert is_trusted_request(
            host="127.0.0.1:3000", origin=None, sec_fetch_site=None, trusted_hosts=()
        )

    def test_missing_host_fails_closed(self) -> None:
        assert not is_trusted_request(
            host=None, origin=None, sec_fetch_site=None, trusted_hosts=()
        )

    def test_rebinding_host_refused(self) -> None:
        assert not is_trusted_request(
            host="evil.example", origin=None, sec_fetch_site=None, trusted_hosts=()
        )

    def test_sec_fetch_cross_site_refused_even_same_origin(self) -> None:
        assert not is_trusted_request(
            host="127.0.0.1:3000",
            origin="http://127.0.0.1:3000",
            sec_fetch_site="cross-site",
            trusted_hosts=(),
        )

    def test_same_origin_passes(self) -> None:
        assert is_trusted_request(
            host="localhost:3000",
            origin="http://localhost:3000",
            sec_fetch_site="same-origin",
            trusted_hosts=(),
        )

    def test_cross_origin_refused(self) -> None:
        assert not is_trusted_request(
            host="127.0.0.1:3000",
            origin="http://localhost:5173",
            sec_fetch_site=None,
            trusted_hosts=(),
        )

    def test_opaque_null_origin_refused(self) -> None:
        assert not is_trusted_request(
            host="127.0.0.1:3000", origin="null", sec_fetch_site=None, trusted_hosts=()
        )

    def test_malformed_origin_refused(self) -> None:
        assert not is_trusted_request(
            host="127.0.0.1:3000", origin="not-a-url", sec_fetch_site=None, trusted_hosts=()
        )

    def test_trusted_host_with_matching_origin_passes(self) -> None:
        assert is_trusted_request(
            host="mynas.example",
            origin="http://mynas.example",
            sec_fetch_site=None,
            trusted_hosts=("mynas.example",),
        )

    def test_trusted_host_with_foreign_origin_refused(self) -> None:
        assert not is_trusted_request(
            host="mynas.example",
            origin="http://evil.example",
            sec_fetch_site=None,
            trusted_hosts=("mynas.example",),
        )


class TestTrustConfigAndLanDerive:
    def test_host_config_validates_on_construction(self) -> None:
        config = WebHostConfig(trusted_hosts=("mynas.example", "192.168.1.5"))
        assert config.trusted_hosts == ("mynas.example", "192.168.1.5")

    def test_host_config_rejects_invalid_entry_loudly(self) -> None:
        with pytest.raises(ValueError):
            WebHostConfig(trusted_hosts=("0x7f.0.0.1",))

    def test_derive_lan_hosts_smoke(self) -> None:
        hosts = derive_lan_hosts()
        assert isinstance(hosts, tuple)
        for text in hosts:
            assert "." in text or text.startswith("[")


# ---- 集成级：create_app 栅栏 ----


@pytest.fixture
def app():
    return create_app(dev=True)


@pytest.fixture
def client(app):
    return TestClient(app, base_url=LOOPBACK)


class TestRestFence:
    def test_loopback_rest_passes(self, client):
        resp = client.get("/api/envs")
        assert resp.status_code == 200

    def test_rebinding_host_gets_403_forbidden(self, client):
        resp = client.get("/api/envs", headers={"Host": "evil.example"})
        assert resp.status_code == 403
        assert resp.text == "forbidden"

    def test_cross_site_marker_gets_403(self, client):
        resp = client.get(
            "/api/envs",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert resp.status_code == 403

    def test_opaque_origin_gets_403(self, client):
        resp = client.get("/api/envs", headers={"Origin": "null"})
        assert resp.status_code == 403

    def test_cross_origin_gets_403(self, client):
        resp = client.get("/api/envs", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403

    def test_same_origin_passes(self, client):
        resp = client.get("/api/envs", headers={"Origin": LOOPBACK})
        assert resp.status_code == 200


class TestPrivilegedPlanePinnedToLoopback:
    """配置平面（REST /api/*）即使部署声明受信主机也仅限回环。"""

    @pytest.fixture
    def lan_client(self):
        config = WebHostConfig(trusted_hosts=("mynas.example",))
        app = create_app(dev=True, host_config=config)
        return TestClient(app, base_url="http://mynas.example")

    def test_rest_from_trusted_host_still_403(self, lan_client):
        assert lan_client.get("/api/envs").status_code == 403

    def test_ws_from_trusted_host_passes(self, lan_client):
        # WS 会话平面允许受信主机接入（Origin 与 Host 同源）。
        # 注意：starlette TestClient 的 websocket_connect 硬编码
        # ws://testserver，不吃 base_url —— Host 头须显式指定。
        with lan_client.websocket_connect(
            "/ws",
            headers={"Host": "mynas.example", "Origin": "http://mynas.example"},
        ):
            pass


class TestWsFence:
    def test_loopback_handshake_passes(self, client):
        # 显式回环 Host：以非浏览器客户端身份经 Host 栅栏放行
        #（websocket_connect 硬编码 ws://testserver，须覆盖 Host 头）
        with client.websocket_connect("/ws", headers={"Host": "127.0.0.1"}):
            pass

    def test_rebinding_ws_handshake_refused(self, client):
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws", headers={"Host": "evil.example"}),
        ):
            pass

    def test_cross_origin_ws_handshake_refused(self, client):
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/ws",
                headers={"Host": "127.0.0.1", "Origin": "http://evil.example"},
            ),
        ):
            pass

    def test_same_origin_ws_handshake_passes(self, client):
        with client.websocket_connect(
            "/ws", headers={"Host": "127.0.0.1", "Origin": LOOPBACK}
        ):
            pass


class TestStaticUnfenced:
    """静态资源不设防（无敏感内容，攻击链断在 API 栅栏）。"""

    def test_static_served_regardless_of_host(self, client):
        # dev=True 无静态文件挂载 → 404；关键是不得被栅栏拦为 403
        resp = client.get("/", headers={"Host": "evil.example"})
        assert resp.status_code != 403


class TestAutoDocsDisabled:
    """自动生成的 API 文档（/docs /redoc /openapi.json）必须禁用：
    它们不受栅栏保护，DNS rebinding 页面可读取完整接口 schema。"""

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_docs_endpoints_absent(self, client, path):
        assert client.get(path).status_code == 404

    def test_openapi_not_readable_via_rebinding_host(self, client):
        resp = client.get("/openapi.json", headers={"Host": "evil.example"})
        assert resp.status_code == 404
