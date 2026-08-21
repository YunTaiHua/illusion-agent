"""
文件周期清理工具
================

提供统一的 TTL 文件清理函数，供 tasks 日志、记忆活动日志、计划文件等
多个子系统复用，避免各模块重复实现清理循环。

函数说明：
    - cleanup_old_files: 删除目录下超过保留期限的文件
    - cleanup_old_plans: 删除计划文件目录下超过保留期限的 .md 计划文件
"""

from __future__ import annotations

import time
from pathlib import Path

DEFAULT_TTL_DAYS = 7  # 默认保留天数
PLANS_TTL_DAYS = 7  # 计划文件保留天数


def cleanup_old_files(
    directory: Path,
    pattern: str = "*",
    *,
    max_age_days: int = DEFAULT_TTL_DAYS,
    max_size_bytes: int | None = None,
) -> int:
    """删除目录下超龄或超大的文件。

    删除条件（满足其一即删）：
        - mtime 超过 max_age_days 天
        - 文件大小 >= max_size_bytes（与 mtime 无关，用于兜底
          "mtime 一直被刷新导致年龄清理永不触发"的文件）

    静默处理异常：目录不可访问、单个文件被占用或删除失败时
    跳过并继续，不影响调用方。

    Args:
        directory: 目标目录
        pattern: 文件匹配模式（glob，如 "*.log" / "memory_*.log*"）
        max_age_days: 保留天数，超过即删除
        max_size_bytes: 单文件大小上限（字节），达到或超过即删除；
            None 表示不按体积清理

    Returns:
        int: 删除的文件数量
    """
    try:
        cutoff = time.time() - max_age_days * 24 * 3600
        removed = 0
        for path in directory.glob(pattern):
            try:
                if not path.is_file():
                    continue
                st = path.stat()
                if st.st_mtime < cutoff or (
                    max_size_bytes is not None and st.st_size >= max_size_bytes
                ):
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                # 单文件不可访问/被占用时跳过
                continue
        return removed
    except OSError:
        # 目录不可访问时静默跳过
        return 0


def cleanup_old_plans(
    plans_dir: Path,
    max_age_days: int = PLANS_TTL_DAYS,
) -> int:
    """删除计划文件目录下超过保留期限的 .md 计划文件。

    计划文件（~/.illusion/plans/*.md）随会话累计且无删除钩子，
    由应用启动时统一按 7 天 TTL 清理，避免无限增长。

    Args:
        plans_dir: 计划文件目录（get_plans_dir() 返回）
        max_age_days: 保留天数，超过即删除

    Returns:
        int: 删除的文件数量
    """
    return cleanup_old_files(plans_dir, "*.md", max_age_days=max_age_days)


# 计划文件清理的"每进程一次"标志：build_runtime 在 Web 多工作区/多会话下
# 会被反复调用，用模块级标志保证一个进程只扫一次目录
_plans_cleanup_done = False


def run_plans_cleanup_once(plans_dir: Path) -> int:
    """进程内仅执行一次的计划文件清理。

    由运行时统一入口显式调用（不再依赖 get_plans_dir 的惰性触发——惰性
    触发在 Web/桌面等不 import plan_file 的启动路径上永远不会被触发），
    并在一次进程生命周期内只清理一次。

    Args:
        plans_dir: 计划文件目录

    Returns:
        int: 删除的文件数量
    """
    global _plans_cleanup_done
    if _plans_cleanup_done:
        return 0
    _plans_cleanup_done = True
    return cleanup_old_plans(plans_dir)
