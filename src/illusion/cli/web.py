"""
Web UI 子命令
=============

提供启动 Web UI 界面的功能。

子命令:
    - (默认): 启动 Web UI 浏览器界面（默认端口 3000）
"""
from __future__ import annotations

from typing import Annotated, Any

import typer

from illusion.cli import web_app
from illusion.config.i18n import t as _t


@web_app.callback(invoke_without_command=True)
def web_start(
    port: int = typer.Option(3000, "--port", "-p", help="Web 服务端口"),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    dev: bool = typer.Option(False, "--dev", help="开发模式（启用 CORS，不 serve 静态文件）"),
    model: str | None = typer.Option(None, "--model", "-m", help="指定模型"),
    prompt: str | None = typer.Option(None, "--prompt", help="初始提示词"),
    trusted_host: Annotated[
        list[str] | None,
        typer.Option(
            "--trusted-host",
            help="受信主机 authority（host[:port]，可多次传入），供非回环部署声明可达名称",
        ),
    ] = None,
) -> None:
    """启动 Illusion Agent Web UI / Launch Illusion Agent Web UI"""
    # working_directory 已由主回调统一切换（见 cli/main.py）
    import sys
    import threading

    import uvicorn

    from illusion.ui.web.auth import create_web_auth
    from illusion.ui.web.security import (
        assert_trusted_authority,
        derive_lan_hosts,
    )
    from illusion.ui.web.server import create_app
    from illusion.ui.web.ws_host import WebHostConfig

    # 信任栅栏受信主机：显式声明优先，绑定所有接口时自动并入本机 LAN 地址。
    # 非法条目在启动时响亮失败（fail loudly），绝不静默忽略。
    explicit_hosts = tuple(trusted_host or ())
    for entry in explicit_hosts:
        try:
            assert_trusted_authority(entry)
        except ValueError as exc:
            typer.echo(_t("web_invalid_trusted_host", reason=exc), err=True)
            raise typer.Exit(code=1) from None
    lan_hosts = derive_lan_hosts() if host in ("0.0.0.0", "::") else ()
    trusted_hosts = explicit_hosts + lan_hosts

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
        trusted_hosts=trusted_hosts,
    )

    # Web 访问认证：每次启动生成新的 launch token（进程生命周期有效），
    # cookie 签名 secret 持久化于配置目录——后端重启后浏览器已登录的会话
    # 依旧有效，无需重新打开打印的 URL。生产与 dev 一致启用（fail-closed）。
    auth = create_web_auth()

    app = create_app(dev=dev, host_config=config, auth=auth)

    url = auth.authenticated_url(f"http://{host}:{port}")
    typer.echo(f"Illusion Agent Web UI: {url}")
    typer.echo(_t("web_auth_enabled"))
    # 信任栅栏状态（栅栏非认证层；REST /api 特权平面仅限回环，
    # 受信主机仅可接入 /ws 会话平面）
    typer.echo(_t("web_trust_enabled"))
    if trusted_hosts:
        typer.echo(_t("web_trusted_hosts", hosts=", ".join(trusted_hosts)))
    if host in ("0.0.0.0", "::"):
        typer.echo(_t("web_bind_all_hint"))
        typer.echo(_t("web_lan_hint"))
    if dev:
        typer.echo(_t("web_dev_host_hint"))
    # 立即刷出访问 URL：桌面壳（Electron）从 stdout 解析该行以拿到带
    # token 的完整地址（pipe 下 Python 默认块缓冲，须显式 flush）
    sys.stdout.flush()
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
