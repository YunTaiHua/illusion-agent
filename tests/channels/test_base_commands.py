"""通用斜杠命令基类测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from illusion.channels.base import InboundMessage
from illusion.channels.base_commands import BaseCommandHandler
from illusion.channels.feishu.session_map import FeishuSessionStore


def _msg(text: str, chat_id: str = "ou_a") -> InboundMessage:
    """构造测试入站消息。"""
    return InboundMessage(
        text=text, chat_id=chat_id, chat_type="dm",
        user_id=chat_id, user_name="tester", message_id="om_1",
    )


@pytest.fixture
def handler(tmp_path: Path):
    """用 BaseCommandHandler + FeishuSessionStore 构造测试 handler。"""
    channel = AsyncMock()
    store = FeishuSessionStore(data_dir=tmp_path)
    return BaseCommandHandler(channel=channel, session_store=store)


@pytest.mark.asyncio
async def test_help_command(handler):
    """/help 返回命令列表。"""
    result = await handler.try_handle(_msg("/help"))
    assert result is True
    handler.channel.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_clear_command(handler):
    """/clear 清空会话。"""
    s = handler.session_store.get_or_create("u:ou_a", "ou_a", "dm")
    handler.session_store.save(s)
    result = await handler.try_handle(_msg("/clear"))
    assert result is True
    s2 = handler.session_store.get_or_create("u:ou_a", "ou_a", "dm")
    assert s2.session_id != s.session_id


@pytest.mark.asyncio
async def test_non_command_returns_false(handler):
    """非斜杠命令返回 False。"""
    result = await handler.try_handle(_msg("你好"))
    assert result is False
    handler.channel.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_command_passes_to_agent(handler):
    """未知斜杠命令放行给 agent（不回复、不吞掉）。

    与 PC 端 handle_line 语义一致：注册表未命中的 / 前缀输入是真实用户
    消息。历史上这里回复"未知命令"，导致渠道端 /xxx 消息无法到达 LLM。
    """
    result = await handler.try_handle(_msg("/foobar"))
    assert result is False
    handler.channel.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_slash_prefixed_chinese_message_passes_to_agent(handler):
    """以 / 开头的真实用户消息（如"/feedback完全删掉…"）放行给 agent。"""
    result = await handler.try_handle(_msg("/feedback完全删掉，帮我看看"))
    assert result is False
    handler.channel.send_text.assert_not_called()
