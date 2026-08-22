"""浏览器信任栅栏（browser-trust fence）
==================================

为 Web 后端提供针对「浏览器混淆代理」攻击的防御设施：本地服务虽然只
监听回环地址，但用户浏览器中打开的任意网页都可以向 ``127.0.0.1`` 发起
请求（Drive-by Localhost）。本模块拦截两条 confused-deputy 路径：

1. **DNS rebinding**：攻击者域名解析到回环地址，页面经
   ``http://evil.example:3000`` 访问本服务。Host 头由浏览器按它相信的
   URL 填充，是 rebinding 唯一无法伪造的标记 —— 因此 **Host 栅栏绑定
   一切请求**（包括不携带 Origin / Sec-Fetch 头的明文读请求）。
2. **跨站请求**：恶意页面从 ``https://evil.example`` 直接向回环端口发
   fetch / WebSocket。现代浏览器在 fetch 上标注 ``Sec-Fetch-Site``，
   在 WebSocket 握手上附带 ``Origin``。

三道栅栏：

- **Host fence**：Host 必须是回环地址或显式声明的 ``trusted_hosts``
  authority，否则拒绝；
- **Sec-Fetch-Site fence**：``cross-site`` 直接拒绝；
- **Origin fence**：携带 Origin 时必须与 Host 严格同源；字面量
  ``null``（沙箱 iframe / file: 页面的不透明 origin）拒绝；缺失时由
  Host 栅栏兜底放行。

明确的设计边界：**这不是认证层**。网络可达性策略属于监听配置，
认证不在本模块范围内。REST ``/api/*`` 属于特权平面（读写凭据与配置），
仅限回环访问 —— 即使部署声明了 ``trusted_hosts``；WS ``/ws`` 承载会话
交互，允许受信主机接入。
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence
from urllib.parse import urlsplit

__all__ = [
    "assert_trusted_authority",
    "canonical_authority",
    "derive_lan_hosts",
    "is_loopback_hostname",
    "is_trusted_authority",
    "is_trusted_request",
    "parse_authority",
]

# WebSocket 拒绝握手的策略关闭码（RFC 6455 7.4.1 Policy Violation）
WS_POLICY_CLOSE_CODE = 1008


def parse_authority(authority: str) -> tuple[str, int | None] | None:
    """解析 authority（``host[:port]``，Host 头形状）为 ``(hostname, port)``。

    经 URL 解析规范化：hostname 小写、IPv6 字面量去括号（如 ``[::1]`` →
    ``::1``）；port 仅在显式写出时返回。无法解析（空 host、非法 port）
    返回 ``None``。
    """
    if not authority:
        return None
    try:
        parts = urlsplit(f"http://{authority}")
    except ValueError:
        return None
    hostname = parts.hostname
    if not hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return (hostname, port)


def _origin_authority(origin: str) -> tuple[str, int | None] | None:
    """解析 Origin 头（完整 URL）为 ``(hostname, port)``。

    仅接受 http/https 来源；``null`` 等不透明 origin 与畸形值返回
    ``None``。
    """
    if not origin:
        return None
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    hostname = parts.hostname
    if not hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return (hostname, port)


def is_loopback_hostname(hostname: str) -> bool:
    """判断已去括号的 hostname 是否指向本机回环（localhost / ::1 / 127/8 全段）。"""
    if hostname == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return addr.is_loopback


def canonical_authority(entry: str) -> str | None:
    """authority 条目的规范形（hostname 小写、IPv6 补括号、显式端口保留）。

    无法解析返回 ``None``。
    """
    parsed = parse_authority(entry)
    if parsed is None:
        return None
    hostname, port = parsed
    host_literal = f"[{hostname}]" if ":" in hostname else hostname
    return host_literal if port is None else f"{host_literal}:{port}"


def _looks_like_ipv4_shorthand(hostname: str) -> bool:
    """WHATWG 判定：最后一个点分标签为全数字或 0x 十六进制时，整个主机
    会被浏览器解析为 IPv4 字面量（如 ``example.1`` → 0.0.0.1、
    ``0x7f000001`` → 127.0.0.1）。此类书写作为 trusted_hosts 条目永远
    匹配不上真实流量，必须拒绝。
    """
    last = hostname.rsplit(".", 1)[-1]
    if last.isdigit():
        return True
    return len(last) > 2 and last[:2].lower() == "0x" and all(
        c in "0123456789abcdefABCDEF" for c in last[2:]
    )


def assert_trusted_authority(entry: str) -> None:
    """断言一个 ``trusted_hosts`` 配置条目是裸 host[:port] 的规范形。

    任何「解析后会静默改写」的形态都必须在加载时响亮失败，而不是等到
    运行期 403 或悄然放宽授权：前后空白、userinfo（``user@host``）、
    附带路径、悬空冒号、零填充/十六进制/缩写段 IP 变形（``0x7f.0.0.1``、
    ``127.1``、``example.1``）等一律拒绝。含数字的普通域名
    （``nas1.example.com``）不受影响 —— 只有末段形如 IPv4 变形才拒绝。
    """
    if entry != entry.strip():
        raise ValueError(f"trusted_hosts 条目含首尾空白: {entry!r}")
    lowered = entry.lower()
    parsed = parse_authority(lowered)
    if parsed is None or canonical_authority(lowered) != lowered:
        raise ValueError(f"trusted_hosts 条目不是裸 host[:port] 规范形: {entry!r}")
    # IP 字面量必须是规范书写（浏览器会把变形形式规范化后填入 Host，
    # 非规范条目永远匹配不上真实流量 —— 正是要阻止的静默失效）。
    hostname = parsed[0]
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        if _looks_like_ipv4_shorthand(hostname):
            raise ValueError(f"trusted_hosts 条目不是规范的 IP 书写: {entry!r}") from None
        return  # 域名条目：小写化后与 canonical 一致即可
    if str(addr) != hostname:
        raise ValueError(f"trusted_hosts 条目不是规范的 IP 书写: {entry!r}")


def is_trusted_authority(
    authority: tuple[str, int | None],
    trusted_hosts: Sequence[str],
) -> bool:
    """判断请求 authority 是否命中某条 ``trusted_hosts`` 条目。

    无端口条目匹配该主机名的任意端口（LAN 部署端口可能由 OS 分配）；
    带端口条目要求 host 与 port 都精确一致。
    """
    hostname, port = authority
    for raw in trusted_hosts:
        parsed = parse_authority(raw)
        if parsed is None:
            continue  # 配置已在构造时断言，此处兜底跳过
        entry_host, entry_port = parsed
        if entry_port is None:
            if entry_host == hostname:
                return True
        elif entry_host == hostname and entry_port == port:
            return True
    return False


def is_trusted_request(
    *,
    host: str | None,
    origin: str | None,
    sec_fetch_site: str | None,
    trusted_hosts: Sequence[str],
) -> bool:
    """浏览器信任栅栏主判定：一个请求是否可抵达本服务的 API 平面。

    Args:
        host: ``Host`` 头（authority 形状）。
        origin: ``Origin`` 头（完整 URL 或 ``null``），可为 ``None``。
        sec_fetch_site: ``Sec-Fetch-Site`` 头，可为 ``None``。
        trusted_hosts: 显式声明的非回环受信 authority 列表。

    判定顺序：先无条件过 Host 栅栏（rebinding 防御，不因
    「缺少浏览器标记」走任何捷径），再拒显式跨站标记，最后做严格同源
    校验。
    """
    # --- Host fence（DNS rebinding）：绑定一切请求 ---
    parsed_host = parse_authority(host) if host else None
    if parsed_host is None:
        return False
    if not (
        is_loopback_hostname(parsed_host[0])
        or is_trusted_authority(parsed_host, trusted_hosts)
    ):
        return False
    # --- Sec-Fetch-Site fence：现代浏览器的跨站标记 ---
    if (sec_fetch_site or "").strip().lower() == "cross-site":
        return False
    # --- Origin fence：携带 Origin 时必须与 Host 严格同源 ---
    if not origin:
        return True  # 缺失标记：Host 栅栏已绑定该请求
    if origin.strip().lower() == "null":
        return False  # 不透明 origin（沙箱 iframe / file: 页面）
    parsed_origin = _origin_authority(origin)
    if parsed_origin is None:
        return False
    return parsed_origin == parsed_host


def derive_lan_hosts() -> tuple[str, ...]:
    """采样本机可被局域网直达的单播地址字面量。

    绑定所有接口（``0.0.0.0`` / ``::``）时用于自动派生受信主机名，免去
    手工声明。排除回环、链路本地、多播与未指定地址；IPv6 字面量输出带
    括号形式。

    局限：依赖主机名解析，虚拟网卡地址可能一并纳入 —— 这是显式信任面
    （声明者本就选择暴露到所有接口），需要更细粒度时用 ``--trusted-host``
    精确指定。
    """
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, proto=socket.IPPROTO_TCP)
    except OSError:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            continue
        text = str(ip)
        if text in seen:
            continue
        seen.add(text)
        out.append(f"[{text}]" if ip.version == 6 else text)
    return tuple(out)
