"""
Protocol 协议模块
==============

本模块定义 React 终端前后端通信的结构化协议模型。

主要功能：
    - 前端请求模型（FrontendRequest）
    - 后端事件模型（BackendEvent）
    - 转录项模型（TranscriptItem）
    - 任务快照模型（TaskSnapshot）

类说明：
    - FrontendRequest: 前端请求模型
    - BackendEvent: 后端事件模型
    - TranscriptItem: 转录项模型
    - TaskSnapshot: 任务快照模型

使用示例：
    >>> from illusion.ui.protocol import FrontendRequest, BackendEvent, TranscriptItem
    >>> 
    >>> # 创建前端请求
    >>> request = FrontendRequest(type="submit_line", line="帮我写一个程序")
    >>> 
    >>> # 创建后端事件
    >>> event = BackendEvent.ready(state, tasks, commands)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from illusion.mcp.types import McpConnectionStatus
from illusion.state.app_state import AppState
from illusion.tasks.types import TaskRecord, to_task_display_status


class FrontendRequest(BaseModel):
    """前端请求模型。

    表示从 React 前端发送到 Python 后端的请求。
    web_* 类型为 Web 前端专属通道（A/B 通道），与 terminal 共用的
    submit_line/apply_select_command 等类型隔离，避免 web 端操作
    与 terminal 端命令流程相互干扰。

    Attributes:
        type: 请求类型
        line: 提交的行内容
        command: 命令名称（web_query 用）
        value: 命令值
        request_id: 请求 ID（web_query 用）
        allowed: 是否允许
        session_allow: 是否允许本会话内该工具（不持久化）
        tool_name: 工具名称
        answer: 用户答案
        session_id: 会话 ID（web_restore_session 用）
        session_ids: 会话 ID 列表（web_delete_sessions 用）
        delete_all: 是否删除全部会话（web_delete_sessions 用）
        setting_key: 设置键名（web_set_setting 用）
        setting_value: 设置值（web_set_setting 用）
        limit: 拉取数量上限（web_request_sessions 用）
        offset: 拉取偏移量（web_request_sessions 用）
        cwd: 工作区目录（web_new_session/web_restore_session/web_delete_sessions/
            web_request_resources/web_request_file_tree/web_request_git_status/
            web_read_file 指定目标工作区）
        path: 目录路径（web_add_workspace/web_remove_workspace 用；
            web_request_file_tree/web_read_file 为工作区内的相对路径）
    """

    type: Literal[
        "submit_line",
        "stop",
        "permission_response",
        "question_response",
        "list_sessions",
        "select_command",
        "apply_select_command",
        "shutdown",
        # === Web 前端专属通道（web_* 命名空间）===
        "web_new_session",
        "web_restore_session",
        "web_delete_sessions",
        "web_set_setting",
        "web_request_sessions",
        "web_request_models",
        "web_request_resources",
        "web_request_file_tree",
        "web_request_git_status",
        "web_read_file",
        "web_file_diff",
        "web_request_agent_tasks",
        "web_request_session_files",
        "web_read_session_file",
        "web_query",
        "web_request_workspaces",
        "web_add_workspace",
        "web_remove_workspace",
        # === agent 向导（terminal + web 共用）===
        "agent_wizard_init",
        "agent_generate_request",
        "agent_generate_cancel",
        "agent_wizard_submit",
        # === Goal 状态栏操作（web GoalBar；terminal 用 /goal 命令）===
        "goal_action",
    ]
    line: str | None = None
    command: str | None = None
    value: str | None = None
    request_id: str | None = None
    allowed: bool | None = None
    session_allow: bool | None = None
    tool_name: str | None = None
    answer: str | None = None
    feedback: str | None = None
    # === web_* 专属字段 ===
    session_id: str | None = None
    session_ids: list[str] | None = None
    delete_all: bool | None = None
    setting_key: str | None = None
    setting_value: Any = None
    limit: int | None = None
    offset: int | None = None
    args: str | None = None
    # === 工作区（多目录空间）专属字段 ===
    cwd: str | None = None
    path: str | None = None
    # submit_line 专属：为 True 时跳过命令注册表，直接当 user 消息提交给 LLM
    treat_as_text: bool | None = None
    # === agent 向导专属字段 ===
    prompt: str | None = None  # agent_generate_request 的自然语言描述
    model: str | None = None  # agent_generate_request 使用的模型
    fields: dict[str, Any] | None = None  # agent_wizard_submit 的字段
    scope: str | None = None  # agent_wizard_submit 的作用域（user/project）
    # === goal_action 专属字段（web GoalBar 的 pause/resume/edit/clear）===
    goal_action: str | None = None  # pause | resume | edit | clear
    goal_id: str | None = None  # CAS：当前 goal 的精确 id
    revision: int | None = None  # CAS：当前 goal 的精确 revision
    objective: str | None = None  # edit 的替换目标文本


class TranscriptItem(BaseModel):
    """转录项模型。

    表示前端呈现的一行转录内容。

    Attributes:
        role: 角色（system/user/assistant/tool/tool_result/log）
        text: 文本内容
        tool_name: 工具名称
        tool_input: 工具输入参数
        is_error: 是否为错误
        reasoning: 思考文本（可选）
    """

    role: Literal["system", "user", "assistant", "tool", "tool_result", "log", "plan"]
    text: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    is_error: bool | None = None
    reasoning: str | None = None
    tool_use_id: str | None = None
    # 新增：并行分组支持
    message_id: str | None = None
    # 新增：会话快照恢复标记
    session_snapshot: bool = False
    # 命令产物标记：由命令选择器/内部命令调用产生的转录（如 /context set 512000），
    # 非真实用户输入。前端以此过滤，不能按文本以 / 开头判断——用户消息
    # 也可能以 / 开头（如 "/xxx 帮我看看"），按前缀过滤会误吞真实消息
    is_command: bool = False


class TaskSnapshot(BaseModel):
    """任务快照模型。

    UI安全的任务表示形式。

    Attributes:
        id: 任务 ID
        type: 任务类型
        status: 任务状态
        description: 任务描述
        metadata: 元数据字典
    """

    id: str
    type: str
    status: str
    description: str
    metadata: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: TaskRecord) -> TaskSnapshot:
        """从任务记录创建任务快照。

        Args:
            record: 任务记录

        Returns:
            TaskSnapshot: 任务快照
        """
        return cls(
            id=record.id,
            type=record.type,
            status=to_task_display_status(record.status),
            description=record.description,
            metadata=dict(record.metadata),
        )


class BackendEvent(BaseModel):
    """后端事件模型。

    表示从 Python 后端发送到 React 前端的事件。

    Attributes:
        type: 事件类型
        select_options: 选择选项列表
        message: 消息文本
        item: 转录项
        state: 状态字典
        tasks: 任务快照列表
        mcp_servers: MCP 服务器状态列表
        commands: 命令列表
        modal: 模态对话框配置
        tool_name: 工具名称
        tool_input: 工具输入参数
        tool_output: 工具输出
        is_error: 是否为错误
        phase: 当前会话阶段
        tool_count: 工具链中的工具数量
        todo_items: 待办事项列表
        todo_markdown: 待办事项 Markdown
        plan_mode: 计划模式
        swarm_teammates: Swarm 队友列表
        swarm_notifications: Swarm 通知列表
        reasoning: 思考增量或最终思考文本
        command_result_data: 指令结果数据
    """

    type: Literal[
        "ready",
        "state_snapshot",
        "tasks_snapshot",
        "transcript_item",
        "assistant_delta",
        "assistant_complete",
        "line_complete",
        "tool_started",
        "tool_input_updated",
        "tool_completed",
        "tool_chain_started",
        "tool_chain_completed",
        "tool_progress",
        "tool_queued",
        "tool_reset",
        "session_rewind",
        "clear_transcript",
        "replace_transcript",
        "modal_request",
        "select_request",
        "todo_update",
        "plan_mode_change",
        "swarm_status",
        "command_result",
        "bg_agent_status",
        "error",
        # === Web 前端专属推送事件（web_* 命名空间）===
        "web_sessions",
        "web_resources",
        "web_setting_changed",
        "web_models",
        "web_file_tree",
        "web_git_status",
        "web_file_content",
        "web_agent_tasks",
        "web_session_files",
        "web_restore_started",
        "web_restore_completed",
        "web_query_result",
        "web_workspaces",
        "shutdown",
        # === agent 向导响应（terminal + web 共用）===
        "agent_wizard_init_response",
        "agent_generate_response",
        "agent_wizard_result",
        # === Goal 状态栏操作结果（web GoalBar 内联错误显示）===
        "goal_action_result",
        # === Goal 轮次生命周期（web toast / terminal StatusBar，不进转录）===
        "goal_status",
        # === 版本更新提醒 ===
        "update_available",
    ]
    select_options: list[dict[str, Any]] | None = None
    message: str | None = None
    item: TranscriptItem | None = None
    state: dict[str, Any] | None = None
    tasks: list[TaskSnapshot] | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    commands: list[str] | None = None
    modal: dict[str, Any] | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    output: str | None = None
    is_error: bool | None = None
    phase: str | None = None          # 当前会话阶段
    tool_count: int | None = None     # 工具链中的工具数量
    # 新增字段用于增强事件
    todo_items: list[dict[str, Any]] | None = None
    todo_markdown: str | None = None
    plan_mode: str | None = None
    swarm_teammates: list[dict[str, Any]] | None = None
    swarm_notifications: list[dict[str, Any]] | None = None
    reasoning: str | None = None
    command_result_data: dict[str, Any] | None = None
    items: list[TranscriptItem] | None = None
    # 新增：结构化输出支持
    structured_output: dict[str, Any] | None = None
    output_type: str | None = None
    tool_metadata: dict[str, Any] | None = None
    # 新增：进度消息
    progress_type: str | None = None
    # assistant_complete 携带：该助手回合后是否跟随工具链（true=中间步骤，false=最终答案）
    # 前端据此在最终文本输出后立即退出 busy，无需等待 line_complete
    tool_chain_follows: bool | None = None
    # 新增：会话回退
    rewind_to_index: int | None = None
    # === rewind / 会话回退事件字段 ===
    restored_text: str | None = None                    # session_rewind 携带的被回退 user 消息（回填输入框）

    # === web_* 推送事件字段 ===
    session_id: str | None = None                       # web_restore_started/completed 的会话 ID
    web_sessions: list[dict[str, Any]] | None = None    # web_sessions 推送的会话列表
    active_session_id: str | None = None                # web_sessions 携带的活跃会话 ID
    web_resources: dict[str, Any] | None = None         # web_resources 推送的资源快照
    web_models: list[dict[str, Any]] | None = None      # web_models 推送的模型选项
    web_workspaces: list[dict[str, Any]] | None = None  # web_workspaces 推送的工作区列表
    cwd: str | None = None                              # web_resources 携带的所属工作区目录
    setting_key: str | None = None                      # web_setting_changed 的键名
    setting_value: Any = None                           # web_setting_changed 的值
    web_query_kind: str | None = None                   # web_query_result 的结果类型（text/transcript_replace/download）
    web_query_payload: Any = None                       # web_query_result 的载荷
    web_request_id: str | None = None                   # web_query_result 关联的请求 ID
    web_command: str | None = None                      # web_query_result 关联的命令名
    web_error: str | None = None                        # web_restore_completed 等事件的错误信息（非空表示操作失败）
    # === 右栏扩展：文件树 / Git 状态 / 文件预览 / 智能体与任务 ===
    web_file_tree: dict[str, Any] | None = None         # web_file_tree 推送的目录条目（path + entries）
    web_git_status: dict[str, Any] | None = None        # web_git_status 推送的 Git 快照（branch/upstream/files）
    web_file_content: dict[str, Any] | None = None      # web_file_content 推送的文件内容（预览）
    web_agent_tasks: list[dict[str, Any]] | None = None # web_agent_tasks 推送的智能体与后台任务列表
    web_session_files: list[dict[str, Any]] | None = None  # web_session_files 推送的会话内修改文件列表（会话文件区块）
    # === agent 向导响应专属字段 ===
    request_id: str | None = None
    error: str | None = None
    agent: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    skills: list[dict[str, Any]] | None = None
    models: list[dict[str, Any]] | None = None
    success: bool | None = None
    path: str | None = None
    errors: dict[str, Any] | None = None
    # === 首次登录标识（ready 事件携带，前端据此自动弹出配置表单）===
    first_login: bool | None = None
    # === 版本更新提醒（update_available 事件携带）===
    latest_version: str | None = None
    # === goal_action_result 专属字段 ===
    goal_action: str | None = None          # 回执的操作名（pause/resume/edit/clear）
    goal_error: dict[str, Any] | None = None  # 失败时的 {code, message}
    # === goal_status 专属字段（结构化轮次生命周期，前端本地化）===
    goal_status: dict[str, Any] | None = None  # {kind: round|wrapup|limit|disarmed, round?, max_rounds?, phase?}

    @classmethod
    def ready(
        cls,
        state: AppState,
        tasks: list[TaskRecord],
        commands: list[str],
        first_login: bool = False,
    ) -> BackendEvent:
        """创建就绪事件。

        Args:
            state: 应用状态
            tasks: 任务记录列表
            commands: 命令列表
            first_login: 是否首次登录（无 env_N 且无 working_directory），
                前端据此自动弹出配置表单

        Returns:
            BackendEvent: 就绪事件
        """
        return cls(
            type="ready",
            state=_state_payload(state),
            tasks=[TaskSnapshot.from_record(task) for task in tasks],
            mcp_servers=[],
            commands=commands,
            first_login=first_login,
        )

    @classmethod
    def state_snapshot(cls, state: AppState) -> BackendEvent:
        """创建状态快照事件。

        Args:
            state: 应用状态

        Returns:
            BackendEvent: 状态快照事件
        """
        return cls(type="state_snapshot", state=_state_payload(state))

    @classmethod
    def tasks_snapshot(cls, tasks: list[TaskRecord]) -> BackendEvent:
        """创建任务快照事件。

        Args:
            tasks: 任务记录列表

        Returns:
            BackendEvent: 任务快照事件
        """
        return cls(
            type="tasks_snapshot",
            tasks=[TaskSnapshot.from_record(task) for task in tasks],
        )

    @classmethod
    def status_snapshot(
        cls,
        *,
        state: AppState,
        mcp_servers: list[McpConnectionStatus],
    ) -> BackendEvent:
        """创建状态快照事件（包含 MCP 信息）。

        Args:
            state: 应用状态
            mcp_servers: MCP 服务器状态列表

        Returns:
            BackendEvent: 状态快照事件
        """
        return cls(
            type="state_snapshot",
            state=_state_payload(state),
            mcp_servers=[
                {
                    "name": server.name,
                    "state": server.state,
                    "detail": server.detail,
                    "transport": server.transport,
                    "auth_configured": server.auth_configured,
                    "tool_count": len(server.tools),
                    "resource_count": len(server.resources),
                }
                for server in mcp_servers
            ],
        )


def _state_payload(state: AppState) -> dict[str, Any]:
    """将应用状态转换为载荷字典。

    Args:
        state: 应用状态

    Returns:
        dict[str, Any]: 状态载荷
    """
    from illusion.swarm.agent_executor import list_active_agents
    return {
        "model": state.model,
        "cwd": state.cwd,
        "auth_status": state.auth_status,
        "base_url": state.base_url,
        "permission_mode": format_permission_mode(state.permission_mode),
        "ui_language": state.ui_language,
        "effort": state.effort,
        "mcp_connected": state.mcp_connected,
        "mcp_failed": state.mcp_failed,
        "output_style": state.output_style,
        "show_thinking": state.show_thinking,
        "phase": state.phase,
        "session_id": state.session_id,
        "session_name": state.session_name,
        "context_window": state.context_window,
        "context_tokens": state.context_tokens,
        "context_cache_read": state.context_cache_read,
        "context_cache_creation": state.context_cache_creation,
        "context_input": state.context_input,
        "context_output": state.context_output,
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
        "cache_read_input_tokens": state.cache_read_input_tokens,
        "cache_creation_input_tokens": state.cache_creation_input_tokens,
        "goal": state.goal,
        "agent_count": len(list_active_agents()),
    }


# 权限模式标签映射
_MODE_LABELS = {
    "default": "Default",
    "plan": "Plan Mode",
    "full_auto": "Auto",
    "yolo": "YOLO",
    "PermissionMode.DEFAULT": "Default",
    "PermissionMode.PLAN": "Plan Mode",
    "PermissionMode.FULL_AUTO": "Auto",
    "PermissionMode.YOLO": "YOLO",
}


def format_permission_mode(raw: str) -> str:
    """将原始权限模式转换为人类可读的标签。

    Args:
        raw: 原始权限模式字符串

    Returns:
        str: 格式化的权限模式标签
    """
    return _MODE_LABELS.get(raw, raw)


__all__ = [
    "BackendEvent",
    "FrontendRequest",
    "TaskSnapshot",
    "TranscriptItem",
    "format_permission_mode",
]
