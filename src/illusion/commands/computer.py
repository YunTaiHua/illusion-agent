"""
Computer Use 斜杠命令
====================

/computer — 管理 computer use（开关 / 版本检查 / 更新）
"""

from __future__ import annotations

from illusion.commands.types import CommandContext, CommandResult
from illusion.config.settings import load_settings, save_settings


async def computer_handler(args: str, context: CommandContext) -> CommandResult:
    """computer use 管理命令处理器"""
    import asyncio

    del context
    settings = load_settings()
    enabled = settings.computer_use.enabled
    tokens = args.split()
    action = tokens[0] if tokens else "show"

    # 开关控制
    if action in ("on", "off", "toggle", "show"):
        if action == "toggle":
            enabled = not enabled
        elif action == "on":
            enabled = True
        elif action == "off":
            enabled = False
        # show：仅展示当前状态（版本检查放线程池避免阻塞事件循环）
        if action == "show":
            status = await asyncio.to_thread(_status_summary, enabled)
            return CommandResult(message=status)
        settings.computer_use.enabled = enabled
        save_settings(settings)
        extra = "" if not enabled else " Restart session to load the tools and skill."
        return CommandResult(
            message=f"Computer Use {'enabled' if enabled else 'disabled'}.{extra}"
        )

    # 版本检查
    if action == "version":
        from illusion.computer.binary import get_latest_version, get_local_version

        local, latest = await asyncio.gather(
            asyncio.to_thread(get_local_version),
            asyncio.to_thread(get_latest_version),
        )
        lines = [f"Computer Use binary: {local or '(not installed)'}"]
        if latest:
            lines.append(f"Latest version: {latest}")
            if local and latest != local:
                lines.append("A newer version is available. Run /computer update to upgrade.")
        else:
            lines.append("Latest version: (query failed)")
        return CommandResult(message="\n".join(lines))

    # 更新二进制
    if action == "update":
        from illusion.computer.binary import CuaNotFoundError, update_cua_binary

        try:
            result = await update_cua_binary()
        except (CuaNotFoundError, OSError) as exc:
            return CommandResult(message=f"Update failed: {exc}")
        if result.get("error"):
            return CommandResult(message=f"Update failed: {result['error']}")
        if not result.get("updated"):
            local = result.get("local_version") or "(not installed)"
            latest = result.get("latest_version")
            return CommandResult(message=f"No update needed (local: {local}, latest: {latest}).")
        return CommandResult(
            message=(
                "Computer Use binary updated: "
                f"{result.get('local_version') or '(unknown)'} -> "
                f"{result.get('latest_version') or '(unknown)'}"
            )
        )

    return CommandResult(
        message="Usage: /computer [show|on|off|toggle|version|update]"
    )


def _status_summary(enabled: bool) -> str:
    """生成 /computer 状态摘要。"""
    from illusion.computer.binary import find_cua_path, get_latest_version, get_local_version

    local = get_local_version()
    path = find_cua_path()
    latest = get_latest_version()
    lines = [
        f"Computer Use: {'on' if enabled else 'off'}",
        f"Binary: {local or '(not installed)'}",
    ]
    if path:
        lines.append(f"Path: {path}")
    if latest:
        lines.append(f"Latest: {latest}")
    return "\n".join(lines)
