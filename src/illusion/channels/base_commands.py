"""通用斜杠命令处理器基类
================================

从 FeishuCommandHandler 提取的通用逻辑，飞书/微信等渠道共用。
支持命令：/help /clear /new /sessions /resume /detach /model

类说明：
    - BaseCommandHandler: 通用命令处理器基类
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from illusion.channels.base import InboundMessage
from illusion.channels.session_history import ChannelSessionStoreProtocol
from illusion.config.i18n import t

if TYPE_CHECKING:
    from illusion.channels.base import Channel


class BaseCommandHandler:
    """通用斜杠命令处理器

    在 agent 处理前拦截 / 命令，处理会话管理操作。
    子类只需提供 channel 和 session_store。

    Attributes:
        channel: 渠道实例（用于发消息）
        session_store: 会话存储
    """

    def __init__(self, channel: Channel, session_store: ChannelSessionStoreProtocol) -> None:
        """初始化

        Args:
            channel: 渠道实例
            session_store: 会话存储（各渠道 store 满足同一静态接口，
                接口语义漂移在类型检查期暴露而非运行时）
        """
        self.channel = channel
        self.session_store = session_store

    async def _reply(self, msg: InboundMessage, text: str) -> None:
        """发送命令回复，自动带 reply_to=msg.message_id

        QQ 群聊要求被动消息（必须有 msg_id），所有命令回复统一走此方法。

        Args:
            msg: 入站消息（取 message_id 作为 reply_to）
            text: 回复文本
        """
        await self.channel.send_text(msg.chat_id, text, reply_to=msg.message_id)

    async def try_handle(self, msg: InboundMessage) -> bool:
        """尝试处理斜杠命令

        所有回复都传 reply_to=msg.message_id，确保 QQ 群聊等要求
        被动消息（必须有 msg_id）的渠道能正常回复命令结果。

        Args:
            msg: 入站消息

        Returns:
            bool: 是斜杠命令并已处理返回 True，否则 False（交由 agent）
        """
        text = msg.text.strip()
        if not text.startswith("/"):
            return False

        key = self.session_store.build_session_key(msg)
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "help":
            await self._reply(msg, t("feishu_cmd_help"))
        elif cmd == "clear":
            self.session_store.clear(key)
            await self._reply(msg, t("feishu_cmd_cleared"))
        elif cmd == "new":
            self.session_store.clear(key)
            await self._reply(msg, t("cmd_new"))
        elif cmd == "stop":
            # /stop 在 ChannelRunner._handle_message 开头已被拦截处理，
            # 不会走到这里。此分支仅作为防御性兜底（如 PC 终端直接调用）
            await self._reply(msg, t("cmd_stop_no_task"))
        elif cmd == "model":
            await self._cmd_model(msg, key, args)
        elif cmd == "sessions":
            await self._cmd_sessions(msg)
        elif cmd == "resume":
            await self._cmd_resume(msg, key, args)
        elif cmd == "detach":
            await self._cmd_detach(msg, key)
        else:
            # 未知命令不吞掉：放行给 agent 作为普通用户消息处理（与 PC 端
            # handle_line 语义一致——注册表未命中的 / 前缀输入是真实用户消息）。
            # 历史上这里回复"未知命令"，导致渠道端 /xxx 消息无法到达 LLM。
            return False
        return True

    async def _cmd_model(self, msg: InboundMessage, key: str, args: str) -> None:
        """处理 /model 命令

        Args:
            msg: 入站消息
            key: 会话键
            args: 命令参数
        """
        if not args or args.lower() == "show":
            session = self.session_store.get_or_create(key, msg.user_id, msg.chat_type)
            model = session.model or "（默认）"
            await self._reply(msg, t("feishu_cmd_model_show", model=model))
            return
        parts = args.split(None, 1)
        if parts[0].lower() == "set" and len(parts) > 1:
            model_name = parts[1].strip()
            self.session_store.set_model(key, model_name)
            await self._reply(msg, t("feishu_cmd_model_set", model=model_name))
        else:
            await self._reply(msg, t("feishu_cmd_model_usage"))

    async def _cmd_sessions(self, msg: InboundMessage) -> None:
        """处理 /sessions 命令：列出本地终端会话

        Args:
            msg: 入站消息
        """
        from illusion.channels.session_history import resolve_channel_working_directory
        from illusion.services.session_storage import list_session_snapshots

        cwd = resolve_channel_working_directory(self.channel.name)
        snapshots = list_session_snapshots(cwd)
        if not snapshots:
            await self._reply(msg, t("feishu_cmd_no_sessions"))
            return
        lines = [t("feishu_cmd_sessions_title")]
        for i, s in enumerate(snapshots, 1):
            sid = s.get("session_id", "?")
            summary = s.get("summary", "?")[:50]
            count = s.get("message_count", 0)
            lines.append(f"  {i}. [{sid}] {summary} ({count} msgs)")
        await self._reply(msg, "\n".join(lines))

    async def _cmd_resume(self, msg: InboundMessage, key: str, args: str) -> None:
        """处理 /resume 命令：恢复本地会话

        Args:
            msg: 入站消息
            key: 会话键
            args: 命令参数（序号或 session_id）
        """
        from illusion.channels.session_history import resolve_channel_working_directory
        from illusion.services.checkpoint_store import CheckpointStore
        from illusion.services.session_storage import (
            list_session_snapshots,
            session_dir_for,
        )

        cwd = resolve_channel_working_directory(self.channel.name)
        snapshots = list_session_snapshots(cwd)
        if not snapshots:
            await self._reply(msg, t("feishu_cmd_no_sessions"))
            return
        chosen = None
        if args:
            try:
                idx = int(args) - 1
                if 0 <= idx < len(snapshots):
                    chosen = snapshots[idx]
            except ValueError:
                pass
            if chosen is None:
                chosen = next((s for s in snapshots if s.get("session_id") == args), None)
        if chosen is None:
            lines = [t("feishu_cmd_sessions_title")]
            for i, s in enumerate(snapshots, 1):
                sid = s.get("session_id", "?")
                summary = s.get("summary", "?")[:50]
                lines.append(f"  {i}. [{sid}] {summary}")
            await self._reply(msg, "\n".join(lines))
            return
        sid = chosen.get("session_id", "")
        session_dir = session_dir_for(cwd, sid)
        store = CheckpointStore(session_dir, sid)
        result = await store.restore()
        # 注入到渠道会话的 context.jsonl（单一权威存储）
        session = self.session_store.get_or_create(key, msg.user_id, msg.chat_type)
        await self.session_store.replace_messages(session, result.messages)
        await self._reply(msg, t("feishu_cmd_resumed", n=len(result.messages)))

    async def _cmd_detach(self, msg: InboundMessage, key: str) -> None:
        """处理 /detach 命令：保存为本地 session

        渠道对话历史已由 CheckpointStore 实时写入实际会话目录的
        context.jsonl，此处仅需补齐 meta.json / index.json 使其出现在
        本地会话列表中。

        Args:
            msg: 入站消息
            key: 会话键
        """
        import time

        from illusion.channels.session_history import resolve_channel_working_directory
        from illusion.services.session_storage import (
            session_dir_for,
            write_index_to,
            write_meta_to,
        )

        session = self.session_store.get_or_create(key, msg.user_id, msg.chat_type)
        if not session.session_id:
            # 全新索引（QQ 首轮 agent turn 前无 sid）：无历史可 detach
            await self._reply(msg, t("feishu_cmd_no_sessions"))
            return
        # 与 /sessions、/resume 的列举口径保持同源（渠道 working_directory
        # → 默认工作区），否则配置了独立目录的渠道 detach 出的会话永远
        # 无法被 /resume 看到
        cwd = session.cwd or resolve_channel_working_directory(self.channel.name)
        sid = session.session_id
        history = await self.session_store.load_messages(session)
        messages = history.messages
        session_dir = session_dir_for(cwd, sid)
        from illusion.config import load_settings
        model = session.model or load_settings().active_model_name
        write_meta_to(session_dir, sid, {
            "session_id": sid,
            "cwd": cwd,
            "model": model,
            "created_at": time.time(),
            "updated_at": time.time(),
            "summary": "",
            "message_count": len(messages),
            "turn_count": sum(1 for m in messages if m.role == "assistant"),
        })
        write_index_to(session_dir, sid)
        await self._reply(msg, t("feishu_cmd_detached", id=sid))
