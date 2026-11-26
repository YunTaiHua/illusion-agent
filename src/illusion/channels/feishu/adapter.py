"""飞书渠道适配器
================

实现 FeishuChannel，对接飞书开放平台的 WS 长连接，处理事件分发与准入控制。

核心职责：
    - 建立 WS 长连接（lark-oapi 官方客户端）
    - 标准化入站事件为 InboundMessage
    - 准入控制（自回显/机器人/@提及/群组策略）
    - 消息收发委托给 messaging 模块

类说明：
    - FeishuChannel: 飞书渠道实现
"""
from __future__ import annotations

import asyncio  # 异步
import logging  # 日志
from collections.abc import AsyncIterator  # 类型
from concurrent.futures import ThreadPoolExecutor  # 专用线程池
from typing import TYPE_CHECKING, Any

from illusion.channels.base import Attachment, Channel, InboundMessage  # 基类与消息类型
from illusion.utils.aioqueue import Queue, QueueShutDown  # 支持关闭语义的异步队列

if TYPE_CHECKING:
    from illusion.channels.config import FeishuChannelConfig  # 配置
    from illusion.config.settings import Settings  # 主设置

logger = logging.getLogger(__name__)  # 日志器

# 模块级专用 executor：飞书 lark-oapi SDK 调用是同步阻塞的 HTTP 请求，
# 单次调用可能耗时数百毫秒到数秒。若复用默认 ThreadPoolExecutor 会与其他
# to_thread 任务（文件 I/O、urllib 请求等）竞争线程，导致 SDK 调用排队
# 饥饿。专用 executor 隔离 SDK 调用，保证发送/编辑消息的响应性。
# daemon 线程：进程退出时自动回收，无需显式 shutdown。
_feishu_executor = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="feishu-sdk",
)

class FeishuChannel(Channel):
    """飞书渠道实现

    通过 WS 长连接接收飞书消息事件，标准化后产出 InboundMessage。

    Attributes:
        name: 渠道名 "feishu"
    """

    name = "feishu"  # 渠道名

    def __init__(self, config: FeishuChannelConfig, settings: Settings) -> None:
        """初始化飞书渠道

        Args:
            config: 飞书配置
            settings: 主设置
        """
        super().__init__(config, settings)
        self._client: Any = None  # lark 客户端
        self._ws: Any = None  # WS 包装
        self._queue: Queue[InboundMessage] = Queue()  # 入站队列（支持 shutdown 哨兵唤醒 listen）
        self._loop: Any = None  # 主事件循环引用（connect 时保存，WS 回调线程用）
        self._bot_open_id: str = ""  # bot 自身 open_id（hydrate 后赋值）
        self._stop_event = asyncio.Event()  # 停止信号
        self._ws_future: Any = None  # executor future（run_in_executor 返回，shutdown 等待用）
        self._ws_executor: ThreadPoolExecutor | None = None  # 本连接专用 WS executor

    def _get_domain(self) -> str:
        """获取飞书 API 域名

        domain == "feishu" 使用国内域名，否则使用海外 Larksuite 域名。

        Returns:
            str: API 基础 URL
        """
        return "https://open.feishu.cn" if self.config.domain == "feishu" else "https://open.larksuite.com"

    async def connect(self) -> None:
        """建立 WS 长连接

        连接前先清理旧资源，失败时调用 shutdown 清理已创建的资源：
        - 先清理旧资源（_supervise 重启时复用 adapter 实例）
        - 失败时调用 shutdown 清理已创建的资源，避免 lark_loop 泄漏
        """
        from illusion.channels.feishu.messaging import build_lark_client
        from illusion.channels.feishu.ws_client import FeishuWSClient
        from illusion.config.i18n import t

        # 先清理旧资源（_supervise 重启时复用 adapter 实例，避免多个 lark_loop 冲突）
        await self._cleanup_resources()

        # 重建入站队列：shutdown() 会永久关闭队列（aioqueue 哨兵不可逆）。
        # 若不重建，_supervise 重启后 listen() 会立即抛 QueueShutDown，令
        # runner.run() 立刻正常返回，_supervise 无退避地无限快速重启——
        # 表现为日志反复 hydrate→stop→connected，断网恢复后仍不断失败。
        self._queue = Queue()
        # 重置停止信号：飞书 listen() 实际走队列哨兵唤醒，未读 _stop_event，
        # 但保留并重建它，与 qq/weixin 保持统一的三渠道停止信号接口。
        self._stop_event = asyncio.Event()

        # 构造 lark 客户端
        self._client = build_lark_client(self.config)
        # 获取 bot 自身 open_id（用于自回显检测和 @提及识别）
        await self._hydrate_bot_id()
        # 构造 WS 包装
        domain = self._get_domain()
        self._ws = FeishuWSClient(
            app_id=self.config.app_id,
            app_secret=self.config.app_secret,
            event_handler=self._on_raw_event,
            domain=domain,
        )
        # 保存当前事件循环引用（WS 回调线程需要用它跨线程投递消息）
        # 用 get_running_loop() 替代已弃用的 get_event_loop()（Python 3.10+）
        self._loop = asyncio.get_running_loop()
        # 每个连接使用独立 executor（max_workers=1）跑阻塞的 start()。
        # 复用模块级单 worker executor 会在断网时被 lark SDK 的阻塞 requests.post
        # 卡死唯一 worker，导致后续重连永远排队无法执行（断网后飞书无法恢复的根因）。
        # 独立 executor + 卡死时 shutdown(wait=False) 放弃，保证下次 connect 总能新建连接。
        self._ws_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="feishu-ws",
        )
        self._ws_future = self._loop.run_in_executor(self._ws_executor, self._ws.start)
        print(t("channel_feishu_connected", bot=self._bot_open_id or "illusion"))

    async def _cleanup_resources(self) -> None:
        """清理旧 WS 客户端资源（connect 重启或 shutdown 时调用）

        停止 ws.stop() 跨线程中断 lark_loop，等待 future 退出，避免多个
        lark_loop 并存导致 "attached to a different loop" 错误。

        资源清理放在最外层 finally：即使等待 ws_future 被外层 task 取消
        （CancelledError 上抛），executor 与 _ws 也会被释放，避免状态残留
        导致下次 connect() 误判。
        """
        # 先停止 WS 客户端（跨线程中断 lark_loop）
        old_lark_loop = None
        if self._ws is not None:
            old_lark_loop = getattr(self._ws, '_lark_loop', None)
            try:
                self._ws.stop()
            except Exception:
                logger.warning("停止飞书 WS 客户端失败", exc_info=True)
        # 等待 WS 线程退出（超时 10s）
        ws_future = self._ws_future
        if ws_future is not None:
            try:
                await asyncio.wait_for(asyncio.shield(ws_future), timeout=10.0)
            except TimeoutError:
                # 线程卡死在 lark SDK 无超时 requests.post / DNS 时，10s 内未退出。
                # 已通过 stop() 排队 _shutdown_loop，阻塞调用返回后线程自会退出；
                # 配合 ws_client.start() 对 requests.post 注入的超时，卡死窗口有限。
                logger.warning("飞书 WS 线程 10s 内未退出，可能卡死，等待其自回收")
                # 强制关闭旧 lark_loop，防止残留导致 "attached to a different loop"
                if old_lark_loop is not None and not old_lark_loop.is_closed():
                    try:
                        old_lark_loop.call_soon_threadsafe(old_lark_loop.stop)
                    except Exception:
                        logger.warning("强制停止旧 lark_loop 失败", exc_info=True)
            except asyncio.CancelledError:
                # ws_client.start() 被 stop() 取消时可能传播 CancelledError，
                # 属正常关闭路径；状态清理交给最外层 finally
                raise
            except Exception:
                # 其他异常属正常关闭路径，不应中断 shutdown 流程
                logger.debug("飞书 WS 清理过程中出现异常", exc_info=True)
            finally:
                self._ws_future = None
        # 释放本连接专用 executor（wait=False：不等待可能卡死的 WS 线程）。
        # 卡死的线程已通过 stop() 排队 _shutdown_loop，其阻塞调用返回后自会退出；
        # 下次 connect() 用全新 executor，避免单 worker executor 被卡死线程永久占用
        # 导致后续重连永远无法执行（断网后飞书无法恢复的根因）。
        try:
            if self._ws_executor is not None:
                self._ws_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            logger.debug("关闭飞书 WS executor 失败", exc_info=True)
        finally:
            self._ws_executor = None
            self._ws = None

    async def _hydrate_bot_id(self) -> None:
        """从飞书 API 获取 bot 自身 open_id

        调用 bot_info 接口获取 bot 的 open_id，
        用于自回显检测和 @提及识别。

        lark SDK 升级后 GetBotInfoRequest 被移除，
        改用 lark_oapi.channel.bot_identity.fetch_bot_identity。
        """
        try:
            from lark_oapi.channel.bot_identity import fetch_bot_identity
            from lark_oapi.core.model.config import Config
            # 构造 Config 对象（fetch_bot_identity 需要）
            config = Config()
            config.app_id = self.config.app_id
            config.app_secret = self.config.app_secret
            config.domain = self._get_domain()
            identity = await fetch_bot_identity(config)
            if identity is not None and identity.open_id:
                self._bot_open_id = identity.open_id
                logger.info("飞书 bot open_id: %s", self._bot_open_id)
        except (ImportError, AttributeError, TypeError, RuntimeError, OSError) as exc:
            logger.warning("获取飞书 bot open_id 失败: %s", exc)

    def get_bot_id(self) -> str:
        """返回飞书 bot 自身 open_id

        Returns:
            str: bot 的 open_id
        """
        return self._bot_open_id

    async def health_probe(self) -> bool:
        """飞书健康探活：获取 tenant_access_token 检测网络和认证状态

        通过轻量 HTTP 调用 tenant_access_token/internal 接口检测
        网络连接和 app_id/app_secret 是否有效。
        成功返回 True，失败返回 False。

        Returns:
            bool: 渠道健康返回 True，僵死返回 False
        """
        if self._client is None:
            return False
        try:
            import json as _json
            import urllib.request as _urllib
            url = f"{self._get_domain()}/open-apis/auth/v3/tenant_access_token/internal"
            payload = _json.dumps({
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            }).encode()
            req = _urllib.Request(url, data=payload, headers={"Content-Type": "application/json"})

            def _do_request() -> dict[str, Any]:
                with _urllib.urlopen(req, timeout=10) as resp:
                    result: dict[str, Any] = _json.loads(resp.read().decode())
                    return result

            data = await asyncio.to_thread(_do_request)
            return data.get("code") == 0
        except (OSError, ValueError, AttributeError, TypeError):
            return False

    def _on_raw_event(self, event: Any) -> None:
        """处理原始飞书事件（在 WS 客户端的 executor 线程调用）

        event 是 lark-oapi 的强类型 P2ImMessageReceiveV1 对象。
        标准化为 InboundMessage 后线程安全地投递到入站队列。

        注意：本方法在子线程调用，必须用 connect() 保存的主 loop 引用
        （不能在此处 asyncio.get_event_loop()，否则拿到的是子线程的新 loop，
        消息会投递到错误的 loop 导致 ChannelRunner 收不到）。

        Args:
            event: 强类型事件对象（P2ImMessageReceiveV1）
        """
        try:
            msg = self._normalize(event)
            if msg is not None and self._admit(msg, mentioned_bot=self._event_mentions_bot(event)):
                # 用保存的主 loop 线程安全地投递到 asyncio.Queue
                loop = getattr(self, "_loop", None)
                if loop is None or loop.is_closed():
                    logger.warning("事件循环不可用，丢弃飞书消息")
                    return

                def _deliver() -> None:
                    # 重连瞬间旧线程可能仍投递陈旧消息：若队列已被 shutdown()
                    # 关闭，put_nowait 抛 QueueShutDown，属正常关闭期信号，静默丢弃
                    try:
                        self._queue.put_nowait(msg)
                    except QueueShutDown:
                        pass

                loop.call_soon_threadsafe(_deliver)
        except Exception:
            logger.exception("处理飞书事件异常")

    def _normalize(self, event: Any) -> InboundMessage | None:
        """把强类型飞书事件标准化为 InboundMessage

        事件对象结构（lark-oapi 强类型）：
            event.event.sender.sender_id.open_id / .sender_type
            event.event.message.chat_id / .chat_type / .message_id / .content

        Args:
            event: P2ImMessageReceiveV1 事件对象

        Returns:
            InboundMessage | None: 标准化消息，无法解析返回 None
        """
        try:
            data = getattr(event, "event", None)
            if data is None:
                return None
            sender = getattr(data, "sender", None)
            message = getattr(data, "message", None)
            if sender is None or message is None:
                return None

            # 发送者信息
            sender_id = getattr(sender, "sender_id", None)
            user_id = ""
            if sender_id is not None:
                # 优先 open_id，其次 union_id、user_id
                user_id = (getattr(sender_id, "open_id", None)
                           or getattr(sender_id, "union_id", None)
                           or getattr(sender_id, "user_id", None)
                           or "")
            sender_type = getattr(sender, "sender_type", "") or ""
            is_bot = sender_type == "app"

            # 消息信息
            chat_id = getattr(message, "chat_id", "") or ""
            chat_type_raw = getattr(message, "chat_type", "p2p") or "p2p"  # p2p 或 group
            message_id = getattr(message, "message_id", "") or ""
            content = getattr(message, "content", '{"text":""}') or '{"text":""}'
            msg_type = getattr(message, "message_type", "") or ""
            text = _extract_text(content)

            # 入站附件识别：image/file 消息归一化为 Attachment
            attachments: list[Attachment] = []
            if msg_type == "image":
                image_key = _extract_field(content, "image_key")
                if image_key:
                    attachments.append(Attachment(
                        id="1",
                        media_type="image",
                        filename=f"image_{image_key[:8]}.png",
                        file_key=image_key,
                    ))
                    if not text:
                        text = "[收到图片]"
            elif msg_type == "file":
                file_key = _extract_field(content, "file_key")
                if file_key:
                    file_name = _extract_field(content, "file_name") or "attachment"
                    # 编码 message_id 到 file_key（格式 "message_id|file_key"），
                    # download_attachment 下载文件消息资源时需要 message_id
                    attachments.append(Attachment(
                        id="1",
                        media_type="file",
                        filename=file_name,
                        file_key=f"{message_id}|{file_key}",
                    ))
                    if not text:
                        text = "[收到文件]"

            return InboundMessage(
                text=text,
                chat_id=chat_id,
                chat_type="group" if chat_type_raw == "group" else "dm",
                user_id=user_id,
                user_name="",  # 飞书事件不直接带显示名，需另调 API 获取
                message_id=message_id,
                is_bot=is_bot,
                attachments=attachments,
            )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
            logger.warning("标准化飞书事件失败: %s", exc)
            return None

    def _event_mentions_bot(self, event: Any) -> bool:
        """检测事件是否 @了 bot

        事件对象的 message.mentions 是 MentionEvent 列表，
        每项有 .id（含 .open_id 等属性）或 .key/name。

        Args:
            event: P2ImMessageReceiveV1 事件对象

        Returns:
            bool: 是否 @了 bot
        """
        try:
            data = getattr(event, "event", None)
            if data is None:
                return False
            message = getattr(data, "message", None)
            if message is None:
                return False
            mentions = getattr(message, "mentions", None) or []
            if not mentions:
                return False
            if not self._bot_open_id:
                return True  # 未能 hydrate bot ID 时，有 mention 即认为 @了
            for m in mentions:
                # MentionEvent.id 是 UserId 对象，有 open_id 属性
                m_id = getattr(m, "id", None)
                if m_id is not None and getattr(m_id, "open_id", None) == self._bot_open_id:
                    return True
            return False
        except (AttributeError, TypeError):
            return False

    def _admit(self, msg: InboundMessage, *, mentioned_bot: bool) -> bool:
        """准入控制：决定消息是否进入 agent

        4 道闸门：自回显、机器人、群组策略、@提及。

        Args:
            msg: 标准化消息
            mentioned_bot: 是否 @了 bot

        Returns:
            bool: 放行返回 True
        """
        # 1. 自回显
        if self._bot_open_id and msg.user_id == self._bot_open_id:
            return False
        # 2. 机器人策略
        if msg.is_bot and not self.config.allow_bots:
            return False
        # 3. 私聊直接放行（不受群组策略与 @提及影响）
        if msg.chat_type == "dm":
            return True
        # 4. 群组：管理员永远放行
        policy = self.config.group_policy
        if msg.user_id in policy.admin_list:
            return True
        # 5. 群组策略
        if policy.mode == "disabled":
            return False
        if policy.mode == "allowlist" and msg.chat_id not in policy.allowlist:
            return False
        if policy.mode == "blacklist" and msg.chat_id in policy.blacklist:
            return False
        # 6. @提及门控
        return not self.config.require_mention or mentioned_bot

    async def listen(self) -> AsyncIterator[InboundMessage]:
        """异步迭代入站消息

        阻塞在 queue.get() 等待消息，shutdown() 调用 queue.shutdown() 投递哨兵
        唤醒 getter 并抛 QueueShutDown，本方法捕获后退出循环。
        """
        while True:
            try:
                msg = await self._queue.get()
            except QueueShutDown:
                break
            yield msg

    async def send_text(self, chat_id: str, text: str, *, reply_to: str = "") -> str:
        """发送交互卡片消息（统一用卡片承载，支持 markdown 渲染）

        卡片内用 markdown 元素，飞书客户端渲染表格/代码块/列表等。
        卡片可通过 edit_message（patch）无限次更新，适合流式输出。

        Args:
            chat_id: 目标会话
            text: 文本内容（可含 markdown）
            reply_to: 要回复的消息 ID（可选）

        Returns:
            str: 新消息 ID
        """
        from illusion.channels.feishu.messaging import send_card
        return await send_card(self._client, chat_id, text, reply_to=reply_to)

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """更新卡片内容（流式编辑）

        卡片用 message.patch 更新，无编辑次数限制（不像 text 的 230072）。

        Args:
            chat_id: 会话标识（卡片 patch 不需要，保留接口兼容）
            message_id: 要更新的卡片消息 ID
            text: 新的卡片内容（markdown）
        """
        from illusion.channels.feishu.messaging import patch_card
        await patch_card(self._client, message_id, text)

    async def send_file(self, chat_id: str, file_path: str, *, reply_to: str = "") -> None:
        """发送文件

        Args:
            chat_id: 目标会话
            file_path: 本地文件路径
            reply_to: 引用的消息 ID（飞书 file API 不需要，保留以兼容基类签名）
        """
        from illusion.channels.feishu.messaging import send_file as _send_f
        await _send_f(self._client, self.config, chat_id, file_path)

    async def send_image(
        self, chat_id: str, image_path: str, *, caption: str = "", reply_to: str = ""
    ) -> str:
        """发送图片到飞书会话

        流程：上传图片获取 image_key → 发送图片消息

        Args:
            chat_id: 目标会话
            image_path: 本地图片文件路径
            caption: 可选附注文字

        Returns:
            str: 新消息 ID
        """
        import io
        import json
        import os

        from illusion.channels.feishu.messaging import resolve_receive_id

        try:
            from lark_oapi.api.im.v1 import (
                CreateImageRequest,
                CreateImageRequestBody,
                CreateMessageRequest,
            )
        except ImportError:
            raise NotImplementedError("feishu requires lark_oapi for send_image")

        loop = asyncio.get_running_loop()
        # 上传图片（图片可能很大，用 to_thread 避免阻塞事件循环）
        from pathlib import Path
        image_bytes = await asyncio.to_thread(Path(image_path).read_bytes)
        image_file = io.BytesIO(image_bytes)
        image_file.name = os.path.basename(image_path)

        body = (
            CreateImageRequestBody.builder()
            .image_type("message")
            .image(image_file)
            .build()
        )
        req = CreateImageRequest.builder().request_body(body).build()
        resp = await loop.run_in_executor(_feishu_executor, self._client.im.v1.image.create, req)
        if not resp.success():
            raise RuntimeError(f"飞书图片上传失败: code={resp.code} msg={resp.msg}")

        image_key = getattr(getattr(resp, "data", None), "image_key", "")
        if not image_key:
            raise RuntimeError("飞书图片上传未返回 image_key")

        # 发送消息
        receive_id, receive_id_type = resolve_receive_id(chat_id)
        if caption:
            post_content = {
                "zh_cn": {
                    "title": "",
                    "content": [[
                        {"tag": "img", "image_key": image_key},
                        {"tag": "text", "text": caption},
                    ]]
                }
            }
            msg_body = {
                "receive_id": receive_id,
                "msg_type": "post",
                "content": json.dumps(post_content, ensure_ascii=False),
            }
        else:
            msg_body = {
                "receive_id": receive_id,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
            }

        req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(msg_body)  # pyright: ignore[reportArgumentType]
            .build()
        )
        resp = await loop.run_in_executor(_feishu_executor, self._client.im.v1.message.create, req)
        if not resp.success():
            raise RuntimeError(f"飞书消息发送失败: code={resp.code} msg={resp.msg}")

        return str(getattr(getattr(resp, "data", None), "message_id", ""))

    async def send_document(
        self, chat_id: str, file_path: str, *, caption: str = "", reply_to: str = ""
    ) -> str:
        """发送文件到飞书会话

        复用 feishu.messaging.send_file，按扩展名路由 file_type
        （pdf/doc/xls/ppt/opus/mp4/stream），避免硬编码 "stream" 导致
        部分文件类型上传后 file_key 与实际类型不匹配，发送失败。

        Args:
            chat_id: 目标会话
            file_path: 本地文件路径
            caption: 可选附注文字
            reply_to: 未使用，保留以兼容基类签名

        Returns:
            str: 新消息 ID
        """
        from illusion.channels.feishu.messaging import send_file

        return await send_file(
            self._client, self.config, chat_id, file_path, caption=caption,
        )

    async def download_attachment(
        self, attachment: Attachment, save_path: str
    ) -> str:
        """下载飞书附件到本地

        飞书下载资源：
            - 图片：GET /im/v1/images/:image_key（仅需 image_key）
            - 文件：GET /im/v1/messages/:message_id/resources/:file_key（需 message_id + file_key）

        入站时 file 附件在 file_key 中编码 message_id，格式 "message_id|file_key"；
        image 附件仅存 image_key（图片下载无需 message_id）。

        Args:
            attachment: 附件对象
            save_path: 本地保存路径

        Returns:
            str: 实际保存路径
        """
        from pathlib import Path

        save_path_obj = Path(save_path)
        save_path_obj.parent.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_running_loop()
        if attachment.media_type == "image":
            # 图片：im.v1.image.get 仅需 image_key
            try:
                from lark_oapi.api.im.v1 import GetImageRequest
            except ImportError:
                raise NotImplementedError(
                    "feishu requires lark_oapi for download_attachment"
                )

            req = GetImageRequest.builder().image_key(attachment.file_key).build()
            resp = await loop.run_in_executor(_feishu_executor, self._client.im.v1.image.get, req)
        else:
            # 文件：im.v1.message_resource.get 需 message_id + file_key
            try:
                from lark_oapi.api.im.v1 import GetMessageResourceRequest
            except ImportError:
                raise NotImplementedError(
                    "feishu requires lark_oapi for download_attachment"
                )

            # file_key 编码格式 "message_id|file_key"
            raw = attachment.file_key
            if "|" in raw:
                message_id, file_key = raw.split("|", 1)
            else:
                message_id, file_key = "", raw
            if not message_id:
                raise RuntimeError(
                    "飞书文件下载需要 message_id，附件 file_key 未编码 message_id"
                )
            req = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type("file")
                .build()
            )
            resp = await loop.run_in_executor(
                _feishu_executor, self._client.im.v1.message_resource.get, req,
            )

        if not resp.success():
            raise RuntimeError(f"飞书附件下载失败: code={resp.code} msg={resp.msg}")

        raw_file = getattr(resp, "file", None)
        if raw_file is None:
            raise RuntimeError("飞书下载未返回文件数据")

        # 下载的附件可能很大，用 to_thread 写盘避免阻塞事件循环
        data = raw_file.read() if hasattr(raw_file, "read") else bytes(raw_file)
        await asyncio.to_thread(save_path_obj.write_bytes, data)
        return str(save_path_obj)

    async def shutdown(self) -> None:
        """关闭渠道

        复用 _cleanup_resources 统一清理。先关闭入站队列唤醒 listen 协程，
        再清理 WS 资源。
        """
        self._stop_event.set()
        self._queue.shutdown()  # 投递哨兵唤醒 listen 协程
        await self._cleanup_resources()


def _extract_text(content: str) -> str:
    """从飞书消息 content JSON 提取纯文本

    Args:
        content: content JSON 字符串

    Returns:
        str: 纯文本
    """
    import json
    try:
        data = json.loads(content)
        return str(data.get("text", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


def _extract_field(content: str, key: str) -> str:
    """从飞书消息 content JSON 提取指定字段值（如 image_key/file_key/file_name）

    Args:
        content: content JSON 字符串
        key: 要提取的键名

    Returns:
        str: 键值，解析失败或不存在返回空串
    """
    import json
    try:
        data = json.loads(content)
        return str(data.get(key, "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return ""
