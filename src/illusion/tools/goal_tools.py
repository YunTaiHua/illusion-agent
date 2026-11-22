"""
Goal 模型工具模块
=================

三个 goal 模型工具；输出统一为 GOAL_OUTPUT 紧凑 JSON。

`update_goal(action="complete")` 经对抗性验证：harness 同步触发单个
对抗性验证子代理（illusion 自己的 verification 代理），PASS 才落
complete；FAIL/PARTIAL 将缺陷回灌给实现者继续修复。

主要组件：
    - GetGoalTool: 读取当前 goal
    - CreateGoalTool: 创建 goal（要求 human 权威）
    - UpdateGoalTool: 编辑/暂停/恢复/完成/受阻（终态经对抗性验证）
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from illusion.goal.manager import GoalManager
from illusion.goal.types import (
    BLOCK_CODE_MODEL_REPORTED,
    GoalError,
    PendingWrapup,
)
from illusion.goal.verifier import run_goal_verification
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


def _goal_manager(context: ToolExecutionContext) -> GoalManager | None:
    manager = context.metadata.get("goal_manager")
    return manager if isinstance(manager, GoalManager) else None


def _error(exc: GoalError) -> ToolResult:
    return ToolResult(output=f"{exc.message} (code: {exc.code})", is_error=True)


def _output(manager: GoalManager) -> ToolResult:
    return ToolResult(output=json.dumps(manager.goal_value(), ensure_ascii=False))


def _has_text(value: str | None) -> bool:
    return value is not None and value != ""


def _has_round_cap(value: int | None) -> bool:
    return value is not None and value != 0


class GetGoalToolInput(BaseModel):
    """get_goal 输入（无参数）。"""


class GetGoalTool(BaseTool[GetGoalToolInput]):
    """读取当前会话 goal。"""

    name = "get_goal"
    description = (
        "Read the current same-session goal, including its exact id/revision, objective, phase, "
        "completed continuation rounds, round limit, blocker reason when present, and whether "
        "another continuation is armed. Call this before updating a goal."
    )
    input_model = GetGoalToolInput

    def is_read_only(self, arguments: GetGoalToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: GetGoalToolInput, context: ToolExecutionContext) -> ToolResult:
        manager = _goal_manager(context)
        if manager is None:
            return ToolResult(output="Goal tools are not available in this context.", is_error=True)
        return _output(manager)


class CreateGoalToolInput(BaseModel):
    """create_goal 输入。"""

    objective: str = Field(
        description="The concrete completion objective inferred from the direct human request."
    )
    max_goal_rounds: int | None = Field(
        default=None,
        description="Optional positive safe-integer limit on automatic continuation rounds.",
    )


class CreateGoalTool(BaseTool[CreateGoalToolInput]):
    """创建 goal。"""

    name = "create_goal"
    description = (
        "Create one persisted same-session completion goal when the current direct human request "
        "is a long-running objective that should continue across autonomous goal rounds. You may "
        "infer that intent without requiring the user to say \"create a goal\". Do not use this "
        "for trivial single-turn work. Execution rejects non-human and subagent authority."
    )
    input_model = CreateGoalToolInput

    async def execute(self, arguments: CreateGoalToolInput, context: ToolExecutionContext) -> ToolResult:
        manager = _goal_manager(context)
        if manager is None:
            return ToolResult(output="Goal tools are not available in this context.", is_error=True)
        try:
            manager.create(
                objective=arguments.objective,
                max_goal_rounds=arguments.max_goal_rounds,
            )
        except GoalError as exc:
            return _error(exc)
        return _output(manager)


class UpdateGoalToolInput(BaseModel):
    """update_goal 输入。"""

    goal_id: str = Field(description="Exact id returned by get_goal.")
    revision: int = Field(description="Exact positive revision returned by get_goal.")
    action: Literal["edit", "pause", "resume", "complete", "blocked"] = Field(
        description="edit | pause | resume | complete | blocked"
    )
    objective: str | None = Field(
        default=None,
        description="Replacement objective; valid only with action edit.",
    )
    max_goal_rounds: int | None = Field(
        default=None,
        description="Replacement cap; valid only with action edit.",
    )
    blocked_reason: str | None = Field(
        default=None,
        description="Concrete blocking condition; required only with action blocked.",
    )


class UpdateGoalTool(BaseTool[UpdateGoalToolInput]):
    """更新 goal（complete 经对抗性验证）。"""

    name = "update_goal"
    description = (
        "Update the exact current goal revision. edit, pause, and resume require a direct "
        "top-level human request. During an automatic continuation of the current goal, complete "
        "and blocked are also allowed. blocked is rejected before the configured minimum round "
        "count; the model remains responsible for judging that the same condition persisted "
        "across those rounds and must explain it in blocked_reason."
    )
    input_model = UpdateGoalToolInput

    async def execute(self, arguments: UpdateGoalToolInput, context: ToolExecutionContext) -> ToolResult:
        manager = _goal_manager(context)
        if manager is None:
            return ToolResult(output="Goal tools are not available in this context.", is_error=True)

        try:
            return await self._execute_update(arguments, manager, context)
        except GoalError as exc:
            return _error(exc)

    async def _execute_update(
        self,
        arguments: UpdateGoalToolInput,
        manager: GoalManager,
        context: ToolExecutionContext,
    ) -> ToolResult:
        # goalRef 校验：goal_id 非空、revision 为正整数
        if (
            not arguments.goal_id
            or arguments.goal_id != arguments.goal_id.strip()
            or not isinstance(arguments.revision, int)
            or isinstance(arguments.revision, bool)
            or arguments.revision < 1
        ):
            raise GoalError(
                "goal_id must be non-empty and revision must be a positive safe integer"
            )

        if arguments.action == "edit":
            manager.require_direct_human()
            if _has_text(arguments.blocked_reason):
                raise GoalError("blocked_reason is valid only with action blocked")
            manager.edit(
                arguments.goal_id,
                arguments.revision,
                objective=arguments.objective,
                max_goal_rounds=arguments.max_goal_rounds,
            )
            return _output(manager)

        if arguments.action in ("pause", "resume"):
            manager.require_direct_human()
            if (
                _has_text(arguments.objective)
                or _has_round_cap(arguments.max_goal_rounds)
                or _has_text(arguments.blocked_reason)
            ):
                raise GoalError(
                    "objective and max_goal_rounds are valid only with action edit; "
                    "blocked_reason is valid only with action blocked"
                )
            if arguments.action == "pause":
                manager.pause(arguments.goal_id, arguments.revision)
            else:
                manager.resume(arguments.goal_id, arguments.revision)
            return _output(manager)

        # complete / blocked：human 或当前 goal round
        manager.require_completion_authority()
        if _has_text(arguments.objective) or _has_round_cap(arguments.max_goal_rounds):
            raise GoalError("objective and max_goal_rounds are valid only with action edit")
        if arguments.action == "complete" and _has_text(arguments.blocked_reason):
            raise GoalError("blocked_reason is valid only with action blocked")
        if arguments.action == "blocked" and (
            arguments.blocked_reason is None or arguments.blocked_reason.strip() == ""
        ):
            raise GoalError("blocked_reason is required with action blocked")
        if arguments.action == "blocked":
            manager.check_blocked_threshold()

        if arguments.action == "blocked":
            view = manager.block(
                arguments.goal_id,
                arguments.revision,
                code=BLOCK_CODE_MODEL_REPORTED,
                message=arguments.blocked_reason or "",
            )
            if manager.current_source == "goal":
                # goal round 内的终态更新延迟注入 wrap-up 收尾消息
                manager.set_pending_wrapup(
                    PendingWrapup(
                        kind="blocked",
                        objective=view.snapshot.objective,
                        blocked_reason=arguments.blocked_reason,
                    )
                )
            return _output(manager)

        # ---- complete：先对抗性验证，PASS 才落 complete ----
        engine = context.metadata.get("query_engine")
        outcome = await run_goal_verification(
            manager,
            engine,
            on_progress=context.on_progress,
        )
        if outcome.auto_paused:
            # 验证 cap / 停滞：goal 已自动 blocked，回灌缺陷 + 阻塞说明
            return ToolResult(
                output=(
                    outcome.gaps_block
                    + f"\n{outcome.summary}\n\n"
                    + json.dumps(manager.goal_value(), ensure_ascii=False)
                ),
                is_error=True,
            )
        if not outcome.achieved:
            # 拒绝：goal 保持 active，缺陷优先回灌
            return ToolResult(
                output=(
                    outcome.gaps_block
                    + f"{outcome.summary}\n\n"
                    + json.dumps(manager.goal_value(), ensure_ascii=False)
                ),
                is_error=True,
            )
        view = manager.complete(arguments.goal_id, arguments.revision)
        if manager.current_source == "goal":
            manager.set_pending_wrapup(
                PendingWrapup(kind="complete", objective=view.snapshot.objective)
            )
        return ToolResult(
            output=json.dumps(manager.goal_value(), ensure_ascii=False) + f"\n{outcome.summary}"
        )


def apply_goal_toggle(engine: Any, registry: Any, manager: Any, enabled: bool) -> None:
    """把 goal 工具注册/注销 + goal_manager 元数据落到一个引擎。

    供 goal 功能设置热切换共用（REST /api/settings/goal → 各活跃 Web
    host）。engine 需持有可变属性 ``_goal_manager`` 与 ``_tool_metadata``
    （QueryEngine 均满足）。与 browser_tools.apply_browser_toggle 同构。

    Args:
        engine: 目标查询引擎（其 _goal_manager / _tool_metadata 就地更新）
        registry: 工具注册表（可能是每会话独立实例）
        manager: GoalManager（enabled=True 时必传；False 时忽略）
        enabled: 启用（注册工具并注入元数据）或禁用（注销并移除）
    """
    goal_tools = (GetGoalTool(), CreateGoalTool(), UpdateGoalTool())
    if enabled:
        engine._goal_manager = manager
        engine._tool_metadata["goal_manager"] = manager
        existing = {t.name for t in registry.list_tools()}
        for tool in goal_tools:
            if tool.name not in existing:
                registry.register(tool)
    else:
        engine._goal_manager = None
        engine._tool_metadata.pop("goal_manager", None)
        for tool in goal_tools:
            registry.unregister(tool.name)
