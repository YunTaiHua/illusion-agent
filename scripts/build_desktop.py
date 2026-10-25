#!/usr/bin/env python3
"""
桌面壳构建统一入口
====================

整合运行时下载、后端预装、图标生成、发行包构建的 CLI 入口。

用法：
    python scripts/build_desktop.py              # 运行所有步骤（一键打包）
    python scripts/build_desktop.py --fetch      # 仅下载运行时
    python scripts/build_desktop.py --install    # 仅预装后端到内置 Python
    python scripts/build_desktop.py --icons      # 仅生成图标
    python scripts/build_desktop.py --dist       # 仅构建发行包（需先 fetch + install + icons）
    python scripts/build_desktop.py --all        # fetch + install + icons + dist
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
DESKTOP_DIR = REPO_ROOT / "desktop"


def _resolve_cmd(cmd: list[str]) -> list[str]:
    """Windows 上将 .cmd/.bat 路径补全，避免 WinError 193。"""
    if sys.platform == "win32" and not cmd[0].endswith((".cmd", ".bat", ".exe")):
        for ext in (".cmd", ".bat", ".exe"):
            candidate = cmd[0] + ext
            if Path(candidate).exists():
                cmd = [candidate] + cmd[1:]
                break
    return cmd


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    """运行命令并检查返回码。"""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False)
    if result.returncode != 0:
        print(f"ERROR: Command failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def fetch_runtimes() -> None:
    """下载内置 Python + Node 运行时"""
    print("\n=== Downloading runtimes ===")
    _run([sys.executable, str(SCRIPTS_DIR / "fetch_python.py")])
    _run([sys.executable, str(SCRIPTS_DIR / "fetch_node.py")])


def build_icons() -> None:
    """生成桌面壳图标"""
    print("\n=== Building icons ===")
    # build_icons.py 依赖 Pillow（合成标准 ICO / macOS 模板图标），
    # 构建环境不保证预装（CI 为裸 setup-python），此处幂等安装
    _run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "Pillow"])
    _run([sys.executable, str(SCRIPTS_DIR / "build_icons.py")])


def _plat_arch() -> str:
    """计算当前平台-arch 标识（与 fetch_python.py 保持一致）"""
    import platform
    plat_map = {"win32": "win", "darwin": "mac", "linux": "linux"}
    plat = plat_map.get(sys.platform, "linux")
    mach = platform.machine().lower()
    arch = "arm64" if mach in ("arm64", "aarch64") else "x64"
    return f"{plat}-{arch}"


def _find_bundled_python() -> Path | None:
    """定位内置 Python 可执行文件（兼容不同平台 install_only 解压布局）"""
    base_dir = DESKTOP_DIR / "resources" / "python" / _plat_arch()
    candidates = [
        base_dir / "python" / "python.exe",                   # win: python/python.exe
        base_dir / "python" / "python3",                      # unix: python/python3
        base_dir / "python" / "install" / "bin" / "python",   # unix install_only: python/install/bin/python
        base_dir / "python" / "install" / "bin" / "python3",
        base_dir / "python.exe",                              # win 直解压
        base_dir / "python3",                                 # unix 直解压
        base_dir / "install" / "bin" / "python",              # unix 无 python/ 中间层
        base_dir / "install" / "bin" / "python3",
        base_dir / "bin" / "python",                          # unix bin/ 直接
        base_dir / "bin" / "python3",
    ]
    python_exe = next((p for p in candidates if p.exists()), None)
    # 兜底：rglob 搜索（兼容非标准顶层目录名，如 cpython-3.12.13+...）
    if python_exe is None:
        for name in ("python3", "python.exe"):
            for match in base_dir.rglob(name):
                # 排除 Scripts/（pip 脚本）、lib/（库文件）等非可执行目录
                if "Scripts" not in match.parts and "lib" not in match.parts:
                    python_exe = match
                    break
            if python_exe:
                break
    return python_exe


def _pyproject_version() -> str:
    """读取 pyproject.toml 顶层 version（[project] 段，列 0 起始不受嵌套键干扰）"""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if match is None:
        print("ERROR: pyproject.toml 缺少顶层 version 字段", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def _verify_bundled_runtime(python_exe: Path) -> None:
    """构建断言：内置 Python 已装的 illusion_agent 版本必须与 pyproject 一致。

    --dist 可在未执行 --install 时单独运行，内置 runtime 若为历史构建残留
    （曾在 9 月安装包中封入两个月前的 0.4.6 后端），桌面壳与后端整体错位。
    版本不一致直接让构建失败，杜绝错位产物出厂。
    """
    expected = _pyproject_version()
    proc = subprocess.run(
        [str(python_exe), "-c",
         "import importlib.metadata as m; print(m.version('illusion-agent'))"],
        capture_output=True, text=True,
    )
    actual = proc.stdout.strip() if proc.returncode == 0 else ""
    if proc.returncode != 0 or actual != expected:
        if actual:
            shown = actual
        else:
            tail = proc.stderr.strip().splitlines()
            shown = f"元数据读取失败: {tail[-1] if tail else '未知错误'}"
        print(
            f"ERROR: 内置 Python 的 illusion_agent 版本（{shown}）与 pyproject（{expected}）"
            f"不一致，内置 runtime 为过期残留。请重新执行 --install（必要时先 --fetch）。",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  内置 runtime 校验通过: illusion_agent {actual}")


def _uv_cmd() -> list[str]:
    """返回可用的 uv 命令：PATH 优先；CI 裸环境幂等安装后经 python -m 调用"""
    uv = shutil.which("uv")
    if uv:
        return [uv]
    _run([sys.executable, "-m", "pip", "install", "--quiet",
          "--disable-pip-version-check", "uv"])
    return [sys.executable, "-m", "uv"]


def install_backend() -> None:
    """将 illusion 包与锁定版本依赖预装到内置 Python（让无环境用户解压即用）

    依赖一律按 uv.lock 安装：uv export 生成带哈希的 requirements（--locked
    校验锁与 pyproject 同步，不同步直接失败，需先 uv lock），pip 按清单安装；
    项目本体 --no-deps 从本地源码构建安装。此前 `pip install {项目根}` 每次
    构建浮动解析 PyPI 最新版，打包的 openai/anthropic 等版本随构建日期漂移，
    与开发测试环境从未一致——"开发态正常、打包态必崩"（issue #4 启动卡遮罩
    的根因类别）。
    """
    print("\n=== Installing backend into bundled Python ===")

    # 检测前端 dist 存在（pyproject force-include 需要，缺失则打包进 wheel 的是空目录）
    web_dist = REPO_ROOT / "frontend" / "web" / "dist"
    terminal_dist = REPO_ROOT / "frontend" / "terminal" / "dist"
    if not web_dist.exists() or not terminal_dist.exists():
        print("ERROR: 前端 dist 缺失，请先构建前端：python scripts/build_frontend.py", file=sys.stderr)
        sys.exit(1)

    python_exe = _find_bundled_python()
    if python_exe is None:
        base_dir = DESKTOP_DIR / "resources" / "python" / _plat_arch()
        print(f"ERROR: 内置 Python 不存在于 {base_dir}\n请先运行 --fetch 下载运行时", file=sys.stderr)
        sys.exit(1)

    # 1) 锁定依赖：uv export 产出 requirements（含哈希）。--no-emit-project
    #    排除项目本体行（随后从本地源码 --no-deps 安装）；产物落
    #    desktop/release/（构建产物目录，已 gitignore）
    lock_path = DESKTOP_DIR / "release" / "requirements.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _run(_uv_cmd() + [
        "export", "--locked", "--no-dev", "--extra", "all",
        "--no-emit-project", "--output-file", str(lock_path),
    ])

    # 2) 按锁安装依赖：版本与哈希逐字节等于开发测试环境（uv.lock）
    _run([
        str(python_exe), "-m", "pip", "install",
        "--no-input", "--disable-pip-version-check",
        "-r", str(lock_path),
    ])

    # 3) 项目本体：本地源码构建安装，依赖已由锁清单提供故 --no-deps
    _run([
        str(python_exe), "-m", "pip", "install",
        "--no-input", "--disable-pip-version-check",
        "--no-deps", f"{REPO_ROOT}[all]",
    ])

    # 确保 Scripts/pip.exe 存在：python-build-standalone 运行时不带 pip 入口脚本，
    # 而上面的 install 不会重装已存在的 pip、也就不会生成 exe。无自有 Python
    # 环境的用户（PATH 被注入内置 python）裸 `pip` 命令会 command not found，
    # 只能退而用 `python -m pip`，故此处强制重装 pip 生成入口脚本。
    _run([
        str(python_exe), "-m", "pip", "install",
        "--no-input", "--disable-pip-version-check",
        "--force-reinstall", "--no-deps", "pip",
    ])

    _verify_bundled_runtime(python_exe)


def build_dist() -> None:
    """编译 TypeScript + electron-builder 打包"""
    print("\n=== Building distribution ===")

    # --dist 可单独执行：发行前断言内置 runtime 与当前源码版本一致，
    # 防止跳过 --install 把历史构建残留的后端封进安装包
    python_exe = _find_bundled_python()
    if python_exe is None:
        print("ERROR: 内置 Python 不存在，请先运行 --fetch + --install", file=sys.stderr)
        sys.exit(1)
    _verify_bundled_runtime(python_exe)

    npm = shutil.which("npm")
    if npm is None:
        print("ERROR: npm not found in PATH", file=sys.stderr)
        sys.exit(1)

    # 确保依赖已安装（检测 electron 是否存在）
    node_modules = DESKTOP_DIR / "node_modules"
    if not (node_modules / "electron" / "package.json").exists():
        print("  Installing npm dependencies...")
        _run(_resolve_cmd([npm, "install", "--no-fund", "--no-audit"]), cwd=DESKTOP_DIR)

    # 编译 TypeScript + 打包
    # 传 --publish never 给 electron-builder，禁用其自动发布到 GitHub Release。
    # 我们用单独的 softprops/action-gh-release 步骤上传产物，避免 electron-builder
    # 在检测到 GITHUB_TOKEN 时尝试自动 publish（会因缺少 repository 字段而报错）。
    _run(_resolve_cmd([npm, "run", "dist", "--", "--publish", "never"]), cwd=DESKTOP_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description="桌面壳构建工具")
    parser.add_argument("--fetch", action="store_true", help="仅下载运行时")
    parser.add_argument("--install", action="store_true", help="仅预装后端到内置 Python")
    parser.add_argument("--icons", action="store_true", help="仅生成图标")
    parser.add_argument("--dist", action="store_true", help="仅构建发行包")
    parser.add_argument("--all", action="store_true", help="执行所有步骤（fetch + install + icons + dist）")
    args = parser.parse_args()

    # 默认行为：如果没有指定任何 flag，执行 --all
    if not (args.fetch or args.install or args.icons or args.dist or args.all):
        args.all = True

    if args.all:
        fetch_runtimes()
        install_backend()
        build_icons()
        build_dist()
    else:
        if args.fetch:
            fetch_runtimes()
        if args.install:
            install_backend()
        if args.icons:
            build_icons()
        if args.dist:
            build_dist()

    print("\nDone.")


if __name__ == "__main__":
    main()
