"""
Skill 加载模块 — 从内置和用户目录加载 Skills
=========================================

支持完整 frontmatter 解析和 SKILL.md 目录格式。

健壮性约定：技能目录中的单个条目损坏（符号链接失效、Windows 下文件型
符号链接指向目录导致 stat 拒绝访问等）只跳过该条目，不中断整体加载。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from illusion.config.paths import get_config_dir, get_project_config_dir
from illusion.config.settings import load_settings
from illusion.skills.bundled import get_bundled_skills
from illusion.skills.registry import SkillRegistry
from illusion.skills.types import SkillDefinition

logger = logging.getLogger(__name__)

# 递归查找嵌套 SKILL.md 技能包的最大深度（整仓拷贝场景典型为 2~3 层）
_SKILL_PACKAGE_MAX_DEPTH = 4
_COMMAND_MAX_DEPTH = 12
_COMMAND_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")


def get_user_skills_dir() -> Path:
    """返回用户 skills 目录，不创建目录。

    路径始终位于 ``get_config_dir()`` 返回的用户配置根下。读取/扫描时
    目录可以不存在，写入技能前由写入操作显式创建它。
    """
    return get_config_dir() / "skills"


def get_project_skills_dir(cwd: str | Path) -> Path:
    """返回项目级 skills 目录（``.illusion/skills/``），不创建目录。

    读取/扫描时项目配置目录可以不存在，写入技能前由写入操作显式创建它。
    """
    return get_project_config_dir(cwd) / "skills"


def get_user_commands_dir() -> Path:
    """返回用户级命令目录，不创建目录。

    路径位于用户配置根下，明确写入命令前由写入操作显式创建它。
    """
    return get_config_dir() / "commands"


def get_project_commands_dir(cwd: str | Path) -> Path:
    """返回项目级命令目录（``.illusion/commands/``），不创建目录。"""
    return get_project_config_dir(cwd) / "commands"


# ---------------------------------------------------------------------------
# 目录遍历安全辅助：单个条目损坏不中断整体加载
# ---------------------------------------------------------------------------


def _safe_iterdir(path: Path) -> list[Path]:
    """列出目录内容（已排序）；目录不可读时返回空列表。"""
    try:
        return sorted(path.iterdir())
    except OSError as exc:
        logger.debug("遍历技能目录失败，跳过: %s: %s", path, exc)
        return []


def _resolve_entry(entry: Path) -> Path | None:
    """解析目录项为可安全访问的真实路径。

    符号链接一律解析到真实目标：Windows 下"文件型符号链接指向目录"
    （git 在无符号链接权限环境检出的仓库常见）直接 stat 会抛
    WinError 5 拒绝访问，realpath 可绕开该限制。
    目标不存在或访问失败返回 None，调用方跳过该条目。
    """
    try:
        if entry.is_symlink():
            resolved = Path(os.path.realpath(entry))
            return resolved if resolved.exists() else None
        return entry
    except OSError as exc:
        logger.debug("解析技能条目失败，跳过: %s: %s", entry, exc)
        return None


def _is_dir(path: Path) -> bool:
    """安全判断目录；stat 失败（权限问题/损坏链接）返回 False。"""
    try:
        return path.is_dir()
    except OSError:
        return False


def _read_text_safe(path: Path) -> str | None:
    """安全读取文本文件；失败时记录日志并返回 None。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("读取技能文件失败，跳过: %s: %s", path, exc)
        return None


def _definition_from_parsed(
    skill: SkillDefinition,
    source: str,
    path: Path,
    skill_root: str | None,
    *,
    kind: str | None = None,
    plugin_root: str | None = None,
    name: str | None = None,
) -> SkillDefinition:
    """将解析结果补充为带实际文件位置的定义。"""
    return SkillDefinition(
        name=name or skill.name,
        description=skill.description,
        content=skill.content,
        source=source,
        path=str(path),
        allowed_tools=skill.allowed_tools,
        model=skill.model,
        hooks=skill.hooks,
        context=skill.context,
        agent=skill.agent,
        disable_model_invocation=skill.disable_model_invocation,
        effort=skill.effort,
        skill_root=skill_root if skill_root is not None else skill.skill_root,
        plugin_root=plugin_root if plugin_root is not None else skill.plugin_root,
        kind=kind or skill.kind,
    )


def get_project_rules_dir(cwd: str | Path) -> Path:
    """返回项目级 rules 目录（``.illusion/rules/``），不创建目录。"""
    return get_project_config_dir(cwd) / "rules"


def load_skill_registry(cwd: str | Path | None = None) -> SkillRegistry:
    """加载内置、用户、项目和插件资源。"""
    registry = SkillRegistry()

    from illusion.permissions.loader import load_project_permissions

    project_permissions = load_project_permissions(cwd) if cwd else None
    denied_names = set(project_permissions.denied_skills) if project_permissions else set()

    # ``denied_skills`` 同时控制资源注册，避免被禁用的命令仍能通过斜杠入口激活。
    if "*" in denied_names:
        return registry

    def allowed(definition: SkillDefinition) -> bool:
        return not _is_denied_name(definition.name, denied_names)

    for skill in get_bundled_skills():
        if allowed(skill):
            registry.register(skill)

    for skill in load_user_skills():
        if allowed(skill):
            registry.register(skill)

    # 用户命令目录优先于项目命令目录，注册表对命令采用首次命中策略。
    for command in load_user_commands():
        if allowed(command):
            registry.register_command(command)

    if cwd is not None:
        for skill in load_project_skills(cwd):
            if allowed(skill):
                registry.register(skill)
        for command in load_project_commands(cwd):
            if allowed(command):
                registry.register_command(command)

        from illusion.plugins.loader import load_plugins

        settings = load_settings()
        for plugin in load_plugins(settings, cwd):
            if not plugin.enabled:
                continue
            for skill in plugin.skills:
                if allowed(skill):
                    registry.register(skill)
            for command in plugin.commands:
                if allowed(command):
                    registry.register_command(command)

    return registry


def _is_denied_name(name: str, denied_names: set[str]) -> bool:
    """按配置中的名称判断资源是否被禁用。"""
    wanted = name.casefold()
    return any(pattern == "*" or pattern.casefold() == wanted for pattern in denied_names)


def load_user_skills() -> list[SkillDefinition]:
    """从用户配置目录加载 skills（~/.illusion/skills/）。

    支持两种格式：
    1. SKILL.md 目录格式：~/.illusion/skills/<skill_name>/SKILL.md
    2. 直接文件格式：~/.illusion/skills/<skill_name>.md
    """
    skills_dir = get_user_skills_dir()
    if not skills_dir.exists():
        return []
    return _scan_skills_dir(skills_dir, "user")


def load_project_skills(cwd: str | Path) -> list[SkillDefinition]:
    """从项目目录加载 skills（<project>/.illusion/skills/）。

    支持两种格式：
    1. SKILL.md 目录格式：<project>/.illusion/skills/<skill_name>/SKILL.md
    2. 直接文件格式：<project>/.illusion/skills/<skill_name>.md
    """
    skills_dir = get_project_skills_dir(cwd)
    if not skills_dir.exists():
        return []
    return _scan_skills_dir(skills_dir, "project")


def load_user_commands() -> list[SkillDefinition]:
    """从配置根目录的 commands/ 递归加载用户命令。"""
    return _scan_commands_dir(get_user_commands_dir(), "user")


def load_project_commands(cwd: str | Path) -> list[SkillDefinition]:
    """从项目 .illusion/commands/ 递归加载项目命令。"""
    return _scan_commands_dir(get_project_commands_dir(cwd), "project")


def _scan_commands_dir(commands_dir: Path, source: str) -> list[SkillDefinition]:
    """递归扫描 markdown 命令，使用相对路径生成稳定的冒号命名。"""
    if not commands_dir.exists():
        return []

    commands: list[SkillDefinition] = []
    seen_paths: set[str] = set()

    def walk(directory: Path, relative_parts: tuple[str, ...], depth: int) -> None:
        if depth > _COMMAND_MAX_DEPTH:
            return
        for entry in _safe_iterdir(directory):
            resolved = _resolve_entry(entry)
            if resolved is None:
                continue
            if _is_dir(resolved):
                if entry.name.startswith("."):
                    continue
                walk(resolved, (*relative_parts, entry.name), depth + 1)
                continue
            if resolved.suffix.casefold() != ".md":
                continue
            key = str(resolved)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            command_name = ":".join((*relative_parts, entry.stem)).casefold()
            if not _COMMAND_NAME_PATTERN.fullmatch(command_name):
                logger.debug("跳过无效命令名: %s", command_name)
                continue
            command = _load_command_file(resolved, command_name, source)
            if command is not None:
                commands.append(command)

    walk(commands_dir, (), 0)
    return commands


def _command_body(content: str) -> str:
    """返回命令 markdown 的正文部分。"""
    normalized = content.removeprefix("\ufeff")
    if not normalized.startswith("---"):
        return content
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        return content
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :])
    return content


def _load_command_file(path: Path, command_name: str, source: str) -> SkillDefinition | None:
    """加载一个 markdown 命令，并保留其文件所在目录作为资源目录。"""
    content = _read_text_safe(path)
    if content is None:
        return None
    if not _command_body(content).strip():
        logger.debug("跳过空命令文件: %s", path)
        return None
    parsed = parse_skill_markdown(command_name, content)
    return _definition_from_parsed(
        parsed,
        source,
        path,
        str(path.parent),
        kind="command",
        name=command_name,
    )


def _scan_skills_dir(skills_dir: Path, source: str) -> list[SkillDefinition]:
    """扫描技能目录，加载全部技能定义。

    目录支持三种布局：
    1. ``<name>/SKILL.md`` — 标准技能包
    2. ``<name>.md`` / ``<name>.yaml`` — 单文件技能
    3. ``<name>/``（无 SKILL.md）— 整仓拷贝场景：递归查找嵌套技能包
       （如 ``<repo>/<skill>/SKILL.md``、``<repo>/skills/<skill>/SKILL.md``）；
       找到嵌套包时跳过散落的 .md 文件（多为 README 等文档，加载出来
       会成为重复/垃圾技能），未找到时回退为目录内散落的单文件技能
       （兼容 ``<dir>/<cmd>.md`` 旧格式）。

    符号链接与真实目录同时声明同一技能时（如仓库内 skills/ 软链指向
    根级技能目录），按真实路径去重，只加载一次；拷贝仓库时软链常被
    解引用为真实目录副本（同名不同路径），此时按技能名去重、先加载
    者生效，避免注册表出现重复技能。
    """
    skills: list[SkillDefinition] = []
    seen_realpaths: set[str] = set()
    seen_names: set[str] = set()

    def _append(skill: SkillDefinition | None) -> None:
        if skill is not None and skill.name not in seen_names:
            seen_names.add(skill.name)
            skills.append(skill)

    for entry in _safe_iterdir(skills_dir):
        resolved = _resolve_entry(entry)
        if resolved is None:
            continue
        key = str(resolved)
        if key in seen_realpaths:
            continue
        seen_realpaths.add(key)

        if _is_dir(resolved):
            skill = _load_skill_package(resolved, source)
            if skill is not None:
                _append(skill)
                continue
            nested = _find_skill_packages(resolved, seen_realpaths)
            if nested:
                for package_dir in nested:
                    _append(_load_skill_package(package_dir, source))
            else:
                # 回退：目录内散落的单文件技能（.md/.yaml）
                for f in _safe_iterdir(resolved):
                    if f.suffix in (".yaml", ".yml"):
                        yaml_skill = _load_yaml_skill(f, source=source)
                        if yaml_skill is not None:
                            _append(yaml_skill)
                    elif f.suffix == ".md":
                        _append(_load_loose_md_file(f, source))
        elif resolved.suffix in (".yaml", ".yml"):
            _append(_load_yaml_skill(resolved, source=source))
        elif resolved.suffix == ".md":
            _append(_load_loose_md_file(resolved, source))
    return skills


def _load_skill_package(package_dir: Path, source: str) -> SkillDefinition | None:
    """加载 SKILL.md 目录格式的技能包；无 SKILL.md 或读取失败返回 None。"""
    skill_md = package_dir / "SKILL.md"
    content = _read_text_safe(skill_md)
    if content is None:
        return None
    skill = parse_skill_markdown(package_dir.name, content, skill_root=str(package_dir))
    return _definition_from_parsed(skill, source, skill_md, str(package_dir))


def _load_loose_md_file(path: Path, source: str) -> SkillDefinition | None:
    """加载散落的单个 .md 技能文件；读取失败时跳过。"""
    content = _read_text_safe(path)
    if content is None:
        return None
    skill = parse_skill_markdown(path.stem, content)
    return _definition_from_parsed(skill, source, path, str(path.parent))


def _find_skill_packages(root: Path, seen: set[str], depth: int = 1) -> list[Path]:
    """在 root 的后代目录中递归查找 SKILL.md 技能包（有界深度）。

    用于整仓拷贝场景：技能仓库内 SKILL.md 可能位于任意子目录深度。
    跳过隐藏目录（.git 等）与已见过的真实路径（符号链接双份声明）。

    Args:
        root: 起始目录
        seen: 已处理的真实路径集合（跨调用共享，加载与查找统一去重）
        depth: 当前递归深度（1 起）
    """
    packages: list[Path] = []
    if depth > _SKILL_PACKAGE_MAX_DEPTH:
        return packages
    for entry in _safe_iterdir(root):
        if entry.name.startswith("."):
            continue
        resolved = _resolve_entry(entry)
        if resolved is None or not _is_dir(resolved):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if (resolved / "SKILL.md").is_file():
            packages.append(resolved)
        else:
            packages.extend(_find_skill_packages(resolved, seen, depth + 1))
    return packages


def parse_skill_markdown(
    default_name: str,
    content: str,
    skill_root: str | None = None,
) -> SkillDefinition:
    """解析 SKILL.md，提取全部 frontmatter 字段。

    frontmatter 解析。
    kebab-case 自动转换为 snake_case。
    """
    name = default_name
    description = ""
    extra_fields: dict[str, Any] = {}

    lines = content.splitlines()

    if lines and lines[0].strip() == "---":
        end_idx = -1
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end_idx = i
                break
        if end_idx > 0:
            fm_text = "\n".join(lines[1:end_idx])
            try:
                data = yaml.safe_load(fm_text)
                if isinstance(data, dict):
                    name = str(data.get("name", default_name)).strip() or default_name
                    description = str(data.get("description", "")).strip()
                    # 只提取 SkillDefinition 已知的 frontmatter 字段
                    _known_fields = {
                        "allowed_tools", "model", "hooks", "context", "agent",
                        "disable_model_invocation", "effort", "skill_root",
                    }
                    for key, value in data.items():
                        snake_key = key.replace("-", "_")
                        if key in ("name", "description"):
                            continue
                        if snake_key in _known_fields:
                            extra_fields[snake_key] = value
                    # allowed-tools 可以是逗号分隔的字符串或列表
                    if "allowed_tools" in extra_fields:
                        at = extra_fields["allowed_tools"]
                        if isinstance(at, str):
                            extra_fields["allowed_tools"] = [t.strip() for t in at.split(",")]
            except yaml.YAMLError as exc:
                logger.debug("解析 skill frontmatter 失败: %s", exc)

    # 回退：从标题和第一段提取
    if not description:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# "):
                if not name or name == default_name:
                    name = stripped[2:].strip() or default_name
                continue
            if stripped and not stripped.startswith("---") and not stripped.startswith("#"):
                description = stripped[:200]
                break

    if not description:
        description = f"Skill: {name}"

    return SkillDefinition(
        name=name,
        description=description,
        content=content,
        source="",
        path=None,
        skill_root=skill_root,
        **extra_fields,
    )


# 向后兼容别名
_parse_skill_markdown = parse_skill_markdown


def _load_yaml_skill(path: Path, source: str) -> SkillDefinition | None:
    """从 YAML 文件加载 skill 定义。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name", path.stem)
    description = data.get("description", "")
    content = data.get("content", "")
    if not content:
        content = yaml.dump(data, allow_unicode=True, default_flow_style=False)
    return SkillDefinition(
        name=name,
        description=description or f"Skill: {name}",
        content=content,
        source=source,
        path=str(path),
        skill_root=str(path.parent),
    )
