"""
浏览器抽象层
============

定义浏览器后端的抽象接口与共享数据结构。
BrowserControlPort / BrowserCommand 契约：桌面版（Electron <webview> 桥接）
与托管版（Playwright Chromium）双后端实现同一接口，上层 browser_* 工具
与右栏可视化不感知差异。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class BrowserCommandError(Exception):
    """浏览器命令执行失败。

    一条命令失败不代表浏览器崩溃，
    调用方（工具层/技能）应重新观察页面状态后重试，而不是放弃整个会话。
    """


# 视口安全边界与默认值
MIN_VIEWPORT_WIDTH = 320
MAX_VIEWPORT_WIDTH = 3840
MIN_VIEWPORT_HEIGHT = 320
MAX_VIEWPORT_HEIGHT = 2160
DEFAULT_VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 720

# 导航协议白名单（仅 http/https/about:blank）
ALLOWED_URL_SCHEMES = ("http", "https")


def validate_url(url: str) -> str:
    """校验导航 URL，返回规范化后的值。

    只允许 http:/https: 与精确的 about:blank；file:、data:、javascript:
    及其他 about:* 一律拒绝。
    """
    trimmed = (url or "").strip()
    if trimmed == "about:blank":
        return trimmed
    lowered = trimmed.lower()
    for scheme in ALLOWED_URL_SCHEMES:
        if lowered.startswith(f"{scheme}://"):
            return trimmed
    raise BrowserCommandError(
        f"URL scheme not allowed: {trimmed!r}. Only http:, https: and about:blank are navigable."
    )


def clamp_viewport(width: int, height: int) -> tuple[int, int]:
    """把视口尺寸收敛到安全边界内。"""
    return (
        max(MIN_VIEWPORT_WIDTH, min(MAX_VIEWPORT_WIDTH, int(width))),
        max(MIN_VIEWPORT_HEIGHT, min(MAX_VIEWPORT_HEIGHT, int(height))),
    )


@dataclass
class TabInfo:
    """受控标签页元数据。

    Attributes:
        id: 稳定的标签页 id（本会话内递增，如 "t1"）
        url: 当前 URL
        title: 页面标题
        active: 是否为当前激活标签
    """

    id: str
    url: str = "about:blank"
    title: str = ""
    active: bool = False
    # 导航边界（工具栏前进/回退按钮置灰依据）
    can_go_back: bool = False
    can_go_forward: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "active": self.active,
            "can_go_back": self.can_go_back,
            "can_go_forward": self.can_go_forward,
        }


@dataclass
class ScreenshotData:
    """截图结果。

    Attributes:
        data: JPEG 编码的图像字节
        mime: MIME 类型（image/jpeg）
    """

    data: bytes
    mime: str = "image/jpeg"


@dataclass
class PageState:
    """当前页面状态快照（供 browser_state 事件与状态对账）。"""

    tabs: list[TabInfo] = field(default_factory=list)
    viewport_width: int = DEFAULT_VIEWPORT_WIDTH
    viewport_height: int = DEFAULT_VIEWPORT_HEIGHT

    def to_dict(self) -> dict[str, object]:
        return {
            "tabs": [t.to_dict() for t in self.tabs],
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
        }


class BrowserBackend(ABC):
    """浏览器后端抽象接口。

    两种实现：
        - PlaywrightBackend：托管 Chromium / 系统 Chrome（Web 与 CLI 模式）
        - DesktopBridgeBackend：Electron 主进程 <webview> 控制服务器（桌面模式）
    """

    @property
    @abstractmethod
    def mode(self) -> str:
        """后端模式标识："managed"（Playwright）或 "desktop"（Electron 桥接）。"""

    @abstractmethod
    async def start(self) -> None:
        """启动浏览器（幂等）。"""

    @abstractmethod
    async def aclose(self) -> None:
        """关闭浏览器并释放资源（幂等）。"""

    @abstractmethod
    async def list_tabs(self) -> list[TabInfo]:
        """返回全部受控标签页（含激活标记）。"""

    @abstractmethod
    async def get_state(self) -> PageState:
        """返回当前浏览器状态快照（tabs/viewport；供 browser_state 事件）。"""

    @abstractmethod
    async def new_tab(self, url: str | None = None, wait_load: bool = True) -> TabInfo:
        """新建标签页，可选立即导航。

        wait_load：是否等待页面加载完成再返回（工具路径=True；面板路径
        桌面端=False——create-with-url 由渲染层 src 加载，瞬时回执）。
        """

    @abstractmethod
    async def close_tab(self, tab_id: str) -> None:
        """关闭标签页；关闭最后一个标签时浏览器保持空会话状态。"""

    @abstractmethod
    async def select_tab(self, tab_id: str) -> TabInfo:
        """激活指定标签页并返回其信息。"""

    @abstractmethod
    async def get_active_tab(self) -> TabInfo:
        """返回当前激活标签页（无标签时自动新建）。"""

    @abstractmethod
    async def navigate(self, tab_id: str, url: str) -> TabInfo:
        """在指定标签页导航（等待 domcontentloaded）。"""

    @abstractmethod
    async def reload(self, tab_id: str) -> TabInfo:
        """重新加载指定标签页。"""

    @abstractmethod
    async def go_back(self, tab_id: str) -> TabInfo:
        """后退。"""

    @abstractmethod
    async def go_forward(self, tab_id: str) -> TabInfo:
        """前进。"""

    @abstractmethod
    async def click(self, tab_id: str, ref: str | None = None, x: float | None = None,
                    y: float | None = None, double: bool = False) -> None:
        """点击元素：ref（快照引用）或页面坐标（x,y）二选一。"""

    @abstractmethod
    async def hover(self, tab_id: str, x: float, y: float) -> None:
        """把鼠标移到页面坐标 (x, y)（拾取模式先悬停再点击，供页面脚本取 hovered）。"""

    @abstractmethod
    async def type_text(self, tab_id: str, text: str, ref: str | None = None,
                        submit: bool = False) -> str:
        """输入文本：ref 存在时先聚焦该元素；submit 时追加回车。

        Returns:
            str: 效果说明（替换选区 / 键入 / 追加），供工具输出展示语义。
        """

    @abstractmethod
    async def press_key(self, tab_id: str, key: str) -> None:
        """按下按键（Playwright 键名，如 Enter/ArrowDown/Control+a）。"""

    @abstractmethod
    async def scroll(self, tab_id: str, x: float, y: float, dx: float, dy: float) -> str:
        """在页面坐标 (x, y) 处滚动 (dx, dy) 像素。

        Returns:
            str: 效果说明（滚动后位置）；完全无效时抛 BrowserCommandError。
        """

    @abstractmethod
    async def screenshot(self, tab_id: str, full_page: bool = False) -> ScreenshotData:
        """截取当前视口（或整页）截图。"""

    @abstractmethod
    async def snapshot(self, tab_id: str) -> dict[str, object]:
        """产出带 ref 标记的 ARIA 快照。

        Returns:
            dict: {url, title, yaml, viewport_width, viewport_height}
        """

    @abstractmethod
    async def evaluate(self, tab_id: str, expression: str) -> str:
        """在页面上下文执行 JavaScript 并返回文本化结果。"""

    async def pick(self, tab_id: str, x: float, y: float) -> dict[str, object]:
        """拾取页面坐标处的元素（网页元素选择器）；默认不支持。"""
        raise BrowserCommandError("Element picking is not supported by this browser backend.")

    async def open_devtools(self, tab_id: str) -> None:
        """打开页面调试工具；默认不支持。"""
        raise BrowserCommandError("DevTools is not supported by this browser backend.")

    async def pick_script(self, tab_id: str, script: str) -> dict[str, object]:
        """执行前端下发的元素拾取脚本（Promise 式，用户操作后 resolve）；默认不支持。"""
        raise BrowserCommandError("Element picking is not supported by this browser backend.")

    async def pick_cancel(self, tab_id: str) -> None:
        """取消进行中的元素拾取；默认不支持。"""
        raise BrowserCommandError("Element picking is not supported by this browser backend.")

    @abstractmethod
    async def resize(self, tab_id: str, width: int, height: int) -> tuple[int, int]:
        """调整视口尺寸，返回收敛后的实际尺寸。"""

    @abstractmethod
    async def wait(self, tab_id: str, seconds: float, load: bool = False) -> None:
        """等待：load=True 等待加载完成，否则固定延时。"""

    async def capture_panel_frame(self, tab_id: str) -> ScreenshotData | None:
        """为右栏可视化捕获一帧画面。

        默认实现复用 screenshot；桌面后端可覆盖以处理面板折叠等场景。
        """
        try:
            return await self.screenshot(tab_id)
        except BrowserCommandError:
            return None
