"""ReactBackendHost 集成测试。

覆盖 Task 4-6 的写路径、读路径、shutdown 编排：
    - _emit 入队事件，不直接写 stdout
    - _write_loop 串行消费 _write_queue
    - _create_background_task 保留强引用
    - _resolve_pending_futures 在 shutdown 时 resolve 所有 pending future
    - _shutdown 9 步优雅关闭序列（含写队列排空）
    - SIGINT 触发 shutdown 请求入队
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from illusion.ui.backend_host import ReactBackendHost
from illusion.ui.protocol import BackendEvent, FrontendRequest
from illusion.utils.aioqueue import Queue, QueueShutDown


def _make_host(**fields: Any) -> ReactBackendHost:
    """绕过 __init__ 构造 host，仅设置测试所需字段。

    ReactBackendHost.__init__ 需要 BackendHostConfig 和完整运行时依赖，
    集成测试只验证 shutdown / emit / dispatch 等方法行为，因此用
    object.__new__ 绕过构造，再手动注入字段。
    """
    host = object.__new__(ReactBackendHost)
    # 默认字段集合（覆盖 _shutdown / _emit 等访问的全部属性）
    defaults: dict[str, Any] = {
        "_config": None,
        "_bundle": None,
        "_write_queue": Queue(),
        "_write_task": None,
        "_dispatch_tasks": set(),
        "_request_queue": asyncio.Queue(),
        "_permission_requests": {},
        "_question_requests": {},
        "_session_allowed_tools": set(),
        "_busy": False,
        "_running": True,
        "_active_line_task": None,
        "_last_tool_inputs": {},
        "_emitted_tool_started_ids": set(),
        "_brief_assistant_text": None,
        "_read_thread": None,
        "_read_thread_cancel": threading.Event(),
        "_periodic_task": None,
        "_cron_poll_task": None,
        "_sigint_remove": None,
        "_stderr_redirector": None,
        "_modal_lock": asyncio.Lock(),
    }
    defaults.update(fields)
    for key, value in defaults.items():
        setattr(host, key, value)
    return host


# === Task 4 写路径测试（原 skip 测试改为真实集成测试） ===


def test_emit_puts_to_write_queue():
    """_emit 入队事件，不直接写 stdout。"""
    host = _make_host()

    asyncio.run(host._emit(BackendEvent(type="shutdown")))

    assert host._write_queue.qsize() == 1
    event = host._write_queue.get_nowait()
    assert event.type == "shutdown"


def test_write_loop_consumes_queue():
    """_write_loop 串行消费 _write_queue，shutdown 后退出。"""

    async def run() -> None:
        host = _make_host()
        # 入队两个事件 + shutdown 哨兵
        await host._emit(BackendEvent(type="shutdown"))
        await host._emit(BackendEvent(type="error", message="test"))
        # 启动写循环
        host._write_task = asyncio.create_task(host._write_loop())
        # 让写循环消费
        await asyncio.sleep(0.05)
        # shutdown 队列唤醒 _write_loop 退出
        host._write_queue.shutdown()
        await host._write_task
        # 队列应已排空
        assert host._write_queue.qsize() == 0

    asyncio.run(run())


def test_create_background_task_keeps_strong_ref():
    """_create_background_task 保留强引用并在完成后清理。"""

    async def dummy() -> None:
        return None

    async def run() -> None:
        host = _make_host()
        task = host._create_background_task(dummy())
        # 强引用集合应包含此 task
        assert task in host._dispatch_tasks
        # 等待完成
        await task
        # 完成后回调应将其从集合移除
        assert task not in host._dispatch_tasks

    asyncio.run(run())


# === Task 6 shutdown + run 编排测试 ===


def test_resolve_pending_futures_sets_default_results():
    """_resolve_pending_futures 把 pending future 设为默认值（False / ""）。"""
    host = _make_host()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        perm_future: asyncio.Future[bool] = loop.create_future()
        question_future: asyncio.Future[str | dict[Any, Any]] = loop.create_future()
        host._permission_requests["perm-1"] = perm_future
        host._question_requests["q-1"] = question_future

        # 都未完成
        assert not perm_future.done()
        assert not question_future.done()

        host._resolve_pending_futures()

        # permission → False（默认拒绝）
        assert perm_future.done()
        assert perm_future.result() is False
        # question → ""（默认空答）
        assert question_future.done()
        assert question_future.result() == ""
        # 字典已清空
        assert host._permission_requests == {}
        assert host._question_requests == {}

    asyncio.run(run())


def test_resolve_pending_futures_skips_already_done():
    """_resolve_pending_futures 跳过已完成的 future，不重复 set_result。"""
    host = _make_host()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        perm_future: asyncio.Future[bool] = loop.create_future()
        perm_future.set_result(True)  # 已完成（用户已批准）
        host._permission_requests["perm-1"] = perm_future

        host._resolve_pending_futures()

        # 已完成的 future 保持原 result（True），未被覆盖为 False
        assert perm_future.result() is True

    asyncio.run(run())


def test_graceful_shutdown_resolves_pending_futures():
    """shutdown 时 pending permission/question futures 被 resolve 为默认值。"""
    host = _make_host()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        perm_future: asyncio.Future[bool] = loop.create_future()
        question_future: asyncio.Future[str | dict[Any, Any]] = loop.create_future()
        host._permission_requests["perm-1"] = perm_future
        host._question_requests["q-1"] = question_future

        # 调用 _shutdown 应触发 _resolve_pending_futures
        await host._shutdown()

        assert perm_future.done()
        assert perm_future.result() is False
        assert question_future.done()
        assert question_future.result() == ""
        # _running 应被置为 False
        assert host._running is False

    asyncio.run(run())


def test_graceful_shutdown_drains_write_queue():
    """shutdown 时写队列排空后 _write_task 退出。"""

    async def run() -> None:
        host = _make_host()
        # 入队两个事件待排空
        await host._emit(BackendEvent(type="shutdown"))
        await host._emit(BackendEvent(type="error", message="drain-test"))
        # 启动写循环
        host._write_task = asyncio.create_task(host._write_loop())
        # 让写循环消费队列
        await asyncio.sleep(0.05)

        # 调用 _shutdown：会调用 _write_queue.shutdown() 唤醒 _write_loop 退出
        await host._shutdown()

        # _write_task 应已完成
        assert host._write_task is not None
        assert host._write_task.done()
        # _write_queue 已 shutdown（_emit 后续 put 抛 QueueShutDown）
        with pytest.raises(QueueShutDown):
            host._write_queue.put_nowait(BackendEvent(type="error", message="after-shutdown"))
        # _running 应被置为 False
        assert host._running is False

    asyncio.run(run())


def test_sigint_triggers_shutdown():
    """SIGINT 回调（_enqueue_shutdown）入队 shutdown 请求到 _request_queue。

    直接发送 SIGINT 难以在单测中稳定复现，因此测试 SIGINT 处理器调用的
    _enqueue_shutdown 方法 —— 这是 install_sigint_handler 注册的回调，
    验证它能正确入队 shutdown 请求，主循环的 `if req.type == "shutdown": break`
    会响应此请求退出。
    """
    host = _make_host()

    # 调用 _enqueue_shutdown（SIGINT 触发时调用的回调）
    host._enqueue_shutdown()

    # 应入队一个 shutdown 请求
    assert host._request_queue.qsize() == 1
    req = host._request_queue.get_nowait()
    assert req.type == "shutdown"


def test_dispatch_stdin_line_enqueues_shutdown_request():
    """stdin 收到 shutdown JSON 时入队 _request_queue（与 SIGINT 等价的关闭路径）。"""
    host = _make_host()

    # 模拟前端发送 shutdown 请求行
    host._dispatch_stdin_line(json.dumps({"type": "shutdown"}))

    # shutdown 请求应入队 _request_queue
    assert host._request_queue.qsize() == 1
    req = host._request_queue.get_nowait()
    assert req.type == "shutdown"


def test_dispatch_stdin_line_invalid_json_is_ignored():
    """stdin 收到无效 JSON 时记录警告但不入队，主循环不受影响。"""
    host = _make_host()

    # 无效 JSON 不应抛异常，也不应入队
    host._dispatch_stdin_line("not-a-json")

    assert host._request_queue.qsize() == 0


# === Bug 修复回归测试：权限模态框不消失 + Ctrl+X 不能终止任务 ===


@pytest.mark.asyncio
async def test_resolve_permission_emits_modal_close_event():
    """_resolve_permission 应同时发 modal_request modal=None 通知前端关闭模态框。

    Bug 1 回归测试：原版 backend_host 在 permission_response 即时处理路径中漏发
    modal_request modal=None 事件，导致前端 setModal(null) 不触发，权限模态框
    永远停留在 UI 上。
    """
    host = _make_host()
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    host._permission_requests["req-1"] = future

    req = FrontendRequest(type="permission_response", request_id="req-1", allowed=True)
    host._resolve_permission(req)

    # future 应被 resolve
    assert future.done()
    assert future.result() is True
    # 应入队 modal_request modal=None 事件
    assert host._write_queue.qsize() == 1
    event = host._write_queue.get_nowait()
    assert event.type == "modal_request"
    assert event.modal is None


@pytest.mark.asyncio
async def test_resolve_permission_session_allow_adds_to_session_set(tmp_path, monkeypatch):
    """_resolve_permission 收到 session_allow=True 时应加入本会话集合但不持久化。

    对齐 kimi-cli 的 approve_for_session 语义：会话级允许仅存于内存，
    重启后失效，不写入 .illusion/permissions.json。
    """
    monkeypatch.chdir(tmp_path)
    host = _make_host()
    # 模拟 bundle 已初始化，cwd 指向临时目录
    bundle = MagicMock()
    bundle.cwd = str(tmp_path)
    host._bundle = bundle

    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    host._permission_requests["req-always"] = future

    # 发送 session_allow=True 的权限响应
    req = FrontendRequest(
        type="permission_response",
        request_id="req-always",
        allowed=True,
        session_allow=True,
        tool_name="Bash",
    )
    host._resolve_permission(req)

    # future 应被 resolve 为 True
    assert future.done()
    assert future.result() is True
    # 本会话内存集合应包含 Bash
    assert "Bash" in host._session_allowed_tools
    # 不应持久化到 permissions.json（会话级允许不写盘）
    perm_file = tmp_path / ".illusion" / "permissions.json"
    assert not perm_file.exists(), "会话级允许不应写入 permissions.json"


@pytest.mark.asyncio
async def test_resolve_permission_skips_session_allow_when_false(tmp_path, monkeypatch):
    """_resolve_permission 收到 session_allow=False 时不应加入会话集合。"""
    monkeypatch.chdir(tmp_path)
    host = _make_host()
    bundle = MagicMock()
    bundle.cwd = str(tmp_path)
    host._bundle = bundle

    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    host._permission_requests["req-normal"] = future

    req = FrontendRequest(
        type="permission_response",
        request_id="req-normal",
        allowed=True,
        session_allow=False,  # 不勾选会话级允许
        tool_name="Bash",
    )
    host._resolve_permission(req)

    assert future.result() is True
    # 不应写入文件
    perm_file = tmp_path / ".illusion" / "permissions.json"
    assert not perm_file.exists()
    # 本会话集合也不应包含
    assert "Bash" not in host._session_allowed_tools


@pytest.mark.asyncio
async def test_resolve_question_emits_modal_close_event():
    """_resolve_question 应同时发 modal_request modal=None 通知前端关闭模态框。"""
    host = _make_host()
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    host._question_requests["req-2"] = future

    host._resolve_question("req-2", "user answer")

    assert future.done()
    assert future.result() == "user answer"
    assert host._write_queue.qsize() == 1
    event = host._write_queue.get_nowait()
    assert event.type == "modal_request"
    assert event.modal is None


@pytest.mark.asyncio
async def test_dispatch_stdin_line_stop_creates_background_task():
    """stop 请求应即时用 _create_background_task 调度 _stop_active_line，不入队。

    Bug 2 回归测试：原版把 stop 入队 _request_queue，但主循环正阻塞在
    await self._active_line_task，无法从 _request_queue 取 stop 请求，
    导致 Ctrl+X 永远无法终止任务。
    """
    host = _make_host()

    # 模拟有活跃任务
    async def _dummy_active_line() -> bool:
        await asyncio.sleep(100)
        return True

    host._active_line_task = asyncio.create_task(_dummy_active_line())
    host._busy = True

    # 捕获 _stop_active_line 调度
    stop_called = asyncio.Event()

    async def _fake_stop() -> None:
        stop_called.set()
        # 模拟 _stop_active_line 的 cancel 行为
        host._active_line_task.cancel()
        try:
            await host._active_line_task
        except asyncio.CancelledError:
            pass

    host._stop_active_line = _fake_stop  # type: ignore[method-assign]

    try:
        host._dispatch_stdin_line(json.dumps({"type": "stop"}))

        # stop 不应入队 _request_queue
        assert host._request_queue.qsize() == 0
        # _stop_active_line 应被调度执行
        await asyncio.wait_for(stop_called.wait(), timeout=2.0)
        # 任务应被 cancel（让事件循环跑一拍让 cancel 生效）
        await asyncio.sleep(0.01)
        assert host._active_line_task.done()
    finally:
        if not host._active_line_task.done():
            host._active_line_task.cancel()
            try:
                await host._active_line_task
            except asyncio.CancelledError:
                pass


# === 并发权限请求串行化回归测试 ===


@pytest.mark.asyncio
async def test_concurrent_ask_permission_serialized_by_modal_lock():
    """并发 _ask_permission 调用应被 _modal_lock 串行化。

    Bug 回归测试：前端 modal 是单例，并发 modal_request 会互相覆盖，
    导致第一个 future 永不 resolve。修复后所有 modal 请求串行执行。
    """
    host = _make_host()

    # 捕获每次 _ask_permission 发送的 modal_request 事件
    emitted_modals: list[dict[str, Any]] = []

    async def _fake_emit(event: BackendEvent) -> None:
        if event.type == "modal_request" and event.modal is not None:
            emitted_modals.append(event.modal)

    host._emit = _fake_emit  # type: ignore[method-assign]

    # 并发启动两个权限请求
    task_a = asyncio.create_task(host._ask_permission("ToolA", "reason A"))
    task_b = asyncio.create_task(host._ask_permission("ToolB", "reason B"))

    # 让事件循环跑一拍，让 task_a 获取锁并发送 modal_request
    await asyncio.sleep(0.05)

    # 此时只有 task_a 的 modal 被发送（task_b 被锁阻塞）
    assert len(emitted_modals) == 1
    assert emitted_modals[0]["tool_name"] == "ToolA"

    # resolve task_a 的 future
    req_a = emitted_modals[0]["request_id"]
    host._permission_requests[req_a].set_result(True)

    # task_a 应完成
    result_a = await asyncio.wait_for(task_a, timeout=2.0)
    assert result_a is True

    # task_b 现在获取锁，发送自己的 modal_request
    await asyncio.sleep(0.05)
    assert len(emitted_modals) == 2
    assert emitted_modals[1]["tool_name"] == "ToolB"

    # resolve task_b 的 future
    req_b = emitted_modals[1]["request_id"]
    host._permission_requests[req_b].set_result(False)

    # task_b 应完成
    result_b = await asyncio.wait_for(task_b, timeout=2.0)
    assert result_b is False


@pytest.mark.asyncio
async def test_concurrent_ask_question_serialized_by_modal_lock():
    """并发 _ask_question 调用应被 _modal_lock 串行化。"""
    host = _make_host()

    emitted_modals: list[dict[str, Any]] = []

    async def _fake_emit(event: BackendEvent) -> None:
        if event.type == "modal_request" and event.modal is not None:
            emitted_modals.append(event.modal)

    host._emit = _fake_emit  # type: ignore[method-assign]

    task_a = asyncio.create_task(host._ask_question("Question A"))
    task_b = asyncio.create_task(host._ask_question("Question B"))

    await asyncio.sleep(0.05)
    assert len(emitted_modals) == 1
    assert emitted_modals[0]["question"] == "Question A"

    req_a = emitted_modals[0]["request_id"]
    host._question_requests[req_a].set_result("answer A")

    result_a = await asyncio.wait_for(task_a, timeout=2.0)
    assert result_a == "answer A"

    await asyncio.sleep(0.05)
    assert len(emitted_modals) == 2
    assert emitted_modals[1]["question"] == "Question B"

    req_b = emitted_modals[1]["request_id"]
    host._question_requests[req_b].set_result("answer B")

    result_b = await asyncio.wait_for(task_b, timeout=2.0)
    assert result_b == "answer B"


async def test_handle_file_mentions_emits_candidates(tmp_path):
    """_handle_file_mentions：terminal 通道 @ 提及补全，request_id 回显 + 候选载荷。

    与 web 端 handle_web_request_file_mentions 对称：同一共享候选收集，
    仅返回路径与技能名（不读内容）。
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    bundle = MagicMock()
    bundle.cwd = str(tmp_path)
    host = _make_host(_bundle=bundle)

    await host._process_request(FrontendRequest(
        type="web_request_file_mentions", query="ma", request_id="m1",
    ))

    event = host._write_queue.get_nowait()
    assert event.type == "web_file_mentions"
    assert event.request_id == "m1"
    assert event.web_file_mentions is not None
    payload = event.web_file_mentions
    # 规范化 query 原样回显；文件命中子串 "ma"（main.py），无技能环境不崩溃
    assert payload["query"] == "ma"
    paths = [c["path"] for c in payload["candidates"]]
    assert any(p.endswith("main.py") for p in paths)
    assert all(c["kind"] in ("dir", "file") for c in payload["candidates"])
