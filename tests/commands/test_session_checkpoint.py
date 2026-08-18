"""session 命令 checkpoint 集成测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.commands.session import new_handler, rewind_handler
from illusion.commands.types import CommandContext


def _make_context(tmp_path: Path, engine=None) -> CommandContext:
    """构造测试 CommandContext。"""
    return CommandContext(
        engine=engine or MagicMock(),
        cwd=str(tmp_path),
        session_id="test_sid",
    )


@pytest.mark.asyncio
async def test_new_handler_full_reset(tmp_path: Path) -> None:
    """/new 调用 full_reset 不保存当前会话。"""
    engine = MagicMock()
    engine.messages = [MagicMock()]
    ctx = _make_context(tmp_path, engine)

    result = await new_handler("", ctx)

    engine.full_reset.assert_called_once()
    assert result.reset_session is True
    assert result.clear_screen is True


@pytest.mark.asyncio
async def test_rewind_no_checkpoint(tmp_path: Path) -> None:
    """无 checkpoint 时 /rewind 返回提示。"""
    engine = MagicMock()
    engine.checkpoint_store = None
    ctx = _make_context(tmp_path, engine)

    result = await rewind_handler("1", ctx)
    assert "No checkpoint" in (result.message or "")


@pytest.mark.asyncio
async def test_rewind_to_checkpoint_id_no_misalignment_after_partial_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rewind_to 按 checkpoint_id 定位，部分移除后再整体回退不应错位。

    场景：
    - 3 轮对话 + 文件修改，快照 cp_id 分别为 0/1/2
    - 先按位置移除最后 1 个快照（模拟底层部分回退）
    - 发新消息：新快照 cp_id=3
    - rewind_to(3)：移除 cp_id>=3 的快照
    - rewind_to(2)：无 cp_id>=2 的快照（已被移除），不恢复文件
    """
    from illusion.services.file_history import (
        FileHistoryState,
        make_snapshot,
        rewind_to,
    )

    state = FileHistoryState(session_id="abc", cwd=str(tmp_path))
    make_snapshot(state, "1", checkpoint_id=0)  # S0
    make_snapshot(state, "2", checkpoint_id=1)  # S1
    make_snapshot(state, "3", checkpoint_id=2)  # S2

    # 按位置移除最后 1 个快照（模拟底层部分回退）
    target_index = max(0, len(state.snapshots) - 1)
    target_cp_id = state.snapshots[target_index].checkpoint_id
    rewind_to(state, target_cp_id)
    assert len(state.snapshots) == 2
    assert [s.checkpoint_id for s in state.snapshots] == [0, 1]

    # 发新消息: cp=3 (假设), 新快照 cp_id=3
    make_snapshot(state, "4", checkpoint_id=3)
    assert [s.checkpoint_id for s in state.snapshots] == [0, 1, 3]

    # rewind_to(3): 移除 cp_id>=3 的快照
    rewind_to(state, 3)
    assert [s.checkpoint_id for s in state.snapshots] == [0, 1]

    # rewind_to(2): 无 cp_id>=2 的快照（S2 已被移除），不恢复文件
    changed = rewind_to(state, 2)
    assert changed == []
    assert [s.checkpoint_id for s in state.snapshots] == [0, 1]


@pytest.mark.asyncio
async def test_resume_loads_file_history(tmp_path: Path) -> None:
    """/resume 后 engine.file_history 应从磁盘加载。"""
    from illusion.services.file_history import (
        FileHistoryState,
        make_snapshot,
        save,
    )
    cwd = str(tmp_path)
    session_id = "abc123"
    state = FileHistoryState(session_id=session_id, cwd=cwd)
    make_snapshot(state, "old", checkpoint_id=0)
    save(state)

    from illusion.services.file_history import load
    loaded = load(cwd, session_id, checkpoint_count=1)
    assert loaded is not None
    assert len(loaded.snapshots) == 1


def test_set_session_id_syncs_file_history_session_id(tmp_path: Path) -> None:
    """set_session_id 应同步已加载 file_history 的 session_id 并重保存到新路径。

    验证场景：/new 后 engine.full_reset 清空 session_id，
    runtime 调用 set_session_id(new_id) 后，file_history.session_id
    应同步为新_id，且 file_history.json 写入新_id 目录。
    """
    from illusion.engine.query_engine import QueryEngine
    from illusion.services.file_history import (
        FileHistoryState,
        load,
        make_snapshot,
    )

    cwd = str(tmp_path)
    old_session_id = "old_sid_abc"
    new_session_id = "new_sid_xyz"

    # 预置旧 session_id 下的 file_history
    state = FileHistoryState(session_id=old_session_id, cwd=cwd)
    make_snapshot(state, "1", checkpoint_id=0)
    from illusion.services.file_history import save as fh_save
    fh_save(state)

    # 构造 engine 并加载旧 file_history
    engine = QueryEngine(
        api_client=MagicMock(),
        tool_registry=MagicMock(),
        permission_checker=MagicMock(),
        cwd=cwd,
        model="test-model",
        system_prompt="",
        session_id=old_session_id,
    )
    engine.load_file_history(checkpoint_count=1)
    assert engine.file_history is not None
    assert engine.file_history.session_id == old_session_id

    # 模拟 /new 后 runtime 调用 set_session_id(new_id)
    engine.set_session_id(new_session_id)
    assert engine._session_id == new_session_id
    assert engine.file_history.session_id == new_session_id

    # 验证 file_history.json 已写入新 session_id 目录
    from illusion.services.session_storage import get_project_session_dir_no_create
    new_path = get_project_session_dir_no_create(cwd) / new_session_id / "file_history.json"
    assert new_path.exists()
    loaded = load(cwd, new_session_id, checkpoint_count=1)
    assert loaded is not None
    assert loaded.session_id == new_session_id
    assert len(loaded.snapshots) == 1


def test_submit_message_no_file_history_when_session_id_empty(tmp_path: Path) -> None:
    """session_id 为空时 submit_message 不应创建 file_history。

    验证 Task 1 修复：移除随机 uuid 兜底后，session_id 为空时
    file_history 保持 None，避免写入随机 id 的孤立目录。
    """
    import inspect

    from illusion.engine.query_engine import QueryEngine

    # 检查源码中不再包含 uuid 兜底逻辑
    source = inspect.getsource(QueryEngine.submit_message)
    assert "uuid.uuid4" not in source
    assert "or uuid" not in source


@pytest.mark.asyncio
async def test_update_session_meta_title_not_polluted_by_stale_session_name(tmp_path: Path) -> None:
    """新会话首条消息落盘时，已清除的 session_name 不会污染 meta.title。

    回归：删除会话后新会话继承上一次重命名名称的问题，根因是
    _update_session_meta 的兜底 `title = existing.get("title") or app_state.session_name`。
    Terminal/Web 端在新建/切换会话时清除共享 session_name 后，新会话落盘 title 应为空。
    """
    from unittest.mock import MagicMock

    from illusion.engine.messages import ConversationMessage
    from illusion.services.session_storage import read_meta, session_dir_for
    from illusion.ui.runtime import _update_session_meta

    cwd = str(tmp_path)
    sid = "abc123def456"
    store = MagicMock()
    store.session_dir = session_dir_for(cwd, sid)
    store.session_id = sid
    engine = MagicMock()
    engine.checkpoint_store = store
    engine.messages = [ConversationMessage.from_user_text("hello")]
    engine.goal_manager = None

    bundle = MagicMock()
    bundle.engine = engine
    bundle.cwd = cwd
    # 模拟已在新建/切换会话时清除：session_name 为空
    bundle.app_state.get.return_value = MagicMock(session_name="", ui_language="zh-CN")
    bundle.current_settings.return_value = MagicMock(active_model_name="claude-test")

    _update_session_meta(bundle)

    meta = read_meta(cwd, sid)
    assert meta is not None
    assert not meta.get("title")
    assert meta.get("summary") == "hello"
