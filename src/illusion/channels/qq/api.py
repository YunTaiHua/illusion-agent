"""QQ Bot REST API 客户端
========================

封装 QQ 开放平台 API v2 的 HTTP 调用：token 管理、消息发送、文件上传。

所有函数接受 aiohttp.ClientSession，由调用方管理生命周期。

参考文档：https://bot.q.qq.com/wiki/develop/api-v2/
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, cast

import aiohttp

logger = logging.getLogger(__name__)

# ── API 端点 ──────────────────────────────────────────────────

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = f"{API_BASE}/gateway"

# ── 消息类型 ──────────────────────────────────────────────────

MSG_TYPE_TEXT = 0
MSG_TYPE_MARKDOWN = 2
MSG_TYPE_MEDIA = 7
MSG_TYPE_INPUT_NOTIFY = 6

# ── 媒体类型 ──────────────────────────────────────────────────

MEDIA_TYPE_IMAGE = 1
MEDIA_TYPE_VIDEO = 2
MEDIA_TYPE_VOICE = 3
MEDIA_TYPE_FILE = 4

# ── 流式消息（C2C stream_messages API） ──────────────────────
# C2C stream_messages API：/v2/users/{openid}/stream_messages
# 通过 stream_msg_id 标识同一条流式消息，多次 PATCH 实现打字机效果

STREAM_INPUT_MODE_REPLACE = "replace"  # 每个 chunk 全量替换消息内容
STREAM_INPUT_STATE_GENERATING = 1  # 生成中
STREAM_INPUT_STATE_DONE = 10  # 终结
STREAM_CONTENT_TYPE_MARKDOWN = "markdown"  # 内容类型

# ── 消息限制 ──────────────────────────────────────────────────

MAX_MESSAGE_LENGTH = 4000
DEDUP_WINDOW_SECONDS = 300
DEDUP_MAX_SIZE = 1000

# ── 重连参数 ──────────────────────────────────────────────────

RECONNECT_BACKOFF = [2, 5, 10, 30, 60]
MAX_RECONNECT_ATTEMPTS = 100
RATE_LIMIT_DELAY = 60

# ── 文件上传参数 ──────────────────────────────────────────────

# md5_10m 哈希取前 10,002,432 字节（QQ API 规范）
_MD5_10M_SIZE = 10_002_432
# upload_part_finish 可重试的业务错误码
_BIZ_CODE_PART_RETRYABLE = 40093001
_PART_FINISH_RETRY_INTERVAL = 1.0
_PART_FINISH_DEFAULT_TIMEOUT = 120.0
# complete_upload 失败重试次数与退避基数
_COMPLETE_UPLOAD_MAX_RETRIES = 2
_COMPLETE_UPLOAD_BASE_DELAY = 2.0

# ── Token 管理 ────────────────────────────────────────────────

# 全局 token 缓存（按 app_id 隔离）
_token_cache: dict[str, dict[str, Any]] = {}


async def ensure_token(
    session: aiohttp.ClientSession,
    app_id: str,
    client_secret: str,
) -> str:
    """获取或刷新 app_access_token

    Token 有效期 7200s，过期前 300s 自动刷新。

    Args:
        session: HTTP 会话
        app_id: 应用 ID
        client_secret: 应用密钥

    Returns:
        str: 有效的 access_token
    """
    cached = _token_cache.get(app_id)
    if cached and cached["expires_at"] > time.monotonic() + 300:
        return str(cached["token"])

    payload = {"appId": app_id, "clientSecret": client_secret}
    async with session.post(TOKEN_URL, json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json()

    token = data["access_token"]
    expires_in = int(data.get("expires_in", 7200))
    _token_cache[app_id] = {
        "token": token,
        "expires_at": time.monotonic() + expires_in,
    }
    logger.info("QQ token 已刷新，有效期 %ds", expires_in)
    return str(token)


# ── 网关 ──────────────────────────────────────────────────────

async def get_gateway_url(
    session: aiohttp.ClientSession,
    token: str,
) -> str:
    """获取 WebSocket 网关地址

    Args:
        session: HTTP 会话
        token: access_token

    Returns:
        str: wss:// 网关 URL
    """
    headers = {"Authorization": f"QQBot {token}"}
    async with session.get(GATEWAY_URL, headers=headers) as resp:
        resp.raise_for_status()
        data = await resp.json()
    url = data["url"]
    logger.info("QQ 网关地址: %s", url)
    return str(url)


# ── 消息发送 ──────────────────────────────────────────────────

def _next_msg_seq() -> int:
    """生成消息序列号（0..65535）

    QQ Bot API 要求每条消息携带 msg_seq，用于消息排序和去重。

    Returns:
        int: 序列号
    """
    time_part = int(time.time()) % 100000000
    rand = int(uuid.uuid4().hex[:4], 16)
    return (time_part ^ rand) % 65536


def _build_text_body(content: str, *, markdown: bool = False) -> dict[str, Any]:
    """构建消息请求体（不含 msg_id，由调用方补充）

    markdown=True 时使用 QQ markdown 信封（msg_type=2），
    否则使用纯文本（msg_type=0）。

    Args:
        content: 消息内容
        markdown: 是否使用 markdown 信封

    Returns:
        dict[str, Any]: 请求体（含 msg_type、msg_seq、content/markdown）
    """
    msg_seq = _next_msg_seq()
    if markdown:
        body: dict[str, Any] = {
            "markdown": {"content": content[:MAX_MESSAGE_LENGTH]},
            "msg_type": MSG_TYPE_MARKDOWN,
            "msg_seq": msg_seq,
        }
    else:
        body = {
            "content": content[:MAX_MESSAGE_LENGTH],
            "msg_type": MSG_TYPE_TEXT,
            "msg_seq": msg_seq,
        }
    return body


async def _parse_qq_response(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """解析 QQ API 响应，处理空 body 和 errcode

    QQ Bot API v2 的消息发送响应可能：
    - 成功：HTTP 200，body 为空或 {"code": 0}
    - 失败：HTTP 200 + {"code": <非0>, "message": "..."}（业务错误）
    - 失败：HTTP 4xx/5xx + 错误 body

    Args:
        resp: aiohttp 响应对象

    Returns:
        dict[str, Any]: 响应 JSON（空 body 返回空 dict）

    Raises:
        RuntimeError: 业务错误（errcode != 0）
        aiohttp.ClientResponseError: HTTP 错误
    """
    if resp.status >= 400:
        err_body = await resp.text()
        logger.error("QQ API HTTP 错误: status=%d body=%s", resp.status, err_body)
        resp.raise_for_status()

    raw = await resp.text()
    if not raw or not raw.strip():
        return {}  # QQ API 成功时可能返回空 body

    try:
        data = cast(dict[str, Any], json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        # 非 JSON 2xx 响应通常是网关错误页（HTML），不应静默成功
        logger.warning("QQ API 响应非 JSON: %s", raw[:200])
        raise RuntimeError(f"QQ API 响应非 JSON: {raw[:200]}")

    # 检查业务错误码
    code = data.get("code", 0)
    if code and code != 0:
        message = data.get("message", "unknown error")
        raise RuntimeError(f"QQ API 业务错误: code={code} message={message}")

    return data


async def send_c2c_message(
    session: aiohttp.ClientSession,
    token: str,
    openid: str,
    content: str,
    msg_id: str,
    *,
    markdown: bool = False,
) -> dict[str, Any]:
    """发送 C2C 私聊消息

    Args:
        session: HTTP 会话
        token: access_token
        openid: 用户 openid
        content: 消息内容
        msg_id: 引用的消息 ID（用于回复定位）
        markdown: 是否使用 markdown 信封（msg_type=2）

    Returns:
        dict[str, Any]: API 响应
    """
    url = f"{API_BASE}/v2/users/{openid}/messages"
    headers = {"Authorization": f"QQBot {token}"}
    body = _build_text_body(content, markdown=markdown)
    if msg_id:
        body["msg_id"] = msg_id
    async with session.post(url, headers=headers, json=body) as resp:
        return await _parse_qq_response(resp)


async def send_group_message(
    session: aiohttp.ClientSession,
    token: str,
    group_openid: str,
    content: str,
    msg_id: str,
    *,
    markdown: bool = False,
) -> dict[str, Any]:
    """发送群聊消息

    群聊 API 要求 msg_id（引用消息 ID）和 msg_seq（消息序列号）。

    Args:
        session: HTTP 会话
        token: access_token
        group_openid: 群 openid
        content: 消息内容
        msg_id: 引用的消息 ID（群聊必须）
        markdown: 是否使用 markdown 信封（msg_type=2）

    Returns:
        dict[str, Any]: API 响应
    """
    url = f"{API_BASE}/v2/groups/{group_openid}/messages"
    headers = {"Authorization": f"QQBot {token}"}
    body = _build_text_body(content, markdown=markdown)
    if msg_id:
        body["msg_id"] = msg_id
    logger.info("QQ 群聊发送: url=%s body=%s", url, body)
    async with session.post(url, headers=headers, json=body) as resp:
        return await _parse_qq_response(resp)


# ── 打字状态 ──────────────────────────────────────────────────

async def send_typing(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: str,
    msg_id: str,
) -> None:
    """发送 C2C 打字状态指示（"正在输入中…"）

    QQ API v2 通过 input_notify 消息类型实现打字状态。
    仅 C2C 私聊有效，群聊不支持。

    Args:
        session: HTTP 会话
        token: access_token
        chat_id: 目标用户 openid
        msg_id: 最近一条入站消息 ID（用于关联打字状态）
    """
    url = f"{API_BASE}/v2/users/{chat_id}/messages"
    headers = {"Authorization": f"QQBot {token}"}
    body = {
        "msg_type": MSG_TYPE_INPUT_NOTIFY,
        "msg_id": msg_id,
        "input_notify": {
            "input_type": 1,
            "input_second": 60,  # 持续 60 秒
        },
        "msg_seq": _next_msg_seq(),
    }
    async with session.post(url, headers=headers, json=body) as resp:
        if resp.status >= 400:
            logger.debug("QQ 打字状态发送失败: status=%d", resp.status)


# ── C2C 流式消息 ──────────────────────────────────────────────


async def send_c2c_stream_message(
    session: aiohttp.ClientSession,
    token: str,
    openid: str,
    *,
    content: str,
    input_state: int,
    msg_id: str,
    msg_seq: int,
    index: int,
    stream_msg_id: str = "",
    event_id: str = "",
) -> dict[str, Any]:
    """发送 C2C 流式消息分片（打字机效果）

    调用 QQ 开放平台 `/v2/users/{openid}/stream_messages` 端点，
    通过 stream_msg_id 标识同一条流式消息，多次调用实现原地全量替换。

    工作流程：
    1. 首次调用不传 stream_msg_id，服务器返回 id（流式消息 ID）
    2. 后续调用复用该 id 作为 stream_msg_id，input_state=GENERATING
    3. 终态调用 input_state=DONE 发送最后分片

    Args:
        session: HTTP 会话
        token: access_token
        openid: 用户 openid
        content: 当前全量消息内容（input_mode=replace，每次都是完整文本）
        input_state: STREAM_INPUT_STATE_GENERATING(1) 或 STREAM_INPUT_STATE_DONE(10)
        msg_id: 引用的入站消息 ID（用于被动消息定位）
        msg_seq: 消息序列号（同一流式会话内所有分片共享）
        index: 分片序号（同一会话内递增）
        stream_msg_id: 流式消息 ID（首次不传，后续复用服务器返回的 id）
        event_id: 事件 ID（必填，通常使用入站 msg_id 同值）

    Returns:
        dict[str, Any]: API 响应，首次包含 id 字段（流式消息 ID）

    Raises:
        RuntimeError: 业务错误
        aiohttp.ClientResponseError: HTTP 错误
    """
    url = f"{API_BASE}/v2/users/{openid}/stream_messages"
    headers = {"Authorization": f"QQBot {token}"}
    body: dict[str, Any] = {
        "input_mode": STREAM_INPUT_MODE_REPLACE,
        "input_state": input_state,
        "content_type": STREAM_CONTENT_TYPE_MARKDOWN,
        "content_raw": content[:MAX_MESSAGE_LENGTH],
        "msg_id": msg_id,
        "msg_seq": msg_seq,
        "index": index,
        # event_id 必填：默认直接使用入站 messageId
        "event_id": event_id or msg_id,
    }
    if stream_msg_id:
        body["stream_msg_id"] = stream_msg_id
    logger.debug(
        "QQ C2C stream: openid=%s state=%d index=%d stream_msg_id=%s len=%d",
        openid, input_state, index, stream_msg_id or "(new)", len(content),
    )
    async with session.post(url, headers=headers, json=body) as resp:
        return await _parse_qq_response(resp)


# ── 文件上传（分片） ──────────────────────────────────────────

async def upload_file(
    session: aiohttp.ClientSession,
    token: str,
    target_id: str,
    file_path: str,
    *,
    is_group: bool = False,
    file_type: int | None = None,
) -> dict[str, Any]:
    """分片上传文件（prepare → PUT parts → part_finish → complete）

    1. POST /v2/{users|groups}/{id}/upload_prepare — 获取 upload_id + presigned URLs
    2. 对每个 part：PUT 数据到 presigned URL（COS）
    3. POST /v2/{users|groups}/{id}/upload_part_finish — 确认分片（每个 part 上传后调用）
    4. POST /v2/{users|groups}/{id}/files — 完成上传，获取 file_info

    Args:
        session: HTTP 会话
        token: access_token
        target_id: openid 或 group_openid
        file_path: 本地文件路径
        is_group: 是否群聊目标
        file_type: 文件类型（MEDIA_TYPE_IMAGE=1/MEDIA_TYPE_FILE=4 等），
            None 时不发送该字段（由服务端默认）

    Returns:
        dict[str, Any]: 含 file_info 字段，用于 send_media_message
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    file_size = path.stat().st_size
    # 大文件哈希计算是 CPU+I/O 密集型，用 to_thread 避免阻塞事件循环
    hashes = await asyncio.to_thread(_compute_file_hashes, file_path, file_size)

    target_type = "groups" if is_group else "users"
    base_url = f"{API_BASE}/v2/{target_type}/{target_id}"
    headers = {"Authorization": f"QQBot {token}"}

    # Step 1: upload_prepare
    prepare_body: dict[str, Any] = {
        "file_name": path.name,
        "file_size": file_size,
        "md5": hashes["md5"],
        "sha1": hashes["sha1"],
        "md5_10m": hashes["md5_10m"],
    }
    if file_type is not None:
        prepare_body["file_type"] = file_type
    async with session.post(
        f"{base_url}/upload_prepare", headers=headers, json=prepare_body,
    ) as resp:
        prepare_data = await _read_qq_json(resp)

    upload_id = str(prepare_data.get("upload_id", ""))
    if not upload_id:
        raise RuntimeError(f"upload_prepare 响应缺少 upload_id: {prepare_data}")
    block_size = int(prepare_data.get("block_size", 0))
    raw_parts = prepare_data.get("parts") or prepare_data.get("part_list") or []
    if not raw_parts:
        raise RuntimeError(f"upload_prepare 响应缺少 parts: {prepare_data}")

    retry_timeout = float(prepare_data.get("retry_timeout", 0) or 0)
    if retry_timeout <= 0:
        retry_timeout = _PART_FINISH_DEFAULT_TIMEOUT

    logger.info(
        "QQ upload_prepare: upload_id=%s block_size=%d parts=%d",
        upload_id, block_size, len(raw_parts),
    )

    # Step 2 & 3: 上传每个分片 + upload_part_finish
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            continue
        part_index = int(raw_part.get("part_index") or raw_part.get("index") or 0)
        presigned_url = str(
            raw_part.get("presigned_url") or raw_part.get("url") or ""
        )
        part_block_size = int(raw_part.get("block_size", 0)) or block_size
        if not presigned_url:
            raise RuntimeError(f"分片 {part_index} 缺少 presigned_url")

        # part_index 从 1 开始
        offset = (part_index - 1) * block_size
        length = min(part_block_size, file_size - offset)

        # 分片读取是阻塞 I/O，用 to_thread 避免阻塞事件循环
        def _read_part(_offset: int = offset, _length: int = length) -> bytes:
            with path.open("rb") as fh:
                fh.seek(_offset)
                return fh.read(_length)
        part_data = await asyncio.to_thread(_read_part)
        part_md5 = hashlib.md5(part_data).hexdigest()

        # PUT 到 presigned URL（COS）
        put_headers = {"Content-Length": str(len(part_data))}
        async with session.put(
            presigned_url, data=part_data, headers=put_headers,
        ) as put_resp:
            if put_resp.status < 200 or put_resp.status >= 300:
                body = await put_resp.text()
                raise RuntimeError(
                    f"COS PUT 分片 {part_index} 失败: status={put_resp.status} "
                    f"body={body[:200]}"
                )

        # upload_part_finish（biz_code 40093001 可重试）
        await _upload_part_finish(
            session, base_url, headers,
            upload_id, part_index, length, part_md5, retry_timeout,
        )

    # Step 4: 完成上传（失败重试 2 次，指数退避）
    return await _complete_upload(session, base_url, headers, upload_id)


def _compute_file_hashes(file_path: str, file_size: int) -> dict[str, str]:
    """计算文件的 md5、sha1、md5_10m 哈希值。

    md5_10m 为前 _MD5_10M_SIZE 字节的 md5；文件小于该尺寸时等于全文件 md5。
    """
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    md5_10m = hashlib.md5()

    need_10m = file_size > _MD5_10M_SIZE
    bytes_read = 0

    with open(file_path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            if need_10m:
                remaining = _MD5_10M_SIZE - bytes_read
                if remaining > 0:
                    md5_10m.update(chunk[:remaining])
            bytes_read += len(chunk)

    full_md5 = md5.hexdigest()
    return {
        "md5": full_md5,
        "sha1": sha1.hexdigest(),
        "md5_10m": md5_10m.hexdigest() if need_10m else full_md5,
    }


async def _read_qq_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """解析 QQ API JSON 响应，业务错误时抛出含 biz_code 的 RuntimeError。

    响应数据可能在 data 字段内，也可能直接在顶层。
    """
    try:
        data = await resp.json(content_type=None)
    except json.JSONDecodeError:
        text = await resp.text()
        raise RuntimeError(
            f"QQ API 非 JSON 响应 (status={resp.status}): {text[:200]}"
        )

    if not isinstance(data, dict):
        return {}

    biz_code = int(data.get("code") or data.get("biz_code") or 0)
    if biz_code != 0:
        message = data.get("message", "") or data.get("msg", "")
        raise RuntimeError(f"QQ API biz_code={biz_code}: {message}")

    if resp.status >= 400:
        raise RuntimeError(f"QQ API HTTP {resp.status}: {str(data)[:200]}")

    inner = data.get("data")
    if isinstance(inner, dict):
        return inner
    return data


async def _upload_part_finish(
    session: aiohttp.ClientSession,
    base_url: str,
    headers: dict[str, str],
    upload_id: str,
    part_index: int,
    block_size: int,
    md5: str,
    retry_timeout: float,
) -> None:
    """调用 upload_part_finish，biz_code 40093001 时重试直到 retry_timeout。"""
    body = {
        "upload_id": upload_id,
        "part_index": part_index,
        "block_size": block_size,
        "md5": md5,
    }
    url = f"{base_url}/upload_part_finish"

    start = time.monotonic()
    attempt = 0
    while True:
        try:
            async with session.post(url, headers=headers, json=body) as resp:
                await _read_qq_json(resp)
            return
        except RuntimeError as exc:
            if str(_BIZ_CODE_PART_RETRYABLE) not in str(exc):
                raise
            elapsed = time.monotonic() - start
            if elapsed >= retry_timeout:
                raise RuntimeError(
                    f"upload_part_finish 重试超时"
                    f"（{retry_timeout:.0f}s，{attempt} 次重试）: {exc}"
                ) from exc
            attempt += 1
            logger.debug(
                "upload_part_finish 可重试错误，第 %d 次，elapsed=%.1fs: %s",
                attempt, elapsed, exc,
            )
            await asyncio.sleep(_PART_FINISH_RETRY_INTERVAL)


async def _complete_upload(
    session: aiohttp.ClientSession,
    base_url: str,
    headers: dict[str, str],
    upload_id: str,
) -> dict[str, Any]:
    """调用 POST /files 完成上传，失败重试 2 次，指数退避（基础延迟 2s）。"""
    body = {"upload_id": upload_id}
    url = f"{base_url}/files"

    last_exc: Exception | None = None
    for attempt in range(_COMPLETE_UPLOAD_MAX_RETRIES + 1):
        try:
            async with session.post(url, headers=headers, json=body) as resp:
                return await _read_qq_json(resp)
        except (aiohttp.ClientError, RuntimeError, OSError) as exc:
            last_exc = exc
            if attempt < _COMPLETE_UPLOAD_MAX_RETRIES:
                delay = _COMPLETE_UPLOAD_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "complete_upload 第 %d 次失败，%.1fs 后重试: %s",
                    attempt + 1, delay, exc,
                )
                await asyncio.sleep(delay)
    raise RuntimeError(
        f"complete_upload 失败（{_COMPLETE_UPLOAD_MAX_RETRIES + 1} 次尝试）: {last_exc}"
    )


async def send_media_message(
    session: aiohttp.ClientSession,
    token: str,
    target_id: str,
    file_info: str,
    *,
    is_group: bool = False,
    msg_id: str = "",
) -> str:
    """发送富媒体消息（msg_type=7）

    需先调用 upload_file 获取 file_info，再调用本方法发送。

    Args:
        session: HTTP 会话
        token: access_token
        target_id: openid 或 group_openid
        file_info: upload_file 返回的 file_info 字符串
        is_group: 是否群聊目标（群聊必须传 msg_id）
        msg_id: 引用的消息 ID（群聊被动消息必须）

    Returns:
        str: 新消息 ID（API 不返回则为空串）
    """
    target_type = "groups" if is_group else "users"
    url = f"{API_BASE}/v2/{target_type}/{target_id}/messages"
    headers = {"Authorization": f"QQBot {token}"}
    body: dict[str, Any] = {
        "content": "",
        "msg_type": MSG_TYPE_MEDIA,
        "media": {"file_info": file_info},
        "msg_seq": _next_msg_seq(),
    }
    if msg_id:
        body["msg_id"] = msg_id
    async with session.post(url, headers=headers, json=body) as resp:
        if resp.status >= 400:
            err_body = await resp.text()
            logger.error("QQ 富媒体发送失败: status=%d body=%s", resp.status, err_body)
            resp.raise_for_status()
        # 复用 _parse_qq_response 处理空 body / 非 JSON / 业务错误码
        data = await _parse_qq_response(resp)
    return str(data.get("id", ""))


async def download_file(
    session: aiohttp.ClientSession,
    token: str,
    target_id: str,
    file_info: str,
    *,
    is_group: bool = False,
) -> bytes:
    """下载文件（通过 file_info）

    QQ Bot API v2 文件下载：GET /v2/{users|groups}/{openid}/files/{file_info}

    注意：该 API 需要文件归属于当前 bot，且 file_info 在有效期内。
    实际可用性取决于 bot 权限和 API 版本，不可用时调用方应回退到 download_url。

    Args:
        session: HTTP 会话
        token: access_token
        target_id: openid 或 group_openid
        file_info: 文件标识
        is_group: 是否群聊目标

    Returns:
        bytes: 文件内容
    """
    target_type = "groups" if is_group else "users"
    url = f"{API_BASE}/v2/{target_type}/{target_id}/files/{file_info}"
    headers = {"Authorization": f"QQBot {token}"}
    async with session.get(url, headers=headers) as resp:
        if resp.status >= 400:
            err_body = await resp.text()
            logger.error("QQ 文件下载失败: status=%d body=%s", resp.status, err_body)
            resp.raise_for_status()
        return await resp.read()


# ── 文本分片（代码块感知） ────────────────────────────────────

# 匹配 ``` 围栏行（开头或闭合）
_RE_FENCE = re.compile(r"^```(\w*)", re.MULTILINE)


def split_text(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """超长文本按段落边界分片，代码块感知

    分片时追踪 ``` 围栏状态：若在代码块内切分，当前片闭合围栏，
    下一片重开围栏（保留语言标记），避免渲染错乱。

    多分片消息附带 (1/n) 编号标记。

    Args:
        text: 原始文本
        max_length: 每片最大长度

    Returns:
        list[str]: 分片列表
    """
    if len(text) <= max_length:
        return [text]

    # 预留编号空间：最多 "(99/99)" = 10 字符
    indicator_reserve = 10
    effective_max = max_length - indicator_reserve

    chunks: list[str] = []
    remaining = text
    in_code_block = False  # 追踪是否在 ``` 代码块内
    code_lang = ""  # 当前代码块的语言标记

    while remaining:
        if len(remaining) <= effective_max:
            # 最后一片：如果在代码块内需要闭合
            if in_code_block:
                chunks.append(remaining.rstrip() + "\n```")
            else:
                chunks.append(remaining)
            break

        # 在有效范围内找切分点
        cut = effective_max
        for sep in ["\n\n", "\n", "。", ".", " "]:
            pos = remaining.rfind(sep, 0, effective_max)
            if pos > effective_max // 2:
                cut = pos + len(sep)
                break

        chunk = remaining[:cut]
        remaining_after = remaining[cut:]

        # 统计当前片中 ``` 围栏的出现次数
        fence_count = len(_RE_FENCE.findall(chunk))

        if in_code_block:
            # 已在代码块内
            if fence_count % 2 == 0:
                # 偶数个新围栏：仍在代码块内 → 闭合 + 重开
                chunk = chunk.rstrip() + "\n```"
                remaining = f"```{code_lang}\n" + remaining_after
            else:
                # 奇数个新围栏：代码块已自然关闭
                in_code_block = False
                code_lang = ""
                remaining = remaining_after
        else:
            # 不在代码块内
            if fence_count % 2 == 0:
                # 偶数个新围栏：仍在代码块外
                remaining = remaining_after
            else:
                # 奇数个新围栏：代码块已开启 → 闭合 + 重开
                in_code_block = True
                # 记录最后一个围栏的语言标记
                last_fence = None
                for m in _RE_FENCE.finditer(chunk):
                    last_fence = m
                if last_fence:
                    code_lang = last_fence.group(1)
                chunk = chunk.rstrip() + "\n```"
                remaining = f"```{code_lang}\n" + remaining_after

        chunks.append(chunk)

    # 添加分片编号
    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"{c}\n\n({i+1}/{total})" for i, c in enumerate(chunks)]

    return chunks


# ── Markdown 剥离 ─────────────────────────────────────────────

# 预编译正则（用于纯文本回退模式）
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_RE_ITALIC_STAR = re.compile(r"\*(.+?)\*")
_RE_BOLD_UNDER = re.compile(r"__(.+?)__", re.DOTALL)
_RE_ITALIC_UNDER = re.compile(r"_(.+?)_")
_RE_CODE_BLOCK = re.compile(r"```[a-zA-Z0-9_+-]*\n?", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"`(.+?)`")
_RE_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")


def strip_markdown(text: str) -> str:
    """剥离 markdown 格式，用于纯文本回退模式

    Args:
        text: 含 markdown 的文本

    Returns:
        str: 纯文本
    """
    text = _RE_BOLD.sub(r"\1", text)
    text = _RE_ITALIC_STAR.sub(r"\1", text)
    text = _RE_BOLD_UNDER.sub(r"\1", text)
    text = _RE_ITALIC_UNDER.sub(r"\1", text)
    text = _RE_CODE_BLOCK.sub("", text)
    text = _RE_INLINE_CODE.sub(r"\1", text)
    text = _RE_HEADING.sub("", text)
    text = _RE_LINK.sub(r"\1", text)
    text = _RE_MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()
