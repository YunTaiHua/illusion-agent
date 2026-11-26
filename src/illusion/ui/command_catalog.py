"""
Web 和终端的斜杠命令目录
=========================

本模块在运行时内置命令与工作区斜杠资源之上，构建 Web 与终端
共享的补全目录和高亮资源条目。

主要功能：
    - runtime_builtin_names: 按注册顺序返回内置命令名（含前导斜杠）
    - web_builtin_names: 按 ``WEB_COMMAND_NAMES`` 白名单过滤 Web 端
      可展示的内置命令
    - slash_resource_entries: 构造可引用的资源条目，同名时保留实际
      会被斜杠调用的命令，并标注短名能否唯一解析
    - terminal_command_catalog: 用同一份资源快照生成终端行首命令
      列表和带类型的行内候选
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

WEB_COMMAND_NAMES = ("compact", "export", "init", "rename", "goal")


def runtime_builtin_names(bundle: Any) -> list[str]:
    """按注册顺序返回内置命令，名称包含前导斜杠。"""
    return [f"/{command.name}" for command in bundle.commands.list_commands()]


def dynamic_command_entries(cwd: str) -> list[dict[str, str]]:
    from illusion.commands.slash_skills import list_slash_commands

    try:
        return list_slash_commands(cwd)
    except (OSError, TypeError, ValueError):
        return []


def web_builtin_names(bundle: Any) -> list[str]:
    available = set(runtime_builtin_names(bundle))
    return [f"/{name}" for name in WEB_COMMAND_NAMES if f"/{name}" in available]


def slash_resource_entries(
    bundle: Any,
    entries: Iterable[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """构造可引用的资源，同名时保留实际会被斜杠调用的命令。"""
    builtins = {name.casefold() for name in runtime_builtin_names(bundle)}
    source = entries if entries is not None else dynamic_command_entries(bundle.cwd)
    selected: dict[str, dict[str, Any]] = {}
    leaf_counts: Counter[str] = Counter()
    for raw in source:
        name = raw["name"].strip().removeprefix("/")
        if not name or any(c.isspace() for c in name):
            continue
        key = name.casefold()
        if f"/{key}" in builtins:
            continue
        leaf_counts[key.rsplit(":", 1)[-1]] += 1
        kind = raw.get("kind", "skill")
        if key not in selected or kind == "command":
            selected[key] = {
                "name": f"/{name}",
                "kind": kind,
                "source": raw.get("source", ""),
                "description": raw.get("description", ""),
            }

    # 短名只有在实际解析会命中同一条目时才可高亮。完整名始终可以用来补全。
    for key, entry in selected.items():
        _, separator, leaf = key.rpartition(":")
        entry["accepts_short_name"] = bool(
            separator and leaf_counts[leaf] == 1
            and f"/{leaf}" not in builtins and leaf not in selected
        )
    return list(selected.values())


def terminal_command_catalog(bundle: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """用同一份资源快照生成行首命令列表和带类型的行内候选。"""
    resources = slash_resource_entries(bundle)
    return runtime_builtin_names(bundle) + [entry["name"] for entry in resources], resources
