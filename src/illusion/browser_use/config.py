"""Browser Use 配置模型
======================

settings.json 中的 ``browser`` 配置段。默认值策略：

- ``enabled=False``：功能默认关闭，用户显式启用后才注入 MCP/skills/broker。
- ``profile="blank"``：默认使用「空白」配置档案（一次性临时用户数据目录），
  不读取用户浏览器的历史/Cookie/登录态；通过 ``profile="user"``（或 CLI
  ``--browser-profile user``）切换为用户真实浏览器数据。
- ``headless=True``：终端场景静默执行，不弹出窗口。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 浏览器配置档案：blank=一次性临时目录；user=用户真实浏览器数据
BrowserProfile = Literal["blank", "user"]

# 受管浏览器启动模式：headless=无头（静默）；headed=带窗口
BrowserLaunchMode = Literal["headless", "headed"]

# 浏览器渠道：auto 按优先级自动探测（chrome → edge → brave → chromium → playwright 内置）
BrowserChannel = Literal["auto", "chrome", "edge", "brave", "chromium"]

_CHANNEL_VALUES: tuple[str, ...] = ("auto", "chrome", "edge", "brave", "chromium")
_PROFILE_VALUES: tuple[str, ...] = ("blank", "user")


class BrowserViewport(BaseModel):
    """默认视口大小（CSS 像素）。

    与 browser-client 的 BROWSER_VIEWPORT_LIMITS 保持一致（320..3840 / 320..2160）。
    """

    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=320, le=2160)


class BrowserSettings(BaseModel):
    """Browser Use 子系统配置。

    Attributes:
        enabled: 总开关。False 时整个子系统不启动（不注入 MCP 服务器、
            不注册 skills、不启动 broker、不拉起浏览器）。
        profile: 浏览器配置档案。"blank" 使用一次性临时用户数据目录（默认，
            干净环境）；"user" 使用用户真实浏览器数据目录（含登录态/Cookie，
            需要先关闭正在运行的浏览器以释放配置文件锁）。
        user_data_dir: profile="user" 时使用的自定义数据目录。为空时自动探测
            当前渠道的默认用户数据目录（如 %LOCALAPPDATA%/Google/Chrome/User Data）。
        headless: True 无头运行（静默，终端场景推荐）；False 弹出浏览器窗口。
        channel: 浏览器渠道。auto 按优先级探测已安装的浏览器。
        executable_path: 显式指定浏览器可执行文件路径（优先级最高）。
        cdp_url: 连接已有浏览器的 CDP 端点（http://host:port）。设置后跳过
            受管启动，直接 attach（此时 profile/user_data_dir 不生效）。
        viewport: 默认视口大小。
        keep_alive_minutes: 浏览器空闲自动回收时间（分钟）。0 表示不回收。
        stream_interval_ms: Web 端实时画面的最小推送间隔（毫秒），也是画面
            内容变化检测的最小采样间隔。
        screenshot_quality: 实时画面 JPEG 质量（1-100）。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    profile: BrowserProfile = "blank"
    user_data_dir: str = ""
    headless: bool = True
    channel: BrowserChannel = "auto"
    executable_path: str = ""
    cdp_url: str = ""
    viewport: BrowserViewport = Field(default_factory=BrowserViewport)
    keep_alive_minutes: int = Field(default=30, ge=0, le=24 * 60)
    stream_interval_ms: int = Field(default=800, ge=100, le=60_000)
    screenshot_quality: int = Field(default=60, ge=1, le=100)

    @field_validator("channel", mode="before")
    @classmethod
    def _normalize_channel(cls, value: object) -> object:
        """渠道名大小写兼容（"Chrome" → "chrome"）。"""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _CHANNEL_VALUES:
                return normalized
        return value

    @field_validator("profile", mode="before")
    @classmethod
    def _normalize_profile(cls, value: object) -> object:
        """配置档案名大小写兼容（"User" → "user"）。"""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _PROFILE_VALUES:
                return normalized
        return value

    @field_validator("cdp_url", mode="before")
    @classmethod
    def _strip_cdp_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    def with_overrides(
        self,
        *,
        profile: str | None = None,
        headless: bool | None = None,
        enabled: bool | None = None,
    ) -> BrowserSettings:
        """应用会话级覆盖（CLI 参数），返回新实例；None 表示不覆盖。

        Args:
            profile: CLI --browser-profile 传入的档案覆盖（"blank"/"user"）。
            headless: CLI --browser-use headless|headed 传入的模式覆盖。
            enabled: CLI --browser-use off|auto 传入的启用覆盖。
        """
        updates: dict[str, object] = {}
        if profile is not None and profile in _PROFILE_VALUES:
            updates["profile"] = profile
        if headless is not None:
            updates["headless"] = headless
        if enabled is not None:
            updates["enabled"] = enabled
        if not updates:
            return self
        return self.model_copy(update=updates)
