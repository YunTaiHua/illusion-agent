"""
MCP 服务器配置加载模块
=====================

本模块提供从设置、固定用户/项目文件和插件中加载 MCP 服务器配置的功能。

主要功能：
    - 从全局设置中加载 MCP 服务器配置
    - 从 ``ILLUSION_CONFIG_DIR/mcp.json`` 加载用户级配置
    - 从 ``<cwd>/.illusion/mcp.json`` 加载项目级配置
    - 从已加载的插件中合并 MCP 服务器配置

函数说明：
    - load_mcp_server_configs: 加载并合并 MCP 服务器配置
    - load_project_mcp_configs: 从项目目录加载 MCP 配置

使用示例：
    >>> from illusion.mcp.config import load_mcp_server_configs
    >>> configs = load_mcp_server_configs(settings, plugins, cwd="/path/to/project")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from illusion.config.paths import (
    get_mcp_config_file_path,
    get_project_dot_mcp_config_file_path,
    get_project_mcp_config_file_path,
)
from illusion.mcp.types import McpServerConfig, _normalize_server_config_type
from illusion.plugins.types import LoadedPlugin

logger = logging.getLogger(__name__)


def _config_file_key(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return str(resolved).casefold() if resolved.drive else str(resolved)


def _load_mcp_configs_from_file(
    config_file: Path,
    server_adapter: TypeAdapter[McpServerConfig],
    *,
    seen_paths: set[str] | None = None,
) -> dict[str, object]:
    """读取一个固定位置的 MCP JSON 配置文件。

    读取入口只检查并读取现有文件，不创建用户配置根、项目 ``.illusion``
    或任何 MCP 目录。单个文件或服务器损坏时继续返回其余有效条目。
    """
    servers: dict[str, object] = {}
    seen = seen_paths if seen_paths is not None else set()
    key = _config_file_key(config_file)
    if key in seen:
        return servers
    try:
        if not config_file.is_file():
            return servers
    except OSError as exc:
        logger.warning("Failed to inspect MCP config %s: %s", config_file, exc)
        return servers
    seen.add(key)

    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read MCP config %s: %s", config_file, exc)
        return servers
    if not isinstance(raw, dict):
        logger.warning("MCP config %s must contain a JSON object", config_file)
        return servers
    return _parse_mcp_config_dict(raw, server_adapter, source=config_file)


def load_user_mcp_configs() -> dict[str, object]:
    """从用户配置根的 ``mcp.json`` 加载 MCP 服务器配置。

    用户配置根由 ``ILLUSION_CONFIG_DIR`` 决定，文件不存在时返回空映射，
    不会因为读取操作创建目录。
    """
    server_adapter: TypeAdapter[McpServerConfig] = TypeAdapter(McpServerConfig)
    return _load_mcp_configs_from_file(get_mcp_config_file_path(), server_adapter)


def load_project_mcp_configs(
    cwd: str | Path,
    *,
    _seen_paths: set[str] | None = None,
) -> dict[str, object]:
    """从项目 `.illusion/mcp.json` 读取 MCP 配置。

    `.illusion/.mcp.json` 作为同一作用域的点文件入口，仅在主文件不存在
    时读取。旧的 `.illusion/mcp/*.json` 目录及其中的 JSON 不会扫描。
    """
    server_adapter: TypeAdapter[McpServerConfig] = TypeAdapter(McpServerConfig)
    primary = get_project_mcp_config_file_path(cwd)
    if primary.is_file():
        return _load_mcp_configs_from_file(
            primary,
            server_adapter,
            seen_paths=_seen_paths,
        )
    return _load_mcp_configs_from_file(
        get_project_dot_mcp_config_file_path(cwd),
        server_adapter,
        seen_paths=_seen_paths,
    )


def _looks_like_server_config(value: object) -> bool:
    """判断一个 dict 是否像是单个 MCP 服务器配置。"""
    if not isinstance(value, dict):
        return False
    return any(k in value for k in ("type", "command", "url", "args"))


def _extract_server_map(raw: dict[str, Any]) -> dict[str, Any] | None:
    """提取已支持的多服务器包装格式。"""
    for key in ("mcpServers", "mcp_servers"):
        if key in raw:
            value = raw[key]
            return value if isinstance(value, dict) else {}

    nested_mcp = raw.get("mcp")
    if isinstance(nested_mcp, dict):
        for key in ("servers", "mcpServers", "mcp_servers"):
            if key in nested_mcp:
                value = nested_mcp[key]
                return value if isinstance(value, dict) else {}
    return None


def _parse_server_entry(
    name: str,
    raw_config: object,
    server_adapter: TypeAdapter[McpServerConfig],
    *,
    source: object | None,
) -> object | None:
    if not isinstance(raw_config, dict) or raw_config.get("enabled", True) is False:
        return None
    try:
        return server_adapter.validate_python(_normalize_server_config_type(raw_config))
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Failed to parse MCP server '%s' in %s: %s",
            name,
            source or "input",
            exc,
        )
        return None


def _parse_mcp_config_dict(
    raw: dict[str, Any],
    server_adapter: TypeAdapter[McpServerConfig],
    *,
    source: object | None = None,
) -> dict[str, object]:
    """解析单服务器、标准多服务器或无包装多服务器 JSON。"""
    servers: dict[str, object] = {}
    server_map = _extract_server_map(raw)

    if server_map is not None:
        for name, server_cfg in server_map.items():
            if not isinstance(name, str):
                continue
            config = _parse_server_entry(name, server_cfg, server_adapter, source=source)
            if config is not None:
                servers[name] = config
        return servers

    if not _looks_like_server_config(raw) and raw:
        all_values_like_config = all(
            isinstance(value, dict) and _looks_like_server_config(value)
            for value in raw.values()
        )
        if all_values_like_config:
            for name, server_cfg in raw.items():
                if not isinstance(name, str):
                    continue
                config = _parse_server_entry(name, server_cfg, server_adapter, source=source)
                if config is not None:
                    servers[name] = config
            return servers

    config = _parse_server_entry(
        Path(str(source)).stem if source else "_inline",
        raw,
        server_adapter,
        source=source,
    )
    if config is not None:
        servers[Path(str(source)).stem if source else "_inline"] = config
    return servers


def load_mcp_server_configs(settings: Any, plugins: list[LoadedPlugin], cwd: str | Path | None = None) -> dict[str, object]:
    """加载并按设置、用户目录、项目目录和插件顺序合并 MCP 配置。"""
    # 加载项目级权限配置
    from illusion.permissions.loader import load_project_permissions
    project_permissions = load_project_permissions(cwd) if cwd else None

    # 检查是否禁用所有 MCP 服务器
    if project_permissions and "*" in project_permissions.denied_mcp_servers:
        return {}

    # 保留已有 settings 配置优先级，目录配置按用户级到项目级覆盖。
    servers = {
        name: cfg for name, cfg in getattr(settings, "mcp_servers", {}).items()
        if getattr(cfg, "enabled", True)
    }
    server_adapter: TypeAdapter[McpServerConfig] = TypeAdapter(McpServerConfig)
    seen_paths: set[str] = set()
    servers.update(
        _load_mcp_configs_from_file(
            get_mcp_config_file_path(),
            server_adapter,
            seen_paths=seen_paths,
        )
    )

    if cwd is not None:
        project_path = get_project_mcp_config_file_path(cwd)
        if not project_path.is_file():
            project_path = get_project_dot_mcp_config_file_path(cwd)
        servers.update(
            _load_mcp_configs_from_file(
                project_path,
                server_adapter,
                seen_paths=seen_paths,
            )
        )

    # 遍历所有已加载的插件
    for plugin in plugins:
        # 跳过未启用的插件
        if not getattr(plugin, "enabled", True):
            continue
        # 插件服务器使用命名空间，避免覆盖已有用户和项目配置。
        for name, config in getattr(plugin, "mcp_servers", {}).items():
            if not getattr(config, "enabled", True):
                continue
            servers.setdefault(f"{plugin.manifest.name}:{name}", config)

    # 过滤掉被禁用的 MCP 服务器
    if project_permissions:
        servers = {
            name: cfg for name, cfg in servers.items()
            if name not in project_permissions.denied_mcp_servers
        }

    return servers


def load_mcp_config_from_string(cfg: str) -> dict[str, object]:
    """从 JSON 字符串或文件路径加载 MCP 服务器配置。

    支持两种输入：
    1. 文件路径（Path 存在时）：读取 JSON 文件
    2. JSON 字符串：直接解析

    支持三种格式：
    1. {"mcpServers": {...}} / {"mcp_servers": {...}}（标准多服务器）
    2. {"server-name": {...}, ...}（无包装多服务器）
    3. {...}（单服务器配置，返回 {"_inline": config}）

    Args:
        cfg: 文件路径或 JSON 字符串

    Returns:
        dict[str, object]: 服务器名称到配置的映射
    """

    _server_adapter: TypeAdapter[McpServerConfig] = TypeAdapter(McpServerConfig)

    # 判断是文件路径还是 JSON 字符串
    cfg_path = Path(cfg)
    source = cfg_path if cfg_path.exists() else None
    if source is not None:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        raw = json.loads(cfg)

    if not isinstance(raw, dict):
        return {}

    return _parse_mcp_config_dict(raw, _server_adapter, source=source or "_inline")
