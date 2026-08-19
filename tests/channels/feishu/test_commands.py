"""飞书侧斜杠命令测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from illusion.channels.base import InboundMessage
from illusion.channels.feishu.commands import FeishuCommandHandler
from illusion.channels.feishu.session_map import FeishuSessionStore


def _msg(text: str, chat_id: str = "ou_a", chat_type: str = "dm") -> InboundMessage:
    """构造测试入站消息。"""
    return InboundMessage(
        text=text, chat_id=chat_id, chat_type=chat_type,
        user_id=chat_id, user_name="tester", message_id="om_1",
    )


@pytest.fixture
def handler(tmp_path: Path):
    """构造命令处理器，channel 为 AsyncMock。"""
    channel = AsyncMock()
    store = FeishuSessionStore(data_dir=tmp_path)
    return FeishuCommandHandler(channel=channel, session_store=store)


@pytest.mark.asyncio
async def test_help_command(handler):
    """/help 返回命令列表。"""
    msg = _msg("/help")
    result = await handler.try_handle(msg)
    assert result is True  # 已处理
    handler.channel.send_text.assert_called_once()
    sent = handler.channel.send_text.call_args[0][1]
    assert "/help" in sent and "/resume" in sent


@pytest.mark.asyncio
async def test_clear_command(handler):
    """/clear 清空会话。"""
    # 先存点历史
    s = handler.session_store.get_or_create("u:ou_a", "ou_a", "dm")
    handler.session_store.save(s, [{"role": "user", "content": "old"}])
    msg = _msg("/clear")
    result = await handler.try_handle(msg)
    assert result is True
    handler.channel.send_text.assert_called_once()
    # 会话历史已清空
    s2 = handler.session_store.get_or_create("u:ou_a", "ou_a", "dm")
    assert s2.messages == []


@pytest.mark.asyncio
async def test_new_command(handler):
    """/new 开启新会话。"""
    msg = _msg("/new")
    result = await handler.try_handle(msg)
    assert result is True
    handler.channel.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_model_show(handler):
    """/model show 显示当前模型。"""
    handler.session_store.set_model("u:ou_a", "gpt-4o")
    msg = _msg("/model show")
    result = await handler.try_handle(msg)
    assert result is True
    sent = handler.channel.send_text.call_args[0][1]
    assert "gpt-4o" in sent


@pytest.mark.asyncio
async def test_model_set(handler):
    """/model set 切换模型。"""
    msg = _msg("/model set claude-sonnet-4")
    result = await handler.try_handle(msg)
    assert result is True
    s = handler.session_store.get_or_create("u:ou_a", "ou_a", "dm")
    assert s.model == "claude-sonnet-4"


@pytest.mark.asyncio
async def test_non_command_returns_false(handler):
    """非斜杠命令返回 False（交由 agent 处理）。"""
    msg = _msg("你好")
    result = await handler.try_handle(msg)
    assert result is False
    handler.channel.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_command_passes_to_agent(handler):
    """未知斜杠命令放行给 agent（不回复、不吞掉）。

    与 PC 端 handle_line 语义一致：注册表未命中的 / 前缀输入是真实用户
    消息。历史上这里回复"未知命令"，导致渠道端 /xxx 消息无法到达 LLM。
    """
    msg = _msg("/foobar")
    result = await handler.try_handle(msg)
    assert result is False
    handler.channel.send_text.assert_not_called()
