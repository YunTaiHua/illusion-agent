"""Tests for 413 (Request Entity Too Large) detection and translation.

回归：网关（openresty 等）以 413 拒绝请求体时，此前被归为通用
RequestFailure 直接报错——413 文案不含 prompt/long，不触发响应式压缩。
现在统一识别为 PayloadTooLargeFailure，由 run_query 剥媒体压缩后重试。
"""

from __future__ import annotations

import httpx

from illusion.api.client import _translate_api_error
from illusion.api.codex_client import _translate_status_error
from illusion.api.errors import (
    PayloadTooLargeFailure,
    RequestFailure,
    is_payload_too_large,
)
from illusion.api.openai_client import OpenAICompatibleClient
from illusion.api.responses_client import ResponsesApiClient


class _FakeProviderError(Exception):
    """模拟各家 SDK 错误对象：str(exc) 即消息文本，携带 status_code。"""

    status_code = 413

    def __init__(self, message: str = "<html>413 Request Entity Too Large</html>") -> None:
        super().__init__(message)


def test_is_payload_too_large_by_status_code():
    assert is_payload_too_large(status_code=413, message="") is True


def test_is_payload_too_large_by_phrase():
    assert is_payload_too_large(
        message="<center><h1>413 Request Entity Too Large</h1></center>"
    ) is True
    assert is_payload_too_large(message="Payload Too Large") is True
    assert is_payload_too_large(message="request too large") is True


def test_is_payload_too_large_by_standalone_code_token():
    assert is_payload_too_large(message="gateway returned status 413") is True


def test_is_payload_too_large_no_false_positive_on_embedded_digits():
    # "14130" 不应被 \b413\b 误命中
    assert is_payload_too_large(message="prompt has 14130 tokens") is False
    assert is_payload_too_large(status_code=400, message="invalid request") is False


def test_anthropic_translate_maps_413_to_payload_too_large():
    translated = _translate_api_error(_FakeProviderError())
    assert isinstance(translated, PayloadTooLargeFailure)


def test_anthropic_translate_keeps_other_errors():
    class _BadRequest(Exception):
        status_code = 400

    translated = _translate_api_error(_BadRequest("bad request"))
    assert isinstance(translated, RequestFailure)
    assert not isinstance(translated, PayloadTooLargeFailure)


def test_openai_translate_maps_413_to_payload_too_large():
    translated = OpenAICompatibleClient._translate_error(_FakeProviderError())
    assert isinstance(translated, PayloadTooLargeFailure)


def test_responses_translate_maps_413_to_payload_too_large():
    request = httpx.Request("POST", "http://gateway.test/v1/responses")
    response = httpx.Response(413, request=request, text="<html>413</html>")
    exc = httpx.HTTPStatusError(
        "Responses request failed with status 413", request=request, response=response
    )
    translated = ResponsesApiClient._translate_error(exc)
    assert isinstance(translated, PayloadTooLargeFailure)


def test_codex_translate_maps_413_to_payload_too_large():
    translated = _translate_status_error(413, "<html>Request Entity Too Large</html>")
    assert isinstance(translated, PayloadTooLargeFailure)
    # 类型层面独立于 RequestFailure：不落入既有可重试判定
    assert not isinstance(translated, RequestFailure)
