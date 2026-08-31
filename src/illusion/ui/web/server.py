"""
Web 服务器模块
=============

本模块提供 FastAPI 应用和 WebSocket 端点，用于启动 Web 前端服务。

安全：所有 /api 与 /ws 请求都经过浏览器信任栅栏
（security.is_trusted_request）——Host 回环/受信校验 + Sec-Fetch-Site
跨站标记 + Origin 严格同源。REST /api/* 属特权平面，仅限回环访问；
静态资源不设防（无敏感内容，攻击链断在 API 栅栏）。
"""

from __future__ import annotations

import logging
import time
from email.utils import formatdate
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from illusion.config.i18n import t as _t
from illusion.ui.web.auth import (
    TOKEN_QUERY,
    WebAuth,
    authority_from_host,
    extract_bearer,
)
from illusion.ui.web.security import (
    WS_POLICY_CLOSE_CODE,
    is_trusted_request,
)
from illusion.ui.web.ws_host import WebBackendHost, WebHostConfig

log = logging.getLogger(__name__)


def _query_param(scope: Scope, name: str) -> str | None:
    """从请求 query string 中取单个参数值（捕获解码失败返回 None）。"""
    qs = scope.get("query_string", b"")
    if not qs:
        return None
    try:
        values = parse_qs(qs.decode("utf-8"), keep_blank_values=False).get(name)
    except (UnicodeDecodeError, ValueError):
        return None
    return values[0] if values else None


def _set_cookie_header(name: str, value: str, max_age: int, *, secure: bool) -> str:
    """拼装签名 session cookie 的 Set-Cookie 头。
    HttpOnly 杜绝 JS 读取；SameSite=Strict 阻断跨站携带；Path=/ 全局生效；
    HTTPS 部署（TLS 终止于本服务）追加 Secure，杜绝明文传输。
    """
    expires = formatdate(time.time() + max_age, usegmt=True)
    secure_part = "Secure; " if secure else ""
    return (
        f"{name}={value}; Max-Age={max_age}; Path=/; Expires={expires}; "
        f"{secure_part}HttpOnly; SameSite=Strict"
    )


async def _send_unauthorized(send: Send) -> None:
    """发送 401 纯文本拒绝（认证层失败，早于栅栏的 403）。"""
    body = _t("web_auth_required").encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_exchange(
    send: Send, *, auth: WebAuth, authority: str, dev: bool, secure: bool
) -> None:
    """首页 token 交换：签发签名 cookie 并清理地址栏中的 token。

    - 非 dev：303 重定向到不带 token 的干净 ``/``，浏览器随后凭 cookie 加载
      首页及静态资源（deepseek-harness 同款流程）；
    - dev：无静态资源挂载，返回 200 提示文本，开发者拿到 cookie 后回到
      Vite 开发地址（localhost 域 cookie 跨端口共享）即可通过认证。
    """
    name, value, max_age = auth.mint_cookie(authority)
    set_cookie = _set_cookie_header(name, value, max_age, secure=secure)
    if dev:
        body = _t("web_auth_success").encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"set-cookie", set_cookie.encode("latin-1")),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 303,
            "headers": [
                (b"location", b"/"),
                (b"cache-control", b"no-store"),
                (b"referrer-policy", b"no-referrer"),
                (b"set-cookie", set_cookie.encode("latin-1")),
                (b"content-length", b"0"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


class _AuthMiddleware:
    """Web 认证层（launch token / 签名 cookie），包裹全部 HTTP 请求。

    认证先于浏览器信任栅栏执行：凭据缺失返回 401，栅栏拒绝返回 403。
    首页 ``GET /`` 承担 token → cookie 交换（详见 ``_send_exchange``）；
    其余路径（``/api/*``、静态资源）按 签名 cookie / ``Authorization:
    Bearer`` / ``?token=`` 三路校验，任一通过即放行到内层栅栏，否则 401。
    dev 模式与生产完全一致（静态资源由 Vite 独立提供，不受本中间件影响）。
    """

    def __init__(self, app: ASGIApp, auth: WebAuth, *, dev: bool) -> None:
        self.app = app
        self.auth = auth
        self.dev = dev

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        headers = Headers(scope=scope)
        authority = authority_from_host(headers.get("host"))
        cookie = headers.get("cookie")
        bearer = extract_bearer(headers.get("authorization"))
        query_token = _query_param(scope, TOKEN_QUERY)

        if method == "GET" and path == "/":
            if self.auth.token_valid(query_token):
                await _send_exchange(
                    send,
                    auth=self.auth,
                    authority=authority or "",
                    dev=self.dev,
                    secure=scope.get("scheme") == "https",
                )
                return
            if authority and cookie and self.auth.cookie_valid(authority, cookie):
                await self.app(scope, receive, send)
                return
            await _send_unauthorized(send)
            return

        if not self.auth.is_authenticated(
            authority=authority,
            cookie_value=cookie,
            bearer_token=bearer,
            query_token=query_token,
        ):
            log.warning(
                "HTTP %s %s rejected: missing or invalid credentials (host=%s)",
                method,
                path,
                headers.get("host"),
            )
            await _send_unauthorized(send)
            return
        await self.app(scope, receive, send)


class _BrowserTrustMiddleware:
    """REST ``/api/*`` 浏览器信任栅栏（纯 ASGI 中间件）。

    REST API 属特权平面（读写凭据与配置、管理 cron 与渠道），整体钉死
    回环 —— 即使部署声明了 ``trusted_hosts``（后者仅放行 /ws 会话平面）。
    失败以 403 纯文本拒绝。WebSocket 由端点内校验；静态资源不设防。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                headers = Headers(scope=scope)
                # 特权平面仅限回环：trusted_hosts 传空元组
                allowed = is_trusted_request(
                    host=headers.get("host"),
                    origin=headers.get("origin"),
                    sec_fetch_site=headers.get("sec-fetch-site"),
                    trusted_hosts=(),
                )
                if not allowed:
                    log.warning(
                        "REST %s rejected by browser-trust fence: host=%s origin=%s",
                        path,
                        headers.get("host"),
                        headers.get("origin"),
                    )
                    await _send_forbidden(send)
                    return
        await self.app(scope, receive, send)


async def _send_forbidden(send: Send) -> None:
    """发送 403 纯文本拒绝响应。"""
    body = b"forbidden"
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _find_frontend_dist() -> Path | None:
    """查找前端打包产物目录。"""
    # server.py 位于 src/illusion/ui/web/server.py，需要向上 5 级到项目根目录
    project_root = Path(__file__).parent.parent.parent.parent.parent
    # illusion/ 包根目录（wheel 安装后为 site-packages/illusion/）
    pkg_root = Path(__file__).parent.parent.parent
    candidates = [
        # 开发模式：项目根目录下的 frontend/web/dist
        project_root / "frontend" / "web" / "dist",
        # pip 安装：包内打包的前端产物（illusion/_web_dist）
        pkg_root / "_web_dist",
        # 当前工作目录（可能从项目根目录运行）
        Path.cwd() / "frontend" / "web" / "dist",
    ]
    for p in candidates:
        if p.is_dir() and (p / "index.html").exists():
            return p
    return None


def create_app(
    *,
    dev: bool = False,
    host_config: WebHostConfig | None = None,
    auth: WebAuth | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例。

    浏览器信任栅栏始终启用（fail-closed）：受信主机列表取自
    ``host_config.trusted_hosts``（仅放行 /ws 会话平面），未提供
    host_config 时仅回环可信。

    Args:
        dev: 开发模式（Vite 独立提供静态资源，不挂载 dist）。
        host_config: Web 主机配置（受信主机等）。
        auth: Web 认证状态。为 ``None`` 时仅启用浏览器信任栅栏
            （默认，供测试与内部复用）；生产入口（cli/web.py）总是传入。
    """
    config = host_config or WebHostConfig()
    # 本地服务不暴露自动生成的 API 文档（/docs /redoc /openapi.json）：
    # 它们不受栅栏保护且含完整接口 schema，属于 DNS rebinding 页面可读的
    # 侦察信息 —— 直接禁用，恢复「Host 栅栏绑定一切请求」的不变性。
    app = FastAPI(
        title="Illusion Agent Web",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # 认证层（可选，外层）→ 浏览器信任栅栏（内层）→ 应用。
    # Starlette 的 add_middleware 是 insert(0)：后添加的位于外层。
    # 认证缺失凭据返回 401；栅栏对非回环/跨站请求返回 403。
    app.add_middleware(_BrowserTrustMiddleware)
    if auth is not None:
        app.add_middleware(_AuthMiddleware, auth=auth, dev=dev)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        # 安全防线：握手前先过认证层（凭据缺失/无效 → 403 拒绝升级），
        # 再过信任栅栏（DNS rebinding / 跨站拒绝）。在 accept() 之前调用
        # close() —— Starlette 会以 HTTP 403 拒绝升级，连接不会进入会话状态。
        headers = websocket.headers
        if auth is not None:
            authority = authority_from_host(headers.get("host"))
            if not auth.is_authenticated(
                authority=authority,
                cookie_value=headers.get("cookie"),
                bearer_token=extract_bearer(headers.get("authorization")),
                query_token=_query_param(websocket.scope, TOKEN_QUERY),
            ):
                log.warning(
                    "WebSocket /ws rejected by auth layer: host=%s",
                    headers.get("host"),
                )
                await websocket.close(code=WS_POLICY_CLOSE_CODE, reason="unauthorized")
                return
        if not is_trusted_request(
            host=headers.get("host"),
            origin=headers.get("origin"),
            sec_fetch_site=headers.get("sec-fetch-site"),
            trusted_hosts=config.trusted_hosts,
        ):
            log.warning(
                "WebSocket /ws rejected by browser-trust fence: host=%s origin=%s",
                headers.get("host"),
                headers.get("origin"),
            )
            await websocket.close(code=WS_POLICY_CLOSE_CODE, reason="forbidden")
            return
        await websocket.accept()
        host = WebBackendHost(config, websocket)
        try:
            await host.run()
        except WebSocketDisconnect:
            log.info("WebSocket client disconnected")
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            log.warning("WebSocket endpoint error: %s", exc)
            # 尝试向前端发送错误事件
            try:
                from starlette.websockets import WebSocketState
                if websocket.application_state == WebSocketState.CONNECTED:
                    import json
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"Backend error: {exc}",
                    }))
            except (WebSocketDisconnect, RuntimeError, OSError) as send_exc:
                log.debug("向前端发送错误事件失败: %s", send_exc)

    # 注册 env/oauth/settings REST 路由（在 StaticFiles mount 之前）
    from illusion.ui.web.env_routes import register_env_routes
    register_env_routes(app, config)
    # 注册渠道配置 REST 路由（channels.json 读写）
    from illusion.ui.web.channels_routes import register_channels_routes
    register_channels_routes(app, config)
    # 注册 cron 定时任务 REST 路由（cron 注册表 CRUD + 调度器状态）
    from illusion.ui.web.cron_routes import register_cron_routes
    register_cron_routes(app, config)

    if not dev:
        dist_dir = _find_frontend_dist()
        if dist_dir is not None:
            app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
