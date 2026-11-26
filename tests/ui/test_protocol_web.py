"""Web 专属协议模型测试模块

验证 web_* 请求类型与事件类型的协议定义正确。
"""

import pytest
from pydantic import ValidationError

from illusion.state.app_state import AppState
from illusion.ui.protocol import BackendEvent, FrontendRequest, _state_payload


class TestWebFrontendRequest:
    """web_* 前端请求类型测试"""

    def test_web_new_session_request(self):
        """测试 web_new_session 请求可构造"""
        req = FrontendRequest(type="web_new_session")
        assert req.type == "web_new_session"

    def test_web_restore_session_request(self):
        """测试 web_restore_session 请求携带 session_id"""
        req = FrontendRequest(type="web_restore_session", session_id="abc123")
        assert req.type == "web_restore_session"
        assert req.session_id == "abc123"

    def test_web_set_setting_request(self):
        """测试 web_set_setting 请求携带 key/value"""
        req = FrontendRequest(type="web_set_setting", setting_key="effort", setting_value="high")
        assert req.setting_key == "effort"
        assert req.setting_value == "high"

    def test_plugin_scope_is_an_optional_resource_scope(self):
        payload = {"type": "web_plugin_toggle", "setting_key": "notes", "setting_value": True}
        assert FrontendRequest.model_validate(payload).plugin_scope is None
        for scope in ("user", "project"):
            assert FrontendRequest.model_validate({**payload, "plugin_scope": scope}).plugin_scope == scope
        with pytest.raises(ValidationError):
            FrontendRequest.model_validate({**payload, "plugin_scope": "global"})

    def test_web_delete_sessions_request(self):
        """测试 web_delete_sessions 请求携带 ids"""
        req = FrontendRequest(type="web_delete_sessions", session_ids=["id1", "id2"])
        assert req.session_ids == ["id1", "id2"]

    def test_web_delete_all_request(self):
        """测试 web_delete_sessions 的 all 标志"""
        req = FrontendRequest(type="web_delete_sessions", delete_all=True)
        assert req.delete_all is True

    def test_web_query_request(self):
        """测试 web_query 请求携带 command/args/request_id"""
        req = FrontendRequest(
            type="web_query",
            command="rewind",
            args="",
            request_id="req-1",
        )
        assert req.command == "rewind"
        assert req.request_id == "req-1"


class TestWebBackendEvent:
    """web_* 后端事件类型测试"""

    def test_web_sessions_event(self):
        """测试 web_sessions 事件可构造"""
        evt = BackendEvent(type="web_sessions", web_sessions=[{"id": "s1", "label": "会话1"}])
        assert evt.type == "web_sessions"
        assert evt.web_sessions == [{"id": "s1", "label": "会话1"}]

    def test_web_resources_event(self):
        """测试 web_resources 事件携带 skills/plugins/rules"""
        evt = BackendEvent(
            type="web_resources",
            web_resources={"skills": [], "plugins": [], "rules": [], "mcp_servers": []},
        )
        assert evt.web_resources == {"skills": [], "plugins": [], "rules": [], "mcp_servers": []}

    def test_web_setting_changed_event(self):
        """测试 web_setting_changed 事件携带 key/value"""
        evt = BackendEvent(type="web_setting_changed", setting_key="effort", setting_value="high")
        assert evt.setting_key == "effort"

    def test_web_models_event(self):
        """测试 web_models 事件携带 options"""
        evt = BackendEvent(type="web_models", web_models=[{"value": "m1", "label": "M1", "active": True}])
        assert evt.web_models[0]["active"] is True

    def test_web_restore_started_event(self):
        """测试 web_restore_started 事件"""
        evt = BackendEvent(type="web_restore_started", session_id="s1")
        assert evt.session_id == "s1"

    def test_web_restore_completed_event(self):
        """测试 web_restore_completed 事件携带 state 快照"""
        evt = BackendEvent(
            type="web_restore_completed",
            session_id="s1",
            state={"model": "claude"},
            items=[],
        )
        assert evt.state == {"model": "claude"}

    def test_web_query_result_event(self):
        """测试 web_query_result 事件携带 request_id/kind/payload"""
        evt = BackendEvent(
            type="web_query_result",
            web_request_id="req-1",
            web_command="turns",
            web_query_kind="text",
            web_query_payload="已开启",
        )
        assert evt.web_request_id == "req-1"
        assert evt.web_query_kind == "text"


class TestStatePayload:
    """_state_payload 状态载荷测试"""

    def test_state_payload_includes_session_name(self):
        """会话显示名称必须写入状态载荷，供终端/Web 标题使用。

        回归防护：前端读 session.status.session_name，若后端未序列化该字段，终端标题功能失效。
        """
        state = AppState(model="test-model", permission_mode="default", session_id="abc", session_name="我的会话")
        payload = _state_payload(state)
        assert payload["session_id"] == "abc"
        assert payload["session_name"] == "我的会话"

    def test_state_payload_empty_session_name(self):
        """会话名称为空时载荷中为 ''（前端回退默认标题）。"""
        state = AppState(model="test-model", permission_mode="default", session_id="abc", session_name="")
        payload = _state_payload(state)
        assert payload["session_name"] == ""
