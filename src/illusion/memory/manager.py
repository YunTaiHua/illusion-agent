"""
记忆管理模块
==========

本模块提供记忆文件的管理操作功能。

主要功能：
    - 列出项目记忆文件
    - 添加/移除记忆条目

函数说明：
    - list_memory_files: 列出记忆文件
    - add_memory_entry: 添加记忆条目
    - remove_memory_entry: 移除记忆条目

使用示例：
    >>> from illusion.memory import list_memory_files, add_memory_entry, remove_memory_entry
    >>> files = list_memory_files(".")
    >>> path = add_memory_entry(".", "Test", "# Test memory content")
    >>> remove_memory_entry(".", "test")
"""

from __future__ import annotations

from pathlib import Path
from re import sub

from illusion.memory.paths import get_memory_dir_for_cwd, get_memory_entrypoint
from illusion.utils.atomic_write import atomic_write_text


def is_memory_enabled(cwd: str | Path) -> bool:
    """检查记忆功能是否启用

    检查项目级权限配置中的 denied_memory 字段。
    如果 denied_memory 为 True，则禁用记忆功能。

    Args:
        cwd: 当前工作目录

    Returns:
        bool: 记忆功能是否启用
    """
    from illusion.permissions.loader import load_project_permissions

    project_permissions = load_project_permissions(cwd)

    # 检查项目级权限配置
    if project_permissions.denied_memory:
        return False

    # 检查全局配置
    from illusion.config.settings import load_settings

    settings = load_settings()
    return settings.memory.enabled


def list_memory_files(cwd: str | Path) -> list[Path]:
    """列出项目的所有记忆markdown文件（根目录 + 类型子目录）

    Args:
        cwd: 当前工作目录

    Returns:
        list[Path]: 排序后的记忆文件路径列表
    """
    from illusion.memory.paths import MEMORY_TYPE_DIRS

    # 检查记忆功能是否启用
    if not is_memory_enabled(cwd):
        return []

    memory_dir = get_memory_dir_for_cwd(cwd)  # 获取记忆目录
    paths = list(memory_dir.glob("*.md"))  # 根目录文件（兼容旧布局）
    for type_name in MEMORY_TYPE_DIRS:  # 类型子目录
        paths.extend((memory_dir / type_name).glob("*.md"))
    return sorted(paths)  # 返回排序后的文件列表


def add_memory_entry(
    cwd: str | Path, title: str, content: str, memory_type: str = ""
) -> Path:
    """创建记忆文件并添加到MEMORY.md索引

    指定 memory_type（user/feedback/project/reference）时，文件写入
    对应类型子目录；否则写入记忆目录根目录（兼容旧布局）。

    Args:
        cwd: 当前工作目录
        title: 记忆标题
        content: 记忆内容
        memory_type: 记忆类型（user/feedback/project/reference，可选）

    Returns:
        Path: 创建的记忆文件路径

    Raises:
        RuntimeError: 记忆功能被禁用时抛出
    """
    from illusion.memory.paths import MEMORY_TYPE_DIRS

    # 检查记忆功能是否启用
    if not is_memory_enabled(cwd):
        raise RuntimeError("Memory is disabled by project permissions")

    memory_dir = get_memory_dir_for_cwd(cwd)  # 获取记忆目录
    # 按类型写入子目录（user/feedback/project/reference）
    if memory_type in MEMORY_TYPE_DIRS:
        memory_dir = memory_dir / memory_type
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"  # 转换为slug
    path = memory_dir / f"{slug}.md"  # 构建文件路径
    atomic_write_text(path, content.strip() + "\n")  # 写入内容

    entrypoint = get_memory_entrypoint(cwd)  # 获取入口点
    existing = (
        entrypoint.read_text(encoding="utf-8") if entrypoint.exists() else "# Memory Index\n"
    )  # 读取现有内容
    # 索引使用相对记忆目录的路径（含类型子目录前缀）
    index_ref = str(path.relative_to(get_memory_dir_for_cwd(cwd))).replace("\\", "/")
    if index_ref not in existing:  # 如果不存在
        # 索引条目格式：- [Title](file.md) — one-line hook
        existing = existing.rstrip() + f"\n- [{title}]({index_ref}) — {title}\n"  # 添加索引条目
        atomic_write_text(entrypoint, existing)  # 写入索引
    return path  # 返回创建的文件路径


def remove_memory_entry(cwd: str | Path, name: str) -> bool:
    """删除记忆文件及其在MEMORY.md中的索引条目

    Args:
        cwd: 当前工作目录
        name: 记忆文件名称 (不带.md扩展名)

    Returns:
        bool: 是否成功删除

    Raises:
        RuntimeError: 记忆功能被禁用时抛出
    """
    from illusion.memory.paths import MEMORY_TYPE_DIRS

    # 检查记忆功能是否启用
    if not is_memory_enabled(cwd):
        raise RuntimeError("Memory is disabled by project permissions")

    memory_dir = get_memory_dir_for_cwd(cwd)  # 获取记忆目录
    # 查找匹配文件：根目录 + 类型子目录
    candidate_dirs = [memory_dir] + [memory_dir / sub for sub in MEMORY_TYPE_DIRS]
    matches = [
        path
        for d in candidate_dirs
        for path in d.glob("*.md")
        if path.stem == name or path.name == name
    ]  # 查找匹配文件
    if not matches:  # 没有匹配
        return False
    path = matches[0]  # 取第一个匹配
    if path.exists():  # 文件存在
        path.unlink()  # 删除文件

    entrypoint = get_memory_entrypoint(cwd)  # 获取入口点
    if entrypoint.exists():  # 入口点存在
        lines = [
            line  # 保留的行
            for line in entrypoint.read_text(encoding="utf-8").splitlines()
            if path.name not in line  # 排除包含删除文件名的行
        ]
        atomic_write_text(entrypoint, "\n".join(lines).rstrip() + "\n")  # 重写入口点
    return True  # 返回成功
