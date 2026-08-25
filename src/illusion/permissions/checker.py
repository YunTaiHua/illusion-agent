"""
权限检查模块
============

本模块实现权限检查功能，用于控制工具执行的权限。

主要功能：
    - 检查工具是否允许执行
    - 支持路径级别的权限规则
    - 支持命令级别的权限规则
    - 根据权限模式决定是否需要用户确认

类说明：
    - PermissionDecision: 权限决策结果
    - PathRule: 路径权限规则
    - PermissionChecker: 权限检查器

使用示例：
    >>> from illusion.permissions import PermissionChecker, PermissionDecision, PermissionMode
    >>> from illusion.config.settings import PermissionSettings
    >>> checker = PermissionChecker(settings)
    >>> decision = checker.evaluate("Bash", is_read_only=False, file_path="/path/to/file")
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from illusion.config.settings import PermissionSettings
from illusion.memory.paths import is_in_memory_dir
from illusion.permissions.modes import PermissionMode
from illusion.permissions.risk import RiskLevel, classify_command_risk, classify_tool_risk

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PermissionDecision:
    """权限决策结果

    表示检查工具调用是否允许执行的结果。

    Attributes:
        allowed: 是否允许执行该工具
        requires_confirmation: 是否需要用户确认
        reason: 决策原因说明
        auto_blocked: 是否为系统自动阻止（如计划模式），而非用户显式拒绝
        sandbox_blocked: 是否被沙箱限制阻止（需要用户确认）
        sandbox_denied_path: 被沙箱拒绝的路径（用于会话级允许）
        high_risk: 是否为高危操作（如删除/还原），即使会话级允许也需重新确认
        risk: 操作风险等级（low/medium/high）
    """

    allowed: bool  # 是否允许执行
    requires_confirmation: bool = False  # 是否需要用户确认
    reason: str = ""  # 决策原因
    auto_blocked: bool = False  # 系统自动阻止（计划模式等）
    sandbox_blocked: bool = False  # 被沙箱限制阻止
    sandbox_denied_path: str = ""  # 被沙箱拒绝的路径
    high_risk: bool = False  # 高危操作（删除/还原等），会话级允许不豁免
    risk: RiskLevel = RiskLevel.LOW  # 操作风险等级


@dataclass(frozen=True)
class PathRule:
    """基于 glob 模式的路径权限规则
    
    用于控制对特定路径的访问权限。
    
    Attributes:
        pattern: glob 模式字符串
        allow: True 表示允许，False 表示拒绝
    """

    pattern: str  # glob 模式
    allow: bool  # True = 允许, False = 拒绝


class PermissionChecker:
    """权限检查器
    
    根据配置的权限模式和规则评估工具使用权限。
    
    Attributes:
        _settings: 权限设置对象
        _path_rules: 解析后的路径规则列表
    
    使用示例：
        >>> checker = PermissionChecker(settings)
        >>> decision = checker.evaluate("Read", is_read_only=True)
    """

    def __init__(self, settings: PermissionSettings) -> None:
        """初始化权限检查器

        Args:
            settings: 权限设置对象
        """
        self._settings = settings
        # 进入计划模式前的权限模式，用于退出时恢复
        self._pre_plan_mode: PermissionMode | None = None
        # 当前计划文件路径（plan mode 下豁免写入限制）
        self._plan_file_path: str | None = None
        # 沙箱文件系统限制路径（通过 sync_sandbox_restrictions 设置）
        # 对齐 OS 级沙箱语义：写入默认拒绝（仅 allow_write 内可写），
        # 读取默认允许（仅 deny_read 限制）。deny_* 覆盖 allow_*。
        self._sandbox_allow_write: list[str] = []
        self._sandbox_deny_write: list[str] = []
        self._sandbox_deny_read: list[str] = []
        # 会话级沙箱允许路径（不持久化，重启后清除）
        self._session_allowed_paths: set[str] = set()
        # 自动审批粘滞拒绝缓存（会话级）：同一操作（tool + 规范化 path/command）
        # 被判官 DENY 后，重试不再重新掷骰子——直接视为"审核已拒绝"降级人工，
        # 保证无人值守 full_auto 下结论确定且不放大 LLM 成本。ALLOW 不缓存。
        self._auto_review_denied_keys: set[str] = set()
        # 风险分级规则使用 risk.py 内置默认（LOW/MEDIUM/HIGH），固定不可自定义
        # 从设置中解析路径规则
        self._path_rules: list[PathRule] = []
        for rule in getattr(settings, "path_rules", []):
            pattern = getattr(rule, "pattern", None) or (rule.get("pattern") if isinstance(rule, dict) else None)
            allow = getattr(rule, "allow", True) if not isinstance(rule, dict) else rule.get("allow", True)
            if isinstance(pattern, str) and pattern.strip():
                self._path_rules.append(PathRule(pattern=pattern.strip(), allow=allow))
            else:
                log.warning(
                    "跳过路径规则，pattern 字段缺失为空或非字符串: %r",
                    rule,
                )

    @property
    def current_mode(self) -> PermissionMode:
        """返回当前权限模式。"""
        return self._settings.mode

    def set_mode(self, mode: PermissionMode) -> None:
        """立即切换权限模式，保存前一个模式以便恢复。

        仅在尚未保存前一个模式时才保存（防止重复调用覆盖原始模式）。

        Args:
            mode: 目标权限模式
        """
        if self._pre_plan_mode is None:
            self._pre_plan_mode = self._settings.mode
        self._settings.mode = mode

    def restore_mode(self) -> None:
        """恢复到进入计划模式之前的权限模式，并清理计划文件路径。"""
        if self._pre_plan_mode is not None:
            self._settings.mode = self._pre_plan_mode
            self._pre_plan_mode = None
        self._plan_file_path = None

    def set_plan_file(self, file_path: str) -> None:
        """设置当前计划文件路径，使其在 plan mode 下可写。

        路径会被规范化为 resolve 后的绝对路径，确保符号链接等场景下比较正确。

        Args:
            file_path: 计划文件的绝对路径
        """
        from pathlib import Path
        self._plan_file_path = str(Path(file_path).expanduser().resolve())

    def sync_sandbox_restrictions(
        self,
        sandbox_settings: Any,
        *,
        working_directory: Path | str | None = None,
    ) -> None:
        """将沙箱文件系统限制同步到权限规则

        使文件工具（Read/Edit/Write/Grep/Glob）也受沙箱路径限制约束，
        对齐 OS 级沙箱语义：
            - 写入：默认拒绝，仅 allow_write 白名单内可写；deny_write 覆盖
            - 读取：默认允许，仅 deny_read 限制

        Args:
            sandbox_settings: SandboxSettings 对象
            working_directory: 工作目录，用于解析相对路径（如 "."）。
                None 时使用当前进程工作目录
        """
        self._sandbox_allow_write = []
        self._sandbox_deny_write = []
        self._sandbox_deny_read = []
        # 沙箱永远开启：不再读取 enabled 开关，无条件加载文件系统限制
        fs = getattr(sandbox_settings, "filesystem", None)
        if fs is None:
            return

        base = Path(working_directory or Path.cwd()).expanduser().resolve()
        self._sandbox_allow_write = [
            self._resolve_sandbox_path(p, base)
            for p in getattr(fs, "allow_write", [])
            if p
        ]
        self._sandbox_deny_write = [
            self._resolve_sandbox_path(p, base)
            for p in getattr(fs, "deny_write", [])
            if p
        ]
        self._sandbox_deny_read = [
            self._resolve_sandbox_path(p, base)
            for p in getattr(fs, "deny_read", [])
            if p
        ]

    @staticmethod
    def _resolve_sandbox_path(path: str, base: Path) -> str:
        """将沙箱路径规则规范化为可比较的绝对路径。

        相对路径（如 "."）基于工作目录解析；绝对路径保留原样（仅展开 ~，
        不做盘符重解释），统一使用 normpath 归一化，便于与 file_path 匹配。

        Args:
            path: 规则路径（可为相对路径、绝对路径或 glob 模式）
            base: 工作目录基准

        Returns:
            str: 规范化后的路径
        """
        p = Path(path).expanduser()
        p_str = str(p)
        # 绝对 POSIX 路径（原始前导 /）保留原样，避免 Windows 下
        # Path("/x") 被重解释为当前盘符根目录。
        if path.strip().startswith("/"):
            return os.path.normpath(path.strip())
        # Windows 绝对路径（盘符、UNC）保留原样
        if re.match(r"^[A-Za-z]:", p_str) or p_str.startswith("\\\\"):
            return os.path.normpath(p_str)
        if not p.is_absolute():
            p = base / p
        return os.path.normpath(str(p))

    @staticmethod
    def _path_matches(pattern: str, target: str) -> bool:
        """判断目标路径是否命中沙箱规则（支持 glob 与目录前缀匹配）。

        Args:
            pattern: 规则路径（可为 glob 模式或纯目录）
            target: 目标绝对路径

        Returns:
            bool: 是否命中
        """
        if fnmatch.fnmatch(target, pattern):
            return True
        # 非 glob 的纯目录规则：使用目录前缀匹配（target 位于 pattern 目录下）
        if not any(ch in pattern for ch in "*?[{"):
            pat = os.path.normpath(str(pattern))
            tgt = os.path.normpath(str(target))
            if tgt == pat or tgt.startswith(pat + os.sep):
                return True
        return False

    @staticmethod
    def _command_matches_allowlist(command: str, allowlist: list[str]) -> bool:
        """判断命令是否命中命令级白名单（前缀匹配，要求词边界）。

        白名单项为命令前缀：命中"相等"或以"前缀 + 空格"开头即放行，
        避免 `ls` 误匹配 `lsof`。对应 settings.json 的 allowed_shell_commands。

        Args:
            command: 待判断的命令字符串
            allowlist: 命令级白名单前缀列表

        Returns:
            bool: 是否命中白名单
        """
        cleaned = command.strip()
        for prefix in allowlist:
            p = prefix.strip()
            if not p:
                continue
            if cleaned == p or cleaned.startswith(p + " "):
                return True
        return False

    def allow_sandbox_path_for_session(self, path: str) -> None:
        """将路径添加到会话级沙箱允许列表（不持久化）

        Args:
            path: 要允许的路径
        """
        self._session_allowed_paths.add(path)

    def clear_session_allowed_paths(self) -> None:
        """清空会话级沙箱允许列表"""
        self._session_allowed_paths.clear()

    @staticmethod
    def auto_review_denied_key(
        tool_name: str,
        file_path: str | None = None,
        command: str | None = None,
        session_id: str = "",
    ) -> str:
        """构造自动审批粘滞拒绝的缓存键（会话 + tool + 规范化 path/command）

        会话前缀隔离：Web 多会话下各会话独立判定，不跨会话泄漏。
        command 继规范化（大小写/空白折叠 / 仅用于缓存键，不改变原命令）。
        """
        import os as _os

        norm_path = (
            _os.path.normcase(_os.path.normpath(file_path)) if file_path else ""
        )
        norm_cmd = " ".join((command or "").split()).casefold()
        return f"{session_id}|{tool_name}|{norm_path}|{norm_cmd}"

    def note_auto_review_denied(self, key: str) -> None:
        """记录一次判官 DENY（会话内粘滞，重试同操作跳过重新审核）"""
        self._auto_review_denied_keys.add(key)

    def is_auto_review_denied(self, key: str) -> bool:
        """该操作此前是否已被判官 DENY（本会话内粘滞）"""
        return key in self._auto_review_denied_keys

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ) -> PermissionDecision:
        """评估工具是否允许执行
        
        根据权限模式和规则检查工具是否可以立即执行。
        
        Args:
            tool_name: 工具名称
            is_read_only: 是否为只读工具
            file_path: 相关的文件路径
            command: 执行的命令字符串
        
        Returns:
            PermissionDecision: 权限决策结果
        """
        # 计算操作风险等级（删除/还原等高危操作会覆盖会话级允许）。
        # 使用 risk.py 内置默认规则（LOW/MEDIUM/HIGH），不传自定义规则。
        risk = classify_tool_risk(
            tool_name=tool_name,
            is_read_only=is_read_only,
            command=command,
        )
        high_risk = risk == RiskLevel.HIGH

        # 显式的工具拒绝列表
        if tool_name in self._settings.denied_tools:
            return PermissionDecision(allowed=False, reason=f"{tool_name} is explicitly denied", risk=risk)

        # 显式的工具允许列表
        if tool_name in self._settings.allowed_tools:
            return PermissionDecision(allowed=True, reason=f"{tool_name} is explicitly allowed", risk=risk)

        # 检查路径级别规则
        if file_path and self._path_rules:
            for rule in self._path_rules:
                if fnmatch.fnmatch(file_path, rule.pattern) and not rule.allow:
                    return PermissionDecision(
                        allowed=False,
                        reason=f"Path {file_path} matches deny rule: {rule.pattern}",
                        risk=risk,
                    )

        # 主对话 LLM 在手动模式下直接 Write/Edit 记忆文件（~/.illusion/memory/
        # 或自定义 memory.directory）时无需用户确认。放在 path_rules 之后
        # （用户显式 deny 规则优先）与沙箱检查之前（记忆目录是 agent 自身
        # 存储区域，豁免沙箱限制）。plan 模式显式排除：变更类操作仍被拦截。
        if (
            not is_read_only
            and file_path
            and tool_name in ("write_file", "edit_file")
            and self._settings.mode != PermissionMode.PLAN
            and is_in_memory_dir(file_path)
        ):
            return PermissionDecision(
                allowed=True, reason="memory directory carve-out", risk=risk
            )

        # 计划文件豁免：计划文件存储在 ~/.illusion/plans/（工作目录之外），
        # 是 plan mode 的核心产物，须允许创建/写入。放在沙箱检查之前，
        # 避免被 allow_write 默认拒绝拦截。退出计划模式（restore_mode）会
        # 清空 _plan_file_path，此后不再豁免。使用规范化路径比较，避免
        # 正/反斜杠与大小写差异导致误判。
        if (
            file_path
            and self._plan_file_path
            and os.path.normcase(os.path.normpath(file_path))
            == os.path.normcase(os.path.normpath(self._plan_file_path))
        ):
            return PermissionDecision(allowed=True, reason="plan file is writable", risk=risk)

        # 命令级显式拒绝：denied_commands（如拒绝 "rm -rf /"）。先于 YOLO，
        # 保证 yolo（含渠道等远程入口）下危险命令仍可被显式规则拦截。
        if command:
            for pattern in getattr(self._settings, "denied_commands", []):
                if isinstance(pattern, str) and fnmatch.fnmatch(command, pattern):
                    return PermissionDecision(
                        allowed=False,
                        reason=f"Command matches deny pattern: {pattern}",
                    )

        # YOLO 模式：绕过沙箱完全运行。置于命令级显式拒绝之后、沙箱检查
        # 之前（跳过沙箱限制），但保留显式拒绝规则（用户显式 deny 优先）：
        # denied_tools / 路径 deny 规则 / denied_commands 先于 YOLO 生效，
        # 确保 yolo（含渠道等远程入口）下危险命令仍可被显式规则拦截。
        if self._settings.mode == PermissionMode.YOLO:
            return PermissionDecision(allowed=True, reason="YOLO mode bypasses sandbox", risk=risk)

        # 检查沙箱文件系统限制（对齐 OS 级沙箱语义）
        # 写入：默认拒绝，仅 allow_write 白名单内可写，deny_write 覆盖
        # 读取：默认允许，仅 deny_read 限制
        if file_path and (
            self._sandbox_allow_write or self._sandbox_deny_write or self._sandbox_deny_read
        ):
            in_session_allowed = file_path in self._session_allowed_paths
            if is_read_only:
                # 读取：默认允许，仅 deny_read 限制
                denied_by_sandbox = any(
                    self._path_matches(p, file_path) for p in self._sandbox_deny_read
                )
            else:
                # 写入：deny_write 优先；否则须在 allow_write 白名单内（默认拒绝）
                denied_by_sandbox = any(
                    self._path_matches(p, file_path) for p in self._sandbox_deny_write
                ) or bool(
                    self._sandbox_allow_write
                    and not any(
                        self._path_matches(p, file_path) for p in self._sandbox_allow_write
                    )
                )
            # 高危操作（删除/还原等）等级高于读取：即使路径已被会话级允许，
            # 仍须重新请求用户确认，防止"已放开访问"被用作删除通行证。
            if denied_by_sandbox and (high_risk or not in_session_allowed):
                return PermissionDecision(
                    allowed=False,
                    reason=f"sandbox_restriction: {file_path}",
                    sandbox_blocked=True,
                    sandbox_denied_path=file_path,
                    high_risk=high_risk,
                    risk=risk,
                )

        # 命令级白名单：用户在 settings.json 的 allowed_shell_commands 配置的命令
        #（bash 与 powershell 通用），命中前缀即放行。高危命令（如 git push --force、
        # rm -rf *）仅当白名单项"完整列出"该高危命令头（即该项本身也是高危模式）时才
        # 可豁免——仅前缀命中（如只配置 git push）不会放行其高危子命令 git push --force。
        # 不变量：置于沙箱检查之后——非 yolo 模式下 allowlist 不越过沙箱边界，
        # 工作区外写入仍触发 sandbox_blocked 确认（回归测试锚定此顺序）；
        # yolo 模式已在上方短路返回，此处仅服务其余模式。
        if command and self._settings.allowed_shell_commands:
            for p in self._settings.allowed_shell_commands:
                if not self._command_matches_allowlist(command, [p]):
                    continue
                # 非高危命令：前缀命中即放行；高危命令：要求白名单项本身即高危模式
                if not high_risk or classify_command_risk(p) == RiskLevel.HIGH:
                    return PermissionDecision(
                        allowed=True,
                        reason=f"Command is in allowed_shell_commands: {p}",
                        risk=risk,
                    )
                # 高危但白名单项仅为普通前缀（如 git push）：不豁免，继续走后续确认流程
                break

        # 完全自动模式：受沙箱限制（上方已检查）且拦高危（内置 HIGH 规则需确认）。
        # 与 YOLO 的区别恒定：auto 永远拦高危，yolo 全部绕过。
        if self._settings.mode == PermissionMode.FULL_AUTO:
            if high_risk:
                return PermissionDecision(
                    allowed=False,
                    requires_confirmation=True,
                    reason="High-risk operations require confirmation in auto mode",
                    high_risk=high_risk,
                    risk=risk,
                )
            return PermissionDecision(allowed=True, reason="Auto mode allows all tools")

        # 只读工具始终允许
        if is_read_only:
            return PermissionDecision(allowed=True, reason="read-only tools are allowed")

        # 计划模式：阻止变更工具（自动阻止，不终止查询循环），但豁免计划文件、退出工具、enter_plan_mode和agent工具
        if self._settings.mode == PermissionMode.PLAN:
            if tool_name == "exit_plan_mode":
                return PermissionDecision(allowed=True, reason="ExitPlanMode is always allowed in plan mode")
            if tool_name == "enter_plan_mode":
                return PermissionDecision(allowed=False, reason="You are already in plan mode", auto_blocked=True)
            if tool_name == "agent":
                return PermissionDecision(allowed=True, reason="Agent tool is allowed in plan mode")
            if file_path and self._plan_file_path and file_path == self._plan_file_path:
                return PermissionDecision(allowed=True, reason="Plan file is writable in plan mode")
            return PermissionDecision(
                allowed=False,
                reason="Plan mode blocks mutating tools until the user exits plan mode",
                auto_blocked=True,
            )

        # 默认模式：按风险分级（LOW 放行 / MEDIUM 确认 / HIGH 必问）。
        #   LOW：只读命令（ls/cat/git status 等）自动放行，不打断常规开发流程。
        #   MEDIUM：普通变更类，确认（可被会话级允许豁免）。
        #   HIGH：高危操作，确认且不可被会话级允许豁免（带 high_risk 标记）。
        if risk == RiskLevel.LOW:
            return PermissionDecision(
                allowed=True,
                reason="Low-risk operation is allowed in default mode",
                risk=risk,
            )
        if risk == RiskLevel.HIGH:
            return PermissionDecision(
                allowed=False,
                requires_confirmation=True,
                reason="High-risk operations require confirmation in default mode",
                high_risk=True,
                risk=risk,
            )
        return PermissionDecision(
            allowed=False,
            requires_confirmation=True,
            reason="Mutating tools require user confirmation in default mode",
            risk=risk,
        )
