"""微信渠道适配器
================

实现 WeixinChannel，对接腾讯 iLink Bot API，通过 HTTP 长轮询收消息。

核心职责：
    - 长轮询拉取消息（getupdates）
    - 准入控制（自回显/机器人/群消息丢弃）
    - context_token 管理（iLink 硬约束：每 peer 回复必须回传）
    - 打字状态（sendtyping）
    - 文本消息发送（sendmessage + 分片）
    - 媒体收发（AES-128-ECB 加密 CDN 协议）

媒体处理：
    - 入站：从 item_list 解析 image/video/file/voice 项，提取 encrypt_query_param
      和 aes_key，存为 Attachment（只存元数据，LLM 调 receive_media 工具按需下载）
    - 出站：send_image/send_document/send_video 通过 _send_file 完成
      AES 加密 → getuploadurl → CDN 上传 → sendmessage with media item
    - 下载：download_attachment 从 CDN 下载密文 → AES 解密 → 保存到指定路径

类说明：
    - WeixinChannel: 微信渠道实现
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from illusion.channels.base import Attachment, Channel, InboundMessage
from illusion.utils.aioqueue import Queue  # 支持关闭语义的异步队列
from illusion.utils.atomic_write import atomic_write_text

if TYPE_CHECKING:
    from illusion.channels.config import WeixinChannelConfig
    from illusion.config.settings import Settings

logger = logging.getLogger(__name__)

# 重试参数
RETRY_DELAY_SECONDS = 2
BACKOFF_DELAY_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 3
SESSION_EXPIRED_ERRCODE = -14
SESSION_PAUSE_SECONDS = 600  # 会话过期后暂停 10 分钟

# AES 密钥缓存 TTL（入站附件的 aes_key 缓存，避免 daemon 重启后丢失）
AES_KEY_CACHE_TTL_SECONDS = 3600

# 图片扩展名（用于 send_image 路由）
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}


class WeixinChannel(Channel):
    """微信渠道实现（iLink Bot API）

    通过 HTTP 长轮询接收消息，不支持消息编辑（流式用打字状态替代）。

    Attributes:
        name: 渠道名 "weixin"
    """

    name = "weixin"

    def __init__(self, config: WeixinChannelConfig, settings: Settings) -> None:
        """初始化微信渠道

        Args:
            config: 微信配置
            settings: 主设置
        """
        super().__init__(config, settings)
        self._poll_session: Any = None  # 长轮询专用 session
        self._send_session: Any = None  # 发送专用 session（total=None 避免超时冲突）
        self._queue: Queue[InboundMessage] = Queue()  # 入站队列（保留供未来扩展，listen 当前用 HTTP 长轮询）
        self._stop_event = asyncio.Event()
        self._loop: Any = None

        # context_token 管理（iLink 硬约束）
        self._context_tokens: dict[str, str] = {}  # user_id → context_token

        # 打字状态管理
        self._typing_tickets: dict[str, str] = {}  # user_id → ticket
        self._typing_ticket_times: dict[str, float] = {}  # user_id → 获取时间

        # 长轮询游标
        self._sync_buf: str = ""

        # bot 自身 account_id（用于自回显检测，用 account_id 而非 user_id）
        self._account_id: str = config.account_id

        # 消息去重
        self._seen_msg_ids: dict[str, float] = {}

        # 入站附件 AES 密钥缓存："{message_id}:{attachment_id}" → (aes_key_b64, expire_time)
        # iLink 的媒体下载需要 encrypt_query_param + aes_key 双凭证，
        # Attachment 标准字段容纳不下 aes_key，故在 adapter 内缓存。
        # TTL 防止无限增长；正常使用场景下 LLM 会立即调用 receive_media 下载。
        self._aes_key_cache: dict[str, tuple[str, float]] = {}

    async def connect(self) -> None:
        """建立 HTTP 连接（长轮询 + 发送分离）"""
        import aiohttp  # 延迟导入

        from illusion.channels.weixin.ilink_api import _make_ssl_connector

        self._loop = asyncio.get_running_loop()
        connector = _make_ssl_connector()
        # 长轮询 session（有超时，35 秒 hold）
        self._poll_session = aiohttp.ClientSession(trust_env=True, connector=connector)
        # 发送 session（total=None，避免并发发送时 aiohttp 超时冲突）
        self._send_session = aiohttp.ClientSession(
            trust_env=True, connector=connector,
            timeout=aiohttp.ClientTimeout(total=None, connect=None, sock_connect=None, sock_read=None),
        )

        # 加载持久化状态
        self._load_context_tokens()
        self._load_sync_buf()

    def _normalize(self, raw_msg: dict[str, Any]) -> InboundMessage | None:
        """把 iLink 入站消息标准化为 InboundMessage

        同时提取 context_token 并缓存（iLink 硬约束），
        解析 item_list 中的 image/video/file/voice 附件元数据。

        附件处理与飞书/QQ 一致：只存元数据，LLM 调 receive_media 工具按需下载。
        AES 密钥（iLink 特有）缓存在 adapter 内，key 为 "{msg_id}:{att_id}"。

        Args:
            raw_msg: iLink 原始消息 dict[str, Any]

        Returns:
            InboundMessage | None: 标准化消息，无法解析返回 None
        """
        try:
            user_id = raw_msg.get("from_user_id", "")
            if not user_id:
                return None

            # 提取并缓存 context_token
            # 收到消息时立即落盘，确保跨进程（如 PC 终端跨渠道投递）能读到最新 token。
            # 此前仅在 send_text 中持久化，导致跨渠道投递读到空或过期 token，
            # iLink API 返回 errcode==0 但静默不投递。
            ctx_token = raw_msg.get("context_token", "")
            if ctx_token:
                self._context_tokens[user_id] = ctx_token
                try:
                    self._save_context_tokens()
                except (OSError, ValueError, AttributeError, TypeError) as save_exc:
                    logger.warning("context_token 持久化失败（不影响消息处理）: %s", save_exc)

            # 提取文本和附件（从 item_list）
            text = ""
            attachments: list[Attachment] = []
            msg_id = raw_msg.get("msgid", "")
            att_seq = 0  # 附件序号（只数附件，不含文本，确保 ID 连续）

            for item in raw_msg.get("item_list", []):
                item_type = item.get("type")
                if item_type == 1:  # ITEM_TEXT
                    text = item.get("text_item", {}).get("text", "")
                elif item_type == 2:  # ITEM_IMAGE
                    att_seq += 1
                    att = self._parse_image_item(item, msg_id, att_seq)
                    if att:
                        attachments.append(att)
                    else:
                        att_seq -= 1  # 解析失败回退
                elif item_type == 5:  # ITEM_VIDEO
                    att_seq += 1
                    att = self._parse_video_item(item, msg_id, att_seq)
                    if att:
                        attachments.append(att)
                    else:
                        att_seq -= 1
                elif item_type == 4:  # ITEM_FILE
                    att_seq += 1
                    att = self._parse_file_item(item, msg_id, att_seq)
                    if att:
                        attachments.append(att)
                    else:
                        att_seq -= 1
                elif item_type == 3:  # ITEM_VOICE
                    att_seq += 1
                    att = self._parse_voice_item(item, msg_id, att_seq)
                    if att:
                        attachments.append(att)
                    else:
                        att_seq -= 1

            is_bot = raw_msg.get("from_user_type") == "bot"

            return InboundMessage(
                text=text,
                chat_id=user_id,  # 微信私聊用 user_id 作为 chat_id
                chat_type="dm",  # 微信 bot 只私聊
                user_id=user_id,
                user_name="",
                message_id=msg_id,
                is_bot=is_bot,
                attachments=attachments,
            )
        except (KeyError, AttributeError, TypeError, ValueError, IndexError) as exc:
            logger.warning("标准化微信消息失败: %s", exc)
            return None

    def _parse_image_item(
        self, item: dict[str, Any], msg_id: str, idx: int
    ) -> Attachment | None:
        """解析 image_item 附件

        iLink image_item 结构：
            - media.encrypt_query_param: 加密下载参数
            - media.aes_key: base64 编码的 AES 密钥
            - media.full_url: 完整下载 URL（备用）
            - aeskey: hex 编码的 AES 密钥（旧字段，部分消息用此）

        Args:
            item: item_list 中的一项
            msg_id: 消息 ID（用于 AES 密钥缓存 key）
            idx: 附件序号

        Returns:
            Attachment | None: 附件对象，解析失败返回 None
        """
        image_item = item.get("image_item") or {}
        media = image_item.get("media") or {}
        encrypt_param = str(media.get("encrypt_query_param") or "")
        full_url = str(media.get("full_url") or "")
        aes_key_b64 = str(media.get("aes_key") or "")

        # 旧字段 aeskey 是 hex 编码，需转 base64(hex_string) 统一格式
        aeskey_hex = str(image_item.get("aeskey") or "")
        if not aes_key_b64 and aeskey_hex:
            aes_key_b64 = base64.b64encode(aeskey_hex.encode("ascii")).decode("ascii")

        if not encrypt_param and not full_url:
            return None

        att_id = str(idx)
        size_val = int(image_item.get("mid_size") or 0)

        # 缓存 aes_key 供 download_attachment 使用
        if aes_key_b64:
            self._aes_key_cache[f"{msg_id}:{att_id}"] = (
                aes_key_b64, time.monotonic() + AES_KEY_CACHE_TTL_SECONDS,
            )
            self._cleanup_aes_cache()

        return Attachment(
            id=att_id,
            media_type="image",
            filename=f"image_{idx}.jpg",
            size=size_val,
            file_key=encrypt_param,  # encrypt_query_param
            download_url=full_url,
            message_id=msg_id,
        )

    def _parse_video_item(
        self, item: dict[str, Any], msg_id: str, idx: int
    ) -> Attachment | None:
        """解析 video_item 附件"""
        video_item = item.get("video_item") or {}
        media = video_item.get("media") or {}
        encrypt_param = str(media.get("encrypt_query_param") or "")
        full_url = str(media.get("full_url") or "")
        aes_key_b64 = str(media.get("aes_key") or "")
        if not encrypt_param and not full_url:
            return None

        att_id = str(idx)
        size_val = int(video_item.get("video_size") or 0)
        if aes_key_b64:
            self._aes_key_cache[f"{msg_id}:{att_id}"] = (
                aes_key_b64, time.monotonic() + AES_KEY_CACHE_TTL_SECONDS,
            )
            self._cleanup_aes_cache()

        return Attachment(
            id=att_id,
            media_type="video",
            filename=f"video_{idx}.mp4",
            size=size_val,
            file_key=encrypt_param,
            download_url=full_url,
            message_id=msg_id,
        )

    def _parse_file_item(
        self, item: dict[str, Any], msg_id: str, idx: int
    ) -> Attachment | None:
        """解析 file_item 附件（保留原始文件名）"""
        file_item = item.get("file_item") or {}
        media = file_item.get("media") or {}
        encrypt_param = str(media.get("encrypt_query_param") or "")
        full_url = str(media.get("full_url") or "")
        aes_key_b64 = str(media.get("aes_key") or "")
        filename = str(file_item.get("file_name") or f"file_{idx}.bin")
        if not encrypt_param and not full_url:
            return None

        att_id = str(idx)
        try:
            size_val = int(file_item.get("len") or 0)
        except (TypeError, ValueError):
            size_val = 0

        if aes_key_b64:
            self._aes_key_cache[f"{msg_id}:{att_id}"] = (
                aes_key_b64, time.monotonic() + AES_KEY_CACHE_TTL_SECONDS,
            )
            self._cleanup_aes_cache()

        return Attachment(
            id=att_id,
            media_type="file",
            filename=filename,
            size=size_val,
            file_key=encrypt_param,
            download_url=full_url,
            message_id=msg_id,
        )

    def _parse_voice_item(
        self, item: dict[str, Any], msg_id: str, idx: int
    ) -> Attachment | None:
        """解析 voice_item 附件

        若有文字转录则跳过（文本已通过 text_item 提取）。
        """
        voice_item = item.get("voice_item") or {}
        if voice_item.get("text"):  # 有转录文本，不需要下载音频
            return None
        media = voice_item.get("media") or {}
        encrypt_param = str(media.get("encrypt_query_param") or "")
        full_url = str(media.get("full_url") or "")
        aes_key_b64 = str(media.get("aes_key") or "")
        if not encrypt_param and not full_url:
            return None

        att_id = str(idx)
        if aes_key_b64:
            self._aes_key_cache[f"{msg_id}:{att_id}"] = (
                aes_key_b64, time.monotonic() + AES_KEY_CACHE_TTL_SECONDS,
            )
            self._cleanup_aes_cache()

        return Attachment(
            id=att_id,
            media_type="audio",
            filename=f"voice_{idx}.silk",
            size=0,
            file_key=encrypt_param,
            download_url=full_url,
            message_id=msg_id,
        )

    def _cleanup_aes_cache(self) -> None:
        """清理过期的 AES 密钥缓存项"""
        if len(self._aes_key_cache) < 100:
            return  # 容量未到阈值，跳过清理
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._aes_key_cache.items() if exp < now]
        for k in expired:
            del self._aes_key_cache[k]

    def _get_aes_key(self, msg_id: str, att_id: str) -> str:
        """从缓存读取 AES 密钥

        Args:
            msg_id: 消息 ID
            att_id: 附件 ID

        Returns:
            str: base64 编码的 AES 密钥，未找到返回空串
        """
        entry = self._aes_key_cache.get(f"{msg_id}:{att_id}")
        if not entry:
            return ""
        aes_key, expire = entry
        if time.monotonic() > expire:
            self._aes_key_cache.pop(f"{msg_id}:{att_id}", None)
            return ""
        return aes_key

    def _admit(self, msg: InboundMessage) -> bool:
        """准入控制：决定消息是否进入 agent

        微信 bot 只能私聊，准入极简（无群组策略）：
        1. 自回显 → 拒绝
        2. 其他机器人（allow_bots=False）→ 拒绝
        3. 群消息 → 拒绝（bot 身份限制）

        Args:
            msg: 标准化消息

        Returns:
            bool: 放行返回 True
        """
        # 1. 自回显（用 account_id 即 bot 身份，如 226d22c4ac3d@im.bot）
        if self._account_id and msg.user_id == self._account_id:
            return False
        # 2. 机器人策略
        if msg.is_bot and not self.config.allow_bots:
            return False
        # 3. 群消息直接丢弃
        return msg.chat_type != "group"

    async def listen(self) -> AsyncIterator[InboundMessage]:
        """长轮询监听消息"""
        logger.info("微信长轮询已启动，sync_buf=%s", repr(self._sync_buf[:30]) if self._sync_buf else "(空)")
        consecutive_failures = 0
        while not self._stop_event.is_set():
            try:
                result = await _poll_with_retry(
                    self._poll_session, base_url=self.config.base_url,
                    token=self.config.token, sync_buf=self._sync_buf,
                )
                consecutive_failures = 0

                # 更新游标
                self._sync_buf = result.get("get_updates_buf", self._sync_buf)
                self._save_sync_buf()

                # 处理消息
                msg_count = len(result.get("msgs", []))
                if msg_count:
                    logger.info("微信收到 %d 条消息", msg_count)
                for raw_msg in result.get("msgs", []):
                    msg = self._normalize(raw_msg)
                    if msg is None:
                        logger.debug("消息标准化失败，跳过")
                        continue
                    if self._is_duplicate(msg.message_id):
                        logger.debug("重复消息，跳过: %s", msg.message_id)
                        continue
                    if self._admit(msg):
                        logger.info("微信消息已准入，yield: user=%s text=%s", msg.user_id, msg.text[:30])
                        yield msg
                    else:
                        logger.info("微信消息被拒绝: user=%s is_bot=%s", msg.user_id, msg.is_bot)

            except asyncio.CancelledError:
                break
            except RuntimeError as exc:
                # 会话过期是不可恢复错误，暂停后重试（而非无限循环）
                if "session_expired" in str(exc) or "会话过期" in str(exc):
                    logger.warning("微信会话过期，暂停 %ds 等待重新扫码: %s", SESSION_PAUSE_SECONDS, exc)
                    await asyncio.sleep(SESSION_PAUSE_SECONDS)
                    consecutive_failures = 0
                    continue
                consecutive_failures += 1
                if consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                    logger.warning("长轮询失败 (%d/3): %s，%ds 后重试",
                                   consecutive_failures, exc, RETRY_DELAY_SECONDS)
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                else:
                    logger.warning("连续失败 %d 次，%ds 退避", consecutive_failures, BACKOFF_DELAY_SECONDS)
                    await asyncio.sleep(BACKOFF_DELAY_SECONDS)
                    consecutive_failures = 0
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError) as exc:
                consecutive_failures += 1
                if consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                    logger.warning("长轮询失败 (%d/3): %s，%ds 后重试",
                                   consecutive_failures, exc, RETRY_DELAY_SECONDS)
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                else:
                    logger.warning("连续失败 %d 次，%ds 退避", consecutive_failures, BACKOFF_DELAY_SECONDS)
                    await asyncio.sleep(BACKOFF_DELAY_SECONDS)
                    consecutive_failures = 0

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
        expired = [k for k, v in self._seen_msg_ids.items() if now - v > 300]
        for k in expired:
            del self._seen_msg_ids[k]
        if msg_id in self._seen_msg_ids:
            return True
        self._seen_msg_ids[msg_id] = now
        return False

    async def send_text(self, chat_id: str, text: str, *, reply_to: str = "") -> str:
        """发送文本消息，超长自动分片，含重试和限流退避

        Args:
            chat_id: 目标会话（微信用 user_id）
            text: 文本内容
            reply_to: 未使用（微信不支持回复引用）

        Returns:
            str: 空字符串（微信无 message_id 返回）
        """
        from illusion.channels.weixin.ilink_api import (
            RATE_LIMIT_ERRCODE,
            SESSION_EXPIRED_ERRCODE,
            _split_text,
            send_message,
        )

        chunks = _split_text(text)
        logger.info("微信发送 %d 个分片到 %s", len(chunks), chat_id)
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(1.5)  # 分片间隔，防限流
            ctx_token = self._context_tokens.get(chat_id, "")

            # 重试逻辑（最多 3 次，处理瞬态失败和限流）
            for attempt in range(3):
                resp = await send_message(
                    self._send_session, base_url=self.config.base_url, token=self.config.token,
                    to=chat_id, text=chunk, context_token=ctx_token or None,
                    client_id=f"illusion-weixin-{uuid.uuid4().hex}",
                )
                errcode = resp.get("errcode", 0)
                if errcode == SESSION_EXPIRED_ERRCODE:
                    # 会话过期：去掉 context_token 降级重试一次
                    if attempt == 0:
                        ctx_token = ""
                        self._context_tokens.pop(chat_id, None)
                        logger.warning("微信会话过期，去掉 context_token 重试")
                        continue
                    from illusion.config.i18n import t
                    raise RuntimeError(t("weixin_session_expired"))
                if errcode == RATE_LIMIT_ERRCODE:
                    logger.warning("微信发送限流，%ds 后重试", RETRY_DELAY_SECONDS * 3)
                    await asyncio.sleep(RETRY_DELAY_SECONDS * 3)
                    continue
                if errcode != 0:
                    logger.warning("微信发送失败 (attempt %d/%d): errcode=%d",
                                   attempt + 1, 3, errcode)
                    if attempt < 2:
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue
                break  # 成功或不可重试的错误

        self._save_context_tokens()
        return ""

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """编辑消息——微信不支持编辑，空操作

        ChannelRunner 调此方法做流式更新时，对微信是 no-op。
        回复走 send_text 一次性发送。

        Args:
            chat_id: 会话标识
            message_id: 未使用
            text: 未使用
        """

    async def send_file(self, chat_id: str, file_path: str, *, reply_to: str = "") -> None:
        """发送文件（按扩展名路由到 send_image/send_document）

        Args:
            chat_id: 目标会话
            file_path: 本地文件路径
            reply_to: 未使用（微信不支持回复引用）
        """
        ext = Path(file_path).suffix.lower()
        if ext in _IMAGE_EXTS:
            await self.send_image(chat_id, file_path, reply_to=reply_to)
        else:
            await self.send_document(chat_id, file_path, reply_to=reply_to)

    async def send_image(
        self, chat_id: str, image_path: str, *, caption: str = "", reply_to: str = ""
    ) -> str:
        """发送图片消息

        通过 AES-128-ECB 加密 → getuploadurl → CDN 上传 → sendmessage 流程。

        Args:
            chat_id: 目标 user_id
            image_path: 本地图片文件路径
            caption: 可选附注文字（先发文本再发图片）
            reply_to: 未使用（微信不支持回复引用）

        Returns:
            str: 客户端消息 ID（用于幂等去重）

        Raises:
            RuntimeError: 加密、上传或发送失败
        """
        return await self._send_file(chat_id, image_path, caption)

    async def send_document(
        self, chat_id: str, file_path: str, *, caption: str = "", reply_to: str = ""
    ) -> str:
        """发送文件（非图片）消息

        与 send_image 同流程，仅 media_type 不同。

        Args:
            chat_id: 目标 user_id
            file_path: 本地文件路径
            caption: 可选附注文字
            reply_to: 未使用（微信不支持回复引用）

        Returns:
            str: 客户端消息 ID

        Raises:
            RuntimeError: 加密、上传或发送失败
        """
        return await self._send_file(chat_id, file_path, caption)

    async def send_video(
        self, chat_id: str, video_path: str, *, caption: str = "", reply_to: str = ""
    ) -> str:
        """发送视频消息

        Args:
            chat_id: 目标 user_id
            video_path: 本地视频文件路径
            caption: 可选附注文字
            reply_to: 未使用

        Returns:
            str: 客户端消息 ID
        """
        return await self._send_file(chat_id, video_path, caption)

    async def download_attachment(
        self, attachment: Attachment, save_path: str
    ) -> str:
        """下载入站附件到本地路径

        从 CDN 下载密文 → AES 解密 → 保存到 save_path。
        AES 密钥从 adapter 内缓存读取（_normalize 时存入）。

        Args:
            attachment: 附件对象（来自 InboundMessage.attachments）
                - file_key: encrypt_query_param（优先）
                - download_url: full_url（备用）
            save_path: 本地保存路径

        Returns:
            str: 实际保存路径

        Raises:
            RuntimeError: 下载或解密失败
            NotImplementedError: cryptography 未安装
        """
        from illusion.channels.weixin.ilink_api import (
            _check_crypto_available,
            download_and_decrypt_media,
        )

        if not _check_crypto_available():
            raise NotImplementedError(
                "微信附件下载需要 cryptography 包，请运行 "
                "`pip install cryptography` 或 `illusion channel login` 选择微信"
            )

        if not self._poll_session:
            raise RuntimeError("微信渠道未连接，无法下载附件")

        # AES 密钥从缓存精确查找（key = "{msg_id}:{att_id}"）
        # Attachment 携带 message_id，确保跨消息不串键
        aes_key_b64 = self._get_aes_key(attachment.message_id, attachment.id)

        encrypt_param = attachment.file_key  # encrypt_query_param
        full_url = attachment.download_url

        if not encrypt_param and not full_url:
            raise RuntimeError(
                f"附件无下载凭证: id={attachment.id} filename={attachment.filename}"
            )

        try:
            data = await download_and_decrypt_media(
                self._poll_session,
                cdn_base_url=self.config.cdn_base_url,
                encrypted_query_param=encrypt_param or None,
                aes_key_b64=aes_key_b64 or None,
                full_url=full_url or None,
            )
        except Exception as exc:
            raise RuntimeError(f"微信附件下载失败: {exc}") from exc

        # 确保父目录存在
        out_path = Path(save_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # 下载的附件可能很大，用 to_thread 写盘避免阻塞事件循环
        await asyncio.to_thread(out_path.write_bytes, data)
        logger.info("微信附件已下载: %s → %s (%d bytes)",
                    attachment.filename, save_path, len(data))
        return str(out_path)

    async def _send_file(
        self, chat_id: str, path: str, caption: str, *, force_file_attachment: bool = False,
    ) -> str:
        """发送媒体文件的核心实现

        流程：
            1. 读取本地文件明文
            2. 生成随机 16 字节 AES 密钥，AES-128-ECB + PKCS#7 加密
            3. 调 getuploadurl 获取 CDN 上传 URL
            4. 上传密文到 CDN，得到 encrypted_query_param
            5. （可选）先发送 caption 文本
            6. 调 sendmessage 发送含 media item 的消息

        Args:
            chat_id: 目标 user_id
            path: 本地文件路径
            caption: 可选附注文字
            force_file_attachment: 强制作为文件附件发送（不区分媒体类型）

        Returns:
            str: 客户端消息 ID

        Raises:
            RuntimeError: 任一步骤失败
        """
        from illusion.channels.weixin.ilink_api import (
            EP_SEND_MESSAGE,
            _aes128_ecb_encrypt,
            _aes_padded_size,
            _api_post,
            _cdn_upload_url,
            get_upload_url,
            upload_ciphertext,
        )

        if not self._send_session:
            raise RuntimeError("微信渠道未连接，无法发送媒体")
        if not self.config.token:
            raise RuntimeError("微信 token 未配置")

        # 文件可能很大（图片/视频/文档），用 to_thread 避免阻塞事件循环
        plaintext = await asyncio.to_thread(Path(path).read_bytes)
        media_type, item_builder = self._outbound_media_builder(
            path, force_file_attachment=force_file_attachment,
        )
        filekey = secrets.token_hex(16)
        aes_key = secrets.token_bytes(16)
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)

        # 获取上传 URL
        upload_response = await get_upload_url(
            self._send_session,
            base_url=self.config.base_url, token=self.config.token,
            to_user_id=chat_id, media_type=media_type, filekey=filekey,
            rawsize=rawsize, rawfilemd5=rawfilemd5,
            filesize=_aes_padded_size(rawsize), aeskey_hex=aes_key.hex(),
        )
        upload_param = str(upload_response.get("upload_param") or "")
        upload_full_url = str(upload_response.get("upload_full_url") or "")

        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = _cdn_upload_url(self.config.cdn_base_url, upload_param, filekey)
        else:
            raise RuntimeError(
                f"getuploadurl 返回既无 upload_param 也无 upload_full_url: {upload_response}"
            )

        # 上传密文到 CDN
        encrypted_query_param = await upload_ciphertext(
            self._send_session, ciphertext=ciphertext, upload_url=upload_url,
        )

        # iLink API 期望 aes_key 为 base64(hex_string)，而非 base64(raw_bytes)
        # 否则接收端解密失败，图片显示为灰框
        aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")
        media_item = item_builder(
            encrypt_query_param=encrypted_query_param,
            aes_key_for_api=aes_key_for_api,
            ciphertext_size=len(ciphertext),
            plaintext_size=rawsize,
            filename=Path(path).name,
            rawfilemd5=rawfilemd5,
        )

        ctx_token = self._context_tokens.get(chat_id, "")
        last_message_id = ""

        # 先发送 caption 文本
        if caption:
            from illusion.channels.weixin.ilink_api import send_message
            last_message_id = f"illusion-weixin-{uuid.uuid4().hex}"
            await send_message(
                self._send_session, base_url=self.config.base_url, token=self.config.token,
                to=chat_id, text=caption, context_token=ctx_token or None,
                client_id=last_message_id,
            )

        # 发送含媒体 item 的消息
        last_message_id = f"illusion-weixin-{uuid.uuid4().hex}"
        message_payload: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": chat_id,
            "client_id": last_message_id,
            "message_type": 2,  # MSG_TYPE_BOT
            "message_state": 2,  # MSG_STATE_FINISH
            "item_list": [media_item],
        }
        if ctx_token:
            message_payload["context_token"] = ctx_token

        await _api_post(
            self._send_session,
            base_url=self.config.base_url, endpoint=EP_SEND_MESSAGE,
            payload={"msg": message_payload}, token=self.config.token,
            timeout_ms=15_000,  # API_TIMEOUT_MS
        )
        logger.info("微信媒体已发送: %s → %s", Path(path).name, chat_id)
        return last_message_id

    def _outbound_media_builder(
        self, path: str, *, force_file_attachment: bool = False,
    ) -> tuple[int, Any]:
        """根据文件 MIME 类型构造出站媒体 item 构造器

        Args:
            path: 本地文件路径
            force_file_attachment: 强制作为文件附件（不走 image/video 通道）

        Returns:
            tuple[int, Callable]: (media_type, item_builder)
                item_builder 接收 encrypt_query_param/aes_key_for_api/ciphertext_size/
                plaintext_size/filename/rawfilemd5 关键字参数，返回 media item dict
        """
        from illusion.channels.weixin.ilink_api import (
            ITEM_FILE,
            ITEM_IMAGE,
            ITEM_VIDEO,
            MEDIA_FILE,
            MEDIA_IMAGE,
            MEDIA_VIDEO,
        )

        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"

        if not force_file_attachment and mime.startswith("image/"):
            return MEDIA_IMAGE, lambda **kw: {
                "type": ITEM_IMAGE,
                "image_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
                    "mid_size": kw["ciphertext_size"],
                },
            }
        if not force_file_attachment and mime.startswith("video/"):
            return MEDIA_VIDEO, lambda **kw: {
                "type": ITEM_VIDEO,
                "video_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
                    "video_size": kw["ciphertext_size"],
                    "play_length": 0,
                    "video_md5": kw.get("rawfilemd5", ""),
                },
            }
        # 默认作为文件附件
        return MEDIA_FILE, lambda **kw: {
            "type": ITEM_FILE,
            "file_item": {
                "media": {
                    "encrypt_query_param": kw["encrypt_query_param"],
                    "aes_key": kw["aes_key_for_api"],
                    "encrypt_type": 1,
                },
                "file_name": kw["filename"],
                "len": str(kw["plaintext_size"]),
            },
        }

    async def start_typing(self, chat_id: str) -> None:
        """开始打字状态指示

        Args:
            chat_id: 目标 user_id
        """
        ticket = await self._ensure_typing_ticket(chat_id)
        if not ticket:
            return
        try:
            from illusion.channels.weixin.ilink_api import TYPING_START, send_typing
            await send_typing(
                self._send_session, base_url=self.config.base_url, token=self.config.token,
                to_user_id=chat_id, typing_ticket=ticket, status=TYPING_START,
            )
        except (ImportError, aiohttp.ClientError, RuntimeError, ValueError, OSError) as exc:
            logger.debug("发送打字状态失败: %s", exc)

    async def stop_typing(self, chat_id: str) -> None:
        """停止打字状态指示

        Args:
            chat_id: 目标 user_id
        """
        ticket = await self._ensure_typing_ticket(chat_id)
        if not ticket:
            return
        try:
            from illusion.channels.weixin.ilink_api import TYPING_STOP, send_typing
            await send_typing(
                self._send_session, base_url=self.config.base_url, token=self.config.token,
                to_user_id=chat_id, typing_ticket=ticket, status=TYPING_STOP,
            )
        except (ImportError, aiohttp.ClientError, RuntimeError, ValueError, OSError) as exc:
            logger.debug("停止打字状态失败: %s", exc)

    async def _ensure_typing_ticket(self, user_id: str) -> str:
        """获取打字 ticket（TTL 600s，过期自动刷新）

        ticket 过期后需刷新，否则 stop_typing 可能静默失效，
        导致用户端永远卡在「正在输入」。

        Args:
            user_id: 目标用户

        Returns:
            str: 打字 ticket，获取失败返回空串
        """
        cached = self._typing_tickets.get(user_id)
        cached_time = self._typing_ticket_times.get(user_id, 0)
        if cached and (time.monotonic() - cached_time < 600):
            return cached

        try:
            from illusion.channels.weixin.ilink_api import get_config
            ctx_token = self._context_tokens.get(user_id, "")
            cfg = await get_config(
                self._send_session, base_url=self.config.base_url,
                token=self.config.token, context_token=ctx_token,
                ilink_user_id=user_id,
            )
            ticket = cfg.get("typing_ticket", "")
            if ticket:
                self._typing_tickets[user_id] = ticket
                self._typing_ticket_times[user_id] = time.monotonic()
            return str(ticket)
        except (ImportError, aiohttp.ClientError, RuntimeError, ValueError, KeyError, OSError) as exc:
            logger.debug("获取打字 ticket 失败: %s", exc)
            return ""

    async def shutdown(self) -> None:
        """关闭渠道"""
        self._stop_event.set()
        # 关闭入站队列唤醒潜在消费者（当前 listen 走 HTTP 长轮询，
        # 但仍调用 queue.shutdown 以保持与其他渠道一致并防御未来改动）
        self._queue.shutdown()
        if self._poll_session is not None:
            await self._poll_session.close()
        if self._send_session is not None:
            await self._send_session.close()

    def get_bot_id(self) -> str:
        """返回微信 bot 自身 account_id

        Returns:
            str: bot 的 account_id
        """
        return self._account_id

    # ─── 持久化 ──────────────────────────────────────────────

    def _load_context_tokens(self) -> None:
        """从磁盘加载 context_tokens"""
        from illusion.config.paths import get_channels_data_dir
        path = get_channels_data_dir() / "weixin" / "context_tokens.json"
        if path.exists():
            try:
                self._context_tokens = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                pass

    def _save_context_tokens(self) -> None:
        """持久化 context_tokens 到磁盘"""
        from illusion.config.paths import get_channels_data_dir
        path = get_channels_data_dir() / "weixin" / "context_tokens.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(self._context_tokens, ensure_ascii=False))

    def _load_sync_buf(self) -> None:
        """从磁盘加载长轮询游标"""
        from illusion.config.paths import get_channels_data_dir
        path = get_channels_data_dir() / "weixin" / "sync_buf.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._sync_buf = data.get("sync_buf", "")
            except (json.JSONDecodeError, ValueError):
                pass

    def _save_sync_buf(self) -> None:
        """持久化游标到磁盘"""
        from illusion.config.paths import get_channels_data_dir
        path = get_channels_data_dir() / "weixin" / "sync_buf.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"sync_buf": self._sync_buf}))


async def _poll_with_retry(
    session: Any,
    *,
    base_url: str,
    token: str,
    sync_buf: str,
) -> dict[str, Any]:
    """调用长轮询，处理会话过期错误码

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        token: Bearer token
        sync_buf: 游标

    Returns:
        dict[str, Any]: 长轮询响应

    Raises:
        RuntimeError: 会话过期需重新扫码
    """
    from illusion.channels.weixin.ilink_api import SESSION_EXPIRED_ERRCODE, poll_updates

    result = await poll_updates(session, base_url=base_url, token=token, sync_buf=sync_buf)

    errcode = result.get("errcode", 0)
    ret = result.get("ret", 0)
    if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
        from illusion.config.i18n import t
        raise RuntimeError(t("weixin_session_expired"))

    return result
