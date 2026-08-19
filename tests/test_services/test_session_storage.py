"""Tests for session persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from illusion.engine.messages import ConversationMessage, TextBlock
from illusion.services.session_storage import (
    InvalidSessionIdError,
    delete_session_by_id,
    export_session_markdown,
    list_session_snapshots,
    read_meta,
    write_meta,
)


def test_write_and_read_meta(tmp_path: Path, monkeypatch):
    """write_meta / read_meta 验证新的会话元数据读写（替代旧 save/load_session_snapshot）。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    meta = {
        "session_id": "abc",
        "cwd": str(project),
        "model": "claude-test",
        "summary": "",
        "message_count": 1,
    }
    write_meta(cwd=project, session_id="abc", meta=meta)

    loaded = read_meta(project, "abc")
    assert loaded is not None
    assert loaded["model"] == "claude-test"
    assert loaded["session_id"] == "abc"


def test_export_session_markdown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    path = export_session_markdown(
        cwd=project,
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
        ],
    )

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "IllusionAgent Session Transcript" in content
    assert "hello" in content
    assert "world" in content


def test_count_turns() -> None:
    """测试轮次计数函数"""
    from illusion.services.session_storage import count_turns

    # 空消息列表
    assert count_turns([]) == 0

    # 只有系统消息
    messages = [
        {"role": "system", "text": "System prompt"},
    ]
    assert count_turns(messages) == 0

    # 只有用户消息
    messages = [
        {"role": "user", "text": "Hello"},
    ]
    assert count_turns(messages) == 1

    # 用户消息和助手消息
    messages = [
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": "Hi there"},
        {"role": "user", "text": "How are you?"},
        {"role": "assistant", "text": "I'm good"},
    ]
    assert count_turns(messages) == 2

    # 以 / 开头的 user 消息：命令本身不进 messages（handle_line 拦截），
    # 出现即真实用户输入（如未知命令落入文本通道），应计入轮次
    messages = [
        {"role": "user", "text": "/feedback完全删掉"},
        {"role": "assistant", "text": "Done"},
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": "Hi there"},
    ]
    assert count_turns(messages) == 2

    # 包含空消息
    messages = [
        {"role": "user", "text": ""},
        {"role": "user", "text": "Hello"},
    ]
    assert count_turns(messages) == 1

    # 包含 content 数组格式
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
    ]
    assert count_turns(messages) == 1


def test_save_load_delete_pending_plan_approval(tmp_path, monkeypatch):
    """测试 pending plan approval 的保存、加载、删除"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    from illusion.services.session_storage import (
        delete_pending_plan_approval,
        load_pending_plan_approval,
        save_pending_plan_approval,
    )

    plan = "# My Plan\n\nStep 1: Do X\nStep 2: Do Y"
    plan_path = "/home/user/.illusion/plans/my-plan.md"

    save_pending_plan_approval(
        cwd=tmp_path,
        session_id="abc123",
        plan=plan,
        plan_path=plan_path,
    )

    loaded = load_pending_plan_approval(tmp_path, "abc123")
    assert loaded is not None
    assert loaded["plan"] == plan
    assert loaded["plan_path"] == plan_path
    assert loaded["session_id"] == "abc123"

    deleted = delete_pending_plan_approval(tmp_path, "abc123")
    assert deleted is True

    loaded_after = load_pending_plan_approval(tmp_path, "abc123")
    assert loaded_after is None


def test_save_load_delete_pending_permission(tmp_path, monkeypatch):
    """测试 pending-permission 持久化函数"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    from illusion.services.session_storage import (
        delete_pending_permission,
        load_pending_permission,
        save_pending_permission,
    )
    cwd = str(tmp_path / "project")
    session_id = "test-session-001"

    # 保存
    path = save_pending_permission(
        cwd=cwd,
        session_id=session_id,
        tool_name="write_file",
        reason="Mutating tools require user confirmation",
    )
    assert path.exists()

    # 加载
    data = load_pending_permission(cwd, session_id)
    assert data is not None
    assert data["tool_name"] == "write_file"
    assert data["reason"] == "Mutating tools require user confirmation"
    assert data["approved"] is False
    assert data["session_id"] == session_id

    # 删除
    delete_pending_permission(cwd, session_id)
    assert load_pending_permission(cwd, session_id) is None


def test_list_session_snapshots_filters_empty(tmp_path: Path, monkeypatch):
    """list_session_snapshots 过滤 message_count == 0 的空会话。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    # 空会话（message_count=0）
    write_meta(cwd=project, session_id="empty1", meta={
        "session_id": "empty1",
        "cwd": str(project),
        "model": "test",
        "created_at": 1000.0,
        "updated_at": 1000.0,
        "summary": "",
        "message_count": 0,
        "turn_count": 0,
    })
    # 有内容的会话
    write_meta(cwd=project, session_id="real1", meta={
        "session_id": "real1",
        "cwd": str(project),
        "model": "test",
        "created_at": 2000.0,
        "updated_at": 2000.0,
        "summary": "你好",
        "message_count": 2,
        "turn_count": 1,
    })

    sessions = list_session_snapshots(cwd=project)
    # 空会话被过滤，只返回有内容的会话
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "real1"
    assert sessions[0]["turn_count"] == 1
    assert sessions[0]["summary"] == "你好"


def test_validate_session_id_rejects_traversal(tmp_path: Path, monkeypatch):
    """session_id 含路径遍历字符应被拒绝。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    # 各种路径遍历尝试都应抛出 InvalidSessionIdError
    evil_ids = [
        "..",
        "../..",
        "..\\..",
        "/etc/passwd",
        "\\windows\\system32",
        "~/malicious",
        "C:\\windows",
        "foo/bar",
        "foo\\bar",
    ]
    for evil in evil_ids:
        with pytest.raises(InvalidSessionIdError):
            read_meta(cwd=project, session_id=evil)
        with pytest.raises(InvalidSessionIdError):
            write_meta(cwd=project, session_id=evil, meta={})
        with pytest.raises(InvalidSessionIdError):
            delete_session_by_id(cwd=project, session_id=evil)

    # 空 session_id 也要拒绝
    with pytest.raises(InvalidSessionIdError):
        read_meta(cwd=project, session_id="")


def test_checkpoint_store_validates_session_id(tmp_path: Path, monkeypatch):
    """CheckpointStore 构造时校验 session_id。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    from illusion.services.checkpoint_store import CheckpointStore

    with pytest.raises(InvalidSessionIdError):
        CheckpointStore(tmp_path / "evil", "../../etc")


def test_read_index_does_not_create_session_dir(monkeypatch, tmp_path):
    """read_index 不应创建 session 目录"""
    import os
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    from illusion.services.session_storage import read_index, get_sessions_dir

    # 在隔离的 data 目录下调用 read_index
    result = read_index(str(tmp_path))

    # read_index 应返回 None（无 index.json）
    assert result is None

    # session 目录不应被创建
    sessions_dir = get_sessions_dir()
    sub_dirs = [p for p in sessions_dir.iterdir() if p.is_dir()]
    assert len(sub_dirs) == 0, "read_index 不应创建任何 session 子目录"


def test_list_session_snapshots_does_not_create_dir(monkeypatch, tmp_path):
    """list_session_snapshots 不应创建 session 目录"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    from illusion.services.session_storage import list_session_snapshots, get_sessions_dir

    result = list_session_snapshots(str(tmp_path))
    assert result == []

    sessions_dir = get_sessions_dir()
    sub_dirs = [p for p in sessions_dir.iterdir() if p.is_dir()]
    assert len(sub_dirs) == 0, "list_session_snapshots 不应创建任何 session 子目录"


def test_write_meta_still_creates_dir(monkeypatch, tmp_path):
    """write_meta 应正常创建 session 目录"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    from illusion.services.session_storage import write_meta, get_project_session_dir

    write_meta(str(tmp_path), "test-session-1", {"session_id": "test-session-1"})

    session_dir = get_project_session_dir(str(tmp_path))
    assert (session_dir / "test-session-1" / "meta.json").exists()


def test_get_project_session_dir_no_create_does_not_create_dir(monkeypatch, tmp_path):
    """公开的 get_project_session_dir_no_create 不应创建 session 子目录。

    回归测试：Task 4 曾将该函数设为私有，外部只读调用方被迫使用会 mkdir 的
    get_project_session_dir，导致空 session 目录遗留。现已公开，外部只读
    调用方应改用它，确保不创建目录。
    """
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    from illusion.services.session_storage import (
        get_project_session_dir_no_create,
        get_sessions_dir,
    )

    project = tmp_path / "repo"
    project.mkdir()

    session_dir = get_project_session_dir_no_create(str(project))

    # 返回的路径不应存在于磁盘
    assert not session_dir.exists()

    # sessions 目录下不应有任何项目子目录
    sessions_dir = get_sessions_dir()
    sub_dirs = [p for p in sessions_dir.iterdir() if p.is_dir()]
    assert len(sub_dirs) == 0, "get_project_session_dir_no_create 不应创建任何 session 子目录"


@pytest.mark.asyncio
async def test_privacy_settings_handler_does_not_create_session_dir(monkeypatch, tmp_path):
    """/privacy-settings 是纯展示命令，不应在磁盘创建 session 子目录。

    回归测试：privacy_settings_handler 原先调用会 mkdir 的 get_project_session_dir
    仅为展示路径，导致空 session 目录遗留。现已改用 get_project_session_dir_no_create。
    """
    from unittest.mock import MagicMock

    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    from illusion.commands.settings import privacy_settings_handler
    from illusion.commands.types import CommandContext
    from illusion.services.session_storage import get_sessions_dir

    context = CommandContext(
        engine=MagicMock(),
        cwd=str(tmp_path / "project"),
    )

    result = await privacy_settings_handler("", context)

    # 命令应正常返回展示消息
    assert result.message is not None
    assert "session_dir" in result.message

    # session 目录下不应有任何项目子目录
    sessions_dir = get_sessions_dir()
    sub_dirs = [p for p in sessions_dir.iterdir() if p.is_dir()]
    assert len(sub_dirs) == 0, "/privacy-settings 不应创建任何 session 子目录"
