"""WebApiDispatcher 单元测试模块

验证 Web 专属请求分发器能正确路由 web_* 请求。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from illusion.ui.web.ws_web_api import WebApiDispatcher


class TestWebApiDispatcherRouting:
    """WebApiDispatcher 请求路由测试"""

    @pytest.fixture
    def dispatcher(self, tmp_path):
        """创建带 mock emit 的分发器"""
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._push_sessions = AsyncMock()
        dispatcher = WebApiDispatcher(host)
        return dispatcher

    def test_dispatcher_constructable(self, dispatcher):
        """测试分发器可构造并持有 host 引用"""
        assert dispatcher._host is not None

    def test_dispatch_table_covers_all_web_types(self, dispatcher):
        """测试 dispatch 表覆盖全部 web_* 请求类型（防御：新增类型必须注册 handler）"""
        from illusion.ui.protocol import FrontendRequest

        table = dispatcher._dispatch_table()
        web_types = [
            t
            for t in FrontendRequest.model_fields["type"].annotation.__args__
            if t.startswith("web_")
        ]
        for wt in web_types:
            assert wt in table, f"web 请求类型 {wt} 未注册 handler"

    @pytest.mark.asyncio
    async def test_handle_isolates_handler_exception(self, monkeypatch, tmp_path):
        """测试 handle 捕获处理异常并发 error 事件，不向主循环冒泡（回归：异常拖垮 host）"""
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        dispatcher = WebApiDispatcher(host)

        # 让 handle_web_restore_session 抛异常
        async def boom(request):
            raise RuntimeError("模拟 resume_handler 内部失败")
        monkeypatch.setattr(dispatcher, "handle_web_restore_session", boom)

        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_restore_session", session_id="s1")
        # 不应抛异常
        await dispatcher.handle(req)
        calls = host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "error" in types


class TestWebHostDispatch:
    """ws_host 主循环分发 web_* 请求到 WebApiDispatcher 测试"""

    def test_host_holds_web_api_dispatcher(self):
        """测试 WebBackendHost 实例持有 _web_api 属性"""
        from illusion.ui.web.ws_host import WebBackendHost, WebHostConfig
        host = WebBackendHost(WebHostConfig(model="test-model"), MagicMock())
        assert hasattr(host, "_web_api")
        assert host._web_api is not None


class TestWebRequestSessions:
    """web_request_sessions 会话列表拉取测试"""

    @pytest.fixture
    def dispatcher_with_bundle(self, monkeypatch, tmp_path):
        """创建带 mock bundle 和 session_storage 的分发器"""
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        host._push_sessions = AsyncMock()
        dispatcher = WebApiDispatcher(host)

        # mock session_storage.list_session_snapshots
        fake_sessions = [
            {"session_id": "s1", "created_at": 1700000000, "message_count": 5, "summary": "测试会话1"},
            {"session_id": "s2", "created_at": 1700000100, "message_count": 3, "summary": "测试会话2"},
        ]
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._list_session_snapshots",
            lambda cwd, limit=20: fake_sessions,
        )
        return dispatcher

    @pytest.mark.asyncio
    async def test_request_sessions_delegates_to_host_push(self, dispatcher_with_bundle):
        """测试拉取会话列表委托给 host._push_sessions（会话列表合并逻辑在 host）"""
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_request_sessions")
        await dispatcher_with_bundle.handle(req)
        dispatcher_with_bundle._host._push_sessions.assert_awaited_once()


class TestWebRestoreSession:
    """web_restore_session 零 suppress 恢复流程测试"""

    @pytest.fixture
    def dispatcher_restore(self, monkeypatch, tmp_path):
        """创建带 mock 恢复流程的分发器。

        预置内存会话（s1），验证"运行时已存在 → 直接从引擎重建转录"路径。
        """
        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._ws_closed = False  # 模拟 WebSocket 处于连接状态
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.session_id = "old-sid"
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        host._push_sessions = AsyncMock()
        host._session_state_payload = MagicMock(return_value={"session_id": "restored-sid"})
        host._refresh_session_display = MagicMock()
        host._set_active_session = MagicMock()
        # 预置内存会话：恢复走"已存在"分支，不触碰磁盘
        memory_session = MagicMock()
        memory_session.session_id = "restored-sid"
        memory_session.engine.messages = []
        memory_session.bundle = MagicMock()
        memory_session.bundle.cwd = str(tmp_path)
        host._sessions = {"s1": memory_session}
        host._active_session_id = "old-sid"
        dispatcher = WebApiDispatcher(host)
        return dispatcher

    @pytest.mark.asyncio
    async def test_restore_emits_started_and_completed(self, dispatcher_restore):
        """测试恢复流程发送 web_restore_started 与 web_restore_completed"""
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_restore_session", session_id="s1")
        await dispatcher_restore.handle(req)
        calls = dispatcher_restore._host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "web_restore_started" in types
        assert "web_restore_completed" in types
        # web_restore_completed 必须携带 state 快照
        completed = next(c.args[0] for c in calls if c.args[0].type == "web_restore_completed")
        assert completed.state is not None
        assert completed.session_id == "restored-sid"
        # 恢复已存在会话应切换为活跃会话
        dispatcher_restore._host._set_active_session.assert_called_once_with("restored-sid")

    @pytest.mark.asyncio
    async def test_restore_no_select_request_or_command_result(self, dispatcher_restore):
        """测试恢复流程不产生 select_request/command_result（零 suppress 保证）"""
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_restore_session", session_id="s1")
        await dispatcher_restore.handle(req)
        calls = dispatcher_restore._host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "select_request" not in types
        assert "command_result" not in types


class _FakeEngine:
    """最小 engine 模拟：attach_session 以 store 为唯一权威（与真实 QueryEngine 一致）。

    真实 QueryEngine.attach_session 会将 session_id 设为 store.session_id，
    MagicMock 无法表达该行为，故用本类替代，使测试能精确断言
    "bundle.session_id 与 store 指向同一会话目录"。
    """

    def __init__(self, session_id: str = "old-sid") -> None:
        self.session_id = session_id
        self.store = None

    def attach_session(self, store) -> None:
        self.store = store
        self.session_id = store.session_id

    def clear(self) -> None:
        pass

    def full_reset(self) -> None:
        self.session_id = ""
        self.store = None

    # sync_app_state 依赖的只读方法
    def set_max_turns(self, _n) -> None:
        pass

    def current_context_tokens(self) -> int:
        return 0

    @property
    def last_api_usage(self):
        return None

    @property
    def total_usage(self):
        return MagicMock(input_tokens=0, output_tokens=0,
                         cache_read_input_tokens=0, cache_creation_input_tokens=0)


class TestWebNewAndDeleteSession:
    """web_new_session 与 web_delete_sessions 测试（多会话架构）"""

    @pytest.fixture
    def dispatcher(self, monkeypatch, tmp_path):
        """多会话架构下新建/删除会话的 mock 宿主。

        新建会话由 host._create_session 负责（真实实现在 host 层测试覆盖），
        此处 mock 返回预置的会话运行时。
        """
        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.session_id = "old-sid"
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        host._push_sessions = AsyncMock()
        host._session_state_payload = MagicMock(return_value={"session_id": "new-sid"})
        host._dispose_session = AsyncMock()
        new_session = MagicMock()
        new_session.session_id = "new-sid"
        new_session.bundle = MagicMock()
        new_session.bundle.cwd = str(tmp_path)
        host._create_session = AsyncMock(return_value=new_session)
        host._sessions = {}
        host._active_session_id = "old-sid"
        dispatcher = WebApiDispatcher(host)
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._list_session_snapshots", lambda cwd, limit=20: []
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._delete_session_by_id", lambda cwd, sid: True
        )
        return dispatcher

    @pytest.mark.asyncio
    async def test_delete_sessions_calls_file_history_cleanup(self, monkeypatch, tmp_path):
        """测试删除会话时必须调用对应 file-history 目录清理（与 CLI 对齐）。

        根因：file-history 备份目录独立于会话目录树存储，必须显式调用
        cleanup_file_history/session_id 否则产生磁盘泄漏。
        """
        from illusion.ui.protocol import FrontendRequest
        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.session_id = "current-sid"
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        host._push_sessions = AsyncMock()
        host._dispose_session = AsyncMock()
        host._create_session = AsyncMock()
        host._session_state_payload = MagicMock(return_value={})
        host._sessions = {}
        host._active_session_id = "current-sid"

        dispatcher = WebApiDispatcher(host)

        cleanup_calls: list[str] = []
        cleanup_all_calls: list[int] = []

        def fake_cleanup(sid: str) -> None:
            cleanup_calls.append(sid)

        def fake_cleanup_all() -> int:
            cleanup_all_calls.append(1)
            return 0

        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._list_session_snapshots",
            lambda cwd, limit=20: [{"session_id": "s1"}, {"session_id": "s2"}],
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._delete_session_by_id",
            lambda cwd, sid: True,
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._cleanup_file_history",
            fake_cleanup,
        )
        # _cleanup_all_file_histories 已从 ws_web_api 移除（delete_all 改为
        # 按会话清理）；cleanup_all_calls 用于断言该函数绝不被调用

        # 场景一：批量删除指定会话
        req = FrontendRequest(type="web_delete_sessions", session_ids=["s1", "s2"])
        await dispatcher.handle(req)
        # 两个会话都应该清理 file-history
        assert set(cleanup_calls) == {"s1", "s2"}, (
            f"每个被删会话都应调用 cleanup_file_history，实际调用={cleanup_calls}"
        )
        cleanup_calls.clear()

        # 场景二：删除全部会话
        req2 = FrontendRequest(type="web_delete_sessions", delete_all=True)
        await dispatcher.handle(req2)
        # delete_all 限定当前工作区：按会话逐个清理 file-history，
        # 不再调用 cleanup_all_file_histories（会清掉所有工作区的撤销历史）
        assert set(cleanup_calls) == {"s1", "s2"}, (
            f"delete_all 应按会话清理 file-history，实际调用={cleanup_calls}"
        )
        assert len(cleanup_all_calls) == 0, (
            "delete_all 不应调用 cleanup_all_file_histories（避免误删其他工作区历史）"
        )

    @pytest.mark.asyncio
    async def test_new_session_emits_web_restore_completed_empty(self, dispatcher):
        """测试新建会话：创建独立运行时并发送空 transcript 的 web_restore_completed"""
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_new_session")
        await dispatcher.handle(req)
        # 新建会话应调用 host._create_session 并切换活跃会话
        dispatcher._host._create_session.assert_awaited_once()
        dispatcher._host._set_active_session.assert_called_once_with("new-sid")
        calls = dispatcher._host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "web_restore_completed" in types
        completed = next(c.args[0] for c in calls if c.args[0].type == "web_restore_completed")
        assert completed.session_id == "new-sid"
        assert completed.items == []

    @pytest.mark.asyncio
    async def test_new_session_does_not_touch_existing_sessions(self, dispatcher):
        """测试新建会话不影响已有会话的运行时（多会话并发核心保证）"""
        from illusion.ui.protocol import FrontendRequest
        existing = MagicMock()
        existing.session_id = "old-sid"
        dispatcher._host._sessions = {"old-sid": existing}
        req = FrontendRequest(type="web_new_session")
        await dispatcher.handle(req)
        # 旧会话运行时必须原样保留
        assert dispatcher._host._sessions["old-sid"] is existing
        dispatcher._host._dispose_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_sessions_pushes_list(self, dispatcher):
        """测试批量删除后释放运行时并推送 web_sessions"""
        from illusion.ui.protocol import FrontendRequest
        target = MagicMock()
        target.session_id = "s1"
        target.busy = False
        dispatcher._host._sessions = {"s1": target}
        req = FrontendRequest(type="web_delete_sessions", session_ids=["s1", "s2"])
        await dispatcher.handle(req)
        # 内存中的被删会话运行时被释放
        dispatcher._host._dispose_session.assert_called_once_with("s1")
        dispatcher._host._push_sessions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_current_session_creates_fresh_session(self, dispatcher):
        """测试删除活跃会话后原子化新建空会话（两阶段竞态防护）"""
        from illusion.ui.protocol import FrontendRequest
        target = MagicMock()
        target.session_id = "old-sid"
        target.busy = False
        dispatcher._host._sessions = {"old-sid": target}
        dispatcher._host._active_session_id = "old-sid"
        req = FrontendRequest(type="web_delete_sessions", session_ids=["old-sid"])
        await dispatcher.handle(req)
        dispatcher._host._dispose_session.assert_called_once_with("old-sid")
        # 活跃会话被删后新建空会话并切换
        dispatcher._host._create_session.assert_awaited_once()
        dispatcher._host._set_active_session.assert_called_once_with("new-sid")
        calls = dispatcher._host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "web_restore_completed" in types
        completed = next(c.args[0] for c in calls if c.args[0].type == "web_restore_completed")
        assert completed.session_id == "new-sid"


class TestWebSetSetting:
    """web_set_setting 统一设置入口测试"""

    @pytest.fixture
    def dispatcher_setting(self, monkeypatch, tmp_path):
        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        dispatcher = WebApiDispatcher(host)

        # mock settings 读写：用真实 Settings 实例便于断言字段写入
        fake_settings = MagicMock()
        fake_settings.effort = "medium"
        fake_settings.permission.mode.value = "default"
        fake_settings.ui_language = "zh-CN"
        fake_settings.context_window = 200000
        fake_settings.model = "env_1.model_1"
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._load_settings", lambda: fake_settings
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._save_settings", lambda s: None
        )
        return dispatcher, fake_settings

    @pytest.mark.asyncio
    async def test_set_effort_writes_and_emits(self, dispatcher_setting):
        """测试设置 effort 后写入 settings 并发送 web_setting_changed + state_snapshot"""
        dispatcher, fake_settings = dispatcher_setting
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_set_setting", setting_key="effort", setting_value="high")
        await dispatcher.handle(req)
        assert fake_settings.effort == "high"
        calls = dispatcher._host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "web_setting_changed" in types

    @pytest.mark.asyncio
    async def test_set_permission_mode_writes(self, dispatcher_setting):
        """测试设置 permission_mode 写入 settings.permission.mode"""
        dispatcher, fake_settings = dispatcher_setting
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_set_setting", setting_key="permission_mode", setting_value="plan")
        await dispatcher.handle(req)
        assert fake_settings.permission.mode.value == "plan"

    @pytest.mark.asyncio
    async def test_set_unknown_key_emits_error(self, dispatcher_setting):
        """测试未知设置键返回 error"""
        dispatcher, _ = dispatcher_setting
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_set_setting", setting_key="nonexistent", setting_value="x")
        await dispatcher.handle(req)
        calls = dispatcher._host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "error" in types

    @pytest.mark.asyncio
    async def test_set_permission_mode_with_real_enum(self, monkeypatch, tmp_path):
        """测试设置 permission_mode 使用真实 PermissionMode 枚举（回归 Enum 只读 value 问题）"""
        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        # 多工作区：默认工作区即 host._bundle（active/workspace_bundles 指向它）
        host._active_bundle = MagicMock(return_value=host._bundle)
        host._workspace_bundles = MagicMock(return_value=[host._bundle])
        dispatcher = WebApiDispatcher(host)

        # 使用真实 Settings 实例（含真实 PermissionMode 枚举）
        from illusion.config.settings import Settings
        real_settings = Settings()
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._load_settings", lambda: real_settings
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._save_settings", lambda s: None
        )
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_set_setting", setting_key="permission_mode", setting_value="plan")
        await dispatcher.handle(req)
        # 验证权限模式确实切换为 plan
        assert real_settings.permission.mode.value == "plan"
        # 验证引擎的权限检查器被更新（回归：旧实现未更新 PermissionChecker）
        host._bundle.engine.set_permission_checker.assert_called_once()
        # 验证发送了 web_setting_changed 而非 error
        calls = host._emit.call_args_list
        types = [c.args[0].type for c in calls]
        assert "web_setting_changed" in types
        assert "error" not in types


class TestEngineSettingBroadcast:
    """设置变更广播到所有会话引擎（多会话一致性）测试"""

    @pytest.mark.asyncio
    async def test_permission_mode_reaches_all_session_engines(self, monkeypatch, tmp_path):
        """切换权限模式必须更新所有会话引擎的 PermissionChecker（安全相关）。"""
        from illusion.ui.protocol import FrontendRequest
        from illusion.ui.web.ws_web_api import WebApiDispatcher

        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        # 多工作区：默认工作区即 host._bundle（active/workspace_bundles 指向它）
        host._active_bundle = MagicMock(return_value=host._bundle)
        host._workspace_bundles = MagicMock(return_value=[host._bundle])
        # 初始引擎 + 两个会话引擎
        host._bundle.engine = MagicMock()
        engine_a = MagicMock()
        engine_b = MagicMock()
        session_a = MagicMock()
        session_a.engine = engine_a
        session_b = MagicMock()
        session_b.engine = engine_b
        host._sessions = {"a": session_a, "b": session_b}
        dispatcher = WebApiDispatcher(host)

        from illusion.config.settings import Settings
        real_settings = Settings()
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._load_settings", lambda: real_settings
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._save_settings", lambda s: None
        )

        req = FrontendRequest(type="web_set_setting", setting_key="permission_mode", setting_value="plan")
        await dispatcher.handle(req)

        # 三个引擎（初始 + 两个会话）都被注入新的 PermissionChecker
        host._bundle.engine.set_permission_checker.assert_called_once()
        engine_a.set_permission_checker.assert_called_once()
        engine_b.set_permission_checker.assert_called_once()
        assert real_settings.permission.mode.value == "plan"

    @pytest.mark.asyncio
    async def test_model_switch_reaches_all_session_engines(self, monkeypatch, tmp_path):
        """切换模型必须同步所有会话引擎的 model 与 api_client。"""
        from illusion.ui.protocol import FrontendRequest
        from illusion.ui.web.ws_web_api import WebApiDispatcher

        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        # 多工作区：默认工作区即 host._bundle（active/workspace_bundles 指向它）
        host._active_bundle = MagicMock(return_value=host._bundle)
        host._workspace_bundles = MagicMock(return_value=[host._bundle])
        host._bundle.engine = MagicMock()
        host._bundle.api_client = MagicMock()
        engine_a = MagicMock()
        session_a = MagicMock()
        session_a.engine = engine_a
        session_a.bundle = MagicMock(cwd=str(tmp_path))
        host._sessions = {"a": session_a}
        dispatcher = WebApiDispatcher(host)

        fake_settings = MagicMock()
        fake_settings.effort = "medium"
        fake_settings.permission.mode.value = "default"
        fake_settings.ui_language = "zh-CN"
        fake_settings.context_window = 200000
        fake_settings.model = "env_1.model_1"
        fake_settings.active_model_name = "NewModel"
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._load_settings", lambda: fake_settings
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._save_settings", lambda s: None
        )
        monkeypatch.setattr(
            "illusion.ui.runtime._rebuild_api_client", lambda b, s: None
        )

        req = FrontendRequest(type="web_set_setting", setting_key="model", setting_value="env_1.model_2")
        await dispatcher.handle(req)

        # 初始引擎 + 会话引擎都同步了 model 与 api_client
        host._bundle.engine.set_model.assert_called_once_with("NewModel")
        host._bundle.engine.set_api_client.assert_called_once()
        engine_a.set_model.assert_called_once_with("NewModel")
        engine_a.set_api_client.assert_called_once()


class TestWebModels:
    """web_models 推送与 web_request_models 测试"""

    @pytest.fixture
    def dispatcher_models(self, tmp_path):
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        # 复用 ws_host 的 _model_select_options 生成模型选项
        host._model_select_options = MagicMock(return_value=[
            {"value": "env_1.model_1", "label": "M1", "active": True},
        ])
        dispatcher = WebApiDispatcher(host)
        return dispatcher

    @pytest.mark.asyncio
    async def test_request_models_emits_web_models(self, dispatcher_models):
        """测试拉取模型选项发送 web_models 事件"""
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_request_models")
        await dispatcher_models.handle(req)
        calls = dispatcher_models._host._emit.call_args_list
        models_evts = [c.args[0] for c in calls if c.args[0].type == "web_models"]
        assert len(models_evts) == 1
        assert models_evts[0].web_models[0]["active"] is True


class TestWebResources:
    """web_resources 推送测试"""

    @pytest.mark.asyncio
    async def test_request_resources_emits_web_resources(self, monkeypatch, tmp_path):
        """测试拉取资源发送 web_resources 事件"""
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        # 多工作区：无会话时资源回退活跃/默认 bundle
        host._sessions = {}
        host._active_bundle = MagicMock(return_value=host._bundle)
        host._workspace_bundles = MagicMock(return_value=[host._bundle])
        dispatcher = WebApiDispatcher(host)

        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._collect_resources",
            lambda bundle: {
                "skills": [{"name": "s1", "description": "d", "source": "project"}],
                "plugins": [],
                "rules": [],
                "mcp_servers": [],
            },
        )
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_request_resources")
        await dispatcher.handle(req)
        calls = host._emit.call_args_list
        res_evts = [c.args[0] for c in calls if c.args[0].type == "web_resources"]
        assert len(res_evts) == 1
        assert res_evts[0].web_resources["skills"][0]["name"] == "s1"

    @pytest.mark.asyncio
    async def test_push_resources_reuses_collect(self, monkeypatch, tmp_path):
        """测试 _push_resources 复用 _collect_resources"""
        host = MagicMock()
        host._emit = AsyncMock()
        bundle = MagicMock()
        bundle.cwd = str(tmp_path)
        dispatcher = WebApiDispatcher(host)
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._collect_resources",
            lambda b: {"skills": [], "plugins": [], "rules": [], "mcp_servers": []},
        )
        await dispatcher._push_resources(bundle)
        assert host._emit.called


class TestWebQuery:
    """web_query B 通道精细化指令测试"""

    @pytest.fixture
    def dispatcher_query(self, monkeypatch, tmp_path):
        host = MagicMock()
        host._emit = AsyncMock()
        host._status_snapshot = MagicMock(return_value=MagicMock())
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        # B 通道指令按会话路由：解析出会话后使用其 bundle
        session = MagicMock()
        session.session_id = "s1"
        session.bundle = MagicMock()
        session.bundle.cwd = str(tmp_path)
        host._resolve_session = MagicMock(return_value=session)
        dispatcher = WebApiDispatcher(host)
        return dispatcher

    @pytest.mark.asyncio
    async def test_query_setting_emits_web_query_result(self, dispatcher_query, monkeypatch):
        """测试设置类指令(/turns 200)走 web_query 后返回 web_query_result"""
        fake_settings = MagicMock()
        fake_settings.effort = "medium"
        fake_settings.permission.mode.value = "default"
        fake_settings.ui_language = "zh-CN"
        fake_settings.context_window = 200000
        fake_settings.model = "env_1.model_1"
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._load_settings", lambda: fake_settings
        )
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._save_settings", lambda s: None
        )
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_query", command="turns", args="200", request_id="r1")
        await dispatcher_query.handle(req)
        calls = dispatcher_query._host._emit.call_args_list
        result_evts = [c.args[0] for c in calls if c.args[0].type == "web_query_result"]
        assert len(result_evts) == 1
        assert result_evts[0].web_request_id == "r1"
        assert result_evts[0].web_query_kind == "text"

    @pytest.mark.asyncio
    async def test_query_unknown_command_emits_result(self, dispatcher_query, monkeypatch):
        """测试未知/执行型指令返回 web_query_result"""
        async def fake_run(line, bundle):
            from illusion.commands.types import CommandResult
            return CommandResult(message="结果文本")
        monkeypatch.setattr(
            "illusion.ui.web.ws_web_api._run_command_via_registry", fake_run
        )
        from illusion.ui.protocol import FrontendRequest
        req = FrontendRequest(type="web_query", command="compact", args="", request_id="r2")
        await dispatcher_query.handle(req)
        calls = dispatcher_query._host._emit.call_args_list
        result_evts = [c.args[0] for c in calls if c.args[0].type == "web_query_result"]
        assert len(result_evts) == 1
        assert result_evts[0].web_query_payload == "结果文本"


from illusion.browser.base import TabInfo
from illusion.ui.protocol import FrontendRequest


class TestBrowserPickAndTabsHandlers:
    """浏览器拾取/tab 处理器：自开兜底、payload 透传、错误不炸分发循环。"""

    def _make_dispatcher(self, tmp_path, manager):
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._bundle.app_state.get.return_value = MagicMock(ui_language="zh-CN")
        host._create_background_task = MagicMock(side_effect=lambda coro: coro.close())
        host._sessions = {}
        # 无活跃 bundle：_resolve_resource_bundle 回落 host._bundle（被测 manager）
        host._active_bundle = MagicMock(return_value=None)
        dispatcher = WebApiDispatcher(host)
        host._bundle.browser_manager = manager
        return dispatcher

    @pytest.mark.asyncio
    async def test_pick_start_missing_script_emits_error(self, tmp_path):
        """无脚本（前端漏传 value）：回 error 事件，不挂起也不抛。"""
        manager = MagicMock()
        manager.is_open = True
        manager.ensure_started = AsyncMock()
        backend = MagicMock()
        backend.get_active_tab = AsyncMock(return_value=TabInfo(id="t1", url="https://x", active=True))
        manager.ensure_started.return_value = backend
        dispatcher = self._make_dispatcher(tmp_path, manager)
        req = FrontendRequest(type="web_browser_pick_start")
        await dispatcher.handle(req)
        evts = [c.args[0] for c in dispatcher._host._emit.call_args_list]
        picks = [e for e in evts if e.type == "browser_pick_result"]
        assert len(picks) == 1 and "error" in picks[0].pick

    @pytest.mark.asyncio
    async def test_pick_start_spawns_background_wait(self, tmp_path):
        """有脚本：get_active_tab + 后台长任务等待（不阻塞分发循环）。"""
        manager = MagicMock()
        manager.is_open = True
        backend = MagicMock()
        backend.get_active_tab = AsyncMock(return_value=TabInfo(id="t1", url="https://x", active=True))
        manager.ensure_started = AsyncMock(return_value=backend)
        dispatcher = self._make_dispatcher(tmp_path, manager)
        req = FrontendRequest(type="web_browser_pick_start", value="(() => Promise.resolve({status:'cancelled'}))()")
        await dispatcher.handle(req)
        backend.get_active_tab.assert_awaited_once()
        dispatcher._host._create_background_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_pick_cancel_swallows_no_tab_error(self, tmp_path):
        """取消时无可用 tab：静默（BrowserCommandError 不冒泡）。"""
        from illusion.browser.base import BrowserCommandError
        manager = MagicMock()
        manager.is_open = True
        backend = MagicMock()
        backend.get_active_tab = AsyncMock(side_effect=BrowserCommandError("No open tabs."))
        manager.ensure_started = AsyncMock(return_value=backend)
        dispatcher = self._make_dispatcher(tmp_path, manager)
        await dispatcher.handle(FrontendRequest(type="web_browser_pick_cancel"))
        evts = [c.args[0] for c in dispatcher._host._emit.call_args_list]
        assert not [e for e in evts if e.type == "error"]

    @pytest.mark.asyncio
    async def test_tabs_new_self_opens_browser(self, tmp_path):
        """+ 新建 tab：自开兜底 + 建页瞬时 + 导航后台（不阻塞串行分发循环）。"""
        manager = MagicMock()
        manager.is_open = False
        manager.panel_open = AsyncMock()
        backend = MagicMock()
        backend.new_tab = AsyncMock(return_value=TabInfo(id="t2", url="about:blank", active=True))
        backend.navigate = AsyncMock(return_value=TabInfo(id="t2", url="https://new.example", active=True))
        manager.ensure_started = AsyncMock(return_value=backend)
        manager.panel_capture = AsyncMock()
        dispatcher = self._make_dispatcher(tmp_path, manager)
        req = FrontendRequest(type="web_browser_tabs", browser_action="new", url="https://new.example")
        await dispatcher.handle(req)
        manager.panel_open.assert_awaited_once()
        # 建页瞬时完成（不带 URL），导航经后台任务执行
        backend.new_tab.assert_awaited_once_with(None)
        dispatcher._host._create_background_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_navigate_runs_in_background(self, tmp_path):
        """面板导航后台执行：分循环不被页面加载阻塞（60s 冻结根因回归）。"""
        manager = MagicMock()
        manager.is_open = True
        backend = MagicMock()
        backend.list_tabs = AsyncMock(return_value=[TabInfo(id="t1", url="about:blank", active=True)])
        backend.get_state = AsyncMock()
        backend.get_state.return_value.tabs = [TabInfo(id="t1", url="about:blank", active=True)]
        backend.get_state.return_value.viewport_width = 1280
        backend.get_state.return_value.viewport_height = 720
        backend.navigate = AsyncMock(return_value=TabInfo(id="t1", url="https://x", active=True))
        manager.ensure_started = AsyncMock(return_value=backend)
        manager.panel_capture = AsyncMock()
        dispatcher = self._make_dispatcher(tmp_path, manager)
        await dispatcher.handle(FrontendRequest(type="web_browser_navigate", url="https://x.example"))
        # 处理器立即返回：navigate 在后台任务里
        backend.navigate.assert_not_awaited()
        dispatcher._host._create_background_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_tabs_close_when_closed_is_noop(self, tmp_path):
        """关闭态下的 close/select：不开浏览器、不建 tab（保持既有语义）。"""
        manager = MagicMock()
        manager.is_open = False
        manager.panel_open = AsyncMock()
        dispatcher = self._make_dispatcher(tmp_path, manager)
        await dispatcher.handle(FrontendRequest(type="web_browser_tabs", browser_action="close", tab_id="t1"))
        manager.panel_open.assert_not_awaited()


class TestPluginToggleReachesSessionEngines:
    """热切换必须覆盖会话引擎的元数据快照（build_session_engine 对
    tool_metadata 做创建时拷贝——只更新 bundle 原引擎会让正在对话的
    会话拿到旧元数据，browser_manager 缺失 → 工具调用报"插件未启用"）。"""

    def test_toggle_on_sets_session_engine_metadata(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from illusion.ui.web.ws_web_api import WebApiDispatcher
        from illusion.ui.protocol import FrontendRequest

        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        session = MagicMock()
        session.engine._tool_metadata = {}
        session.bundle = host._bundle
        host._sessions = {"s1": session}
        host._workspace_bundles = MagicMock(return_value=[host._bundle])

        # 热切换路径里的浏览器组件打桩（管理器/工具集均为 mock）
        import illusion.ui.web.ws_web_api as mod
        manager = MagicMock()
        monkeypatch.setattr(
            mod, "_load_settings",
            lambda: MagicMock(enabled_plugins={}, browser=MagicMock(
                kernel="auto", headless=True,
                viewport_width=1280, viewport_height=720, proxy="auto")),
        )
        monkeypatch.setattr(mod, "_save_settings", lambda s: None)
        import illusion.browser as browser_pkg
        monkeypatch.setattr(browser_pkg, "BrowserManager", MagicMock(return_value=manager))
        import illusion.tools.browser_tools as bt
        monkeypatch.setattr(bt, "create_browser_tools", lambda: [])

        dispatcher = WebApiDispatcher(host)
        import asyncio
        asyncio.run(dispatcher.handle(FrontendRequest(
            type="web_plugin_toggle", setting_key="browser-use", setting_value=True)))

        # 会话引擎元数据必须拿到 bundle 的 browser_manager（回归核心断言：
        # 热切换前会话引擎的元数据快照里没有它）
        assert session.engine._tool_metadata["browser_manager"]             is host._bundle.browser_manager

    def test_toggle_off_clears_session_engine_metadata(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from illusion.ui.web.ws_web_api import WebApiDispatcher
        from illusion.ui.protocol import FrontendRequest

        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        session = MagicMock()
        session.engine._tool_metadata = {"browser_manager": MagicMock()}
        session.bundle = host._bundle
        host._sessions = {"s1": session}
        host._workspace_bundles = MagicMock(return_value=[])

        import illusion.ui.web.ws_web_api as mod
        monkeypatch.setattr(mod, "_load_settings",
                            lambda: MagicMock(enabled_plugins={}))
        monkeypatch.setattr(mod, "_save_settings", lambda s: None)
        import illusion.tools.browser_tools as bt
        monkeypatch.setattr(bt, "create_browser_tools", lambda: [])

        dispatcher = WebApiDispatcher(host)
        import asyncio
        asyncio.run(dispatcher.handle(FrontendRequest(
            type="web_plugin_toggle", setting_key="browser-use", setting_value=False)))

        assert "browser_manager" not in session.engine._tool_metadata
