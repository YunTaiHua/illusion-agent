"""
浏览器内核解析
==============

内核解析链：
Playwright 自带 Chromium → 系统 Chrome → 系统 Edge。
不在此处下载浏览器：Chromium 缺失时用 `illusion browser setup` 安装，
或经 channel= 兜底到系统浏览器。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from illusion.browser.base import BrowserCommandError


def _program_files_candidates() -> list[Path]:
    """Windows 常见浏览器安装路径。"""
    roots: list[str] = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        value = os.environ.get(var)
        if value:
            roots.append(value)
    candidates: list[Path] = []
    for root in roots:
        candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for root in roots[:2]:  # Edge 只在 Program Files 下
        candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for root in roots:
        candidates.append(Path(root) / "Chromium" / "Application" / "chrome.exe")
    return candidates


def _posix_candidates() -> list[Path]:
    """macOS / Linux 常见浏览器路径。"""
    return [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/microsoft-edge"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
    ]


def find_system_browser() -> Path | None:
    """按 Chrome → Edge → Chromium 顺序探测系统浏览器可执行文件。"""
    candidates = _program_files_candidates() if os.name == "nt" else _posix_candidates()
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    # PATH 兜底（Linux 常见）
    for name in ("google-chrome", "google-chrome-stable", "microsoft-edge", "chromium",
                 "chromium-browser"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


async def resolve_launch_kwargs(pw: Any, kernel: str = "auto") -> dict[str, Any]:
    """解析 Playwright chromium.launch 的内核参数。

    Args:
        pw: 已启动的 async_playwright 对象（含 .chromium）。
        kernel: "auto" | "chromium" | "chrome" | "msedge"。

    Returns:
        dict: 传给 chromium.launch 的 kwargs（executable_path / channel）。

    Raises:
        BrowserCommandError: 找不到任何可用内核。
    """
    # 显式指定内核时直接使用对应 channel（playwright 内置 channel 探测）
    if kernel in ("chrome", "msedge"):
        return {"channel": kernel}
    if kernel == "chromium":
        bundled = _bundled_chromium_path(pw)
        if bundled is None:
            raise BrowserCommandError(
                "Playwright bundled Chromium not found. Run `illusion browser setup` once, "
                "or set browser.kernel to chrome/msedge to use a system browser."
            )
        return {"executable_path": str(bundled)}
    # auto：Playwright Chromium → 系统 Chrome/Edge
    bundled = _bundled_chromium_path(pw)
    if bundled is not None:
        return {"executable_path": str(bundled)}
    system = find_system_browser()
    if system is not None:
        return {"executable_path": str(system)}
    raise BrowserCommandError(
        "No browser available: Playwright Chromium is not installed and no system "
        "Chrome/Edge/Chromium was found. Run `illusion browser setup` once to download "
        "Chromium, or point browser.kernel at an installed system browser."
    )


def _bundled_chromium_path(pw: Any) -> Path | None:
    """返回 Playwright 自带 Chromium 的可执行路径（未安装时 None）。"""
    try:
        path = Path(pw.chromium.executable_path)
    except Exception:  # noqa: BLE001 — driver 异常统一视为未安装
        return None
    return path if path.is_file() else None


# ---------------------------------------------------------------------------
# 代理解析
# ---------------------------------------------------------------------------
# Playwright 无头 Chromium 在 Windows 上不继承系统代理（已知行为），
# 需要显式传 proxy 参数。解析顺序（proxy="auto" 时）：
#   HTTP(S)_PROXY 环境变量 → Windows 注册表系统代理（ProxyEnable/ProxyServer）


def resolve_proxy(proxy_setting: str = "auto") -> dict[str, str] | None:
    """解析 Playwright launch 的 proxy 参数。

    Args:
        proxy_setting: "auto" | "off" | 代理 URL（如 "http://127.0.0.1:7890"）。

    Returns:
        dict | None: {"server": "..."} 或 None（不走代理）。
    """
    setting = (proxy_setting or "").strip()
    if setting.lower() in ("off", "none", "direct", ""):
        return None
    if setting.lower() != "auto":
        return {"server": setting}
    # auto：环境变量优先
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(var)
        if value:
            return {"server": value}
    # Windows 系统代理（Clash/v2rayN 等写注册表）
    if sys.platform == "win32":
        server = _windows_system_proxy()
        if server:
            return {"server": server if "://" in server else f"http://{server}"}
    return None


def _windows_system_proxy() -> str | None:
    """读取 Windows 用户级系统代理（WinINET 注册表）。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enable:
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        winreg.CloseKey(key)
    except OSError:
        return None
    server = (server or "").strip()
    if not server:
        return None
    # "http=host:port;https=host:port" 形式：优先取 https/http 段
    if "=" in server:
        parts: dict[str, str] = {}
        for item in server.split(";"):
            if "=" not in item:
                continue
            name, _, value = item.partition("=")
            parts[name] = value
        server = parts.get("https") or parts.get("http") or ""
        server = server.strip()
    return server or None
