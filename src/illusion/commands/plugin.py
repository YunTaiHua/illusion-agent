"""
插件斜杠命令
============

/plugin — 管理插件
"""

from __future__ import annotations

from typing import Any

from illusion.commands.types import CommandContext, CommandResult
from illusion.config.settings import load_settings
from illusion.plugins.installer import install_plugin_from_path, uninstall_plugin
from illusion.plugins.loader import load_plugins, native_browser_enabled, set_plugin_enabled


async def plugin_handler(args: str, context: CommandContext) -> CommandResult:
    """插件管理命令处理器"""
    tokens = args.strip().split(maxsplit=1)
    if not tokens or tokens[0] == "list":
        return CommandResult(message=context.plugin_summary or "No plugins discovered.")
    if tokens[0] in ("enable", "disable") and len(tokens) == 2:
        name = tokens[1]
        enabled = tokens[0] == "enable"
        try:
            scope = set_plugin_enabled(name, enabled, context.cwd)
        except ValueError as exc:
            return CommandResult(message=str(exc))
        await _refresh_plugin_runtime(context, browser_changed=name == "browser-use" and scope == "user")
        verb = "Enabled" if enabled else "Disabled"
        return CommandResult(message=f"{verb} plugin '{name}' ({scope}).")
    if tokens[0] == "install" and len(tokens) == 2:
        path = install_plugin_from_path(tokens[1])
        return CommandResult(message=f"Installed plugin to {path}")
    if tokens[0] == "uninstall" and len(tokens) == 2:
        if uninstall_plugin(tokens[1]):
            return CommandResult(message=f"Uninstalled plugin '{tokens[1]}'")
        return CommandResult(message=f"Plugin '{tokens[1]}' not found")
    return CommandResult(message="Usage: /plugin [list|enable NAME|disable NAME|install PATH|uninstall NAME]")


async def _refresh_plugin_runtime(context: CommandContext, *, browser_changed: bool) -> None:
    """在当前引擎上更新注册表，立即退出时 SessionEnd 也使用新策略。"""
    if context.engine is None:
        return
    executor = getattr(context.engine, "_hook_executor", None)
    refresh_browser = browser_changed and context.tool_registry is not None
    if executor is None and not refresh_browser:
        return
    settings = load_settings()
    plugins = load_plugins(settings, context.cwd)
    if executor is not None:
        from illusion.hooks.loader import load_hook_registry

        executor.update_registry(load_hook_registry(settings, plugins, context.cwd))
    if not refresh_browser:
        return

    from illusion.browser import BrowserConfig, BrowserManager
    from illusion.tools.browser_tools import apply_browser_toggle

    enabled = native_browser_enabled(plugins)
    manager: Any = context.engine._tool_metadata.get("browser_manager")
    if enabled and manager is None:
        manager = BrowserManager(BrowserConfig(
            kernel=settings.browser.kernel,
            headless=settings.browser.headless,
            viewport_width=settings.browser.viewport_width,
            viewport_height=settings.browser.viewport_height,
            proxy=settings.browser.proxy,
        ))
    if not enabled and manager is not None:
        await manager.aclose(purge=True)
        manager = None
    apply_browser_toggle(context.engine, context.tool_registry, manager, enabled)
