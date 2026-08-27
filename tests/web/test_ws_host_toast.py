"""toast 事件构建与下发测试。

覆盖：
    - build_toast_event 载荷结构与正文截断
    - notifications.enabled 总开关关闭时返回 None（后端不下发）
    - play_sound 已按 (enabled, sound) 联动标注
    - _emit_toast 在开关关闭时静默跳过
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from illusion.ui.protocol import BackendEvent
from illusion.ui.web import ws_host as ws_host_module
from illusion.ui.web.ws_host import WebBackendHost, build_toast_event


@pytest.fixture
def toast_enabled(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """可控的 (enabled, sound) 开关桩：默认全开，用例内可改。"""
    state = [True, True]

    def fake_effective() -> tuple[bool, bool]:
        return state[0], (state[0] and state[1])

    monkeypatch.setattr(ws_host_module, "_toast_settings_effective", fake_effective)
    return state


def test_build_toast_event_payload(toast_enabled: list[bool]) -> None:
    """载荷包含 kind/level/title/body/play_sound，会话标记由 _emit 负责"""
    event = build_toast_event("task_complete", "success", "任务已完成", "一切就绪")
    assert event is not None
    assert event.type == "toast"
    assert event.toast == {
        "kind": "task_complete",
        "level": "success",
        "title": "任务已完成",
        "body": "一切就绪",
        "play_sound": True,
    }


def test_build_toast_event_truncates_long_body(toast_enabled: list[bool]) -> None:
    """超长正文在服务端统一截断，防止撑爆系统级通知气泡"""
    event = build_toast_event("task_complete", "success", "标题", "x" * 5000)
    assert event is not None
    assert event.toast is not None
    assert len(event.toast["body"]) <= ws_host_module._TOAST_BODY_MAX_CHARS


def test_build_toast_event_disabled_emits_none(toast_enabled: list[bool]) -> None:
    """notifications.enabled=False：总开关关闭 → 不构建事件（None）"""
    toast_enabled[0] = False
    assert build_toast_event("ask", "info", "提问", "内容") is None


def test_play_sound_gated_by_master_switch(toast_enabled: list[bool]) -> None:
    """音效联动：toast 关闭时即使 sound=True 也不发声；都开启时才为 True"""
    toast_enabled[0] = False
    toast_enabled[1] = True
    assert build_toast_event("permission", "info", "权限", "理由") is None

    toast_enabled[0] = True
    toast_enabled[1] = False
    event = build_toast_event("permission", "info", "权限", "理由")
    assert event is not None
    assert event.toast is not None
    assert event.toast["play_sound"] is False

    toast_enabled[0] = True
    toast_enabled[1] = True
    event = build_toast_event("permission", "info", "权限", "理由")
    assert event is not None
    assert event.toast is not None
    assert event.toast["play_sound"] is True


@pytest.mark.asyncio
async def test_emit_toast_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_emit_toast 在总开关关闭时不产生任何事件入队（静默跳过）"""
    monkeypatch.setattr(
        ws_host_module,
        "_toast_settings_effective",
        lambda: (False, False),
    )
    host = object.__new__(WebBackendHost)
    emitted: list[tuple[BackendEvent, str | None]] = []

    async def fake_emit(event: BackendEvent, *, session_id: str | None = None) -> None:
        emitted.append((event, session_id))

    host._emit = fake_emit  # type: ignore[method-assign]
    await host._emit_toast("task_stopped", "info", "任务已终止", "摘要", session_id="s1")
    assert emitted == []


@pytest.mark.asyncio
async def test_emit_toast_routes_with_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_emit_toast 正常路径携带类别与会话 ID 下发"""
    captured: dict[str, Any] = {}

    async def fake_emit(event: BackendEvent, *, session_id: str | None = None) -> None:
        # 复刻真实 _emit 的路由语义：按 kwargs 会话标记事件
        if session_id:
            event.session_id = session_id
        captured["event"] = event
        captured["session_id"] = session_id

    monkeypatch.setattr(ws_host_module, "_toast_settings_effective", lambda: (True, True))
    host = object.__new__(WebBackendHost)
    host._emit = fake_emit  # type: ignore[method-assign]
    await host._emit_toast("ask", "info", "等待回答", "选哪个？", session_id="s2")

    event: BackendEvent = captured["event"]
    assert event.type == "toast"
    assert captured["session_id"] == "s2"
    assert event.session_id == "s2"
    assert event.toast is not None
    assert event.toast["kind"] == "ask"


def test_real_settings_path_reads_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实配置读取路径：经 load_settings 感知 notifications 配置"""
    from illusion.config.settings import NotificationSettings, Settings

    settings = Settings(notifications=NotificationSettings(enabled=False, sound=True))

    class FakeSettingsModule:
        @staticmethod
        def load_settings() -> Settings:
            return settings

    monkeypatch.setitem(sys.modules, "illusion.config.settings", FakeSettingsModule)
    enabled, sound = ws_host_module._toast_settings_effective()
    assert enabled is False
    # 音效仅在 toast 开启时处理
    assert sound is False


def test_toast_settings_effective_falls_back_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置读取异常时按默认全开处理（通知是增益功能，不因配置损坏静音）"""

    def broken_load() -> None:
        raise RuntimeError("config unreadable")

    class BrokenSettingsModule:
        load_settings = staticmethod(broken_load)

    monkeypatch.setitem(sys.modules, "illusion.config.settings", BrokenSettingsModule)
    assert ws_host_module._toast_settings_effective() == (True, True)


@pytest.mark.asyncio
async def test_emit_command_error_finish_parity() -> None:
    """_emit_command_error 的 line_complete 奇偶性：默认补发（解 busy），
    finish=False 时不补发（外层已有统一收尾）——任一遗漏/双发都会破坏
    前端 busy 生命周期。"""
    from illusion.ui.web.ws_host import WebBackendHost

    async def scenario(finish: bool | None) -> list[str]:
        host = object.__new__(WebBackendHost)
        types: list[str] = []

        async def fake_emit(event: BackendEvent, *, session_id: str | None = None) -> None:
            types.append(event.type)

        host._emit = fake_emit  # type: ignore[method-assign]
        if finish is None:
            await host._emit_command_error("boom", session_id="s1")
        else:
            await host._emit_command_error("boom", session_id="s1", finish=finish)
        return types

    assert await scenario(None) == ["command_result", "line_complete"]
    assert await scenario(True) == ["command_result", "line_complete"]
    assert await scenario(False) == ["command_result"]
