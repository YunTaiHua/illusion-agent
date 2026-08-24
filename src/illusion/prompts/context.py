"""
高级系统提示词组装模块
======================

本模块实现运行时系统提示词的组装功能。

主要功能：
    - 构建技能章节
    - 组装包含项目指令和记忆的完整运行时提示词
    - 加载 Claude.md 指令文件

使用示例：
    >>> from illusion.prompts.context import build_runtime_system_prompt
    >>> from illusion.config.settings import Settings
    >>> prompt = build_runtime_system_prompt(settings, cwd="/path/to/project")
"""

from __future__ import annotations

from pathlib import Path

from illusion.config.settings import Settings
from illusion.memory import find_relevant_memories, load_memory_prompt
from illusion.prompts.claudemd import load_claude_md_prompt
from illusion.prompts.system_prompt import build_system_prompt
from illusion.skills.loader import get_project_rules_dir, load_skill_registry


def _build_rules_section(cwd: str | Path) -> str | None:
    """构建项目级 rules 章节

    从 .illusion/rules/ 目录加载所有 .md 文件作为项目指令。

    Args:
        cwd: 工作目录

    Returns:
        str | None: rules 章节字符串，如果没有 rules 则返回 None
    """
    # 加载项目级权限配置
    from illusion.permissions.loader import (
        filter_rules_by_permissions,
        is_rules_disabled,
        load_project_permissions,
    )

    project_permissions = load_project_permissions(cwd)

    # 检查是否禁用所有 rules
    if is_rules_disabled(project_permissions):
        return None

    rules_dir = get_project_rules_dir(cwd)
    all_rule_files = sorted(rules_dir.glob("*.md"))
    if not all_rule_files:
        return None

    # 过滤掉被禁用的 rules
    rule_files = filter_rules_by_permissions(all_rule_files, project_permissions)

    contents = []
    for path in rule_files:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            contents.append(content)

    if not contents:
        return None

    return "# Project Rules\n\n" + "\n\n---\n\n".join(contents)


def _build_skills_section(cwd: str | Path) -> str | None:
    """构建技能章节

    生成列出可用技能的系统提示词章节。

    Args:
        cwd: 工作目录

    Returns:
        str | None: 技能章节字符串，如果没有技能则返回 None
    """
    registry = load_skill_registry(cwd)
    skills = registry.list_skills()
    if not skills:
        return None
    lines = [
        "# Available Skills",
        "",
        (
            "The following skills are available via the `skill` tool. "
            'When a user\'s request matches a skill, invoke it with `skill(name="<skill_name>")` '
            "to load detailed instructions before proceeding."
        ),
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
    channel_hint: str | None = None,
) -> str:
    """构建运行时系统提示词

    组装完整的运行时提示词，包含项目指令和记忆。

    Args:
        settings: 设置对象
        cwd: 工作目录
        latest_user_prompt: 最新的用户提示词（用于相关记忆搜索）

    Returns:
        str: 完整的运行时系统提示词
    """
    sections = [build_system_prompt(cwd=str(cwd))]

    # 计划模式
    from illusion.permissions.modes import PermissionMode

    if settings.permission.mode == PermissionMode.PLAN:
        from illusion.config.plan_file import DEFAULT_SESSION_ID, get_plan_file_path

        plan_path = str(get_plan_file_path(DEFAULT_SESSION_ID))
        sections.append(
            "# Plan Mode\n"
            "Plan mode is active. The user indicated that they do not want you to execute yet -- "
            "you MUST NOT make any edits (with the exception of registered plan files), "
            "run any non-readonly tools (including changing configs or making commits), or otherwise "
            "make any changes to the system.\n\n"
            "## Plan File Info:\n"
            f"Your plan file is: {plan_path}\n"
            "This file does NOT exist yet — you must create it using the Write tool.\n\n"
            "## Iterative Planning Workflow\n"
            "You are pair-planning with the user. Explore the code to build context, ask the user "
            "questions when you hit decisions you can't make alone, and write your findings into plan "
            "files as you go.\n\n"
            "### The Loop\n"
            "Repeat this cycle until the plan is complete:\n"
            "1. **Explore** — Use Glob, Grep, Read to read code.\n"
            "2. **Update plan files** — After each discovery, immediately capture what you learned.\n"
            "3. **Ask the user** — When you hit an ambiguity, use AskUserQuestion. Then go back to step 1.\n\n"
            "### Plan File Structure\n"
            "- Begin with a **Context** section: explain why this change is being made\n"
            "- Include only your recommended approach, not all alternatives\n"
            "- Include the paths of critical files to be modified\n"
            "- Include a verification section describing how to test the changes\n\n"
            "### Ending Your Turn\n"
            "Your turn should only end by either:\n"
            "- Using AskUserQuestion to gather more information\n"
            "- Calling ExitPlanMode when the plan is ready for approval\n\n"
            "**Important:** Use ExitPlanMode to request plan approval. Do NOT ask about plan approval "
            "via text or AskUserQuestion."
        )

    # 推理设置
    # 对于 Anthropic 格式，effort 通过系统提示词传递
    # 对于 OpenAI 格式，effort 通过 API 参数传递，不在系统提示词中包含
    api_format = settings.api_format
    if api_format == "anthropic":
        sections.append(
            "# Reasoning Settings\n"
            f"- Effort: {settings.effort}\n"
            "Adjust depth and iteration count to match this setting while still completing the task."
        )

    # Goal 章节（settings.goal.enabled 时注入）
    if settings.goal.enabled:
        from illusion.goal.prompts import goal_guidance

        sections.append(f"# Goal\n\n{goal_guidance(settings.goal.blocked_after_consecutive_rounds)}")

    # 技能章节
    skills_section = _build_skills_section(cwd)
    if skills_section:
        sections.append(skills_section)

    # Claude.md 指令
    claude_md = load_claude_md_prompt(cwd)
    if claude_md:
        sections.append(claude_md)

    # 项目级 rules
    rules_section = _build_rules_section(cwd)
    if rules_section:
        sections.append(rules_section)

    # 记忆功能
    if settings.memory.enabled:
        # 检查项目级权限配置
        from illusion.permissions.loader import load_project_permissions

        project_perms = load_project_permissions(cwd)
        if not project_perms.denied_memory:
            memory_section = load_memory_prompt(
                cwd,
                max_entrypoint_lines=settings.memory.max_entrypoint_lines,
                max_entrypoint_bytes=settings.memory.max_entrypoint_bytes,
            )
            if memory_section:
                sections.append(memory_section)

            # 相关记忆
            if latest_user_prompt:
                relevant = find_relevant_memories(
                    latest_user_prompt,
                    cwd,
                    max_results=settings.memory.max_files,
                )
                if relevant:
                    lines = ["# Relevant Memories"]
                    for header in relevant:
                        content = header.path.read_text(encoding="utf-8", errors="replace").strip()
                        lines.extend(
                            [
                                "",
                                f"## {header.path.name}",
                                "```md",
                                content[:8000],
                                "```",
                            ]
                        )
                    sections.append("\n".join(lines))

    # 渠道平台感知提示词
    if channel_hint:
        sections.append(f"# Channel Context\n\n{channel_hint}")

    return "\n\n".join(section for section in sections if section.strip())
