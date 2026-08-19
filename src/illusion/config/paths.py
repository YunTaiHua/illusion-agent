"""
IllusionAgent 配置和数据目录路径解析模块
=======================================

遵循 XDG 类似约定，默认使用 ~/.illusion/ 作为基础目录。

本模块提供各种目录路径的获取函数，支持环境变量覆盖，
确保配置、数据、日志等文件存储在正确的位置。

函数说明：
    - get_config_dir: 获取配置目录
    - get_config_file_path: 获取配置文件路径
    - get_data_dir: 获取数据目录
    - get_logs_dir: 获取日志目录
    - get_sessions_dir: 获取会话存储目录
    - get_tasks_dir: 获取后台任务输出目录
    - get_feedback_dir: 获取反馈存储目录
    - get_project_config_dir: 获取项目级配置目录
    - get_cron_dir: 获取 cron 数据目录
    - get_cron_registry_path: 获取 cron 注册表文件路径
    - get_feedback_log_path: 获取反馈日志文件路径
    - get_mcp_log_path: 获取 MCP 服务器日志文件路径

使用示例：
    >>> from illusion.config.paths import get_config_dir, get_data_dir
    >>> config = get_config_dir()
    >>> data = get_data_dir()
"""

from __future__ import annotations

import os  # 导入 os 模块用于环境变量和路径操作
from pathlib import Path, PurePath  # 导入 Path 类用于路径处理

# 常量定义
_DEFAULT_BASE_DIR = ".illusion"  # 默认基础目录名称
_CONFIG_FILE_NAME = "settings.json"  # 配置文件名称


def _sanitize_path_component(value: str) -> str:
    """将路径组件规范化为安全形式。"""
    sanitized = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "-" for ch in value)
    sanitized = sanitized.strip("-_")
    return sanitized or "default"


def validate_safe_path(candidate: str) -> Path:
    """校验用户输入的路径安全性，拒绝路径穿越攻击。

    安全策略：
        - 拒绝包含 ``..`` 的路径（防止跳出工作目录的路径穿越攻击）
        - 拒绝 ``~`` 开头的路径（防止 ``expanduser()`` 展开到用户家目录）

    注意：绝对路径不在此层拒绝。``resolve_relative_path`` 允许绝对路径直接通过，
    现有测试和工具行为依赖此特性。绝对路径的访问控制由 permission checker
    在更上层统一处理。

    Args:
        candidate: 用户输入的原始路径字符串

    Returns:
        Path: 原样返回的 Path 对象（不展开、不解析），由调用方自行 join

    Raises:
        ValueError: 路径包含 ``..`` 或以 ``~`` 开头
    """
    p = Path(candidate)
    if ".." in p.parts:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")
    if candidate.startswith("~"):
        raise ValueError(f"Home directory paths not allowed: {candidate!r}")
    return p


def resolve_relative_path(base: Path, candidate: str) -> Path:
    """解析相对路径为绝对路径，并拒绝路径穿越攻击。

    安全策略：通过 :func:`validate_safe_path` 拒绝包含 ``..`` 和以 ``~``
    开头的路径，通过校验后与 ``base`` 拼接并 resolve 为绝对路径。

    Args:
        base: 基础目录（通常是 ``context.cwd``）
        candidate: 候选路径（必须是相对路径，不得含 ``..``）

    Returns:
        解析后的绝对路径

    Raises:
        ValueError: 路径包含 ``..`` 或以 ``~`` 开头
    """
    safe_path = validate_safe_path(candidate)
    return (base / safe_path).resolve()


def get_config_dir() -> Path:
    """返回配置目录，必要时创建

    解析顺序：
    1. ILLUSION_CONFIG_DIR 环境变量（优先）
    2. ~/.illusion/（默认）

    Returns:
        Path: 配置目录路径
    """
    env_dir = os.environ.get("ILLUSION_CONFIG_DIR")
    if env_dir:
        config_dir = Path(env_dir)
    else:
        config_dir = Path.home() / _DEFAULT_BASE_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_file_path() -> Path:
    """返回主设置文件路径（~/.illusion/settings.json）
    
    Returns:
        Path: 配置文件路径
    """
    return get_config_dir() / _CONFIG_FILE_NAME


def get_data_dir() -> Path:
    """返回数据目录（用于缓存、历史等）

    解析顺序：
    1. ILLUSION_DATA_DIR 环境变量（优先）
    2. ~/.illusion/data/（默认）

    Returns:
        Path: 数据目录路径
    """
    env_dir = os.environ.get("ILLUSION_DATA_DIR")
    if env_dir:
        data_dir = Path(env_dir)
    else:
        data_dir = get_config_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_logs_dir() -> Path:
    """返回日志目录

    解析顺序：
    1. ILLUSION_LOGS_DIR 环境变量（优先）
    2. ~/.illusion/logs/（默认）

    Returns:
        Path: 日志目录路径
    """
    env_dir = os.environ.get("ILLUSION_LOGS_DIR")
    if env_dir:
        logs_dir = Path(env_dir)
    else:
        logs_dir = get_config_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_sessions_dir() -> Path:
    """返回会话存储目录
    
    用于存储对话会话相关的数据文件。
    
    Returns:
        Path: 会话目录路径
    """
    sessions_dir = get_data_dir() / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def get_tasks_dir() -> Path:
    """返回后台任务输出目录
    
    用于存储后台任务执行结果和输出文件。
    当设置 ``ILLUSION_TASK_LIST_ID`` 环境变量时，任务会隔离到对应子目录。
    
    Returns:
        Path: 任务目录路径
    """
    tasks_root = get_data_dir() / "tasks"
    task_list_id = os.environ.get("ILLUSION_TASK_LIST_ID", "").strip()
    if task_list_id:
        tasks_dir = tasks_root / _sanitize_path_component(task_list_id)
    else:
        tasks_dir = tasks_root
    tasks_dir.mkdir(parents=True, exist_ok=True)
    return tasks_dir


def get_feedback_dir() -> Path:
    """返回反馈存储目录
    
    用于存储用户反馈相关的数据文件。
    
    Returns:
        Path: 反馈目录路径
    """
    feedback_dir = get_data_dir() / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    return feedback_dir


def get_feedback_log_path() -> Path:
    """返回反馈日志文件路径

    位于统一日志目录（~/.illusion/logs/）下，便于 log_cleanup 工具
    统一清理管理，避免反馈日志散落在 data 目录中无人维护。

    Returns:
        Path: 反馈日志文件路径
    """
    return get_logs_dir() / "feedback.log"


def get_mcp_log_path(server_name: str) -> Path:
    """返回 MCP 服务器 stderr 日志文件路径

    位于统一日志目录（~/.illusion/logs/）下，便于 log_cleanup 工具
    统一清理管理。server_name 会被规范化为安全的文件名组件，
    避免用户配置的服务器名包含路径分隔符等特殊字符。

    Args:
        server_name: MCP 服务器名称（settings.json 中 mcpServers 的 key）

    Returns:
        Path: MCP 日志文件路径（形如 ``~/.illusion/logs/mcp_{safe_name}.log``）
    """
    safe_name = _sanitize_path_component(server_name)
    return get_logs_dir() / f"mcp_{safe_name}.log"


def get_cron_dir() -> Path:
    """返回 cron 数据目录

    用于存储定时任务相关的所有文件（注册表、历史、PID 等）。

    Returns:
        Path: cron 数据目录路径
    """
    cron_dir = get_data_dir() / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    return cron_dir


def get_cron_registry_path() -> Path:
    """返回 cron 注册表文件路径

    用于存储定时任务配置信息。

    Returns:
        Path: cron 注册表文件路径
    """
    return get_cron_dir() / "jobs.json"


def get_project_config_dir(cwd: str | Path) -> Path:
    """返回项目级 .illusion 目录

    在当前工作目录下创建 .illusion 子目录，用于存储项目级配置。

    cwd 无效（如守护进程启动目录被删除）时回退到用户级配置目录，
    避免 WinError 267 阻断 build_runtime。

    加固：cwd 若非真实路径类型（如测试误传 MagicMock），直接回退到
    用户级配置目录，避免在 cwd 下创建 ``MagicMock/.../.illusion`` 目录。

    Args:
        cwd: 当前工作目录

    Returns:
        Path: 项目配置目录路径
    """
    if not isinstance(cwd, (str, PurePath)):
        return get_config_dir()
    try:
        project_dir = Path(cwd).resolve() / ".illusion"
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir
    except (OSError, FileNotFoundError):
        # cwd 失效，回退到用户级配置目录
        return get_config_dir()


def get_project_mcp_dir(cwd: str | Path) -> Path:
    """返回项目级 MCP 配置目录（.illusion/mcp/）

    Args:
        cwd: 当前工作目录

    Returns:
        Path: 项目 MCP 配置目录路径
    """
    path = get_project_config_dir(cwd) / "mcp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_channels_file_path() -> Path:
    """返回渠道配置文件路径（~/.illusion/channels.json）

    用于存储各消息渠道（飞书等）的配置信息，与主 settings.json 分离。

    Returns:
        Path: 渠道配置文件路径
    """
    return get_config_dir() / "channels.json"


def get_workspaces_file_path() -> Path:
    """返回 Web 多工作区注册表文件路径（~/.illusion/workspaces.json）

    用于存储 Web 端注册的目录空间列表（默认工作区来自
    settings.working_directory，不重复存储于此文件）。

    Returns:
        Path: 工作区注册表文件路径
    """
    return get_config_dir() / "workspaces.json"


def get_channels_data_dir() -> Path:
    """返回渠道数据目录（~/.illusion/channels/）

    用于存储渠道运行时数据，如飞书会话历史、守护进程 PID 等。

    Returns:
        Path: 渠道数据目录路径
    """
    d = get_config_dir() / "channels"
    d.mkdir(parents=True, exist_ok=True)
    return d
