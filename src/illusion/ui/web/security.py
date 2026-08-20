"""Web UI 安全模块
=============

为 Web 后端提供启动令牌鉴权与 Origin 校验，缓解跨站 WebSocket 劫持
（CSWSH / Drive-by Localhost）与 REST 无鉴权问题。

威胁模型
--------
Web 服务默认只监听 127.0.0.1，但同浏览器内恶意网页（如 evil.com）仍可
向 ws://127.0.0.1:3000/ws 发起跨站 WebSocket 握手，或向 http://127.0.0.1:3000/api/*
发起跨站 REST 请求。由于浏览器对 WebSocket 握手不施加同源策略（SOP），
且本服务此前无任何鉴权，攻击者可无接触地驱动 Agent 执行任意命令（RCE）。

防御策略（双重）
----------------
1. **Origin 校验**：拒绝来自非本机/未配置来源的 WebSocket 握手（服务端唯一可靠的
   跨站判定手段）。Origin 头由浏览器代为填充、不可被页面脚本伪造。
2. **启动令牌（token）**：服务启动时生成一次性高熵令牌，通过 HttpOnly + SameSite
   Cookie 自动随浏览器请求携带（同源请求天然携带，跨站请求被 SameSite 阻断），
   服务端对 WebSocket 握手与 REST 请求统一校验。

令牌仅在内存中保存（不落盘），随服务进程生命周期存在；重启后旧令牌失效。
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.security.utils import get_authorization_scheme_param

# 启动令牌 Cookie 名称
AUTH_COOKIE_NAME = "illusion_web_token"
# 允许携带令牌的请求头方案（浏览器 fetch 亦可显式设置，便于非 Cookie 客户端）
AUTH_HEADER_SCHEME = "Bearer"
# 默认允许的 Origin 主机（浏览器页面来源）。端口任意。主机名统一为
# 去括号形式（_parse_origin 返回 unbracketed IPv6，如 ::1）。
_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}
# 本机回环地址（作为无 Origin 的 WebSocket 客户端的 Host 兜底校验）
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}

# WebSocket 拒绝握手时使用的关闭码（策略性关闭，RFC 6455 7.4.1）
WS_POLICY_CLOSE_CODE = 1008


@dataclass(frozen=True)
class WebAuthConfig:
    """Web 鉴权配置。

    Attributes:
        token: 启动令牌（每次服务启动随机生成）。None 表示不启用鉴权。
        cookie_name: Cookie 名称。
        allowed_origin_hosts: 额外允许的 Origin 主机名（除 localhost/127.0.0.1 外）。
            桌面壳或自建前端需要从其他来源连接时在此追加。
    """

    token: str | None = None
    cookie_name: str = AUTH_COOKIE_NAME
    allowed_origin_hosts: frozenset[str] = field(
        default_factory=lambda: frozenset(_ALLOWED_ORIGIN_HOSTS)
    )

    @property
    def enabled(self) -> bool:
        return bool(self.token)


def generate_auth_token() -> str:
    """生成 32 字节（256 bit）高熵启动令牌。"""
    return secrets.token_urlsafe(32)


def _parse_origin(origin: str | None) -> tuple[str, str | None, int | None]:
    """解析 Origin 头为 (scheme, host, port)。

    Origin 形如 http://localhost:3000 或 https://evil.com。
    返回 host 为去括号 IPv6 形式。无法解析返回 (scheme, None, None)。
    """
    if not origin:
        return ("", None, None)
    scheme, _, rest = origin.partition("://")
    if not scheme or not rest:
        return ("", None, None)
    host = rest
    port: int | None = None
    if host.startswith("["):  # IPv6 字面量，如 [::1]:3000
        end = host.find("]")
        if end == -1:
            return (scheme.lower(), None, None)
        bracket_host = host[1:end]
        remainder = host[end + 1 :]
        if remainder.startswith(":"):
            try:
                port = int(remainder[1:])
            except ValueError:
                return (scheme.lower(), None, None)
        return (scheme.lower(), bracket_host, port)
    # 常规 host[:port]
    if ":" in host:
        candidate_host, _, port_str = host.rpartition(":")
        if port_str.isdigit():
            host = candidate_host
            port = int(port_str)
    return (scheme.lower(), host, port)


def origin_is_allowed(origin: str | None, allowed_hosts: frozenset[str]) -> bool:
    """校验 Origin 是否来自允许的主机（端口任意）。

    仅比对 host，不比对端口，以便桌面壳/前端可用任意端口。返回 False
    表示应拒绝该来源。
    """
    if not origin:
        return False
    _, host, _ = _parse_origin(origin)
    if host is None:
        return False
    return host in allowed_hosts


def cookie_value(request: Request) -> str | None:
    """从请求 Cookie 中读取鉴权令牌。"""
    return request.cookies.get(AUTH_COOKIE_NAME)


def ws_cookie_value(websocket: WebSocket) -> str | None:
    """从 WebSocket 握手请求 Cookie 中读取鉴权令牌。"""
    return websocket.cookies.get(AUTH_COOKIE_NAME)


def request_token(request: Request) -> str | None:
    """从请求中提取令牌：优先 Cookie，其次 Authorization: Bearer 头。"""
    cookie = cookie_value(request)
    if cookie:
        return cookie
    auth_header = request.headers.get("authorization")
    if auth_header:
        scheme, param = get_authorization_scheme_param(auth_header)
        if scheme.lower() == AUTH_HEADER_SCHEME.lower() and param:
            return param
    return None


def ws_request_token(websocket: WebSocket) -> str | None:
    """从 WebSocket 握手请求中提取令牌：优先 Cookie，其次查询参数 ?token=...。"""
    cookie = ws_cookie_value(websocket)
    if cookie:
        return cookie
    query = websocket.query_params.get("token")
    if query:
        return query
    return None


def _tokens_match(a: str | None, b: str | None) -> bool:
    """常量时间比较，防时序侧信道。"""
    if not a or not b:
        return False
    return hmac.compare_digest(a, b)


def _auth_config_from_request(request: Request) -> WebAuthConfig | None:
    """从请求关联的 app.state 取鉴权配置。"""
    auth = getattr(request.app.state, "auth", None)
    if isinstance(auth, WebAuthConfig):
        return auth
    return None


def require_auth(request: Request) -> None:
    """FastAPI 依赖：REST 请求鉴权。

    未启用鉴权时放行（向后兼容测试与内部注册场景）；启用后校验 Cookie /
    Authorization 头中的令牌，失败返回 401。
    """
    auth = _auth_config_from_request(request)
    if auth is None or not auth.enabled:
        return
    provided = request_token(request)
    if not _tokens_match(provided, auth.token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


async def require_ws_auth(websocket: WebSocket) -> bool:
    """WebSocket 握手鉴权：返回 True 表示放行。

    在 accept() 之前调用。校验失败时以策略关闭码关闭连接并返回 False。
    未启用鉴权时放行。
    """
    auth = getattr(websocket.app.state, "auth", None)
    if not isinstance(auth, WebAuthConfig) or not auth.enabled:
        return True
    provided = ws_request_token(websocket)
    if not _tokens_match(provided, auth.token):
        try:
            await websocket.close(code=WS_POLICY_CLOSE_CODE, reason="Unauthorized")
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        return False
    return True


async def check_ws_origin(websocket: WebSocket) -> bool:
    """WebSocket 握手 Origin 校验：返回 True 表示放行。

    校验规则：
        - 请求携带 Origin 头 → 必须命中允许主机白名单（否则拒绝）；
        - 无 Origin 头（非浏览器客户端，如桌面壳内部连接）→ 依据 Host 头是否为
          本机回环地址放行（保守兜底）。
    """
    auth = getattr(websocket.app.state, "auth", None)
    # 未配置鉴权（auth=None）：完全放行，保持测试与内部注册场景向后兼容
    if auth is None:
        return True
    allowed_hosts = auth.allowed_origin_hosts
    origin = websocket.headers.get("origin")
    if origin:
        if origin_is_allowed(origin, allowed_hosts):
            return True
        try:
            await websocket.close(code=WS_POLICY_CLOSE_CODE, reason="Forbidden Origin")
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        return False
    # 无 Origin：按 Host 头兜底（本机回环才放行）
    host_header = websocket.headers.get("host", "")
    hostname = host_header.split(":")[0].lower()
    if hostname in _LOOPBACK_HOSTS:
        return True
    try:
        await websocket.close(code=WS_POLICY_CLOSE_CODE, reason="Forbidden Origin")
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    return False


__all__ = [
    "AUTH_COOKIE_NAME",
    "AUTH_HEADER_SCHEME",
    "WS_POLICY_CLOSE_CODE",
    "WebAuthConfig",
    "generate_auth_token",
    "origin_is_allowed",
    "require_auth",
    "require_ws_auth",
    "check_ws_origin",
]
