"""
斜杠命令公共类型定义
====================

定义 CommandResult、CommandContext、CommandHandler 等命令系统核心类型。
从 registry.py 中提取，供所有命令模块共享。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from illusion.engine.query_engine import QueryEngine

if TYPE_CHECKING:
    from illusion.state import AppStateStore
    from illusion.tools.base import ToolRegistry


@dataclass
class CommandResult:
    """斜杠命令执行结果

    Attributes:
        message: 返回给用户的消息
        should_exit: 是否应该退出程序
        clear_screen: 是否应该清除屏幕
        replay_messages: 要在TUI中重放的消息列表
        continue_pending: 是否继续待处理的工具循环
        continue_turns: 继续的回合数
        refresh_state: 命令处理后是否刷新 app_state
    """

    message: str | None = None  # 返回消息
    should_exit: bool = False  # 退出标志
    clear_screen: bool = False  # 清屏标志
    replay_messages: list[Any] | None = None  # ConversationMessage列表用于TUI重放
    needs_api_rebuild: bool = False  # 需要重建 API 客户端（跨 env 切换模型时）
    continue_pending: bool = False  # 继续待处理标志
    continue_turns: int | None = None  # 继续回合数
    reset_session: bool = False  # 是否重置会话ID
    restored_session_id: str | None = None  # 恢复的会话ID
    refresh_state: bool = False  # 命令处理后是否刷新 app_state（sync_app_state）
    rewind_restored_text: str | None = None  # rewind 被回退的最后一条 user 消息（供前端回填输入框）
    drive_goal: bool = False  # 命令后立即驱动 goal 轮次（/goal 创建 / resume 后）


@dataclass
class CommandContext:
    """命令处理器可用的上下文

    Attributes:
        engine: 查询引擎实例
        hooks_summary: hooks摘要
        mcp_summary: MCP摘要
        plugin_summary: 插件摘要
        cwd: 当前工作目录
        tool_registry: 工具注册表
        app_state: 应用状态存储
        session_id: 当前会话ID
        channel_hint: 渠道感知提示词（重建系统提示词时复用）
        interactive: 是否运行在可交互终端（TUI）。交互式命令（如模型能力
            勾选引导）仅在 True 时询问；Web 会话为 False，跳过询问
    """

    engine: QueryEngine  # 查询引擎
    hooks_summary: str = ""  # hooks摘要
    mcp_summary: str = ""  # MCP摘要
    plugin_summary: str = ""  # 插件摘要
    cwd: str = "."  # 当前工作目录
    tool_registry: ToolRegistry | None = None  # 工具注册表
    app_state: AppStateStore | None = None  # 应用状态
    session_id: str = ""  # 当前会话ID
    channel_hint: str | None = None  # 渠道感知提示词（重建系统提示词时复用）
    interactive: bool = False  # 是否支持交互式输入（TUI 终端）


# 命令处理器类型别名
CommandHandler = Callable[[str, CommandContext], Awaitable[CommandResult]]
