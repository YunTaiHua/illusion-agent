"""
API 错误类型模块
================

本模块定义 IllusionAgent API 相关的自定义异常类型。

主要功能：
    - 定义基础 API 异常类
    - 提供认证失败、速率限制、请求失败等具体异常

类说明：
    - IllusionAgentApiError: 所有 API 异常的基类
    - AuthenticationFailure: 认证失败异常
    - RateLimitFailure: 速率限制异常
    - RequestFailure: 请求失败异常

使用示例：
    >>> from illusion.api.errors import AuthenticationFailure, RateLimitFailure
    >>> try:
    >>>     # API 调用
    >>> except AuthenticationFailure as e:
    >>>     print(f"认证失败: {e}")
"""

from __future__ import annotations

import re


class IllusionAgentApiError(RuntimeError):
    """API 异常基类

    所有上游 API 失败异常的基类，继承自 RuntimeError。
    用于统一处理来自不同 API 提供商的错误。
    """


class AuthenticationFailure(IllusionAgentApiError):
    """认证失败异常

    当上游服务拒绝提供的凭据时抛出此异常。
    可能的原因包括：API 密钥无效、令牌过期、权限不足等。
    """


class RateLimitFailure(IllusionAgentApiError):
    """速率限制异常

    当上游服务因请求频率超限而拒绝请求时抛出此异常。
    通常包含重试建议信息。
    """


class RequestFailure(IllusionAgentApiError):
    """请求失败异常

    用于通用的请求或传输失败情况。
    可能的原因包括：网络连接错误、服务不可用、超时等。
    """


class PayloadTooLargeFailure(IllusionAgentApiError):
    """请求体过大异常

    当上游服务/网关以 413（Request Entity Too Large）拒绝请求体时抛出。
    与 prompt-too-long 不同：这是网关（openresty/nginx 等）对请求体
    字节大小的硬限制，而非模型上下文 token 上限——典型触发源是历史
    消息中内嵌的图片截图/工具结果 base64。上层据此触发响应式压缩
    （剥离媒体 + 清理工具结果）后重试当前轮次。
    """


# 413 判定短语（网关返回的 HTML/JSON 错误页中稳定出现的文案）
_PAYLOAD_TOO_LARGE_PHRASES = (
    "entity too large",
    "payload too large",
    "request too large",
)
# 独立的 "413" 数字 token（\b 边界避免误命中 "14130" 之类的数字串）
_PAYLOAD_TOO_LARGE_CODE_RE = re.compile(r"\b413\b")


def is_payload_too_large(status_code: int | None = None, message: str = "") -> bool:
    """判定错误是否为请求体过大（413）。

    Args:
        status_code: HTTP 状态码（未知时传 None）
        message: 错误消息文本（含网关 HTML 错误页原文）

    Returns:
        bool: 是否为 413 / 请求体过大
    """
    if status_code == 413:
        return True
    lowered = (message or "").lower()
    if any(phrase in lowered for phrase in _PAYLOAD_TOO_LARGE_PHRASES):
        return True
    return bool(_PAYLOAD_TOO_LARGE_CODE_RE.search(lowered))
