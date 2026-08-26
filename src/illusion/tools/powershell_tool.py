"""
PowerShell 命令执行工具
======================

本模块提供执行 PowerShell 命令并捕获标准输出/错误的功能。

主要组件：
    - PowerShellTool: 执行 PowerShell 命令的工具

使用示例：
    >>> from illusion.tools import PowerShellTool
    >>> tool = PowerShellTool()
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from illusion.permissions.modes import PermissionMode
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult
from illusion.tools.shell_common import (
    MAX_OUTPUT_LENGTH,
    CommandExecutor,
    append_background_timeout_hint,
)
from illusion.utils.shell import create_argv_subprocess

logger = logging.getLogger(__name__)

# PowerShell 版本类型
PowerShellEdition = Literal["core", "desktop"]


def _append_chunk(output_file: Path, chunk: bytes) -> None:
    """将字节块追加写入输出文件（在线程池中执行以避免阻塞事件循环）。"""
    with output_file.open("ab") as handle:
        handle.write(chunk)


# ---------------------------------------------------------------------------
# PowerShell 检测
# ---------------------------------------------------------------------------

def _find_powershell() -> str | None:
    """在系统上查找 PowerShell。优先 pwsh (Core 7+) 而非 powershell (5.1)。"""
    pwsh = shutil.which("pwsh")
    if pwsh:
        return pwsh
    ps = shutil.which("powershell")
    if ps:
        return ps
    return None


def _get_powershell_edition(powershell_path: str | None) -> PowerShellEdition | None:
    """根据可执行文件名确定 PowerShell 版本。

    'pwsh' → Core (7+), 'powershell' → Desktop (5.1)。
    """
    if not powershell_path:
        return None
    base = powershell_path.replace("/", "\\").split("\\")[-1].lower()
    base = base.replace(".exe", "")
    if base == "pwsh":
        return "core"
    return "desktop"


# ---------------------------------------------------------------------------
# 提示词生成
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT_MS = 120_000  # 默认超时 2 分钟
_MAX_TIMEOUT_MS = 600_000      # 最大超时 10 分钟


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
        "  - Avoid unnecessary `Start-Sleep` commands:\n"
        "    - Do not sleep between commands that can run immediately — just run them.\n"
        "    - If your command is long running and you would like to be notified when it "
        "finishes — simply run your command using `run_in_background`. There is no need to "
        "sleep in this case.\n"
        "    - Do not retry failing commands in a sleep loop — diagnose the root cause or "
        "consider an alternative approach.\n"
        "    - If waiting for a background task you started with `run_in_background`, you will "
        "be notified when it completes — do not poll.\n"
        "    - If you must poll an external process, use a check command rather than sleeping first.\n"
        "    - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user."
    )


def _get_edition_section(edition: PowerShellEdition | None) -> str:
    """Version-specific syntax guidance.

    The model's training data covers both editions but it can't tell which one
    it's targeting, so it either emits pwsh-7 syntax on 5.1 (parser error → exit 1)
    or needlessly avoids && on 7.
    """
    if edition == "desktop":
        return (
            "PowerShell edition: Windows PowerShell 5.1 (powershell.exe)\n"
            "   - Pipeline chain operators `&&` and `||` are NOT available — they cause a parser "
            "error. To run B only if A succeeds: `A; if ($?) { B }`. To chain unconditionally: `A; B`.\n"
            "   - Ternary (`?:`), null-coalescing (`??`), and null-conditional (`?.`) operators are "
            "NOT available. Use `if/else` and explicit `$null -eq` checks instead.\n"
            "   - Avoid `2>&1` on native executables. In 5.1, redirecting a native command's stderr "
            "inside PowerShell wraps each line in an ErrorRecord (NativeCommandError) and sets `$?` to "
            "`$false` even when the exe returned exit code 0. stderr is already captured for you — "
            "don't redirect it.\n"
            "   - Default file encoding is UTF-16 LE (with BOM). When writing files other tools will "
            "read, pass `-Encoding utf8` to `Out-File`/`Set-Content`.\n"
            "   - `ConvertFrom-Json` returns a PSCustomObject, not a hashtable. `-AsHashtable` is not available."
        )
    if edition == "core":
        return (
            "PowerShell edition: PowerShell 7+ (pwsh)\n"
            "   - Pipeline chain operators `&&` and `||` ARE available and work like bash. Prefer "
            "`cmd1 && cmd2` over `cmd1; cmd2` when cmd2 should only run if cmd1 succeeds.\n"
            "   - Ternary (`$cond ? $a : $b`), null-coalescing (`??`), and null-conditional (`?.`) "
            "operators are available.\n"
            "   - Default file encoding is UTF-8 without BOM."
        )
    # Detection not yet resolved or PS not installed — give conservative 5.1-safe guidance.
    return (
        "PowerShell edition: unknown — assume Windows PowerShell 5.1 for compatibility\n"
        "   - Do NOT use `&&`, `||`, ternary `?:`, null-coalescing `??`, or null-conditional `?:`. "
        "These are PowerShell 7+ only and parser-error on 5.1.\n"
        "   - To chain commands conditionally: `A; if ($?) { B }`. Unconditionally: `A; B`."
    )


def _build_powershell_description() -> str:
    ps_path = _find_powershell()
    edition = _get_powershell_edition(ps_path)
    background_note = _get_background_usage_note()
    sleep_guidance = _get_sleep_guidance()

    sections = [
        (
            "Executes a given PowerShell command with optional timeout. Each invocation starts a "
            "fresh PowerShell process; shell state (variables, functions) does not persist between calls."
        ),
        "",
        (
            "IMPORTANT: This tool is for terminal operations via PowerShell: git, npm, docker, and PS "
            "cmdlets. DO NOT use it for file operations (reading, writing, editing, searching, finding files) "
            "- use the specialized tools for this instead."
        ),
        "",
        _get_edition_section(edition),
        "",
        "Before executing the command, please follow these steps:",
        "",
        "1. Directory Verification:",
        (
            "   - If the command will create new directories or files, first use `Get-ChildItem` (or `ls`) "
            "to verify the parent directory exists and is the correct location"
        ),
        "",
        "2. Command Execution:",
        "   - Always quote file paths that contain spaces with double quotes",
        "   - Capture the output of the command.",
        "",
        "PowerShell Syntax Notes:",
        '   - Variables use $ prefix: $myVar = "value"',
        "   - Escape character is backtick (`), not backslash",
        "   - Use Verb-Noun cmdlet naming: Get-ChildItem, Set-Location, New-Item, Remove-Item",
        "   - Common aliases: ls (Get-ChildItem), cd (Set-Location), cat (Get-Content), rm (Remove-Item)",
        "   - Pipe operator | works similarly to bash but passes objects, not text",
        "   - Use Select-Object, Where-Object, ForEach-Object for filtering and transformation",
        '   - String interpolation: "Hello $name" or "Hello $($obj.Property)"',
        (
            "   - Registry access uses PSDrive prefixes: `HKLM:\\SOFTWARE\\...`, `HKCU:\\...` — NOT raw "
            "`HKEY_LOCAL_MACHINE\\...`"
        ),
        (
            '   - Environment variables: read with `$env:NAME`, set with `$env:NAME = "value"` '
            "(NOT `Set-Variable` or bash `export`)"
        ),
        '   - Call native exe with spaces in path via call operator: `& "C:\\Program Files\\App\\app.exe" arg1 arg2`',
        "",
        "Interactive and blocking commands (will hang — this tool runs with -NonInteractive):",
        "   - NEVER use `Read-Host`, `Get-Credential`, `Out-GridView`, `$Host.UI.PromptForChoice`, or `pause`",
        (
            "   - Destructive cmdlets (`Remove-Item`, `Stop-Process`, `Clear-Content`, etc.) may prompt for "
            "confirmation. Add `-Confirm:$false` when you intend the action to proceed. Use `-Force` for "
            "read-only/hidden items."
        ),
        "   - Never use `git rebase -i`, `git add -i`, or other commands that open an interactive editor",
        "",
        "Passing multiline strings (commit messages, file content) to native executables:",
        (
            "   - Use a single-quoted here-string so PowerShell does not expand `$` or backticks inside. "
            "The closing `'@` MUST be at column 0 (no leading whitespace) on its own line — indenting it "
            "is a parse error:"
        ),
        "<example>",
        "git commit -m @'",
        "Commit message here.",
        "Second line with $literal dollar signs.",
        "'@",
        "</example>",
        (
            "   - Use `@'...'@` (single-quoted, literal) not `@\"...\"@` (double-quoted, interpolated) "
            "unless you need variable expansion"
        ),
        (
            "   - For arguments containing `-`, `@`, or other characters PowerShell parses as operators, "
            "use the stop-parsing token: `git log --% --format=%H`"
        ),
        "",
        "Usage notes:",
        "  - The command argument is required.",
        (
            f"  - You can specify an optional timeout in milliseconds (up to {_MAX_TIMEOUT_MS}ms / "
            f"{_MAX_TIMEOUT_MS // 60000} minutes). If not specified, commands will timeout after "
            f"{_DEFAULT_TIMEOUT_MS}ms ({_DEFAULT_TIMEOUT_MS // 60000} minutes)."
        ),
        "  - It is very helpful if you write a clear, concise description of what this command does.",
        (
            f"  - If the output exceeds {MAX_OUTPUT_LENGTH} characters, output will be truncated before "
            "being returned to you."
        ),
    ]

    if background_note is not None:
        sections.append(background_note)

    sections.extend([
        "  - Avoid using PowerShell to run commands that have dedicated tools, unless explicitly instructed:",
        "    - File search: Use Glob (NOT Get-ChildItem -Recurse)",
        "    - Content search: Use Grep (NOT Select-String)",
        "    - Read files: Use Read (NOT Get-Content)",
        "    - Edit files: Use Edit",
        "    - Write files: Use Write (NOT Set-Content/Out-File)",
        "    - Communication: Output text directly (NOT Write-Output/Write-Host)",
        "  - When issuing multiple commands:",
        (
            "    - If the commands are independent and can run in parallel, make multiple PowerShell tool "
            "calls in a single message."
        ),
        (
            "    - If the commands depend on each other and must run sequentially, chain them in a single "
            "PowerShell call (see edition-specific chaining syntax above)."
        ),
        "    - Use `;` only when you need to run commands sequentially but don't care if earlier commands fail.",
        "    - DO NOT use newlines to separate commands (newlines are ok in quoted strings and here-strings)",
        (
            "  - Do NOT prefix commands with `cd` or `Set-Location` -- the working directory is already set "
            "to the correct project directory automatically."
        ),
    ])

    if sleep_guidance is not None:
        sections.append(sleep_guidance)

    sections.extend([
        "  - For git commands:",
        "    - Prefer to create a new commit rather than amending an existing commit.",
        (
            "    - Before running destructive operations (e.g., git reset --hard, git push --force, "
            "git checkout --), consider whether there is a safer alternative that achieves the same goal. "
            "Only use destructive operations when they are truly the best approach."
        ),
        (
            "    - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) "
            "unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue."
        ),
    ])

    return "\n".join(sections)


class PowerShellToolInput(BaseModel):
    """Arguments for the powershell tool."""

    command: str = Field(description="PowerShell command to execute")
    cwd: str | None = Field(default=None, description="Working directory override")
    timeout_ms: int = Field(default=120000, ge=1000, le=600000)
    run_in_background: bool = Field(
        default=False,
        description="Set to true to run this command in the background",
    )


class PowerShellTool(BaseTool[PowerShellToolInput]):
    """执行 PowerShell 命令并捕获标准输出/错误。

    用于在 Windows 平台上执行 PowerShell 命令。
    """

    name = "powershell"
    description = _build_powershell_description()
    input_model = PowerShellToolInput

    async def execute(self, arguments: PowerShellToolInput, context: ToolExecutionContext) -> ToolResult:
        # 查找 PowerShell
        powershell = _find_powershell()
        if powershell is None:
            return ToolResult(output="PowerShell is not available on this machine", is_error=True)

        # 解析工作目录
        cwd = Path(arguments.cwd).expanduser() if arguments.cwd else context.cwd

        # 确定版本特定的标志
        edition = _get_powershell_edition(powershell)
        if edition == "core":
            # pwsh 7+ 支持 -NoProfile -NonInteractive -Command
            args = ["-NoProfile", "-NonInteractive", "-Command", arguments.command]
        else:
            # Windows PowerShell 5.1
            args = ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", arguments.command]

        # YOLO 模式：绕过沙箱完全运行。即使未显式禁用沙箱，也强制以无沙箱方式
        # 执行（与 bash_tool 一致）。沙箱命中与否由 create_argv_subprocess 结合
        # SandboxManager 判定，确保与 bash 相同的沙箱覆盖。
        disable_sandbox = False
        checker = (context.metadata or {}).get("permission_checker")
        if checker is not None and getattr(checker, "current_mode", None) == PermissionMode.YOLO:
            disable_sandbox = True

        # 创建子进程（沙箱感知，与 bash 一致）
        argv = [powershell, *args]
        process = await create_argv_subprocess(
            argv,
            cwd=str(cwd.resolve()),
            command=arguments.command,
            disable_sandbox=disable_sandbox,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            new_process_group=True,
        )

        # 后台运行模式：注册到 BackgroundTaskManager，返回 task_id
        if arguments.run_in_background:
            import time as _time

            from illusion.config.paths import get_tasks_dir
            from illusion.tasks.manager import _task_id, get_task_manager
            from illusion.tasks.types import TaskRecord

            manager = get_task_manager()
            task_id = _task_id("local_bash")  # powershell 也归入 local_bash 类型

            record = TaskRecord(
                id=task_id,
                type="local_bash",
                status="running",
                description=arguments.command[:80] if arguments.command else "powershell background",
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
                            logger.warning("[powershell_tool] on_task_complete callback failed for %s", task_id, exc_info=True)
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
                    logger.exception("[powershell_tool] Background task %s failed", task_id)
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

            bg_async_task = asyncio.create_task(_background_wait(), name=f"ps-bg-{task_id}")
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
