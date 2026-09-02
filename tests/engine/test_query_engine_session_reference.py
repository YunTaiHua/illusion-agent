"""QueryEngine 会话提及注入集成测试。

验证 submit_message 在提交边界解析 @ 会话提及：用户消息保留规范提及
文本（@[label](illusion-session:<id>) 原样入库），紧随注入源会话只读
快照消息，且两条消息均持久化到 context.jsonl（重放/恢复一致）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.engine.query_engine import QueryEngine
from illusion.services.session_reference import (
    format_session_mention,
    is_session_reference_snapshot,
    render_session_reference_snapshot,
)


def _make_engine(tmp_path: Path, session_id: str = "cur000000000") -> QueryEngine:
    """构造测试用 QueryEngine（不调用真实 API）。"""
    engine = QueryEngine(
        api_client=MagicMock(),
        tool_registry=MagicMock(),
        permission_checker=MagicMock(),
        cwd=tmp_path,
        model="test-model",
        system_prompt="sys",
        session_id=session_id,
    )
    return engine


def _write_source_session(cwd: Path, sid: str) -> None:
    """在 cwd 工作区写入一个含两轮对话的源会话。"""
    from illusion.services.session_storage import (
        get_project_session_dir,
        write_meta_to,
    )

    session_dir = get_project_session_dir(str(cwd)) / sid
    session_dir.mkdir(parents=True)
    write_meta_to(session_dir, sid, {"session_id": sid, "cwd": str(cwd)})
    records = [
        {"role": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "源会话的问题"}]}},
        {"role": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "源会话的回答"}]}},
    ]
    (session_dir / "context.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_submit_message_injects_snapshot_and_persists(
    tmp_path: Path, monkeypatch
) -> None:
    """含提及的消息：改写为 @label + 注入快照，两条消息均落盘。"""
    from illusion.services.checkpoint_store import CheckpointStore

    _write_source_session(tmp_path, "aaa000bbb111")
    engine = _make_engine(tmp_path)
    store = CheckpointStore(tmp_path / "sess", "abc")
    engine.set_checkpoint_store(store)

    import illusion.engine.query_engine as qe_mod

    async def _fake_run_query(ctx, msgs):
        if False:
            yield

    monkeypatch.setattr(qe_mod, "run_query", _fake_run_query)

    prompt = f"参考 {format_session_mention('aaa000bbb111', '调研')} 继续"
    async for _ in engine.submit_message(prompt):
        pass

    messages = engine.messages
    # 直接消息（规范提及文本原样）+ 注入快照
    assert messages[0].role == "user"
    assert messages[0].text == prompt
    assert messages[1].role == "user"
    assert is_session_reference_snapshot(messages[1].text)
    assert "源会话的问题" in messages[1].text and "源会话的回答" in messages[1].text

    # 两条消息均持久化：restore 重建的历史与内存一致
    result = await store.restore()
    assert result.messages[0].text == prompt
    assert is_session_reference_snapshot(result.messages[1].text)


@pytest.mark.asyncio
async def test_submit_message_without_mention_has_no_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    """普通消息：不注入快照，行为与原实现一致。"""
    from illusion.services.checkpoint_store import CheckpointStore

    engine = _make_engine(tmp_path)
    store = CheckpointStore(tmp_path / "sess", "abc")
    engine.set_checkpoint_store(store)

    import illusion.engine.query_engine as qe_mod

    async def _fake_run_query(ctx, msgs):
        if False:
            yield

    monkeypatch.setattr(qe_mod, "run_query", _fake_run_query)

    async for _ in engine.submit_message("@src/main.py 看看这个文件"):
        pass

    messages = engine.messages
    assert len(messages) == 1
    assert messages[0].text == "@src/main.py 看看这个文件"
    assert not any(is_session_reference_snapshot(m.text) for m in messages)


@pytest.mark.asyncio
async def test_submit_message_self_reference_not_injected(
    tmp_path: Path, monkeypatch
) -> None:
    """自引用（提及当前会话）：改写为 @label 但不注入快照。"""
    from illusion.services.checkpoint_store import CheckpointStore

    engine = _make_engine(tmp_path, session_id="abc0000000ff")
    store = CheckpointStore(tmp_path / "sess", "abc")
    engine.set_checkpoint_store(store)

    import illusion.engine.query_engine as qe_mod

    async def _fake_run_query(ctx, msgs):
        if False:
            yield

    monkeypatch.setattr(qe_mod, "run_query", _fake_run_query)

    async for _ in engine.submit_message(format_session_mention("abc0000000ff", "当前")):
        pass

    messages = engine.messages
    # 规范文本原样保留，不注入快照
    assert messages[0].text == format_session_mention("abc0000000ff", "当前")
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_snapshot_message_excluded_from_real_user_filters(tmp_path: Path) -> None:
    """注入快照不计入真实用户消息口径（latest_user_prompt 等）。"""
    from illusion.engine.messages import ConversationMessage
    from illusion.ui.runtime import _last_user_text

    engine = _make_engine(tmp_path)
    snapshot = render_session_reference_snapshot([])
    engine._messages.append(ConversationMessage.from_user_text("真实用户消息"))
    engine._messages.append(ConversationMessage.from_user_text(snapshot))
    # 最后一条真实用户消息是直接消息而非注入快照
    assert _last_user_text(engine.messages) == "真实用户消息"
