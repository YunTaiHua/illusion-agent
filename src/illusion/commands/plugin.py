"""
插件斜杠命令
============

/plugin — 管理插件
"""

from __future__ import annotations

from typing import Any

from illusion.commands.types import CommandContext, CommandResult
from illusion.config.settings import load_settings, save_settings
from illusion.plugins.installer import install_plugin_from_path, uninstall_plugin
from illusion.plugins.loader import load_plugins


async def plugin_handler(args: str, context: CommandContext) -> CommandResult:
    """插件管理命令处理器"""
    settings = load_settings()
    tokens = args.split()
    if not tokens or tokens[0] == "list":
        return CommandResult(message=context.plugin_summary or "No plugins discovered.")
    if tokens[0] in ("enable", "disable") and len(tokens) == 2:
        enabled = tokens[0] == "enable"
        settings.enabled_plugins[tokens[1]] = enabled
        save_settings(settings)
        if tokens[1] == "browser-use" and context.engine is not None:
            # 内置浏览器热切换：注册/注销 browser_* 工具并把 BrowserManager
            # 写入/移出当前引擎元数据（与 Web 端 web_plugin_toggle 同语义）；
            # 管理器存于引擎元数据，close_runtime 兜底关闭
            from illusion.tools.browser_tools import apply_browser_toggle
            metadata = context.engine._tool_metadata
            manager: Any = metadata.get("browser_manager")
            if enabled and manager is None:
                from illusion.browser import BrowserConfig, BrowserManager
                manager = BrowserManager(BrowserConfig(
                    kernel=settings.browser.kernel,
                    headless=settings.browser.headless,
                    viewport_width=settings.browser.viewport_width,
                    viewport_height=settings.browser.viewport_height,
                    proxy=settings.browser.proxy,
                ))
            if not enabled and manager is not None:
                import asyncio

                asyncio.get_running_loop().create_task(manager.aclose())
                manager = None
            apply_browser_toggle(context.engine, context.tool_registry, manager, enabled)
        verb = "Enabled" if enabled else "Disabled"
        return CommandResult(message=f"{verb} plugin '{tokens[1]}'. (hot-reloaded)")
    if tokens[0] == "install" and len(tokens) == 2:
        path = install_plugin_from_path(tokens[1])
        return CommandResult(message=f"Installed plugin to {path}")
    if tokens[0] == "uninstall" and len(tokens) == 2:
        if uninstall_plugin(tokens[1]):
            return CommandResult(message=f"Uninstalled plugin '{tokens[1]}'")
        return CommandResult(message=f"Plugin '{tokens[1]}' not found")
    plugins = load_plugins(settings, context.cwd)
    if plugins:
        return CommandResult(message=context.plugin_summary)
    return CommandResult(message="Usage: /plugin [list|enable NAME|disable NAME|install PATH|uninstall NAME]")
