"""
Bash 命令执行工具
================

本模块提供执行 shell 命令并捕获标准输出/错误的功能。

主要组件：
    - BashTool: 执行 bash 命令的工具

使用示例：
    >>> from illusion.tools import BashTool
    >>> tool = BashTool()
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

from illusion.permissions.modes import PermissionMode
from illusion.platforms import get_platform
from illusion.sandbox import SandboxUnavailableError
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult
from illusion.tools.shell_common import CommandExecutor, append_background_timeout_hint
from illusion.utils.shell import _resolve_windows_bash, create_shell_subprocess

logger = logging.getLogger(__name__)


class BashToolInput(BaseModel):
    """Bash 工具参数。

    属性：
        command: 要执行的 shell 命令
        cwd: 可选的工作目录覆盖
        timeout_ms: 超时毫秒数（1000-600000）
        run_in_background: 是否在后台运行
    """

    command: str = Field(description="Shell command to execute")
    cwd: str | None = Field(default=None, description="Working directory override")
    timeout_ms: int = Field(default=120000, ge=1000, le=600000)
    run_in_background: bool = Field(
        default=False,
        description="Set to true to run this command in the background",
    )


# ---------------------------------------------------------------------------
# 提示词生成
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT_MS = 120_000  # 默认超时 2 分钟
_MAX_TIMEOUT_MS = 600_000      # 最大超时 10 分钟
_MAX_OUTPUT_LENGTH = 30_000    # 最大输出长度


def _append_chunk(output_file: Path, chunk: bytes) -> None:
    """将字节块追加写入输出文件（在线程池中执行以避免阻塞事件循环）。"""
    with output_file.open("ab") as handle:
        handle.write(chunk)


def _get_background_usage_note() -> str | None:
    if os.environ.get("ILLUSION_DISABLE_BACKGROUND_TASKS", "").lower() in ("1", "true"):
        return None
    return (
        "You can use the `run_in_background` parameter to run the command in the background. "
        "Only use this if you don't need the result immediately and are OK being notified when "
        "the command completes later. You do not need to check the output right away - you'll be "
        "notified when it finishes. You do not need to use '&' at the end of the command when "
        "using this parameter. "
        "You will be automatically notified when it completes — do NOT sleep or poll "
        "task_output for progress. If you need the full output later, use `task_output` "
        "with the task_id. Continue with other work or respond to the user instead."
    )


def _get_sleep_guidance() -> str | None:
    if os.environ.get("ILLUSION_DISABLE_BACKGROUND_TASKS", "").lower() in ("1", "true"):
        return None
    return (
        "  - Avoid unnecessary `sleep` commands:\n"
        "    - Do not sleep between commands that can run immediately — just run them.\n"
        "    - If your command is long running and you would like to be notified when it finishes — "
        "use `run_in_background`. No sleep needed.\n"
        "    - Do not retry failing commands in a sleep loop — diagnose the root cause.\n"
        "    - If waiting for a background task you started with `run_in_background`, you will be "
        "notified when it completes — do not poll.\n"
        "    - If you must poll an external process, use a check command (e.g. `gh run view`) "
        "rather than sleeping first.\n"
        "    - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user."
    )


def _get_commit_and_pr_instructions() -> str:
    return """\
# Committing changes with git

Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:

You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance. The numbered steps below indicate which commands should be batched in parallel.

Git Safety Protocol:
- NEVER update the git config
- NEVER run destructive git commands (push --force, reset --hard, checkout ., restore ., clean -f, branch -D) unless the user explicitly requests these actions. Taking unauthorized destructive actions is unhelpful and can result in lost work, so it's best to ONLY run these commands when given direct instructions
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- CRITICAL: Always create NEW commits rather than amending, unless the user explicitly requests a git amend. When a pre-commit hook fails, the commit did NOT happen — so --amend would modify the PREVIOUS commit, which may result in destroying work or losing previous changes. Instead, after hook failure, fix the issue, re-stage, and create a NEW commit
- When staging files, prefer adding specific files by name rather than using "git add -A" or "git add .", which can accidentally include sensitive files (.env, credentials) or large binaries
- NEVER commit changes unless the user explicitly asks you to. It is VERY IMPORTANT to only commit when explicitly asked, otherwise the user will feel that you are being too proactive

1. Run the following bash commands in parallel, each using the Bash tool:
  - Run a git status command to see all untracked files. IMPORTANT: Never use the -uall flag as it can cause memory issues on large repos.
  - Run a git diff command to see both staged and unstaged changes that will be committed.
  - Run a git log command to see recent commit messages, so that you can follow this repository's commit message style.
2. Analyze all staged changes (both previously staged and newly added) and draft a commit message:
  - Summarize the nature of the changes (eg. new feature, enhancement to an existing feature, bug fix, refactoring, test, docs, etc.). Ensure the message accurately reflects the changes and their purpose (i.e. "add" means a wholly new feature, "update" means an enhancement to an existing feature, "fix" means a bug fix, etc.).
  - Do not commit files that likely contain secrets (.env, credentials.json, etc). Warn the user if they specifically request to commit those files
  - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what"
  - Ensure it accurately reflects the changes and their purpose
3. Run the following commands in parallel:
   - Add relevant untracked files to the staging area.
   - Create the commit.
   - Run git status after the commit completes to verify success.
   Note: git status depends on the commit completing, so run it sequentially after the commit.
4. If the commit fails due to pre-commit hook: fix the issue and create a NEW commit

Important notes:
- NEVER run additional commands to read or explore code, besides git bash commands
- NEVER use the TodoWrite or Agent tools
- DO NOT push to the remote repository unless the user explicitly asks you to do so
- IMPORTANT: Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.
- IMPORTANT: Do not use --no-edit with git rebase commands, as the --no-edit flag is not a valid option for git rebase.
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
- In order to ensure good formatting, ALWAYS pass the commit message via a HEREDOC, a la this example:
<example>
git commit -m "$(cat <<'EOF'
   Commit message here.
   EOF
   )"
</example>

# Creating pull requests
Use the gh command via the Bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.

IMPORTANT: When the user asks you to create a pull request, follow these steps carefully:

1. Run the following bash commands in parallel using the Bash tool, in order to understand the current state of the branch since it diverged from the main branch:
   - Run a git status command to see all untracked files (never use -uall flag)
   - Run a git diff command to see both staged and unstaged changes that will be committed
   - Check if the current branch tracks a remote branch and is up to date with the remote, so you know if you need to push to the remote
   - Run a git log command and `git diff [base-branch]...HEAD` to understand the full commit history for the current branch (from the time it diverged from the base branch)
2. Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request!!!), and draft a pull request title and summary:
   - Keep the PR title short (under 70 characters)
   - Use the description/body for details, not the title
3. Run the following commands in parallel:
   - Create new branch if needed
   - Push to remote with -u flag if needed
   - Create PR using gh pr create with the format below. Use a HEREDOC to pass the body to ensure correct formatting.
<example>
gh pr create --title "the pr title" --body "$(cat <<'EOF'
## Summary
<1-3 bullet points>

## Test plan
[Bulleted markdown checklist of TODOs for testing the pull request...]
EOF
)"
</example>

Important:
- DO NOT use the TodoWrite or Agent tools
- Return the PR URL when you're done, so the user can see it

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments"""


def _build_bash_description() -> str:
    background_note = _get_background_usage_note()

    tool_preference_items = [
        "File search: Use Glob (NOT find or ls)",
        "Content search: Use Grep (NOT grep or rg)",
        "Read files: Use Read (NOT cat/head/tail)",
        "Edit files: Use Edit (NOT sed/awk)",
        "Write files: Use Write (NOT echo >/cat <<EOF)",
        "Communication: Output text directly (NOT echo/printf)",
    ]

    avoid_commands = "`find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo`"

    multiple_commands_subitems = (
        'If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message. '
        'Example: if you need to run "git status" and "git diff", send a single message with two Bash tool calls in parallel.\n'
        "If the commands depend on each other and must run sequentially, use a single Bash call with '&&' to chain them together.\n"
        "Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.\n"
        "DO NOT use newlines to separate commands (newlines are ok in quoted strings)."
    )

    git_subitems = (
        "Prefer to create a new commit rather than amending an existing commit.\n"
        "Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), "
        "consider whether there is a safer alternative that achieves the same goal. Only use destructive operations "
        "when they are truly the best approach.\n"
        "Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the "
        "user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue."
    )

    sleep_subitems = (
        "Do not sleep between commands that can run immediately — just run them.\n"
        "If your command is long running and you would like to be notified when it finishes — "
        "use `run_in_background`. No sleep needed.\n"
        "Do not retry failing commands in a sleep loop — diagnose the root cause.\n"
        "If waiting for a background task you started with `run_in_background`, you will be notified "
        "when it completes — do not poll.\n"
        "If you must poll an external process, use a check command (e.g. `gh run view`) rather than "
        "sleeping first.\n"
        "If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user."
    )

    instruction_items = [
        "If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.",
        'Always quote file paths that contain spaces with double quotes in your command (e.g., cd "path with spaces/file.txt")',
        "Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.",
        (
            f"You may specify an optional timeout in milliseconds (up to {_MAX_TIMEOUT_MS}ms / {_MAX_TIMEOUT_MS // 60000} minutes). "
            f"By default, your command will timeout after {_DEFAULT_TIMEOUT_MS}ms ({_DEFAULT_TIMEOUT_MS // 60000} minutes)."
        ),
    ]

    if background_note is not None:
        instruction_items.append(background_note)

    instruction_items.extend([
        "When issuing multiple commands:",
        multiple_commands_subitems,
        "For git commands:",
        git_subitems,
        "Avoid unnecessary `sleep` commands:",
        sleep_subitems,
    ])

    # Build tool preference bullets
    preference_bullets = "\n".join(f" - {item}" for item in tool_preference_items)

    # Build instruction bullets
    instruction_lines: list[str] = []
    for item in instruction_items:
        if "\n" in item:
            # Multi-line sub-items get their own indented block
            for line in item.split("\n"):
                instruction_lines.append(f"   - {line}" if not line.startswith(" ") else f" {line}")
        else:
            instruction_lines.append(f" - {item}")

    sections = [
        "Executes a given bash command and returns its output.",
        "",
        (
            "The working directory persists between commands, but shell state does not. "
            "The shell environment is initialized from the user's profile (bash or zsh)."
        ),
        "",
        (
            f"IMPORTANT: Avoid using this tool to run {avoid_commands} commands, unless explicitly "
            "instructed or after you have verified that a dedicated tool cannot accomplish your task. "
            "Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:"
        ),
        "",
        preference_bullets,
        (
            "While the Bash tool can do similar things, it's better to use the built-in tools as they "
            "provide a better user experience and make it easier to review tool calls and give permission."
        ),
        "",
        "# Instructions",
    ]

    sections.extend(instruction_lines)

    sections.append("")
    sections.append(_get_commit_and_pr_instructions())

    return "\n".join(sections)


class BashTool(BaseTool[BashToolInput]):
    """执行 shell 命令并捕获标准输出/错误。

    用于执行终端操作，如 git、npm、docker 等命令。
    """

    name = "bash"
    description = _build_bash_description()
    input_model = BashToolInput

    async def execute(self, arguments: BashToolInput, context: ToolExecutionContext) -> ToolResult:
        # 检查 Windows 平台上的 bash 可用性
        if get_platform() == "windows":
            bash_path = _resolve_windows_bash()
            if not bash_path:
                return ToolResult(
                    output=(
                        "Bash is not available on this Windows machine. "
                        "Install Git for Windows or set ILLUSION_AGENT_GIT_BASH_PATH, "
                        "or use the powershell tool for command execution."
                    ),
                    is_error=True,
                )

        # 解析工作目录
        cwd = Path(arguments.cwd).expanduser() if arguments.cwd else context.cwd
        # YOLO 模式：绕过沙箱完全运行。权限检查器在 YOLO 模式下已放行，
        # 此处关闭 OS 级沙箱。dangerouslyDisableSandbox 已移除，不再由模型指定。
        disable_sandbox = False
        checker = (context.metadata or {}).get("permission_checker")
        if checker is not None and getattr(checker, "current_mode", None) == PermissionMode.YOLO:
            disable_sandbox = True
        try:
            # 创建 shell 子进程
            process = await create_shell_subprocess(
                arguments.command,
                cwd=cwd,
                disable_sandbox=disable_sandbox,
                stdin=asyncio.subprocess.DEVNULL,  # 防止 Windows 上的句柄继承死锁
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                new_process_group=True,
            )
        except SandboxUnavailableError as exc:
            return ToolResult(output=str(exc), is_error=True)

        # 后台运行模式：注册到 BackgroundTaskManager，返回 task_id
        if arguments.run_in_background:
            from illusion.tasks.manager import _task_id, get_task_manager

            manager = get_task_manager()
            task_id = _task_id("local_bash")

            # 创建 task record（不通过 create_shell_task，因为进程已启动）
            import time as _time

            from illusion.config.paths import get_tasks_dir
            from illusion.tasks.types import TaskRecord

            record = TaskRecord(
                id=task_id,
                type="local_bash",
                status="running",
                description=arguments.command[:80] if arguments.command else "bash background",
                cwd=str(cwd.resolve()),
                output_file=get_tasks_dir() / f"{task_id}.log",
                command=arguments.command,
                created_at=_time.time(),
                started_at=_time.time(),
            )
            await asyncio.to_thread(record.output_file.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(record.output_file.write_text, "", encoding="utf-8")
            # stamp 归属会话 ID（Web 多会话模式下按上下文路由完成通知）
            from illusion.tasks.manager import current_task_session_owner
            owner_sid = current_task_session_owner()
            if owner_sid:
                record.metadata["owner_session_id"] = owner_sid
            manager._tasks[task_id] = record
            manager._output_locks[task_id] = asyncio.Lock()
            # 注册 process，使 stop_task 能找到并终止（否则 stop 直接返回，无法打断）
            manager._processes[task_id] = process
            # 注册到 bg_agent_tracker，使完成时能自动通知 LLM
            tracker = context.metadata.get("bg_agent_tracker") if context.metadata else None
            if tracker is not None:
                tracker.register(task_id)

            async def _background_wait() -> None:
                """后台等待进程完成，累积输出到 task output_file。"""
                try:
                    assert process.stdout is not None
                    assert process.stderr is not None
                    while True:
                        chunk = await process.stdout.read(4096)
                        if not chunk:
                            break
                        # 锁保护共享输出文件，I/O 委托给线程池避免阻塞事件循环
                        async with manager._output_locks[task_id]:
                            await asyncio.to_thread(_append_chunk, record.output_file, chunk)
                    # stderr 也累积到 output_file（与 manager._copy_output 一致）
                    stderr_data = await process.stderr.read()
                    if stderr_data:
                        async with manager._output_locks[task_id]:
                            await asyncio.to_thread(_append_chunk, record.output_file, stderr_data)
                    return_code = await process.wait()
                    record.return_code = return_code
                    # 已被 stop_task 标记为 killed 时不覆盖（与 manager._watch_process 一致）
                    if record.status != "killed":
                        record.status = "completed" if return_code == 0 else "failed"
                    record.ended_at = _time.time()
                    # 通知 on_task_complete 回调
                    if manager.on_task_complete is not None:
                        try:
                            manager.on_task_complete(task_id, record)
                        except (OSError, RuntimeError, ValueError, KeyError):
                            logger.warning("[bash_tool] on_task_complete callback failed for %s", task_id, exc_info=True)
                    # 清理注册（与 manager._watch_process 一致）
                    manager._processes.pop(task_id, None)
                    manager._waiters.pop(task_id, None)
                except asyncio.CancelledError:
                    # task_stop 取消时，终止整个进程树
                    from illusion.utils.shell import terminate_process_tree
                    await terminate_process_tree(process)
                    record.status = "killed"
                    record.ended_at = _time.time()
                    manager._processes.pop(task_id, None)
                    manager._waiters.pop(task_id, None)
                    # 递减 tracker 计数（被停止的任务不注入通知）
                    if tracker is not None:
                        tracker.discard(task_id)
                    raise
                except (OSError, RuntimeError):
                    logger.exception("[bash_tool] Background task %s failed", task_id)
                    record.status = "failed"
                    record.ended_at = _time.time()
                    manager._processes.pop(task_id, None)
                    manager._waiters.pop(task_id, None)
                finally:
                    # 关闭 subprocess transport，避免 Windows 上管道句柄泄漏
                    # （stop_task 杀进程后 process.wait() 可能不返回，transport
                    #   不会自动关闭，_background_wait 被 cancel 时泄漏）
                    transport = getattr(process, "_transport", None)
                    if transport is not None:
                        with contextlib.suppress(Exception):
                            transport.close()

            bg_async_task = asyncio.create_task(_background_wait(), name=f"bash-bg-{task_id}")
            record.async_task = bg_async_task
            # 注册 watcher，使 stop_task 杀进程后能等待 _background_wait 完成
            manager._waiters[task_id] = bg_async_task

            return ToolResult(
                output=(
                    f"Command launched in background (task_id={task_id}).\n"
                    "You will be automatically notified when it completes — do NOT sleep or "
                    "poll task_output for progress. If you need the full output later, use "
                    "`task_output` with the task_id. Continue with other work or respond to "
                    "the user instead."
                ),
                is_error=False,
            )

        # 执行命令并归一化结果
        timeout_seconds = arguments.timeout_ms // 1000
        result = await CommandExecutor.run_and_normalize(
            process,
            timeout=timeout_seconds,
        )
        return ToolResult(
            output=append_background_timeout_hint(
                result.output,
                timed_out=result.metadata.get("timed_out") is True,
                timeout_ms=arguments.timeout_ms,
            ),
            is_error=result.is_error,
            metadata=dict(result.metadata),
        )
