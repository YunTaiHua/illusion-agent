"""飞书消息收发
================

封装飞书消息的发送、编辑、文件上传等操作，基于 lark-oapi SDK。

渲染策略：
    - 含 markdown 表格 → 纯 text（飞书 post 的 md tag 无法渲染表格）
    - 含 markdown 特征 → post 富文本，内含 {tag:md} 元素让飞书客户端渲染
    - 无 markdown 特征 → 纯 text

所有方法均延迟导入 lark_oapi，确保未安装 SDK 时模块可导入。
所有 lark-oapi SDK 同步调用均通过 loop.run_in_executor(_feishu_executor, ...)
包装到专用线程池，避免阻塞事件循环且不与其他 to_thread 任务争抢线程。

函数说明：
    - build_lark_client: 构造飞书 lark 客户端
    - build_outbound_payload: 渲染决策（text/post）
    - send_text: 发送文本消息
    - edit_message: 编辑消息（流式编辑）
    - send_file: 上传并发送文件
    - resolve_receive_id: 解析 chat_id 到 receive_id_type
"""
from __future__ import annotations

import asyncio  # 异步
import json  # JSON 构造
import logging  # 日志
import re  # markdown 探测
from pathlib import Path  # 路径
from typing import TYPE_CHECKING, Any  # 类型

from illusion.channels.feishu.adapter import _feishu_executor  # 飞书 SDK 专用线程池

if TYPE_CHECKING:
    from illusion.channels.config import FeishuChannelConfig  # 配置

logger = logging.getLogger(__name__)  # 日志器

# 飞书文件上传类型路由表
# 飞书 im.v1.file.create 要求 file_type 按扩展名分类，且必须与发送消息时的
# msg_type 严格匹配（飞书错误码 230055）：
#   - pdf/doc/xls/ppt：飞书原生支持的办公文档类型，msg_type=file
#   - opus：音频流，msg_type=audio
#   - mp4：视频流，msg_type=media
#   - stream：兜底类型（.txt/.md/.json/.zip 等），msg_type=file
# 注意：media tag（post 富文本）仅支持视频文件，对 stream/doc 等类型会报 230055。
_FEISHU_DOC_UPLOAD_TYPES: dict[str, str] = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}
_FEISHU_OPUS_UPLOAD_EXTENSIONS = {".ogg", ".opus"}
_FEISHU_MEDIA_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".avi", ".m4v"}
_FEISHU_FILE_UPLOAD_TYPE_DEFAULT = "stream"


def _resolve_feishu_file_routing(file_name: str) -> tuple[str, str]:
    """根据文件扩展名解析飞书 (file_type, msg_type)

    飞书要求 file_type 与 msg_type 严格匹配，否则报 code=230055：
        - .ogg/.opus → ("opus", "audio")
        - .mp4/.mov/.avi/.m4v → ("mp4", "media")
        - .pdf/.doc/.docx/.xls/.xlsx/.ppt/.pptx → (对应类型, "file")
        - 其他 → ("stream", "file")

    Args:
        file_name: 文件名（含扩展名）

    Returns:
        tuple[str, str]: (file_type, msg_type)
    """
    from pathlib import Path

    ext = Path(file_name).suffix.lower()
    if ext in _FEISHU_OPUS_UPLOAD_EXTENSIONS:
        return "opus", "audio"
    if ext in _FEISHU_MEDIA_UPLOAD_EXTENSIONS:
        return "mp4", "media"
    if ext in _FEISHU_DOC_UPLOAD_TYPES:
        return _FEISHU_DOC_UPLOAD_TYPES[ext], "file"
    return _FEISHU_FILE_UPLOAD_TYPE_DEFAULT, "file"


# 飞书域名映射
_DOMAINS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}

# ─── Markdown 探测正则 ───────────────────────────────────────────────
# markdown 特征：标题/列表/有序列表/分隔线/代码块/行内代码/粗体/删除线/下划线/斜体/链接/引用
_MARKDOWN_HINT_RE = re.compile(
    r"(^#{1,6}\s)|(^\s*[-*]\s)|(^\s*\d+\.\s)|(^\s*---+\s*$)|"
    r"(```)|(`[^`\n]+`)|(\*\*[^*\n].+?\*\*)|(~~[^~\n].+?~~)|"
    r"(<u>.+?</u>)|(\*[^*\n]+\*)|(\[[^\]]+\]\([^)]+\))|(^>\s)",
    re.MULTILINE,
)
# 表格探测：一行 |...| 紧跟一行分隔符 |---|
_MARKDOWN_TABLE_RE = re.compile(r"^\|.*\|\n\|[-|: ]+\|", re.MULTILINE)
# 代码块围栏
_MARKDOWN_FENCE_OPEN_RE = re.compile(r"^```([^\n`]*)\s*$")
_MARKDOWN_FENCE_CLOSE_RE = re.compile(r"^```\s*$")
# post 内容格式错误（降级兜底用）
_POST_CONTENT_INVALID_RE = re.compile(
    r"content format of the post type is incorrect", re.IGNORECASE
)
# 降级剥离用的正则
_RE_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_RE_ITALIC_STAR = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_RE_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_RE_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def build_lark_client(cfg: FeishuChannelConfig) -> Any:
    """构造飞书 lark 客户端

    Args:
        cfg: 飞书渠道配置

    Returns:
        lark.Client 实例
    """
    import lark_oapi as lark  # 延迟导入
    return (
        lark.Client.builder()
        .app_id(cfg.app_id)
        .app_secret(cfg.app_secret)
        .domain(_DOMAINS.get(cfg.domain, _DOMAINS["feishu"]))
        .build()
    )


def resolve_receive_id(chat_id: str) -> tuple[str, str]:
    """解析 chat_id 到 (receive_id, receive_id_type)

    路由规则：
    - ou_ 前缀 → open_id 类型
    - 其他 → chat_id 类型

    Args:
        chat_id: 原始会话标识

    Returns:
        tuple[str, str]: (receive_id, receive_id_type)
    """
    if chat_id.startswith("ou_"):
        return chat_id, "open_id"
    return chat_id, "chat_id"


def build_outbound_payload(text: str) -> tuple[str, str]:
    """渲染决策：根据内容选择 text 或 post 消息类型

    根据内容选择 text 或 post 消息类型：
    1. 含 markdown 表格 → 强制 text（飞书 post md tag 无法渲染表格）
    2. 含 markdown 特征 → post 富文本（用 md magic tag 让飞书渲染）
    3. 无 markdown 特征 → 纯 text

    Args:
        text: 待发送文本

    Returns:
        tuple[str, str]: (msg_type, content_json)
    """
    # 1. 表格优先降级为纯文本（飞书 post 渲染表格会空白）
    if _MARKDOWN_TABLE_RE.search(text):
        return "text", json.dumps({"text": text}, ensure_ascii=False)
    # 2. markdown 特征用 post 富文本
    if _MARKDOWN_HINT_RE.search(text):
        return "post", _build_markdown_post_payload(text)
    # 3. 纯文本
    return "text", json.dumps({"text": text}, ensure_ascii=False)


def _build_markdown_post_payload(content: str) -> str:
    """构造飞书 post 富文本 payload

    使用飞书 post 的 md magic tag，让飞书客户端自己渲染 markdown。
    含代码块时按 fence 行切分（否则代码块后的正文会被飞书吞掉）。

    Args:
        content: markdown 文本

    Returns:
        str: post content JSON
    """
    rows = _build_markdown_post_rows(content)
    return json.dumps({"zh_cn": {"content": rows}}, ensure_ascii=False)


def _build_markdown_post_rows(content: str) -> list[list[dict[str, str]]]:
    """把 markdown 切分为飞书 post 的行（每行一个 md 元素）

    飞书 post 的 md 元素有一个 bug：当 md 元素内同时含围栏代码块和后续正文时，
    飞书会把代码块后的内容吞掉。解决：按真实 fence 行切分成多个独立 row。

    Args:
        content: markdown 文本

    Returns:
        list[list[dict]]: post content 结构（行的列表，每行是元素列表）
    """
    if not content:
        return [[{"tag": "md", "text": ""}]]
    # 无代码块：单个 md 元素即可
    if "```" not in content:
        return [[{"tag": "md", "text": content}]]

    rows: list[list[dict[str, str]]] = []
    current: list[str] = []
    in_code_block = False

    def _flush() -> None:
        """把累积的行 flush 成一个独立的 row"""
        if current:
            text = "\n".join(current)
            if text.strip():
                rows.append([{"tag": "md", "text": text}])
            current.clear()

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if in_code_block:
            # 代码块内：检测关闭 fence
            is_fence = bool(_MARKDOWN_FENCE_CLOSE_RE.match(stripped))
        else:
            # 代码块外：检测开启 fence
            is_fence = bool(_MARKDOWN_FENCE_OPEN_RE.match(stripped))

        if is_fence:
            if not in_code_block:
                _flush()  # 代码块前的 prose 独立成 row
            current.append(raw_line)
            in_code_block = not in_code_block
            if not in_code_block:
                _flush()  # 代码块独立成 row
            continue
        current.append(raw_line)

    _flush()  # 收尾
    return rows or [[{"tag": "md", "text": content}]]


def _strip_markdown_to_plain_text(text: str) -> str:
    """把 markdown 剥离为纯文本（post 降级兜底用）

    Args:
        text: markdown 文本

    Returns:
        str: 剥离后的纯文本
    """
    text = _RE_BOLD.sub(r"\1", text)  # **粗体** → 粗体
    text = _RE_ITALIC_STAR.sub(r"\1", text)  # *斜体* → 斜体
    text = _RE_INLINE_CODE.sub(r"\1", text)  # `代码` → 代码
    text = _RE_HEADING.sub("", text)  # ## 标题 → 标题
    text = _RE_LINK.sub(r"\1", text)  # [文本](url) → 文本
    return text.strip()


async def send_text(client: Any, cfg: FeishuChannelConfig, chat_id: str,
                    text: str, *, reply_to: str = "") -> str:
    """发送文本消息，返回新消息 ID

    根据内容自动选择 text 或 post 格式。
    post 发送失败（内容格式错误）时自动降级为纯文本。

    Args:
        client: lark 客户端
        cfg: 渠道配置
        chat_id: 目标会话
        text: 文本内容
        reply_to: 要回复的消息 ID（可选）

    Returns:
        str: 新消息 ID
    """
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
    )

    receive_id, receive_id_type = resolve_receive_id(chat_id)

    # 空内容直接拒绝（飞书会返回 230001）
    if not text or not text.strip():
        raise ValueError("飞书消息内容为空")

    # 清理可能引发飞书校验失败的控制字符（保留换行 tab）
    clean_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    loop = asyncio.get_running_loop()
    msg_type, content = build_outbound_payload(clean_text)
    body = {"receive_id": receive_id, "msg_type": msg_type, "content": content}
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(body)  # pyright: ignore[reportArgumentType]
        .build()
    )
    resp = await loop.run_in_executor(_feishu_executor, client.im.v1.message.create, req)
    if resp.success():
        return resp.data.message_id  # type: ignore[no-any-return]

    # post 内容格式错误时降级为纯文本
    err_msg = str(getattr(resp, "msg", ""))
    if msg_type == "post" and _POST_CONTENT_INVALID_RE.search(err_msg):
        logger.info("post 内容格式错误，降级为纯文本重发")
        plain = _strip_markdown_to_plain_text(clean_text)
        body = {"receive_id": receive_id, "msg_type": "text",
                "content": json.dumps({"text": plain}, ensure_ascii=False)}
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body)  # pyright: ignore[reportArgumentType]
            .build()
        )
        resp = await loop.run_in_executor(_feishu_executor, client.im.v1.message.create, req)
        if resp.success():
            return resp.data.message_id  # type: ignore[no-any-return]

    raise RuntimeError(f"飞书发送失败: code={resp.code} msg={resp.msg}")


async def edit_message(client: Any, chat_id: str, message_id: str, text: str) -> None:
    """编辑已发送消息（用于流式编辑）

    text 类型消息用 update 接口编辑。
    流式编辑始终用纯 text 格式（避免 post 格式的频繁切换与校验开销），
    最终完整消息（含 markdown）由 finalize 路径重新发送 post。

    失败时记日志（可能限流），不抛异常以免中断流式。

    Args:
        client: lark 客户端
        chat_id: 会话标识（仅用于日志）
        message_id: 要编辑的消息 ID
        text: 新文本
    """
    from lark_oapi.api.im.v1 import (
        UpdateMessageRequest,
    )

    loop = asyncio.get_running_loop()
    # 流式编辑用纯 text 格式（update 接口只支持 text）
    content = json.dumps({"text": text}, ensure_ascii=False)
    req = (
        UpdateMessageRequest.builder()
        .message_id(message_id)
        .request_body({"msg_type": "text", "content": content})  # pyright: ignore[reportArgumentType]
        .build()
    )
    resp = await loop.run_in_executor(_feishu_executor, client.im.v1.message.update, req)
    if not resp.success():
        # 230072 = 编辑次数超限（飞书硬限制），属预期，finalize 会新建消息补全
        if resp.code == 230072:
            logger.info("飞书消息编辑次数超限（230072），等待 finalize 补全")
        else:
            logger.warning("飞书编辑消息失败（可能限流）: code=%s msg=%s", resp.code, resp.msg)


def build_card_content(text: str) -> str:
    """构造飞书交互卡片 content JSON（JSON 2.0 结构）

    必须显式声明 schema:"2.0"，飞书才按 2.0 解析——否则默认 1.0，
    而 1.0 的 markdown 标签不支持标题/表格/代码块渲染。
    2.0 的 markdown 组件支持 CommonMark 标准语法（含表格/代码块/列表/标题）。

    Args:
        text: 完整文本（可含 markdown）

    Returns:
        str: 卡片 content JSON 字符串
    """
    # 净化图片 URL（避免飞书 200570 错误）
    text = _sanitize_card_text(text)
    card = {
        "schema": "2.0",  # 必须显式声明，否则默认 1.0（不支持表格/标题/代码块）
        "body": {
            "elements": [
                {"tag": "markdown", "content": text},
            ],
        },
    }
    return json.dumps(card, ensure_ascii=False)


async def send_card(client: Any, chat_id: str, text: str, *, reply_to: str = "") -> str:
    """发送交互卡片消息，返回新消息 ID

    卡片用 markdown 元素渲染，支持表格/代码块等富文本。
    卡片可通过 patch_card 无限次更新（无 230072 限制），适合流式输出。

    Args:
        client: lark 客户端
        chat_id: 目标会话
        text: 卡片内容（markdown）
        reply_to: 要回复的消息 ID（可选）

    Returns:
        str: 新消息 ID
    """
    from lark_oapi.api.im.v1 import CreateMessageRequest

    loop = asyncio.get_running_loop()
    receive_id, receive_id_type = resolve_receive_id(chat_id)
    content = build_card_content(text)
    body = {"receive_id": receive_id, "msg_type": "interactive", "content": content}
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(body)  # pyright: ignore[reportArgumentType]
        .build()
    )
    resp = await loop.run_in_executor(_feishu_executor, client.im.v1.message.create, req)
    if not resp.success():
        raise RuntimeError(f"飞书卡片发送失败: code={resp.code} msg={resp.msg}")
    return resp.data.message_id  # type: ignore[no-any-return]


async def patch_card(client: Any, message_id: str, text: str) -> None:
    """更新已发送的卡片内容（流式编辑核心）

    卡片的 message.patch 接口无编辑次数限制（不像 text 的 230072），
    适合流式输出过程中反复更新。

    Args:
        client: lark 客户端
        message_id: 要更新的卡片消息 ID
        text: 新的卡片内容（markdown）
    """
    from lark_oapi.api.im.v1 import PatchMessageRequest

    loop = asyncio.get_running_loop()
    content = build_card_content(text)
    req = (
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body({"content": content})  # pyright: ignore[reportArgumentType]
        .build()
    )
    resp = await loop.run_in_executor(_feishu_executor, client.im.v1.message.patch, req)
    if not resp.success():
        logger.warning("飞书卡片更新失败: code=%s msg=%s", resp.code, resp.msg)


async def send_file(
    client: Any,
    cfg: FeishuChannelConfig,
    chat_id: str,
    file_path: str,
    *,
    caption: str = "",
) -> str:
    """上传并发送文件到飞书会话

    流程：按扩展名路由 (file_type, msg_type) → im.v1.file.create 上传 →
    CreateMessageRequest 发送。file_type 与 msg_type 必须严格匹配：
        - opus → file_type=opus, msg_type=audio
        - mp4 → file_type=mp4, msg_type=media
        - pdf/doc/xls/ppt → file_type=对应类型, msg_type=file
        - stream（.txt/.md/.json/...） → file_type=stream, msg_type=file

    注意：media tag（post 富文本）仅支持视频文件，对 stream/doc 等类型会报 230055，
    因此 caption 不嵌入 post，而是先发一条文本消息，再发文件。

    Args:
        client: lark 客户端
        cfg: 渠道配置（未使用，保留以兼容签名）
        chat_id: 目标会话
        file_path: 本地文件路径
        caption: 可选附注文字（先于文件发送）

    Returns:
        str: 新消息 ID（文件消息 ID；发送失败抛异常）

    Raises:
        FileNotFoundError: 文件不存在
        RuntimeError: 上传失败或未返回 file_key、消息发送失败
    """
    import io
    import json
    import os

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    try:
        from lark_oapi.api.im.v1 import (  # noqa: I001
            CreateFileRequest, CreateFileRequestBody, CreateMessageRequest,
        )
    except ImportError as exc:
        raise NotImplementedError("feishu requires lark_oapi for send_file") from exc

    file_name = os.path.basename(file_path)
    file_bytes = path.read_bytes()
    file_obj = io.BytesIO(file_bytes)
    file_obj.name = file_name
    file_type, resolved_msg_type = _resolve_feishu_file_routing(file_name)
    logger.info(
        "发送文件到飞书 %s: %s file_type=%s msg_type=%s size=%d",
        chat_id, file_name, file_type, resolved_msg_type, len(file_bytes),
    )

    loop = asyncio.get_running_loop()

    # 上传文件
    body = (
        CreateFileRequestBody.builder()
        .file_type(file_type)
        .file_name(file_name)
        .file(file_obj)
        .build()
    )
    req = CreateFileRequest.builder().request_body(body).build()
    resp = await loop.run_in_executor(_feishu_executor, client.im.v1.file.create, req)
    if not resp.success():
        log_id = getattr(resp, "get_log_id", lambda: "")()
        logger.error(
            "飞书文件上传失败: code=%s msg=%s log_id=%s file_type=%s",
            resp.code, resp.msg, log_id, file_type,
        )
        raise RuntimeError(f"飞书文件上传失败: code={resp.code} msg={resp.msg}")

    file_key = getattr(getattr(resp, "data", None), "file_key", "")
    if not file_key:
        logger.error("飞书文件上传未返回 file_key: resp=%s", resp)
        raise RuntimeError("飞书文件上传未返回 file_key")

    receive_id, receive_id_type = resolve_receive_id(chat_id)

    # 有 caption 时先发一条文本消息（media tag 仅支持视频，不能用于携带 caption）
    if caption:
        try:
            await send_text(client, cfg, chat_id, caption)
        except (RuntimeError, OSError, AttributeError, TypeError, ValueError) as exc:
            logger.warning("飞书 caption 发送失败（继续发文件）: %s", exc)

    # 发送文件消息：msg_type 严格按路由结果（audio/media/file）
    msg_body = {
        "receive_id": receive_id,
        "msg_type": resolved_msg_type,
        "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
    }
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(msg_body)  # pyright: ignore[reportArgumentType]
        .build()
    )
    resp = await loop.run_in_executor(_feishu_executor, client.im.v1.message.create, req)
    if not resp.success():
        log_id = getattr(resp, "get_log_id", lambda: "")()
        logger.error(
            "飞书消息发送失败: code=%s msg=%s log_id=%s receive_id_type=%s msg_type=%s file_type=%s",
            resp.code, resp.msg, log_id, receive_id_type, resolved_msg_type, file_type,
        )
        raise RuntimeError(f"飞书消息发送失败: code={resp.code} msg={resp.msg}")

    return str(getattr(getattr(resp, "data", None), "message_id", ""))


# ---------------------------------------------------------------------------
# CardKit 流式卡片构造
# ---------------------------------------------------------------------------

STREAMING_ELEMENT_ID = "streaming_content"
LOADING_ICON_ELEMENT_ID = "loading_icon"
_LOADING_ICON_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"

# 匹配 markdown 图片语法 ![alt](src)，其中 src 以 http 开头（非飞书 img_key）
_MD_IMAGE_URL_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^\)]+)\)")


def _sanitize_card_text(text: str) -> str:
    """净化卡片文本中的无效图片引用

    飞书卡片 markdown 会把 ![alt](src) 中的 src 当作 img_key，
    如果 src 是外部 URL（非飞书 img_v3_xxx 格式），会报 200570 错误。
    将 ![alt](url) 转为 [alt](url)（链接形式）。
    """
    def _replacer(m: re.Match[str]) -> str:
        alt = m.group(1)
        url = m.group(2)
        return f"[{alt}]({url})" if alt else url
    return _MD_IMAGE_URL_RE.sub(_replacer, text)


def build_streaming_card() -> str:
    """构造流式初始卡片 JSON

    schema 2.0 + streaming_mode: true + 两个 element_id 锚点：
    - streaming_content: 流式文本（text_size=normal_v2）
    - loading_icon: loading 动画（custom_icon）

    cardElement.content() 后续只更新 streaming_content 的 content 字符串。
    """
    card = {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "locales": ["zh_cn", "en_us"],
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "",
                    "text_align": "left",
                    "text_size": "normal_v2",
                    "element_id": STREAMING_ELEMENT_ID,
                },
                {
                    "tag": "markdown",
                    "content": " ",
                    "icon": {
                        "tag": "custom_icon",
                        "img_key": _LOADING_ICON_KEY,
                        "size": "16px 16px",
                    },
                    "element_id": LOADING_ICON_ELEMENT_ID,
                },
            ],
        },
    }
    return json.dumps(card, ensure_ascii=False)


def build_complete_card(
    text: str,
    reasoning_text: str = "",
    elapsed_ms: int = 0,
    is_error: bool = False,
    show_reasoning: bool = True,
) -> str:
    """构造终态卡片 JSON

    结构：
    - [可选] collapsible_panel: 思考过程折叠面板（默认折叠，notation 小字号）
    - markdown: 主体回复
    - markdown: footer（耗时 / 错误标识）

    Args:
        text: 主体回复文本
        reasoning_text: 思考过程文本（为空则不渲染折叠面板）
        elapsed_ms: 总耗时（毫秒）
        is_error: 是否错误终态
        show_reasoning: 是否显示思考过程折叠面板
    """
    elements: list[dict[str, Any]] = []

    # 净化文本中的 markdown 图片 URL（避免飞书 200570 错误）
    text = _sanitize_card_text(text)

    if reasoning_text and show_reasoning:
        elapsed_s = elapsed_ms / 1000.0
        title = f"💭 Thought for {elapsed_s:.1f}s"
        elements.append({
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {
                "title": {"tag": "markdown", "content": title},
            },
            "border": {"color": "grey", "corner_radius": "5px"},
            "vertical_spacing": "8px",
            "padding": "8px 8px 8px 8px",
            "elements": [
                {"tag": "markdown", "content": reasoning_text, "text_size": "notation"},
            ],
        })

    elements.append({"tag": "markdown", "content": text})

    elapsed_s = elapsed_ms / 1000.0
    if is_error:
        footer = f"<font color='red'>出错 · 耗时 {elapsed_s:.1f}s</font>"
    else:
        footer = f"已完成 · 耗时 {elapsed_s:.1f}s"
    elements.append({"tag": "markdown", "content": footer, "text_size": "notation"})

    card = {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            "wide_screen_mode": True,
            "update_multi": True,
        },
        "body": {"elements": elements},
    }
    return json.dumps(card, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CardKit API 封装
# ---------------------------------------------------------------------------


async def create_card_entity(client: Any, card_content: str) -> str:
    """通过 CardKit API 创建卡片实体，返回 card_id

    Args:
        client: lark 客户端
        card_content: 卡片 JSON 字符串

    Returns:
        str: card_id（失败返回空字符串）
    """
    from lark_oapi.api.cardkit.v1 import CreateCardRequest

    loop = asyncio.get_running_loop()
    req = (
        CreateCardRequest.builder()
        .request_body({
            "type": "card_json",
            "data": card_content,
        })  # pyright: ignore[reportArgumentType]
        .build()
    )
    try:
        resp = await loop.run_in_executor(_feishu_executor, client.cardkit.v1.card.create, req)
        if not resp.success():
            logger.warning(
                "CardKit 创建卡片失败: code=%s msg=%s", resp.code, resp.msg,
            )
            return ""
        return getattr(resp.data, "card_id", "") or ""
    except (RuntimeError, OSError, AttributeError, TypeError) as exc:
        logger.warning("CardKit 创建卡片异常: %s", exc)
        return ""


async def send_card_by_card_id(
    client: Any, chat_id: str, card_id: str, *, reply_to: str = "",
) -> str:
    """通过 card_id 引用发送卡片消息，返回 message_id

    content 格式: {"type":"card","data":{"card_id":"xxx"}}

    Args:
        client: lark 客户端
        chat_id: 目标会话
        card_id: CardKit 卡片 ID
        reply_to: 要回复的消息 ID（可选）

    Returns:
        str: message_id（失败抛 RuntimeError）
    """
    from lark_oapi.api.im.v1 import CreateMessageRequest

    loop = asyncio.get_running_loop()
    receive_id, receive_id_type = resolve_receive_id(chat_id)
    content = json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)
    body = {"receive_id": receive_id, "msg_type": "interactive", "content": content}
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(body)  # pyright: ignore[reportArgumentType]
        .build()
    )
    resp = await loop.run_in_executor(_feishu_executor, client.im.v1.message.create, req)
    if not resp.success():
        raise RuntimeError(f"飞书卡片消息发送失败: code={resp.code} msg={resp.msg}")
    return resp.data.message_id  # type: ignore[no-any-return]


async def stream_card_element_content(
    client: Any, card_id: str, element_id: str, content: str, sequence: int,
) -> bool:
    """调用 CardKit cardElement.content() 增量更新 element

    content 是完整累积文本（非 delta），飞书端自动 diff 并渲染打字机动画。
    sequence 单调递增，用于乱序保护。

    Args:
        client: lark 客户端
        card_id: CardKit 卡片 ID
        element_id: 目标 element ID（如 streaming_content）
        content: 完整累积文本
        sequence: 单调递增序号

    Returns:
        bool: True 成功，False 失败（如 230020 限流）
    """
    from lark_oapi.api.cardkit.v1 import (
        ContentCardElementRequest,
        ContentCardElementRequestBody,
    )

    body = (
        ContentCardElementRequestBody.builder()
        .content(content)
        .sequence(sequence)
        .build()
    )
    req = (
        ContentCardElementRequest.builder()
        .card_id(card_id)
        .element_id(element_id)
        .request_body(body)
        .build()
    )
    try:
        # Python SDK 属性名是 card_element（下划线），不是 cardElement（JS SDK 驼峰）
        resp = await client.cardkit.v1.card_element.acontent(req)
        if not resp.success():
            logger.info(
                "CardKit 流式更新失败（跳帧）: code=%s msg=%s seq=%d",
                resp.code, resp.msg, sequence,
            )
            return False
        return True
    except (RuntimeError, OSError, AttributeError, TypeError) as exc:
        logger.warning("CardKit 流式更新异常: %s seq=%d", exc, sequence)
        return False


async def set_card_streaming_mode(
    client: Any, card_id: str, streaming_mode: bool, sequence: int,
) -> bool:
    """调用 CardKit card.settings() 切换流式模式

    流式卡片创建时 streaming_mode=true，终态收尾必须调用本接口设置为 False，
    否则飞书客户端仍处于流式态（loading 动画不停止、streaming_content element
    仍等待增量），会导致终态卡片渲染异常（如循环显示思考过程）。

    通过 PATCH /cards/:card_id/settings 发送 {"streaming_mode": false} + sequence。

    Args:
        client: lark 客户端
        card_id: CardKit 卡片 ID
        streaming_mode: True 开启流式，False 关闭流式
        sequence: 单调递增序号

    Returns:
        bool: True 成功，False 失败
    """
    from lark_oapi.api.cardkit.v1 import (
        SettingsCardRequest,
        SettingsCardRequestBody,
    )

    loop = asyncio.get_running_loop()
    body = (
        SettingsCardRequestBody.builder()
        .settings(json.dumps({"streaming_mode": streaming_mode}))
        .sequence(sequence)
        .build()
    )
    req = (
        SettingsCardRequest.builder()
        .card_id(card_id)
        .request_body(body)  # pyright: ignore[reportArgumentType]
        .build()
    )
    try:
        resp = await loop.run_in_executor(_feishu_executor, client.cardkit.v1.card.settings, req)
        if not resp.success():
            logger.warning(
                "CardKit 流式模式切换失败: code=%s msg=%s streaming_mode=%s",
                resp.code, resp.msg, streaming_mode,
            )
            return False
        return True
    except (RuntimeError, OSError, AttributeError, TypeError) as exc:
        logger.warning(
            "CardKit 流式模式切换异常: %s streaming_mode=%s", exc, streaming_mode,
        )
        return False


async def update_cardkit_card(
    client: Any, card_id: str, card_content: str, sequence: int,
) -> bool:
    """调用 CardKit card.update() 全卡替换

    用于终态收尾（在 set_card_streaming_mode(False) 之后调用，替换为完整卡片）。

    Args:
        client: lark 客户端
        card_id: CardKit 卡片 ID
        card_content: 新的完整卡片 JSON 字符串
        sequence: 单调递增序号

    Returns:
        bool: True 成功，False 失败
    """
    from lark_oapi.api.cardkit.v1 import UpdateCardRequest

    loop = asyncio.get_running_loop()
    req = (
        UpdateCardRequest.builder()
        .card_id(card_id)
        .request_body({
            "card": {"type": "card_json", "data": card_content},
            "sequence": sequence,
        })  # pyright: ignore[reportArgumentType]
        .build()
    )
    try:
        resp = await loop.run_in_executor(_feishu_executor, client.cardkit.v1.card.update, req)
        if not resp.success():
            logger.warning(
                "CardKit 全卡更新失败: code=%s msg=%s", resp.code, resp.msg,
            )
            return False
        return True
    except (RuntimeError, OSError, AttributeError, TypeError) as exc:
        logger.warning("CardKit 全卡更新异常: %s", exc)
        return False
