"""print 模式沙箱两选项（允许/拒绝）多轮退出机制测试。

沙箱权限确认在 print 模式下使用独立的 sandbox_permission_prompt
（make_print_mode_sandbox_permission），仅提供"允许/拒绝"两选项，
并通过独立的 pending-sandbox 文件实现跨轮次退出。与通用权限
（print 模式 Y/N，pending-permission 文件）严格区分。
"""
from __future__ import annotations

import json

import pytest

from illusion.utils.atomic_write import atomic_write_text


@pytest.mark.asyncio
async def test_print_mode_sandbox_permission_two_option_allow(tmp_path):
    """print 模式沙箱权限：首次调用持久化并返回 False，批准后放行并删除。"""
    from illusion.services.session_storage import (
        _pending_sandbox_path,
        load_pending_sandbox,
    )
    from illusion.ui.terminal_io import make_print_mode_sandbox_permission

    cwd = str(tmp_path)
    session_id = "testsid"
    state: dict = {}

    prompt = make_print_mode_sandbox_permission(cwd=cwd, session_id=session_id, state=state)

    # 首次调用：沙箱限制触发权限请求 → 持久化 + 设置 state flag + 返回 False
    allowed = await prompt("bash", "Sandbox restriction: /etc/x - sandbox_restriction: /etc/x")
    assert allowed is False
    assert state["pending_sandbox_raised"] is True
    assert state["pending_sandbox_tool"] == "bash"
    pending = load_pending_sandbox(cwd, session_id)
    assert pending is not None
    assert pending["tool_name"] == "bash"
    assert pending.get("approved") is False

    # 模拟 app.py 恢复：用户输入 "Y"（允许）→ 更新 pending-sandbox 文件 approved=true
    payload = {
        "session_id": session_id,
        "tool_name": "bash",
        "reason": "Sandbox restriction: /etc/x",
        "approved": True,
        "created_at": 0,
    }
    atomic_write_text(
        _pending_sandbox_path(cwd, session_id),
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )

    # 再次调用：pending approved=true → 放行并删除 pending（一次性）
    allowed = await prompt("bash", "Sandbox restriction: /etc/x - sandbox_restriction: /etc/x")
    assert allowed is True
    assert _pending_sandbox_path(cwd, session_id).exists() is False


@pytest.mark.asyncio
async def test_print_mode_sandbox_permission_deny(tmp_path):
    """print 模式沙箱权限：用户拒绝（N）后删除 pending，继续返回 False。"""
    from illusion.services.session_storage import (
        _pending_sandbox_path,
        delete_pending_sandbox,
        load_pending_sandbox,
    )
    from illusion.ui.terminal_io import make_print_mode_sandbox_permission

    cwd = str(tmp_path)
    session_id = "testsid2"
    state: dict = {}

    prompt = make_print_mode_sandbox_permission(cwd=cwd, session_id=session_id, state=state)

    # 首次调用持久化
    allowed = await prompt("bash", "Sandbox restriction: /etc/x")
    assert allowed is False
    assert load_pending_sandbox(cwd, session_id) is not None

    # 模拟 app.py 恢复：用户拒绝（N）→ 删除 pending
    delete_pending_sandbox(cwd, session_id)
    assert _pending_sandbox_path(cwd, session_id).exists() is False

    # 再次调用：无 pending → 再次持久化并返回 False（拒绝）
    allowed = await prompt("bash", "Sandbox restriction: /etc/x")
    assert allowed is False
    assert load_pending_sandbox(cwd, session_id) is not None


@pytest.mark.asyncio
async def test_print_mode_sandbox_independent_from_general_permission(tmp_path):
    """print 模式沙箱权限与通用权限使用独立的 pending 文件，互不干扰。"""
    from illusion.services.session_storage import (
        _pending_permission_path,
        _pending_sandbox_path,
    )
    from illusion.ui.terminal_io import (
        make_print_mode_permission,
        make_print_mode_sandbox_permission,
    )

    cwd = str(tmp_path)
    session_id = "testsid3"
    state: dict = {}

    sandbox_prompt = make_print_mode_sandbox_permission(cwd=cwd, session_id=session_id, state=state)
    general_prompt = make_print_mode_permission(cwd=cwd, session_id=session_id, state=state)

    # 触发沙箱权限请求
    await sandbox_prompt("bash", "Sandbox restriction: /etc/x")
    assert _pending_sandbox_path(cwd, session_id).exists() is True
    # 通用权限文件不应被创建
    assert _pending_permission_path(cwd, session_id).exists() is False

    # 分别触发通用权限请求
    await general_prompt("bash", "normal permission")
    assert _pending_permission_path(cwd, session_id).exists() is True


@pytest.mark.asyncio
async def test_print_mode_sandbox_permission_uses_sandbox_callback(tmp_path):
    """print 模式下沙箱权限应走 sandbox_permission_prompt（两选项），而非
    permission_prompt（三选项）或 ask_user_prompt（三选项）。

    通过 query.py 的源码断言：sandbox_blocked 分支在 print_mode 时调用
    context.sandbox_permission_prompt，交互模式才调用 ask_user_prompt 三选项。
    """
    import inspect

    from illusion.engine import query as query_module

    source = inspect.getsource(query_module)
    # print 模式沙箱权限分支复用 sandbox_permission_prompt（两选项），
    # 但受 LLM 自动审核 guard 包裹（审核不介入时才走人工三分支）
    assert "context.print_mode and context.sandbox_permission_prompt is not None" in source
    # 判官 DENY 不再直接终止：降级人工确认，判官意见附进确认文案
    # （锚定降级语义：_review_handled 仅 ALLOW 为真 + 文案拼接）
    assert "_review_handled = _review_result is not None and bool(_review_result[0])" in source
    assert '" (LLM review denied: {_review_deny})"' in source
    # 交互模式三选项仍保留（同样是审核 guard 包裹后）
    assert "context.ask_user_prompt is not None" in source
    # 高危操作在交互模式下也仅提供两选项（去掉"当前会话允许"）
    assert 'if decision.high_risk:' in source
    assert "Allow for session" in source
    assert "当前会话允许" in source
    # 高危操作即便选中会话级允许也不放行（解析层保护）
    assert "(not decision.high_risk) and" in source