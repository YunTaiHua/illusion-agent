"""
插件安装辅助模块
================

本模块提供插件安装和卸载功能。

主要功能：
    - 从路径安装插件到用户插件目录
    - 卸载用户插件

使用示例：
    >>> from illusion.plugins.installer import install_plugin_from_path, uninstall_plugin
    >>> install_plugin_from_path("/path/to/plugin")
    >>> uninstall_plugin("my_plugin")
"""

from __future__ import annotations

import shutil
from pathlib import Path

from illusion.plugins.loader import _find_manifest, _load_manifest, get_user_plugins_dir


def install_plugin_from_path(source: str | Path) -> Path:
    """验证并安装一个插件目录到用户插件目录。"""
    src = Path(source).expanduser().resolve()
    if not src.is_dir():
        raise ValueError(f"Plugin source must be a directory: {source}")
    manifest_path = _find_manifest(src)
    if manifest_path is None:
        raise ValueError(f"Plugin manifest not found: {src}")
    if _load_manifest(src) is None:
        raise ValueError(f"Invalid plugin manifest: {manifest_path}")

    destination_root = get_user_plugins_dir().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    dest = destination_root / src.name
    if src == dest.resolve():
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def uninstall_plugin(name: str) -> bool:
    """按用户插件目录中的直接子目录名称卸载插件。"""
    if not name or Path(name).name != name or name in {".", ".."}:
        return False
    root = get_user_plugins_dir().resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True
