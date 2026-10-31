"""
版本同步脚本
============

从 pyproject.toml 读取版本号，生成前端版本文件。

使用方法：
    python scripts/sync_version.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def read_version_from_pyproject() -> str:
    """从 pyproject.toml 读取版本号"""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_path}")

    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    version = data.get("project", {}).get("version")
    if not version:
        raise ValueError("Version not found in pyproject.toml")

    return version


def generate_terminal_version_file(version: str) -> None:
    """生成终端前端版本文件"""
    content = f"""/**
 * 版本信息模块
 *
 * 此文件由 scripts/sync_version.py 自动生成，请勿手动修改。
 * 版本号来源于 pyproject.toml。
 */

/** 当前版本号 */
export const VERSION = '{version}';
"""
    output_path = Path(__file__).parent.parent / "frontend" / "terminal" / "src" / "version.ts"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"Generated: {output_path}")


def generate_web_version_file(version: str) -> None:
    """生成 Web 前端版本文件"""
    content = f"""/**
 * 版本信息模块
 *
 * 此文件由 scripts/sync_version.py 自动生成，请勿手动修改。
 * 版本号来源于 pyproject.toml。
 */

/** 当前版本号 */
export const VERSION = '{version}';
"""
    output_path = Path(__file__).parent.parent / "frontend" / "web" / "src" / "version.ts"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"Generated: {output_path}")


def update_init_version(init_path: Path, version: str) -> None:
    """更新 __init__.py 中的 __version__"""
    if not init_path.exists():
        print(f"Warning: {init_path} not found, skipping")
        return

    content = init_path.read_text(encoding="utf-8")
    import re
    new_content, count = re.subn(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{version}"',
        content,
    )
    if count == 0:
        print(f"Warning: __version__ not found in {init_path}, skipping")
        return

    if content == new_content:
        print(f"Version already up to date: {init_path}")
        return

    init_path.write_text(new_content, encoding="utf-8")
    print(f"Updated version in: {init_path}")


def update_package_json_version(package_path: Path, version: str) -> None:
    """更新 package.json 中的版本号"""
    if not package_path.exists():
        print(f"Warning: {package_path} not found, skipping")
        return

    with package_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("version") == version:
        print(f"Version already up to date: {package_path}")
        return

    data["version"] = version
    with package_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Updated version in: {package_path}")


def update_package_lock_version(package_path: Path, version: str) -> None:
    """更新 package-lock.json 中的版本号

    更新顶层 version 和 packages[""] 中的 version。

    Args:
        package_path: package-lock.json 路径
        version: 目标版本号
    """
    if not package_path.exists():
        print(f"Warning: {package_path} not found, skipping")
        return

    with package_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    changed = False

    # 更新顶层 version
    if data.get("version") != version:
        data["version"] = version
        changed = True

    # 更新 packages[""] 中的 version（npm v2+ 格式）
    packages = data.get("packages", {})
    root_pkg = packages.get("", {})
    if root_pkg.get("version") != version:
        root_pkg["version"] = version
        packages[""] = root_pkg
        data["packages"] = packages
        changed = True

    if not changed:
        print(f"Version already up to date: {package_path}")
        return

    with package_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Updated version in: {package_path}")


def sync_uv_lock() -> None:
    """运行 `uv lock` 同步 uv.lock 中项目自身版本号

    release-desktop CI 通过 `uv export --locked` 导出依赖，--locked 会严格
    校验 uv.lock 与 pyproject.toml 一致；版本号变更后若不同步，三平台
    桌面打包会全部失败。uv lock 只更新项目自身版本号一行，依赖零变化。
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "uv", "lock"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("Warning: uv not available (python -m uv failed), skipping uv.lock sync")
        return

    if result.returncode == 0:
        if result.stdout.strip():
            print(result.stdout.strip())
        print("Synced uv.lock via `uv lock`.")
    else:
        print(f"Warning: uv lock failed (exit {result.returncode}), skipping")
        if result.stderr.strip():
            print(result.stderr.strip())


def main() -> None:
    """主函数"""
    version = read_version_from_pyproject()
    print(f"Version from pyproject.toml: {version}")

    generate_terminal_version_file(version)
    generate_web_version_file(version)

    # 更新 __init__.py 和 package.json 版本号
    root = Path(__file__).parent.parent
    update_init_version(root / "src" / "illusion" / "__init__.py", version)
    update_package_json_version(root / "frontend" / "web" / "package.json", version)
    update_package_json_version(root / "frontend" / "terminal" / "package.json", version)
    update_package_lock_version(root / "frontend" / "web" / "package-lock.json", version)
    update_package_lock_version(root / "frontend" / "terminal" / "package-lock.json", version)

    # 同步桌面壳版本号
    update_package_json_version(root / "desktop" / "package.json", version)
    update_package_lock_version(root / "desktop" / "package-lock.json", version)

    # 同步 uv.lock（uv export --locked 严格校验，不同步会导致桌面打包 CI 失败）
    sync_uv_lock()

    print("Version sync completed successfully.")


if __name__ == "__main__":
    main()
