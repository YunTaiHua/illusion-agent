"""
IllusionAgent CLI 包
====================

本包提供 IllusionAgent 命令行界面，使用 typer 构建。

主要功能：
    - 交互式会话模式
    - 非交互式打印模式
    - MCP 服务器管理
    - 插件管理
    - 认证管理
    - 资源添加（model）
    - Cron 任务调度管理
    - 渠道管理
    - Web UI 启动
    - 自更新
    - 工作目录管理（set）

子命令说明：
    - mcp: MCP 服务器管理（list、add、remove）
    - plugin: 插件管理（list、install、uninstall）
    - auth: 认证管理（login、status、logout、switch）
    - add: 向已有环境添加资源（model）
    - cron: Cron 调度管理（start、stop、status、list、toggle、history、logs）
    - channel: 渠道管理（login、serve、status、enable、disable、logout）
    - web: 启动 Web UI
    - update: 自更新
    - set: 设置工作目录

使用示例：
    >>> illusion                    # 启动交互式会话
    >>> illusion -p "你的提示词"     # 非交互式打印模式
    >>> illusion auth login         # 认证登录（新建 env）
    >>> illusion set "E:\\Projects" # 设置工作目录
"""
from __future__ import annotations

import sys
from typing import Any, cast

import typer

from illusion import __version__


class _ReconfigurableStream:
    """支持 reconfigure 方法的流类型（用于类型安全地调用 stdout/stderr.reconfigure）。"""

    def reconfigure(self, **kwargs: Any) -> None: ...


# 确保 Windows 上 stdout/stderr 使用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    cast(_ReconfigurableStream, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    cast(_ReconfigurableStream, sys.stderr).reconfigure(encoding="utf-8", errors="replace")


def _version_callback(value: bool) -> None:
    """版本回调函数"""
    if value:
        print(f"illusion {__version__}")
        raise typer.Exit()


# 创建主应用程序
app = typer.Typer(
    name="illusion",
    help=(
        "Illusion Agent - AI 驱动的编程助手\n"
        "默认启动交互式会话，使用 -p/--print 进入非交互模式"
    ),
    add_completion=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)

# 创建子命令应用
mcp_app = typer.Typer(name="mcp", help="MCP 服务器管理 / Manage MCP servers")
plugin_app = typer.Typer(name="plugin", help="插件管理 / Manage plugins")
auth_app = typer.Typer(name="auth", help="认证管理 / Manage authentication")
cron_app = typer.Typer(name="cron", help="定时任务管理 / Manage cron scheduler and jobs")
web_app = typer.Typer(name="web", help="启动 Web 界面 / Launch Web UI")
browser_app = typer.Typer(name="browser", help="内置浏览器管理 / Manage the built-in browser")
add_app = typer.Typer(name="add", help="添加资源 / Add resources (e.g. add model to existing env)")
channel_app = typer.Typer(name="channel", help="渠道管理 / Manage messaging channels")

# 注册子命令到主应用
app.add_typer(mcp_app)
app.add_typer(plugin_app)
app.add_typer(auth_app)
app.add_typer(cron_app)
app.add_typer(web_app)
app.add_typer(browser_app)
app.add_typer(add_app)
app.add_typer(channel_app)

# 导入各子命令模块以触发命令注册（顺序重要：先 shared/workspace，再子命令，最后 main）
# 使用 importlib.import_module 进行副作用导入，避免未使用导入告警
import importlib

for _module_name in (
    "shared",
    "workspace",
    "mcp",
    "plugin",
    "cron",
    "auth",
    "browser",
    "web",
    "update",
    "channel",
    "main",
):
    importlib.import_module(f"illusion.cli.{_module_name}")

__all__ = ["app"]
