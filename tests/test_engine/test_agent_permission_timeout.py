"""子代理权限确认超时行为测试。"""
from __future__ import annotations

import asyncio
import time

import pytest

from illusion.engine.query import (
    PermissionDenied,
    wait_for_ask_user_decision,
    wait_for_permission_decision,
)


def _make_future(value: bool | None = None) -> asyncio.Future[bool]:
    """创建测试用 future，value 非 None 时立即 resolve。"""
    fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    if value is not None:
        fut.set_result(value)
    return fut


async def test_main_dialog_unified_timeout(monkeypatch) -> None:
    """回归：285s 超时统一作用于所有会话——主对话不再无限阻塞。

    权限确认无人响应时主对话同样抛带原因的 PermissionDenied（由
    _execute_tool_call 捕获转 error 工具结果），不再产生孤儿 modal。
    """
    monkeypatch.setattr("illusion.swarm.agent_executor.get_agent_context", lambda: None)
    monkeypatch.setattr(
        "illusion.engine.query.AGENT_PERMISSION_TIMEOUT_SECONDS", 2.85
    )
    fut = _make_future(None)  # 永不 resolve
    with pytest.raises(PermissionDenied) as exc_info:
        await wait_for_permission_decision(fut, "bash")
    assert "timed out" in str(exc_info.value)


async def test_main_dialog_resolved_in_time(monkeypatch) -> None:
    """主对话：及时响应仍正常返回（统一超时不影响正常确认）。"""
    monkeypatch.setattr("illusion.swarm.agent_executor.get_agent_context", lambda: None)
    fut = _make_future(None)

    async def _resolve_later() -> None:
        await asyncio.sleep(0.01)
        fut.set_result(True)

    asyncio.create_task(_resolve_later())
    assert await wait_for_permission_decision(fut, "bash") is True


async def test_subagent_received_response(monkeypatch) -> None:
    """子代理上下文：用户及时响应时正常返回。"""
    monkeypatch.setattr(
        "illusion.swarm.agent_executor.get_agent_context", lambda: object()
    )
    fut = _make_future(False)
    assert await wait_for_permission_decision(fut, "bash") is False


async def test_ask_user_question_timeout_returns_placeholder(monkeypatch) -> None:
    """普通问答（ask_user_question）超时：不抛异常，返回占位答案让 agent 自行决策。

    与沙箱权限区别对待：问答是征询偏好而非安全闸门，15 分钟超时后返回
    "(no response...)" + 自行决策提示，任务照常继续。
    """
    monkeypatch.setattr(
        "illusion.swarm.agent_executor.get_agent_context", lambda: object()
    )
    monkeypatch.setattr(
        "illusion.engine.query.ASK_USER_QUESTION_TIMEOUT_SECONDS", 9
    )
    fut = _make_future(None)  # 永不 resolve
    answer = await wait_for_ask_user_decision(fut, "ask_user_question")
    assert isinstance(answer, str)
    assert answer.startswith("(no response within 15 minutes")
    assert "best fits the user's intent" in answer


async def test_sandbox_confirm_timeout_still_denies(monkeypatch) -> None:
    """沙箱权限确认超时：仍抛带原因的 PermissionDenied（与问答区别对待）。"""
    monkeypatch.setattr(
        "illusion.swarm.agent_executor.get_agent_context", lambda: object()
    )
    monkeypatch.setattr(
        "illusion.engine.query.AGENT_PERMISSION_TIMEOUT_SECONDS", 2.85
    )
    fut = _make_future(None)
    with pytest.raises(PermissionDenied) as exc_info:
        await wait_for_ask_user_decision(fut, "sandbox confirmation")
    assert "timed out" in str(exc_info.value)


async def test_subagent_times_out(monkeypatch) -> None:
    """子代理上下文：等待超期无响应抛带原因的 PermissionDenied（fail-closed 拒绝）。"""
    monkeypatch.setattr(
        "illusion.swarm.agent_executor.get_agent_context", lambda: object()
    )
    # 缩短超时到毫秒级，避免真实等待
    monkeypatch.setattr(
        "illusion.engine.query.AGENT_PERMISSION_TIMEOUT_SECONDS", 2.85
    )
    fut = _make_future(None)  # 永不 resolve
    with pytest.raises(PermissionDenied) as exc_info:
        await wait_for_permission_decision(fut, "bash")
    assert "timed out" in str(exc_info.value)
    assert exc_info.value.tool_name == "bash"


async def test_subagent_ask_user_timeout_returns_placeholder(monkeypatch) -> None:
    """子代理上下文：普通问答超时同样返回占位答案（统一超时对子代理生效）。"""
    monkeypatch.setattr(
        "illusion.swarm.agent_executor.get_agent_context", lambda: object()
    )
    monkeypatch.setattr(
        "illusion.engine.query.ASK_USER_QUESTION_TIMEOUT_SECONDS", 9
    )
    future: asyncio.Future[str | dict[str, object]] = (
        asyncio.get_running_loop().create_future()
    )  # 永不 resolve
    answer = await wait_for_ask_user_decision(future, "ask_user_question")
    assert isinstance(answer, str)
    assert answer.startswith("(no response within 15 minutes")


async def test_main_dialog_ask_user_waits(monkeypatch) -> None:
    """主对话：提问及时响应正常返回（统一 9s 问答超时不影响正常交互）。"""
    monkeypatch.setattr("illusion.swarm.agent_executor.get_agent_context", lambda: None)
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def _resolve_later() -> None:
        await asyncio.sleep(0.01)
        future.set_result("选项A")

    asyncio.create_task(_resolve_later())
    assert await wait_for_ask_user_decision(future, "ask_user_question") == "选项A"

async def test_activity_heartbeat_refreshes_during_wait(monkeypatch) -> None:
    """等待期间陪跑心跳：周期性刷新 idle 活动时间戳（子代理不被 300s 墙截断）。

    用短 interval 注入验证心跳确实在等待期间被周期性调用；refresher 为
    None（主对话/Web）时退化为直接等待、不启动心跳。
    """
    from illusion.engine.query import _with_activity_heartbeat

    calls: list[float] = []

    def refresher() -> None:
        calls.append(time.monotonic())

    fut = _make_future(None)

    async def _resolve_later() -> None:
        await asyncio.sleep(0.06)
        fut.set_result(True)

    asyncio.create_task(_resolve_later())
    result = await _with_activity_heartbeat(fut, refresher, interval=0.02)
    assert result is True
    assert len(calls) >= 1, "等待期间心跳应被调用"


async def test_activity_heartbeat_noop_without_refresher(monkeypatch) -> None:
    """refresher 为 None：无心跳协程，直接等待（主对话/Web 行为不变）。"""
    from illusion.engine.query import _with_activity_heartbeat

    fut = _make_future(False)
    assert await _with_activity_heartbeat(fut, None) is False
