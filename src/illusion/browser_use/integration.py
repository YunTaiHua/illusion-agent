"""Browser Use ↔ 运行时集成
===========================

供 ``ui/runtime.build_runtime`` 与 Web 设置通道使用的集成胶水：

- :func:`playwright_available`: 可选依赖探测（缺失时功能静默降级并告警）
- :func:`create_browser_service`: 按设置构建（不启动）BrowserUseService
- :func:`inject_browser_mcp_config`: 把 node_repl MCP 服务器追加进 server_configs
- :func:`apply_cli_browser_overrides`: CLI --browser-use / --browser-profile 会话覆盖

设计约定（与 zcode 插件「显式启用」思路一致）：
    browser.enabled=false 时本模块不产生任何副作用；enabled=true 但 Playwright
    缺失时告警并降级为不注入（保持会话可用）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from illusion.browser_use.config import BrowserSettings
from illusion.browser_use.service import BrowserUseService

logger = logging.getLogger(__name__)


def playwright_available() -> bool:
    """探测 Playwright 运行时是否可导入（不启动浏览器）。"""
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


def create_browser_service(
    settings: BrowserSettings, *, config_dir: str | None = None
) -> BrowserUseService | None:
    """按设置构建 BrowserUseService（不启动；调用方 await start()）。

    enabled=false 或 Playwright 缺失时返回 None（调用方跳过全部注入）。
    """
    if not settings.enabled:
        return None
    if not playwright_available():
        logger.warning(
            "browser.enabled=true 但 playwright 未安装，Browser Use 已降级关闭。"
            "可执行 `pip install 'illusion-agent[browser]'` 启用。"
        )
        return None
    return BrowserUseService(settings, config_dir=config_dir)


def inject_browser_mcp_config(
    server_configs: dict[str, Any],
    service: BrowserUseService | None,
    *,
    cwd: str | None = None,
) -> None:
    """把 node_repl MCP 服务器配置注入 server_configs（就地修改）。

    与用户自配的 ``node_repl`` 服务器同名冲突时跳过注入（用户显式配置优先）。

    Args:
        server_configs: MCP 服务器配置表（就地修改）
        service: Browser Use 服务（None 表示功能未启用）
        cwd: 工作区目录（内核的 require/import 解析基准）
    """
    if service is None:
        return
    if "node_repl" in server_configs:
        logger.info("检测到用户自配的 node_repl MCP 服务器，跳过内置 Browser Use 注入")
        return
    config = service.build_mcp_server_config(cwd=cwd)
    if config is not None:
        server_configs["node_repl"] = config


# 会话级浏览器覆盖环境变量（CLI --browser-use / --browser-profile 写入；
# 与 cron 的 ILLUSION_PERMISSION_MODE 同一模式：进程内生效，不持久化）
ENV_BROWSER_USE = "ILLUSION_BROWSER_USE"
ENV_BROWSER_PROFILE = "ILLUSION_BROWSER_PROFILE"


def apply_env_browser_overrides(settings: Any) -> Any:
    """应用进程环境变量中的浏览器会话覆盖（CLI 入口写入，不持久化）。"""
    browser_use = os.environ.get(ENV_BROWSER_USE)
    browser_profile = os.environ.get(ENV_BROWSER_PROFILE)
    if not browser_use and not browser_profile:
        return settings
    return apply_cli_browser_overrides(
        settings, browser_use=browser_use, browser_profile=browser_profile
    )


def apply_cli_browser_overrides(
    settings: Any,
    *,
    browser_use: str | None = None,
    browser_profile: str | None = None,
) -> Any:
    """应用 CLI 浏览器会话覆盖（不持久化），返回新 Settings（或原实例）。

    Args:
        settings: 已加载的 Settings。
        browser_use: ``off``（本会话禁用）| ``auto``（跟随设置）|
            ``headless``（启用+无头）| ``headed``（启用+有窗口）。
        browser_profile: ``blank``（空白档案）| ``user``（用户数据）。
    """
    if browser_use is None and browser_profile is None:
        return settings
    enabled: bool | None = None
    headless: bool | None = None
    if browser_use == "off":
        enabled = False
    elif browser_use == "auto":
        enabled = True
    elif browser_use == "headless":
        enabled, headless = True, True
    elif browser_use == "headed":
        enabled, headless = True, False
    browser = settings.browser.with_overrides(
        profile=browser_profile, headless=headless, enabled=enabled
    )
    return settings.model_copy(update={"browser": browser})
