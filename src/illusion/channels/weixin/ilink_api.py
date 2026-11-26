"""iLink Bot API 客户端
======================

封装腾讯 iLink Bot API 的 HTTP 调用（长轮询/收发/打字/扫码/媒体上传下载）。

所有 HTTP 调用延迟导入 aiohttp，确保未安装依赖时模块可导入。
AES 加解密延迟导入 cryptography，未安装时媒体功能不可用但不影响文本收发。

API 端点：
    - ilink/bot/get_bot_qrcode: 获取登录二维码
    - ilink/bot/get_qrcode_status: 轮询扫码状态
    - ilink/bot/getupdates: 长轮询拉取新消息
    - ilink/bot/sendmessage: 发送消息（文本/图片/视频/文件/语音）
    - ilink/bot/sendtyping: 发送打字状态
    - ilink/bot/getconfig: 获取打字 ticket
    - ilink/bot/getuploadurl: 获取 CDN 上传 URL

CDN：
    - 媒体下载: GET {cdn_base_url}/download?encrypted_query_param=...
    - 媒体上传: POST {upload_url}，响应头 x-encrypted-param 返回加密引用
    - 所有媒体通过 AES-128-ECB 加密传输
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────────────
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0  # 131072

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_SEND_TYPING = "ilink/bot/sendtyping"
EP_GET_CONFIG = "ilink/bot/getconfig"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000
CONFIG_TIMEOUT_MS = 10_000
QR_TIMEOUT_MS = 35_000
UPLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_TIMEOUT_SECONDS = 60

# 出站媒体上传重试：CDN 上传易瞬时失败，加重试避免文件丢失
UPLOAD_MAX_ATTEMPTS = 3
UPLOAD_BACKOFF_SECONDS = (1.0, 2.0, 4.0)  # 指数退避

MAX_MESSAGE_LENGTH = 2000

# 消息类型/状态常量
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5
TYPING_START = 1
TYPING_STOP = 2

# 媒体类型（用于 getuploadurl 的 media_type 字段）
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4

# 错误码
SESSION_EXPIRED_ERRCODE = -14
RATE_LIMIT_ERRCODE = -2

# CDN 主机白名单（SSRF 防护）
_WEIXIN_CDN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "novac2c.cdn.weixin.qq.com",
        "ilinkai.weixin.qq.com",
        "wx.qlogo.cn",
        "thirdwx.qlogo.cn",
        "res.wx.qq.com",
        "mmbiz.qpic.cn",
        "mmbiz.qlogo.cn",
    }
)


def _check_crypto_available() -> bool:
    """检查 cryptography 是否可用

    Returns:
        bool: 可用返回 True
    """
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """PKCS#7 填充

    Args:
        data: 原始数据
        block_size: 块大小（默认 16，AES 标准）

    Returns:
        bytes: 填充后的数据
    """
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 加密 + PKCS#7 填充

    Args:
        plaintext: 明文
        key: 16 字节 AES 密钥

    Returns:
        bytes: 密文
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密 + PKCS#7 去填充

    Args:
        ciphertext: 密文
        key: 16 字节 AES 密钥

    Returns:
        bytes: 明文
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _aes_padded_size(size: int) -> int:
    """计算 AES-128-ECB + PKCS#7 填充后的密文长度

    Args:
        size: 明文字节数

    Returns:
        int: 密文字节数
    """
    return ((size + 1 + 15) // 16) * 16


def _parse_aes_key(aes_key_b64: str) -> bytes:
    """解析 iLink 消息载荷中的 AES 密钥

    iLink 的 aes_key 字段可能是：
        - base64(16 字节原始密钥)
        - base64(hex_string) → 32 字节解码后是 hex 字符串

    Args:
        aes_key_b64: base64 编码的 AES 密钥

    Returns:
        bytes: 16 字节 AES 密钥

    Raises:
        ValueError: 格式无法识别
    """
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format ({len(decoded)} decoded bytes)")


def _cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    """构造 CDN 下载 URL

    Args:
        cdn_base_url: CDN 基础 URL
        encrypted_query_param: 加密查询参数

    Returns:
        str: 完整下载 URL
    """
    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"


def _cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    """构造 CDN 上传 URL

    Args:
        cdn_base_url: CDN 基础 URL
        upload_param: 上传参数（来自 getuploadurl 响应）
        filekey: 文件 key

    Returns:
        str: 完整上传 URL
    """
    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )


def _assert_weixin_cdn_url(url: str) -> None:
    """SSRF 防护：校验 URL 指向微信 CDN 白名单

    Args:
        url: 待校验 URL

    Raises:
        ValueError: URL 不在白名单
    """
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
    except Exception as exc:
        raise ValueError(f"Unparseable media URL: {url!r}") from exc

    if scheme not in {"http", "https"}:
        raise ValueError(
            f"Media URL has disallowed scheme {scheme!r}; only http/https are permitted."
        )
    if host not in _WEIXIN_CDN_ALLOWLIST:
        raise ValueError(
            f"Media URL host {host!r} is not in the WeChat CDN allowlist. "
            "Refusing to fetch to prevent SSRF."
        )


@dataclass
class WeixinCredentials:
    """扫码登录后获取的微信凭据

    Attributes:
        account_id: iLink Bot 账号 ID（@im.bot 格式）
        token: 鉴权 token（Bearer）
        base_url: API 入口（可能因重定向改变）
        user_id: bot 自身 ilink user id
    """

    account_id: str
    token: str
    base_url: str
    user_id: str


def _random_wechat_uin() -> str:
    """生成随机 X-WECHAT-UIN 值（每次请求不同）

    Returns:
        str: 随机数字字符串
    """
    return str(random.randint(10**17, 10**18 - 1))


def _make_ssl_connector() -> Any:
    """创建带 certifi CA 证书的 TCPConnector（若可用）

    腾讯 iLink 服务器在部分系统 CA 商店中无法验证（如 macOS Homebrew OpenSSL）。
    安装 certifi 时使用其 Mozilla CA 证书包，否则回退到 aiohttp 默认。

    Returns:
        aiohttp.TCPConnector 或 None
    """
    try:
        import ssl

        import certifi
    except ImportError:
        return None
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    import aiohttp as _aiohttp
    return _aiohttp.TCPConnector(ssl=ssl_ctx)


def _base_info() -> dict[str, Any]:
    """iLink base_info 附加字段"""
    return {"channel_version": "2.2.0"}


def _build_headers(token: str, body: str = "") -> dict[str, str]:
    """构造 iLink API 请求头

    Args:
        token: Bearer token
        body: 请求体字符串（用于计算 Content-Length）

    Returns:
        dict[str, Any]: 请求头字典
    """
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body:
        headers["Content-Length"] = str(len(body.encode("utf-8")))
    return headers


def _is_retryable_upload_error(exc: BaseException) -> bool:
    """判断上传错误是否值得重试

    可重试：网络超时、连接错误、5xx 服务端错误。
    不重试：4xx 业务错误（请求本身有问题，重试无意义）。

    用正则从 RuntimeError 消息中提取 HTTP 状态码（如 "HTTP 400:"），
    而非子串匹配（"HTTP 4"/"HTTP 5"），避免其他 RuntimeError 消息
    碰巧包含这些子串导致误分类。

    Args:
        exc: 捕获的异常

    Returns:
        bool: 可重试返回 True
    """
    import re

    import aiohttp  # 延迟导入
    if isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError, ConnectionError)):
        return True
    if isinstance(exc, RuntimeError):
        match = re.search(r"HTTP (\d{3})", str(exc))
        if match:
            status = int(match.group(1))
            if status >= 500:
                return True  # 5xx 服务端错误，重试
            if status >= 400:
                return False  # 4xx 业务错误，不重试
    return False


async def _retry_transient(
    func: Any, *, name: str, max_attempts: int = UPLOAD_MAX_ATTEMPTS,
) -> Any:
    """对瞬时错误带指数退避重试

    Args:
        func: 无参协程工厂（每次调用返回新协程）
        name: 操作名（用于日志）
        max_attempts: 最大尝试次数

    Returns:
        func 的返回值

    Raises:
        最后一次失败的异常（若全部重试失败）
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except Exception as exc:
            if not _is_retryable_upload_error(exc) or attempt == max_attempts:
                raise
            delay = UPLOAD_BACKOFF_SECONDS[
                min(attempt - 1, len(UPLOAD_BACKOFF_SECONDS) - 1)
            ]
            logger.warning(
                "%s 第 %d/%d 次失败（%s），%gs 后重试",
                name, attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)
    # 不可达：每次迭代要么 return 要么 raise，for 循环结束后此处不会执行。
    # mypy 需要显式返回/抛出才不报警告，故保留此路径。
    raise RuntimeError(f"{name}: 所有重试均失败（此分支不应到达）")


async def _api_post(
    session: Any,
    *,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """通用 iLink API POST 调用

    所有 POST 请求自动注入 base_info 字段，手动序列化 JSON 并设置 Content-Length，
    满足 iLink API 的请求要求。

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        endpoint: 端点路径
        payload: 请求体
        token: Bearer token
        timeout_ms: 超时（毫秒）

    Returns:
        dict[str, Any]: 响应 JSON

    Raises:
        asyncio.TimeoutError: 请求超时
    """
    import aiohttp  # 延迟导入

    url = f"{base_url}/{endpoint}"
    body = json.dumps({**payload, "base_info": _base_info()}, ensure_ascii=False, separators=(",", ":"))
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.post(url, data=body, headers=_build_headers(token, body), timeout=timeout) as resp:
        raw = await resp.text()
        if not resp.ok:
            raise RuntimeError(f"iLink POST {endpoint} HTTP {resp.status}: {raw[:200]}")
        return cast(dict[str, Any], json.loads(raw))


async def _api_get(
    session: Any,
    *,
    base_url: str,
    endpoint: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """iLink API GET 调用（用于扫码登录的两个端点）

    这两个端点不需要 token，只需 iLink-App-Id 和 iLink-App-ClientVersion 头。
    响应用 response.text() + json.loads() 解析（避免 content-type 问题）。

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        endpoint: 端点路径（含 query 参数）
        timeout_ms: 超时（毫秒）

    Returns:
        dict[str, Any]: 响应 JSON
    """
    url = f"{base_url}/{endpoint}"
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }

    async def _do() -> dict[str, Any]:
        async with session.get(url, headers=headers) as response:
            raw = await response.text()
            if not response.ok:
                raise RuntimeError(f"iLink GET {endpoint} HTTP {response.status}: {raw[:200]}")
            return cast(dict[str, Any], json.loads(raw))

    return await asyncio.wait_for(_do(), timeout=timeout_ms / 1000)


async def poll_updates(
    session: Any,
    *,
    base_url: str,
    token: str,
    sync_buf: str,
    timeout_ms: int = LONG_POLL_TIMEOUT_MS,
) -> dict[str, Any]:
    """长轮询拉取新消息

    服务端 hold 请求最多 35s，有消息立即返回。
    超时返回空批次。

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        token: Bearer token
        sync_buf: 长轮询游标
        timeout_ms: 超时（毫秒）

    Returns:
        dict[str, Any]: {ret, msgs: [...], get_updates_buf: 新游标}
    """
    try:
        return await _api_post(
            session, base_url=base_url, endpoint=EP_GET_UPDATES,
            payload={"get_updates_buf": sync_buf}, token=token, timeout_ms=timeout_ms,
        )
    except TimeoutError:
        return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}


async def send_message(
    session: Any,
    *,
    base_url: str,
    token: str,
    to: str,
    text: str,
    context_token: str | None,
    client_id: str,
) -> dict[str, Any]:
    """发送文本消息

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        token: Bearer token
        to: 目标 user_id
        text: 文本内容
        context_token: 该 peer 的 context_token（iLink 硬约束，必须回传）
        client_id: 客户端消息 ID（幂等去重用）

    Returns:
        dict[str, Any]: API 响应（可能含 errcode）
    """
    if not text or not text.strip():
        raise ValueError("微信消息内容不能为空")

    message: dict[str, Any] = {
        "from_user_id": "",
        "to_user_id": to,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }
    if context_token:
        message["context_token"] = context_token

    return await _api_post(
        session, base_url=base_url, endpoint=EP_SEND_MESSAGE,
        payload={"msg": message}, token=token, timeout_ms=API_TIMEOUT_MS,
    )


async def send_typing(
    session: Any,
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    typing_ticket: str,
    status: int,
) -> None:
    """发送打字状态

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        token: Bearer token
        to_user_id: 目标 user_id
        typing_ticket: 打字 ticket（getconfig 获取）
        status: 1=开始（TYPING_START），2=停止（TYPING_STOP）
    """
    await _api_post(
        session, base_url=base_url, endpoint=EP_SEND_TYPING,
        payload={
            "ilink_user_id": to_user_id,
            "typing_ticket": typing_ticket,
            "status": status,
        },
        token=token, timeout_ms=CONFIG_TIMEOUT_MS,
    )


async def get_config(
    session: Any,
    *,
    base_url: str,
    token: str,
    context_token: str,
    ilink_user_id: str = "",
) -> dict[str, Any]:
    """获取配置（含打字 ticket）

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        token: Bearer token
        context_token: peer 的 context_token
        ilink_user_id: 目标用户的 ilink_user_id（API 要求）

    Returns:
        dict[str, Any]: 含 typing_ticket 等配置
    """
    payload: dict[str, Any] = {"context_token": context_token}
    if ilink_user_id:
        payload["ilink_user_id"] = ilink_user_id
    return await _api_post(
        session, base_url=base_url, endpoint=EP_GET_CONFIG,
        payload=payload,
        token=token, timeout_ms=CONFIG_TIMEOUT_MS,
    )


async def get_upload_url(
    session: Any,
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    media_type: int,
    filekey: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey_hex: str,
) -> dict[str, Any]:
    """获取 CDN 上传 URL

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        token: Bearer token
        to_user_id: 目标用户 ID
        media_type: 媒体类型（MEDIA_IMAGE/VIDEO/FILE/VOICE）
        filekey: 文件 key（随机 hex）
        rawsize: 原始文件字节数
        rawfilemd5: 原始文件 MD5 hex
        filesize: 加密后密文字节数
        aeskey_hex: AES 密钥的 hex 字符串

    Returns:
        dict[str, Any]: 含 upload_param 或 upload_full_url
    """
    result = await _retry_transient(
        lambda: _api_post(
            session, base_url=base_url, endpoint=EP_GET_UPLOAD_URL,
            payload={
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": rawsize,
                "rawfilemd5": rawfilemd5,
                "filesize": filesize,
                "no_need_thumb": True,
                "aeskey": aeskey_hex,
            },
            token=token, timeout_ms=API_TIMEOUT_MS,
        ),
        name="get_upload_url",
    )
    return dict(result) if result is not None else {}


async def upload_ciphertext(
    session: Any,
    *,
    ciphertext: bytes,
    upload_url: str,
) -> str:
    """上传加密媒体到 CDN

    Args:
        session: aiohttp.ClientSession
        ciphertext: 加密后的密文
        upload_url: 上传 URL（来自 getuploadurl 响应）

    Returns:
        str: 加密查询参数（encrypted_query_param），用于后续 sendmessage

    Raises:
        RuntimeError: 上传失败或响应缺少 x-encrypted-param 头
    """
    async def _do_upload() -> str:
        async with session.post(
            upload_url, data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
        ) as response:
            if response.status == 200:
                encrypted_param = response.headers.get("x-encrypted-param")
                if encrypted_param:
                    await response.read()
                    return str(encrypted_param)
                raw = await response.text()
                raise RuntimeError(f"CDN upload missing x-encrypted-param header: {raw[:200]}")
            raw = await response.text()
            raise RuntimeError(f"CDN upload HTTP {response.status}: {raw[:200]}")
    result = await _retry_transient(
        lambda: asyncio.wait_for(_do_upload(), timeout=UPLOAD_TIMEOUT_SECONDS),
        name="upload_ciphertext",
    )
    return str(result) if result is not None else ""


async def download_bytes(
    session: Any,
    *,
    url: str,
    timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    """下载字节数据

    Args:
        session: aiohttp.ClientSession
        url: 下载 URL
        timeout_seconds: 超时秒数

    Returns:
        bytes: 下载的字节内容
    """
    async def _do_download() -> bytes:
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.read()
            return bytes(data)
    return await asyncio.wait_for(_do_download(), timeout=timeout_seconds)


async def download_and_decrypt_media(
    session: Any,
    *,
    cdn_base_url: str,
    encrypted_query_param: str | None,
    aes_key_b64: str | None,
    full_url: str | None,
    timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    """下载并解密媒体文件

    Args:
        session: aiohttp.ClientSession
        cdn_base_url: CDN 基础 URL
        encrypted_query_param: 加密查询参数（优先）
        aes_key_b64: base64 编码的 AES 密钥（可选，不提供则不解密）
        full_url: 完整 URL（当 encrypted_query_param 为空时使用）
        timeout_seconds: 超时秒数

    Returns:
        bytes: 解密后的媒体内容

    Raises:
        RuntimeError: 既无 encrypted_query_param 也无 full_url
        ValueError: full_url 不在 CDN 白名单（SSRF 防护）
    """
    if encrypted_query_param:
        raw = await download_bytes(
            session,
            url=_cdn_download_url(cdn_base_url, encrypted_query_param),
            timeout_seconds=timeout_seconds,
        )
    elif full_url:
        _assert_weixin_cdn_url(full_url)
        raw = await download_bytes(session, url=full_url, timeout_seconds=timeout_seconds)
    else:
        raise RuntimeError("media item had neither encrypt_query_param nor full_url")
    if aes_key_b64:
        raw = _aes128_ecb_decrypt(raw, _parse_aes_key(aes_key_b64))
    return raw


async def get_bot_qrcode(session: Any, *, base_url: str) -> dict[str, Any]:
    """获取登录二维码

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口

    Returns:
        dict[str, Any]: 含 qrcode（hex）和 qrcode_img_content
    """
    return await _api_get(
        session, base_url=base_url,
        endpoint=f"{EP_GET_BOT_QR}?bot_type=3",
        timeout_ms=QR_TIMEOUT_MS,
    )


async def get_qrcode_status(session: Any, *, base_url: str, qrcode: str) -> dict[str, Any]:
    """轮询扫码状态

    Args:
        session: aiohttp.ClientSession
        base_url: API 入口
        qrcode: 二维码 hex token

    Returns:
        dict[str, Any]: 含 status（wait/scaned/confirmed/expired 等）
    """
    return await _api_get(
        session, base_url=base_url,
        endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode}",
        timeout_ms=QR_TIMEOUT_MS,
    )


def _split_text(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """在段落边界分片（\\n\\n），尽量不在段落中间断

    Args:
        text: 待分片文本
        max_len: 单片最大长度

    Returns:
        list[str]: 分片列表
    """
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > max_len and current:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks or [text]


async def qr_login_with_browser() -> WeixinCredentials | None:
    """完整的扫码登录流程（浏览器投射二维码）

    流程：
    1. 获取二维码
    2. 用 qrcode 库生成 PNG，启动临时 HTTP 服务投射到浏览器
    3. 轮询扫码状态（wait/scaned/scaned_but_redirect/expired/confirmed）
    4. 扫码成功后关闭服务，返回凭据

    Returns:
        WeixinCredentials | None: 凭据，扫码超时返回 None
    """
    import time as _time

    import aiohttp  # 延迟导入

    from illusion.config.i18n import t

    print(t("weixin_qr_fetching"))
    base_url = ILINK_BASE_URL
    timeout = aiohttp.ClientTimeout(total=QR_TIMEOUT_MS / 1000)
    connector = _make_ssl_connector()

    async with aiohttp.ClientSession(
        timeout=timeout, trust_env=True, connector=connector,
    ) as session:
        # 1. 获取二维码
        qr_resp = await get_bot_qrcode(session, base_url=base_url)
        logger.info("获取二维码完整响应: %s", qr_resp)  # 调试日志
        qrcode_hex = qr_resp.get("qrcode", "")  # hex token，用于轮询状态
        qr_url = qr_resp.get("qrcode_img_content", "")  # URL，用于生成二维码图片供手机扫描
        if not qrcode_hex:
            logger.error("获取二维码失败: %s", qr_resp)
            return None

        # 2. 启动浏览器二维码投射（用 URL 生成图片，微信才能识别为登录链接）
        qr_content = qr_url or qrcode_hex  # 优先用 URL
        server_info = _serve_qr_in_browser(qr_content)
        print(t("weixin_qr_waiting"))

        # 3. 轮询扫码状态（使用 status 字段判断）
        deadline = _time.monotonic() + 480  # 总超时 8 分钟
        refresh_count = 0
        max_refreshes = 3
        try:
            while _time.monotonic() < deadline:
                try:
                    status_resp = await get_qrcode_status(
                        session, base_url=base_url, qrcode=qrcode_hex,
                    )
                except TimeoutError:
                    await asyncio.sleep(1)
                    continue
                logger.info("扫码状态响应: %s", status_resp)  # 调试日志

                status = str(status_resp.get("status") or "wait")

                if status == "wait":
                    pass  # 等待扫码，继续轮询

                elif status == "scaned":
                    print(t("weixin_qr_scanned"))

                elif status == "scaned_but_redirect":
                    redirect_host = str(status_resp.get("redirect_host") or "")
                    if redirect_host:
                        base_url = f"https://{redirect_host}"
                        logger.info("扫码重定向到: %s", base_url)
                        print(t("weixin_qr_redirect"))

                elif status == "expired":
                    refresh_count += 1
                    if refresh_count > max_refreshes:
                        print(t("weixin_qr_timeout"))
                        return None
                    print(t("weixin_qr_expired"))
                    try:
                        qr_resp = await get_bot_qrcode(session, base_url=ILINK_BASE_URL)
                        qrcode_hex = qr_resp.get("qrcode", "")
                        qr_url = qr_resp.get("qrcode_img_content", "")
                        if not qrcode_hex:
                            logger.error("刷新二维码失败: %s", qr_resp)
                            return None
                        # 更新浏览器中的二维码图片
                        _refresh_qr_server(server_info, qr_url or qrcode_hex)
                    except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError, RuntimeError, ImportError, OSError) as exc:
                        logger.error("刷新二维码异常: %s", exc)
                        return None

                elif status == "confirmed":
                    account_id = str(status_resp.get("ilink_bot_id") or "")
                    token = str(status_resp.get("bot_token") or "")
                    base_url = str(status_resp.get("baseurl") or base_url)
                    user_id = str(status_resp.get("ilink_user_id") or "")
                    if not account_id or not token:
                        logger.error("扫码确认但凭据不完整: %s", status_resp)
                        return None
                    print(t("weixin_login_success"))
                    return WeixinCredentials(
                        account_id=account_id,
                        token=token,
                        base_url=base_url,
                        user_id=user_id,
                    )

                await asyncio.sleep(1)

            # 超时
            print(t("weixin_qr_timeout"))
            return None
        finally:
            server_info["server"].shutdown()


def _serve_qr_in_browser(qr_hex: str) -> dict[str, Any]:
    """启动临时 HTTP 服务投射二维码 PNG 到浏览器

    用标准库 http.server（不依赖 aiohttp——扫码阶段依赖可能未装）。

    Args:
        qr_hex: 二维码内容（hex）

    Returns:
        dict[str, Any]: 含 server/port/state，供后续刷新/关闭
    """
    import base64
    import io
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import qrcode  # 延迟导入

    # 生成二维码 PNG
    img = qrcode.make(qr_hex)
    buf = io.BytesIO()
    img.save(buf, format="PNG")  # type: ignore[call-arg]
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    # 状态容器（供刷新时更新 HTML）
    state = {"img_b64": img_b64}

    def _build_html() -> str:
        return f"""<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>微信扫码登录 - IllusionAgent</title>
<style>
  body {{ font-family: sans-serif; text-align: center; padding: 40px; background: #f5f5f5; }}
  .card {{ background: white; border-radius: 12px; padding: 30px; display: inline-block; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  img {{ display: block; margin: 20px auto; }}
  h2 {{ color: #333; }}
  p {{ color: #888; }}
</style></head><body>
<div class="card">
<h2>微信扫码登录</h2>
<p>请用微信扫描下方二维码</p>
<img src="data:image/png;base64,{state['img_b64']}" width="280" height="280">
<p style="font-size:12px;color:#aaa">页面每5秒自动刷新</p>
</div>
</body></html>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_build_html().encode())

        def log_message(self, *args: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
            pass  # 静默日志

    http_server = HTTPServer(("127.0.0.1", 0), Handler)  # 0 = 系统分配端口
    port = http_server.server_address[1]
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    webbrowser.open(f"http://127.0.0.1:{port}")

    from illusion.config.i18n import t
    print(t("weixin_qr_browser_opened", url=f"http://127.0.0.1:{port}"))
    return {"server": http_server, "port": port, "state": state}


def _refresh_qr_server(server_info: dict[str, Any], qr_hex: str) -> None:
    """刷新二维码服务页面（二维码过期时）

    更新 state 中的 img_b64，页面自动刷新时会拿到新二维码。

    Args:
        server_info: _serve_qr_in_browser 返回的 dict[str, Any]
        qr_hex: 新二维码内容
    """
    import base64
    import io

    import qrcode

    img = qrcode.make(qr_hex)
    buf = io.BytesIO()
    img.save(buf, format="PNG")  # type: ignore[call-arg]
    server_info["state"]["img_b64"] = base64.b64encode(buf.getvalue()).decode()
