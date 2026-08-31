"""Web 认证层（launch token + 签名 cookie）
======================================

在浏览器信任栅栏（security.py，防 DNS rebinding / CSRF）之上提供真正的
认证层：

- **launch token**：每次启动 Web 服务时随机生成（``secrets.token_urlsafe``），
  进程生命周期内有效。CLI 把带 token 的完整 URL 打印给用户，浏览器初次
  打开即完成登录；非浏览器客户端（脚本、测试）也可直接用该 token 以
  ``Authorization: Bearer`` 头或 ``?token=`` 查询参数认证。
- **签名 cookie**：浏览器首次携带 token 访问首页时，服务端校验通过后
  签发一个 HMAC-SHA256 签名的 cookie（HttpOnly + SameSite=Strict +
  authority 绑定 + 有效期），并 303 跳转到不带 token 的干净 URL。此后
  浏览器请求凭 cookie 通过认证，地址栏不残留 token。
- **持久化签名 secret**：cookie 的签名密钥持久化于配置目录（
  ``~/.illusion/web_auth_secret.json``），后端重启后已签发的 cookie 依旧
  有效 —— 用户无需在每次重启后重新打开打印的 URL。

校验路径（三选一等效）：签名 cookie、``Authorization: Bearer <token>``、
``?token=`` 查询参数。token 比较使用常量时间比较。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from illusion.config.paths import get_config_dir
from illusion.ui.web.security import parse_authority

__all__ = [
    "WebAuth",
    "authenticated_url",
    "authority_from_host",
    "cookie_header_value",
    "create_web_auth",
    "extract_bearer",
]

# cookie 有效时长（秒）
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
# 首页 token 交换查询参数名
TOKEN_QUERY = "token"
# cookie 名前缀（后接 authority 的 sha256，authority 绑定）
_COOKIE_PREFIX = "illusion-auth-"
_COOKIE_VERSION = 1
_SECRET_FILE_NAME = "web_auth_secret.json"
_SECRET_VERSION = 1
_SECRET_BYTES = 32
_BASE64URL_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes | None:
    if not value or any(c not in _BASE64URL_SAFE for c in value):
        return None
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except ValueError:
        # base64 解码失败（非法字符/长度）均归结为 ValueError 家族
        return None


def _canonical_secret(value: str) -> bytes | None:
    """校验持久化 secret 是 32 字节的 base64url 字符串。"""
    decoded = _b64url_decode(value)
    if decoded is None or len(decoded) != _SECRET_BYTES:
        return None
    return decoded


def _constant_time_equal(actual: str, expected: str) -> bool:
    """常量时间字符串比较（两侧为 utf-8 字节）。"""
    a = actual.encode("utf-8")
    b = expected.encode("utf-8")
    return len(a) == len(b) and hmac.compare_digest(a, b)


def _write_secret_file(path: Path, content: bytes) -> None:
    """原子写入签名 secret 文件，并把权限收敛为 0600。

    先写同目录临时文件（mkstemp 默认仅属主可读写）再 ``os.replace``
    改名，避免「先截断后写」的并发竞态把文件写坏；Unix 上再显式
    chmod 0600，防止同机其他本地用户读取后伪造任意 authority 的
    cookie（Windows 由 ACL 约束，chmod 不生效也无害）。
    """
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".web-auth-", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass  # Windows 等不支持 POSIX 权限位的平台：由系统 ACL 约束
        os.replace(tmp_path, path)
    except OSError as exc:
        raise RuntimeError(f"无法创建 Web 认证签名 secret 文件: {path}") from exc
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_or_create_secret(secret_path: Path | None) -> bytes:
    """读取持久化签名 secret；文件不存在时创建，格式非法时响亮失败。

    签名 secret 跨进程/跨重启稳定（cookie 的生命线）：文件存在但格式
    非法（版本不符、secret 非 32 字节 base64url）直接抛错而非静默重写，
    否则用户浏览器里所有已签发 cookie 会被无声作废。
    """
    path = secret_path or get_config_dir() / _SECRET_FILE_NAME
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Web 认证签名 secret 文件不可读: {path}") from exc
        if not isinstance(raw, dict) or raw.get("version") != _SECRET_VERSION:
            raise RuntimeError(f"Web 认证签名 secret 文件格式不受支持: {path}")
        secret = raw.get("secret")
        if not isinstance(secret, str):
            raise RuntimeError(f"Web 认证签名 secret 文件缺少 secret: {path}")
        decoded = _canonical_secret(secret)
        if decoded is None:
            raise RuntimeError(f"Web 认证签名 secret 文件中的 secret 无效: {path}")
        return decoded
    raw_secret = secrets.token_bytes(_SECRET_BYTES)
    generated = {
        "version": _SECRET_VERSION,
        "secret": _b64url_encode(raw_secret),
    }
    _write_secret_file(path, (json.dumps(generated, indent=2) + "\n").encode("utf-8"))
    return raw_secret


@dataclass(frozen=True)
class WebAuth:
    """Web 认证状态：launch token + 持久化 cookie 签名 secret。

    Attributes:
        launch_token: 本次进程的启动令牌（进程生命周期有效）。
        secret: cookie 签名密钥（跨进程/跨重启持久化）。
    """

    launch_token: str
    secret: bytes

    def authenticated_url(self, base_url: str) -> str:
        """把 token 附加到首页 URL，得到可完成浏览器登录的完整访问地址。

        ``http://host:port`` → ``http://host:port/?token=...``。
        """
        url = urlsplit(base_url)
        query = f"{TOKEN_QUERY}={self.launch_token}"
        if url.query:
            query = f"{url.query}&{query}"
        # 路径为空时补 "/"，保证输出形如 http://host:port/?token=...
        return url._replace(path=url.path or "/", query=query).geturl()

    # ---- 凭据校验 ----

    def token_valid(self, candidate: str | None) -> bool:
        """launch token 常量时间校验（``?token=`` 或 Bearer 头共用）。"""
        if not candidate:
            return False
        return _constant_time_equal(candidate, self.launch_token)

    def is_authenticated(
        self,
        *,
        authority: str | None,
        cookie_value: str | None,
        bearer_token: str | None,
        query_token: str | None,
    ) -> bool:
        """综合校验：签名 cookie 有效，或 Bearer / query token 命中 launch token。"""
        if self.token_valid(bearer_token) or self.token_valid(query_token):
            return True
        if authority is None or not cookie_value:
            return False
        return self.cookie_valid(authority, cookie_value)

    # ---- 签名 cookie ----

    def cookie_name(self, authority: str) -> str:
        """authority 绑定的 cookie 名（名中含其 sha256，互不冲突）。"""
        digest = hashlib.sha256(authority.encode("utf-8")).digest()
        return _COOKIE_PREFIX + _b64url_encode(digest)

    def mint_cookie(
        self,
        authority: str,
        now: int | None = None,
    ) -> tuple[str, str, int]:
        """签发一个签名 cookie，返回 ``(name, header_value, max_age_seconds)``。

        payload 记录 版本 / authority / 签发时刻 / 过期时刻，HMAC-SHA256
        以 ``secret`` 为钥签名（篡改与伪造由签名校验拦截）。
        """
        max_age = COOKIE_MAX_AGE_SECONDS
        issued = int(now if now is not None else time.time() * 1000)
        payload = {
            "version": _COOKIE_VERSION,
            "authority": authority,
            "issued_at": issued,
            "expires_at": issued + max_age * 1000,
        }
        body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64url_encode(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
        return self.cookie_name(authority), f"v1.{body}.{signature}", max_age

    def cookie_valid(self, authority: str, cookie_value: str) -> bool:
        """校验 cookie：签名匹配、authority 与请求一致、未过期。"""
        name = self.cookie_name(authority)
        found = cookie_header_value(cookie_value, name)
        if found is None:
            return False
        payload = self._decode_cookie(found)
        if payload is None or payload.get("authority") != authority:
            return False
        now_ms = int(time.time() * 1000)
        try:
            issued = int(payload["issued_at"])
            expires = int(payload["expires_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return issued <= now_ms < expires and expires > issued

    def _decode_cookie(self, value: str) -> dict[str, Any] | None:
        parts = value.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return None
        body, encoded_signature = parts[1], parts[2]
        expected = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest()
        actual = _b64url_decode(encoded_signature)
        if actual is None or len(actual) != len(expected) or not hmac.compare_digest(actual, expected):
            return None
        decoded = _b64url_decode(body)
        if decoded is None:
            return None
        try:
            payload = json.loads(decoded.decode("utf-8"))
        except ValueError:
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _COOKIE_VERSION
            or not isinstance(payload.get("authority"), str)
        ):
            return None
        return payload


def create_web_auth(secret_path: Path | None = None) -> WebAuth:
    """创建 Web 认证状态：新 launch token + 持久化签名 secret。"""
    return WebAuth(
        launch_token=secrets.token_urlsafe(_SECRET_BYTES),
        secret=_read_or_create_secret(secret_path),
    )


def authenticated_url(base_url: str, auth: WebAuth) -> str:
    """兼容辅助：``auth.authenticated_url`` 的下游便捷入口。"""
    return auth.authenticated_url(base_url)


def authority_from_host(host: str | None) -> str | None:
    """把请求的 Host 头规范化为 cookie 绑定的 authority（``host[:port]``）。

    返回 ``None`` 表示无法解析（认证视为失败）；IPv6 保留括号。
    """
    if not host:
        return None
    parsed = parse_authority(host)
    if parsed is None:
        return None
    hostname, port = parsed
    literal = f"[{hostname}]" if ":" in hostname else hostname
    return literal if port is None else f"{literal}:{port}"


def extract_bearer(authorization: str | None) -> str | None:
    """从 ``Authorization`` 头提取 Bearer token（大小写不敏感前缀匹配）。

    容忍任意空白分隔（空格 / tab / 多空格）。格式为
    ``Bearer <token>``，否则返回 None。
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def cookie_header_value(header_value: str, name: str) -> str | None:
    """从 Cookie 头中读取指定名字的值（不做通用 Cookie 解析，按 ``;`` 分段）。"""
    for segment in header_value.split(";"):
        at = segment.find("=")
        if at == -1 or segment[:at].strip() != name:
            continue
        return segment[at + 1 :].strip()
    return None