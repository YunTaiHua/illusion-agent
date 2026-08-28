"""
IllusionAgent 核心模块
====================

IllusionAgent 是一个 AI 驱动的编程助手，提供交互式会话和命令行工具。

主要子模块：
    - api: API 客户端集成
    - auth: 认证管理
    - config: 配置系统
    - engine: 核心引擎
    - hooks: 钩子系统
    - mcp: MCP 客户端
    - swarm: Swarm 后端
    - tools: 内置工具
    - ui: 用户界面

使用示例：
    >>> import illusion
    >>> from illusion.config import load_settings
    >>> from illusion.tools import create_default_tool_registry
"""

from __future__ import annotations

__version__ = "0.4.9"