"""
统一 Cron 定时任务工具
======================

将创建、列表、删除、开关、手动触发
合并为单一工具，通过 action 参数区分操作。

支持的操作（actions）：
    - status: 查看调度器状态
    - list: 列出所有定时任务
    - add: 创建新的定时任务
    - update: 修改已有任务（启用/禁用、更新计划等）
    - remove: 删除定时任务
    - run: 手动触发执行任务

数据模型：
    - id: 唯一标识符（自动生成）
    - name: 人类可读名称
    - schedule: 5 字段 cron 表达式（本地时间）
    - prompt: 触发时执行的提示词
    - enabled: 是否启用
    - recurring: 是否重复执行
    - delete_after_run: 执行后自动删除

使用示例：
    >>> from illusion.tools.cron_tool import CronTool
    >>> tool = CronTool()
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from illusion.services.cron import (
    delete_cron_job,
    get_cron_job,
    load_cron_jobs,
    set_job_enabled,
    upsert_cron_job,
    validate_cron_expression,
)
from illusion.services.cron_scheduler import (
    execute_job,
    get_scheduler,
    is_scheduler_running,
)
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

# 支持的操作列表
_ACTIONS = ("status", "list", "add", "update", "remove", "run")


class CronToolInput(BaseModel):
    """统一 Cron 工具参数。

    属性：
        action: 操作类型
        name: 任务名称（add 可选；update/remove/run 必填）
        schedule: 5 字段 cron 表达式（add/update）
        prompt: 触发时执行的提示词（add 必填；update 可选）
        recurring: 是否重复执行（add/update）
        delete_after_run: 执行后自动删除（add/update）
        enabled: 启用/禁用状态（update）
        include_disabled: 列表是否包含禁用任务（list）
        timeout_seconds: 手动运行超时秒数（run）
    """

    action: str = Field(
        description=f"Action: {', '.join(_ACTIONS)}",
    )
    # add/update 操作通用参数
    name: str | None = Field(
        default=None,
        description="Job name or ID (optional for add; required for update/remove/run)",
    )
    schedule: str | None = Field(
        default=None,
        description="5-field cron expression in local time (add/update). E.g. '*/5 * * * *', '0 9 * * 1-5'",
    )
    prompt: str | None = Field(
        default=None,
        description=(
            "Prompt to execute in an isolated session when the job fires (required for add; optional for update). "
            "CRITICAL: this prompt should focus ONLY on the task itself (e.g., '检查 git 状态并生成简洁报告'). "
            "Do NOT include delivery instructions in the prompt — do NOT write things like "
            "'use send_to_channel to send to WeChat' or 'send the result to <channel>'. "
            "Delivery is handled automatically by the scheduler via the 'deliver_to' field, "
            "which delivers the subprocess stdout to the target channel. "
            "The subprocess cannot see your current channel sessions and should not attempt delivery itself."
        ),
    )
    recurring: bool | None = Field(
        default=None,
        description="True = fire on every cron match until deleted. False = fire once then auto-delete. Set to true/false to change (add/update).",
    )
    delete_after_run: bool | None = Field(
        default=None,
        description="Delete the job record after execution (add/update). Set to true/false to change.",
    )
    # update 操作参数
    enabled: bool | None = Field(
        default=None,
        description="Enable or disable the job (update)",
    )
    # list 操作参数
    include_disabled: bool = Field(
        default=False,
        description="Include disabled jobs in list output",
    )
    # run 操作参数
    timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=3600,
        description="Timeout in seconds for manual run",
    )
    # add 操作参数：投递目标列表
    deliver_to: list[str] = Field(
        default_factory=list,
        description=(
            "Delivery targets for cron job STDOUT (auto-delivered by scheduler after the job runs). "
            "PREFER this field when the goal involves a channel conversation (e.g. 'send the report "
            "to my WeChat') — the scheduler delivers the result to the channel chat. "
            "Empty list = local only (terminal execution, no channel delivery). "
            "Each item MUST use 'channel:chat_id' format. Multiple items = broadcast to all targets. "
            "This is a SCHEDULER-level field — the scheduler reads subprocess stdout and delivers it "
            "to each channel:chat_id. Do NOT also write delivery instructions inside the 'prompt' field. "
            "To find the chat_id, follow this order: "
            "(1) PREFER calling the list_channel_sessions tool to show active "
            "sessions and pick the right chat_id; "
            "(2) if the tool is unavailable or returns nothing, manually check "
            "~/.illusion/channels/<channel>/sessions/ and STRIP the prefix: "
            "feishu 'u_ou_xxx.json' -> 'ou_xxx' (private), "
            "'g_oc_xxx_ou_xxx.json' -> 'oc_xxx' (group, use the oc_ part); "
            "weixin 'u_<wxid>.json' -> '<wxid>' (strip leading 'u_'); "
            "qq '<openid>.json' -> '<openid>' (filename is the ID). "
            "(3) only if BOTH fail, ask the user for the chat_id. "
            "Examples: ['feishu:oc_xxx'], ['weixin:wxid@im.wechat', 'feishu:ou_xxx'], ['qq:openid']. "
            "If created from a channel session, the origin chat_id is auto-filled."
        ),
    )
    # add/update 操作参数：指定会话执行
    session_id: str | None = Field(
        default=None,
        description=(
            "Session to execute the job in (add/update). Three-state semantics:\n"
            "- omitted (None): default behavior — isolated new session\n"
            "- empty string '': execute in the CURRENT active session (the current conversation)\n"
            "- specific value: execute in that session (find IDs via the list_sessions tool)\n"
            "NOTE: session_id selects which PROJECT conversation the job RUNS in (history context). "
            "It is NOT for channel delivery — if the goal involves a channel conversation, "
            "PREFER the deliver_to field (delivers the job's STDOUT to channel chats). "
            "Both can be combined (run in a project session + deliver result to a channel).\n"
            "When a specific session is set, the running TUI/Web frontend executes the prompt in "
            "that session (busy state and session list update automatically); if no frontend is "
            "running, it falls back to resuming the session in a subprocess."
        ),
    )


class CronTool(BaseTool[CronToolInput]):
    """Manage scheduled cron jobs (status/list/add/update/remove/run).

    Jobs execute in isolated sessions via `illusion -p`, not blocking the current session.
    Uses standard 5-field cron expressions in the user's local timezone.
    """

    name = "cron"
    description = """Manage scheduled cron jobs (status/list/add/update/remove/run). Use for reminders, delayed follow-ups, and recurring tasks. Do not emulate scheduling with exec sleep.

ACTIONS:
- status: Check scheduler status and job counts
- list: List all scheduled jobs (use include_disabled:true to show disabled)
- add: Create a new scheduled job (requires schedule, prompt; optional name, session_id, deliver_to)
- update: Modify an existing job (requires name; can update schedule/prompt/recurring/delete_after_run/enabled/deliver_to/session_id)
- remove: Delete a job (requires name)
- run: Manually trigger a job immediately (requires name)

SESSION_ID (add/update, optional):
- omitted: isolated new session (default)
- empty string '': execute in the CURRENT active session (the current conversation)
- specific value: execute in that session (find IDs via the list_sessions tool)

DELIVER_TO vs SESSION_ID (important):
- deliver_to delivers the job's STDOUT to channel conversations (channel:chat_id).
  When the goal involves a channel conversation (e.g. "send the report to my WeChat"),
  PREFER deliver_to — the scheduler delivers the result automatically.
- session_id selects which PROJECT conversation the job RUNS in (history context).
  It is NOT for channel delivery.
- Both can be combined: run in a project session AND deliver the result to a channel.

SCHEDULE (standard 5-field cron, user's local time):
- minute hour day-of-month month day-of-week
- "*/5 * * * *" = every 5 minutes
- "0 * * * *" = every hour
- "0 9 * * 1-5" = weekdays at 9am local
- "30 14 7 5 *" = May 7th at 2:30pm

ONE-SHOT EXAMPLES:
  "remind me at 2:30pm today" -> schedule: "30 14 <today_dom> <today_month> *", recurring: false
  "run smoke test tomorrow morning" -> schedule: "57 8 <tomorrow_dom> <tomorrow_month> *", recurring: false

AVOID :00 AND :30 when the task allows it:
  "every morning around 9" -> "57 8 * * *" or "3 9 * * *" (not "0 9 * * *")
  "hourly" -> "7 * * * *" (not "0 * * * *")

PROMPT vs DELIVER_TO (CRITICAL — read this before writing the prompt field):
  - The 'prompt' field is ONLY the task itself (e.g., "检查 git 状态并生成简洁报告").
    Do NOT put delivery instructions in the prompt — no "send the result to WeChat",
    no "use send_to_channel to deliver", no chat_id inside the prompt.
  - The 'deliver_to' field is a SCHEDULER-level field. After the subprocess finishes,
    the scheduler reads its stdout and delivers it to each channel:chat_id you set here.
    The LLM running inside the cron subprocess CANNOT see your current channel sessions
    and should NOT try to deliver anything itself.
  - Wrong:   prompt="检查 git 状态...然后使用 send_to_channel 发送到微信,chat_id=xxx"
  - Right:   prompt="检查 git 状态并生成简洁报告"  +  deliver_to=["weixin:xxx"]

DELIVER_TO (add action, optional):
  Format: list of 'channel:chat_id' strings. Empty list = local only.
  Multiple items = broadcast stdout to all targets (best-effort, failures logged).
  To find the chat_id, follow this order:
  (1) PREFER calling the list_channel_sessions tool first to show active
      sessions and pick the right one — if the target channel has only one
      session, use that chat_id directly without asking the user;
  (2) if the tool is unavailable or returns nothing, manually check
      ~/.illusion/channels/<channel>/sessions/ and strip the filename prefix
      (feishu 'u_ou_xxx.json' -> 'ou_xxx', 'g_oc_xxx_ou_xxx.json' -> 'oc_xxx';
       weixin 'u_<wxid>.json' -> '<wxid>'; qq '<openid>.json' -> '<openid>');
  (3) only if BOTH fail, ask the user for the chat_id directly.
  Do NOT ask the user for a chat_id without first trying (1) and (2) —
  most users don't know it.

EXECUTION:
  Jobs run via `illusion -p` in an isolated subprocess, not blocking the current session.
  The scheduler auto-starts when a job is created.
  The subprocess stdout is what gets delivered to deliver_to — keep the prompt
  focused on producing the report/output you want delivered.
  Recurring jobs do not auto-expire; delete them manually when no longer needed.

Returns JSON result for each action."""
    input_model = CronToolInput

    def __init__(
        self,
        *,
        origin_channel: str = "",
        chat_id: str = "",
    ) -> None:
        self._origin_channel = origin_channel
        self._chat_id = chat_id

    async def execute(
        self,
        arguments: CronToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        action = arguments.action.strip().lower()

        if action not in _ACTIONS:
            return ToolResult(
                output=f"Unknown action: {action!r}. Supported: {', '.join(_ACTIONS)}",
                is_error=True,
            )

        handler = {
            "status": self._handle_status,
            "list": self._handle_list,
            "add": self._handle_add,
            "update": self._handle_update,
            "remove": self._handle_remove,
            "run": self._handle_run,
        }[action]

        return await handler(arguments, context)

    # ------------------------------------------------------------------
    # status: 调度器状态
    # ------------------------------------------------------------------

    async def _handle_status(
        self,
        arguments: CronToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """查看调度器运行状态和任务统计。"""
        del arguments, context

        scheduler = get_scheduler()
        status = scheduler.status()

        state = "running" if status["running"] else "stopped"
        lines = [
            f"Scheduler: {state}",
            f"Total jobs: {status['total_jobs']}",
            f"Enabled: {status['enabled_jobs']}",
        ]
        if status.get("pid"):
            lines.append(f"PID: {status['pid']}")

        # 显示 IPC 连接数（引用方主程序数量）
        try:
            from illusion.daemon_ipc import DaemonClient, DaemonType, ping_daemon
            client = DaemonClient(daemon_type=DaemonType.CRON, pid=os.getpid())
            pong = ping_daemon(client, timeout=2.0)
            if pong is not None:
                lines.append(f"Connections: {pong.get('connections', '?')}")
        except (ImportError, OSError, TimeoutError):
            logger.debug("[cron_tool] IPC ping failed", exc_info=True)

        return ToolResult(output="\n".join(lines))

    # ------------------------------------------------------------------
    # list: 列出任务
    # ------------------------------------------------------------------

    async def _handle_list(
        self,
        arguments: CronToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """列出所有定时任务。"""
        del context

        jobs = load_cron_jobs()

        if not arguments.include_disabled:
            jobs = [j for j in jobs if j.get("enabled", True)]

        if not jobs:
            return ToolResult(output="No cron jobs configured.")

        scheduler_state = "running" if is_scheduler_running() else "stopped"
        lines = [f"Scheduler: {scheduler_state}", ""]

        for job in jobs:
            enabled = "+" if job.get("enabled", True) else "-"
            name = job.get("name", job.get("id", "?"))
            schedule = job.get("schedule", "?")
            recurring = "recurring" if job.get("recurring", True) else "one-shot"

            last_run = job.get("last_run", "never")
            if last_run != "never":
                last_run = last_run[:19]
            last_status = job.get("last_status", "")
            status_str = f" ({last_status})" if last_status else ""

            next_run = job.get("next_run", "n/a")
            if next_run != "n/a":
                next_run = next_run[:19]

            errors = job.get("consecutive_errors", 0)
            error_str = f" [errors: {errors}]" if errors > 0 else ""

            lines.append(
                f"[{enabled}] {name}  {schedule} ({recurring})\n"
                f"     prompt: {job.get('prompt', '?')[:60]}\n"
                f"     last: {last_run}{status_str}  next: {next_run}{error_str}"
            )

        return ToolResult(output="\n".join(lines))

    # ------------------------------------------------------------------
    # add: 创建任务
    # ------------------------------------------------------------------

    async def _handle_add(
        self,
        arguments: CronToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """创建新的定时任务，并自动启动调度器。"""
        if not arguments.schedule:
            return ToolResult(
                output="Missing required parameter: schedule\n"
                "Use a 5-field cron expression, e.g. '*/5 * * * *' (every 5 min)",
                is_error=True,
            )

        if not arguments.prompt:
            return ToolResult(
                output="Missing required parameter: prompt\n"
                "prompt is executed in an isolated session when the job fires",
                is_error=True,
            )

        if not validate_cron_expression(arguments.schedule):
            return ToolResult(
                output=(
                    f"Invalid cron expression: {arguments.schedule!r}\n"
                    "Use 5-field format: minute hour day month weekday\n"
                    "Examples: '*/5 * * * *' (every 5 min), '0 9 * * 1-5' (weekdays 9am)"
                ),
                is_error=True,
            )

        # 构建任务字典
        job_data: dict[str, Any] = {
            "schedule": arguments.schedule.strip(),
            "prompt": arguments.prompt,
            "recurring": arguments.recurring
            if arguments.recurring is not None
            else True,
            "delete_after_run": arguments.delete_after_run
            if arguments.delete_after_run is not None
            else False,
            "cwd": str(context.cwd),
            # deliver_to 由 LLM 显式填写；留空=仅本地执行，不回投来源渠道
            "deliver_to": arguments.deliver_to,
            "origin_channel": self._origin_channel,
            "chat_id": self._chat_id,
        }

        if arguments.name:
            job_data["name"] = arguments.name.strip()
        # 指定会话执行（三态）：
        # - 未传（None）→ 不设置（独立新会话，默认行为）
        # - 空串 "" → 当前活跃会话（context.metadata 注入的 session_id）
        # - 具体值 → 指定会话
        if arguments.session_id is not None:
            target_sid = arguments.session_id.strip()
            if target_sid:
                job_data["session_id"] = target_sid
            else:
                current_sid = str(context.metadata.get("session_id") or "").strip()
                if current_sid:
                    job_data["session_id"] = current_sid

        # 创建任务
        job_id = upsert_cron_job(job_data)

        # 自动启动调度器（如果未运行）
        # 通过 spawn cron 守护进程实现，IPC 连接数支持多实例共享
        try:
            from illusion.services.cron_spawn import maybe_spawn_cron_daemon
            maybe_spawn_cron_daemon()
        except (ImportError, OSError, subprocess.SubprocessError):
            # spawn 失败不应阻止任务创建
            logger.warning("[cron_tool] Failed to spawn cron daemon", exc_info=True)

        kind = "recurring" if (arguments.recurring if arguments.recurring is not None else True) else "one-shot"
        name_display = arguments.name or job_id
        return ToolResult(
            output=f"Created {kind} job '{name_display}' [{arguments.schedule}] (id: {job_id})"
        )

    # ------------------------------------------------------------------
    # update: 修改任务
    # ------------------------------------------------------------------

    async def _handle_update(
        self,
        arguments: CronToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """修改已有任务的计划、启用状态等。"""
        if not arguments.name:
            return ToolResult(
                output="Missing required parameter: name (job name or ID to update)",
                is_error=True,
            )

        job = get_cron_job(arguments.name)
        if job is None:
            return ToolResult(
                output=f"Cron job not found: {arguments.name}",
                is_error=True,
            )

        changes: list[str] = []

        if arguments.enabled is not None:
            if set_job_enabled(arguments.name, arguments.enabled):
                state = "enabled" if arguments.enabled else "disabled"
                changes.append(f"{state}")
            else:
                return ToolResult(
                    output=f"Failed to update job: {arguments.name}",
                    is_error=True,
                )

        if arguments.schedule is not None:
            if not validate_cron_expression(arguments.schedule):
                return ToolResult(
                    output=f"Invalid cron expression: {arguments.schedule!r}",
                    is_error=True,
                )
            job["schedule"] = arguments.schedule.strip()
            upsert_cron_job(job)
            changes.append(f"schedule={arguments.schedule.strip()}")

        if arguments.prompt is not None:
            job["prompt"] = arguments.prompt
            upsert_cron_job(job)
            changes.append("prompt updated")

        if arguments.recurring is not None and arguments.recurring != job.get("recurring", True):
            job["recurring"] = arguments.recurring
            upsert_cron_job(job)
            changes.append(f"recurring={arguments.recurring}")

        if (
            arguments.delete_after_run is not None
            and arguments.delete_after_run != job.get("delete_after_run", False)
        ):
            job["delete_after_run"] = arguments.delete_after_run
            upsert_cron_job(job)
            changes.append(f"delete_after_run={arguments.delete_after_run}")

        if arguments.deliver_to is not None:
            new_deliver_to = arguments.deliver_to
            if new_deliver_to != job.get("deliver_to", []):
                job["deliver_to"] = new_deliver_to
                upsert_cron_job(job)
                changes.append(
                    f"deliver_to={new_deliver_to or 'cleared'}"
                )

        # 指定会话执行（三态，与 add 一致）：
        # - 未传（None）→ 不更新（保持原值）
        # - 空串 "" → 更新为当前活跃会话（context.metadata 注入的 session_id）
        # - 具体值 → 更新为指定会话
        if arguments.session_id is not None:
            target_sid = arguments.session_id.strip()
            if target_sid:
                new_session_id = target_sid
            else:
                new_session_id = str(context.metadata.get("session_id") or "").strip()
            if new_session_id != job.get("session_id", ""):
                if new_session_id:
                    job["session_id"] = new_session_id
                else:
                    # 当前上下文无会话 ID（罕见）：视为清除
                    job.pop("session_id", None)
                upsert_cron_job(job)
                changes.append(
                    f"session_id={new_session_id}" if new_session_id else "session_id cleared"
                )

        if changes:
            return ToolResult(
                output=f"Updated cron job: {arguments.name} ({', '.join(changes)})"
            )
        else:
            return ToolResult(
                output="No fields to update (available: schedule, prompt, recurring, enabled, delete_after_run)",
                is_error=True,
            )

    # ------------------------------------------------------------------
    # remove: 删除任务
    # ------------------------------------------------------------------

    async def _handle_remove(
        self,
        arguments: CronToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """删除定时任务。"""
        del context

        if not arguments.name:
            return ToolResult(
                output="Missing required parameter: name (job name or ID to remove)",
                is_error=True,
            )

        if not delete_cron_job(arguments.name):
            return ToolResult(
                output=f"Cron job not found: {arguments.name}",
                is_error=True,
            )

        return ToolResult(output=f"Deleted cron job: {arguments.name}")

    # ------------------------------------------------------------------
    # run: 手动触发
    # ------------------------------------------------------------------

    async def _handle_run(
        self,
        arguments: CronToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """手动触发执行定时任务。"""
        del context

        if not arguments.name:
            return ToolResult(
                output="Missing required parameter: name (job name or ID to run)",
                is_error=True,
            )

        job = get_cron_job(arguments.name)
        if job is None:
            return ToolResult(
                output=f"Cron job not found: {arguments.name}",
                is_error=True,
            )

        prompt = job.get("prompt", "")
        if not prompt:
            return ToolResult(
                output=f"Job has no prompt: {arguments.name}",
                is_error=True,
            )

        # 在独立会话中执行
        entry = await execute_job(job, timeout=arguments.timeout_seconds)

        status = entry.get("status", "unknown")
        returncode = entry.get("returncode", "?")
        stdout = entry.get("stdout", "").strip()
        stderr = entry.get("stderr", "").strip()

        parts = [f"Triggered {arguments.name} ({status}, rc={returncode})"]
        if stdout:
            parts.append(f"Output:\n{stdout}")
        if stderr and status != "success":
            parts.append(f"Error:\n{stderr}")

        return ToolResult(
            output="\n".join(parts),
            is_error=status != "success",
            metadata={"returncode": returncode, "status": status},
        )
