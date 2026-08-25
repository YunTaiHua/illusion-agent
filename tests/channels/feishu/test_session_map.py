"""飞书会话存储测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from illusion.channels.base import InboundMessage
from illusion.channels.feishu.session_map import FeishuSession, FeishuSessionStore
from illusion.engine.messages import ConversationMessage


def _msg(chat_id: str, user_id: str, chat_type: str = "dm") -> InboundMessage:
    """构造测试用入站消息。"""
    return InboundMessage(
        text="hi", chat_id=chat_id, chat_type=chat_type,
        user_id=user_id, user_name="tester", message_id="om_1",
    )


def test_build_session_key_dm(tmp_path: Path):
    """私聊按用户隔离。"""
    store = FeishuSessionStore(data_dir=tmp_path)
    assert store.build_session_key(_msg("ou_a", "ou_a", "dm")) == "u:ou_a"


def test_build_session_key_group_per_user(tmp_path: Path):
    """群组默认每用户隔离。"""
    store = FeishuSessionStore(data_dir=tmp_path, group_sessions_per_user=True)
    key = store.build_session_key(_msg("oc_room", "ou_a", "group"))
    assert key == "g:oc_room:ou_a"


def test_build_session_key_group_shared(tmp_path: Path):
    """群组可配置为共享会话。"""
    store = FeishuSessionStore(data_dir=tmp_path, group_sessions_per_user=False)
    key = store.build_session_key(_msg("oc_room", "ou_a", "group"))
    assert key == "g:oc_room"


def test_get_or_create_new_session(tmp_path: Path):
    """新 key 创建空会话索引。"""
    store = FeishuSessionStore(data_dir=tmp_path)
    session = store.get_or_create("u:ou_a", "ou_a", "dm")
    assert isinstance(session, FeishuSession)
    assert session.cwd == ""
    assert session.session_id  # 非空 ID


def test_get_or_create_returns_existing(tmp_path: Path):
    """重复 get 返回同一会话索引。"""
    store = FeishuSessionStore(data_dir=tmp_path)
    s1 = store.get_or_create("u:ou_a", "ou_a", "dm")
    store.save(s1)
    s2 = store.get_or_create("u:ou_a", "ou_a", "dm")
    assert s2.session_id == s1.session_id


def test_index_json_has_no_messages_field(tmp_path: Path):
    """映射索引文件不再内嵌 messages（历史由 context.jsonl 权威承载）。"""
    store = FeishuSessionStore(data_dir=tmp_path)
    session = store.get_or_create("u:ou_a", "ou_a", "dm")
    store.save(session)
    raw = json.loads((tmp_path / "u_ou_a.json").read_text(encoding="utf-8"))
    assert "messages" not in raw
    assert raw["session_id"] == session.session_id


@pytest.mark.asyncio
async def test_load_messages_empty_and_roundtrip(tmp_path: Path):
    """load_messages 空会话返回空列表；写入 context.jsonl 后能读回。"""
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import session_dir_for

    store = FeishuSessionStore(data_dir=tmp_path)
    session = store.get_or_create("u:ou_a", "ou_a", "dm")
    cwd = str(tmp_path / "ws")
    session.cwd = cwd
    store.save(session)

    # 无历史
    assert (await store.load_messages(session)).messages == []

    # 写入两轮消息后读回
    sdir = session_dir_for(cwd, session.session_id)
    cp = CheckpointStore(sdir, session.session_id)
    await cp.append_checkpoint()
    await cp.append_message(ConversationMessage.from_user_text("hello"))
    await cp.append_message(ConversationMessage(role="assistant", content=[]))

    msgs = (await store.load_messages(session)).messages
    assert [m.text for m in msgs if m.role == "user"] == ["hello"]


@pytest.mark.asyncio
async def test_replace_messages_rewrites_history(tmp_path: Path):
    """replace_messages 用外部消息重建会话历史（/resume 注入）。"""
    store = FeishuSessionStore(data_dir=tmp_path)
    session = store.get_or_create("u:ou_a", "ou_a", "dm")
    session.cwd = str(tmp_path / "ws")
    store.save(session)

    external = [
        ConversationMessage.from_user_text("restored q"),
        ConversationMessage(role="assistant", content=[]),
    ]
    await store.replace_messages(session, external)

    msgs = (await store.load_messages(session)).messages
    assert [m.text for m in msgs if m.role == "user"] == ["restored q"]


def test_clear_removes_session(tmp_path: Path):
    """clear 后重建为全新会话（新 session_id）。"""
    store = FeishuSessionStore(data_dir=tmp_path)
    s1 = store.get_or_create("u:ou_a", "ou_a", "dm")
    store.save(s1)
    store.clear("u:ou_a")
    s2 = store.get_or_create("u:ou_a", "ou_a", "dm")
    assert s2.session_id != s1.session_id


def test_set_and_get_model(tmp_path: Path):
    """会话模型可单独设置与读取。"""
    store = FeishuSessionStore(data_dir=tmp_path)
    store.get_or_create("u:ou_a", "ou_a", "dm")
    store.set_model("u:ou_a", "gpt-4o")
    s2 = store.get_or_create("u:ou_a", "ou_a", "dm")
    assert s2.model == "gpt-4o"
