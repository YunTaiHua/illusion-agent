"""
Hatch 构建钩子
=============

在 wheel 构建前自动执行前端构建（npm install + npm run build）。
需要 Node.js 18+ 环境。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """在 wheel 构建前自动构建 terminal 和 web 前端。"""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        """在构建开始前执行前端构建。"""
        root = Path(self.root)
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "Node.js/npm is not installed. "
                "Please install Node.js 18+ from https://nodejs.org/ "
                "to build the frontend assets."
            )

        # 同步版本号
        self._sync_version(root)

        self._build_frontend(root, npm, "terminal")
        self._build_frontend(root, npm, "web")
        self._build_browser_runtime(root, npm)

    def _build_browser_runtime(self, root: Path, npm: str) -> None:
        """构建 Browser Use node_repl 运行时（browser_runtime/ → dist/）。"""
        runtime_dir = root / "browser_runtime"
        if not (runtime_dir / "package.json").exists():
            print("hatch_build: skipping browser_runtime (no package.json)")
            return
        dist_dir = runtime_dir / "dist"
        if (dist_dir / "mcp-server.js").is_file():
            print("hatch_build: skipping browser_runtime (dist already exists)")
            return
        print("hatch_build: building browser_runtime...")
        self._run([npm, "install", "--no-fund", "--no-audit"], runtime_dir)
        self._run([npm, "run", "build"], runtime_dir)

    def _build_frontend(self, root: Path, npm: str, name: str) -> None:
        """构建单个前端。"""
        frontend_dir = root / "frontend" / name
        if not (frontend_dir / "package.json").exists():
            print(f"hatch_build: skipping {name} (no package.json)")
            return

        # 如果 dist 已存在则跳过构建
        dist_dir = frontend_dir / "dist"
        if dist_dir.exists() and any(dist_dir.iterdir()):
            print(f"hatch_build: skipping {name} (dist already exists)")
            return

        print(f"hatch_build: building {name} frontend...")

        # npm install（npm 会自动跳过已安装的包）
        self._run([npm, "install", "--no-fund", "--no-audit"], frontend_dir)

        # npm run build
        self._run([npm, "run", "build"], frontend_dir)

    def _sync_version(self, root: Path) -> None:
        """同步版本号到前端文件。"""
        sync_script = root / "scripts" / "sync_version.py"
        if sync_script.exists():
            print("hatch_build: syncing version...")
            result = subprocess.run(
                [sys.executable, str(sync_script)],
                cwd=str(root),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"Warning: Version sync failed: {result.stderr}")
            else:
                print(result.stdout.strip())

    @staticmethod
    def _resolve_cmd(cmd: list[str]) -> list[str]:
        """Windows 上将 .cmd/.bat 路径补全，避免 WinError 193。"""
        if sys.platform == "win32" and not cmd[0].endswith((".cmd", ".bat", ".exe")):
            for ext in (".cmd", ".bat", ".exe"):
                candidate = cmd[0] + ext
                if Path(candidate).exists():
                    cmd = [candidate] + cmd[1:]
                    break
        return cmd

    @staticmethod
    def _fix_node_env(npm_path: str) -> dict[str, str]:
        """将 npm 所在目录插入 PATH 前面，确保 npm.cmd 内部找到正确的 node.exe。

        某些环境（如通过 pip 安装的 nodejs-wheel 包）会在 Python Scripts
        目录中放置一个假的 node.exe，导致 npm.cmd 内部调用 node 时失败。
        npm.cmd 使用 %~dp0\\node.exe 引用同目录的 node，所以我们把 npm
        所在目录加到 PATH 最前面，让 npm.cmd 能找到真正的 node。
        """
        import os

        npm_dir = str(Path(npm_path).parent)
        env = dict(os.environ)
        current_path = env.get("PATH", "")
        if not current_path.startswith(npm_dir):
            env["PATH"] = npm_dir + ";" + current_path
        return env

    def _run(self, cmd: list[str], cwd: Path) -> None:
        """运行命令，失败时抛出异常。"""
        cmd = self._resolve_cmd(cmd)
        env = self._fix_node_env(cmd[0])
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {' '.join(cmd)}\n"
                f"stdout: {result.stdout[-500:]}\n"
                f"stderr: {result.stderr[-500:]}"
            )
