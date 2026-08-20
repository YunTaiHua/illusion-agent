"""
Web UI 子命令
=============

提供启动 Web UI 界面的功能。

子命令:
    - (默认): 启动 Web UI 浏览器界面（默认端口 3000）
"""
from __future__ import annotations

from typing import Any

import typer

from illusion.cli import web_app


@web_app.callback(invoke_without_command=True)
def web_start(
    port: int = typer.Option(3000, "--port", "-p", help="Web 服务端口"),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    dev: bool = typer.Option(False, "--dev", help="开发模式（启用 CORS，不 serve 静态文件）"),
    model: str | None = typer.Option(None, "--model", "-m", help="指定模型"),
    prompt: str | None = typer.Option(None, "--prompt", help="初始提示词"),
) -> None:
    """启动 Illusion Agent Web 界面 / Launch Illusion Agent Web UI"""
    import threading

    import uvicorn

    # working_directory 已由主回调统一切换（见 cli/main.py）
    from illusion.ui.web.security import WebAuthConfig, generate_auth_token
    from illusion.ui.web.server import create_app
    from illusion.ui.web.ws_host import WebHostConfig

    # 生产模式：启动时生成一次性高熵令牌，启用 Web 鉴权（Origin 校验 + 令牌
    # Cookie）。令牌仅存于内存，重启后旧令牌失效；浏览器经同源 Cookie 自动携带。
    # dev 模式不启用令牌（Vite 代理下浏览器页面在 localhost:5173、拿不到本机
    # Cookie，令牌会拦截开发工作流），但仍启用 Origin 校验。
    auth_config = WebAuthConfig() if dev else WebAuthConfig(token=generate_auth_token())

    # 渠道自动激活：与 illusion 主命令一致，有 enabled 渠道时 spawn 守护进程。
    # 渠道启用必须配置运行目录（working_directory，见 channel enable --working-directory
    # 与 /api/channels PATCH 校验），守护进程内 runner 按该目录锚定 agent 运行。
    _daemon_proc = None
    _daemon_client = None
    try:
        from illusion.channels import maybe_spawn_channel_daemon
        _daemon_proc, _daemon_client = maybe_spawn_channel_daemon()
    except (OSError, RuntimeError) as exc:
        import logging
        logging.getLogger(__name__).warning("渠道自动激活失败: %s", exc)

    # cron 自动激活（与 illusion 主命令一致）
    _cron_proc = None
    _cron_client = None
    try:
        from illusion.services.cron_spawn import maybe_spawn_cron_daemon
        _cron_proc, _cron_client = maybe_spawn_cron_daemon()
    except (OSError, RuntimeError) as exc:
        import logging
        logging.getLogger(__name__).warning("cron 自动激活失败: %s", exc)

    # PC 端渠道感知：与 illusion 主命令一致，注入 channel_hint + channel_tools
    # 让 web 端 LLM 也能看到已启用渠道并用跨渠道工具发文件
    pc_channel_hint: str | None = None
    pc_channel_tools: list[Any] | None = None
    try:
        from illusion.channels.config import load_channels_config
        from illusion.prompts.channel_hints import (
            get_channel_hint,
            list_active_sessions,
        )
        _cfg = load_channels_config()
        if _cfg.has_enabled_channels():
            other_names = _cfg.enabled_channel_names()
            _active = {
                name: list_active_sessions(name, _cfg, limit=5)
                for name in other_names
            }
            pc_channel_hint = get_channel_hint(
                current_channel=None,
                channels_config=_cfg,
                active_sessions=_active,
            )
            # 注入跨渠道工具
            from illusion.channels.tools.cross_channel import (
                ListChannelSessionsTool,
                SendToChannelTool,
            )
            pc_channel_tools = [ListChannelSessionsTool(_cfg), SendToChannelTool(_cfg)]
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        import logging
        logging.getLogger(__name__).warning("PC 渠道感知加载失败: %s", exc)

    config = WebHostConfig(
        model=model,
        channel_hint=pc_channel_hint,
        channel_tools=pc_channel_tools,
    )

    app = create_app(dev=dev, host_config=config, auth=auth_config)

    url = f"http://{host}:{port}"
    typer.echo(f"Illusion Agent Web UI: {url}")
    # Web 鉴权已启用（Origin 校验 + 启动令牌 Cookie）：令牌经同源 Cookie 自动
    # 传递，浏览器/桌面壳均无需手动拼接，仅在控制台提示已启用。
    if auth_config.enabled:
        import logging
        logging.getLogger(__name__).info("Web 鉴权已启用：Origin 校验 + 启动令牌 Cookie")
    if not dev:
        import os

        # 桌面壳（Electron）通过 ILLUSION_NO_BROWSER_OPEN=1 禁止自动打开系统浏览器，
        # 由桌面壳自行 loadURL 加载界面；CLI 直接运行时该变量未设置，行为不变
        if not os.environ.get("ILLUSION_NO_BROWSER_OPEN"):
            import webbrowser
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    # Ctrl+C / 正常退出时关闭 IPC 连接，守护进程检测到连接归零后自动退出
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass  # IPC 连接关闭即触发守护进程退出
    finally:
        # 关闭 IPC 连接（OS 也会在进程退出时自动关闭）
        for ref in (_cron_client, _daemon_client):
            if ref is not None:
                ref.close()
