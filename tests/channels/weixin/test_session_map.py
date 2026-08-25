"""微信会话存储测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from illusion.channels.base import InboundMessage
from illusion.channels.weixin.session_map import WeixinSession, WeixinSessionStore
from illusion.engine.messages import ConversationMessage


def _msg(chat_id: str, user_id: str) -> InboundMessage:
    """构造测试用入站消息。"""
    return InboundMessage(
        text="hi", chat_id=chat_id, chat_type="dm",
        user_id=user_id, user_name="tester", message_id="om_1",
    )


def test_build_session_key_dm(tmp_path: Path):
    """私聊按用户隔离。"""
    store = WeixinSessionStore(data_dir=tmp_path)
    assert store.build_session_key(_msg("wx_a", "wx_a")) == "u:wx_a"


def test_get_or_create_new(tmp_path: Path):
    """新 key 创建空会话索引。"""
    store = WeixinSessionStore(data_dir=tmp_path)
    session = store.get_or_create("u:wx_a", "wx_a", "dm")
    assert isinstance(session, WeixinSession)
    assert session.session_id


def test_save_and_load_roundtrip(tmp_path: Path):
    """索引保存后能读回（不含 messages 字段）。"""
    store = WeixinSessionStore(data_dir=tmp_path)
    s1 = store.get_or_create("u:wx_a", "wx_a", "dm")
    s1.cwd = str(tmp_path / "ws")
    store.save(s1)
    raw = json.loads((tmp_path / "u_wx_a.json").read_text(encoding="utf-8"))
    assert "messages" not in raw
    s2 = store.get_or_create("u:wx_a", "wx_a", "dm")
    assert s2.session_id == s1.session_id
    assert s2.cwd == s1.cwd


@pytest.mark.asyncio
async def test_history_roundtrip_via_context_jsonl(tmp_path: Path):
    """对话历史经 context.jsonl 读写。"""
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import session_dir_for

    store = WeixinSessionStore(data_dir=tmp_path)
    s1 = store.get_or_create("u:wx_a", "wx_a", "dm")
    cwd = str(tmp_path / "ws")
    s1.cwd = cwd
    store.save(s1)

    assert (await store.load_messages(s1)).messages == []

    sdir = session_dir_for(cwd, s1.session_id)
    cp = CheckpointStore(sdir, s1.session_id)
    await cp.append_checkpoint()
    await cp.append_message(ConversationMessage.from_user_text("persisted"))

    msgs = (await store.load_messages(s1)).messages
    assert [m.text for m in msgs] == ["persisted"]


def test_clear_removes_session(tmp_path: Path):
    """clear 后重建为新会话。"""
    store = WeixinSessionStore(data_dir=tmp_path)
    s1 = store.get_or_create("u:wx_a", "wx_a", "dm")
    store.save(s1)
    store.clear("u:wx_a")
    s2 = store.get_or_create("u:wx_a", "wx_a", "dm")
    assert s2.session_id != s1.session_id


def test_set_and_get_model(tmp_path: Path):
    """会话模型可设置与读取。"""
    store = WeixinSessionStore(data_dir=tmp_path)
    store.get_or_create("u:wx_a", "wx_a", "dm")
    store.set_model("u:wx_a", "gpt-4o")
    s = store.get_or_create("u:wx_a", "wx_a", "dm")
    assert s.model == "gpt-4o"
