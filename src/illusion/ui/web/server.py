"""
Web 服务器模块
=============

本模块提供 FastAPI 应用和 WebSocket 端点，用于启动 Web 前端服务。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from illusion.ui.web.security import (
    WebAuthConfig,
    check_ws_origin,
    require_ws_auth,
)
from illusion.ui.web.ws_host import WebBackendHost, WebHostConfig

log = logging.getLogger(__name__)


class _WebAuthMiddleware(BaseHTTPMiddleware):
    """REST /api/* 鉴权中间件。

    请求侧：校验 Cookie / Authorization 头中的启动令牌，缺失或错误返回 401。
    响应侧：为同源浏览器会话下发 HttpOnly + SameSite=Strict 的令牌 Cookie，
    使后续同源请求（含 WebSocket 握手）自动携带令牌；跨站请求不会携带该
    Cookie（SameSite=Strict），从而阻断跨站读/写 API。

    静态资源（/、/assets 等）不要求令牌，保证页面与脚本可加载；页面脚本
    发起 /api/* 与 /ws 时浏览器自动带上令牌 Cookie。
    """

    def __init__(self, app: Any, auth: WebAuthConfig) -> None:
        super().__init__(app)
        self._auth = auth

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        # 仅保护 /api/* 下的 REST 端点；WebSocket 由端点内校验，静态资源放行
        is_api = path.startswith("/api/") or path == "/api"
        if is_api:
            from illusion.ui.web.security import request_token, _tokens_match

            provided = request_token(request)
            if not _tokens_match(provided, self._auth.token):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        response = await call_next(request)
        # 成功响应下发令牌 Cookie：首页供浏览器初始化会话；API 成功响应亦下发，
        # 使先以 Bearer 认证的客户端（如桌面壳/CLI 探测）同样建立会话 Cookie。
        if response.status_code < 400:
            response.set_cookie(
                key=self._auth.cookie_name,
                value=self._auth.token or "",
                httponly=True,
                samesite="strict",
                max_age=None,  # 会话 Cookie：浏览器关闭即失效
            )
        return response


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
    auth: WebAuthConfig | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例。

    Args:
        dev: 开发模式（启用 CORS，不 serve 静态文件）。
        host_config: Web 后端主机配置。
        auth: Web 鉴权配置（启动令牌 + Origin 白名单）。None 表示不启用鉴权，
            供测试与内部注册场景使用；生产启动（cli/web.py、桌面壳）必须传入。
    """
    app = FastAPI(title="Illusion Agent Web")
    # 鉴权配置挂到 app.state，供 REST 依赖与 WebSocket 校验读取
    app.state.auth = auth

    if auth is not None and auth.enabled:
        # REST /api/* 统一鉴权中间件：校验 Cookie / Authorization 头中的启动令牌，
        # 并在响应中下发 HttpOnly + SameSite=Strict 令牌 Cookie（供同源请求携带，
        # 跨站请求被 SameSite 阻断，从而同时缓解 CSRF 与跨站读 API）。
        app.add_middleware(_WebAuthMiddleware, auth=auth)

    if dev:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        # 安全防线：先校验 Origin（跨站 WebSocket 劫持），再校验启动令牌。
        # 任一失败则在 accept() 之前以策略关闭码关闭连接，不进入握手成功状态。
        if not await check_ws_origin(websocket):
            log.info("WebSocket /ws rejected: forbidden origin=%s", websocket.headers.get("origin"))
            return
        if not await require_ws_auth(websocket):
            log.info("WebSocket /ws rejected: missing/invalid auth token")
            return
        await websocket.accept()
        config = host_config or WebHostConfig()
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
    register_env_routes(app, host_config)
    # 注册渠道配置 REST 路由（channels.json 读写）
    from illusion.ui.web.channels_routes import register_channels_routes
    register_channels_routes(app, host_config)
    # 注册 cron 定时任务 REST 路由（cron 注册表 CRUD + 调度器状态）
    from illusion.ui.web.cron_routes import register_cron_routes
    register_cron_routes(app, host_config)

    if not dev:
        dist_dir = _find_frontend_dist()
        if dist_dir is not None:
            app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
