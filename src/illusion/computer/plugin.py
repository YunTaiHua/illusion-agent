"""Computer Use 内置插件构建模块。

以 plugin 方式注入 computer use 的 MCP 工具扩展（cua-driver MCP 服务器）
与内置 skill。开关（settings.computer_use.enabled）控制是否注册：
关闭时既不注入 MCP 服务器也不注入 skill。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from illusion.computer.binary import expected_bin_path, find_cua_path
from illusion.computer.constants import MCP_SERVER_NAME, PLUGIN_NAME
from illusion.computer.skill import build_computer_use_skill
from illusion.mcp.types import McpStdioServerConfig
from illusion.plugins.schemas import PluginManifest
from illusion.plugins.types import LoadedPlugin


def build_computer_plugin(settings: Any) -> LoadedPlugin | None:
    """构建内置 Computer Use 插件。

    Args:
        settings: Settings 实例（读取 computer_use.enabled）

    Returns:
        LoadedPlugin | None: 开关关闭时返回 None（不注入任何工具/skill）
    """
    computer_use = getattr(settings, "computer_use", None)
    if computer_use is None or not getattr(computer_use, "enabled", False):
        return None

    # MCP 服务器命令解析：env > bin 缓存 > PATH；均未命中时使用期望路径
    # （启动阶段 ensure_cua_binary 会完成下载，见 ui/runtime.build_runtime）。
    cua_path = find_cua_path() or expected_bin_path()

    return LoadedPlugin(
        manifest=PluginManifest(
            name=PLUGIN_NAME,
            version="0.0.0",
            description="Computer Use：操作本机桌面应用的 MCP 工具与 skill",
            enabled_by_default=False,
        ),
        path=Path(__file__).parent,
        enabled=True,
        skills=[build_computer_use_skill()],
        mcp_servers={
            MCP_SERVER_NAME: McpStdioServerConfig(
                command=cua_path,
                args=["mcp"],
            )
        },
    )
