"""
记忆管理斜杠命令
================

/memory — 查看和管理项目记忆

用法：
    /memory                         显示记忆目录信息（terminal 无参数时打开开关选择框）
    /memory on | off | toggle       开启/关闭/切换记忆功能
    /memory auto on | off | toggle  开启/关闭/切换后台自动提取与整合
    /memory status                  显示记忆功能与后台自动提取状态
    /memory list                    列出所有记忆文件
    /memory show NAME               显示指定记忆内容
    /memory add TITLE :: CONTENT    创建记忆（默认根目录）
    /memory add user TITLE :: CONTENT   按类型创建记忆（user/feedback/project/reference）
    /memory remove NAME             删除记忆
"""

from __future__ import annotations

from pathlib import Path

from illusion.commands.types import CommandContext, CommandResult
from illusion.config.i18n import t
from illusion.config.settings import load_settings, save_settings
from illusion.memory import (
    add_memory_entry,
    get_memory_dir_for_cwd,
    get_memory_entrypoint,
    list_memory_files,
    remove_memory_entry,
)
from illusion.memory.paths import MEMORY_TYPE_DIRS


async def _memory_toggle(args: str, context: CommandContext) -> CommandResult:
    """处理记忆功能开关子命令（on/off/toggle/status、auto on/off/toggle）。"""
    del context
    settings = load_settings()
    tokens = args.split()
    if tokens and tokens[0] == "auto":
        state = settings.memory.auto_extract
        action = tokens[1] if len(tokens) == 2 else "status"
        if action == "status":
            return CommandResult(
                message=t("memory_auto_show", state=("on" if state else "off"))
            )
        enabled = {"on": True, "off": False, "toggle": not state}.get(action)
        if enabled is None:
            return CommandResult(message=t("memory_usage"))
        if enabled and not settings.memory.enabled:
            # 记忆关闭时不得单独开启后台提取/整合
            return CommandResult(message=t("memory_auto_need_mem"))
        settings.memory.auto_extract = enabled
        save_settings(settings)
        return CommandResult(
            message=t("memory_auto_on" if enabled else "memory_auto_off")
        )
    action = tokens[0] if tokens else "status"
    state = settings.memory.enabled
    if action == "status":
        return CommandResult(
            message=t(
                "memory_show",
                enabled=("on" if state else "off"),
                auto=("on" if settings.memory.auto_extract else "off"),
            )
        )
    enabled = {"on": True, "off": False, "toggle": not state}.get(action)
    if enabled is None:
        return CommandResult(message=t("memory_usage"))
    settings.memory.enabled = enabled
    if not enabled:
        # 记忆关闭连带关闭后台自动提取/整合
        settings.memory.auto_extract = False
    save_settings(settings)
    return CommandResult(
        message=t("memory_enabled" if enabled else "memory_disabled")
    )


async def memory_handler(args: str, context: CommandContext) -> CommandResult:
    """记忆管理命令处理器"""
    tokens = args.split(maxsplit=1)
    if not tokens:
        memory_dir = get_memory_dir_for_cwd(context.cwd)
        entrypoint = get_memory_entrypoint(context.cwd)
        return CommandResult(
            message=f"Memory directory: {memory_dir}\nEntrypoint: {entrypoint}"
        )
    action = tokens[0]
    rest = tokens[1] if len(tokens) == 2 else ""
    # 功能开关子命令（on/off/toggle/status、auto on/off）
    if (
        action in {"on", "off", "toggle", "status"}
        or action == "auto"
    ):
        return await _memory_toggle(args, context)
    if action == "list":
        memory_files = list_memory_files(context.cwd)
        if not memory_files:
            return CommandResult(message="No memory files.")
        # 显示相对记忆目录的路径（含类型子目录）
        memory_dir = get_memory_dir_for_cwd(context.cwd)
        lines = [
            str(p.relative_to(memory_dir)).replace("\\", "/") for p in memory_files
        ]
        return CommandResult(message="\n".join(lines))
    if action == "show" and rest:
        memory_dir = get_memory_dir_for_cwd(context.cwd)
        # 支持 "user/user_role" 或 "user_role" 两种形式
        path: Path | None = memory_dir / rest
        if path is not None and not path.exists():
            path = memory_dir / f"{rest}.md"
        if path is not None and not path.exists():
            # 在类型子目录中查找
            for sub in MEMORY_TYPE_DIRS:
                candidate = memory_dir / sub / rest
                if candidate.exists():
                    path = candidate
                    break
                candidate = memory_dir / sub / f"{rest}.md"
                if candidate.exists():
                    path = candidate
                    break
        if path is None or not path.exists():
            return CommandResult(message=f"Memory entry not found: {rest}")
        return CommandResult(message=path.read_text(encoding="utf-8"))
    if action == "add" and rest:
        title, separator, content = rest.partition("::")
        if not separator or not title.strip() or not content.strip():
            return CommandResult(
                message="Usage: /memory add [user|feedback|project|reference] TITLE :: CONTENT"
            )
        # 可选类型前缀：/memory add user TITLE :: CONTENT
        memory_type = ""
        first_word, _, remainder = title.strip().partition(" ")
        if first_word in MEMORY_TYPE_DIRS and "::" not in remainder:
            memory_type = first_word
            title = remainder
        path = add_memory_entry(
            context.cwd, title.strip(), content.strip(), memory_type=memory_type
        )
        return CommandResult(message=f"Added memory entry {path.name} (type: {memory_type or 'root'})")
    if action == "remove" and rest:
        if remove_memory_entry(context.cwd, rest.strip()):
            return CommandResult(message=f"Removed memory entry {rest.strip()}")
        return CommandResult(message=f"Memory entry not found: {rest.strip()}")
    return CommandResult(
        message="Usage: /memory [list|show NAME|add [user|feedback|project|reference] TITLE :: CONTENT|remove NAME]"
    )
