"""
内置工具注册模块
================

本模块提供 IllusionAgent 内置工具的注册和管理功能。

主要组件：
    - BaseTool: 工具抽象基类
    - ToolExecutionContext: 工具执行上下文
    - ToolResult: 工具执行结果
    - ToolRegistry: 工具注册表
    - create_default_tool_registry: 创建默认工具注册表

使用示例：
    >>> from illusion.tools import create_default_tool_registry, ToolRegistry
    >>> registry = create_default_tool_registry()
"""

from typing import Any

from illusion.tools.agent_tool import AgentTool
from illusion.tools.ask_user_question_tool import AskUserQuestionTool
from illusion.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from illusion.tools.bash_tool import BashTool
from illusion.tools.config_tool import ConfigTool
from illusion.tools.cron_tool import CronTool
from illusion.tools.enter_plan_mode_tool import EnterPlanModeTool
from illusion.tools.enter_worktree_tool import EnterWorktreeTool
from illusion.tools.exit_plan_mode_tool import ExitPlanModeTool
from illusion.tools.exit_worktree_tool import ExitWorktreeTool
from illusion.tools.file_edit_tool import FileEditTool
from illusion.tools.file_read_tool import FileReadTool
from illusion.tools.file_write_tool import FileWriteTool
from illusion.tools.glob_tool import GlobTool
from illusion.tools.goal_tools import CreateGoalTool, GetGoalTool, UpdateGoalTool
from illusion.tools.grep_tool import GrepTool
from illusion.tools.list_mcp_resources_tool import ListMcpResourcesTool
from illusion.tools.list_sessions_tool import ListSessionsTool
from illusion.tools.lsp_tool import LspTool
from illusion.tools.mcp_auth_tool import McpAuthTool
from illusion.tools.mcp_tool import McpToolAdapter
from illusion.tools.powershell_tool import PowerShellTool
from illusion.tools.read_mcp_resource_tool import ReadMcpResourceTool
from illusion.tools.send_message_tool import SendMessageTool
from illusion.tools.skill_tool import SkillTool
from illusion.tools.sleep_tool import SleepTool
from illusion.tools.task_output_tool import TaskOutputTool
from illusion.tools.task_stop_tool import TaskStopTool
from illusion.tools.team_create_tool import TeamCreateTool
from illusion.tools.team_delete_tool import TeamDeleteTool
from illusion.tools.todo_write_tool import TodoWriteTool
from illusion.tools.web_fetch_tool import WebFetchTool
from illusion.tools.web_search_tool import WebSearchTool


def create_default_tool_registry(
    mcp_manager: Any = None,
    channel_tools: list[BaseTool[Any]] | None = None,
    goal_enabled: bool = False,
) -> ToolRegistry:
    """返回默认内置工具注册表

    Args:
        mcp_manager: MCP 管理器（可选）
        channel_tools: 渠道内置工具列表（可选，渠道启用时由调用方传入）
        goal_enabled: 是否注册 goal 工具（settings.goal.enabled；goal 属根
            会话，工具经引擎的 tool_metadata 拿到 GoalManager）

    Returns:
        ToolRegistry: 工具注册表
    """
    registry = ToolRegistry()
    tools: list[BaseTool[Any]] = [
        BashTool(),
        PowerShellTool(),
        AskUserQuestionTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        LspTool(),
        McpAuthTool(),
        GlobTool(),
        GrepTool(),
        SkillTool(),
        WebFetchTool(),
        WebSearchTool(),
        ConfigTool(),
        SleepTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        TodoWriteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        ListSessionsTool(),
        CronTool(),
        TaskStopTool(),
        TaskOutputTool(),
        AgentTool(),
        SendMessageTool(),
        TeamCreateTool(),
        TeamDeleteTool(),
    ]
    if goal_enabled:
        # goal 工具（get_goal/create_goal/update_goal）
        tools.extend([GetGoalTool(), CreateGoalTool(), UpdateGoalTool()])
    for tool in tools:
        registry.register(tool)
    if mcp_manager is not None:
        registry.register(ListMcpResourcesTool(mcp_manager))
        registry.register(ReadMcpResourceTool(mcp_manager))
        for tool_info in mcp_manager.list_tools():
            registry.register(McpToolAdapter(mcp_manager, tool_info))
    # 注册渠道内置工具（飞书文档/云盘等，渠道启用时由调用方传入）
    if channel_tools:
        for tool in channel_tools:
            registry.register(tool)
    return registry


__all__ = [
    "BaseTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
]
