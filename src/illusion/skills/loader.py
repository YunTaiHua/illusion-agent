"""
Skill 加载模块 — 从内置和用户目录加载 Skills
=========================================

支持完整 frontmatter 解析和 SKILL.md 目录格式。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from illusion.config.paths import get_config_dir, get_project_config_dir
from illusion.config.settings import load_settings
from illusion.skills.bundled import get_bundled_skills
from illusion.skills.registry import SkillRegistry
from illusion.skills.types import SkillDefinition

logger = logging.getLogger(__name__)


def get_user_skills_dir() -> Path:
    """返回用户 skills 目录。"""
    path = get_config_dir() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_project_skills_dir(cwd: str | Path) -> Path:
    """返回项目级 skills 目录（.illusion/skills/）。"""
    path = get_project_config_dir(cwd) / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_project_rules_dir(cwd: str | Path) -> Path:
    """返回项目级 rules 目录（.illusion/rules/）。"""
    path = get_project_config_dir(cwd) / "rules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_skill_registry(cwd: str | Path | None = None) -> SkillRegistry:
    """加载内置和用户定义的 skills。"""
    registry = SkillRegistry()

    # 加载项目级权限配置
    from illusion.permissions.loader import load_project_permissions
    project_permissions = load_project_permissions(cwd) if cwd else None

    # 检查是否禁用所有 skills
    if project_permissions and "*" in project_permissions.denied_skills:
        return registry

    # 注册内置 skills（目录形态 skill 按功能门控注入：browser_use ← settings.browser.enabled）
    enabled_features: frozenset[str] = frozenset()
    try:
        if load_settings().browser.enabled:
            enabled_features = frozenset({"browser_use"})
    except Exception:  # noqa: BLE001, S110 - settings 读取失败不阻塞 skills 加载
        pass
    for skill in get_bundled_skills(enabled_features):
        if project_permissions and skill.name in project_permissions.denied_skills:
            continue
        registry.register(skill)

    # 注册用户 skills
    for skill in load_user_skills():
        if project_permissions and skill.name in project_permissions.denied_skills:
            continue
        registry.register(skill)

    # 如果提供了工作目录，加载项目级 skills 和插件 skills
    if cwd is not None:
        for skill in load_project_skills(cwd):
            if project_permissions and skill.name in project_permissions.denied_skills:
                continue
            registry.register(skill)

        from illusion.plugins.loader import load_plugins

        settings = load_settings()
        for plugin in load_plugins(settings, cwd):
            if not plugin.enabled:
                continue
            for skill in plugin.skills:
                if project_permissions and skill.name in project_permissions.denied_skills:
                    continue
                registry.register(skill)

    return registry


def load_user_skills() -> list[SkillDefinition]:
    """从用户配置目录加载 skills。

    支持两种格式：
    1. SKILL.md 目录格式：~/.illusion/skills/<skill_name>/SKILL.md
    2. 直接文件格式：~/.illusion/skills/<skill_name>.md
    """
    skills: list[SkillDefinition] = []
    skills_dir = get_user_skills_dir()
    if not skills_dir.exists():
        return skills

    for sub in sorted(skills_dir.iterdir()):
        if sub.is_dir():
            # SKILL.md 目录格式
            skill_md = sub / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                skill = parse_skill_markdown(sub.name, content, skill_root=str(sub))
                skills.append(SkillDefinition(
                    name=skill.name,
                    description=skill.description,
                    content=skill.content,
                    source="user",
                    path=str(skill_md),
                    allowed_tools=skill.allowed_tools,
                    model=skill.model,
                    hooks=skill.hooks,
                    context=skill.context,
                    agent=skill.agent,
                    disable_model_invocation=skill.disable_model_invocation,
                    effort=skill.effort,
                    skill_root=str(sub),
                ))
            else:
                # 遍历目录中的 .md/.yaml 文件
                for path in sorted(sub.iterdir()):
                    if path.suffix in (".yaml", ".yml"):
                        sk = _load_yaml_skill(path, source="user")
                        if sk:
                            skills.append(sk)
                    elif path.suffix == ".md":
                        content = path.read_text(encoding="utf-8")
                        sk = parse_skill_markdown(path.stem, content)
                        skills.append(SkillDefinition(
                            name=sk.name,
                            description=sk.description,
                            content=sk.content,
                            source="user",
                            path=str(path),
                            allowed_tools=sk.allowed_tools,
                            model=sk.model,
                            hooks=sk.hooks,
                            context=sk.context,
                            agent=sk.agent,
                            disable_model_invocation=sk.disable_model_invocation,
                            effort=sk.effort,
                        ))
        elif sub.suffix in (".yaml", ".yml"):
            yaml_skill = _load_yaml_skill(sub, source="user")
            if yaml_skill is not None:
                skills.append(yaml_skill)
        elif sub.suffix == ".md":
            content = sub.read_text(encoding="utf-8")
            skill = parse_skill_markdown(sub.stem, content)
            skills.append(SkillDefinition(
                name=skill.name,
                description=skill.description,
                content=skill.content,
                source="user",
                path=str(sub),
                allowed_tools=skill.allowed_tools,
                model=skill.model,
                hooks=skill.hooks,
                context=skill.context,
                agent=skill.agent,
                disable_model_invocation=skill.disable_model_invocation,
                effort=skill.effort,
            ))
    return skills


def load_project_skills(cwd: str | Path) -> list[SkillDefinition]:
    """从项目目录加载 skills。

    支持两种格式：
    1. SKILL.md 目录格式：<project>/.illusion/skills/<skill_name>/SKILL.md
    2. 直接文件格式：<project>/.illusion/skills/<skill_name>.md
    """
    skills: list[SkillDefinition] = []
    skills_dir = get_project_skills_dir(cwd)
    if not skills_dir.exists():
        return skills
    for sub in sorted(skills_dir.iterdir()):
        if sub.is_dir():
            # SKILL.md 目录格式
            skill_md = sub / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                skill = parse_skill_markdown(sub.name, content, skill_root=str(sub))
                skills.append(SkillDefinition(
                    name=skill.name,
                    description=skill.description,
                    content=skill.content,
                    source="project",
                    path=str(skill_md),
                    allowed_tools=skill.allowed_tools,
                    model=skill.model,
                    hooks=skill.hooks,
                    context=skill.context,
                    agent=skill.agent,
                    disable_model_invocation=skill.disable_model_invocation,
                    effort=skill.effort,
                    skill_root=str(sub),
                ))
            else:
                # 遍历目录中的 .md/.yaml 文件
                for path in sorted(sub.iterdir()):
                    if path.suffix in (".yaml", ".yml"):
                        sk = _load_yaml_skill(path, source="project")
                        if sk:
                            skills.append(sk)
                    elif path.suffix == ".md":
                        content = path.read_text(encoding="utf-8")
                        sk = parse_skill_markdown(path.stem, content)
                        skills.append(SkillDefinition(
                            name=sk.name,
                            description=sk.description,
                            content=sk.content,
                            source="project",
                            path=str(path),
                            allowed_tools=sk.allowed_tools,
                            model=sk.model,
                            hooks=sk.hooks,
                            context=sk.context,
                            agent=sk.agent,
                            disable_model_invocation=sk.disable_model_invocation,
                            effort=sk.effort,
                        ))
        elif sub.suffix in (".yaml", ".yml"):
            # YAML 文件
            yaml_skill = _load_yaml_skill(sub, source="project")
            if yaml_skill is not None:
                skills.append(yaml_skill)
        elif sub.suffix == ".md":
            # 直接 .md 文件
            content = sub.read_text(encoding="utf-8")
            skill = parse_skill_markdown(sub.stem, content)
            skills.append(SkillDefinition(
                name=skill.name,
                description=skill.description,
                content=skill.content,
                source="project",
                path=str(sub),
                allowed_tools=skill.allowed_tools,
                model=skill.model,
                hooks=skill.hooks,
                context=skill.context,
                agent=skill.agent,
                disable_model_invocation=skill.disable_model_invocation,
                effort=skill.effort,
            ))
    return skills


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
    )
