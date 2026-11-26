"""
记忆路径模块
==========

本模块提供持久化记忆的路径管理功能。

记忆采用单层 user 级存储：`~/.illusion/memory/{项目名}-{sha1哈希前12位}/`，
支持通过 `settings.json` 的 `memory.directory` 字段覆盖为自定义目录。

函数说明：
    - get_memory_dir: 获取全局 memory 根目录（~/.illusion/memory）
    - get_memory_dir_for_cwd: 获取当前项目使用的记忆目录（自定义优先）
    - get_memory_entrypoint: 获取记忆入口点文件（MEMORY.md）
    - resolve_custom_memory_dir: 校验并解析自定义记忆目录路径

使用示例：
    >>> from illusion.memory import get_memory_dir_for_cwd, get_memory_entrypoint
    >>> mem_dir = get_memory_dir_for_cwd(".")
    >>> entrypoint = get_memory_entrypoint(".")
"""

from __future__ import annotations

import os
from hashlib import sha1
from pathlib import Path

from illusion.config.paths import get_config_dir


def get_memory_dir() -> Path:
    """返回全局 memory 根目录（~/.illusion/memory）。"""
    return get_config_dir() / "memory"


def get_project_memory_dir(cwd: str | Path) -> Path:
    """获取项目默认记忆目录（user 级全局配置目录）

    目录名格式: {项目名}-{sha1哈希前12位}
    使用项目路径的哈希确保唯一性，所有项目共享 ~/.illusion/memory/ 根目录。

    Args:
        cwd: 当前工作目录

    Returns:
        Path: 记忆目录的Path对象
    """
    memory_dir = get_project_memory_dir_no_create(cwd)
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def get_project_memory_dir_no_create(cwd: str | Path) -> Path:
    """返回项目默认记忆目录路径，但不创建目录。

    读取/扫描路径必须使用此 getter，避免启动时仅为构建提示词而留下空的
    用户记忆目录。明确写入记忆时继续使用 :func:`get_project_memory_dir`。

    Args:
        cwd: 当前工作目录

    Returns:
        Path: 记忆目录路径，目录可能尚不存在
    """
    path = Path(cwd).resolve()
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return get_memory_dir() / f"{path.name}-{digest}"


def get_memory_dir_for_cwd_no_create(cwd: str | Path) -> Path:
    """返回当前项目的记忆目录路径，但不创建目录。

    自定义记忆目录与默认用户级目录都只计算路径。该入口供提示词构建、
    记忆搜索等只读调用使用。
    """
    from illusion.config.settings import load_settings

    settings = load_settings()
    if settings.memory.directory:
        custom = resolve_custom_memory_dir(settings.memory.directory)
        if custom is not None:
            return custom
    return get_project_memory_dir_no_create(cwd)


def get_memory_dir_for_cwd(cwd: str | Path) -> Path:
    """获取当前项目使用的记忆目录（自定义优先，默认回退 user 级）

    解析优先级：
        1. settings.json 的 memory.directory（自定义目录，仅全局配置）
        2. 默认：~/.illusion/memory/{项目名}-{sha1哈希前12位}/

    Args:
        cwd: 当前工作目录

    Returns:
        Path: 记忆目录的Path对象（目录已创建）
    """
    memory_dir = get_memory_dir_for_cwd_no_create(cwd)
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def resolve_custom_memory_dir(raw: str) -> Path | None:
    """校验并解析自定义记忆目录路径。

    校验规则：
        - 支持 ~/ 开头展开（用户方便），拒绝裸 ~、~/.、~/.. 等歧义路径
        - 必须是绝对路径（拒绝相对路径，避免相对 CWD 的歧义）
        - 拒绝根目录 / 盘符根、UNC 路径、空字节

    Args:
        raw: 原始路径字符串（可能含 ~ 前缀）

    Returns:
        Path | None: 解析后的绝对路径，非法路径返回 None
    """
    if not raw or not raw.strip():
        return None
    candidate = raw.strip()

    # ~/ 展开（仅支持 ~/ 和 ~\\ 前缀；裸 ~ 视为非法）
    if candidate.startswith(("~/", "~\\")):
        rest = candidate[2:]
        # 规范化后拒绝展开到 $HOME 或其祖先的歧义路径
        # （规范化后 ~/foo/.. → "." → 拒绝）
        if os.path.normpath(rest) in ("", ".", ".."):
            return None
        candidate = str(Path.home() / rest)
    else:
        # 非 ~/ 开头必须是绝对路径
        if not Path(candidate).is_absolute():
            return None

    path = Path(candidate).resolve()
    # 拒绝根目录/盘符根（resolve 后长度 < 3 的路径基本是根）
    if len(path.parts) < 2:
        return None
    # 拒绝 UNC 路径
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return None
    # 拒绝空字节
    if "\x00" in text:
        return None
    # 拒绝展开到 $HOME 或其祖先（如 ~/../.. 经 resolve 后落在 home 之上）
    home = Path.home().resolve()
    if path == home or path in home.parents:
        return None
    return path


def get_memory_entrypoint(cwd: str | Path) -> Path:
    """获取记忆入口点文件（MEMORY.md）

    仅保留 user 级记忆入口：入口文件始终位于记忆目录下。
    项目级记忆目录（{cwd}/.illusion/memory/）已移除。

    Args:
        cwd: 当前工作目录

    Returns:
        Path: MEMORY.md 文件的Path对象
    """
    return get_memory_dir_for_cwd(cwd) / "MEMORY.md"


# 记忆类型子目录（与记忆文件 frontmatter 的 type 字段对应）
MEMORY_TYPE_DIRS = ("user", "feedback", "project", "reference")


def get_memory_type_dir(cwd: str | Path, memory_type: str) -> Path:
    """获取指定类型的记忆子目录。

    记忆按类型存储于 MEMORY.md 同级的 user/feedback/project/reference
    子目录中，避免根目录杂乱。

    Args:
        cwd: 当前工作目录
        memory_type: 记忆类型（user/feedback/project/reference）

    Returns:
        Path: 类型子目录（不存在则创建）
    """
    type_dir = get_memory_dir_for_cwd(cwd) / memory_type
    type_dir.mkdir(parents=True, exist_ok=True)
    return type_dir


def is_in_memory_dir(path: str | Path) -> bool:
    """判断路径是否位于记忆目录内（权限 carve-out 用）。

    覆盖默认记忆根（~/.illusion/memory）与自定义记忆目录
    （settings.memory.directory）。仅做路径包含判断，不要求文件存在。

    Args:
        path: 候选路径（绝对路径或相对路径）

    Returns:
        bool: 路径是否位于记忆目录内
    """
    try:
        candidate = Path(path).resolve()
    except OSError:
        return False
    # 默认记忆根：~/.illusion/memory（廉价检查，无需读取 settings）
    try:
        candidate.relative_to(get_memory_dir().resolve())
        return True
    except ValueError:
        pass
    # 自定义记忆目录（settings.memory.directory）
    try:
        from illusion.config.settings import load_settings

        custom = load_settings().memory.directory
    except Exception:  # noqa: BLE001 - 配置读取失败按未设置处理
        return False
    if not custom:
        return False
    resolved = resolve_custom_memory_dir(custom)
    if resolved is None:
        return False
    try:
        candidate.relative_to(resolved)
        return True
    except ValueError:
        return False
