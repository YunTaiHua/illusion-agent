"""受管 Chromium 会话（Playwright 后端）
======================================

负责浏览器生命周期与 Tab 注册表：

- 三种启动路径：空白档案（一次性临时用户数据目录）、用户档案（真实浏览器
  数据，需先关闭正在运行的浏览器）、CDP attach（settings.browser.cdp_url）。
- Tab 注册表：tabId → Playwright Page；agent 自有 tab 与用户 tab 分离
  （list 与 listUserTabs 严格分离，与协议语义一致）。
- ref 注册表：snapshot 注入 ``data-illusion-ref`` 属性，ref 操作经
  ``[data-illusion-ref="…"]`` locator 定位；导航后属性失效并统一清理。
- JS 弹窗跟踪：dialog 监听器保持弹窗 pending，交由 handleDialog 处置。

Playwright 是可选依赖（``pip install illusion-agent[browser]``），本模块在
导入时才引入，缺失时抛出带安装提示的 :class:`BrowserDependencyError`。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from illusion.browser_use.config import BrowserSettings
from illusion.browser_use.discovery import find_browser_executable, find_user_data_dir
from illusion.browser_use.protocol import (
    BrowserCommandFailure,
    BrowserViewportSize,
    JsDialogInfo,
    LifecycleStatus,
)

logger = logging.getLogger(__name__)

# JS 弹窗类型（协议 Literal 收窄用）
LifecycleDialogType = Literal["alert", "confirm", "prompt", "beforeunload"]

# snapshot 注入的 ref 属性名；locator 侧以 [data-<attr>] 定位
REF_ATTRIBUTE = "data-illusion-ref"
REF_LOCATOR_TEMPLATE = f'[{REF_ATTRIBUTE}="{{ref}}"]'

# 空白档案临时目录前缀
_BLANK_PROFILE_PREFIX = "illusion-browser-blank-"


class BrowserDependencyError(RuntimeError):
    """Playwright 依赖缺失。"""



def _require_playwright() -> Any:
    """延迟导入 playwright 包；缺失时给出可操作的安装提示。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - 依赖环境相关
        raise BrowserDependencyError(
            "Browser Use 需要 playwright 运行时。请执行：\n"
            "  pip install 'illusion-agent[browser]'\n"
            "  python -m playwright install chromium\n"
            "或在 settings.json 中关闭 browser.enabled。"
        ) from exc
    from playwright.async_api import async_playwright

    return async_playwright


@dataclass
class PendingDialog:
    """待处置的 JS 弹窗（Playwright Dialog + 协议信息）。"""

    info: JsDialogInfo
    dialog: Any  # playwright.async_api.Dialog（可选依赖，运行时动态对象）


@dataclass
class TabEntry:
    """注册表中的单个 tab。

    Attributes:
        tabId: 协议 tab 标识（uuid hex）。
        page: Playwright Page 对象。
        viewport: 创建/最近一次设置的真实 CSS 视口。
        agent_owned: True 表示 agent 经 newTab/claimTab 取得的受控 tab；
            False 表示用户档案中已存在的页面（仅出现在 user/CDP 模式）。
        lifecycle: finalize 生命周期标记（active/deliverable/handoff）。
        pending_dialog: 当前待处置弹窗（无则 None）。
        cdp_session: 惰性创建的 CDP 会话（导航历史查询用）。
    """

    tabId: str
    page: Any
    viewport: BrowserViewportSize
    agent_owned: bool = True
    lifecycle: LifecycleStatus = "active"
    pending_dialog: PendingDialog | None = None
    cdp_session: Any = field(default=None, repr=False)

    async def clear_refs(self) -> None:
        """移除本 tab 页面上的全部 ref 注入属性（快照重建前调用）。"""
        try:
            await self.page.evaluate(
                "() => { document.querySelectorAll('[data-illusion-ref]')"
                ".forEach((el) => el.removeAttribute('data-illusion-ref')); }"
            )
        except Exception as exc:
            logger.debug("ref 注入清理失败", exc_info=exc)


class ManagedBrowser:
    """受管 Chromium：启动/attach、Tab 注册表、代次管理。

    Usage:
        browser = ManagedBrowser(settings)
        await browser.start()
        ...  # 经 browser.registry 访问 tabs
        await browser.stop()
    """

    def __init__(self, settings: BrowserSettings) -> None:
        self._settings = settings
        self._playwright: Any = None
        self._context: Any = None
        self._browser: Any = None  # connect_over_cdp 模式下的 Browser
        self.registry: dict[str, TabEntry] = {}
        self.generation: int = 0
        self._started = False
        self._blank_profile_dir: Path | None = None
        # 启动/停止互斥；命令执行不持锁（见 commands.py 并发说明）
        self._lifecycle_lock = asyncio.Lock()
        self._headless: bool = settings.headless

    # --- 生命周期 ---

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def headless(self) -> bool:
        return self._headless

    @property
    def default_viewport(self) -> BrowserViewportSize:
        """settings 配置的默认视口（viewport reset 的还原目标）。"""
        return BrowserViewportSize(
            width=self._settings.viewport.width, height=self._settings.viewport.height
        )

    @property
    def driver_browser(self) -> Any:
        """CDP attach 模式下的 Playwright Browser 对象（窗口管理用）。"""
        return self._browser

    async def start(self) -> None:
        """启动浏览器或 attach 到 CDP 端点。重复调用无副作用。"""
        async with self._lifecycle_lock:
            if self._started:
                return
            async_playwright = _require_playwright()
            try:
                self._playwright = await async_playwright().start()
                if self._settings.cdp_url:
                    await self._connect_cdp()
                else:
                    await self._launch_persistent()
            except BrowserCommandFailure:
                await self._teardown_quiet()
                raise
            except Exception as exc:
                await self._teardown_quiet()
                # 用户档案被正在运行的浏览器锁定是最常见失败，给出可操作提示
                if self._settings.profile == "user" and self._settings.cdp_url == "":
                    raise BrowserCommandFailure(
                        "backend_unavailable",
                        f"无法启动用户档案浏览器：{exc}。"
                        "请先完全退出正在运行的浏览器（含后台进程），"
                        '或将 settings.json 的 browser.profile 改为 "blank"。',
                    ) from exc
                raise BrowserCommandFailure("backend_unavailable", f"浏览器启动失败：{exc}") from exc
            self._started = True
            self.generation += 1

    async def stop(self) -> None:
        """关闭浏览器并释放 Playwright 资源。幂等。"""
        async with self._lifecycle_lock:
            if not self._started:
                return
            await self._teardown_quiet()
            self._started = False
            self.generation += 1

    async def _teardown_quiet(self) -> None:
        """资源回收：单点失败不阻塞其余清理。"""
        for entry in list(self.registry.values()):
            self._unbind_page_events(entry)
        self.registry.clear()
        # 顺序：context/browser（页面资源）→ playwright 驱动进程（.stop()）
        for closer in (self._context, self._browser):
            if closer is None:
                continue
            try:
                await closer.close()
            except Exception:
                logger.debug("browser_use 资源回收失败", exc_info=True)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("browser_use 驱动停止失败", exc_info=True)
        self._context = None
        self._browser = None
        self._playwright = None
        self._playwright_ctx = None
        # 空白档案临时目录清理
        if self._blank_profile_dir is not None:
            shutil_rmtree_quiet(self._blank_profile_dir)
            self._blank_profile_dir = None

    async def _launch_persistent(self) -> None:
        """以持久化上下文启动受管浏览器（blank / user 档案共用路径）。"""
        settings = self._settings
        if settings.profile == "user":
            user_data_dir = self._resolve_user_data_dir()
            launch_args = [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
            ]
        else:
            user_data_dir = Path(tempfile.mkdtemp(prefix=_BLANK_PROFILE_PREFIX))
            self._blank_profile_dir = user_data_dir
            launch_args = ["--no-first-run", "--no-default-browser-check"]
        executable = find_browser_executable(
            channel=settings.channel, explicit_path=settings.executable_path
        )
        if executable is None:
            raise BrowserCommandFailure(
                "backend_unavailable",
                "未找到可用的 Chromium 系浏览器。请安装 Google Chrome，或执行 "
                "`python -m playwright install chromium`，或在 settings.json 中"
                "配置 browser.executable_path。",
            )
        self._headless = settings.headless
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=settings.headless,
            executable_path=str(executable),
            args=launch_args,
            viewport={
                "width": settings.viewport.width,
                "height": settings.viewport.height,
            },
            accept_downloads=True,
        )
        self._context.on("page", self._on_page_attached)
        # 持久化上下文启动时恢复既有标签页（user 档案恢复会话时常见）
        for page in self._context.pages:
            self._register_page(page, agent_owned=False)

    def _resolve_user_data_dir(self) -> Path:
        """解析用户档案数据目录：显式配置优先，其次按渠道探测默认目录。"""
        if self._settings.user_data_dir:
            return Path(self._settings.user_data_dir).expanduser()
        executable = find_browser_executable(
            channel=self._settings.channel, explicit_path=self._settings.executable_path
        )
        data_dir = find_user_data_dir(self._settings.channel, resolved_executable=executable)
        if data_dir is None:
            raise BrowserCommandFailure(
                "backend_unavailable",
                "未探测到用户浏览器数据目录。请在 settings.json 中显式配置 "
                "browser.user_data_dir（如 %LOCALAPPDATA%\\Google\\Chrome\\User Data）。",
            )
        return data_dir

    async def _connect_cdp(self) -> None:
        """attach 到已有浏览器的 CDP 端点。"""
        self._browser = await self._playwright.chromium.connect_over_cdp(self._settings.cdp_url)
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else await self._browser.new_context(
            viewport={
                "width": self._settings.viewport.width,
                "height": self._settings.viewport.height,
            }
        )
        self._headless = False  # attach 模式下可见性由目标浏览器决定
        self._context.on("page", self._on_page_attached)
        for page in self._context.pages:
            self._register_page(page, agent_owned=False)

    # --- Tab 注册表 ---

    def _on_page_attached(self, page: Any) -> None:
        """新页面挂载（用户手开/弹窗）：注册为非 agent 自有 tab。"""
        self._register_page(page, agent_owned=False)

    def _register_page(self, page: Any, *, agent_owned: bool) -> str:
        """注册页面并绑定事件；已注册的页面直接返回既有 tabId。"""
        for entry in self.registry.values():
            if entry.page is page:
                return entry.tabId
        tab_id = uuid.uuid4().hex[:12]
        viewport = BrowserViewportSize(
            width=self._settings.viewport.width, height=self._settings.viewport.height
        )
        entry = TabEntry(tabId=tab_id, page=page, viewport=viewport, agent_owned=agent_owned)
        page.on("dialog", self._make_dialog_handler(entry))
        page.on("close", lambda _page=None, e=entry: self._on_page_closed(e))
        self.registry[tab_id] = entry
        return tab_id

    def _on_page_closed(self, entry: TabEntry) -> None:
        self.registry.pop(entry.tabId, None)

    def _make_dialog_handler(self, entry: TabEntry) -> Any:
        """dialog 监听器：记录弹窗并保持 pending（不自动 accept/dismiss）。"""

        def _on_dialog(dialog: Any) -> None:
            raw_type = str(getattr(dialog, "type", "alert") or "alert")
            dialog_type = raw_type if raw_type in ("alert", "confirm", "prompt", "beforeunload") else "alert"
            entry.pending_dialog = PendingDialog(
                info=JsDialogInfo(
                    type=cast(LifecycleDialogType, dialog_type),
                    message=str(getattr(dialog, "message", "")),
                    defaultPrompt=str(getattr(dialog, "default_value", "") or "") or None,
                ),
                dialog=dialog,
            )

        return _on_dialog

    def _unbind_page_events(self, entry: TabEntry) -> None:
        """停止页面对象使用（close 前的注册表摘除；事件监听随页面销毁释放）。"""
        entry.pending_dialog = None

    async def create_tab(self, *, url: str = "about:blank") -> TabEntry:
        """新建 agent 自有 tab（newTab 命令后端）。"""
        if self._context is None:
            raise BrowserCommandFailure("backend_unavailable", "浏览器未启动")
        page = await self._context.new_page()
        tab_id = self._register_page(page, agent_owned=True)
        entry = self.registry[tab_id]
        try:
            await page.set_viewport_size(
                {"width": self._settings.viewport.width, "height": self._settings.viewport.height}
            )
        except Exception as exc:
            logger.debug("新 tab 视口设置失败", exc_info=exc)
        if url and url != "about:blank":
            await page.goto(url, wait_until="load", timeout=30_000)
        return entry

    def resolve_tab(self, tab_id: str | None) -> TabEntry:
        """解析 tabId → TabEntry；None 取最近激活的 agent 自有 tab。

        Raises:
            BrowserCommandFailure: tabId 不存在或无可选 tab（ref_not_found /
                backend_unavailable 语义，与插件错误码一致）。
        """
        if tab_id is not None:
            entry = self.registry.get(tab_id)
            if entry is None:
                raise BrowserCommandFailure("ref_not_found", f"Browser tab '{tab_id}' is unavailable")
            return entry
        owned = [e for e in self.registry.values() if e.agent_owned]
        if owned:
            return owned[-1]
        if self.registry:
            return next(iter(self.registry.values()))
        raise BrowserCommandFailure("backend_unavailable", "当前没有可用的浏览器标签页")

    def agent_tabs(self) -> list[TabEntry]:
        """agent 自有 tabs（list 命令可见集合）。"""
        return [e for e in self.registry.values() if e.agent_owned]

    def user_tabs(self) -> list[TabEntry]:
        """非 agent 自有页面（listUserTabs 可见集合）。"""
        return [e for e in self.registry.values() if not e.agent_owned]

    async def claim_tab(self, tab_id: str) -> TabEntry:
        """将用户页面纳管为 agent 自有 tab（claimTab 命令后端）。"""
        entry = self.registry.get(tab_id)
        if entry is None:
            raise BrowserCommandFailure("ref_not_found", f"Browser tab '{tab_id}' is unavailable")
        entry.agent_owned = True
        return entry

    async def cdp_session_for(self, entry: TabEntry) -> Any:
        """惰性创建页面级 CDP 会话（导航历史等 Playwright 未覆盖能力）。"""
        if entry.cdp_session is None:
            entry.cdp_session = await self._context.new_cdp_session(entry.page)
        return entry.cdp_session


def shutil_rmtree_quiet(directory: Path) -> None:
    """静默删除目录树（空白档案临时目录清理；失败仅记日志）。"""
    import shutil

    try:
        shutil.rmtree(directory, ignore_errors=True)
    except Exception:  # pragma: no cover - 平台相关防御
        logger.debug("临时浏览器档案清理失败: %s", directory, exc_info=True)
