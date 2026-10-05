"""
IllusionAgent 内置浏览器
========================

内置浏览器核心模块：

    - base: 浏览器后端抽象（命令契约）
    - executable: 内核解析链（Playwright Chromium → 系统 Chrome/Edge）
    - playwright_backend: 托管浏览器后端（Web/CLI 模式）
    - desktop_bridge: Electron <webview> 桥接后端（桌面模式）
    - snapshot: ARIA 快照 + ref 体系
    - manager: 会话级浏览器管理器（RuntimeBundle 持有）
    - seeding: 内置 browser-use 插件 seed
"""

from __future__ import annotations

from illusion.browser.base import BrowserCommandError
from illusion.browser.manager import BrowserConfig, BrowserManager

__all__ = [
    "BrowserCommandError",
    "BrowserConfig",
    "BrowserManager",
]
