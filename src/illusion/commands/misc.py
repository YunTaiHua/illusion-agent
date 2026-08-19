"""
杂项斜杠命令
============

/exit, /version, /copy, /export, /share,
/help, /hooks, /reload-plugins, /skills, /continue
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import httpx

from illusion import __version__
from illusion.commands.helpers import copy_to_clipboard, last_message_text
from illusion.commands.types import CommandContext, CommandResult
from illusion.config.settings import load_settings
from illusion.plugins.loader import load_plugins
from illusion.services import export_session_markdown
from illusion.skills.loader import load_skill_registry


async def exit_handler(_: str, context: CommandContext) -> CommandResult:
    """退出程序"""
    del context
    return CommandResult(should_exit=True)


async def version_handler(_: str, context: CommandContext) -> CommandResult:
    """显示版本号"""
    del context
    return CommandResult(message=f"IllusionAgent {_get_current_version()}")


async def copy_handler(args: str, context: CommandContext) -> CommandResult:
    """复制最新回复或指定文本"""
    text = args.strip() or last_message_text(context.engine.messages)
    if not text:
        return CommandResult(message="Nothing to copy.")
    copied, target = copy_to_clipboard(text)
    if copied:
        return CommandResult(message=f"Copied {len(text)} characters to the clipboard.")
    return CommandResult(message=f"Clipboard unavailable. Saved copied text to {target}")


async def export_handler(_: str, context: CommandContext) -> CommandResult:
    """导出当前转录"""
    path = export_session_markdown(cwd=context.cwd, messages=context.engine.messages)
    return CommandResult(message=f"Exported transcript to {path}")


async def share_handler(_: str, context: CommandContext) -> CommandResult:
    """创建可分享的转录快照"""
    path = export_session_markdown(cwd=context.cwd, messages=context.engine.messages)
    return CommandResult(message=f"Created shareable transcript snapshot at {path}")


def make_help_handler(registry: Any) -> Any:
    """创建 help 命令处理器（需要引用 registry 实例）"""

    async def help_handler(args: str, context: CommandContext) -> CommandResult:
        """显示可用命令"""
        return CommandResult(message=registry.help_text())

    return help_handler


async def hooks_handler(_: str, context: CommandContext) -> CommandResult:
    """显示已配置的 hooks"""
    return CommandResult(message=context.hooks_summary or "No hooks configured.")


async def reload_plugins_handler(_: str, context: CommandContext) -> CommandResult:
    """重新加载插件"""
    settings = load_settings()
    plugins = load_plugins(settings, context.cwd)
    if not plugins:
        return CommandResult(message="No plugins discovered.")
    lines = ["Reloaded plugins:"]
    for plugin in plugins:
        state = "enabled" if plugin.enabled else "disabled"
        lines.append(f"- {plugin.manifest.name} [{state}]")
    return CommandResult(message="\n".join(lines))


async def skills_handler(args: str, context: CommandContext) -> CommandResult:
    """列出或显示可用技能"""
    from illusion.skills.loader import get_project_skills_dir, get_user_skills_dir

    skill_registry = load_skill_registry(context.cwd)
    skills = skill_registry.list_skills()

    if not skills:
        return CommandResult(message="No skills available.")

    tokens = args.strip().split()

    # /skills — 列出所有技能
    if not tokens:
        user_skills_dir = get_user_skills_dir()
        project_skills_dir = get_project_skills_dir(context.cwd)
        lines = ["Available skills:", ""]
        if user_skills_dir.exists():
            lines.append(f"User skills directory: {user_skills_dir}")
        if project_skills_dir.exists():
            lines.append(f"Project skills directory: {project_skills_dir}")
        lines.append("")
        for i, skill in enumerate(skills, 1):
            source = f" [{skill.source}]"
            first_line = skill.description.split("\n", 1)[0][:60] if skill.description else "(empty)"
            lines.append(f"  {i}. {skill.name}{source}  —  {first_line}")
        lines.append("")
        lines.append("Usage: /skills <name|number>  — view a specific skill")
        return CommandResult(message="\n".join(lines))

    # /skills <name|number> — 显示指定技能内容
    target = tokens[0]
    selected = None

    # 按序号查找
    try:
        idx = int(target) - 1
        if 0 <= idx < len(skills):
            selected = skills[idx]
    except ValueError:
        pass

    # 按名称查找
    if selected is None:
        for skill in skills:
            if skill.name.lower() == target.lower():
                selected = skill
                break

    if selected is None:
        return CommandResult(message=f"Skill not found: {target}. Use /skills to list available skills.")

    return CommandResult(message=selected.content)


def _check_pypi_latest() -> str | None:
    """查询 PyPI 获取 illusion-agent 最新版本号

    Returns:
        str | None: 最新版本号，查询失败返回 None
    """
    try:
        resp = httpx.get(
            "https://pypi.org/pypi/illusion-agent/json",
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return str(resp.json()["info"]["version"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def _get_current_version() -> str:
    """获取当前实际运行代码的版本号

    直接使用源码声明的 __version__（与 pyproject.toml 同步）。
    不使用 importlib.metadata：可编辑安装（-e）时 dist-info 停留在安装时刻，
    会滞后于实际运行的源码版本，导致更新检测误报。

    Returns:
        str: 当前版本号
    """
    return __version__


def _run_pip_upgrade(packages: list[str]) -> tuple[bool, str]:
    """执行 pip install --upgrade

    Args:
        packages: 要升级的包名列表

    Returns:
        tuple[bool, str]: (是否成功, 输出信息)
    """
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "pip upgrade timed out"
    except OSError as exc:
        return False, str(exc)


def _run_pip_install(pkgs: list[str]) -> tuple[bool, str]:
    """通过 pip install 安装指定包（渠道依赖首次配置时调用）

    复用 _run_pip_upgrade 的子进程调用模式，但使用 install 子命令（不带 --upgrade）。

    Args:
        pkgs: 要安装的包名列表（含版本约束）

    Returns:
        tuple[bool, str]: (是否成功, 输出文本)
    """
    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "pip install timed out"
    except OSError as exc:
        return False, str(exc)


async def continue_handler(args: str, context: CommandContext) -> CommandResult:
    """继续被中断的工具循环"""
    raw = args.strip()
    if not context.engine.has_pending_continuation():
        return CommandResult(message="Nothing to continue (no pending tool results).")

    turns: int | None = None
    if raw:
        tokens = raw.split()
        if tokens[0] == "set" and len(tokens) == 2:
            raw = tokens[1]
        try:
            turns = int(raw)
        except ValueError:
            return CommandResult(message="Usage: /continue [COUNT]")
        turns = max(1, min(turns, 512))

    return CommandResult(
        message="Continuing pending tool loop...",
        continue_pending=True,
        continue_turns=turns,
    )
