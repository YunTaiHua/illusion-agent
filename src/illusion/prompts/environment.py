"""
环境检测模块
============

本模块实现系统提示词构建所需的环境检测功能。

主要功能：
    - 检测操作系统和版本
    - 检测用户 shell
    - 检测 Git 仓库信息
    - 收集完整的环境信息快照

使用示例：
    >>> from illusion.prompts.environment import get_environment_info, EnvironmentInfo
    >>> env = get_environment_info(cwd="/path/to/project")
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from illusion.config.paths import get_config_dir_override


@dataclass
class EnvironmentInfo:
    """当前运行时环境的信息快照
    
    包含操作系统、shell、平台、工作目录、日期和 Git 信息等。
    
    Attributes:
        os_name: 操作系统名称
        os_version: 操作系统版本
        platform_machine: 平台架构
        shell: 用户 shell
        cwd: 工作目录
        home_dir: 主目录
        date: 当前日期
        python_version: Python 版本
        is_git_repo: 是否为 Git 仓库
        git_branch: Git 分支名称
        hostname: 主机名
        extra: 额外信息字典
    """

    os_name: str  # 操作系统名称
    os_version: str  # 操作系统版本
    platform_machine: str  # 平台架构
    shell: str  # 用户 shell
    cwd: str  # 工作目录
    home_dir: str  # 主目录
    date: str  # 当前日期
    python_version: str  # Python 版本
    is_git_repo: bool  # 是否为 Git 仓库
    git_branch: str | None = None  # Git 分支名称
    hostname: str = ""  # 主机名
    extra: dict[str, str] = field(default_factory=dict[str, Any])  # 额外信息
    user_config_dir: str | None = None  # 显式覆盖的 Illusion 用户配置根


def detect_os() -> tuple[str, str]:
    """检测当前平台的操作系统和版本
    
    Returns:
        tuple[str, str]: (操作系统名称, 操作系统版本)
    """
    system = platform.system()
    if system == "Linux":
        try:
            import distro
            return "Linux", distro.version(pretty=True) or platform.release()
        except ImportError:
            return "Linux", platform.release()
    elif system == "Darwin":
        mac_ver = platform.mac_ver()[0]
        return "macOS", mac_ver or platform.release()
    elif system == "Windows":
        win_ver = platform.version()
        return "Windows", win_ver
    return system, platform.release()


def detect_shell() -> str:
    """检测用户的 shell
    
    首先检查 SHELL 环境变量，然后在 Windows 上查找 Git Bash，
    最后回退检查 PATH 上的常见 shell。
    
    Returns:
        str: 检测到的 shell 名称
    """
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name

    # 在 Windows 上，使用专门的 bash 解析来查找 Git Bash
    if platform.system() == "Windows":
        from illusion.utils.shell import _resolve_windows_bash
        win_bash = _resolve_windows_bash()
        if win_bash:
            return "bash"

    # 回退：检查 PATH 上的常见 shell
    for candidate in ("bash", "zsh", "fish", "sh"):
        if shutil.which(candidate):
            return candidate

    return "unknown"


def detect_git_info(cwd: str) -> tuple[bool, str | None]:
    """检查工作目录是否在 Git 仓库中并返回分支名称
    
    Args:
        cwd: 工作目录
    
    Returns:
        tuple[bool, str | None]: (是否为 Git 仓库, 分支名称)
    """
    run_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
            stdin=subprocess.DEVNULL,
            check=False,
            **run_kwargs,
        )
        is_git = result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, NotADirectoryError, OSError, subprocess.TimeoutExpired):
        return False, None

    if not is_git:
        return False, None

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
            stdin=subprocess.DEVNULL,
            check=False,
            **run_kwargs,
        )
        branch = result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, NotADirectoryError, OSError, subprocess.TimeoutExpired):
        branch = None

    return True, branch


def get_environment_info(cwd: str | None = None) -> EnvironmentInfo:
    """收集所有环境信息到一个 EnvironmentInfo 快照
    
    Args:
        cwd: 工作目录。如果为 None，则使用当前目录
    
    Returns:
        EnvironmentInfo: 包含所有环境信息的数据类
    """
    if cwd is None:
        cwd = os.getcwd()

    os_name, os_version = detect_os()
    shell = detect_shell()
    is_git, branch = detect_git_info(cwd)
    config_override = get_config_dir_override()

    return EnvironmentInfo(
        os_name=os_name,
        os_version=os_version,
        platform_machine=platform.machine(),
        shell=shell,
        cwd=cwd,
        home_dir=str(Path.home()),
        user_config_dir=str(config_override.absolute()) if config_override is not None else None,
        date=datetime.now(tz=UTC).strftime("%Y-%m-%d"),
        python_version=platform.python_version(),
        is_git_repo=is_git,
        git_branch=branch,
        hostname=platform.node(),
    )
