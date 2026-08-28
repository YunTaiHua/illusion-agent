"""
插件发现和加载模块
==================

实现插件的发现和加载功能。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path, PurePath
from typing import Any

from pydantic import ValidationError

from illusion.config.paths import get_config_dir
from illusion.mcp.types import McpServerConfig
from illusion.plugins.schemas import PluginManifest
from illusion.plugins.types import LoadedPlugin
from illusion.skills.types import SkillDefinition

logger = logging.getLogger(__name__)


def get_user_plugins_dir() -> Path:
    """获取用户插件目录。"""
    path = get_config_dir() / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_project_plugins_dir(cwd: str | Path) -> Path:
    """获取项目插件目录。

    cwd 无效（如守护进程启动目录被删除）时回退到用户级插件目录，
    避免 WinError 267 阻断 build_runtime。

    加固：cwd 若非真实路径类型（如测试误传 MagicMock），直接回退到
    用户级插件目录，避免在 cwd 下创建 ``MagicMock/.../.illusion`` 目录。
    """
    if not isinstance(cwd, (str, PurePath)):
        return get_user_plugins_dir()
    try:
        path = Path(cwd).resolve() / ".illusion" / "plugins"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except (OSError, FileNotFoundError):
        # cwd 失效，回退到用户级插件目录（已由 get_user_plugins_dir 保证存在）
        return get_user_plugins_dir()


def _find_manifest(plugin_dir: Path) -> Path | None:
    """查找插件清单文件。"""
    for candidate in [
        plugin_dir / "plugin.json",
        plugin_dir / ".claude-plugin" / "plugin.json",
    ]:
        if candidate.exists():
            return candidate
    return None


def discover_plugin_paths(cwd: str | Path) -> list[Path]:
    """发现插件目录。"""
    roots = [get_user_plugins_dir(), get_project_plugins_dir(cwd)]
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if path.is_dir() and _find_manifest(path) is not None:
                paths.append(path)
    return paths


def load_plugins(settings: Any, cwd: str | Path) -> list[LoadedPlugin]:
    """从磁盘加载所有插件。"""
    # 加载项目级权限配置
    from illusion.permissions.loader import load_project_permissions
    project_permissions = load_project_permissions(cwd)

    # 检查是否禁用所有插件
    if "*" in project_permissions.denied_plugins:
        return []

    plugins: list[LoadedPlugin] = []
    for path in discover_plugin_paths(cwd):
        plugin = load_plugin(path, settings.enabled_plugins)
        if plugin is not None:
            # 检查是否禁用特定插件
            if plugin.manifest.name in project_permissions.denied_plugins:
                continue
            plugins.append(plugin)

    # 内置 Computer Use 插件（以 plugin 方式注入 MCP 工具与 skill）。
    # 由 settings.computer_use.enabled 开关控制：关闭时不注入；
    # 同时遵守项目级 denied_plugins（与磁盘插件一致）。
    from illusion.computer.plugin import build_computer_plugin

    computer_plugin = build_computer_plugin(settings)
    if computer_plugin is not None and computer_plugin.name in project_permissions.denied_plugins:
        computer_plugin = None
    if computer_plugin is not None:
        plugins.append(computer_plugin)

    return plugins


def load_plugin(path: Path, enabled_plugins: dict[str, bool]) -> LoadedPlugin | None:
    """加载单个插件目录。"""
    manifest_path = _find_manifest(path)
    if manifest_path is None:
        return None
    try:
        manifest = PluginManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        logger.debug("Failed to load plugin manifest from %s: %s", manifest_path, exc)
        return None
    enabled = enabled_plugins.get(manifest.name, manifest.enabled_by_default)

    # 从多个位置发现技能（支持 SKILL.md 目录格式和命名空间）
    skills = _load_plugin_skills(path / manifest.skills_dir, manifest.name, path)

    # 从 plugin commands/ 目录发现命令
    commands_dir = path / "commands"
    if commands_dir.exists():
        skills.extend(_load_plugin_skills(commands_dir, manifest.name, path))

    # 从 plugin agents/ 目录发现智能体
    # 安全限制：插件 agent 禁止解析 hooks
    agents_dir = path / "agents"
    if agents_dir.exists():
        skills.extend(_load_plugin_skills(agents_dir, manifest.name, path, parse_hooks=False))

    # 从 hooks/ 目录或根 hooks.json 发现钩子（新格式：dict[str, list[HookMatcherDefinition]]）
    hooks = _load_plugin_hooks(path / manifest.hooks_file, path)
    hooks_dir_file = path / "hooks" / "hooks.json"
    if not hooks and hooks_dir_file.exists():
        hooks = _load_plugin_hooks_structured(hooks_dir_file, path)

    mcp = _load_plugin_mcp(path / manifest.mcp_file)
    mcp_json = path / ".mcp.json"
    if not mcp and mcp_json.exists():
        mcp = _load_plugin_mcp(mcp_json)

    return LoadedPlugin(
        manifest=manifest,
        path=path,
        enabled=enabled,
        skills=skills,
        hooks=hooks,
        mcp_servers=mcp,
        commands=[s for s in skills if s.source == "plugin"],
    )


def _load_plugin_skills(
    path: Path,
    plugin_name: str,
    plugin_root: Path,
    *,
    parse_hooks: bool = True,
) -> list[SkillDefinition]:
    """从目录加载技能，支持 SKILL.md 目录格式和命名空间。


    技能名称格式：{plugin_name}:{skill_name}
    """
    if not path.exists():
        return []

    skills: list[SkillDefinition] = []
    for item in sorted(path.iterdir()):
        if item.is_dir():
            # SKILL.md 目录格式
            skill_md = item / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                from illusion.skills.loader import parse_skill_markdown
                skill = parse_skill_markdown(item.name, content, skill_root=str(item))
                namespaced_name = f"{plugin_name}:{skill.name}"
                skills.append(SkillDefinition(
                    name=namespaced_name,
                    description=skill.description,
                    content=skill.content,
                    source="plugin",
                    path=str(skill_md),
                    allowed_tools=skill.allowed_tools,
                    model=skill.model,
                    hooks=skill.hooks if parse_hooks else None,
                    context=skill.context,
                    agent=skill.agent,
                    disable_model_invocation=skill.disable_model_invocation,
                    effort=skill.effort,
                    skill_root=str(item),
                ))
            else:
                # 遍历目录中的文件
                for f in sorted(item.iterdir()):
                    if f.suffix == ".md":
                        skills.append(_load_md_skill(f, plugin_name, str(plugin_root), parse_hooks=parse_hooks))
                    elif f.suffix in (".yaml", ".yml"):
                        from illusion.skills.loader import _load_yaml_skill
                        sk = _load_yaml_skill(f, source="plugin")
                        if sk:
                            skills.append(SkillDefinition(
                                name=f"{plugin_name}:{sk.name}",
                                description=sk.description,
                                content=sk.content,
                                source="plugin",
                                path=sk.path,
                                skill_root=str(plugin_root),
                            ))
        elif item.suffix == ".md":
            skills.append(_load_md_skill(item, plugin_name, str(plugin_root)))
        elif item.suffix in (".yaml", ".yml"):
            from illusion.skills.loader import _load_yaml_skill
            sk = _load_yaml_skill(item, source="plugin")
            if sk:
                skills.append(SkillDefinition(
                    name=f"{plugin_name}:{sk.name}",
                    description=sk.description,
                    content=sk.content,
                    source="plugin",
                    path=sk.path,
                    skill_root=str(plugin_root),
                ))
    return skills


def _load_md_skill(
    path: Path,
    plugin_name: str,
    plugin_root: str,
    *,
    parse_hooks: bool = True,
) -> SkillDefinition:
    """加载单个 .md 技能文件。"""
    from illusion.skills.loader import parse_skill_markdown
    content = path.read_text(encoding="utf-8")
    skill = parse_skill_markdown(path.stem, content)
    return SkillDefinition(
        name=f"{plugin_name}:{skill.name}",
        description=skill.description,
        content=skill.content,
        source="plugin",
        path=str(path),
        allowed_tools=skill.allowed_tools,
        model=skill.model,
        hooks=skill.hooks if parse_hooks else None,
        context=skill.context,
        agent=skill.agent,
        disable_model_invocation=skill.disable_model_invocation,
        effort=skill.effort,
        skill_root=plugin_root,
    )


def _load_plugin_hooks(path: Path, plugin_root: Path) -> dict[str, list[Any]]:
    """从平面 hooks.json 文件加载钩子。

    返回 dict[str, list[Any]]，值是原始列表（在 load_hook_registry 中统一解析）。
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # 应用变量替换
    raw = _substitute_plugin_vars_in_hooks(raw, plugin_root)
    result: dict[str, list[Any]] = raw
    return result


def _load_plugin_hooks_structured(path: Path, plugin_root: Path) -> dict[str, list[Any]]:
    """从结构化 hooks/hooks.json 格式加载钩子。

    格式：{ "hooks": { "EventName": [{ "matcher": "...", "hooks": [...] }] } }
    返回 dict[str, list[Any]]，值是原始列表。
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    hooks_data = raw.get("hooks", raw)
    if not isinstance(hooks_data, dict):
        return {}
    # 应用变量替换
    hooks_data = _substitute_plugin_vars_in_hooks(hooks_data, plugin_root)
    result: dict[str, list[Any]] = hooks_data
    return result


def _substitute_plugin_vars_in_hooks(data: Any, plugin_root: Path) -> Any:
    """递归替换钩子配置中的插件变量。路径统一使用正斜杠。"""
    if isinstance(data, str):
        root_str = str(plugin_root).replace("\\", "/")
        data = data.replace("${CLAUDE_PLUGIN_ROOT}", root_str)
        return data
    elif isinstance(data, dict):
        return {k: _substitute_plugin_vars_in_hooks(v, plugin_root) for k, v in data.items()}
    elif isinstance(data, list):
        return [_substitute_plugin_vars_in_hooks(item, plugin_root) for item in data]
    return data


def _load_plugin_mcp(path: Path) -> dict[str, McpServerConfig]:
    """从 JSON 文件加载 MCP 服务器配置。"""
    if not path.exists():
        return {}
    from illusion.mcp.types import McpJsonConfig

    raw = json.loads(path.read_text(encoding="utf-8"))
    parsed = McpJsonConfig.model_validate(raw)
    return parsed.mcpServers
