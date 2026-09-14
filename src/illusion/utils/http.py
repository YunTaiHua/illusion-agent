"""HTTP 客户端工具模块
====================

提供统一的 httpx.AsyncClient 工厂函数，注入系统证书库以兼容
SteamTools（BeyondDimension）等工具注入的 HTTPS 中间人证书。

主要组件：
    - create_async_client: 统一的 httpx.AsyncClient 工厂函数
    - create_trusted_ssl_context: 系统证书库 SSL 上下文（供 anthropic/openai
      等 SDK 自建 http 栈时以 http_client= 注入——SDK 不走本工厂，
      必须单独注入，否则系统代理的 MITM 证书会验证失败）
    - create_sdk_transport_client: 按 anthropic SDK 版本（<1.0 用 httpx、
      >=1.0 用 httpx2）构造注入系统证书库的异步 HTTP 客户端
"""

from __future__ import annotations

import ssl
from typing import Any

import httpx


def _create_ssl_context() -> ssl.SSLContext:
    """创建 SSL 上下文，优先使用系统证书库（truststore）。

    truststore 会加载 Windows/macOS 系统证书库，包括 SteamTools
    注入的 MITM 证书；若 truststore 不可用，回退到 httpx 默认验证。
    """
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def create_trusted_ssl_context() -> ssl.SSLContext:
    """返回注入系统证书库的 SSL 上下文（公开接口）。

    供不经本工厂的 HTTP 客户端（anthropic/openai 等 SDK 自建 http 栈）
    以 ``http_client=httpx.AsyncClient(verify=create_trusted_ssl_context())``
    的方式复用同一套证书兼容逻辑。
    """
    return _create_ssl_context()


def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """创建 httpx.AsyncClient，注入系统证书库。

    所有需要 HTTPS 的 httpx.AsyncClient 都应通过此函数创建，
    以统一处理 SSL 证书兼容性问题（如 SteamTools MITM 证书）。

    Args:
        **kwargs: 透传给 httpx.AsyncClient 的参数（timeout、follow_redirects 等）

    Returns:
        httpx.AsyncClient: 配置好系统证书库的异步客户端
    """
    ssl_context = _create_ssl_context()
    kwargs.setdefault("verify", ssl_context)
    return httpx.AsyncClient(**kwargs)


def create_sdk_transport_client() -> Any:
    """创建适配当前 anthropic SDK 版本的异步 HTTP 客户端（注入系统证书库）。

    anthropic>=1.0 的 SDK 自建栈基于 httpx2（独立传输库），旧版基于 httpx；
    按 SDK 版本选择对应传输，避免注入 ``httpx.AsyncClient`` 被新版 SDK 构造
    时校验拒绝（TypeError: Invalid ``http_client`` argument）。
    """
    return _sdk_transport_module().AsyncClient(verify=create_trusted_ssl_context())


def _sdk_transport_module() -> Any:
    """按 anthropic 版本返回 http 传输模块：<1.0 用 httpx，>=1.0 用 httpx2。"""
    try:
        import anthropic
        from packaging.version import Version
    except ImportError:
        # anthropic 缺失属异常环境（本包强依赖），兜底退回 httpx
        return httpx
    if Version(anthropic.__version__) < Version("1.0"):
        return httpx
    import httpx2

    return httpx2
