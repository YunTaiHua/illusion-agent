"""
项目级权限加载模块
================

本模块提供从项目目录加载权限配置的功能。

主要功能：
    - load_project_permissions: 加载项目级权限配置
    - filter_rules_by_permissions: 根据权限配置过滤 rules 文件列表

使用示例：
    >>> from illusion.permissions.loader import load_project_permissions, filter_rules_by_permissions
    >>> perms = load_project_permissions("/path/to/project")
    >>> filtered_rules = filter_rules_by_permissions(rule_files, perms)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from illusion.config.paths import get_project_config_dir
from illusion.permissions.schemas import ProjectPermissions
from illusion.utils.atomic_write import atomic_write_text, config_write_lock, file_revision

logger = logging.getLogger(__name__)


def _permissions_document(
    path: Path, *, strict: bool = True
) -> tuple[dict[str, Any], ProjectPermissions]:
    """保留原始对象用于局部更新。只有文件确实不存在才等同空配置。"""
    try:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if path.is_symlink():  # 损坏的链接不能作为新文件覆盖
                raise
            raw = {}
        try:
            permissions = ProjectPermissions.model_validate(raw)
        except ValidationError:
            if strict or not isinstance(raw, dict) or "enabled_plugins" not in raw:
                raise
            # 新字段格式错误时，其余权限（例如 denied_skills）仍按旧契约生效。
            logger.warning("Ignoring invalid enabled_plugins in %s", path)
            permissions = ProjectPermissions.model_validate({**raw, "enabled_plugins": {}})
        return raw, permissions
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read project permissions {path}: {exc}") from exc


def load_project_permissions(cwd: str | Path, *, strict: bool = False) -> ProjectPermissions:
    """只读加载项目权限。严格模式禁止将损坏或不可读的策略视为空策略。"""
    path = get_project_config_dir(cwd) / "permissions.json"
    try:
        return _permissions_document(path, strict=strict)[1]
    except ValueError as exc:
        if strict:
            raise
        logger.warning("%s", exc)
        return ProjectPermissions()


def _project_plugin_preference_path(cwd: str | Path) -> Path:
    """在真实工作区内写权限文件，保留已有链接及其指向的策略。"""
    try:
        workspace = Path(cwd).resolve(strict=True)
        config_dir = (workspace / ".illusion").resolve(strict=True)
        if not config_dir.is_relative_to(workspace):
            raise ValueError(f"Cannot write project permissions outside workspace {workspace}: {config_dir}")
        path = config_dir / "permissions.json"
        if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
            raise ValueError(f"Cannot replace linked project permissions: {path}")
        return path
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot locate project permissions for {cwd}: {exc}") from exc


def set_project_plugin_preference(
    cwd: str | Path, name: str, enabled: bool, *, legacy_name: str | None = None
) -> None:
    """仅合并一个项目插件开关，保留硬拒绝、其他权限和未知字段。"""
    path = _project_plugin_preference_path(cwd)
    try:
        with config_write_lock(path):
            revision = file_revision(path)
            raw, permissions = _permissions_document(path)
            permissions.require_plugin_allowed(name, legacy_name=legacy_name)
            # POSIX 上目录可写即可 replace，须额外尊重只读的目标文件。
            if path.exists() and (not path.stat().st_mode & 0o222 or not os.access(path, os.W_OK)):
                raise PermissionError("file is read-only")
            raw.setdefault("enabled_plugins", {})[name] = enabled
            content = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
            if _project_plugin_preference_path(cwd) != path or file_revision(path) != revision:
                raise ValueError(f"Project permissions changed during update: {path}. Refresh and retry.")
            atomic_write_text(path, content)
    except OSError as exc:
        raise ValueError(f"Cannot write project permissions {path}: {exc}") from exc


def filter_rules_by_permissions(
    rule_files: list[Path],
    project_permissions: ProjectPermissions,
) -> list[Path]:
    """根据权限配置过滤 rules 文件列表

    Args:
        rule_files: rules 文件路径列表
        project_permissions: 项目级权限配置对象

    Returns:
        list[Path]: 过滤后的 rules 文件路径列表
    """
    # 检查是否禁用所有 rules
    if "*" in project_permissions.denied_rules:
        return []

    # 过滤掉被禁用的 rules
    return [f for f in rule_files if f.stem not in project_permissions.denied_rules]


def is_rules_disabled(project_permissions: ProjectPermissions) -> bool:
    """检查是否禁用所有 rules

    Args:
        project_permissions: 项目级权限配置对象

    Returns:
        bool: 是否禁用所有 rules
    """
    return "*" in project_permissions.denied_rules
