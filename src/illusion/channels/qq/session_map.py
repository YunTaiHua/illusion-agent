"""QQ 渠道会话存储
==================

实现 QQSession 和 QQSessionStore，模式与 FeishuSessionStore 一致。
会话索引以 JSON 文件持久化到磁盘；对话历史统一由实际会话目录的
context.jsonl 承载（与本地终端 session 同构），可通过 /resume
/detach 互通。公共行为见 ChannelSessionIndex / BaseChannelSessionStore
（channels.session_history）。

类说明：
    - QQSession: QQ 会话索引
    - QQSessionStore: QQ 会话存储管理器
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from illusion.channels.base import InboundMessage, SessionInfo
from illusion.channels.session_history import (
    BaseChannelSessionStore,
    ChannelSessionIndex,
)

logger = logging.getLogger(__name__)


@dataclass
class QQSession(ChannelSessionIndex):
    """QQ 会话索引（字段定义见基类）"""


class QQSessionStore(BaseChannelSessionStore):
    """QQ 渠道会话存储管理器

    会话索引以 JSON 文件存储在 data_dir 下，key 作为文件名。

    Attributes:
        data_dir: 存储目录
        group_sessions_per_user: 群组会话是否按用户隔离
    """

    session_cls = QQSession
    channel_name = "qq"

    def build_session_key(self, msg: InboundMessage) -> str:
        """构建会话键

        私聊：chat_id
        群聊（隔离）：chat_id_user_id
        群聊（不隔离）：chat_id

        Args:
            msg: 入站消息

        Returns:
            str: 会话键
        """
        if msg.chat_type == "group" and self.group_sessions_per_user:
            return f"{msg.chat_id}_{msg.user_id}"
        return msg.chat_id

    def list_active(self, limit: int = 5) -> list[SessionInfo]:
        """列出最近活跃的 QQ 会话（按文件 mtime 排序）

        QQ 会话文件名为 chat_id（openid）或 chat_id_user_id（群组隔离）。
        chat_id 即文件名主体。

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
            # QQ: 文件名可能是 chat_id 或 chat_id_user_id
            # chat_id 即文件名（QQ openid 可能含下划线，保守用整个 name）
            chat_id = name
            chat_type = raw.get("chat_type", "dm")
            mtime = path.stat().st_mtime
            last_active = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            result.append(SessionInfo(
                chat_id=chat_id,
                user_name=raw.get("user_id", "") or "",
                chat_type=chat_type,
                last_active=last_active,
            ))
        return result
