"""
cua-driver (cua) 二进制的发现、下载、版本检查与更新模块。

cua 是 Computer Use 功能依赖的驱动二进制（来源项目：github.com/trycua/cua）。
下载/缓存风格与 ripgrep 一致，但二进制统一存放在 ``~/.illusion/bin/`` 目录
（rg 与 cua 共用该目录）。

更新策略：rg 已停止维护（版本固定 14.1.1）；cua 仍在持续发布新版本，
因此额外提供版本检查与更新能力（GitHub Releases API）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class CuaNotFoundError(Exception):
    """cua 二进制不可用时抛出"""


class CuaError(Exception):
    """cua 执行失败时抛出"""


# GitHub Releases 下载地址模板（cua-driver 组件，稳定通道）
CUA_REPO = "trycua/cua"
CUA_TAG_PREFIX = "cua-driver-rs-v"

# 回退版本：GitHub API 不可用（限流/断网）时使用，与上游安装脚本的
# "baked version" 策略一致——保证首次下载不依赖 API 也能成功；
# 之后可通过 /computer update 或 web 设置表单升级到实际最新版。
DEFAULT_CUA_VERSION = "0.22.1"

CUA_DOWNLOAD_URL = "https://github.com/{repo}/releases/download/{tag}/{asset}"

# 平台键 -> (release asset 标签, 归档格式, 归档内二进制子路径)
# Windows 使用 zip，其余使用 tar.gz；macOS 提供 universal 单归档。
PLATFORM_MAP: dict[str, tuple[str, str, str]] = {
    "x64-win32": ("windows-x86_64", "zip", "cua-driver-rs-{v}-windows-x86_64/cua-driver.exe"),
    "arm64-win32": ("windows-arm64", "zip", "cua-driver-rs-{v}-windows-arm64/cua-driver.exe"),
    "x64-darwin": ("darwin-universal", "tar.gz", "cua-driver-rs-{v}-darwin-universal/cua-driver"),
    "arm64-darwin": ("darwin-universal", "tar.gz", "cua-driver-rs-{v}-darwin-universal/cua-driver"),
    "x64-linux": ("linux-x86_64", "tar.gz", "cua-driver"),
    "arm64-linux": ("linux-arm64", "tar.gz", "cua-driver"),
}


def get_platform_key() -> str:
    """获取当前平台的键名（与 ripgrep 模块保持同构）。"""
    machine = platform.machine().lower()
    plat = sys.platform

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = machine

    return f"{arch}-{plat}"


def get_cua_binary_name(platform_key: str) -> str:
    """获取 cua 二进制文件名。Windows 上为 ``cua-driver.exe``，其他平台为 ``cua-driver``。"""
    if platform_key.endswith("-win32"):
        return "cua-driver.exe"
    return "cua-driver"


def get_bin_dir() -> str:
    """获取二进制缓存目录（rg 与 cua 共用，替代旧的 ripgrep 专属目录）。

    Returns:
        缓存目录路径：~/.illusion/bin/
    """
    from illusion.config.paths import get_config_dir

    return str(get_config_dir() / "bin")


def find_cua_path() -> str | None:
    """查找 cua 二进制路径。

    优先级：
    1. 环境变量 ILLUSION_CUA_PATH
    2. 本地缓存 ~/.illusion/bin/cua-driver
    3. 系统 PATH

    Returns:
        cua 二进制路径；未找到时返回 None（不抛出）
    """
    # 1. 检查环境变量
    env_path = os.environ.get("ILLUSION_CUA_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    # 2. 检查本地缓存目录
    bin_dir = get_bin_dir()
    platform_key = get_platform_key()
    binary_name = get_cua_binary_name(platform_key)
    cache_path = os.path.join(bin_dir, binary_name)
    if os.path.exists(cache_path):
        return cache_path

    # 3. 检查系统 PATH
    system_path = shutil.which("cua-driver")
    if system_path:
        return system_path

    return None


def expected_bin_path() -> str:
    """返回 bin 目录中期望的 cua 二进制路径（可能尚不存在）。"""
    return os.path.join(get_bin_dir(), get_cua_binary_name(get_platform_key()))


def _build_download_info(version: str) -> tuple[str, str, str]:
    """根据版本构建下载信息。

    Args:
        version: 版本号（不含 v 前缀）

    Returns:
        (下载 URL, 归档格式, 二进制在归档内的相对子路径)

    Raises:
        CuaNotFoundError: 当前平台不支持
    """
    platform_key = get_platform_key()
    if platform_key not in PLATFORM_MAP:
        raise CuaNotFoundError(f"不支持的平台: {platform_key}")

    arch_label, archive_format, inner_path = PLATFORM_MAP[platform_key]
    tag = f"{CUA_TAG_PREFIX}{version}"

    if archive_format == "zip":
        asset_name = f"cua-driver-rs-{version}-{arch_label}.zip"
    elif arch_label == "darwin-universal":
        asset_name = f"cua-driver-rs-{version}-darwin-universal.tar.gz"
    else:
        asset_name = f"cua-driver-rs-{version}-{arch_label}-binary.tar.gz"

    url = CUA_DOWNLOAD_URL.format(repo=CUA_REPO, tag=tag, asset=asset_name)
    return url, archive_format, inner_path.format(v=version)


def get_latest_version(timeout: float = 15.0) -> str | None:
    """查询最新的稳定版 cua-driver 版本。

    优先使用已安装的 cua 二进制自带 ``check-update --json``（官方维护机制，
    内部带缓存），二进制缺失或查询失败时回退到 GitHub Releases API
    （只匹配 ``cua-driver-rs-v*`` 稳定版标签，忽略 draft 与 nightly）。

    Args:
        timeout: GitHub API 请求超时（秒）

    Returns:
        str | None: 最新稳定版本号（如 "0.22.1"）；查询失败时返回 None
    """
    # 1. 优先使用 cua 二进制自带的 check-update（有 20h 缓存，避免频繁触 API）
    from_binary = _latest_version_from_binary()
    if from_binary:
        return from_binary
    return _latest_version_from_api(timeout)


def _latest_version_from_binary(timeout: float = 15.0) -> str | None:
    """通过 ``cua-driver check-update --json`` 查询最新版本。"""
    path = find_cua_path()
    if not path:
        return None
    try:
        import json

        result = subprocess.run(
            [path, "check-update", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return None
    latest = payload.get("latest_version")
    return str(latest).strip() if latest else None


def _latest_version_from_api(timeout: float = 15.0) -> str | None:
    """通过 GitHub Releases API 查询最新稳定版（回退路径）。"""
    api_url = f"https://api.github.com/repos/{CUA_REPO}/releases?per_page=100"
    try:
        with urllib.request.urlopen(api_url, timeout=timeout) as resp:
            releases = resp.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    import json

    try:
        items = json.loads(releases)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(items, list):
        return None

    versions: list[tuple[tuple[int, int, int], str]] = []
    pattern = re.compile(rf"^{re.escape(CUA_TAG_PREFIX)}(\d+)\.(\d+)\.(\d+)$")
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("draft"):
            continue
        tag = item.get("tag_name", "")
        match = pattern.match(tag)
        if not match:
            continue
        key = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        versions.append((key, tag[len(CUA_TAG_PREFIX):]))
    if not versions:
        return None
    versions.sort(key=lambda item: item[0])
    return versions[-1][1]


def extract_archive(archive_path: str, extract_dir: str, inner_path: str) -> None:
    """从归档中解压出 cua 二进制并移动到 extract_dir 根目录。

    Args:
        archive_path: 归档文件路径
        extract_dir: 解压目标目录
        inner_path: 二进制在归档内的相对路径
    """
    if archive_path.endswith(".zip"):
        import zipfile

        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extract(inner_path, extract_dir)
    else:
        import tarfile

        with tarfile.open(archive_path, "r:gz") as tf:
            member = tf.getmember(inner_path)
            tf.extract(member, extract_dir)

    extracted = os.path.join(extract_dir, inner_path.replace("/", os.sep))
    target = os.path.join(extract_dir, os.path.basename(inner_path))
    if extracted != target:
        os.rename(extracted, target)


def download_cua_binary(version: str | None = None) -> str:
    """下载 cua 二进制到 bin 缓存目录。

    Args:
        version: 指定版本（None 时尝试最新稳定版，API 不可用时回退到
            DEFAULT_CUA_VERSION）

    Returns:
        下载后的 cua 二进制路径

    Raises:
        CuaNotFoundError: 平台不支持或下载失败时
    """
    import tempfile

    if version is None:
        version = get_latest_version()
        if not version:
            # GitHub API 不可用（限流/断网）时回退到固定版本，
            # 保证首次安装不依赖 API（与上游安装脚本的 baked version 一致）
            version = DEFAULT_CUA_VERSION
            logger.warning(
                "无法解析最新 cua-driver 版本，回退到固定版本 %s", version
            )

    url, archive_format, inner_path = _build_download_info(version)
    platform_key = get_platform_key()
    binary_name = get_cua_binary_name(platform_key)
    bin_dir = get_bin_dir()

    os.makedirs(bin_dir, exist_ok=True)

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{archive_format}") as tmp:
            tmp_path = tmp.name
            try:
                urllib.request.urlretrieve(url, tmp_path)
            except OSError as exc:
                raise CuaNotFoundError(f"下载 cua 失败: {exc}")

        extract_archive(tmp_path, bin_dir, inner_path)

        # 设置权限（非 Windows）
        cua_path = os.path.join(bin_dir, binary_name)
        if sys.platform != "win32":
            os.chmod(cua_path, 0o755)

        return cua_path
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def ensure_cua_binary() -> str:
    """确保 cua 二进制可用，返回二进制路径。

    优先级：env > bin 缓存 > PATH > download（最新稳定版）

    Returns:
        cua 二进制路径

    Raises:
        CuaNotFoundError: cua 不可用时
    """
    existing = await asyncio.to_thread(find_cua_path)
    if existing:
        return existing
    # 涉及网络与磁盘解压，必须放线程池中执行
    return await asyncio.to_thread(download_cua_binary)


def _run_version_command() -> str | None:
    """运行 cua-driver --version 获取本地版本。

    Returns:
        str | None: 版本号（如 "0.22.1"）；二进制缺失或执行失败时返回 None
    """
    path = find_cua_path()
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or "").strip()
    # 输出形如 "cua-driver 0.22.1" 或带 target 后缀，提取首个 x.y.z 段
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output)
    if not match:
        return output.splitlines()[0] if output else None
    return match.group(0)


def get_local_version() -> str | None:
    """获取本地 cua 二进制版本号（同步封装，供斜杠指令/web 路由使用）。"""
    return _run_version_command()


def check_update() -> dict[str, Any]:
    """检查 cua 是否有可用更新。

    Returns:
        dict: 含 local_version / latest_version / update_available 的摘要。
            local_version 为 None 表示未安装；latest_version 为 None 表示
            网络查询失败。
    """
    local = _run_version_command()
    latest = get_latest_version()
    update_available = bool(
        local and latest and _compare_versions(latest, local) > 0
    )
    return {
        "local_version": local,
        "latest_version": latest,
        "update_available": update_available,
    }


def _compare_versions(a: str, b: str) -> int:
    """按语义化版本比较两个版本号（a > b 返回正数，相等返回 0，否则负数）。"""

    def _key(v: str) -> tuple[int, int, int]:
        parts = re.findall(r"\d+", v)
        nums = [int(part) for part in parts[:3]]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums)  # type: ignore[return-value]

    try:
        ka, kb = _key(a), _key(b)
    except (TypeError, ValueError):
        return 0
    return (ka > kb) - (ka < kb)


async def update_cua_binary() -> dict[str, Any]:
    """更新 cua 二进制到最新稳定版。

    仅在存在可用更新时执行下载；无更新或二进制缺失时给出说明。

    Returns:
        dict: 更新结果摘要（updated / local_version / latest_version）
    """
    current = await asyncio.to_thread(_run_version_command)
    latest = await asyncio.to_thread(get_latest_version)
    if not latest:
        return {"updated": False, "error": "无法解析最新版本，请检查网络"}
    if current and _compare_versions(latest, current) <= 0:
        return {"updated": False, "local_version": current, "latest_version": latest}

    # 执行更新（覆盖 bin 目录中的二进制）
    await asyncio.to_thread(download_cua_binary, latest)
    new_local = await asyncio.to_thread(_run_version_command)
    return {"updated": True, "local_version": new_local, "latest_version": latest}
