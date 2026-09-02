"""terminal 通道 @ 提及候选（backend_host._handle_file_mentions）单元测试

验证会话候选分区：磁盘快照合并、查询过滤、当前会话排除与 request_id 回显
（与 web 端 ws_web_api.handle_web_request_file_mentions 对称）。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from illusion.ui.backend_host import BackendHostConfig, ReactBackendHost
from illusion.ui.protocol import BackendEvent, FrontendRequest


class _UnusedApiClient:
    """占位 API 客户端（本测试不触发模型调用）。"""

    def stream_message(self, request):  # pragma: no cover - 未被调用
        raise AssertionError("stream_message should not be called")


def _make_session(cwd: Path, sid: str, title: str) -> None:
    """在工作区写入一个可被候选发现的会话 meta。"""
    from illusion.services.session_storage import (
        get_project_session_dir,
        write_meta_to,
    )

    session_dir = get_project_session_dir(str(cwd)) / sid
    session_dir.mkdir(parents=True)
    write_meta_to(session_dir, sid, {
        "session_id": sid,
        "cwd": str(cwd),
        "title": title,
        "summary": "",
        "message_count": 2,
        "turn_count": 2,
        "updated_at": 100.0,
    })


def _make_host(cwd: Path, current_session_id: str) -> ReactBackendHost:
    """构造带 mock bundle 的 host（cwd / 当前会话 ID / 中文界面）。"""
    host = ReactBackendHost(BackendHostConfig(api_client=_UnusedApiClient()))
    bundle = MagicMock()
    bundle.cwd = str(cwd)
    bundle.engine.session_id = current_session_id
    bundle.app_state.get().ui_language = "zh-CN"
    bundle.current_settings().ui_language = "zh-CN"
    host._bundle = bundle
    host._emit = AsyncMock()
    return host


@pytest.mark.asyncio
async def test_file_mentions_includes_sessions(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_session(ws, "aaa000000001", "调研笔记")

    host = _make_host(ws, "cur000000000")
    await host._handle_file_mentions(FrontendRequest(
        type="web_request_file_mentions", query="调研", request_id="m1",
    ))

    host._emit.assert_called_once()
    evt: BackendEvent = host._emit.call_args.args[0]
    assert evt.type == "web_file_mentions"
    assert evt.request_id == "m1"
    payload = evt.web_file_mentions
    assert payload is not None
    assert payload["query"] == "调研"
    assert payload["sessions"] == [{
        "kind": "session",
        "sessionId": "aaa000000001",
        "path": "调研笔记",
        "description": payload["sessions"][0]["description"],
        "cwd": str(ws),
    }]
    assert "轮" in payload["sessions"][0]["description"]


@pytest.mark.asyncio
async def test_file_mentions_exclude_current_session(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _make_session(ws, "aaa000000001", "当前会话")

    host = _make_host(ws, "aaa000000001")
    await host._handle_file_mentions(FrontendRequest(
        type="web_request_file_mentions", query="", request_id="m2",
    ))

    evt: BackendEvent = host._emit.call_args.args[0]
    assert evt.web_file_mentions is not None
    # 当前会话（自引用）不作为候选
    assert evt.web_file_mentions["sessions"] == []
