"""浏览器可执行文件与用户数据目录发现
====================================

适配自 browser-use 项目的 chrome 探测逻辑（MIT License），按 IllusionAgent
品牌与渠道命名重写。探测优先级（channel="auto" 时）：

1. 显式配置的 executable_path
2. Playwright 内置 Chromium（%LOCALAPPDATA%/ms-playwright/chromium-*）
3. 系统安装的渠道浏览器（chrome → edge → brave → chromium）

用户数据目录探测（profile="user" 且未显式指定 user_data_dir 时）按渠道
返回各平台默认位置（如 Windows 的 %LOCALAPPDATA%/Google/Chrome/User Data）。

所有探测均为纯路径检查，不启动进程、不读注册表，可安全单测。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Literal

# 渠道类型：与 config.BrowserChannel 保持一致（此处用宽松字符串便于探测层解耦）
Channel = Literal["auto", "chrome", "edge", "brave", "chromium"]

# auto 渠道的探测优先级（chromium 排最后：发行版浏览器更完整）
_AUTO_CHANNEL_PRIORITY: tuple[str, ...] = ("chrome", "edge", "brave", "chromium")


def _expand(path: str) -> Path:
    """展开环境变量与用户目录（Windows %LOCALAPPDATA% 等手动展开，兼容 GUI 进程）。"""
    expanded = os.path.expandvars(os.path.expanduser(path))
    return Path(expanded)


def _windows_channel_paths(channel: str) -> list[Path]:
    """Windows 下各渠道的可执行文件候选路径（按优先级）。"""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LocalAppData", str(Path.home() / "AppData" / "Local"))
    candidates: dict[str, list[str]] = {
        "chrome": [
            rf"{program_files}\Google\Chrome\Application\chrome.exe",
            rf"{program_files_x86}\Google\Chrome\Application\chrome.exe",
            rf"{local_appdata}\Google\Chrome\Application\chrome.exe",
        ],
        "edge": [
            rf"{program_files}\Microsoft\Edge\Application\msedge.exe",
            rf"{program_files_x86}\Microsoft\Edge\Application\msedge.exe",
        ],
        "brave": [
            rf"{program_files}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{program_files_x86}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{local_appdata}\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        "chromium": [
            rf"{program_files}\Chromium\Application\chrome.exe",
            rf"{local_appdata}\Chromium\Application\chrome.exe",
        ],
    }
    return [_expand(p) for p in candidates.get(channel, [])]


def _posix_channel_paths(channel: str) -> list[Path]:
    """macOS / Linux 下各渠道的可执行文件候选路径（按优先级）。"""
    darwin = sys.platform == "darwin"
    if darwin:
        applications = "/Applications"
        home = Path.home()
        candidates: dict[str, list[str]] = {
            "chrome": [
                f"{applications}/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ],
            "edge": [
                f"{applications}/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ],
            "brave": [
                f"{applications}/Brave Browser.app/Contents/MacOS/Brave Browser",
            ],
            "chromium": [
                f"{applications}/Chromium.app/Contents/MacOS/Chromium",
            ],
        }
    else:
        binaries: dict[str, list[str]] = {
            "chrome": ["google-chrome", "google-chrome-stable"],
            "edge": ["microsoft-edge", "microsoft-edge-stable"],
            "brave": ["brave-browser", "brave"],
            "chromium": ["chromium", "chromium-browser"],
        }
        candidates = {channel: list(binaries.get(channel, []))}
    paths: list[Path] = []
    for entry in candidates.get(channel, []):
        if entry.startswith("/"):
            paths.append(Path(entry))
        else:
            found = shutil.which(entry)
            if found:
                paths.append(Path(found))
    return paths


def _playwright_bundled_chromium() -> Path | None:
    """返回 Playwright 内置 Chromium 可执行文件（存在时）。

    Playwright 浏览器目录结构：ms-playwright/chromium-<build>/chrome-win/chrome.exe
    （Windows）、chrome-mac/…（macOS）、chrome-linux/chrome（Linux）。
    多版本共存时取字典序最大的构建号。
    """
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if base:
        roots = [_expand(base)]
    else:
        # 与 Playwright 官方默认安装位置一致：Windows 在 %LOCALAPPDATA%，
        # Linux 在 ~/.cache，macOS 在 ~/Library/Caches
        roots = [
            _expand(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")) + "/ms-playwright"),
            _expand("~/.cache/ms-playwright"),
            _expand("~/Library/Caches/ms-playwright"),
        ]
    seen: set[Path] = set()
    for root in roots:
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        builds = sorted(
            (d for d in root.iterdir() if d.is_dir() and d.name.startswith("chromium-")),
            key=lambda d: d.name,
            reverse=True,
        )
        for build in builds:
            for relative in (
                "chrome-win/chrome.exe",
                "chrome-win/chrome",
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                "chrome-linux/chrome",
            ):
                candidate = build / relative
                if candidate.is_file():
                    return candidate
    return None


def find_browser_executable(channel: Channel = "auto", explicit_path: str = "") -> Path | None:
    """探测可用的浏览器可执行文件。

    Args:
        channel: 渠道（auto 按内置优先级探测）。
        explicit_path: 显式路径，非空且存在时直接返回。

    Returns:
        存在的可执行文件路径；全部未命中时 None（调用方提示安装）。
    """
    if explicit_path:
        candidate = _expand(explicit_path)
        if candidate.is_file():
            return candidate
    if channel == "chromium":
        # 显式指定 chromium 时优先发行版，其次 Playwright 内置
        for candidate in _windows_channel_paths("chromium") if sys.platform == "win32" else _posix_channel_paths("chromium"):
            if candidate.is_file():
                return candidate
        return _playwright_bundled_chromium()
    channels = _AUTO_CHANNEL_PRIORITY if channel == "auto" else (channel,)
    for ch in channels:
        candidates = _windows_channel_paths(ch) if sys.platform == "win32" else _posix_channel_paths(ch)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    # 渠道浏览器都未命中：Playwright 内置 Chromium 兜底
    return _playwright_bundled_chromium()


def _windows_user_data_dirs(channel: str) -> list[Path]:
    """Windows 下各渠道的默认用户数据目录。"""
    local_appdata = os.environ.get("LocalAppData", str(Path.home() / "AppData" / "Local"))
    candidates: dict[str, str] = {
        "chrome": rf"{local_appdata}\Google\Chrome\User Data",
        "edge": rf"{local_appdata}\Microsoft\Edge\User Data",
        "brave": rf"{local_appdata}\BraveSoftware\Brave-Browser\User Data",
        "chromium": rf"{local_appdata}\Chromium\User Data",
    }
    return [_expand(candidates[channel])]


def _posix_user_data_dirs(channel: str) -> list[Path]:
    """macOS / Linux 下各渠道的默认用户数据目录。"""
    home = Path.home()
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        candidates: dict[str, Path] = {
            "chrome": support / "Google" / "Chrome",
            "edge": support / "Microsoft Edge",
            "brave": support / "BraveSoftware" / "Brave-Browser",
            "chromium": support / "Chromium",
        }
        return [candidates[channel]]
    config = home / ".config"
    candidates = {
        "chrome": config / "google-chrome",
        "edge": config / "microsoft-edge",
        "brave": config / "BraveSoftware" / "Brave-Browser",
        "chromium": config / "chromium",
    }
    return [candidates[channel]]


def find_user_data_dir(channel: Channel, resolved_executable: Path | None = None) -> Path | None:
    """探测渠道对应的默认用户数据目录（存在且含 Local State 时返回）。

    Args:
        channel: 渠道；auto 时按可执行文件反推渠道，反推不出按优先级探测。
        resolved_executable: 已解析的可执行文件路径，用于 auto 渠道反推。
    """
    inferred = _infer_channel_from_executable(resolved_executable) if resolved_executable else None
    channels: tuple[str, ...] = (inferred,) if inferred else _AUTO_CHANNEL_PRIORITY
    if channel != "auto":
        channels = (channel,)
    for ch in channels:
        if not ch:
            continue
        dirs = _windows_user_data_dirs(ch) if sys.platform == "win32" else _posix_user_data_dirs(ch)
        for directory in dirs:
            # Local State 是用户数据目录的标志文件，避免误报空目录
            if (directory / "Local State").is_file():
                return directory
    return None


def _infer_channel_from_executable(executable: Path) -> str | None:
    """从可执行文件路径反推渠道名（用于 auto 渠道的用户数据目录定位）。"""
    lowered = str(executable).lower()
    for channel, marker in (
        ("chrome", "chrome"),
        ("edge", "msedge"),
        ("brave", "brave"),
        ("chromium", "chromium"),
    ):
        if marker in lowered:
            return channel
    return None
