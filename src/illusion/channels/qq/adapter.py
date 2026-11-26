"""QQ 渠道适配器
================

实现 QQChannel，对接 QQ 开放平台 Bot API v2，通过 WebSocket 网关收消息。

核心职责：
    - WebSocket 连接管理（心跳/重连）
    - 消息标准化（C2C/群聊 → InboundMessage）
    - 准入控制（自回显/机器人/群组策略）
    - 消息发送（文本/分片）
    - 打字状态指示

类说明：
    - QQChannel: QQ 渠道实现
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import aiohttp

from illusion.channels.base import Attachment, Channel, InboundMessage
from illusion.channels.qq.api import (
    DEDUP_MAX_SIZE,
    DEDUP_WINDOW_SECONDS,
    MAX_MESSAGE_LENGTH,
    ensure_token,
    send_c2c_message,
    send_group_message,
    split_text,
    strip_markdown,
)
from illusion.utils.aioqueue import Queue, QueueShutDown  # 支持关闭语义的异步队列

if TYPE_CHECKING:
    from illusion.channels.config import QQChannelConfig
    from illusion.config.settings import Settings

logger = logging.getLogger(__name__)

# QQ CDN 可信域名后缀（用于附件下载时判断是否可附带 bot token）
_QQ_CDN_HOST_SUFFIXES = (".qq.com", ".qq.com.cn")


def _is_qq_cdn_url(url: str) -> bool:
    """判断 URL 是否指向 QQ 可信 CDN 域名。

    用于在附件下载时决定是否携带 Authorization 头，防止 bot token
    通过恶意构造的 attachment.url 泄露到第三方 host。
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    return any(host.endswith(suffix) for suffix in _QQ_CDN_HOST_SUFFIXES)


class QQChannel(Channel):
    """QQ 渠道实现（QQ Bot API v2）

    通过 WebSocket 网关接收消息，支持 C2C 私聊和群聊。

    Attributes:
        name: 渠道名 "qq"
    """

    name = "qq"

    def __init__(self, config: QQChannelConfig, settings: Settings) -> None:
        """初始化 QQ 渠道

        Args:
            config: QQ 配置
            settings: 主设置
        """
        super().__init__(config, settings)
        self._queue: Queue[InboundMessage] = Queue()  # 入站队列（支持 shutdown 哨兵唤醒 listen）
        self._stop_event = asyncio.Event()
        self._ws_client: Any = None  # QQWSClient 实例
        self._session: Any = None  # aiohttp.ClientSession（发送用）
        # 注意：不再缓存 self._token。每次 API 调用都通过 _get_token()
        # 获取最新 token（内部有 _token_cache + 过期检查，不会重复请求）。
        # 原因：WS 重连时 ws_client 会刷新 _token_cache，但旧的 self._token
        # 不会更新，导致重连后发送消息用旧 token 报 "token not exist or expire"。

        # bot 自身 openid（用于自回显检测）
        self._bot_openid: str = ""

        # 消息去重
        self._seen_msg_ids: dict[str, float] = {}

        # 打字状态防抖
        self._last_typing_time: float = 0.0
        self._typing_debounce: float = 50.0
        # 最近一条入站消息 ID（用于打字状态关联）
        self._last_msg_id: dict[str, str] = {}

        # chat_type 缓存（用于判断 C2C vs 群聊）
        self._chat_type_cache: dict[str, str] = {}

        # markdown 支持（从配置读取，默认启用）
        self._markdown_support: bool = getattr(config, "markdown_support", True)

    async def _get_token(self) -> str:
        """获取有效的 QQ Bot access token

        每次调用都经过 ensure_token()，内部有缓存+过期检查，
        不会重复请求。WS 重连后 _token_cache 已刷新，此方法自动
        返回新 token，无需手动同步。

        Returns:
            str: 有效的 access token
        """
        if not self._session:
            raise RuntimeError("QQ adapter 未连接，无法获取 token")
        return await ensure_token(
            self._session, self.config.app_id, self.config.client_secret,
        )

    async def connect(self) -> None:
        """建立 WS 连接和 HTTP session

        失败时清理已创建的 session/ws_client，避免 Unclosed client session 警告。
        """
        import aiohttp

        from illusion.channels.qq.ws_client import QQWSClient

        # 先清理旧资源（_supervise 重启时可能复用 adapter 实例）
        await self._cleanup_resources()

        self._session = aiohttp.ClientSession(trust_env=True)
        self._ws_client = QQWSClient(
            app_id=self.config.app_id,
            client_secret=self.config.client_secret,
            on_event=self._on_ws_event,
        )
        try:
            await self._ws_client.connect()
        except Exception:
            # connect 失败时清理已创建的 session/ws_client，避免泄漏
            await self._cleanup_resources()
            raise

        # 获取 bot 自身 openid（用于自回显检测）
        await self._hydrate_bot_openid()

    async def _cleanup_resources(self) -> None:
        """清理 ws_client/session 资源（connect 失败或 shutdown 时调用）"""
        try:
            if self._ws_client is not None:
                await self._ws_client.close()
        except Exception:
            logger.debug("清理 QQ WS 客户端失败", exc_info=True)
        try:
            if self._session is not None and not self._session.closed:
                await self._session.close()
        except Exception:
            logger.debug("清理 QQ HTTP session 失败", exc_info=True)
        self._ws_client = None
        self._session = None

    async def _hydrate_bot_openid(self) -> None:
        """从 QQ API 获取 bot 自身 openid

        调用 /users/@me 接口获取 bot 的 openid，
        用于自回显检测。
        """
        if not self._session:
            return
        try:
            token = await self._get_token()
            from illusion.channels.qq.api import API_BASE
            headers = {"Authorization": f"QQBot {token}"}
            async with self._session.get(f"{API_BASE}/users/@me", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._bot_openid = data.get("id", "") or ""
                    if self._bot_openid:
                        logger.info("QQ bot openid: %s", self._bot_openid)
        except (
            ImportError, aiohttp.ClientError, ValueError,
            KeyError, AttributeError, TypeError,
        ) as exc:
            logger.warning("获取 QQ bot openid 失败: %s", exc)

    def get_bot_id(self) -> str:
        """返回 QQ bot 自身 openid

        Returns:
            str: bot 的 openid
        """
        return self._bot_openid

    async def _on_ws_event(self, event_type: str, data: dict[str, Any]) -> None:
        """WS 事件回调，标准化后放入队列

        Args:
            event_type: 事件类型（如 C2C_MESSAGE_CREATE）
            data: 事件数据
        """
        msg: InboundMessage | None = None

        if event_type == "C2C_MESSAGE_CREATE":
            msg = self._normalize_c2c(data)
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            logger.debug("QQ 群聊原始事件: id=%s, content=%s, group=%s",
                         data.get("id"), repr(data.get("content")),
                         data.get("group_openid"))
            msg = self._normalize_group(data)

        if msg is None:
            return

        if self._is_duplicate(msg.message_id):
            logger.debug("QQ 重复消息，跳过: %s", msg.message_id)
            return

        if self._admit(msg):
            # 缓存最近一条入站消息 ID（用于打字状态关联）
            if msg.message_id and msg.chat_id:
                self._last_msg_id[msg.chat_id] = msg.message_id

            # 空 @消息（只 @机器人没有文字）→ 回复帮助提示，不传给 LLM
            if not msg.text.strip() and msg.chat_type == "group":
                logger.info("QQ 空 @消息，回复帮助提示: user=%s", msg.user_id)
                from illusion.config.i18n import t as _t
                await self.send_text(msg.chat_id, _t("feishu_cmd_help"),
                                     reply_to=msg.message_id)
                return

            # 附件只存元数据，LLM 通过 receive_media 工具按需下载
            # （与飞书一致，避免入站下载失败导致消息无响应）
            logger.info("QQ 消息已准入: user=%s text=%s attachments=%d",
                        msg.user_id, msg.text[:30], len(msg.attachments))
            self._queue.put_nowait(msg)
        else:
            logger.info("QQ 消息被拒绝: user=%s is_bot=%s", msg.user_id, msg.is_bot)

    def _normalize_c2c(self, raw: dict[str, Any]) -> InboundMessage | None:
        """标准化 C2C 私聊消息

        Args:
            raw: QQ API 原始消息数据

        Returns:
            InboundMessage | None: 标准化消息，无法解析返回 None
        """
        try:
            msg_id = str(raw.get("id", ""))
            content = str(raw.get("content", "")).strip()
            _author = raw.get("author")
            author = _author if isinstance(_author, dict) else {}
            # QQ C2C 事件 author 字段是 user_openid（不是 id）
            user_id = str(author.get("user_openid") or author.get("id") or "")
            user_name = str(author.get("username") or "")

            if not msg_id or not user_id:
                return None

            # 缓存 chat_type
            self._chat_type_cache[user_id] = "dm"

            return InboundMessage(
                text=content,
                chat_id=user_id,  # C2C 用 user_id 作为 chat_id
                chat_type="dm",
                user_id=user_id,
                user_name=user_name,
                message_id=msg_id,
                attachments=self._parse_attachments(raw),
            )
        except (KeyError, AttributeError, TypeError, ValueError, IndexError) as exc:
            logger.warning("标准化 QQ C2C 消息失败: %s", exc)
            return None

    def _normalize_group(self, raw: dict[str, Any]) -> InboundMessage | None:
        """标准化群聊消息

        群聊消息需要去除 @机器人 的文本前缀。

        Args:
            raw: QQ API 原始消息数据

        Returns:
            InboundMessage | None: 标准化消息，无法解析返回 None
        """
        try:
            msg_id = str(raw.get("id", ""))
            content = str(raw.get("content", "")).strip()
            _author = raw.get("author")
            author = _author if isinstance(_author, dict) else {}
            # QQ 群聊事件 author 字段是 member_openid（不是 id）
            user_id = str(author.get("member_openid") or author.get("id") or "")
            user_name = str(author.get("username") or "")
            group_openid = str(raw.get("group_openid") or "")

            if not msg_id or not user_id or not group_openid:
                logger.warning("QQ 群聊消息缺少必填字段: msg_id=%s user_id=%s group=%s",
                               repr(msg_id), repr(user_id), repr(group_openid))
                return None

            # 去除 @mention 前缀
            mentions = raw.get("mentions", [])
            for mention in mentions:
                mention_id = str(mention.get("id", ""))
                mention_name = str(mention.get("username", ""))
                # QQ @mention 格式: <@!bot_id> 或 @username
                for prefix in [f"<@!{mention_id}>", f"@{mention_name}"]:
                    if content.startswith(prefix):
                        content = content[len(prefix):].strip()
                        break

            # 缓存 chat_type
            self._chat_type_cache[group_openid] = "group"

            return InboundMessage(
                text=content,
                chat_id=group_openid,
                chat_type="group",
                user_id=user_id,
                user_name=user_name,
                message_id=msg_id,
                attachments=self._parse_attachments(raw),
            )
        except (KeyError, AttributeError, TypeError, ValueError, IndexError) as exc:
            logger.warning("标准化 QQ 群聊消息失败: %s", exc)
            return None

    def _parse_attachments(self, raw: dict[str, Any]) -> list[Attachment]:
        """解析 QQ 消息的 attachments 字段

        QQ Bot API v2 附件结构（每项）：
            - content_type: MIME 类型（image/png、application/pdf 等）
            - filename: 文件名
            - size: 字节数
            - id: 附件 ID
            - url: 下载 URL（临时，可能需要鉴权）

        Args:
            raw: 原始消息数据

        Returns:
            list[Attachment]: 标准化附件列表
        """
        attachments: list[Attachment] = []
        raw_attachments = raw.get("attachments")
        if not isinstance(raw_attachments, list):
            return attachments

        for idx, att in enumerate(raw_attachments):
            if not isinstance(att, dict):
                continue
            content_type = str(att.get("content_type", ""))
            if content_type.startswith("image/"):
                media_type = "image"
            elif content_type.startswith("video/"):
                media_type = "video"
            elif content_type.startswith("audio/"):
                media_type = "audio"
            else:
                media_type = "file"

            try:
                size_val = int(att.get("size", 0))
            except (TypeError, ValueError):
                size_val = 0

            attachments.append(Attachment(
                id=str(att.get("id", idx + 1)),
                media_type=media_type,
                filename=str(att.get("filename", f"attachment_{idx + 1}")),
                size=size_val,
                file_key=str(att.get("file_info", "")),
                download_url=str(att.get("url", "")),
            ))
        return attachments

    def _admit(self, msg: InboundMessage) -> bool:
        """准入控制：决定消息是否进入 agent

        准入规则：
        1. 自回显 → 拒绝
        2. 机器人消息（allow_bots=False）→ 拒绝
        3. 私聊 → 放行
        4. 群聊 → 检查群组策略

        Args:
            msg: 标准化消息

        Returns:
            bool: 放行返回 True
        """
        # 1. 自回显
        if self._bot_openid and msg.user_id == self._bot_openid:
            return False

        # 2. 机器人策略
        if msg.is_bot and not self.config.allow_bots:
            return False

        # 3. 私聊直接放行
        if msg.chat_type == "dm":
            return True

        # 4. 群聊策略
        policy = self.config.group_policy

        # 管理员永远放行
        if msg.user_id in policy.admin_list:
            return True

        if policy.mode == "disabled":
            return False

        if policy.mode == "allowlist":
            return msg.chat_id in policy.allowlist

        if policy.mode == "blacklist":
            return msg.chat_id not in policy.blacklist

        # mode == "open"
        return True

    def _is_duplicate(self, msg_id: str) -> bool:
        """消息去重（msg_id + 5 分钟 TTL）

        Args:
            msg_id: 消息 ID

        Returns:
            bool: 重复返回 True
        """
        if not msg_id:
            return False
        now = time.monotonic()
        # 清理过期记录
        expired = [k for k, v in self._seen_msg_ids.items() if now - v > DEDUP_WINDOW_SECONDS]
        for k in expired:
            del self._seen_msg_ids[k]
        # 超过容量限制时清理最旧的
        if len(self._seen_msg_ids) >= DEDUP_MAX_SIZE:
            oldest = min(self._seen_msg_ids, key=lambda k: self._seen_msg_ids[k])
            del self._seen_msg_ids[oldest]
        if msg_id in self._seen_msg_ids:
            return True
        self._seen_msg_ids[msg_id] = now
        return False

    async def listen(self) -> AsyncIterator[InboundMessage]:
        """异步迭代器，不断 yield 入站消息

        _listen_loop 自己永远循环处理重连，不依赖外部 _supervise 重启。
        listen() 只负责从队列取消息 yield。

        阻塞在 queue.get() 等待消息，shutdown() 调用 queue.shutdown() 投递哨兵
        唤醒 getter 并抛 QueueShutDown，本方法捕获后退出循环。
        """
        logger.info("QQ 监听已启动")
        while True:
            try:
                msg = await self._queue.get()
            except QueueShutDown:
                break
            yield msg

    def _format_message(self, content: str) -> str:
        """格式化消息内容

        markdown_support=True 时原样传递（QQ 自行渲染），
        False 时剥离 markdown 格式为纯文本。

        Args:
            content: 原始消息内容

        Returns:
            str: 格式化后的消息
        """
        if self._markdown_support:
            return content
        return strip_markdown(content)

    async def send_text(self, chat_id: str, text: str, *, reply_to: str = "") -> str:
        """发送文本消息，超长自动分片（代码块感知）

        markdown_support=True 时使用 markdown 信封（msg_type=2），
        分片时自动处理代码块围栏的闭合与重开。

        Args:
            chat_id: 目标会话（openid 或 group_openid）
            text: 文本内容
            reply_to: 引用的消息 ID（可选）

        Returns:
            str: 发送的消息 ID（QQ API 不返回则为空串）
        """
        if not self._session:
            return ""

        # 确保 token 有效（每次调用，重连后自动获取新 token）
        token = await self._get_token()

        # 格式化（markdown 原样传递 or 剥离为纯文本）
        formatted = self._format_message(text)
        chunks = split_text(formatted, MAX_MESSAGE_LENGTH)
        logger.info("QQ 发送 %d 个分片到 %s (markdown=%s, reply_to=%s)",
                     len(chunks), chat_id, self._markdown_support, repr(reply_to))

        is_group = self._chat_type_cache.get(chat_id) == "group"

        # QQ 群聊 API 要求 msg_id（被动消息），无 msg_id 时跳过（主动消息无权限）
        if is_group and not reply_to:
            logger.warning("QQ 群聊发送跳过：缺少 msg_id（主动消息无权限）")
            return ""

        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(1.5)  # 分片间隔，防限流

            # 尝试发送，markdown 失败时降级为纯文本
            use_markdown = self._markdown_support
            for attempt in range(3):
                try:
                    if is_group:
                        await send_group_message(
                            self._session, token, chat_id, chunk,
                            msg_id=reply_to,
                            markdown=use_markdown,
                        )
                    else:
                        await send_c2c_message(
                            self._session, token, chat_id, chunk,
                            msg_id=reply_to or "",
                            markdown=use_markdown,
                        )
                    break
                except Exception as exc:
                    # markdown 权限/格式错误时降级为纯文本重试
                    if use_markdown and attempt == 0:
                        logger.warning("QQ markdown 发送失败，降级为纯文本: %s", exc)
                        use_markdown = False
                        continue
                    logger.warning("QQ 发送失败 (attempt %d/3): %s", attempt + 1, exc)
                    if attempt < 2:
                        await asyncio.sleep(2)
                    else:
                        raise

        return ""

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """编辑消息——QQ 不支持编辑，空操作"""

    async def send_file(self, chat_id: str, file_path: str, *, reply_to: str = "") -> None:
        """发送文件（三步分片上传）

        Args:
            chat_id: 目标会话
            file_path: 本地文件路径
            reply_to: 引用的消息 ID（QQ 群聊被动消息需要；当前 upload_file 未使用，保留以兼容基类签名）
        """
        try:
            from illusion.channels.qq.api import upload_file
            is_group = self._chat_type_cache.get(chat_id) == "group"
            token = await self._get_token()
            await upload_file(
                self._session, token, chat_id, file_path,
                is_group=is_group,
            )
        except (ImportError, aiohttp.ClientError, RuntimeError, OSError, ValueError) as exc:
            logger.warning("QQ 文件发送失败: %s", exc)

    async def send_image(
        self, chat_id: str, image_path: str, *, caption: str = "", reply_to: str = ""
    ) -> str:
        """发送图片到 QQ 会话

        QQ 富媒体消息：先 upload_file 上传图片（file_type=1）获取 file_info，
        再 send_media_message 发送 msg_type=7 富媒体消息。
        QQ 富媒体消息不支持单独 caption，caption 作为后续文本消息发送。

        群聊场景必须传 reply_to（被动消息 msg_id），否则 API 拒绝主动消息。

        Args:
            chat_id: 目标会话（openid 或 group_openid）
            image_path: 本地图片路径
            caption: 可选附注文字
            reply_to: 引用的消息 ID（群聊被动消息必须）

        Returns:
            str: 新消息 ID（API 不返回则为空串）

        Raises:
            RuntimeError: session 未初始化、群聊缺少 reply_to、上传/发送失败
        """
        if not self._session:
            raise RuntimeError("QQ session 未初始化，无法发送图片")

        from illusion.channels.qq.api import (
            MEDIA_TYPE_IMAGE,
            send_media_message,
            upload_file,
        )

        token = await self._get_token()

        is_group = self._chat_type_cache.get(chat_id) == "group"

        # QQ 群聊 API 要求 msg_id（被动消息），无 msg_id 时主动消息无权限
        if is_group and not reply_to:
            raise RuntimeError(
                "QQ 群聊发送图片需要 reply_to（被动消息 msg_id），主动消息无权限"
            )

        upload_resp = await upload_file(
            self._session, token, chat_id, image_path,
            is_group=is_group,
            file_type=MEDIA_TYPE_IMAGE,
        )
        file_info = str(upload_resp.get("file_info", ""))
        if not file_info:
            raise RuntimeError(f"QQ 图片上传未返回 file_info: {upload_resp}")

        msg_id = await send_media_message(
            self._session, token, chat_id, file_info,
            is_group=is_group, msg_id=reply_to,
        )

        if caption:
            await self.send_text(chat_id, caption, reply_to=reply_to)

        return msg_id

    async def send_document(
        self, chat_id: str, file_path: str, *, caption: str = "", reply_to: str = ""
    ) -> str:
        """发送文件到 QQ 会话

        QQ 富媒体消息：先 upload_file 上传文件（file_type=4）获取 file_info，
        再 send_media_message 发送 msg_type=7 富媒体消息。
        caption 作为后续文本消息发送。

        群聊场景必须传 reply_to（被动消息 msg_id），否则 API 拒绝主动消息。

        Args:
            chat_id: 目标会话（openid 或 group_openid）
            file_path: 本地文件路径
            caption: 可选附注文字
            reply_to: 引用的消息 ID（群聊被动消息必须）

        Returns:
            str: 新消息 ID（API 不返回则为空串）

        Raises:
            RuntimeError: session 未初始化、群聊缺少 reply_to、上传/发送失败
        """
        if not self._session:
            raise RuntimeError("QQ session 未初始化，无法发送文件")

        from illusion.channels.qq.api import (
            MEDIA_TYPE_FILE,
            send_media_message,
            upload_file,
        )

        token = await self._get_token()

        is_group = self._chat_type_cache.get(chat_id) == "group"

        # QQ 群聊 API 要求 msg_id（被动消息），无 msg_id 时主动消息无权限
        if is_group and not reply_to:
            raise RuntimeError(
                "QQ 群聊发送文件需要 reply_to（被动消息 msg_id），主动消息无权限"
            )

        upload_resp = await upload_file(
            self._session, token, chat_id, file_path,
            is_group=is_group,
            file_type=MEDIA_TYPE_FILE,
        )
        file_info = str(upload_resp.get("file_info", ""))
        if not file_info:
            raise RuntimeError(f"QQ 文件上传未返回 file_info: {upload_resp}")

        msg_id = await send_media_message(
            self._session, token, chat_id, file_info,
            is_group=is_group, msg_id=reply_to,
        )

        if caption:
            await self.send_text(chat_id, caption, reply_to=reply_to)

        return msg_id

    async def download_attachment(
        self, attachment: Attachment, save_path: str
    ) -> str:
        """下载 QQ 附件到本地

        优先使用 attachment.download_url（QQ 入站附件的临时 URL）直接下载；
        若不可用，回退到通过 file_key (file_info) 调用 QQ 文件下载 API。
        两者均不可用时抛 NotImplementedError。

        Args:
            attachment: 附件对象（来自 InboundMessage.attachments）
            save_path: 本地保存路径

        Returns:
            str: 实际保存路径

        Raises:
            NotImplementedError: 无可用下载方式或下载失败
        """
        from pathlib import Path

        save_path_obj = Path(save_path)
        save_path_obj.parent.mkdir(parents=True, exist_ok=True)

        if not self._session:
            raise NotImplementedError("QQ session 未初始化，无法下载附件")

        token = await self._get_token()

        # 优先使用 download_url（QQ 入站附件的临时 URL）
        url_error: Exception | None = None
        if attachment.download_url:
            try:
                # QQ 返回的 URL 常是协议相对格式（//xxx），需补 https: 前缀
                url = attachment.download_url
                if url.startswith("//"):
                    url = f"https:{url}"
                # 仅对 QQ 可信 CDN 域名附带 Authorization，避免 bot token 泄露到第三方 host
                headers: dict[str, str] = {}
                if _is_qq_cdn_url(url):
                    headers["Authorization"] = f"QQBot {token}"
                else:
                    logger.warning(
                        "QQ 附件 URL 非 QQ CDN 域名，不带 Authorization: %s",
                        url[:80],
                    )
                async with self._session.get(url, headers=headers) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                # 下载的附件可能很大，用 to_thread 写盘避免阻塞事件循环
                await asyncio.to_thread(save_path_obj.write_bytes, data)
                logger.info("QQ 附件下载成功: %s → %s (%d bytes)",
                            attachment.filename, save_path_obj, len(data))
                return str(save_path_obj)
            except (aiohttp.ClientError, OSError, ValueError, RuntimeError) as exc:
                url_error = exc
                logger.warning("QQ 附件 URL 下载失败: %s (url=%s)",
                               exc, attachment.download_url[:80])

        # 回退：通过 file_key (file_info) 调用文件下载 API
        if attachment.file_key:
            from illusion.channels.qq.api import download_file

            # download_attachment 签名不含 chat_id，使用 bot_openid 作为 target_id（C2C 场景）
            target_id = self._bot_openid or ""
            if not target_id:
                raise NotImplementedError(
                    "QQ 附件 file_info 下载需要 bot_openid，但未获取到"
                )
            try:
                data = await download_file(
                    self._session, token, target_id,
                    attachment.file_key,
                    is_group=False,
                )
                # 下载的附件可能很大，用 to_thread 写盘避免阻塞事件循环
                await asyncio.to_thread(save_path_obj.write_bytes, data)
                return str(save_path_obj)
            except Exception as exc:
                logger.warning("QQ 附件 file_info 下载失败: %s", exc)
                raise NotImplementedError(
                    f"QQ 附件下载失败: {exc}"
                ) from exc

        # 两种方式都不可用或都失败，给出详细错误信息
        if url_error:
            raise NotImplementedError(
                f"QQ 附件下载失败（url={attachment.download_url[:60]}...）: {url_error}"
            ) from url_error
        raise NotImplementedError(
            f"QQ 附件缺少 download_url 和 file_key，无法下载: {attachment.filename}"
        )

    async def start_typing(self, chat_id: str) -> None:
        """开始打字状态指示（C2C only，50s 防抖）

        仅私聊发送打字状态，群聊跳过。
        需要缓存的入站消息 ID（_last_msg_id），无则跳过。

        Args:
            chat_id: 目标会话
        """
        # 仅 C2C 发送打字状态
        if self._chat_type_cache.get(chat_id) != "dm":
            return

        # 防抖：50s 内不重复发送
        now = time.monotonic()
        if now - self._last_typing_time < self._typing_debounce:
            return
        self._last_typing_time = now

        # 获取缓存的入站消息 ID
        msg_id = self._last_msg_id.get(chat_id)
        if not msg_id:
            return

        try:
            from illusion.channels.qq.api import send_typing
            token = await self._get_token()
            await send_typing(self._session, token, chat_id, msg_id)
        except (ImportError, aiohttp.ClientError, RuntimeError, ValueError) as exc:
            logger.debug("QQ 打字状态发送失败: %s", exc)

    async def stop_typing(self, chat_id: str) -> None:
        """停止打字状态指示——QQ API 无停止接口，空操作"""

    async def shutdown(self) -> None:
        """关闭渠道"""
        self._stop_event.set()
        self._queue.shutdown()  # 投递哨兵唤醒 listen 协程
        await self._cleanup_resources()
