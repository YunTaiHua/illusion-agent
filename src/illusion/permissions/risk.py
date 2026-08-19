"""
操作风险分级模块
================

对工具调用进行风险分级，用于沙箱权限判定：

    - LOW: 只读 / 无副作用操作（读文件、查命令、grep 等）
    - MEDIUM: 一般变更操作（写文件、编辑、增删目录内文件等）
    - HIGH: 高危 / 破坏性操作（rm、git restore、Remove-Item 等）

设计动机：
    高危操作（删除、恢复、强制重置等）的权限等级高于读取。即使某个路径
    已被会话级允许（session allow），涉及高危操作时仍必须重新请求用户确认，
    避免"已放开某文件访问"被误用作删除/还原的通行证。

类说明：
    - RiskLevel: 风险等级枚举
    - DANGEROUS_BASH_PATTERNS: bash/sh 高危命令正则集合
    - DANGEROUS_POWERSHELL_PATTERNS: powershell 高危命令正则集合
    - classify_command_risk: 对 shell 命令进行风险分级
    - classify_tool_risk: 对内置工具调用进行风险分级
    - is_high_risk_command: 判断命令是否为高危命令

使用示例：
    >>> from illusion.permissions.risk import classify_command_risk, RiskLevel
    >>> classify_command_risk("rm -rf build") == RiskLevel.HIGH
    True
"""

from __future__ import annotations

import re
from enum import Enum

# 变更类内置工具（写入 / 编辑文件，属于 MEDIUM 风险）
_MUTATING_TOOLS_SOURCE: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "mcp_auth",
        "send_message",
        "cron",
        "todo_write",
        "task_output",
        "agent",
        "team_create",
        "team_delete",
        "enter_plan_mode",
        "exit_plan_mode",
        "enter_worktree",
        "exit_worktree",
    }
)

# 默认 MEDIUM 变更类工具（内置默认规则，web 端只读展示）
DEFAULT_MEDIUM_RISK_TOOLS: tuple[str, ...] = tuple(sorted(_MUTATING_TOOLS_SOURCE))

# 高危 shell 命令（bash / sh / zsh 等 POSIX shell）
# 每条为一个 (正则, 说明) 元组。正则匹配命令首词及其子命令。
# 注意：只匹配"命令本身"，不匹配参数，避免误伤（如 `git status` 非高危）。
_DANGEROUS_BASH_SPECS: tuple[tuple[str, str], ...] = (
    (r"^\s*(?:/usr)?/s?bin/(rm|rmdir|shred)\b", "删除文件/目录"),
    (r"^\s*(?:env\s+|sudo\s+|time\s+|nice\s+|nohup\s+)*(?:/usr)?/s?bin/(rm|rmdir|shred)\b", "删除文件/目录(带包装器)"),
    (r"^\s*rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+", "rm 递归/强制删除"),
    (r"^\s*rm\s+-[a-zA-Z]*([rf])[a-zA-Z]*\b", "rm 带 -r/-f 删除"),
    (r"^\s*rmdir\s+", "rmdir 删除空目录"),
    (r"^\s*rm\s+", "rm 删除"),
    (r"^\s*shred\s+", "shred 覆写删除"),
    (r"^\s*git\s+restore\b", "git restore 还原工作区"),
    (r"^\s*git\s+reset\s+--hard\b", "git reset --hard 丢弃提交"),
    (r"^\s*git\s+clean\s+(-[a-zA-Z]*[fd][a-zA-Z]*\b|--?force|--?\? )", "git clean 清理未跟踪文件"),
    (r"^\s*git\s+checkout\s+--(\s|$)", "git checkout -- 丢弃修改"),
    (r"^\s*git\s+stash\s+drop\b", "git stash drop 丢弃暂存"),
    (r"^\s*git\s+stash\s+clear\b", "git stash clear 清空暂存"),
    (r"^\s*git\s+branch\s+-D\b", "git branch -D 强制删分支"),
    (r"^\s*git\s+push\s+(--force|-f)\b", "git push 强制推送"),
    (r"^\s*git\s+checkout\s+\S+\s+--\b", "git checkout <rev> -- 还原文件"),
    (r"^\s*truncate\s+", "truncate 截断文件"),
    (r"^\s*dd\s+of=.*\/dev\/(hd|sd|nvme)[a-z0-9]*\b", "dd 写入块设备"),
    (r"^\s*mkfs\.\w+\s+", "mkfs 格式化设备"),
    (r"^\s*dd\s+if=\S+\s+of=\S+\s+bs=\S+\s+count=\S+", "dd 覆写"),
    (r"^\s*rm\s+-rf\s+/\s*$", "rm -rf / 根目录"),
)

# 高危 PowerShell 命令
_DANGEROUS_POWERSHELL_SPECS: tuple[tuple[str, str], ...] = (
    (r"^\s*Remove-Item\b", "Remove-Item 删除"),
    (r"^\s*Del\b", "Del 删除"),
    (r"^\s*Erase\b", "Erase 删除"),
    (r"^\s*rd\b", "rd 删除目录"),
    (r"^\s*rmdir\b", "rmdir 删除目录"),
    (r"^\s*Remove-ItemProperty\b", "Remove-ItemProperty 删除属性"),
    (r"^\s*Clear-Content\b", "Clear-Content 清空内容"),
    (r"^\s*Clear-Item\b", "Clear-Item 清空项"),
    (r"^\s*Remove-Item\s+-Recurse\b", "Remove-Item 递归删除"),
    (r"^\s*Remove-Item\s+-Force\b", "Remove-Item 强制删除"),
    (r"^\s*Format-Volume\b", "Format-Volume 格式化卷"),
    (r"^\s*Clear-Disk\b", "Clear-Disk 清空磁盘"),
)

# 默认高危正则（内置默认规则，web 端只读展示）
DEFAULT_DANGEROUS_BASH_PATTERNS: tuple[str, ...] = tuple(p for p, _ in _DANGEROUS_BASH_SPECS)
DEFAULT_DANGEROUS_POWERSHELL_PATTERNS: tuple[str, ...] = tuple(p for p, _ in _DANGEROUS_POWERSHELL_SPECS)

# 编译后的内置高危规则（默认生效且不可覆盖，规则已内置固定）
_DANGEROUS_BASH: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), d) for p, d in _DANGEROUS_BASH_SPECS
)
_DANGEROUS_POWERSHELL: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), d) for p, d in _DANGEROUS_POWERSHELL_SPECS
)


class RiskLevel(str, Enum):
    """操作风险等级枚举。

    Attributes:
        LOW: 只读 / 无副作用操作
        MEDIUM: 一般变更操作
        HIGH: 高危 / 破坏性操作
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _strip_wrappers(command: str) -> str:
    """剥离常见命令包装器（sudo/env/time/nice/nohup/command）与环境变量前缀。

    while 循环反复剥离直到不再变化，以支持多层/混合包装（如 `sudo sudo rm`、
    `command sudo rm`、`sudo env rm`）；同时剥离形如 `FOO=bar` 的环境变量前缀，
    防止 `FOO=bar rm -rf /` 绕过高危识别。
    """
    cleaned = command.strip()
    # 反复剥离环境变量前缀与包装器，直到不再变化（支持任意层数与混合顺序，
    # 如 `FOO=bar sudo rm`、`command sudo rm`、`sudo sudo rm`）
    while True:
        stripped = False
        # 剥离命令开头的环境变量赋值前缀（如 `FOO=1 BAR=2 cmd`）
        env_m = re.match(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", cleaned)
        if env_m:
            cleaned = cleaned[env_m.end():].strip()
            stripped = True
        for wrapper in ("sudo", "env", "time", "nice", "nohup", "command"):
            m = re.match(rf"^\s*{re.escape(wrapper)}\s+", cleaned)
            if m:
                cleaned = cleaned[m.end():].strip()
                stripped = True
                break
        if stripped:
            continue
        # 剥离前导选项参数（包装器自带的选项+值，如 `nice -n 5 rm`、`env -i cmd`）。
        # 剥离后才是真实命令；真实命令不会以 `-` 开头，故不会误伤 `rm -rf` 等。
        opt_m = re.match(r"^\s*(-[A-Za-z0-9]+\s+)(\S+\s+)?", cleaned)
        if opt_m:
            cleaned = cleaned[opt_m.end():].strip()
            stripped = True
        if not stripped:
            break
    return cleaned


def _compile_patterns(
    patterns: tuple[str, ...] | list[str] | None,
    builtin: tuple[tuple[re.Pattern[str], str], ...],
) -> tuple[tuple[re.Pattern[str], str], ...]:
    """将用户自定义正则字符串编译为 (pattern, 说明) 元组。

    未提供自定义模式时返回内置规则；提供时按用户规则编译（说明为空串）。

    Args:
        patterns: 用户自定义正则字符串序列，None 表示使用内置默认
        builtin: 内置 (编译正则, 说明) 规则

    Returns:
        tuple[tuple[re.Pattern[str], str], ...]: 编译后的规则
    """
    if not patterns:
        return builtin
    return tuple((re.compile(p), "") for p in patterns)


def classify_command_risk(
    command: str,
    *,
    dangerous_bash: tuple[str, ...] | list[str] | None = None,
    dangerous_powershell: tuple[str, ...] | list[str] | None = None,
    read_only_commands: tuple[str, ...] | list[str] | None = None,
) -> RiskLevel:
    """对 shell 命令进行风险分级。

    判断命令是否为高危（破坏性）操作。命令会先剥离包装器再匹配，
    避免 `sudo rm` 绕过检测。

    Args:
        command: 原始命令字符串（bash 或 powershell 均可）
        dangerous_bash: 自定义 bash 高危正则（None 使用内置默认）
        dangerous_powershell: 自定义 powershell 高危正则（None 使用内置默认）
        read_only_commands: 自定义只读命令前缀（None 使用内置默认）

    Returns:
        RiskLevel: 命令的风险等级（HIGH/MEDIUM/LOW）
    """
    if not command or not command.strip():
        return RiskLevel.LOW

    bash_rules = _compile_patterns(dangerous_bash, _DANGEROUS_BASH)
    ps_rules = _compile_patterns(dangerous_powershell, _DANGEROUS_POWERSHELL)

    cleaned = _strip_wrappers(command)

    # 先匹配 bash 高危模式
    for pattern, _desc in bash_rules:
        if pattern.match(cleaned) or pattern.match(command):
            return RiskLevel.HIGH

    # 再匹配 powershell 高危模式
    for pattern, _desc in ps_rules:
        if pattern.match(cleaned) or pattern.match(command):
            return RiskLevel.HIGH

    # 复合命令（&& / || / ; / |）逐段检查，防止 `echo ok && rm -rf x` 绕过
    for segment in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command):
        seg = _strip_wrappers(segment)
        if not seg:
            continue
        for pattern, _desc in bash_rules + ps_rules:
            if pattern.match(seg):
                return RiskLevel.HIGH

    # 含 shell 重定向（> / >> / 2> / &> 等）的命令会写入文件，即使命令前缀是
    # 只读命令（如 `echo x >> file`）也不代表无副作用，保守判为 MEDIUM 需确认。
    if re.search(r"(?:^|[\s;&|])\d*[>&]", cleaned):
        return RiskLevel.MEDIUM

    # 其余命令视为一般变更（MEDIUM）——无法确认为只读时保守分级。
    # 仅当命令明显为只读（无副作用）时才判 LOW。
    if _is_read_only_command(cleaned, read_only_commands):
        return RiskLevel.LOW
    return RiskLevel.MEDIUM


# 默认只读命令前缀（LOW 风险，内置默认规则，web 端只读展示）
DEFAULT_READ_ONLY_COMMANDS: tuple[str, ...] = (
    "ls", "cat", "head", "tail", "grep", "rg", "find", "git status",
    "git diff", "git log", "git show", "pwd", "echo", "which", "type",
    "git branch", "git remote", "git tag", "docker ps", "ps", "whoami",
    "id", "pwd", "date", "printf",
)


def _is_read_only_command(
    cleaned: str,
    read_only_commands: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """判断命令是否明显为只读（无副作用）。

    Args:
        cleaned: 已剥离包装器的命令字符串
        read_only_commands: 用户自定义只读命令前缀（None 使用内置默认）

    Returns:
        bool: 是否为只读命令
    """
    if not cleaned:
        return False
    prefixes = read_only_commands if read_only_commands is not None else DEFAULT_READ_ONLY_COMMANDS
    for prefix in prefixes:
        if cleaned == prefix or cleaned.startswith(prefix + " "):
            return True
    return False


def classify_tool_risk(
    *,
    tool_name: str,
    is_read_only: bool,
    command: str | None = None,
    dangerous_bash: tuple[str, ...] | list[str] | None = None,
    dangerous_powershell: tuple[str, ...] | list[str] | None = None,
    read_only_commands: tuple[str, ...] | list[str] | None = None,
    medium_risk_tools: tuple[str, ...] | list[str] | None = None,
) -> RiskLevel:
    """对工具调用进行风险分级。

    Args:
        tool_name: 工具名称
        is_read_only: 是否为只读工具
        command: 命令字符串（bash/powershell 等命令型工具）
        dangerous_bash: 自定义 bash 高危正则（None 使用内置默认）
        dangerous_powershell: 自定义 powershell 高危正则（None 使用内置默认）
        read_only_commands: 自定义只读命令前缀（None 使用内置默认）
        medium_risk_tools: 自定义 MEDIUM 变更类工具（None 使用内置默认）

    Returns:
        RiskLevel: 工具调用的风险等级
    """
    # 命令型工具：基于命令内容分级
    if command:
        return classify_command_risk(
            command,
            dangerous_bash=dangerous_bash,
            dangerous_powershell=dangerous_powershell,
            read_only_commands=read_only_commands,
        )

    # 只读工具 → LOW
    if is_read_only:
        return RiskLevel.LOW

    # 记忆目录与变更类工具 → MEDIUM
    mutating_tools = medium_risk_tools if medium_risk_tools is not None else _MUTATING_TOOLS_SOURCE
    if tool_name in mutating_tools:
        return RiskLevel.MEDIUM

    # 其余变更工具 → MEDIUM
    return RiskLevel.MEDIUM


def is_high_risk_command(command: str) -> bool:
    """判断命令是否为高危（破坏性）操作。

    Args:
        command: 命令字符串

    Returns:
        bool: True 表示为高危操作
    """
    return classify_command_risk(command) == RiskLevel.HIGH