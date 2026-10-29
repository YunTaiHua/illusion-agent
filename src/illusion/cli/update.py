"""
自更新子命令
============

提供 IllusionAgent 的自更新功能。

子命令:
    - update: 检查并更新到最新版本
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from illusion.cli import app
from illusion.config.i18n import t

if TYPE_CHECKING:
    from illusion.commands.types import CommandResult


@app.command("update")
def update_cmd(
    deps: bool = typer.Option(False, "--deps", help="同时更新依赖 / Also update dependencies"),
) -> None:
    """检查并更新 IllusionAgent

    查询 PyPI 获取最新版本，对比后交互式确认更新。
    """
    import asyncio

    async def _run() -> None:
        result = await _update_cli("--deps" if deps else "")
        if result.message:
            print(result.message)

    asyncio.run(_run())


# 文件占用类错误关键词（小写匹配）
_LOCK_KEYWORDS = (
    "permission denied",
    "access is denied",
    "being used by another process",
    "permissionerror",
)


def _is_locked_by_running_process(output: str) -> bool:
    """检测 pip 输出是否表明文件被运行中的进程锁定。"""
    lower = output.lower()
    return any(keyword in lower for keyword in _LOCK_KEYWORDS)


async def _update_cli(args: str) -> CommandResult:
    """CLI 更新入口，复用 handler 逻辑"""
    from pathlib import Path

    from illusion.commands.misc import (
        _check_pypi_latest,
        _get_current_version,
        _run_pip_upgrade,
    )
    from illusion.commands.types import CommandResult

    include_deps = "--deps" in args

    current = _get_current_version()
    print(t("update_checking"))
    latest = _check_pypi_latest()

    if latest is None:
        print(t("update_network_error"))
        print(t("update_installing"))
        ok, output = _run_pip_upgrade(["illusion-agent"])
        if ok:
            new_ver = _get_current_version()
            return CommandResult(message=t("update_success", version=new_ver))
        if _is_locked_by_running_process(output):
            return CommandResult(message=t("update_locked_by_running_process"))
        return CommandResult(message=t("update_failed", error=output[:200]))

    if latest == current:
        msg = t("update_latest", version=current)
        if not include_deps:
            return CommandResult(message=msg)
        print(msg)
    else:
        print(t("update_available", current=current, latest=latest))
        print(t("update_confirm"))
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            return CommandResult(message="Cancelled.")

        print(t("update_installing"))
        ok, output = _run_pip_upgrade(["illusion-agent"])
        if ok:
            print(t("update_success", version=latest))
        else:
            if _is_locked_by_running_process(output):
                return CommandResult(message=t("update_locked_by_running_process"))
            return CommandResult(message=t("update_failed", error=output[:200]))

    if include_deps:
        # tomllib 是 Python 3.11+ 标准库，低版本回退到 tomli 第三方包
        import tomllib as _tomllib

        print(t("update_deps_checking"))
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not pyproject_path.exists():
            pyproject_path = Path.cwd() / "pyproject.toml"

        if pyproject_path.exists():
            with pyproject_path.open("rb") as f:
                data = _tomllib.load(f)
            deps = data.get("project", {}).get("dependencies", [])
            pkg_names = []
            for dep in deps:
                name = dep.split(">=")[0].split("==")[0].split("<=")[0].split("~=")[0].split("[")[0].strip()
                pkg_names.append(name)

            if pkg_names:
                print(t("update_deps_available"))
                for pkg in pkg_names:
                    print(f"  - {pkg}")
                print(t("update_deps_confirm"))
                try:
                    input()
                except (KeyboardInterrupt, EOFError):
                    return CommandResult(message="Cancelled.")

                ok, output = _run_pip_upgrade(pkg_names)
                if ok:
                    return CommandResult(message=t("update_deps_success"))
                if _is_locked_by_running_process(output):
                    return CommandResult(message=t("update_locked_by_running_process"))
                return CommandResult(message=t("update_failed", error=output[:200]))

    return CommandResult(message="")
