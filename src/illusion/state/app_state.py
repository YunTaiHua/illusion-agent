"""
应用状态模块
===========

本模块定义 IllusionAgent 应用状态数据模型。

主要功能：
    - 定义共享的UI/会话状态数据结构
    - 支持状态属性的不可变更新

类说明：
    - AppState: 应用状态数据类

使用示例：
    >>> from illusion.state import AppState
    >>> state = AppState(model="claude-3-5-sonnet-20241022", permission_mode="default")
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppState:
    """共享的可变UI/会话状态数据类
    
    Attributes:
        model: 当前使用的模型名称
        permission_mode: 权限模式 (default/plan/bypassPermissions 等)
        ui_language: UI语言 (默认 zh-CN)
        cwd: 当前工作目录
        auth_status: 认证状态
        base_url: API基础URL
        effort: 推理 Effort 级别 (low/medium/high)
        mcp_connected: 已连接的MCP服务器数量
        mcp_failed: 失败的MCP服务器数量
        show_thinking: 是否显示思考过程
        phase: 会话阶段 (idle/thinking/tool_executing)
        team_context: 当前会话的团队上下文（若已创建团队）
        max_tokens: 最大输出令牌数
        input_tokens: 累积 API input tokens（非缓存）
        output_tokens: 累积 API output tokens
        cache_read_input_tokens: 累积缓存命中 tokens
        cache_creation_input_tokens: 累积缓存写入 tokens
    """

    model: str  # 模型名称
    permission_mode: str  # 权限模式
    ui_language: str = "zh-CN"  # UI语言
    cwd: str = "."  # 当前工作目录
    auth_status: str = "missing"  # 认证状态
    base_url: str = ""  # API基础URL
    effort: str = "medium"  # 推理 Effort 级别
    mcp_connected: int = 0  # 已连接的MCP服务器数量
    mcp_failed: int = 0  # 失败的MCP服务器数量
    show_thinking: bool = True  # 是否显示思考过程
    phase: str = "idle"  # 会话阶段: idle / thinking / tool_executing
    session_id: str = ""  # 当前会话 ID
    session_name: str = ""  # 当前会话显示名称（CLI --name / /rename）
    context_window: int = 0  # 上下文窗口大小（tokens）
    context_tokens: int = 0  # 当前已用 tokens（估算）
    context_cache_read: int = 0  # 最后一次 API 调用的缓存命中 tokens
    context_cache_creation: int = 0  # 最后一次 API 调用的缓存写入 tokens
    context_input: int = 0  # 最后一次 API 调用的非缓存输入 tokens
    context_output: int = 0  # 最后一次 API 调用的输出 tokens
    team_context: dict[str, object] | None = None  # 团队上下文
    max_tokens: int = 16384  # 最大输出令牌数
    input_tokens: int = 0  # 累积 API input tokens（非缓存）
    output_tokens: int = 0  # 累积 API output tokens
    cache_read_input_tokens: int = 0  # 累积缓存命中 tokens
    cache_creation_input_tokens: int = 0  # 累积缓存写入 tokens
    goal: dict[str, object] | None = None  # goal 视图（phase/objective/rounds 等；无目标为 None）
