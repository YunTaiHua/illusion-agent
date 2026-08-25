"""YOLO 模式下显式拒绝规则的回归测试。

YOLO 应"绕过沙箱"但"保留显式 deny"：denied_tools / 路径 deny 规则 /
denied_commands 必须先于 YOLO 分支生效（渠道已整体切 yolo，危险命令仍须
可被显式规则拦截）。
"""
from __future__ import annotations

from illusion.config.settings import (
    PermissionMode,
    PermissionSettings,
    SandboxFilesystemSettings,
    SandboxSettings,
)
from illusion.permissions import PermissionChecker


def _yolo_checker(denied_commands: list[str] | None = None) -> PermissionChecker:
    settings = PermissionSettings(
        mode=PermissionMode.YOLO,
        denied_commands=denied_commands or [],
    )
    return PermissionChecker(settings)


def test_yolo_allows_sandbox_path(tmp_path) -> None:
    """YOLO 绕过沙箱限制：工作区外写入直接放行（不触发 sandbox_blocked）。"""
    settings = PermissionSettings(mode=PermissionMode.YOLO)
    checker = PermissionChecker(settings)
    checker.sync_sandbox_restrictions(
        SandboxSettings(
            filesystem=SandboxFilesystemSettings(allow_write=["."])
        ),
        working_directory=str(tmp_path),
    )
    outside = str(tmp_path.parent / "outside" / "x.txt")
    decision = checker.evaluate(
        "write_file", is_read_only=False, file_path=outside
    )
    assert decision.allowed is True
    assert decision.sandbox_blocked is False


def test_yolo_denied_commands_still_blocked() -> None:
    """YOLO 下 denied_commands 仍生效（危险命令可被显式规则拦截）。"""
    checker = _yolo_checker(denied_commands=["rm -rf *"])
    blocked = checker.evaluate(
        "bash", is_read_only=False, command="rm -rf /tmp/data"
    )
    assert blocked.allowed is False
    assert "deny" in blocked.reason
    allowed = checker.evaluate(
        "bash", is_read_only=False, command="ls /tmp"
    )
    assert allowed.allowed is True


def test_yolo_denied_tools_still_blocked() -> None:
    """YOLO 下 denied_tools 仍生效。"""
    settings = PermissionSettings(
        mode=PermissionMode.YOLO, denied_tools=["web_fetch"]
    )
    checker = PermissionChecker(settings)
    decision = checker.evaluate("web_fetch", is_read_only=True)
    assert decision.allowed is False

def test_allowlist_does_not_bypass_sandbox_in_non_yolo(tmp_path) -> None:
    """回归锚点：非 yolo 模式下 allowlist 不越过沙箱边界。

    allowed_shell_commands 命中的命令若写工作区外，必须先触发
    sandbox_blocked 确认（沙箱检查先于 allowlist）；否则配置了白名单的
    用户会静默失去工作区外写入确认。此顺序曾被意外回退，用本测试锚定。
    """
    settings = PermissionSettings(
        mode=PermissionMode.DEFAULT,
        allowed_shell_commands=["echo *"],
    )
    checker = PermissionChecker(settings)
    checker.sync_sandbox_restrictions(
        SandboxSettings(
            filesystem=SandboxFilesystemSettings(allow_write=["."])
        ),
        working_directory=str(tmp_path),
    )
    outside = str(tmp_path.parent / "outside" / "x.txt")
    decision = checker.evaluate(
        "bash",
        is_read_only=False,
        command=f"echo poc > {outside}",
        file_path=outside,
    )
    assert decision.sandbox_blocked is True, (
        "非 yolo 下 allowlist 命中不应越过 sandbox_blocked 确认"
    )

    # 对照组：工作区内同命令不被沙箱拦截，正常进入 DEFAULT 确认流程
    # （allowed=False + requires_confirmation=True，区别于 sandbox_blocked）
    inside = str(tmp_path / "inside.txt")
    ok = checker.evaluate(
        "bash", is_read_only=False, command=f"echo hi > {inside}", file_path=inside
    )
    assert ok.sandbox_blocked is False
    assert ok.requires_confirmation is True
