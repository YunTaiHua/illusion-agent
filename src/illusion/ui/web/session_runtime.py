"""
Web 会话运行时模块
=================

本模块定义 Web 端单个会话的运行时状态（SessionRuntime），
供 ws_host（宿主管理）与 ws_web_api（请求分发）共用，
避免两者相互 import 造成循环依赖。

设计要点：
    - 每个会话持有独立的 QueryEngine（经会话级 bundle 隔离）
    - busy / phase / awaiting_input 等标志按会话隔离，是宿主
      判断会话活动状态的唯一权威
    - 行处理任务在各自引擎上并发执行，互不阻塞
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from illusion.engine.query_engine import QueryEngine
from illusion.ui.runtime import RuntimeBundle

# 内存会话运行时上限：超过后淘汰最旧的非 busy 非 active 会话（防内存膨胀）
MAX_MATERIALIZED_SESSIONS = 8


@dataclass
class SessionRuntime:
    """Web 端单个会话的运行时状态。

    每个会话持有独立的引擎（经会话级 bundle 隔离），行处理任务在各自
    引擎上并发执行互不干扰。busy / phase / awaiting_input 等标志按会话
    隔离，是宿主判断会话活动状态的唯一权威。

    Attributes:
        session_id: 会话 ID
        bundle: 会话级 RuntimeBundle（持有本会话独立 QueryEngine）
        busy: 该会话是否正在处理行任务
        active_line_task: 该会话当前的行处理任务
        phase: 会话阶段（idle/thinking/tool_executing/awaiting_input）
        awaiting_input: 是否正在等待用户输入（权限/问答/计划审批模态框）
        rewind_target_idx: rewind 两步选择的中间状态
        current_request_id: 当前 apply_select_command 的请求 ID（响应匹配）
        last_tool_inputs: 每个工具名称的最后输入（富事件发射 / 问答回退）
        emitted_tool_started_ids: 已发 tool_started 事件的工具调用 ID
        created_at: 运行时创建时间戳
        label / summary / turn_count / message_count: 列表展示字段
        context_tokens: 实时上下文占用（列表展示）
        workspace_cwd: 会话所属工作区目录（多目录空间场景，初始为所在
            bundle 的 cwd；用于会话列表按目录分组与 cron 委托匹配）
    """

    session_id: str
    bundle: RuntimeBundle
    workspace_cwd: str = ""
    busy: bool = False
    active_line_task: asyncio.Task[None] | None = None
    phase: str = "idle"
    awaiting_input: bool = False
    rewind_target_idx: int | None = None
    current_request_id: str | None = None
    last_tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    emitted_tool_started_ids: set[str] = field(default_factory=set)
    # 当前行是否为斜杠命令（_process_line 按 commands.lookup 判定）：
    # True 时行内产生的 ErrorEvent 视为命令反馈，改经 toast 通道下发
    current_line_is_command: bool = False
    created_at: float = field(default_factory=time.time)
    label: str = ""
    summary: str = ""
    title: str = ""
    message_count: int = 0
    turn_count: int = 0
    context_tokens: int = 0

    @property
    def engine(self) -> QueryEngine:
        """该会话的独立引擎。"""
        return self.bundle.engine
