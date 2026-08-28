#!/usr/bin/env python3
"""
内置 Node.js 运行时下载脚本
============================

从 nodejs.org 官方下载 tarball / zip，解压到 desktop/resources/node/<plat-arch>/。

用法：
    python scripts/fetch_node.py                   # 默认 24.20.0
    python scripts/fetch_node.py --version 22.13.1

解压使用 Python 标准库 tarfile + zipfile（跨平台兼容）。

路径约定：
    DESKTOP_ROOT = desktop/
    输出: DESKTOP_ROOT/resources/node/<plat-arch>/
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parent.parent / "desktop"
RESOURCES = DESKTOP_ROOT / "resources"

DEFAULT_VERSION = "24.20.0"

# node 官方 tarball 三元组
NODE_TRIPLE_MAP = {
    ("win32", "x64"): "win-x64",
    ("win32", "arm64"): "win-arm64",
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "x64"): "darwin-x64",
    ("linux", "x64"): "linux-x64",
    ("linux", "arm64"): "linux-arm64",
}

# 桌面壳内部用的平台-arch 标识（与 runtime.ts platArch 保持一致）
PLAT_ARCH_MAP = {
    "win32": "win",
    "darwin": "mac",
    "linux": "linux",
}


def node_triple() -> str:
    """node 官方 tarball 三元组"""
    import platform
    mach = platform.machine().lower()
    arch = "arm64" if mach in ("arm64", "aarch64") else "x64"
    key = (sys.platform, arch)
    result = NODE_TRIPLE_MAP.get(key)
    if result is None:
        print(f"不支持的平台：{key[0]}-{key[1]}", file=sys.stderr)
        sys.exit(1)
    return result


def plat_arch() -> str:
    """桌面壳内部用的平台-arch 标识"""
    import platform
    plat = PLAT_ARCH_MAP.get(sys.platform, "linux")
    mach = platform.machine().lower()
    arch = "arm64" if mach in ("arm64", "aarch64") else "x64"
    return f"{plat}-{arch}"


def main() -> None:
    parser = argparse.ArgumentParser(description="下载内置 Node.js 运行时")
    parser.add_argument("--version", default=DEFAULT_VERSION, help=f"Node 版本（默认 {DEFAULT_VERSION}）")
    args = parser.parse_args()

    version = args.version
    triple = node_triple()
    is_win = sys.platform == "win32"
    ext = "zip" if is_win else "tar.gz"
    name = f"node-v{version}-{triple}.{ext}"
    url = f"https://nodejs.org/dist/v{version}/{name}"

    out_dir = RESOURCES / "node" / plat_arch()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / name

    print(f"下载 {name}\n  {url}")
    urllib.request.urlretrieve(url, archive)

    print(f"解压到 {out_dir}")
    if is_win:
        # Windows: 下载的是 zip
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(out_dir)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(out_dir)
    archive.unlink()

    # node 解压后会多一层 node-vX.X.X-<triple>/ 目录，提升到 outDir 根
    extracted = out_dir / f"node-v{version}-{triple}"
    if extracted.exists():
        for entry in extracted.iterdir():
            dst = out_dir / entry.name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            shutil.move(str(entry), str(dst))
        extracted.rmdir()

    print(f"Node {version} -> {out_dir}")


if __name__ == "__main__":
    main()
