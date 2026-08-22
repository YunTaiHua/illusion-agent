"""原子写入工具
================

提供文件原子写入功能，确保写入过程中崩溃不会损坏目标文件。

原理：先写入同目录临时文件，再通过 os.replace 原子替换目标文件。
os.replace 在 POSIX 和 Windows 上均为原子操作（同文件系统）。

主要函数：
    - atomic_write_text: 原子写入文本文件
    - atomic_write_bytes: 原子写入二进制文件

使用示例：
    >>> from illusion.utils.atomic_write import atomic_write_text
    >>> atomic_write_text(Path("/tmp/config.json"), '{"key": "value"}')
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

# os.replace 重试参数：Windows 上目标文件可能被其他进程短暂占用
# （杀毒软件实时扫描、编辑器索引、并发的原子写入竞争），导致 WinError 5。
_REPLACE_RETRIES: int = 3
_REPLACE_BASE_DELAY: float = 0.1  # 指数退避：0.1s / 0.2s / 0.4s


def _replace_with_retry(tmp_path: str, target: str) -> None:
    """原子替换目标文件，失败时指数退避重试。

    仅重试 OSError（含 WinError 5 权限拒绝），最终失败时抛出原异常。
    """
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp_path, target)
            return
        except OSError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_BASE_DELAY * (2**attempt))


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """原子写入文本文件

    写入同目录临时文件后通过 os.replace 原子替换目标文件，
    确保写入过程中崩溃不会留下损坏文件。

    Args:
        path: 目标文件路径
        content: 要写入的文本内容
        encoding: 文本编码，默认 utf-8
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # 在同目录创建临时文件（确保同文件系统，os.replace 才能原子）
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".~",
        suffix=path.suffix,
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp_path, str(path))
    except BaseException:
        # 写入失败时清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_bytes(
    path: Path,
    content: bytes,
) -> None:
    """原子写入二进制文件

    写入同目录临时文件后通过 os.replace 原子替换目标文件，
    确保写入过程中崩溃不会留下损坏文件。

    Args:
        path: 目标文件路径
        content: 要写入的二进制内容
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".~",
        suffix=path.suffix,
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
