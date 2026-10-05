"""
内置浏览器子命令
================

子命令:
    setup: 手动安装 Playwright Chromium 内核（一次性；有系统 Chrome/Edge
           时无需执行——内核解析链会自动兜底）。
"""
from __future__ import annotations

import sys

import typer

from illusion.cli import browser_app


@browser_app.command("setup")
def browser_setup() -> None:
    """下载 Playwright Chromium 内核（内置浏览器的一次性手动安装）。"""
    if not _playwright_installed():
        typer.echo(
            "Playwright 未安装 / Playwright is not installed.\n"
            "它是 illusion-agent 的必需依赖，请重装：pip install illusion-agent",
            err=True,
        )
        raise typer.Exit(1)
    if _chromium_available():
        typer.echo("Chromium 内核已存在 / Chromium is already installed. Nothing to do.")
        return
    if _system_browser_available():
        typer.echo(
            "检测到系统 Chrome/Edge，内置浏览器可直接使用，无需下载内核。\n"
            "System Chrome/Edge detected — the built-in browser works without downloading. "
            "Still downloading Chromium is optional; continuing anyway."
        )
    typer.echo("正在下载 Chromium（约 130MB，一次性）/ Downloading Chromium (~130MB, one-time)...")
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    if proc.returncode != 0:
        typer.echo("下载失败 / Download failed. Check your network or proxy.", err=True)
        raise typer.Exit(proc.returncode or 1)
    typer.echo("完成 / Done. The built-in browser is ready.")


def _playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _chromium_available() -> bool:
    import os
    from pathlib import Path

    from illusion.browser.executable import resolve_launch_kwargs  # noqa: F401

    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        bases = [Path(override)]
    elif sys.platform == "win32":
        bases = [Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"]
    elif sys.platform == "darwin":
        bases = [Path.home() / "Library" / "Caches" / "ms-playwright"]
    else:
        bases = [Path.home() / ".cache" / "ms-playwright"]
    for base in bases:
        try:
            if base.exists() and any(base.glob("chromium*")):
                return True
        except OSError:
            continue
    return False


def _system_browser_available() -> bool:
    from illusion.browser.executable import find_system_browser

    return find_system_browser() is not None
