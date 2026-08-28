"""Computer Use 工具策略模块（结果提示 + 暴露白名单）。

- 在 computer use MCP 工具的结果末尾追加提示，告知 LLM 存在对应 skill 指导，
  避免 LLM 凭直觉调用工具而忽略既定操作规范。
- 对 computer 服务器的 MCP 工具做白名单过滤：cua-driver 自带 50+ 工具
  （浏览器/录制/生命周期/诊断等），全部暴露会撑爆上下文。
"""

from __future__ import annotations

from illusion.computer.constants import (
    COMPUTER_MCP_SERVER,
    COMPUTER_TOOL_ALLOWLIST,
    SKILL_HINT,
)


def computer_use_hint(server_name: str) -> str:
    """返回指定 MCP 服务器对应的结果提示；非 computer 服务器返回空串。

    Args:
        server_name: MCP 服务器键（如 "computer:cua"）

    Returns:
        str: 追加到工具结果末尾的提示文案
    """
    if server_name == COMPUTER_MCP_SERVER:
        return SKILL_HINT
    return ""


def is_computer_use_server(server_name: str) -> bool:
    """判断服务器是否属于 computer use（用于追加 skill 提示与结果图片落盘策略）。"""
    return server_name == COMPUTER_MCP_SERVER


def is_computer_tool_exposed(server_name: str, tool_name: str) -> bool:
    """判断一个 MCP 工具是否应暴露给 LLM。

    computer 服务器按 COMPUTER_TOOL_ALLOWLIST 过滤；其他服务器全部暴露。

    Args:
        server_name: MCP 服务器键
        tool_name: 工具名

    Returns:
        bool: 是否暴露
    """
    if server_name != COMPUTER_MCP_SERVER:
        return True
    return tool_name in COMPUTER_TOOL_ALLOWLIST
