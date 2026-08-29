"""Browser Use 命令协议类型
==========================

定义 browser-client（node_repl 内核侧）与 Python 宿主之间的命令信封与结果
结构。字段集合与 browser-use 插件 shared/contracts 中的 zod schema 逐一对齐
（strict 模式：不允许出现协议外字段，避免下游校验拒绝）：

- ``CommandResult``: 统一结果信封 ``{ok, ..., elapsedMs}``
- ``TabSummary``: tab 摘要（tabId/url/title/viewport/active）
- ``PageState``: getState 返回的页面状态
- ``SnapshotElement`` / ``Snapshot``: dom_cua 可见元素快照
- ``BrowserCommandError``: 协议化错误（code ∈ BrowserErrorCode）

协议常量（视口边界、超时预算）同样集中在本模块。
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# browser-client BROWSER_VIEWPORT_LIMITS：自由尺寸视口的安全边界
VIEWPORT_MIN_WIDTH = 320
VIEWPORT_MAX_WIDTH = 3840
VIEWPORT_MIN_HEIGHT = 320
VIEWPORT_MAX_HEIGHT = 2160

# 常规 locator/evaluate/导航等待的默认超时预算（browser-client 与 docs 约定 3000ms）
ROUTINE_TIMEOUT_MS = 3_000
# 下载事件等待上限（docs/api.json operationTimeout：download 可达 120000ms）
DOWNLOAD_TIMEOUT_MS = 120_000
# 导航类命令的硬上限（goto 等，非「常规等待」预算）
NAVIGATION_TIMEOUT_MS = 30_000

BrowserErrorCode = Literal[
    "backend_unavailable",
    "capability_unsupported",
    "duplicate_request_id",
    "ref_not_found",
    "navigation_blocked",
    "timeout",
    "renderer_unreachable",
    "cancelled",
    "execution_error",
]

LifecycleStatus = Literal["active", "deliverable", "handoff"]
MouseButton = Literal["left", "right", "middle"]
KeyboardModifier = Literal["Alt", "Control", "ControlOrMeta", "Meta", "Shift"]
LoadState = Literal["load", "domcontentloaded", "networkidle"]
WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]
WaitForState = Literal["attached", "detached", "visible", "hidden"]


class BrowserViewportSize(BaseModel):
    """CSS 视口尺寸（协议要求正整数）。"""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ElementRect(BaseModel):
    """元素包围盒（视口 CSS 像素，x/y 为左上角）。"""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    width: float
    height: float


class TabSummary(BaseModel):
    """tab 摘要。url/title 协议要求必须为字符串（可为空串）。"""

    model_config = ConfigDict(extra="forbid")

    tabId: str
    url: str = ""
    title: str = ""
    viewport: BrowserViewportSize
    active: bool | None = None
    lifecycle: LifecycleStatus | None = None


class PageState(BaseModel):
    """getState 返回的页面状态。"""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    canGoBack: bool
    canGoForward: bool
    scrollX: float | None = None
    scrollY: float | None = None
    viewportWidth: int | None = None
    viewportHeight: int | None = None


class SnapshotElement(BaseModel):
    """dom_cua 可见元素条目。

    ref 是快照句柄（click/type/hover/select/check 等 ref 操作的定位依据）；
    selector/xpath 供模型构造稳定 locator；attributes 只包含有界稳定属性。
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    tag: str
    role: str | None = None
    name: str | None = None
    text: str | None = None
    value: str | None = None
    disabled: bool | None = None
    checked: bool | None = None
    selector: str
    xpath: str
    rect: ElementRect
    inViewport: bool
    parentRef: str | None = None
    framePath: str | None = None
    attributes: dict[str, str] | None = None


class SnapshotDomNode(BaseModel):
    """快照的语义 DOM 骨架节点（有界；供模型先读页面语义再看动作细节）。"""

    model_config = ConfigDict(extra="forbid")

    tag: str
    depth: int = Field(ge=0)
    inViewport: bool
    ref: str | None = None
    role: str | None = None
    name: str | None = None
    text: str | None = None


class Snapshot(BaseModel):
    """get_visible_dom / snapshot 命令结果。"""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    dom: list[SnapshotDomNode] | None = None
    domTruncated: bool | None = None
    elements: list[SnapshotElement]
    truncated: bool


class JsDialogInfo(BaseModel):
    """JS 弹窗信息（getDialog 返回）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["alert", "confirm", "prompt", "beforeunload"]
    message: str
    defaultPrompt: str | None = None


class CommandErrorPayload(BaseModel):
    """结果信封的 error 载荷。

    sideEffect="uncertain" 表示命令可能在失败前已产生副作用（调用方不应
    盲目重试）。
    """

    model_config = ConfigDict(extra="forbid")

    code: BrowserErrorCode
    message: str
    sideEffect: Literal["none", "uncertain"] | None = None


class ScreenshotPayload(BaseModel):
    """截图载荷。协议约束 mimeType 固定为 image/png。"""

    model_config = ConfigDict(extra="forbid")

    base64: str
    mimeType: Literal["image/png"] = "image/png"


class CommandResult(BaseModel):
    """统一命令结果信封（strict：字段与插件 zod schema 一一对应）。

    构造请使用 :func:`ok_result` / :func:`error_result`，确保 elapsedMs 与
    meta 字段始终完整。
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    state: PageState | None = None
    snapshot: Snapshot | None = None
    image: ScreenshotPayload | None = None
    tabs: list[TabSummary] | None = None
    userTabs: list[dict[str, str]] | None = None
    tab: TabSummary | None = None
    value: Any = None
    element: SnapshotElement | None = None
    dialog: JsDialogInfo | None = None
    error: CommandErrorPayload | None = None
    meta: dict[str, Any] | None = None
    elapsedMs: int = Field(ge=0, default=0)


class BrowserCommandFailure(Exception):
    """协议化命令失败。

    commands 层内部用异常表达失败路径，execute 顶层统一捕获并折叠为
    ``error_result``，保证任何命令都以信封结束而不向外抛裸异常。
    """

    def __init__(self, code: BrowserErrorCode, message: str, *, side_effect_uncertain: bool = False) -> None:
        super().__init__(message)
        self.code: BrowserErrorCode = code
        self.message = message
        self.side_effect_uncertain = side_effect_uncertain


def ok_result(started: float, **payload: Any) -> dict[str, Any]:
    """构造成功信封。started 为 time.perf_counter() 起点（自动计算 elapsedMs）。"""
    result = CommandResult(ok=True, elapsedMs=_elapsed_ms(started), **payload)
    return _dump_result(result)


def error_result(
    started: float,
    code: BrowserErrorCode,
    message: str,
    *,
    side_effect_uncertain: bool = False,
) -> dict[str, Any]:
    """构造失败信封。"""
    result = CommandResult(
        ok=False,
        elapsedMs=_elapsed_ms(started),
        error=CommandErrorPayload(
            code=code,
            message=message,
            sideEffect="uncertain" if side_effect_uncertain else None,
        ),
    )
    return _dump_result(result)


def failure_to_result(started: float, failure: BrowserCommandFailure) -> dict[str, Any]:
    """把 BrowserCommandFailure 折叠为失败信封。"""
    return error_result(
        started,
        failure.code,
        failure.message,
        side_effect_uncertain=failure.side_effect_uncertain,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _dump_result(result: CommandResult) -> dict[str, Any]:
    """序列化信封，剔除值为 None 的 optional 字段（协议 strict 校验更友好）。"""
    return {key: value for key, value in result.model_dump().items() if value is not None}
