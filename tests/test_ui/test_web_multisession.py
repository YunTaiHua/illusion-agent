"""Web 多会话并发测试模块。

覆盖多会话架构的核心保证：
    - 会话行任务并发执行，互不阻塞（fire-and-forget 分发）
    - 同会话 busy 互斥，不同会话可同时提交
    - stop 只作用于目标会话
    - _push_sessions 合并内存会话（含空会话）与磁盘快照
    - 新建会话创建独立引擎，不影响既有会话
    - 事件按 session_id 路由
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from illusion.ui.protocol import BackendEvent, FrontendRequest
from illusion.ui.web.session_runtime import SessionRuntime
from illusion.ui.web.ws_host import WebBackendHost, _WorkspaceState, _cwd_key
from illusion.ui.web.ws_web_api import WebApiDispatcher
from illusion.utils.aioqueue import Queue


def _make_host(**fields: Any) -> WebBackendHost:
    """绕过 __init__ 构造 host，仅设置测试所需字段（与既有测试同模式）。"""
    host = object.__new__(WebBackendHost)
    bundle = MagicMock()
    bundle.cwd = "/fake/cwd"
    bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
    bundle.current_settings.return_value = MagicMock(ui_language="zh-CN")
    bundle.session_id = "init-sid"
    defaults: dict[str, Any] = {
        "_config": None,
        "_websocket": MagicMock(),
        "_bundle": bundle,
        "_sessions": {},
        "_active_session_id": None,
        # 多工作区状态：默认工作区挂接 mock bundle（与 run() 初始化一致）
        "_workspaces": {_cwd_key(bundle.cwd): _WorkspaceState(cwd=bundle.cwd, bundle=bundle)},
        "_default_workspace_cwd": bundle.cwd,
        "_write_queue": Queue(),
        "_write_task": None,
        "_dispatch_tasks": set(),
        "_request_queue": asyncio.Queue(),
        "_permission_requests": {},
        "_question_requests": {},
        "_modal_locks": {},
        "_session_allowed_tools": set(),
        "_running": True,
        "_ws_closed": False,
        "_periodic_task": None,
    }
    defaults.update(fields)
    for key, value in defaults.items():
        setattr(host, key, value)
    # 多工作区：默认目录解析改为返回 mock bundle 的 cwd。
    # （object.__new__ 构造无真实配置/注册表；真实 WebBackendHost 中
    #   _resolve_workspace_cwd 动态读取 settings.working_directory）
    if "_resolve_workspace_cwd" not in fields:
        host._resolve_workspace_cwd = lambda cwd=None: bundle.cwd  # type: ignore[method-assign]
    return host


def _make_session(host: WebBackendHost, sid: str) -> SessionRuntime:
    """构造会话运行时并注册到 host。"""
    session = SessionRuntime(
        session_id=sid,
        bundle=MagicMock(),
    )
    session.bundle.app_state = host._bundle.app_state
    session.bundle.cwd = host._bundle.cwd
    session.engine.messages = []
    session.engine.current_context_tokens.return_value = 0
    session.engine._bg_agent_tracker.has_completions.return_value = False
    host._sessions[sid] = session
    return session


class TestConcurrentDispatch:
    """行任务并发分发测试"""

    @pytest.mark.asyncio
    async def test_submit_line_spawns_independent_task_per_session(self, monkeypatch):
        """两个会话同时 submit_line：各自创建独立行任务，互不阻塞。"""
        host = _make_host()
        session_a = _make_session(host, "a")
        session_b = _make_session(host, "b")
        host._active_session_id = "a"

        processed: list[str] = []

        async def fake_process_line(session, line, **kwargs):
            processed.append(session.session_id)
            # 模拟长时间任务：让并发窗口真实存在
            await asyncio.sleep(0.05)
            return True

        monkeypatch.setattr(host, "_process_line", fake_process_line)
        monkeypatch.setattr(host, "_finish_session_line", AsyncMock())
        monkeypatch.setattr(host, "_push_sessions", AsyncMock())
        monkeypatch.setattr(host, "_update_phase", AsyncMock())
        monkeypatch.setattr(host, "_status_snapshot", MagicMock(return_value=MagicMock()))

        # 会话 A 提交（fire-and-forget，主循环不等待）
        await host._dispatch_request(
            FrontendRequest(type="submit_line", line="task A", session_id="a")
        )
        assert session_a.busy is True
        assert session_a.active_line_task is not None

        # 会话 B 在 A 运行期间提交——不应被 A 阻塞
        await host._dispatch_request(
            FrontendRequest(type="submit_line", line="task B", session_id="b")
        )
        assert session_b.busy is True
        assert session_b.active_line_task is not None

        # 让事件循环调度两个行任务，确认并发执行（A 尚未完成时 B 已开始）
        await asyncio.sleep(0)
        assert set(processed) == {"a", "b"}

        # 等待任务完成（行任务结束后 active_line_task 会被清空，先保存引用）
        task_a = session_a.active_line_task
        task_b = session_b.active_line_task
        await asyncio.gather(task_a, task_b)
        assert processed == ["a", "b"]

    @pytest.mark.asyncio
    async def test_busy_gate_is_per_session(self, monkeypatch):
        """同会话重复提交被拒绝；不同会话可同时提交。"""
        host = _make_host()
        session_a = _make_session(host, "a")
        _make_session(host, "b")
        host._active_session_id = "a"
        emitted: list[BackendEvent] = []

        async def fake_emit(event: BackendEvent, **kwargs: Any) -> None:
            # 模拟真实 _emit 的会话标记逻辑
            sid = kwargs.get("session_id")
            if sid:
                event.session_id = sid
            emitted.append(event)

        host._emit = fake_emit  # type: ignore[assignment]
        monkeypatch.setattr(host, "_process_line", AsyncMock(return_value=True))
        monkeypatch.setattr(host, "_push_sessions", AsyncMock())
        monkeypatch.setattr(host, "_finish_session_line", AsyncMock())

        session_a.busy = True  # 模拟 A 正在运行

        # 会话 A 再次提交 → 拒绝
        await host._dispatch_request(
            FrontendRequest(type="submit_line", line="again", session_id="a")
        )
        errors = [e for e in emitted if e.type == "error"]
        assert errors
        assert "busy" in errors[0].message.lower()
        assert errors[0].session_id == "a"

        # 会话 B 提交 → 正常接受
        await host._dispatch_request(
            FrontendRequest(type="submit_line", line="from B", session_id="b")
        )
        assert host._sessions["b"].busy is True

    @pytest.mark.asyncio
    async def test_stop_targets_only_requested_session(self, monkeypatch):
        """stop 只取消目标会话的行任务，其他会话任务不受影响。"""
        host = _make_host()
        _make_session(host, "a")
        session_b = _make_session(host, "b")
        host._active_session_id = "a"
        emitted: list[BackendEvent] = []

        async def fake_emit(event: BackendEvent, **kwargs: Any) -> None:
            emitted.append(event)

        host._emit = fake_emit  # type: ignore[assignment]
        monkeypatch.setattr(host, "_push_sessions", AsyncMock())

        # 会话 B 有一个运行中的行任务（长任务）
        async def long_task():
            await asyncio.sleep(10)

        task_b = asyncio.create_task(long_task())
        session_b.active_line_task = task_b
        session_b.busy = True

        # 会话 A 没有任务 → stop(A) 提示无活动任务
        await host._stop_active_line("a")
        assert not task_b.done(), "stop(A) 不应影响会话 B 的任务"
        # 会话 B 的任务被取消
        await host._stop_active_line("b")
        assert task_b.cancelled() or task_b.done()


class TestSessionRegistry:
    """会话运行时注册表测试"""

    @pytest.mark.asyncio
    async def test_create_session_builds_independent_engine(self, monkeypatch):
        """新建会话生成新 ID 与独立引擎，不触碰共享 bundle。"""
        host = _make_host()
        fake_engine = MagicMock()
        fake_engine._tool_metadata = {}
        fake_engine._bg_agent_tracker = MagicMock()
        fake_engine.current_context_tokens.return_value = 0
        fake_engine.messages = []
        monkeypatch.setattr(
            "illusion.ui.web.ws_host.build_session_engine",
            lambda *a, **k: fake_engine,
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_host.build_session_bundle",
            lambda bundle, sid, engine: MagicMock(engine=engine, session_id=sid),
        )

        session = await host._create_session()

        assert session.session_id
        assert session.session_id in host._sessions
        assert session.engine is fake_engine
        # 初始 bundle 的引擎未被替换
        assert host._bundle.engine is not fake_engine

    @pytest.mark.asyncio
    async def test_create_session_clears_stale_session_name(self, monkeypatch):
        """新建会话清除工作区共享 app_state 的 session_name 残留。

        回归：重命名会话后删除，残留名称会污染新会话——新会话首条消息
        落盘时 _save_session_snapshot 会把它写入新会话的 meta.title，
        导致新会话继承上一次的会话名称。新建会话必须重置该共享字段。
        """
        host = _make_host()
        # 模拟上一会话重命名残留：共享 app_state 已写入 session_name
        host._bundle.app_state.get.return_value = MagicMock(
            session_name="旧会话名称", ui_language="zh-CN"
        )
        fake_engine = MagicMock()
        fake_engine._tool_metadata = {}
        fake_engine._bg_agent_tracker = MagicMock()
        fake_engine.current_context_tokens.return_value = 0
        fake_engine.messages = []
        monkeypatch.setattr(
            "illusion.ui.web.ws_host.build_session_engine",
            lambda *a, **k: fake_engine,
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_host.build_session_bundle",
            lambda bundle, sid, engine: MagicMock(
                engine=engine, session_id=sid, app_state=bundle.app_state, cwd=bundle.cwd
            ),
        )

        await host._create_session()

        # 共享 app_state 的 session_name 残留已被清除，不再污染新会话
        host._bundle.app_state.set.assert_called_once_with(session_name="")

    @pytest.mark.asyncio
    async def test_set_active_session_clears_stale_session_name(self, monkeypatch):
        """切换活跃会话清除共享 app_state 的 session_name 残留。

        回归：重命名当前会话后切到"已创建但未输入首条消息"的空会话，
        残留名称会污染该空会话（首条消息落盘时写入 meta.title）。
        切换活跃会话必须重置该共享字段。
        """
        host = _make_host()
        session = _make_session(host, "a")
        # 模拟上一会话重命名残留
        host._bundle.app_state.get.return_value = MagicMock(
            session_name="旧会话名称", ui_language="zh-CN"
        )

        host._set_active_session("a")

        # 切换时同步 session_id 并清除 session_name 残留
        session.bundle.app_state.set.assert_called_once_with(session_id="a", session_name="")

    @pytest.mark.asyncio
    async def test_dispose_session_removes_runtime(self, monkeypatch):
        """释放会话运行时：取消行任务并关闭引擎。"""
        host = _make_host()
        session = _make_session(host, "a")
        host._active_session_id = "a"
        closed: list[str] = []
        session.engine.aclose = AsyncMock(side_effect=lambda: closed.append("a"))  # type: ignore[attr-defined]

        await host._dispose_session("a")

        assert "a" not in host._sessions
        assert closed == ["a"]
        assert host._active_session_id is None


class TestEvictionAndRestoreSafety:
    """淘汰与恢复失败的安全保障测试"""

    @pytest.mark.asyncio
    async def test_dispose_session_stops_owned_tasks(self, monkeypatch):
        """释放会话运行时必须停止其归属的后台任务（防完成通知污染其他会话）。"""
        host = _make_host()
        session = _make_session(host, "a")
        stopped: list[list[str]] = []

        async def fake_stop_all(bundle, *, session_ids=None):
            stopped.append(session_ids or [])

        monkeypatch.setattr(
            "illusion.ui.runtime.stop_all_tasks", fake_stop_all
        )
        session.engine.aclose = AsyncMock()  # type: ignore[attr-defined]

        await host._dispose_session("a")

        assert stopped == [["a"]], "dispose 必须停止该会话归属的后台任务"

    @pytest.mark.asyncio
    async def test_restore_failure_closes_engine(self, monkeypatch):
        """恢复失败时必须关闭已创建的引擎（防运行时泄漏）。"""
        host = _make_host()
        host._emit = AsyncMock()
        host._push_sessions = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._session_state_payload = MagicMock(return_value={})
        host._ws_closed = False
        host._set_active_session = MagicMock()
        host._refresh_session_display = MagicMock()
        host._maybe_evict_sessions = MagicMock()

        fake_engine = MagicMock()
        fake_engine._tool_metadata = {}
        fake_engine._bg_agent_tracker = MagicMock()
        fake_engine.aclose = AsyncMock()  # type: ignore[attr-defined]
        # 多工作区：磁盘 meta 存在 → 定位到默认工作区后再走恢复流程
        monkeypatch.setattr(
            "illusion.services.session_storage.read_meta",
            lambda cwd, sid: {"session_id": sid, "cwd": cwd} if sid == "s1" else None,
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api.build_session_engine",
            lambda *a, **k: fake_engine,
        )
        monkeypatch.setattr(
            "illusion.ui.runtime.build_session_bundle",
            lambda bundle, sid, engine: MagicMock(engine=engine, session_id=sid),
        )

        async def failing_resume(args, context):
            raise RuntimeError("磁盘损坏")

        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._resume_handler", failing_resume
        )
        dispatcher = WebApiDispatcher(host)

        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_restore_session", session_id="s1")
        await dispatcher.handle(req)

        # 失败路径：运行时已移除、引擎已关闭、completed 携带错误
        assert "s1" not in host._sessions
        fake_engine.aclose.assert_awaited_once()
        calls = host._emit.call_args_list
        completed = next(c.args[0] for c in calls if c.args[0].type == "web_restore_completed")
        assert completed.web_error is not None

    def test_orphan_task_completion_returns_none(self):
        """无归属任务（其他连接的 host 创建）路由返回 None，完成通知被丢弃。"""
        host = _make_host()
        _make_session(host, "a")
        orphan = MagicMock()
        orphan.metadata = {}
        assert host._route_task_completion(orphan) is None


class TestTaskCompletionRouting:
    """后台任务完成通知按归属会话路由测试"""

    def test_route_task_completion_by_owner(self):
        """任务带 owner_session_id → 路由到对应会话；无归属 → None。"""
        host = _make_host()
        session_a = _make_session(host, "a")
        _make_session(host, "b")
        assert session_a is not None

        owned = MagicMock()
        owned.metadata = {"owner_session_id": "a"}
        assert host._route_task_completion(owned) is session_a

        # 无归属任务（启动期/terminal 遗留）→ None（调用方回退初始引擎）
        orphan = MagicMock()
        orphan.metadata = {}
        assert host._route_task_completion(orphan) is None

        # 归属已淘汰会话的任务 → None（避免投递到错误 tracker）
        stale = MagicMock()
        stale.metadata = {"owner_session_id": "gone"}
        assert host._route_task_completion(stale) is None

    def test_owner_stamp_on_task_creation(self):
        """任务创建时 stamp 归属会话（contextvar 传播）。"""
        import asyncio

        from illusion.tasks.manager import get_task_manager, session_owner_ctx

        manager = get_task_manager()
        task_id = None

        async def run():
            nonlocal task_id
            token = session_owner_ctx.set("session-x")
            try:
                record = manager.create_pending_task(
                    subject="s", description="d"
                )
                task_id = record.id
                assert record.metadata.get("owner_session_id") == "session-x"
            finally:
                session_owner_ctx.reset(token)

        asyncio.run(run())
        assert task_id is not None

    def test_powershell_background_stamps_owner(self):
        """powershell 后台任务创建路径包含归属 stamp（与 bash 一致）。"""
        import inspect

        from illusion.tools import powershell_tool

        src = inspect.getsource(powershell_tool)
        assert "current_task_session_owner" in src, (
            "powershell 后台任务必须 stamp owner_session_id（否则完成通知路由错误）"
        )
        assert "owner_session_id" in src


class TestPushSessions:
    """会话列表推送测试"""

    @pytest.mark.asyncio
    async def test_push_sessions_merges_memory_and_disk(self, monkeypatch):
        """内存会话（含空会话）与磁盘快照合并；busy/active/in_memory 标记正确。"""
        host = _make_host()
        emitted: list[BackendEvent] = []

        async def fake_emit(event: BackendEvent, **kwargs: Any) -> None:
            emitted.append(event)

        host._emit = fake_emit  # type: ignore[assignment]
        host._active_session_id = "mem-1"

        # 内存会话：有内容的运行中会话 + 无内容的空会话
        running = _make_session(host, "mem-1")
        running.busy = True
        running.phase = "tool_executing"
        running.message_count = 3
        running.summary = "运行中的会话"
        running.engine.messages = []
        fresh = _make_session(host, "mem-2")
        fresh.engine.messages = []

        # 磁盘快照：一个未 materialized 的旧会话
        monkeypatch.setattr(
            "illusion.services.session_storage.list_session_snapshots",
            lambda cwd, limit=50: [
                {"session_id": "disk-1", "created_at": 1700000000,
                 "message_count": 5, "turn_count": 2, "summary": "旧会话"},
            ],
        )
        monkeypatch.setattr(
            "illusion.services.session_storage.read_meta", lambda cwd, sid: None
        )

        await host._push_sessions()

        sessions_evt = next(e for e in emitted if e.type == "web_sessions")
        items = {i["id"]: i for i in sessions_evt.web_sessions}
        # 有内容的内存会话出现在列表
        assert "mem-1" in items
        # 无内容的纯内存空会话不显示（列表只放有内容的会话，避免"新会话"占位）
        assert "mem-2" not in items
        # 运行中会话携带 busy/phase/active 标记
        assert items["mem-1"]["busy"] is True
        assert items["mem-1"]["phase"] == "tool_executing"
        assert items["mem-1"]["active"] is True
        assert items["mem-1"]["in_memory"] is True
        # 磁盘会话标记未 materialized
        assert items["disk-1"]["in_memory"] is False
        assert items["disk-1"]["active"] is False
        assert sessions_evt.active_session_id == "mem-1"

    @pytest.mark.asyncio
    async def test_event_routing_keeps_session_id(self):
        """会话级事件携带 session_id（前端按此路由）。"""
        host = _make_host()
        session = _make_session(host, "s1")
        emitted: list[BackendEvent] = []

        async def fake_emit(event: BackendEvent, **kwargs: Any) -> None:
            # 模拟真实 _emit 的会话标记逻辑
            sid = kwargs.get("session_id")
            if sid:
                event.session_id = sid
            emitted.append(event)

        host._emit = fake_emit  # type: ignore[assignment]

        await host._emit(
            BackendEvent(type="line_complete"), session_id=session.session_id
        )
        assert emitted[-1].session_id == "s1"


class TestWebApiSessionScoping:
    """web_* handler 会话作用域测试"""

    @pytest.mark.asyncio
    async def test_restore_does_not_touch_other_sessions(self, monkeypatch):
        """恢复会话只影响目标会话，其他会话的运行时保持不变。"""
        host = _make_host()
        host._push_sessions = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._session_state_payload = MagicMock(return_value={})
        host._ws_closed = False
        host._set_active_session = MagicMock()
        host._refresh_session_display = MagicMock()
        other = _make_session(host, "other")
        # 预置目标会话（内存路径）
        target = _make_session(host, "target")
        target.engine.messages = []
        dispatcher = WebApiDispatcher(host)

        req = FrontendRequest(type="web_restore_session", session_id="target")
        await dispatcher.handle(req)

        # 其他会话运行时原样保留
        assert host._sessions["other"] is other
        assert host._sessions["target"] is target
        # 恢复切换活跃会话
        host._set_active_session.assert_called_once_with("target")

    @pytest.mark.asyncio
    async def test_restore_creates_runtime_on_demand(self, monkeypatch):
        """目标会话无内存运行时（页面刷新后）：创建独立引擎并从磁盘恢复。"""
        host = _make_host()
        host._push_sessions = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._session_state_payload = MagicMock(return_value={"session_id": "s1"})
        host._ws_closed = False
        host._set_active_session = MagicMock()
        host._refresh_session_display = MagicMock()
        host._maybe_evict_sessions = MagicMock()

        fake_engine = MagicMock()
        fake_engine._tool_metadata = {}
        fake_engine._bg_agent_tracker = MagicMock()
        # 多工作区：磁盘 meta 存在 → 定位到默认工作区后再按需物化
        monkeypatch.setattr(
            "illusion.services.session_storage.read_meta",
            lambda cwd, sid: {"session_id": sid, "cwd": cwd} if sid == "s1" else None,
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api.build_session_engine",
            lambda *a, **k: fake_engine,
        )
        monkeypatch.setattr(
            "illusion.ui.runtime.build_session_bundle",
            lambda bundle, sid, engine: MagicMock(engine=engine, session_id=sid),
        )

        async def fake_resume_handler(args, context):
            result = MagicMock()
            result.restored_session_id = "s1"
            result.replay_messages = []
            return result

        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._resume_handler", fake_resume_handler
        )
        dispatcher = WebApiDispatcher(host)

        req = FrontendRequest(type="web_restore_session", session_id="s1")
        await dispatcher.handle(req)

        assert "s1" in host._sessions
        assert host._sessions["s1"].engine is fake_engine
        host._set_active_session.assert_called_once_with("s1")
