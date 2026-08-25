"""飞书会话存储
==============

管理 chat_id → 飞书独立会话的映射，支持拉取/保存本地 session。

映射文件存于 ~/.illusion/channels/feishu/sessions/<key>.json，仅保存
索引字段（session_id / user_id / chat_type / model / cwd）；对话历史
统一由实际会话目录的 context.jsonl 承载（与本地终端 session 同构），
可通过 /resume /detach 互通。公共行为见 ChannelSessionIndex /
BaseChannelSessionStore（channels.session_history）。

类说明：
    - FeishuSession: 飞书会话索引
    - FeishuSessionStore: 飞书会话存储管理器
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from illusion.channels.base import InboundMessage, SessionInfo
from illusion.channels.session_history import (
    BaseChannelSessionStore,
    ChannelSessionIndex,
)


@dataclass
class FeishuSession(ChannelSessionIndex):
    """飞书会话索引（字段定义见基类）"""


class FeishuSessionStore(BaseChannelSessionStore):
    """飞书会话存储管理器

    按 session key（DM 用 user_id，群组用 chat_id+user_id）隔离会话。
    """

    session_cls = FeishuSession
    channel_name = "feishu"

    def build_session_key(self, msg: InboundMessage) -> str:
        """根据入站消息构造会话隔离键

        移植自 hermes 的 build_session_key 逻辑：
        - 私聊：每用户独立
        - 群组：默认每用户每群独立，可配置为群共享

        Args:
            msg: 入站消息

        Returns:
            str: 会话隔离键
        """
        if msg.chat_type == "group":
            if self.group_sessions_per_user:
                return f"g:{msg.chat_id}:{msg.user_id}"  # 群内每用户独立
            return f"g:{msg.chat_id}"  # 群共享
        return f"u:{msg.user_id}"  # 私聊每用户独立

    def clear_by_session_id(self, session_id: str) -> bool:
        """按 session_id 删除会话索引文件（用于 /delete 命令跨渠道清理）

        遍历所有 JSON 文件，找到匹配的 session_id 并删除。

        Args:
            session_id: 要删除的会话 ID

        Returns:
            bool: 找到并删除返回 True
        """
        for path in self.data_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("session_id") == session_id:
                    path.unlink()
                    return True
            except (json.JSONDecodeError, ValueError, OSError):
                continue
        return False

    def list_active(self, limit: int = 5) -> list[SessionInfo]:
        """列出最近活跃的会话（按文件 mtime 排序）

        扫描 data_dir/*.json，反推 chat_id 和 user_id。
        文件名格式：u_<user_id>.json（私聊）或 g_<chat_id>_<user_id>.json（群聊）。

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
            name = path.stem  # 去 .json
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError, OSError):
                continue
            # 反推 chat_id 和 chat_type
            if name.startswith("u_"):
                # 私聊：u_<user_id>，chat_id 即 user_id
                chat_id = name[2:]
                chat_type = "dm"
            elif name.startswith("g_"):
                # 群聊：g_<chat_id>_<user_id>，飞书 chat_id 以 oc_ 开头，user_id 以 ou_ 开头
                remainder = name[2:]
                # 找到 oc_ 开头的部分作为 chat_id，再用 _ou_ 截断去掉 user_id
                if "oc_" in remainder:
                    idx = remainder.index("oc_")
                    chat_id = remainder[idx:]
                    if "_ou_" in chat_id:
                        chat_id = chat_id[:chat_id.index("_ou_")]
                else:
                    chat_id = remainder
                chat_type = "group"
            else:
                chat_id = name
                chat_type = "dm"
            mtime = path.stat().st_mtime
            last_active = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            result.append(SessionInfo(
                chat_id=chat_id,
                user_name=raw.get("user_id", "") or "",
                chat_type=chat_type,
                last_active=last_active,
            ))
        return result
