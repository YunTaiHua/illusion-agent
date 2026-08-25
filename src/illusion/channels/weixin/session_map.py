"""微信会话存储
==============

管理 user_id → 微信独立会话的映射。
结构与 FeishuSessionStore 相同，独立目录存储。

映射文件仅保存索引字段（session_id / user_id / chat_type / model /
cwd），对话历史统一由实际会话目录的 context.jsonl 承载
（与本地终端 session 同构），可通过 /resume /detach 互通。
公共行为见 ChannelSessionIndex / BaseChannelSessionStore
（channels.session_history）。

类说明：
    - WeixinSession: 微信会话索引
    - WeixinSessionStore: 微信会话存储管理器
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from illusion.channels.base import InboundMessage, SessionInfo
from illusion.channels.session_history import (
    BaseChannelSessionStore,
    ChannelSessionIndex,
)


@dataclass
class WeixinSession(ChannelSessionIndex):
    """微信会话索引（字段定义见基类）"""


class WeixinSessionStore(BaseChannelSessionStore):
    """微信会话索引管理器

    微信 bot 只能私聊，按 user_id 隔离会话。
    """

    session_cls = WeixinSession
    channel_name = "weixin"

    def __init__(self, data_dir: Path) -> None:
        """初始化（微信只私聊，不使用群组隔离开关）

        Args:
            data_dir: 会话数据目录
        """
        super().__init__(data_dir)

    def build_session_key(self, msg: InboundMessage) -> str:
        """构建会话隔离键

        微信 bot 只能私聊，按 user_id 隔离。

        Args:
            msg: 入站消息

        Returns:
            str: 会话隔离键
        """
        return f"u:{msg.user_id}"

    def list_active(self, limit: int = 5) -> list[SessionInfo]:
        """列出最近活跃的微信会话（按文件 mtime 排序）

        微信会话文件名为 u_<wxid>（私聊，无群聊）。chat_id 即 user_id。

        Args:
            limit: 最多返回多少条

        Returns:
            list[SessionInfo]: 最近活跃会话，最新在前
        """
        files = sorted(
            self.data_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        result: list[SessionInfo] = []
        for path in files:
            name = path.stem
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError, OSError):
                continue
            # 微信: u_<wxid>，chat_id = wxid
            chat_id = name.removeprefix("u_")
            mtime = path.stat().st_mtime
            last_active = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            result.append(SessionInfo(
                chat_id=chat_id,
                user_name=raw.get("user_id", "") or chat_id,
                chat_type="dm",
                last_active=last_active,
            ))
        return result
