"""
Browser Use 子系统
==================

IllusionAgent 内置浏览器自动化运行时。参照 browser-use 插件（node_repl MCP +
browser-client + skills + docs manifests）的架构构建：

- ``config``: settings.json 中的 ``browser`` 配置段（总开关 / 无头 / 配置档案等）
- ``discovery``: Chromium 系列浏览器与用户数据目录发现
- ``protocol``: browser-client 命令信封与结果结构（类型与校验）
- ``session``: 基于 Playwright 的受管 Chromium 生命周期与 Tab 注册表
- ``commands``: 命令分发执行器（协议方法 → Playwright 操作）
- ``broker``: 面向 node_repl 内核的回环 TCP broker（令牌鉴权）
- ``service``: BrowserUseService —— 子系统编排入口（broker + 浏览器 + 实时画面）
- ``runtime_assets``: 内置 node 运行时资产定位与 MCP 服务器启动命令构建
- ``integration``: 与 settings / skills / MCP 注入的集成胶水

启用方式（settings.json）::

    "browser": { "enabled": true, ... }

启用后注入：
    - ``node_repl`` stdio MCP 服务器（工具 mcp__node_repl__js / js_reset /
      js_add_node_module_dir），内核中经 ``agent.browsers`` 驱动本模块
    - ``browser-use:control-browser`` 与 ``browser-use:web-gui-tester`` 内置 skills
    - Web 端右栏「用量」页签的浏览器实时画面

模块设计说明：
    协议层（protocol/commands）与传输层（broker）解耦；传输层只做鉴权、
    分帧与并发编排，命令语义全部由 commands 承担，方便对两层分别测试。
"""

from illusion.browser_use.config import BrowserSettings, BrowserViewport
from illusion.browser_use.service import BrowserUseService

__all__ = [
    "BrowserSettings",
    "BrowserUseService",
    "BrowserViewport",
]
