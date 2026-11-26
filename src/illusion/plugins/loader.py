"""
插件发现和加载模块
==================

实现插件的发现和加载功能。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Any

from pydantic import TypeAdapter, ValidationError

from illusion.config.paths import get_config_dir, get_project_config_dir
from illusion.mcp.types import McpServerConfig
from illusion.permissions.loader import load_project_permissions, set_project_plugin_preference
from illusion.plugins.schemas import LegacyPluginManifest, PluginManifest
from illusion.plugins.types import LoadedPlugin, PluginScope, is_builtin_browser_install
from illusion.skills.types import SkillDefinition

logger = logging.getLogger(__name__)


# 清单候选顺序是插件加载、安装和验证共用的稳定契约。
MANIFEST_RELATIVE_PATHS: tuple[str, ...] = (
    ".illusion-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    ".zcode-plugin/plugin.json",
    "plugin.json",
)


def get_user_plugins_dir() -> Path:
    """获取用户插件目录，不创建目录。

    插件发现可在目录不存在时安全调用。安装或内置插件 seed 写入前，
    由对应写入入口显式创建目录。
    """
    return get_config_dir() / "plugins"


def get_project_plugins_dir(cwd: str | Path) -> Path:
    """获取项目插件目录，不创建目录。

    ``cwd`` 无效时回退到用户插件目录。插件发现不会因此创建任一目录，
    写入项目插件前由写入入口显式创建目标父目录。
    """
    return get_project_config_dir(cwd) / "plugins"


def _find_manifest(plugin_dir: Path) -> Path | None:
    """按固定优先级查找插件清单，首个存在的文件决定插件格式。"""
    root = Path(plugin_dir)
    for relative_path in MANIFEST_RELATIVE_PATHS:
        candidate = root / relative_path
        try:
            if candidate.is_file():
                return candidate
        except OSError as exc:
            logger.debug("访问插件清单失败，继续查找: %s: %s", candidate, exc)
    return None


def _load_manifest(plugin_dir: Path) -> tuple[Path, PluginManifest] | None:
    """读取并验证插件根目录中的清单。"""
    manifest_path = _find_manifest(plugin_dir)
    if manifest_path is None:
        return None
    try:
        manifest = PluginManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValidationError) as exc:
        logger.warning("插件清单无效，跳过 %s: %s", manifest_path, exc)
        return None
    return manifest_path, manifest


def _canonical_path(path: Path) -> str:
    """返回用于资源去重的规范路径。"""
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path.absolute()))


def _resolve_plugin_path(plugin_root: Path, raw_path: str, component: str) -> Path | None:
    """把清单中的相对资源路径解析到插件根内。"""
    if not isinstance(raw_path, str) or not raw_path.strip():
        logger.warning("插件 %s 资源路径必须是非空字符串: %r", component, raw_path)
        return None

    value = raw_path.strip()
    path_value = Path(value)
    windows_value = PureWindowsPath(value)
    if path_value.is_absolute() or windows_value.is_absolute() or windows_value.drive:
        logger.warning("插件 %s 资源路径必须是相对路径: %s", component, value)
        return None

    root = Path(plugin_root).resolve()
    try:
        resolved = (root / path_value).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        logger.warning("插件 %s 资源路径越出安装根，已跳过: %s (%s)", component, value, exc)
        return None
    if resolved == root:
        logger.warning("插件 %s 资源路径不能指向安装根: %s", component, value)
        return None
    return resolved


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _warn_unsupported_manifest_value(component: str, value: Any) -> None:
    logger.warning("不支持的插件 %s 清单格式，已跳过: %r", component, value)


def _string_paths(value: Any, component: str) -> list[str]:
    """读取资源路径字段中的 string/list，并报告其它元素。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        paths: list[str] = []
        for item in value:
            paths.extend(_string_paths(item, component))
        return paths
    if value is not None:
        _warn_unsupported_manifest_value(component, value)
    return []


def _resolve_resource_paths(
    plugin_root: Path,
    component: str,
    raw_paths: list[str],
    *,
    warn_missing: bool = True,
) -> list[Path]:
    """解析并按真实路径去重资源入口。"""
    paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        resolved = _resolve_plugin_path(plugin_root, raw_path, component)
        if resolved is None:
            continue
        key = _canonical_path(resolved)
        if key in seen:
            continue
        seen.add(key)
        if not _safe_is_file(resolved) and not _safe_is_dir(resolved):
            if warn_missing:
                logger.warning("插件 %s 资源不存在，已跳过: %s", component, raw_path)
            continue
        paths.append(resolved)
    return paths


def _plugin_directories(cwd: str | Path) -> Iterator[tuple[PluginScope, Path]]:
    """从实际扫描的根标记来源。项目根与用户根相同时只扫描用户根。"""
    user_root = get_user_plugins_dir()
    project_root = get_project_plugins_dir(cwd)
    roots: list[tuple[PluginScope, Path]] = [("user", user_root)]
    if _canonical_path(project_root) != _canonical_path(user_root):
        roots.append(("project", project_root))
    for scope, root in roots:
        if not _safe_is_dir(root):
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError as exc:
            logger.debug("遍历插件目录失败，跳过: %s: %s", root, exc)
            continue
        for path in entries:
            if not path.name.startswith(".") and _safe_is_dir(path):
                yield scope, path


def discover_plugin_paths(cwd: str | Path) -> list[Path]:
    """发现具有清单的安装目录，按真实路径去重。"""
    paths: list[Path] = []
    seen_paths: set[str] = set()
    for _, path in _plugin_directories(cwd):
        if _find_manifest(path) is not None:
            key = _canonical_path(path)
            if key not in seen_paths:
                seen_paths.add(key)
                paths.append(path)
    return paths


def native_browser_enabled(plugins: Iterable[LoadedPlugin]) -> bool:
    """只允许标准用户安装位置的 browser-use 提供原生工具。"""
    return any(p.enabled and p.is_builtin_browser for p in plugins)


@dataclass(frozen=True)
class _PluginInstall:
    scope: PluginScope
    path: Path
    manifest_path: Path
    manifest: PluginManifest

    @property
    def priority(self) -> int:
        if is_builtin_browser_install(self.manifest.name, self.scope, self.path):
            return 2
        return 1 if self.scope == "project" else 0

    @property
    def legacy_name(self) -> str | None:
        """升级前首个存在的有效清单身份，只用于匹配硬拒绝。"""
        for relative in ("plugin.json", ".claude-plugin/plugin.json"):
            candidate = self.path / relative
            if candidate == self.manifest_path:
                return None
            try:
                if candidate.exists():
                    legacy = LegacyPluginManifest.model_validate_json(candidate.read_text(encoding="utf-8"))
                    return legacy.name
            except (OSError, ValueError):
                # 旧版不会跳过存在但无效的首选清单再加载后续清单。
                return None
        return None


def _discover_installs(cwd: str | Path) -> dict[str, _PluginInstall]:
    """在读取开关及加载资源前按清单名称选择安装，同级保留目录排序首项。"""
    installs: dict[str, _PluginInstall] = {}
    for scope, path in _plugin_directories(cwd):
        loaded = _load_manifest(path)
        if loaded is None:
            continue
        install = _PluginInstall(scope, path, *loaded)
        previous = installs.get(install.manifest.name)
        if previous is None or install.priority > previous.priority:
            installs[install.manifest.name] = install
    return installs


def load_plugins(settings: Any, cwd: str | Path) -> list[LoadedPlugin]:
    """加载当前工作区可见的插件，保留可重新启用的普通禁用项。"""
    try:
        permissions = load_project_permissions(cwd, strict=True)
    except ValueError as exc:
        logger.warning("Skipping plugins: %s", exc)
        return []
    if "*" in permissions.denied_plugins:
        return []

    plugins: list[LoadedPlugin] = []
    for name, install in _discover_installs(cwd).items():
        if permissions.denied_plugins and permissions.is_plugin_denied(name, legacy_name=install.legacy_name):
            continue
        preferences = settings.enabled_plugins if install.scope == "user" else permissions.enabled_plugins
        plugins.append(_load_install(install, preferences))
    return plugins


def set_plugin_enabled(
    name: str, enabled: bool, cwd: str | Path, *, scope: PluginScope | None = None
) -> PluginScope:
    """设置当前工作区实际安装的插件。scope 是来源断言，成功时返回实际来源。"""
    if not isinstance(name, str) or not name.strip() or not isinstance(enabled, bool):
        raise ValueError("A plugin name and a boolean enabled state are required.")
    if scope not in (None, "user", "project"):
        raise ValueError(f"Invalid plugin scope: {scope!r} (expected user or project).")

    install = _discover_installs(cwd).get(name)
    if install is None:
        raise ValueError(f"Plugin '{name}' not found in this workspace.")
    if scope is not None and scope != install.scope:
        raise ValueError(
            f"Plugin '{name}' has scope '{install.scope}', not '{scope}'. Refresh the plugin list."
        )

    if install.scope == "project":
        set_project_plugin_preference(cwd, name, enabled, legacy_name=install.legacy_name)
    else:
        from illusion.config.paths import get_config_file_path
        from illusion.config.settings import load_settings, save_settings
        from illusion.utils.atomic_write import config_write_lock

        try:
            with config_write_lock(get_config_file_path()):
                load_project_permissions(cwd, strict=True).require_plugin_allowed(name, legacy_name=install.legacy_name)
                settings = load_settings()
                settings.enabled_plugins[name] = enabled
                save_settings(settings)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"Cannot update user settings for plugin '{name}': {exc}") from exc
    return install.scope


def load_plugin(path: Path, enabled_plugins: dict[str, bool]) -> LoadedPlugin | None:
    """独立加载单个插件目录，保留原有调用约定。"""
    path = Path(path)
    loaded = _load_manifest(path)
    if loaded is None:
        return None
    return _load_install(_PluginInstall("user", path, *loaded), enabled_plugins)


def _load_install(install: _PluginInstall, enabled_plugins: dict[str, bool]) -> LoadedPlugin:
    plugin_root = install.path.resolve()
    manifest = install.manifest
    enabled = (enabled_plugins or {}).get(manifest.name, manifest.enabled_by_default)

    # skills 有显式声明时使用声明的完整入口集合，否则使用 skills_dir 默认入口。
    if manifest.skills is None:
        skill_paths = _resolve_resource_paths(
            plugin_root,
            "skills",
            [manifest.skills_dir],
            warn_missing=False,
        )
    else:
        skill_paths = _resolve_resource_paths(
            plugin_root,
            "skills",
            _string_paths(manifest.skills, "skills"),
        )
    skills: list[SkillDefinition] = []
    for skill_path in skill_paths:
        _append_unique_skills(
            skills,
            _load_plugin_skills(skill_path, manifest.name, plugin_root),
        )

    # commands 默认目录与显式路径都可用，清单映射在同一处展开。
    commands_value = manifest.commands
    command_paths = ["commands"]
    if not isinstance(commands_value, dict):
        command_paths.extend(_string_paths(commands_value, "commands"))
    command_roots = _resolve_resource_paths(
        plugin_root,
        "commands",
        command_paths,
        warn_missing=False,
    )
    commands: list[SkillDefinition] = []
    for command_path in command_roots:
        _append_unique_skills(
            commands,
            _load_plugin_skills(
                command_path,
                manifest.name,
                plugin_root,
                kind="command",
            ),
        )
    _append_unique_skills(
        commands,
        _load_plugin_command_mapping(commands_value, manifest.name, plugin_root),
    )

    # agents 复用现有目录解析器，插件来源会在其返回结果上统一标记。
    agents_value = manifest.agents
    agent_paths = ["agents"]
    if not isinstance(agents_value, dict):
        agent_paths.extend(_string_paths(agents_value, "agents"))
    agent_roots = _resolve_resource_paths(
        plugin_root,
        "agents",
        agent_paths,
        warn_missing=False,
    )
    agents = _load_plugin_agents_from_paths(agent_roots, plugin_root)
    agents.extend(_load_plugin_agent_mapping(agents_value, plugin_root))
    agents = _dedupe_agents(agents)

    hooks = _load_plugin_hooks_from_manifest(manifest, plugin_root)
    mcp = _load_plugin_mcp_from_manifest(manifest, plugin_root)

    return LoadedPlugin(
        manifest=manifest,
        path=plugin_root,
        enabled=enabled,
        scope=install.scope,
        skills=skills,
        hooks=hooks,
        mcp_servers=mcp,
        commands=commands,
        agents=agents,
    )


def _append_unique_skills(
    target: list[SkillDefinition],
    candidates: list[SkillDefinition],
) -> None:
    """按最终名称去重技能和命令，保留首次声明。"""
    seen_names = {skill.name for skill in target}
    for skill in candidates:
        if skill.name in seen_names:
            continue
        seen_names.add(skill.name)
        target.append(skill)


def _load_plugin_agents(agents_dir: Path) -> list[Any]:
    """从单个插件 agents/ 目录加载代理定义。"""
    return _load_plugin_agents_from_paths([agents_dir], Path(agents_dir).parent)


def _load_plugin_agents_from_paths(paths: list[Path], plugin_root: Path) -> list[Any]:
    """通过现有目录解析器加载目录或单个 Markdown 代理文件。"""
    try:
        from illusion.coordinator.agent_definitions import load_agents_dir
    except ImportError as exc:
        logger.debug("加载插件代理定义失败，解析器不可用: %s", exc)
        return []

    agents: list[Any] = []
    seen_files: set[str] = set()
    for resource_path in paths:
        if _safe_is_dir(resource_path):
            source_dir = resource_path
            selected_filename: str | None = None
        elif _safe_is_file(resource_path) and resource_path.suffix.lower() == ".md":
            source_dir = resource_path.parent
            selected_filename = resource_path.stem
        else:
            logger.warning("插件 agents 资源必须是目录或 Markdown 文件，已跳过: %s", resource_path)
            continue

        try:
            loaded_agents = load_agents_dir(source_dir)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("加载插件 agents 失败，已跳过 %s: %s", resource_path, exc)
            continue

        for agent in loaded_agents:
            filename = getattr(agent, "filename", None)
            if selected_filename is not None and filename != selected_filename:
                continue
            if isinstance(filename, str) and filename.startswith("."):
                continue
            source_file = source_dir / f"{filename}.md" if isinstance(filename, str) else None
            key = _canonical_path(source_file) if source_file is not None else f"name:{agent.name}"
            if key in seen_files:
                continue
            seen_files.add(key)
            # 插件代理不能通过自身 frontmatter 注册作用域 hooks。
            agents.append(agent.model_copy(update={"source": "plugin", "hooks": None}))
    return agents


def _load_plugin_agent_mapping(
    value: Any,
    plugin_root: Path,
) -> list[Any]:
    """加载 agents 映射中指向文件的条目。"""
    if not isinstance(value, dict):
        return []
    mapped: list[Any] = []
    for name, metadata in value.items():
        source: str | None = None
        if isinstance(metadata, str):
            source = metadata
        elif isinstance(metadata, dict):
            raw_source = metadata.get("source", metadata.get("path"))
            if isinstance(raw_source, str):
                source = raw_source
            else:
                _warn_unsupported_manifest_value("agents", {name: metadata})
        else:
            _warn_unsupported_manifest_value("agents", {name: metadata})
        if source is None:
            continue
        paths = _resolve_resource_paths(plugin_root, "agents", [source])
        mapped.extend(_load_plugin_agents_from_paths(paths, plugin_root))
    return mapped


def _dedupe_agents(agents: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for agent in agents:
        name = str(getattr(agent, "name", ""))
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(agent)
    return result


def _make_plugin_skill(
    skill: SkillDefinition,
    plugin_name: str,
    plugin_root: Path,
    skill_root: Path,
    path: Path,
    *,
    parse_hooks: bool,
    kind: str,
) -> SkillDefinition:
    """把通用解析结果补充插件来源和两个路径根。"""
    fields = getattr(SkillDefinition, "__dataclass_fields__", {})
    values: dict[str, Any] = {
        "name": f"{plugin_name}:{skill.name}",
        "description": skill.description,
        "content": skill.content,
        "source": "plugin",
        "path": str(path),
        "allowed_tools": skill.allowed_tools,
        "model": skill.model,
        "hooks": skill.hooks if parse_hooks else None,
        "context": skill.context,
        "agent": skill.agent,
        "disable_model_invocation": skill.disable_model_invocation,
        "effort": skill.effort,
        "skill_root": str(skill_root),
        "kind": kind,
    }
    if "plugin_root" in fields:
        values["plugin_root"] = str(plugin_root)
    return SkillDefinition(**values)


def _load_plugin_skills(
    path: Path,
    plugin_name: str,
    plugin_root: Path,
    *,
    parse_hooks: bool = True,
    kind: str = "skill",
) -> list[SkillDefinition]:
    """从文件或目录加载插件技能和命令。"""
    if not _safe_is_file(path) and not _safe_is_dir(path):
        return []

    from illusion.skills.loader import (
        _read_text_safe,
        _resolve_entry,
        _safe_iterdir,
        parse_skill_markdown,
    )

    def load_file(file_path: Path) -> SkillDefinition | None:
        suffix = file_path.suffix.lower()
        if suffix == ".md":
            return _load_md_skill(
                file_path,
                plugin_name,
                plugin_root,
                parse_hooks=parse_hooks,
                kind=kind,
                skill_root=file_path.parent,
            )
        if suffix in (".yaml", ".yml"):
            from illusion.skills.loader import _load_yaml_skill

            parsed = _load_yaml_skill(file_path, source="plugin")
            if parsed is None:
                return None
            return _make_plugin_skill(
                parsed,
                plugin_name,
                plugin_root,
                file_path.parent,
                file_path,
                parse_hooks=parse_hooks,
                kind=kind,
            )
        return None

    if _safe_is_file(path):
        loaded = load_file(path)
        return [loaded] if loaded is not None else []

    skills: list[SkillDefinition] = []
    seen_realpaths: set[str] = set()
    for item in _safe_iterdir(path):
        if item.name.startswith("."):
            continue
        resolved = _resolve_entry(item)
        if resolved is None:
            continue
        key = _canonical_path(resolved)
        if key in seen_realpaths:
            continue
        seen_realpaths.add(key)

        if _safe_is_dir(resolved):
            skill_md = resolved / "SKILL.md"
            if _safe_is_file(skill_md):
                content = _read_text_safe(skill_md)
                if content is not None:
                    skill = parse_skill_markdown(
                        resolved.name,
                        content,
                        skill_root=str(resolved),
                    )
                    skills.append(
                        _make_plugin_skill(
                            skill,
                            plugin_name,
                            plugin_root,
                            resolved,
                            skill_md,
                            parse_hooks=parse_hooks,
                            kind=kind,
                        )
                    )
                    continue

            # 没有 SKILL.md 的目录保留旧的散文件兼容行为。
            for nested in _safe_iterdir(resolved):
                if nested.name.startswith("."):
                    continue
                nested_resolved = _resolve_entry(nested)
                if nested_resolved is None or not _safe_is_file(nested_resolved):
                    continue
                nested_key = _canonical_path(nested_resolved)
                if nested_key in seen_realpaths:
                    continue
                seen_realpaths.add(nested_key)
                loaded = load_file(nested_resolved)
                if loaded is not None:
                    skills.append(loaded)
            continue

        loaded = load_file(resolved)
        if loaded is not None:
            skills.append(loaded)
    return skills


def _load_md_skill(
    path: Path,
    plugin_name: str,
    plugin_root: str | Path,
    *,
    parse_hooks: bool = True,
    kind: str = "skill",
    skill_root: Path | None = None,
) -> SkillDefinition | None:
    """加载单个 Markdown 技能或命令文件。"""
    from illusion.skills.loader import _read_text_safe, parse_skill_markdown

    content = _read_text_safe(path)
    if content is None:
        return None
    skill = parse_skill_markdown(path.stem, content)
    root = Path(plugin_root)
    return _make_plugin_skill(
        skill,
        plugin_name,
        root,
        skill_root or path.parent,
        path,
        parse_hooks=parse_hooks,
        kind=kind,
    )


def _load_plugin_command_mapping(
    value: Any,
    plugin_name: str,
    plugin_root: Path,
) -> list[SkillDefinition]:
    """加载 commands 清单的名称映射，支持 source/path 或 inline content。"""
    if not isinstance(value, dict):
        return []

    from illusion.skills.loader import parse_skill_markdown

    commands: list[SkillDefinition] = []
    for raw_name, metadata in value.items():
        name = str(raw_name).strip().lstrip("/")
        if not name:
            _warn_unsupported_manifest_value("commands", {raw_name: metadata})
            continue

        source: str | None = None
        content: str | None = None
        description: str | None = None
        if isinstance(metadata, str):
            source = metadata
        elif isinstance(metadata, dict):
            raw_source = metadata.get("source", metadata.get("path"))
            if isinstance(raw_source, str):
                source = raw_source
            raw_content = metadata.get("content")
            if isinstance(raw_content, str):
                content = raw_content
            raw_description = metadata.get("description")
            if isinstance(raw_description, str):
                description = raw_description
            if source is not None and content is not None:
                logger.warning("插件 commands 映射同时包含 source 和 content，已跳过: %s", name)
                continue
        else:
            _warn_unsupported_manifest_value("commands", {raw_name: metadata})
            continue

        if source is not None:
            paths = _resolve_resource_paths(plugin_root, "commands", [source])
            if not paths:
                continue
            for command_path in paths:
                loaded = _load_plugin_skills(
                    command_path,
                    plugin_name,
                    plugin_root,
                    kind="command",
                )
                if len(loaded) == 1 and _safe_is_file(command_path):
                    command = replace(
                        loaded[0],
                        name=f"{plugin_name}:{name}",
                        description=description or loaded[0].description,
                    )
                    commands.append(command)
                else:
                    commands.extend(loaded)
            continue
        if content is None:
            logger.warning(
                "插件 commands 映射必须提供 source/path 或 content，已跳过: %s",
                name,
            )
            continue

        parsed = parse_skill_markdown(name, content)
        if description:
            parsed = replace(parsed, name=name, description=description)
        else:
            parsed = replace(parsed, name=name)
        commands.append(
            _make_plugin_skill(
                parsed,
                plugin_name,
                plugin_root,
                plugin_root,
                plugin_root / f"<inline-command-{name}>",
                parse_hooks=True,
                kind="command",
            )
        )
    return commands


def _merge_hook_data(
    target: dict[str, list[Any]],
    source: dict[str, list[Any]],
) -> None:
    for event, matchers in source.items():
        target.setdefault(event, []).extend(matchers)


def _normalize_hooks_data(raw: Any, plugin_root: Path) -> dict[str, list[Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("hooks"), dict):
        raw = raw["hooks"]
    if not isinstance(raw, dict):
        logger.warning("插件 hooks 数据必须是事件映射对象，已跳过")
        return {}

    result: dict[str, list[Any]] = {}
    for event, matchers in raw.items():
        if not isinstance(event, str) or not isinstance(matchers, list):
            logger.warning("插件 hooks 事件必须映射到列表，已跳过: %s", event)
            continue
        result[event] = _substitute_plugin_vars_in_hooks(matchers, plugin_root)
    return result


def _load_plugin_hooks(path: Path, plugin_root: Path) -> dict[str, list[Any]]:
    """从一个插件 hooks JSON 文件读取事件映射。"""
    if not _safe_is_file(path):
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("读取插件 hooks 失败，已跳过 %s: %s", path, exc)
        return {}
    return _normalize_hooks_data(raw, plugin_root)


def _load_plugin_hooks_structured(path: Path, plugin_root: Path) -> dict[str, list[Any]]:
    """从结构化 hooks/hooks.json 文件读取事件映射。"""
    return _load_plugin_hooks(path, plugin_root)


def _load_plugin_hooks_from_manifest(
    manifest: PluginManifest,
    plugin_root: Path,
) -> dict[str, list[Any]]:
    defaults = _resolve_resource_paths(
        plugin_root,
        "hooks",
        ["hooks/hooks.json", manifest.hooks_file, "hooks.json"],
        warn_missing=False,
    )
    explicit_paths: list[str] = []
    inline_values: list[Any] = []
    if isinstance(manifest.hooks, (str, list)):
        for item in _flatten_manifest_items(manifest.hooks, "hooks"):
            if isinstance(item, str):
                explicit_paths.append(item)
            else:
                inline_values.append(item)
    elif isinstance(manifest.hooks, dict):
        inline_values.append(manifest.hooks)

    paths = _merge_unique_paths(
        defaults,
        _resolve_resource_paths(plugin_root, "hooks", explicit_paths),
    )
    result: dict[str, list[Any]] = {}
    for path in paths:
        if not _safe_is_file(path):
            logger.warning("插件 hooks 资源必须是文件，已跳过: %s", path)
            continue
        _merge_hook_data(result, _load_plugin_hooks(path, plugin_root))
    for inline in inline_values:
        if isinstance(inline, dict):
            _merge_hook_data(result, _normalize_hooks_data(inline, plugin_root))
        else:
            _warn_unsupported_manifest_value("hooks", inline)
    return result


def _flatten_manifest_items(value: Any, component: str) -> list[Any]:
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            result.extend(_flatten_manifest_items(item, component))
        return result
    if isinstance(value, (str, dict)):
        return [value]
    _warn_unsupported_manifest_value(component, value)
    return []


def _merge_unique_paths(*groups: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for group in groups:
        for path in group:
            key = _canonical_path(path)
            if key in seen:
                continue
            seen.add(key)
            result.append(path)
    return result


def _substitute_plugin_vars_in_hooks(data: Any, plugin_root: Path) -> Any:
    """递归替换插件根路径变量。"""
    if isinstance(data, str):
        root_str = str(plugin_root).replace("\\", "/")
        return data.replace("${CLAUDE_PLUGIN_ROOT}", root_str)
    if isinstance(data, dict):
        return {key: _substitute_plugin_vars_in_hooks(value, plugin_root) for key, value in data.items()}
    if isinstance(data, list):
        return [_substitute_plugin_vars_in_hooks(item, plugin_root) for item in data]
    return data


def _looks_like_mcp_server(value: Any) -> bool:
    return isinstance(value, dict) and any(
        key in value for key in ("type", "command", "url", "args")
    )


def _normalize_mcp_data(raw: Any, plugin_root: Path) -> dict[str, McpServerConfig]:
    if not isinstance(raw, dict):
        logger.warning("插件 MCP 数据必须是对象，已跳过")
        return {}
    raw = _substitute_plugin_vars_in_hooks(raw, plugin_root)
    if "mcpServers" in raw:
        servers = raw["mcpServers"]
    elif "mcp_servers" in raw:
        servers = raw["mcp_servers"]
    elif _looks_like_mcp_server(raw):
        servers = {"_inline": raw}
    else:
        servers = raw
    if not isinstance(servers, dict):
        logger.warning("插件 MCP 服务器映射必须是对象，已跳过")
        return {}

    adapter: TypeAdapter[McpServerConfig] = TypeAdapter(McpServerConfig)
    result: dict[str, McpServerConfig] = {}
    for name, config in servers.items():
        if not isinstance(name, str) or not isinstance(config, dict):
            logger.warning("插件 MCP 服务器配置格式不支持，已跳过: %s", name)
            continue
        try:
            from illusion.mcp.types import _normalize_server_config_type

            parsed = adapter.validate_python(_normalize_server_config_type(config))
        except (TypeError, ValueError, ValidationError) as exc:
            logger.warning("插件 MCP 服务器无效，已跳过 %s: %s", name, exc)
            continue
        if getattr(parsed, "enabled", True):
            result[name] = parsed
    return result


def _load_plugin_mcp(path: Path, plugin_root: Path | None = None) -> dict[str, McpServerConfig]:
    """从一个插件 MCP JSON 文件读取服务器映射。"""
    if not _safe_is_file(path):
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("读取插件 MCP 配置失败，已跳过 %s: %s", path, exc)
        return {}
    return _normalize_mcp_data(raw, plugin_root or path.parent)


def _load_plugin_mcp_from_manifest(
    manifest: PluginManifest,
    plugin_root: Path,
) -> dict[str, McpServerConfig]:
    defaults = _resolve_resource_paths(
        plugin_root,
        "mcpServers",
        [".mcp.json", manifest.mcp_file, "mcp.json"],
        warn_missing=False,
    )
    explicit_paths: list[str] = []
    inline_values: list[Any] = []
    value = manifest.mcp_servers
    if isinstance(value, (str, list)):
        for item in _flatten_manifest_items(value, "mcpServers"):
            if isinstance(item, str):
                explicit_paths.append(item)
            else:
                inline_values.append(item)
    elif isinstance(value, dict):
        inline_values.append(value)

    paths = _merge_unique_paths(
        defaults,
        _resolve_resource_paths(plugin_root, "mcpServers", explicit_paths),
    )
    result: dict[str, McpServerConfig] = {}
    for path in paths:
        if not _safe_is_file(path):
            logger.warning("插件 MCP 资源必须是文件，已跳过: %s", path)
            continue
        result.update(_load_plugin_mcp(path, plugin_root))
    for inline in inline_values:
        result.update(_normalize_mcp_data(inline, plugin_root))
    return result
