"""
钩子加载器模块
==============

从设置和插件加载钩子注册表。
格式：{ "PreToolUse": [{ "matcher": "Bash", "hooks": [...] }] }
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from illusion.config.paths import get_project_hooks_dir, get_user_hooks_dir
from illusion.hooks.events import HookEvent
from illusion.hooks.schemas import (
    HookDefinition,
    HookMatcherDefinition,
    parse_hook_definition,
)

logger = logging.getLogger(__name__)


class HookRegistry:
    """钩子注册表，按事件存储 HookMatcherDefinition 列表。"""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookMatcherDefinition]] = defaultdict(list)

    def register(self, event: HookEvent, matcher: HookMatcherDefinition) -> None:
        """注册一个钩子匹配器。"""
        self._hooks[event].append(matcher)

    def register_hook(self, event: HookEvent, hook: HookDefinition, matcher: str = "") -> None:
        """注册单个钩子（便捷方法）。"""
        self._hooks[event].append(HookMatcherDefinition(matcher=matcher, hooks=[hook]))

    def get(self, event: HookEvent) -> list[HookMatcherDefinition]:
        """获取指定事件的所有匹配器。"""
        return list(self._hooks.get(event, []))

    def get_hooks_flat(self, event: HookEvent) -> list[HookDefinition]:
        """获取指定事件的所有钩子（扁平列表）。"""
        hooks = []
        for matcher in self.get(event):
            hooks.extend(matcher.hooks)
        return hooks

    def summary(self) -> str:
        """生成人类可读的钩子摘要。"""
        lines: list[str] = []
        for event in HookEvent:
            matchers = self.get(event)
            if not matchers:
                continue
            lines.append(f"{event.value}:")
            for m in matchers:
                for hook in m.hooks:
                    detail = getattr(hook, "command", None) or getattr(hook, "prompt", None) or getattr(hook, "url", None) or ""
                    suffix = f" matcher={m.matcher}" if m.matcher else ""
                    lines.append(f"  - {hook.type}{suffix}: {detail}")
        return "\n".join(lines)


def _normalize_hooks_value(value: Any) -> list[HookMatcherDefinition]:
    """将事件对应的值规范化为 HookMatcherDefinition 列表。"""
    if not isinstance(value, list):
        return []
    result: list[HookMatcherDefinition] = []
    for item in value:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        if "hooks" not in item or not isinstance(item.get("hooks"), list):
            continue
        matcher = item.get("matcher", "")
        if matcher is None:
            matcher = ""
        if not isinstance(matcher, str):
            continue
        hooks: list[HookDefinition] = []
        for raw_hook in item["hooks"]:
            if not isinstance(raw_hook, dict) or raw_hook.get("enabled", True) is False:
                continue
            try:
                hooks.append(parse_hook_definition(raw_hook))
            except (TypeError, ValueError):
                continue
        if hooks:
            result.append(HookMatcherDefinition(matcher=matcher, hooks=hooks))
    return result


def _normalize_hook_config(value: Any) -> dict[str, list[HookMatcherDefinition]]:
    """解析事件映射或带 hooks 根节点的本地声明。"""
    if not isinstance(value, dict) or value.get("enabled", True) is False:
        return {}
    events = value.get("hooks", value)
    if not isinstance(events, dict):
        return {}
    result: dict[str, list[HookMatcherDefinition]] = {}
    for raw_event, hooks_value in events.items():
        if not isinstance(raw_event, str):
            continue
        matchers = _normalize_hooks_value(hooks_value)
        if matchers:
            result[raw_event] = matchers
    return result


def _local_hook_config_paths(root: Path) -> list[Path]:
    """返回一个配置根目录下的本地 Hook JSON 候选路径。

    只计算候选路径并读取现有文件，不创建 ``hooks`` 或配置根目录。
    """
    hooks_dir = root / "hooks"
    paths = [root / "hooks.json", hooks_dir / "hooks.json"]
    try:
        paths.extend(sorted(path for path in hooks_dir.glob("*.json") if path.name != "hooks.json"))
    except OSError as exc:
        logger.warning("Failed to scan Hook config directory %s: %s", hooks_dir, exc)
    return paths


def _load_local_hook_configs(cwd: str | Path | None) -> list[dict[str, list[HookMatcherDefinition]]]:
    """从用户配置根和目标项目根读取本地 Hook 声明，不创建目录。"""
    roots = [get_user_hooks_dir().parent]
    if cwd is not None:
        roots.append(get_project_hooks_dir(cwd).parent)

    configs: list[dict[str, list[HookMatcherDefinition]]] = []
    seen_paths: set[str] = set()
    for root in roots:
        for path in _local_hook_config_paths(root):
            try:
                key = os.path.normcase(str(path.resolve()))
            except OSError:
                key = os.path.normcase(str(path.absolute()))
            if key in seen_paths:
                continue
            try:
                is_file = path.is_file()
            except OSError as exc:
                logger.warning("Failed to inspect Hook config %s: %s", path, exc)
                continue
            if not is_file:
                continue
            seen_paths.add(key)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load Hook config %s: %s", path, exc)
                continue
            parsed = _normalize_hook_config(raw)
            if parsed:
                configs.append(parsed)
    return configs


def load_hook_registry(settings: Any, plugins: Any = None, cwd: str | Path | None = None) -> HookRegistry:
    """从设置、本地 JSON 声明和已启用插件加载钩子注册表。"""
    registry = HookRegistry()

    # 加载项目级权限配置
    from illusion.permissions.loader import load_project_permissions
    project_permissions = load_project_permissions(cwd) if cwd else None

    # 检查是否禁用所有 hooks
    if project_permissions and "*" in project_permissions.denied_hooks:
        return registry

    settings_hooks = _normalize_hook_config(getattr(settings, "hooks", {}))
    for raw_event, matchers in settings_hooks.items():
        try:
            event = HookEvent(str(raw_event))
        except ValueError:
            continue
        if project_permissions and event.value in project_permissions.denied_hooks:
            continue
        for matcher_def in matchers:
            registry.register(event, matcher_def)

    for local_config in _load_local_hook_configs(cwd):
        for raw_event, matchers in local_config.items():
            try:
                event = HookEvent(str(raw_event))
            except ValueError:
                continue
            if project_permissions and event.value in project_permissions.denied_hooks:
                continue
            for matcher_def in matchers:
                registry.register(event, matcher_def)

    for plugin in plugins or []:
        if not getattr(plugin, "enabled", True):
            continue
        plugin_hooks = _normalize_hook_config(getattr(plugin, "hooks", {}))
        for raw_event, matchers in plugin_hooks.items():
            try:
                event = HookEvent(str(raw_event))
            except ValueError:
                continue

            # 检查是否禁用特定事件的 hooks
            if project_permissions and event.value in project_permissions.denied_hooks:
                continue

            for matcher_def in matchers:
                registry.register(event, matcher_def)

    return registry
