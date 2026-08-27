"""
代理定义加载系统模块
==================

本模块提供 IllusionAgent 代理定义加载和管理功能。

主要功能：
    - 内置代理定义
    - 从 markdown 文件加载代理定义
    - YAML frontmatter 解析

类说明：
    - AgentDefinition: 完整的代理定义数据模型

常量说明：
    - AGENT_COLORS: 有效的代理颜色名称
    - EFFORT_LEVELS: 有效的 Effort 级别
    - PERMISSION_MODES: 有效的权限模式
    - MEMORY_SCOPES: 有效的记忆范围
    - ISOLATION_MODES: 有效的隔离模式

函数说明：
    - get_builtin_agent_definitions: 获取内置代理定义
    - get_all_agent_definitions: 获取所有代理定义
    - get_agent_definition: 获取指定名称的代理定义
    - load_agents_dir: 从目录加载代理定义

使用示例：
    >>> from illusion.coordinator import get_builtin_agent_definitions
    >>> agents = get_builtin_agent_definitions()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from illusion.config.paths import get_config_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: 有效的颜色名称 (对应 TS 中的 AgentColorName)
AGENT_COLORS: frozenset[str] = frozenset(
    {
        "red",  # 红色
        "green",  # 绿色
        "blue",  # 蓝色
        "yellow",  # 黄色
        "purple",  # 紫色
        "orange",  # 橙色
        "cyan",  # 青色
        "magenta",  # 品红
        "white",  # 白色
        "gray",  # 灰色
    }
)

#: 有效的 Effort 级别 (对应 TS 中的 EFFORT_LEVELS)
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

#: 有效的权限模式 (对应 TS 中的 PERMISSION_MODES)
PERMISSION_MODES: tuple[str, ...] = (
    "default",  # 默认
    "acceptEdits",  # 接受编辑
    "bypassPermissions",  # 绕过权限
    "plan",  # 计划模式
    "dontAsk",  # 不询问
)

#: 有效的记忆范围 (对应 TS 中的 AgentMemoryScope)
MEMORY_SCOPES: tuple[str, ...] = ("user", "project", "local")

#: 有效的隔离模式
ISOLATION_MODES: tuple[str, ...] = ("worktree", "remote")


# ---------------------------------------------------------------------------
# AgentDefinition model
# ---------------------------------------------------------------------------


class AgentDefinition(BaseModel):
    """完整的代理定义，包含所有配置字段
    
    字段映射到 TypeScript ``BaseAgentDefinition``:
    - ``name``          → ``agentType``
    - ``description``   → ``whenToUse``
    - ``system_prompt`` → ``getSystemPrompt()`` 返回值
    - ``tools``         → ``tools`` (None 表示所有工具 / ``['*']`` 等效)
    - ``disallowed_tools`` → ``disallowedTools``
    - ``skills``        → ``skills``
    - ``mcp_servers``   → ``mcpServers``
    - ``hooks``         → ``hooks``
    - ``color``         → ``color``
    - ``model``         → ``model``
    - ``effort``        → ``effort``
    - ``permission_mode`` → ``permissionMode``
    - ``max_turns``     → ``maxTurns``
    - ``filename``      → ``filename``
    - ``base_dir``      → ``baseDir``
    - ``critical_system_reminder`` → ``criticalSystemReminder_EXPERIMENTAL``
    - ``required_mcp_servers`` → ``requiredMcpServers``
    - ``background``    → ``background``
    - ``initial_prompt`` → ``initialPrompt``
    - ``memory``        → ``memory``
    - ``isolation``     → ``isolation``
    - ``omit_claude_md`` → ``omitClaudeMd``
    """

    # --- required ---
    name: str  # 代理类型标识
    description: str  # 使用时机描述

    # --- prompt / tools ---
    system_prompt: str | None = None  # 系统提示词
    tools: list[str] | None = None  # None 表示所有工具允许; ['*'] 等效
    disallowed_tools: list[str] | None = None  # 禁止的工具列表

    # --- model & effort ---
    model: str | None = None  # 模型覆盖; None 表示继承默认值
    effort: str | int | None = None  # "low" | "medium" | "high" 或正整数

    # --- permissions ---
    permission_mode: str | None = None  # PERMISSION_MODES 之一

    # --- agent loop control ---
    max_turns: int | None = None  # 代理停止前的最大代理轮次数; 必须 > 0

    # --- skills & mcp ---
    skills: list[str] = Field(default_factory=list)  # 技能列表
    mcp_servers: list[Any] | None = None  # str 引用或 {name: config} 字典
    required_mcp_servers: list[str] | None = None  # 必须存在的服务器名模式

    # --- hooks ---
    hooks: dict[str, Any] | None = None  # 代理启动时注册的作用域 hooks

    # --- ui ---
    color: str | None = None  # AGENT_COLORS 之一

    # --- lifecycle ---
    background: bool = False  # 生成时始终作为后台任务运行
    initial_prompt: str | None = None  # 附加到第一个用户回合
    memory: str | None = None  # MEMORY_SCOPES 之一
    isolation: str | None = None  # ISOLATION_MODES 之一

    # --- metadata ---
    filename: str | None = None  # 不含 .md 扩展名的原始文件名
    base_dir: str | None = None  # 加载代理定义的目录
    critical_system_reminder: str | None = None  # 短消息，在每个用户回合重新注入
    pending_snapshot_update: dict[str, Any] | None = None  # 记忆快照跟踪
    omit_claude_md: bool = False  # 跳过此代理的 CLAUDE.md 注入

    # --- Python-specific ---
    permissions: list[str] = Field(default_factory=list)  # 额外的权限规则
    subagent_type: str = "general-purpose"  # 线束使用的路由键
    source: Literal["builtin", "user", "plugin"] = "builtin"  # 来源


# ---------------------------------------------------------------------------
# System-prompt constants (translated from TS built-in agent files)
# ---------------------------------------------------------------------------

# 共享代理前缀
_SHARED_AGENT_PREFIX = (
    "You are an agent for Illusion Agent. "
    "Given the user's message, you should use the tools available to complete the task. "
    "Complete the task fully — don't gold-plate, but don't leave it half-done."
)

_SHARED_AGENT_GUIDELINES = """Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture
- Investigating complex questions that require exploring many files
- Performing multi-step research tasks

Guidelines:
- For file searches: search broadly when you don't know where something lives. Use Read when you know the specific file path.
- For analysis: Start broad and narrow down. Use multiple search strategies if the first doesn't yield results.
- Be thorough: Check multiple locations, consider different naming conventions, look for related files.
- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested."""

# 通用代理系统提示词
_GENERAL_PURPOSE_SYSTEM_PROMPT = (
    f"{_SHARED_AGENT_PREFIX} When you complete the task, respond with a concise report covering "
    "what was done and any key findings — the caller will relay this to the user, so it only needs "
    f"the essentials.\n\n{_SHARED_AGENT_GUIDELINES}"
)

# 探索代理系统提示词
_EXPLORE_SYSTEM_PROMPT = """You are a file search specialist for Illusion Agent. You excel at thoroughly navigating and exploring codebases.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use Glob for broad file pattern matching
- Use Grep for searching file contents with regex
- Use Read when you know the specific file path you need to read
- Use Bash for shell operations when needed
- Adapt your search approach based on the thoroughness level specified by the caller
- Communicate your final report directly as a regular message

NOTE: You are meant to be a fast agent that returns output as quickly as possible. In order to achieve this you must:
- Make efficient use of the tools that you have at your disposal: be smart about how you search for files and implementations
- Wherever possible you should try to spawn multiple parallel tool calls for grepping and reading files

Complete the user's search request efficiently and report your findings clearly."""


# 验证代理系统提示词
_VERIFICATION_SYSTEM_PROMPT = """You are a verification specialist. Your job is not to confirm the implementation works — it's to try to break it.

You have two documented failure patterns. First, verification avoidance: when faced with a check, you find reasons not to run it — you read code, narrate what you would test, write "PASS," and move on. Second, being seduced by the first 80%: you see a polished UI or a passing test suite and feel inclined to pass it, not noticing half the buttons do nothing, the state vanishes on refresh, or the backend crashes on bad input. The first 80% is the easy part. Your entire value is in finding the last 20%. The caller may spot-check your commands by re-running them — if a PASS step has no command output, or output that doesn't match re-execution, your report gets rejected.

=== CRITICAL: DO NOT MODIFY THE PROJECT ===
You are STRICTLY PROHIBITED from:
- Creating, modifying, or deleting any files IN THE PROJECT DIRECTORY
- Installing dependencies or packages
- Running git write operations (add, commit, push)

You MAY write ephemeral test scripts to a temp directory (/tmp or $TMPDIR) via Bash redirection when inline commands aren't sufficient — e.g., a multi-step race harness or a Playwright test. Clean up after yourself.

Check your ACTUAL available tools rather than assuming from this prompt. You may have WebFetch, WebSearch, MCP browser tools, or other MCP tools depending on the session — do not skip capabilities you didn't think to check for.

=== WHAT YOU RECEIVE ===
You will receive: the original task description, files changed, approach taken, and optionally a plan file path.

=== VERIFICATION STRATEGY ===
Adapt your strategy based on what was changed:

**Frontend changes**: Start dev server → check your tools for browser automation (MCP browser tools, WebFetch) and USE them to navigate, screenshot, click, and read console — do NOT say "needs a real browser" without attempting → curl a sample of page subresources since HTML can serve 200 while everything it references fails → run frontend tests
**Backend/API changes**: Start server → curl/fetch endpoints → verify response shapes against expected values (not just status codes) → test error handling → check edge cases
**CLI/script changes**: Run with representative inputs → verify stdout/stderr/exit codes → test edge inputs (empty, malformed, boundary) → verify --help / usage output is accurate
**Infrastructure/config changes**: Validate syntax → dry-run where possible (terraform plan, kubectl apply --dry-run=server, docker build, nginx -t) → check env vars / secrets are actually referenced, not just defined
**Library/package changes**: Build → full test suite → import the library from a fresh context and exercise the public API as a consumer would → verify exported types match README/docs examples
**Bug fixes**: Reproduce the original bug → verify fix → run regression tests → check related functionality for side effects
**Mobile (iOS/Android)**: Clean build → install on simulator/emulator → dump accessibility/UI tree (idb ui describe-all / uiautomator dump), find elements by label, tap by tree coords, re-dump to verify; screenshots secondary → kill and relaunch to test persistence → check crash logs (logcat / device console)
**Data/ML pipeline**: Run with sample input → verify output shape/schema/types → test empty input, single row, NaN/null handling → check for silent data loss (row counts in vs out)
**Database migrations**: Run migration up → verify schema matches intent → run migration down (reversibility) → test against existing data, not just empty DB
**Refactoring (no behavior change)**: Existing test suite MUST pass unchanged → diff the public API surface (no new/removed exports) → spot-check observable behavior is identical (same inputs → same outputs)
**Other change types**: The pattern is always the same — (a) figure out how to exercise this change directly (run/call/invoke/deploy it), (b) check outputs against expectations, (c) try to break it with inputs/conditions the implementer didn't test. The strategies above are worked examples for common cases.

=== REQUIRED STEPS (universal baseline) ===
1. Read the project's ILLUSION.md / CLAUDE.md / AGENTS.md / README for build/test commands and conventions. Check package.json / Makefile / pyproject.toml for script names. If the implementer pointed you to a plan or spec file, read it — that's the success criteria.
2. Run the build (if applicable). A broken build is an automatic FAIL.
3. Run the project's test suite (if it has one). Failing tests are an automatic FAIL.
4. Run linters/type-checkers if configured (eslint, tsc, mypy, etc.).
5. Check for regressions in related code.

Then apply the type-specific strategy above. Match rigor to stakes: a one-off script doesn't need race-condition probes; production payments code needs everything.

Test suite results are context, not evidence. Run the suite, note pass/fail, then move on to your real verification. The implementer is an LLM too — its tests may be heavy on mocks, circular assertions, or happy-path coverage that proves nothing about whether the system actually works end-to-end.

=== RECOGNIZE YOUR OWN RATIONALIZATIONS ===
You will feel the urge to skip checks. These are the exact excuses you reach for — recognize them and do the opposite:
- "The code looks correct based on my reading" — reading is not verification. Run it.
- "The implementer's tests already pass" — the implementer is an LLM. Verify independently.
- "This is probably fine" — probably is not verified. Run it.
- "Let me start the server and check the code" — no. Start the server and hit the endpoint.
- "I don't have a browser" — did you actually check for MCP browser tools or WebFetch? If present, use them. If an MCP tool fails, troubleshoot (server running? selector right?). The fallback exists so you don't invent your own "can't do this" story.
- "This would take too long" — not your call.
If you catch yourself writing an explanation instead of a command, stop. Run the command.

=== ADVERSARIAL PROBES (adapt to the change type) ===
Functional tests confirm the happy path. Also try to break it:
- **Concurrency** (servers/APIs): parallel requests to create-if-not-exists paths — duplicate sessions? lost writes?
- **Boundary values**: 0, -1, empty string, very long strings, unicode, MAX_INT
- **Idempotency**: same mutating request twice — duplicate created? error? correct no-op?
- **Orphan operations**: delete/reference IDs that don't exist
These are seeds, not a checklist — pick the ones that fit what you're verifying.

=== BEFORE ISSUING PASS ===
Your report must include at least one adversarial probe you ran (concurrency, boundary, idempotency, orphan op, or similar) and its result — even if the result was "handled correctly." If all your checks are "returns 200" or "test suite passes," you have confirmed the happy path, not verified correctness. Go back and try to break something.

=== BEFORE ISSUING FAIL ===
You found something that looks broken. Before reporting FAIL, check you haven't missed why it's actually fine:
- **Already handled**: is there defensive code elsewhere (validation upstream, error recovery downstream) that prevents this?
- **Intentional**: does ILLUSION.md / CLAUDE.md / AGENTS.md / comments / commit message explain this as deliberate?
- **Not actionable**: is this a real limitation but unfixable without breaking an external contract (stable API, protocol spec, backwards compat)? If so, note it as an observation, not a FAIL — a "bug" that can't be fixed isn't actionable.
Don't use these as excuses to wave away real issues — but don't FAIL on intentional behavior either.

=== OUTPUT FORMAT (REQUIRED) ===
Every check MUST follow this structure. A check without a Command run block is not a PASS — it's a skip.

```
### Check: [what you're verifying]
**Command run:**
  [exact command you executed]
**Output observed:**
  [actual terminal output — copy-paste, not paraphrased. Truncate if very long but keep the relevant part.]
**Result: PASS** (or FAIL — with Expected vs Actual)
```

Good:
```
### Check: POST /api/register validation
**Command run:**
  curl -s -X POST http://localhost:3000/api/register -H 'Content-Type: application/json' -d '{"email":"test@example.com","password":"short"}'
**Output observed:**
  {"error":"Password must be at least 8 characters","code":"VALIDATION_ERROR"}
**Result: PASS**
```

Bad (rejected):
```
### Check: POST /api/register validation
**Result: PASS**
Evidence: Reviewed the route handler in routes/auth.py. The logic correctly validates
email format and password length before DB insert.
```
(No command run. Reading code is not verification.)

End with exactly this line (parsed by caller):

VERDICT: PASS
or
VERDICT: FAIL
or
VERDICT: PARTIAL

PARTIAL is for environmental limitations only (no test framework, tool unavailable, server can't start) — not for "I'm unsure whether this is a bug." If you can run the check, you must decide PASS or FAIL.

Use the literal string `VERDICT: ` followed by exactly one of `PASS`, `FAIL`, `PARTIAL`. No markdown bold, no punctuation, no variation.
- **FAIL**: include what failed, exact error output, reproduction steps.
- **PARTIAL**: what was verified, what could not and why (missing tool/env), what the implementer should know."""

# 验证关键提醒
_VERIFICATION_CRITICAL_REMINDER = (
    "CRITICAL: This is a VERIFICATION-ONLY task. You CANNOT edit, write, or create files "
    "IN THE PROJECT DIRECTORY (tmp is allowed for ephemeral test scripts). "
    "You MUST end with VERDICT: PASS, VERDICT: FAIL, or VERDICT: PARTIAL."
)


# ---------------------------------------------------------------------------
# Built-in agent definitions
# ---------------------------------------------------------------------------

# 内置代理定义列表
_BUILTIN_AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        name="general-purpose",  # 通用代理
        description=(
            "General-purpose agent for researching complex questions, searching for code, "
            "and executing multi-step tasks. When you are searching for a keyword or file "
            "and are not confident that you will find the right match in the first few tries "
            "use this agent to perform the search for you."
        ),
        tools=["*"],  # 所有工具
        system_prompt=_GENERAL_PURPOSE_SYSTEM_PROMPT,  # 系统提示词
        subagent_type="general-purpose",  # 代理类型
        source="builtin",  # 来源
        base_dir="built-in",  # 基础目录
    ),
    AgentDefinition(
        name="explore",  # 探索代理
        description=(
            "Fast agent specialized for exploring codebases. Use this when you need to "
            "quickly find files by patterns (eg. \"src/components/**/*.tsx\"), search code "
            "for keywords (eg. \"API endpoints\"), or answer questions about the codebase "
            "(eg. \"how do API endpoints work?\"). When calling this agent, specify the "
            "desired thoroughness level: \"quick\" for basic searches, \"medium\" for "
            "moderate exploration, or \"very thorough\" for comprehensive analysis across "
            "multiple locations and naming conventions."  # 使用说明
        ),
        disallowed_tools=["edit_file", "write_file"],  # 禁止的工具
        system_prompt=_EXPLORE_SYSTEM_PROMPT,  # 系统提示词
        omit_claude_md=True,  # 跳过CLAUDE.md
        subagent_type="explore",  # 代理类型
        source="builtin",  # 来源
        base_dir="built-in",  # 基础目录
    ),
    AgentDefinition(
        name="verification",  # 验证代理
        description=(
            "Use this agent to verify that implementation work is correct before reporting "
            "completion. Invoke after non-trivial tasks (3+ file edits, backend/API changes, "
            "infrastructure changes). Pass the ORIGINAL user task description, list of files "
            "changed, and approach taken. The agent runs builds, tests, linters, and checks "
            "to produce a PASS/FAIL/PARTIAL verdict with evidence."  # 使用说明
        ),
        disallowed_tools=["edit_file", "write_file"],  # 禁止的工具
        system_prompt=_VERIFICATION_SYSTEM_PROMPT,  # 系统提示词
        critical_system_reminder=_VERIFICATION_CRITICAL_REMINDER,  # 关键提醒
        color="red",  # 颜色
        background=True,  # 后台运行
        model="inherit",  # 模型
        subagent_type="verification",  # 代理类型
        source="builtin",  # 来源
        base_dir="built-in",  # 基础目录
    ),
]


def get_builtin_agent_definitions() -> list[AgentDefinition]:
    """获取内置代理定义列表
    
    Returns:
        list[AgentDefinition]: 内置代理定义列表
    """
    return list(_BUILTIN_AGENTS)


# ---------------------------------------------------------------------------
# Markdown / YAML-frontmatter loader
# ---------------------------------------------------------------------------


def _parse_agent_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """从markdown文件解析YAML frontmatter
    
    返回 (frontmatter_dict, body) 元组。使用 ``yaml.safe_load`` 进行
    正确的YAML解析 (支持 hooks, mcpServers 等嵌套结构)。
    
    Args:
        content: 文件完整内容
    
    Returns:
        tuple[dict[str, Any], str]: (frontmatter字典, body文本)
    """
    frontmatter: dict[str, Any] = {}
    body = content

    lines = content.splitlines()  # 分割行
    if not lines or lines[0].strip() != "---":  # 无frontmatter
        return frontmatter, body

    end_index: int | None = None  # 结束索引
    for i, line in enumerate(lines[1:], start=1):  # 遍历内容行
        if line.strip() == "---":  # 找到结束标记
            end_index = i
            break

    if end_index is None:  # 未找到结束标记
        return frontmatter, body

    fm_text = "\n".join(lines[1:end_index])  # frontmatter文本
    try:
        parsed = yaml.safe_load(fm_text)  # 解析YAML
        if isinstance(parsed, dict):  # 是字典
            frontmatter = parsed
    except yaml.YAMLError:  # 解析失败
        # 回退到简单的 key:value 解析
        for fm_line in lines[1:end_index]:
            if ":" in fm_line:
                key, _, value = fm_line.partition(":")  # 分割
                frontmatter[key.strip()] = value.strip().strip("'\"")  # 添加

    # Body是 --- 之后的所有内容
    body = "\n".join(lines[end_index + 1 :]).strip()  # 合并
    return frontmatter, body


def _parse_str_list(raw: Any) -> list[str] | None:
    """将逗号分隔的字符串或列表解析为字符串列表
    
    Args:
        raw: 原始值
    
    Returns:
        list[str] | None: 解析后的列表
    """
    if raw is None:  # 空值
        return None
    if isinstance(raw, list):  # 列表
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):  # 字符串
        items = [t.strip() for t in raw.split(",") if t.strip()]  # 分割
        return items if items else None
    return None


def _parse_positive_int(raw: Any) -> int | None:
    """从frontmatter解析正整数，无效时返回None
    
    Args:
        raw: 原始值
    
    Returns:
        int | None: 解析后的整数
    """
    if raw is None:  # 空值
        return None
    try:
        val = int(raw)  # 转换为整数
        return val if val > 0 else None  # 正数
    except (TypeError, ValueError):  # 转换失败
        return None


def load_agents_dir(directory: Path) -> list[AgentDefinition]:
    """从目录中的 .md 文件加载代理定义
    
    每个文件应包含YAML frontmatter，至少有 ``name`` 和
    ``description`` 字段。markdown body 成为 ``system_prompt``。
    
    支持的 frontmatter 字段 (全部可选，除非注明):
    
    必需:
    * ``name`` — 代理类型标识
    * ``description`` — 显示给生成代理的使用时机描述
    
    可选:
    * ``tools`` — 逗号分隔或YAML列表的工具名
    * ``disallowedTools`` / ``disallowed_tools`` — 逗号分隔或列表的禁止工具
    * ``model`` — 模型覆盖 (如 "default", "inherit")
    * ``effort`` — "low", "medium", "high", 或正整数
    * ``permissionMode`` / ``permission_mode`` — PERMISSION_MODES 之一
    * ``maxTurns`` / ``max_turns`` — 正整数回合限制
    * ``skills`` — 逗号分隔或技能名列表
    * ``mcpServers`` / ``mcp_servers`` — MCP服务器引用或内联配置的列表
    * ``hooks`` — YAML字典的作用域hooks
    * ``color`` — AGENT_COLORS 之一
    * ``background`` — true/false; 作为后台任务运行
    * ``initialPrompt`` / ``initial_prompt` - 附加到第一个用户回合的字符串
    * ``memory`` — MEMORY_SCOPES 之一
    * ``isolation`` — ISOLATION_MODES 之一
    * ``omitClaudeMd`` / ``omit_claude_md`` — true/false; 跳过CLAUDE.md注入
    * ``criticalSystemReminder`` / ``critical_system_reminder`` — 重新注入的消息
    * ``requiredMcpServers`` / ``required_mcp_servers`` — 必需服务器模式列表
    * ``permissions`` — 逗号分隔的额外权限规则
    * ``subagent_type`` — 路由键 (Python特定，默认为name)
    
    Args:
        directory: 代理定义目录
    
    Returns:
        list[AgentDefinition]: 加载的代理定义列表
    """
    agents: list[AgentDefinition] = []  # 代理列表

    if not directory.is_dir():  # 非目录
        return agents

    for path in sorted(directory.glob("*.md")):  # 遍历md文件
        try:
            content = path.read_text(encoding="utf-8")  # 读取内容
            frontmatter, body = _parse_agent_frontmatter(content)  # 解析frontmatter

            name = str(frontmatter.get("name", "")).strip() or path.stem  # 名称
            description = str(frontmatter.get("description", "")).strip()  # 描述
            if not description:  # 无描述
                description = f"Agent: {name}"  # 默认描述

            # 从YAML反转义 literal \n
            description = description.replace("\\n", "\n")

            # --- tools ---
            tools = _parse_str_list(frontmatter.get("tools"))  # 工具

            # --- disallowed tools ---
            disallowed_raw = frontmatter.get(
                "disallowedTools", frontmatter.get("disallowed_tools")  # 尝试两个键名
            )
            disallowed_tools = _parse_str_list(disallowed_raw)  # 解析

            # --- model ---
            model_raw = frontmatter.get("model")  # 模型
            model: str | None = None
            if isinstance(model_raw, str) and model_raw.strip():  # 字符串
                trimmed = model_raw.strip()
                model = "inherit" if trimmed.lower() == "inherit" else trimmed  # inherit转换

            # --- effort ---
            effort_raw = frontmatter.get("effort")  # effort
            effort: str | int | None = None
            if effort_raw is not None:  # 有值
                if isinstance(effort_raw, int):  # 整数
                    effort = effort_raw if effort_raw > 0 else None  # 正数
                elif isinstance(effort_raw, str) and effort_raw in EFFORT_LEVELS:  # 有效字符串
                    effort = effort_raw
                else:
                    logger.debug("Agent %s: invalid effort %r", name, effort_raw)  # 无效

            # --- permissionMode ---
            perm_raw = frontmatter.get("permissionMode", frontmatter.get("permission_mode"))  # 尝试两个键名
            permission_mode: str | None = None
            if isinstance(perm_raw, str) and perm_raw in PERMISSION_MODES:  # 有效值
                permission_mode = perm_raw
            elif perm_raw is not None:  # 有值但无效
                logger.debug("Agent %s: invalid permissionMode %r", name, perm_raw)

            # --- maxTurns ---
            max_turns_raw = frontmatter.get("maxTurns", frontmatter.get("max_turns"))  # 尝试两个键名
            max_turns = _parse_positive_int(max_turns_raw)  # 解析
            if max_turns_raw is not None and max_turns is None:  # 有值但解析失败
                logger.debug("Agent %s: invalid maxTurns %r", name, max_turns_raw)

            # --- skills ---
            skills_raw = frontmatter.get("skills")  # 技能
            skills = _parse_str_list(skills_raw) or []  # 解析

            # --- mcpServers ---
            mcp_raw = frontmatter.get("mcpServers", frontmatter.get("mcp_servers"))  # MCP服务器
            mcp_servers: list[Any] | None = None
            if isinstance(mcp_raw, list):  # 列表
                mcp_servers = mcp_raw if mcp_raw else None  # 空列表转None

            # --- hooks ---
            hooks_raw = frontmatter.get("hooks")  # hooks
            hooks: dict[str, Any] | None = None
            if isinstance(hooks_raw, dict):  # 字典
                hooks = hooks_raw

            # --- color ---
            color_raw = frontmatter.get("color")  # 颜色
            color: str | None = None
            if isinstance(color_raw, str) and color_raw in AGENT_COLORS:  # 有效值
                color = color_raw

            # --- background ---
            bg_raw = frontmatter.get("background")  # 后台
            background = bg_raw is True or bg_raw == "true"  # 布尔转换

            # --- initialPrompt ---
            ip_raw = frontmatter.get("initialPrompt", frontmatter.get("initial_prompt"))  # 初始提示词
            initial_prompt: str | None = None
            if isinstance(ip_raw, str) and ip_raw.strip():  # 非空字符串
                initial_prompt = ip_raw

            # --- memory ---
            memory_raw = frontmatter.get("memory")  # 记忆
            memory: str | None = None
            if isinstance(memory_raw, str) and memory_raw in MEMORY_SCOPES:  # 有效值
                memory = memory_raw
            elif memory_raw is not None:  # 有值但无效
                logger.debug("Agent %s: invalid memory %r", name, memory_raw)

            # --- isolation ---
            iso_raw = frontmatter.get("isolation")  # 隔离
            isolation: str | None = None
            if isinstance(iso_raw, str) and iso_raw in ISOLATION_MODES:  # 有效值
                isolation = iso_raw
            elif iso_raw is not None:  # 有值但无效
                logger.debug("Agent %s: invalid isolation %r", name, iso_raw)

            # --- omitClaudeMd ---
            ocm_raw = frontmatter.get("omitClaudeMd", frontmatter.get("omit_claude_md"))  # 跳过CLAUDE.md
            omit_claude_md = ocm_raw is True or ocm_raw == "true"  # 布尔转换

            # --- criticalSystemReminder ---
            csr_raw = frontmatter.get(
                "criticalSystemReminder", frontmatter.get("critical_system_reminder")  # 关键提醒
            )
            critical_system_reminder: str | None = None
            if isinstance(csr_raw, str) and csr_raw.strip():  # 非空字符串
                critical_system_reminder = csr_raw

            # --- requiredMcpServers ---
            rms_raw = frontmatter.get(
                "requiredMcpServers", frontmatter.get("required_mcp_servers")  # 必需MCP服务器
            )
            required_mcp_servers = _parse_str_list(rms_raw)  # 解析

            # --- permissions (Python-specific) ---
            permissions: list[str] = []  # 权限
            raw_perms = frontmatter.get("permissions", "")  # 权限规则
            if raw_perms:  # 有值
                permissions = [p.strip() for p in str(raw_perms).split(",") if p.strip()]  # 解析

            agents.append(
                AgentDefinition(
                    name=name,  # 名称
                    description=description,  # 描述
                    system_prompt=body or None,  # 系统提示词
                    tools=tools,  # 工具
                    disallowed_tools=disallowed_tools,  # 禁止工具
                    model=model,  # 模型
                    effort=effort,  # effort
                    permission_mode=permission_mode,  # 权限模式
                    max_turns=max_turns,  # 最大回合
                    skills=skills,  # 技能
                    mcp_servers=mcp_servers,  # MCP服务器
                    hooks=hooks,  # hooks
                    color=color,  # 颜色
                    background=background,  # 后台
                    initial_prompt=initial_prompt,  # 初始提示词
                    memory=memory,  # 记忆
                    isolation=isolation,  # 隔离
                    omit_claude_md=omit_claude_md,  # 跳过CLAUDE.md
                    critical_system_reminder=critical_system_reminder,  # 关键提醒
                    required_mcp_servers=required_mcp_servers,  # 必需MCP服务器
                    permissions=permissions,  # 权限
                    filename=path.stem,  # 文件名
                    base_dir=str(directory),  # 基础目录
                    subagent_type=str(frontmatter.get("subagent_type", name)),  # 代理类型
                    source="user",  # 来源
                )
            )
        except Exception:  # 解析失败
            logger.debug("Failed to parse agent from %s", path, exc_info=True)
            continue

    return agents


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _get_user_agents_dir() -> Path:
    """获取用户代理定义目录
    
    Returns:
        Path: 用户代理目录路径 (~/.illusion/agents/)
    """
    return get_config_dir() / "agents"


def get_all_agent_definitions(cwd: str | None = None) -> list[AgentDefinition]:
    """获取所有代理定义: 内置 + 用户 + 项目级 + 插件

    合并顺序 (相同名称后写入者胜出):
    1. 内置代理 (最低优先级)
    2. 用户代理 (~/.illusion/agents/)
    3. 项目级代理 ({cwd}/.illusion/agents/)
    4. 插件代理 (从活动的插件加载)

    用户定义覆盖同名内置代理; 项目级定义覆盖用户定义;
    插件定义覆盖项目级定义。

    Args:
        cwd: 项目工作目录（项目级/插件代理的加载基准）；缺省为当前进程目录

    Returns:
        list[AgentDefinition]: 所有代理定义列表
    """
    import os

    from illusion.config.paths import get_project_config_dir

    agent_map: dict[str, AgentDefinition] = {}  # 代理映射

    # 1. 内置代理 (最低优先级)
    for agent in get_builtin_agent_definitions():
        agent_map[agent.name] = agent

    # 2. 用户自定义代理
    user_agents = load_agents_dir(_get_user_agents_dir())
    for agent in user_agents:
        agent_map[agent.name] = agent

    # 3. 项目级代理 — 与 AgentWizard project scope 写入路径一致
    try:
        project_agents_dir = get_project_config_dir(cwd or os.getcwd()) / "agents"
        project_agents = load_agents_dir(project_agents_dir)
        for agent in project_agents:
            agent_map[agent.name] = agent
    except (OSError, ValueError) as exc:
        logger.debug("加载项目级代理定义失败: %s", exc)

    # 4. 插件代理 — 延迟加载以避免导入循环
    try:
        from illusion.config.settings import load_settings
        from illusion.plugins.loader import load_plugins

        settings = load_settings()  # 加载设置
        cwd = os.getcwd()  # 当前目录
        for plugin in load_plugins(settings, cwd):  # 加载插件
            if not plugin.enabled:  # 未启用
                continue
            for agent_def in getattr(plugin, "agents", []):  # 遍历代理定义
                if isinstance(agent_def, AgentDefinition):  # 是代理定义
                    agent_map[agent_def.name] = agent_def  # 添加
    except (ImportError, OSError, ValueError, TypeError, RuntimeError) as exc:
        logger.debug("加载插件代理定义失败: %s", exc)

    return list(agent_map.values())


def get_agent_definition(name: str) -> AgentDefinition | None:
    """获取指定名称的代理定义
    
    Args:
        name: 代理名称
    
    Returns:
        AgentDefinition | None: 代理定义，若不存在返回None
    """
    for agent in get_all_agent_definitions():  # 遍历所有代理
        if agent.name == name:  # 匹配
            return agent
    return None


def has_required_mcp_servers(agent: AgentDefinition, available_servers: list[str]) -> bool:
    """检查代理的必需MCP服务器是否全部可用
    
    ``required_mcp_servers`` 中的每个模式必须匹配 (不区分大小写)
    ``available_servers`` 中至少一个服务器的子字符串。
    
    Args:
        agent: 代理定义
        available_servers: 可用的MCP服务器列表
    
    Returns:
        bool: 所有必需MCP服务器是否可用
    """
    if not agent.required_mcp_servers:  # 无必需服务器
        return True
    return all(
        any(pattern.lower() in server.lower() for server in available_servers)  # 模式匹配
        for pattern in agent.required_mcp_servers
    )


def filter_agents_by_mcp_requirements(
    agents: list[AgentDefinition],
    available_servers: list[str],
) -> list[AgentDefinition]:
    """只返回必需MCP服务器可用的代理
    
    Args:
        agents: 代理定义列表
        available_servers: 可用的MCP服务器列表
    
    Returns:
        list[AgentDefinition]: 过滤后的代理列表
    """
    return [a for a in agents if has_required_mcp_servers(a, available_servers)]