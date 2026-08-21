"""文件周期清理工具测试。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from illusion.utils.log_cleanup import cleanup_old_files, cleanup_old_plans, run_plans_cleanup_once


def _make_old_file(path: Path, *, age_days: float) -> None:
    """创建 mtime 为 age_days 天前的文件。"""
    path.write_text("old", encoding="utf-8")
    old = time.time() - age_days * 24 * 3600
    os.utime(path, (old, old))


def test_cleanup_removes_old_files(tmp_path: Path):
    old = tmp_path / "old.log"
    _make_old_file(old, age_days=10)

    removed = cleanup_old_files(tmp_path, "*.log", max_age_days=7)
    assert removed == 1
    assert not old.exists()


def test_cleanup_keeps_recent_files(tmp_path: Path):
    recent = tmp_path / "recent.log"
    recent.write_text("new", encoding="utf-8")  # mtime = now

    removed = cleanup_old_files(tmp_path, "*.log", max_age_days=7)
    assert removed == 0
    assert recent.exists()


def test_cleanup_respects_pattern(tmp_path: Path):
    old_memory = tmp_path / "memory_extract.log"
    _make_old_file(old_memory, age_days=10)
    old_other = tmp_path / "other.log"
    _make_old_file(old_other, age_days=10)

    removed = cleanup_old_files(tmp_path, "memory_*.log", max_age_days=7)
    assert removed == 1
    assert not old_memory.exists()
    assert old_other.exists()


def test_cleanup_missing_dir(tmp_path: Path):
    """目录不存在时静默返回 0。"""
    removed = cleanup_old_files(tmp_path / "nope", "*.log")
    assert removed == 0


def test_cleanup_custom_ttl(tmp_path: Path):
    file_5d = tmp_path / "five.log"
    _make_old_file(file_5d, age_days=5)

    # 3 天 TTL → 删除；10 天 TTL → 保留
    assert cleanup_old_files(tmp_path, "*.log", max_age_days=3) == 1
    file_5d_2 = tmp_path / "five2.log"
    _make_old_file(file_5d_2, age_days=5)
    assert cleanup_old_files(tmp_path, "*.log", max_age_days=10) == 0


def test_cleanup_by_size(tmp_path: Path):
    """超过体积上限的文件应被删除（与 mtime 无关）。"""
    f = tmp_path / "too_big.log"
    f.write_text("x" * 5000, encoding="utf-8")  # 5KB
    # 年龄新（mtime=now），但体积超限 → 删除
    assert cleanup_old_files(tmp_path, "*.log", max_size_bytes=1000) == 1
    assert not f.exists()


def test_cleanup_by_size_keeps_small_files(tmp_path: Path):
    """未超过体积上限的文件应保留。"""
    f = tmp_path / "small.log"
    f.write_text("x" * 100, encoding="utf-8")
    assert cleanup_old_files(tmp_path, "*.log", max_size_bytes=5000) == 0
    assert f.exists()


def test_cleanup_by_size_none_is_noop(tmp_path: Path):
    """max_size_bytes=None 时不按体积清理。"""
    large = tmp_path / "large.log"
    large.write_text("x" * 99999, encoding="utf-8")
    assert cleanup_old_files(tmp_path, "*.log") == 0  # 无 max_size_bytes
    assert large.exists()


def test_cleanup_by_age_or_size_either_wins(tmp_path: Path):
    """年龄或体积任一条件满足即删。"""
    old = tmp_path / "old_small.log"
    old.write_text("x", encoding="utf-8")
    old_ts = time.time() - 10 * 24 * 3600
    os.utime(old, (old_ts, old_ts))
    # 体积小（< 1000），但年龄超 7 天 → 删除
    assert cleanup_old_files(tmp_path, "*.log", max_age_days=7, max_size_bytes=1000) == 1


def test_cleanup_glob_backup_pattern(tmp_path: Path):
    """memory_*.log* 应覆盖活动文件与滚动备份。"""
    active = tmp_path / "memory_dream.log"
    active.write_text("x", encoding="utf-8")
    backup = tmp_path / "memory_dream.log.1"
    backup.write_text("x", encoding="utf-8")
    backup2 = tmp_path / "memory_dream.log.2.gz"
    backup2.write_text("x", encoding="utf-8")
    for f in (active, backup, backup2):
        _make_old_file(f, age_days=10)

    # 旧的 memory_*.log 仅匹配 active，旧的 memory_*.log* 匹配全部
    assert cleanup_old_files(tmp_path, "memory_*.log", max_age_days=7) == 1
    # 重建
    for f in (active, backup, backup2):
        f.write_text("x", encoding="utf-8")
        _make_old_file(f, age_days=10)
    assert cleanup_old_files(tmp_path, "memory_*.log*", max_age_days=7) == 3


def test_cleanup_old_plans_removes_old_only(tmp_path: Path):
    """cleanup_old_plans 只删除超 TTL 的 .md 计划文件。"""
    old_plan = tmp_path / "swift-phoenix.md"
    _make_old_file(old_plan, age_days=10)
    recent_plan = tmp_path / "cosmic-lighthouse.md"
    recent_plan.write_text("x", encoding="utf-8")
    non_md = tmp_path / "notes.txt"
    non_md.write_text("x", encoding="utf-8")

    removed = cleanup_old_plans(tmp_path)
    assert removed == 1
    assert not old_plan.exists()
    assert recent_plan.exists()
    assert non_md.exists()


def test_cleanup_old_plans_missing_dir(tmp_path: Path):
    """计划目录不存在时静默返回 0。"""
    assert cleanup_old_plans(tmp_path / "no-plans") == 0


def test_run_plans_cleanup_once_runs_only_once(tmp_path: Path):
    """run_plans_cleanup_once 每进程只清理一次。"""
    import illusion.utils.log_cleanup as _lc

    old_plan = tmp_path / "old.md"
    _make_old_file(old_plan, age_days=10)

    try:
        # 首次调用执行清理
        assert run_plans_cleanup_once(tmp_path) == 1
        assert not old_plan.exists()

        # 再次放入旧文件，二次调用应被 once 标志拦截，不再清理
        second = tmp_path / "second.md"
        _make_old_file(second, age_days=10)
        assert run_plans_cleanup_once(tmp_path) == 0
        assert second.exists()
    finally:
        # 重置 once 标志，避免污染同进程后续测试
        _lc._plans_cleanup_done = False
