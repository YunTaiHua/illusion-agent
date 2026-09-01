"""fork_session 单元测试模块
=========================

覆盖 fork 的边界算术——这是回归代价最高的代码：
    - 全量/截断复制的 checkpoint 对齐（截断须连同截去第 N+1 轮轮首
      的 _checkpoint 行，否则 /rewind 轮次偏移）
    - 文件历史复制：session_id 改写、快照按保留 checkpoint 截断、
      备份目录复制，fork 后 /rewind both 能恢复文件
    - "[fork N]" 备份前缀（title/summary、fork-of-fork 不叠加、序号
      为全项目已有最大值 + 1）
    - 源会话不存在返回 None
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from illusion.engine.messages import ConversationMessage
from illusion.services import file_history as fh
from illusion.services.checkpoint_store import CheckpointStore
from illusion.services.session_storage import (
    fork_session,
    read_meta,
    session_dir_for,
    write_meta,
)
from illusion.tools import create_default_tool_registry


def _write_source_session(
    ws: Path, sid: str, turns: int, *, title: str | None = None
) -> None:
    """构造一个 turns 轮的磁盘会话（每轮 checkpoint+user+assistant）。"""
    sd = session_dir_for(ws, sid)
    sd.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(turns):
        lines.append(json.dumps({"role": "_checkpoint", "id": i}))
        lines.append(json.dumps({
            "role": "user",
            "message": {"role": "user", "content": [
                {"type": "text", "text": f"q{i + 1}"},
            ]},
        }))
        lines.append(json.dumps({
            "role": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": f"a{i + 1}"},
            ]},
        }))
    (sd / "context.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    meta = {
        "session_id": sid,
        "summary": "源会话摘要",
        "message_count": turns * 2,
        "turn_count": turns,
        "created_at": 1,
        "updated_at": 1,
    }
    if title:
        meta["title"] = title
    write_meta(ws, sid, meta)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的配置目录：file-history 备份写入临时目录而非 ~/.illusion。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        "illusion.config.paths.get_config_dir", lambda: config_dir)
    monkeypatch.setattr(
        "illusion.services.file_history.get_config_dir", lambda: config_dir)
    return ws


def test_fork_full_copy_preserves_turns(workspace: Path) -> None:
    _write_source_session(workspace, "aaaaaaaaaaaa", 3)
    new_sid = fork_session(workspace, "aaaaaaaaaaaa")
    assert new_sid is not None
    meta = read_meta(workspace, new_sid)
    assert meta["turn_count"] == 3
    assert meta["message_count"] == 6
    assert meta["forked_from"] == "aaaaaaaaaaaa"
    # checkpoint 数与轮数一致,rewind 计数不偏移
    store = CheckpointStore(session_dir_for(workspace, new_sid), new_sid)
    result = asyncio.run(store.restore())
    assert len(result.messages) == 6
    assert store.next_checkpoint_id == 3
    # fork 目录文件形态与 live 会话一致:锁文件 + 文件历史备份目录始终生成
    from illusion.config.paths import get_config_dir

    fork_dir = session_dir_for(workspace, new_sid)
    assert (fork_dir / "context.jsonl.lock").is_file()
    assert (get_config_dir() / "file-history" / new_sid).is_dir()


def test_fork_truncation_keeps_checkpoints_aligned(workspace: Path) -> None:
    _write_source_session(workspace, "aaaaaaaaaaaa", 4)
    new_sid = fork_session(workspace, "aaaaaaaaaaaa", 2)
    assert new_sid is not None
    meta = read_meta(workspace, new_sid)
    assert meta["turn_count"] == 2
    assert meta["message_count"] == 4
    store = CheckpointStore(session_dir_for(workspace, new_sid), new_sid)
    result = asyncio.run(store.restore())
    users = [m.text for m in result.messages if m.role == "user"]
    assert users == ["q1", "q2"]
    assert store.next_checkpoint_id == 2
    # rewind 1 轮后只剩第 1 轮（checkpoint 对齐则轮次精确）
    r2 = asyncio.run(store.rewind_to(store.next_checkpoint_id - 1))
    users2 = [m.text for m in r2.messages if m.role == "user"]
    assert users2 == ["q1"]


def test_fork_copies_file_history_and_rewind_restores(workspace: Path) -> None:
    """fork 复制文件历史(session_id 改写+快照截断+备份目录),
    fork 会话内 /rewind both 能从自己的备份恢复文件。"""

    sid = "aaaaaaaaaaaa"
    f1 = workspace / "a.txt"
    f1.write_text("a0", encoding="utf-8")

    # 源会话:2 轮,每轮经真实 track_edit 备份后修改文件
    sd = session_dir_for(workspace, sid)
    store = CheckpointStore(sd, sid)
    engine = _make_engine(workspace, store)
    write_meta(workspace, sid, {
        "session_id": sid, "summary": "s", "message_count": 4,
        "turn_count": 2, "created_at": 1, "updated_at": 1,
    })
    _run_turn(engine, store, f1, "a1", 1)
    _run_turn(engine, store, f1, "a2", 2)
    assert f1.read_text(encoding="utf-8") == "a2"

    # 截断 fork 保留 1 轮
    new_sid = fork_session(workspace, sid, 1)
    assert new_sid is not None

    # 复制的 file_history.json:session_id 已改写、快照按 checkpoint 截断
    data = json.loads(
        (session_dir_for(workspace, new_sid) / "file_history.json")
        .read_text(encoding="utf-8"))
    assert data["session_id"] == new_sid
    assert [s["checkpoint_id"] for s in data["snapshots"]] == [0]

    # 备份目录已复制
    from illusion.config.paths import get_config_dir
    assert (get_config_dir() / "file-history" / new_sid).is_dir()

    # 未对齐载入路径(_ensure_file_history 不传 checkpoint_count)也不会
    # 看到越界快照——截断在复制时就已完成
    loaded = fh.load(
        str(workspace), new_sid, session_dir=session_dir_for(workspace, new_sid))
    assert loaded is not None
    assert [s.checkpoint_id for s in loaded.snapshots] == [0]

    # fork 会话内新增一轮修改,然后 /rewind both 语义:恢复到该轮开始前
    fstore = CheckpointStore(session_dir_for(workspace, new_sid), new_sid)
    fengine = _make_engine(workspace, fstore)
    fengine.apply_restore(asyncio.run(fstore.restore()))
    fengine.load_file_history(checkpoint_count=fstore.next_checkpoint_id)
    _run_turn(fengine, fstore, f1, "a_fork", 2)
    assert f1.read_text(encoding="utf-8") == "a_fork"

    changed = fh.rewind_to(fengine.file_history, fstore.next_checkpoint_id - 1)
    assert changed
    # 恢复到 fork 点的文件实况
    assert f1.read_text(encoding="utf-8") == "a2"


def _make_engine(workspace: Path, store: CheckpointStore):
    from illusion.engine.query_engine import QueryEngine

    engine = QueryEngine(
        api_client=object(),
        tool_registry=create_default_tool_registry(),
        permission_checker=None,
        cwd=str(workspace),
        model="test",
        system_prompt="",
    )
    engine.attach_session(store)
    return engine


def _run_turn(engine, store: CheckpointStore, fpath: Path, content: str, turn_no: int) -> None:
    """模拟一轮:checkpoint + 用户消息 + 文件历史快照 + 编辑文件。"""
    from illusion.services.file_history import make_snapshot

    cp = asyncio.run(store.append_checkpoint())
    engine._messages.append(ConversationMessage.from_user_text(f"q{turn_no}"))
    asyncio.run(store.append_message(engine._messages[-1]))
    engine._ensure_file_history()
    make_snapshot(engine._file_history, str(len(engine._messages)), cp)
    engine.on_before_tool_execute("edit_file", {"file_path": str(fpath)})
    fpath.write_text(content, encoding="utf-8")


def test_fork_prefix_on_title_and_summary(workspace: Path) -> None:
    _write_source_session(
        workspace, "aaaaaaaaaaaa", 2, title="爬虫项目")
    sid2 = "bbbbbbbbbbbb"
    _write_source_session(workspace, sid2, 2)  # 无 title

    f1 = fork_session(workspace, "aaaaaaaaaaaa")
    f2 = fork_session(workspace, "aaaaaaaaaaaa")  # 同源再 fork:序号递增
    f3 = fork_session(workspace, sid2)

    m1 = read_meta(workspace, f1)
    m2 = read_meta(workspace, f2)
    m3 = read_meta(workspace, f3)
    assert m1["title"] == "[fork 1] 爬虫项目"
    assert m2["title"] == "[fork 2] 爬虫项目"  # 序号递增且不叠加旧前缀
    assert m3.get("title") is None
    assert m3["summary"] == "[fork 3] 源会话摘要"  # 无 title 时写 summary


def test_fork_missing_source_returns_none(workspace: Path) -> None:
    assert fork_session(workspace, "zzzzzzzzzzzz") is None


def test_fork_of_fork_does_not_stack_prefix(workspace: Path) -> None:
    _write_source_session(
        workspace, "aaaaaaaaaaaa", 2, title="[fork 7] 原标题")
    new_sid = fork_session(workspace, "aaaaaaaaaaaa")
    meta = read_meta(workspace, new_sid)
    assert meta["title"] == "[fork 8] 原标题"
