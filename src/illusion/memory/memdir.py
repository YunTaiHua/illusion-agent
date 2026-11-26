"""
记忆提示词模块
=============

本模块构建记忆相关的系统提示词段落

提示词包含：
    - 记忆系统总览与目录信息
    - 四种记忆类型定义（user / feedback / project / reference）
    - 禁止保存的内容（What NOT to save）
    - 保存步骤（两步：主题文件 + MEMORY.md 索引）
    - 访问时机（When to access + 记忆漂移警告）
    - 回忆验证（Before recommending from memory）
    - 与其他持久化机制的区别
    - MEMORY.md 入口内容（行/字节截断 + 警告）

函数说明：
    - load_memory_prompt: 加载完整的记忆提示词段落
    - truncate_entrypoint_content: 截断 MEMORY.md 内容
"""

from __future__ import annotations

from pathlib import Path

from illusion.memory.paths import get_memory_dir_for_cwd_no_create

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
AUTO_MEM_DISPLAY_NAME = "auto memory"

# 四种记忆类型
MEMORY_TYPES = ["user", "feedback", "project", "reference"]

# 目录已存在提示：防止模型浪费轮次在 mkdir/ls
DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly with the Write tool "
    "(do not run mkdir or check for its existence)."
)

# 记忆漂移警告
MEMORY_DRIFT_CAVEAT = (
    "- Memory records can become stale over time. Use memory as context for what was true "
    "at a given point in time. Before answering the user or building assumptions based "
    "solely on information in memory records, verify that the memory is still correct "
    "and up-to-date by reading the current state of the files or resources. If a recalled "
    "memory conflicts with current information, trust what you observe now — and update "
    "or remove the stale memory rather than acting on it."
)

# 记忆类型定义段落
TYPES_SECTION: list[str] = [
    "## Types of memory",
    "",
    "There are several discrete types of memory that you can store in your memory system:",
    "",
    "<types>",
    "<type>",
    "    <name>user</name>",
    "    <description>Details about the user: their role, goals, preferences, responsibilities, or knowledge level.</description>",
    "    <when_to_save>When you learn any details about the user's role, preferences, responsibilities, or knowledge</when_to_save>",
    "    <how_to_use>When your work should be informed by the user's profile or perspective. For example, if the user is asking you to explain a part of the code, you should answer that question in a way that is tailored to the specific details that they will find most valuable or that helps them build their mental model in relation to domain knowledge they already have.</how_to_use>",
    "    <examples>",
    "    user: I'm a data scientist investigating what logging we have in place",
    "    assistant: [saves user memory: user is a data scientist, currently focused on observability/logging]",
    "",
    "    user: I've been writing Go for ten years but this is my first time touching the React side of this repo",
    "    assistant: [saves user memory: deep Go expertise, new to React and this project's frontend — frame frontend explanations in terms of backend analogues]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>feedback</name>",
    "    <description>Guidance the user has given you about how to approach work — both what to avoid and what to keep doing. These are a very important type of memory to read and write as they allow you to remain coherent and responsive to the way you should approach work in the project. Record from failure AND success: if you only save corrections, you will avoid past mistakes but drift away from approaches the user has already validated, and may grow overly cautious.</description>",
    '    <when_to_save>Any time the user corrects your approach ("no not that", "don\'t", "stop doing X") OR confirms a non-obvious approach worked ("yes exactly", "perfect, keep doing that", accepting an unusual choice without pushback). Corrections are easy to notice; confirmations are quieter — watch for them. In both cases, save what is applicable to future conversations, especially if surprising or not obvious from the code. Include *why* so you can judge edge cases later.</when_to_save>',
    "    <how_to_use>Let these memories guide your behavior so that the user does not need to offer the same guidance twice.</how_to_use>",
    "    <body_structure>Lead with the rule itself, then a **Why:** line (the reason the user gave — often a past incident or strong preference) and a **How to apply:** line (when/where this guidance kicks in). Knowing *why* lets you judge edge cases instead of blindly following the rule.</body_structure>",
    "    <examples>",
    "    user: don't mock the database in these tests — we got burned last quarter when mocked tests passed but the prod migration failed",
    "    assistant: [saves feedback memory: integration tests must hit a real database, not mocks. Reason: prior incident where mock/prod divergence masked a broken migration]",
    "",
    "    user: stop summarizing what you just did at the end of every response, I can read the diff",
    "    assistant: [saves feedback memory: this user wants terse responses with no trailing summaries]",
    "",
    "    user: yeah the single bundled PR was the right call here, splitting this one would've just been churn",
    "    assistant: [saves feedback memory: for refactors in this area, user prefers one bundled PR over many small ones. Confirmed after I chose this approach — a validated judgment call, not a correction]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>project</name>",
    "    <description>Information that you learn about ongoing work, goals, initiatives, bugs, or incidents within the project that is not otherwise derivable from the code or git history. Project memories help you understand the broader context and motivation behind the work the user is doing within this working directory.</description>",
    '    <when_to_save>When you learn who is doing what, why, or by when. These states change relatively quickly so try to keep your understanding of this up to date. Always convert relative dates in user messages to absolute dates when saving (e.g., "Thursday" → "2026-03-05"), so the memory remains interpretable after time passes.</when_to_save>',
    "    <how_to_use>Use these memories to more fully understand the details and nuance behind the user's request and make better informed suggestions.</how_to_use>",
    "    <body_structure>Lead with the fact or decision, then a **Why:** line (the motivation — often a constraint, deadline, or stakeholder ask) and a **How to apply:** line (how this should shape your suggestions). Project memories decay fast, so the why helps future-you judge whether the memory is still load-bearing.</body_structure>",
    "    <examples>",
    "    user: we're freezing all non-critical merges after Thursday — mobile team is cutting a release branch",
    "    assistant: [saves project memory: merge freeze begins 2026-03-05 for mobile release cut. Flag any non-critical PR work scheduled after that date]",
    "",
    "    user: the reason we're ripping out the old auth middleware is that legal flagged it for storing session tokens in a way that doesn't meet the new compliance requirements",
    "    assistant: [saves project memory: auth middleware rewrite is driven by legal/compliance requirements around session token storage, not tech-debt cleanup — scope decisions should favor compliance over ergonomics]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>reference</name>",
    "    <description>Stores pointers to where information can be found in external systems. These memories allow you to remember where to look to find up-to-date information outside of the project directory.</description>",
    "    <when_to_save>When you learn about resources in external systems and their purpose. For example, that bugs are tracked in a specific project in Linear or that feedback can be found in a specific Slack channel.</when_to_save>",
    "    <how_to_use>When the user references an external system or information that may be in an external system.</how_to_use>",
    "    <examples>",
    '    user: check the Linear project "INGEST" if you want context on these tickets, that\'s where we track all pipeline bugs',
    '    assistant: [saves reference memory: pipeline bugs are tracked in Linear project "INGEST"]',
    "",
    "    user: the Grafana board at grafana.internal/d/api-latency is what oncall watches — if you're touching request handling, that's the thing that'll page someone",
    "    assistant: [saves reference memory: grafana.internal/d/api-latency is the oncall latency dashboard — check it when editing request-path code]",
    "    </examples>",
    "</type>",
    "</types>",
    "",
]

# 禁止保存的内容
WHAT_NOT_TO_SAVE_SECTION: list[str] = [
    "## What NOT to save in memory",
    "",
    "- Code patterns, conventions, architecture, file paths, or project structure — these can be derived by reading the current project state.",
    "- Git history, recent changes, or who-changed-what — `git log` / `git blame` are authoritative.",
    "- Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.",
    "- Anything already documented in CLAUDE.md files.",
    "- Ephemeral task details: in-progress work, temporary state, current conversation context.",
    "",
    "These exclusions apply even when the user explicitly asks you to save. If they ask you to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it — that is the part worth keeping.",
    "",
]

# 访问时机
WHEN_TO_ACCESS_SECTION: list[str] = [
    "## When to access memories",
    "- When memories seem relevant, or the user references prior-conversation work.",
    "- You MUST access memory when the user explicitly asks you to check, recall, or remember.",
    "- If the user says to *ignore* or *not use* memory: proceed as if MEMORY.md were empty. Do not apply remembered facts, cite, compare against, or mention memory content.",
    MEMORY_DRIFT_CAVEAT,
    "",
]

# 回忆验证
TRUSTING_RECALL_SECTION: list[str] = [
    "## Before recommending from memory",
    "",
    "A memory that names a specific function, file, or flag is a claim that it existed *when the memory was written*. It may have been renamed, removed, or never merged. Before recommending it:",
    "",
    "- If the memory names a file path: check the file exists.",
    "- If the memory names a function or flag: grep for it.",
    "- If the user is about to act on your recommendation (not just asking about history), verify first.",
    "",
    '"The memory says X exists" is not the same as "X exists now."',
    "",
    "A memory that summarizes repo state (activity logs, architecture snapshots) is frozen in time. If the user asks about *recent* or *current* state, prefer `git log` or reading the code over recalling the snapshot.",
    "",
]

# frontmatter 格式示例
FRONTMATTER_EXAMPLE: list[str] = [
    "```markdown",
    "---",
    "name: {{memory name}}",
    "description: {{one-line description — used to decide relevance}}",
    f"type: {{{{{', '.join(MEMORY_TYPES)}}}}}",
    "---",
    "",
    "{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}}",
    "```",
]

# 与其他持久化机制的区别
PERSISTENCE_SECTION: list[str] = [
    "## Memory and other forms of persistence",
    "Memory is one of several persistence mechanisms available to you as you assist the user in a given conversation. The distinction is often that memory can be recalled in future conversations and should not be used for persisting information that is only useful within the scope of the current conversation.",
    "- When to use or update a plan instead of memory: If you are about to start a non-trivial implementation task and would like to reach alignment with the user on your approach you should use a Plan rather than saving this information to memory. Similarly, if you already have a plan within the conversation and you have changed your approach persist that change by updating the plan rather than saving a memory.",
    "- When to use or update tasks instead of memory: When you need to break your work in current conversation into discrete steps or keep track of your progress use tasks instead of saving to memory. Tasks are great for persisting information about the work that needs to be done in the current conversation, but memory should be reserved for information that will be useful in future conversations.",
    "",
]


def truncate_entrypoint_content(
    raw: str,
    *,
    max_lines: int = MAX_ENTRYPOINT_LINES,
    max_bytes: int = MAX_ENTRYPOINT_BYTES,
) -> str:
    """截断 MEMORY.md 内容到行数和字节数上限，并附上警告。

    先按行截断（自然边界），再按字节截断（最后一个换行前截断，
    避免切断行内内容）。截断时附加警告说明原因。

    Args:
        raw: MEMORY.md 原始内容
        max_lines: 最大行数
        max_bytes: 最大字节数

    Returns:
        str: 截断后的内容（含警告）
    """
    trimmed = raw.strip()
    content_lines = trimmed.split("\n")
    line_count = len(content_lines)
    byte_count = len(trimmed)

    was_line_truncated = line_count > max_lines
    was_byte_truncated = byte_count > max_bytes

    if not was_line_truncated and not was_byte_truncated:
        return trimmed

    truncated = "\n".join(content_lines[:max_lines]) if was_line_truncated else trimmed
    if len(truncated) > max_bytes:
        cut_at = truncated.rfind("\n", 0, max_bytes)
        truncated = truncated[: cut_at if cut_at > 0 else max_bytes]

    if was_byte_truncated and not was_line_truncated:
        reason = f"{byte_count} bytes (limit: {max_bytes}) — index entries are too long"
    elif was_line_truncated and not was_byte_truncated:
        reason = f"{line_count} lines (limit: {max_lines})"
    else:
        reason = f"{line_count} lines and {byte_count} bytes"

    return (
        truncated + f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. Only part of it was loaded. "
        "Keep index entries to one line under ~200 chars; move detail into topic files."
    )


def build_memory_lines(memory_dir: Path) -> list[str]:
    """构建完整的记忆行为指令（不含 MEMORY.md 内容）。

    类型定义 → 禁止保存 → 保存步骤 → 访问时机 → 回忆验证 → 持久化机制区分。

    Args:
        memory_dir: 记忆目录

    Returns:
        list[str]: 提示词行列表
    """
    how_to_save = [
        "## How to save memories",
        "",
        "Saving a memory is a two-step process:",
        "",
        "**Step 1** — write the memory to its own file **inside the type-specific subdirectory** matching its `type` field (e.g. `user/user_role.md`, `feedback/feedback_testing.md`, `project/project_plan.md`, `reference/reference_linear.md`) using this frontmatter format:",
        "",
        *FRONTMATTER_EXAMPLE,
        "",
        f"**Step 2** — add a pointer to that file in `{ENTRYPOINT_NAME}`. `{ENTRYPOINT_NAME}` is an index, not a memory — each entry should be one line, under ~150 characters: `- [Title](user/user_role.md) — one-line hook` (use the path relative to the memory directory, including the type subdirectory). It has no frontmatter. Never write memory content directly into `{ENTRYPOINT_NAME}`.",
        "",
        f"- `{ENTRYPOINT_NAME}` is always loaded into your conversation context — lines after {MAX_ENTRYPOINT_LINES} will be truncated, so keep the index concise",
        "- Keep the name, description, and type fields in memory files up-to-date with the content",
        "- Organize memory semantically by topic, not chronologically",
        "- Update or remove memories that turn out to be wrong or outdated",
        "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        "",
    ]

    lines: list[str] = [
        f"# {AUTO_MEM_DISPLAY_NAME}",
        "",
        f"You have a persistent, file-based memory system at `{memory_dir}`. {DIR_EXISTS_GUIDANCE}",
        "",
        "You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        *TYPES_SECTION,
        *WHAT_NOT_TO_SAVE_SECTION,
        "",
        *how_to_save,
        *WHEN_TO_ACCESS_SECTION,
        "",
        *TRUSTING_RECALL_SECTION,
        "",
        *PERSISTENCE_SECTION,
    ]
    return lines


def load_memory_prompt(
    cwd: str | Path,
    *,
    max_entrypoint_lines: int = MAX_ENTRYPOINT_LINES,
    max_entrypoint_bytes: int = MAX_ENTRYPOINT_BYTES,
) -> str | None:
    """构建当前项目的记忆提示词段落。

    Args:
        cwd: 当前工作目录
        max_entrypoint_lines: 入口点文件最大行数（覆盖默认 200）
        max_entrypoint_bytes: 入口点文件最大字节数（覆盖默认 25000）

    Returns:
        str | None: 格式化后的记忆提示词，如果失败返回 None
    """
    memory_dir = get_memory_dir_for_cwd_no_create(cwd)
    entrypoint = memory_dir / "MEMORY.md"
    lines = build_memory_lines(memory_dir)

    if entrypoint.exists():
        raw = entrypoint.read_text(encoding="utf-8", errors="replace")
        content = truncate_entrypoint_content(
            raw,
            max_lines=max_entrypoint_lines,
            max_bytes=max_entrypoint_bytes,
        )
        if content:
            lines.extend(["", "## MEMORY.md", "```md", content, "```"])
    else:
        lines.extend(
            [
                "",
                "## MEMORY.md",
                "(not created yet)",
            ]
        )

    return "\n".join(lines)
