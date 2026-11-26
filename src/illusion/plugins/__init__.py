"""
插件模块
========

本模块提供 IllusionAgent 插件系统的加载和管理功能。

主要组件：
    - PluginManifest: 插件清单
    - LoadedPlugin: 已加载插件
    - discover_plugin_paths: 发现插件路径
    - get_project_plugins_dir: 获取项目插件目录
    - get_user_plugins_dir: 获取用户插件目录
    - load_plugins: 加载插件
    - install_plugin_from_path: 从路径安装插件
    - uninstall_plugin: 卸载插件

使用示例：
    >>> from illusion.plugins import load_plugins, get_user_plugins_dir
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from illusion.plugins.installer import install_plugin_from_path, uninstall_plugin
    from illusion.plugins.loader import (
        discover_plugin_paths,
        get_project_plugins_dir,
        get_user_plugins_dir,
        load_plugins,
        set_plugin_enabled,
    )
    from illusion.plugins.schemas import PluginManifest
    from illusion.plugins.types import LoadedPlugin

__all__ = [
    "LoadedPlugin",
    "PluginManifest",
    "discover_plugin_paths",
    "get_project_plugins_dir",
    "get_user_plugins_dir",
    "install_plugin_from_path",
    "load_plugins",
    "set_plugin_enabled",
    "uninstall_plugin",
]


def __getattr__(name: str) -> object:
    if name in {"discover_plugin_paths", "get_project_plugins_dir", "get_user_plugins_dir", "load_plugins", "set_plugin_enabled"}:
        from illusion.plugins.loader import (
            discover_plugin_paths,
            get_project_plugins_dir,
            get_user_plugins_dir,
            load_plugins,
            set_plugin_enabled,
        )

        return {
            "discover_plugin_paths": discover_plugin_paths,
            "get_project_plugins_dir": get_project_plugins_dir,
            "get_user_plugins_dir": get_user_plugins_dir,
            "load_plugins": load_plugins,
            "set_plugin_enabled": set_plugin_enabled,
        }[name]
    if name in {"install_plugin_from_path", "uninstall_plugin"}:
        from illusion.plugins.installer import install_plugin_from_path, uninstall_plugin

        return {
            "install_plugin_from_path": install_plugin_from_path,
            "uninstall_plugin": uninstall_plugin,
        }[name]
    if name == "PluginManifest":
        from illusion.plugins.schemas import PluginManifest

        return PluginManifest
    if name == "LoadedPlugin":
        from illusion.plugins.types import LoadedPlugin

        return LoadedPlugin
    raise AttributeError(name)
