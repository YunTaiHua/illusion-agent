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
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from illusion.ui.web.security import (
    WS_POLICY_CLOSE_CODE,
    is_trusted_request,
)
from illusion.ui.web.ws_host import WebBackendHost, WebHostConfig

log = logging.getLogger(__name__)


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
) -> FastAPI:
    """创建 FastAPI 应用实例。

    浏览器信任栅栏始终启用（fail-closed）：受信主机列表取自
    ``host_config.trusted_hosts``（仅放行 /ws 会话平面），未提供
    host_config 时仅回环可信。
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

    # REST /api/* 栅栏中间件（特权平面钉死回环）。Starlette 的
    # add_middleware 是 insert(0)：后添加的位于外层。当前只有这一层
    # HTTP 中间件；开发流程统一走 Vite 代理（changeOrigin 改写 Host +
    # 剥 Origin），代理转发的请求以「非浏览器客户端」身份过 Host 栅栏。
    app.add_middleware(_BrowserTrustMiddleware)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        # 安全防线：握手前过信任栅栏。拒绝时在 accept() 之前调用 close()
        # —— Starlette 会以 HTTP 403 拒绝升级，连接不会进入会话状态。
        headers = websocket.headers
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
