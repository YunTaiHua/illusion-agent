"""Computer Use 功能包。

为 IllusionAgent 提供操作本机桌面应用（Computer Use）的能力：
- 以 plugin 方式注入 computer use 的 MCP 工具扩展（cua-driver MCP 服务器）
  与内置 skill（开关见 settings.computer_use.enabled）；
- cua 二进制管理与更新（存放于 ~/.illusion/bin/，风格与 ripgrep 一致）；
- 工具结果末尾追加 skill 提示；工具结果中的图片落盘到
  ~/.illusion/cache/computer/ 并返回路径（避免撑爆 LLM 上下文）。
"""

from __future__ import annotations

from illusion.computer.binary import (
    check_update,
    download_cua_binary,
    ensure_cua_binary,
    find_cua_path,
    get_latest_version,
    get_local_version,
    update_cua_binary,
)
from illusion.computer.constants import (
    COMPUTER_MCP_SERVER,
    MCP_SERVER_NAME,
    PLUGIN_NAME,
    SKILL_HINT,
    SKILL_NAME,
)
from illusion.computer.hint import computer_use_hint, is_computer_use_server
from illusion.computer.plugin import build_computer_plugin

__all__ = [
    "COMPUTER_MCP_SERVER",
    "MCP_SERVER_NAME",
    "PLUGIN_NAME",
    "SKILL_HINT",
    "SKILL_NAME",
    "build_computer_plugin",
    "check_update",
    "computer_use_hint",
    "download_cua_binary",
    "ensure_cua_binary",
    "find_cua_path",
    "get_latest_version",
    "get_local_version",
    "is_computer_use_server",
    "update_cua_binary",
]
