"""API 错误日志（error_log）测试模块

本模块提供 API 400 错误体落盘的单元测试，包括：
- 400 落盘 / 非 400 跳过
- anthropic/openai SDK 异常形态（status_code + body）与 httpx 异常形态
  （response.status_code）的提取
- 清理规则与既有文件日志一致（超龄/超大文件删除，glob 覆盖滚动备份）
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

import illusion.api.error_log as error_log_module
from illusion.api.error_log import log_api_error


@pytest.fixture(autouse=True)
def _log_dir(tmp_path, monkeypatch):
    """把日志目录隔离到临时路径并重置进程级单例。"""
    monkeypatch.setenv("ILLUSION_LOGS_DIR", str(tmp_path))
    error_log_module._logger = None
    yield tmp_path
    error_log_module._logger = None


class _SDKStyleError(Exception):
    """模拟 anthropic/openai SDK 异常形态（status_code + body）。"""

    def __init__(self, status_code: int, body: dict | None = None):
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code
        self.body = body


def _httpx_style_error(status_code: int, payload: bytes) -> httpx.HTTPStatusError:
    """构造 httpx 形态异常（responses 客户端使用）。"""
    request = httpx.Request("POST", "https://example.test/responses")
    response = httpx.Response(status_code, request=request, content=payload)
    return httpx.HTTPStatusError(
        f"Server error '{status_code}'", request=request, response=response,
    )


def _read_log(log_dir: Path) -> str:
    return (log_dir / "api_error.log").read_text(encoding="utf-8")


class TestLogApiError:
    """log_api_error 落盘行为测试"""

    def test_400_body_written(self, tmp_path):
        """400 错误体（含回传校验文案）落盘"""
        exc = _SDKStyleError(400, {
            "error": {"type": "invalid_request_error", "message": "The `content[].thinking` in the thinking mode must be passed back to the API."},
        })
        log_api_error(exc, provider="anthropic", model="deepseek-v4-flash")
        content = _read_log(tmp_path)
        assert "must be passed back" in content
        assert '"provider": "anthropic"' in content
        assert '"model": "deepseek-v4-flash"' in content
        assert '"status": 400' in content

    def test_non_400_skipped(self, tmp_path):
        """429/5xx 不落盘（瞬态错误，避免刷掉格式错误）"""
        log_api_error(_SDKStyleError(429), provider="openai", model="gpt-5.4")
        log_api_error(_SDKStyleError(503), provider="openai", model="gpt-5.4")
        assert not (tmp_path / "api_error.log").exists()

    def test_httpx_style_exception(self, tmp_path):
        """httpx HTTPStatusError（无 .status_code/.body）正确提取状态与消息"""
        payload = json.dumps({"error": {"message": "reasoning item required"}}).encode()
        log_api_error(_httpx_style_error(400, payload), provider="responses", model="gpt-5.4")
        content = _read_log(tmp_path)
        assert '"status": 400' in content
        assert "reasoning item required" in content

    def test_body_fallback_to_str(self, tmp_path):
        """无 body 属性时退回异常字符串"""
        exc = _SDKStyleError(400)  # body=None
        log_api_error(exc, provider="openai", model="m")
        content = _read_log(tmp_path)
        assert "Error code: 400" in content


class TestErrorLogCleanup:
    """清理规则测试（与 memory/log.py 同一语义）"""

    def test_stale_and_oversized_backups_removed(self, tmp_path):
        """创建 logger 时清理超龄与超大文件（glob 覆盖滚动备份）"""
        stale = tmp_path / "api_error.log.1"
        stale.write_text("old", encoding="utf-8")
        past = time.time() - 8 * 24 * 3600  # 超过 7 天 TTL
        os.utime(stale, (past, past))
        oversized = tmp_path / "api_error.log.2"
        oversized.write_text("x" * (11 * 1024 * 1024), encoding="utf-8")  # 超过 10MB 兜底

        log_api_error(_SDKStyleError(400, {"error": {"message": "m"}}), provider="p")

        assert not stale.exists()
        assert not oversized.exists()
        assert (tmp_path / "api_error.log").exists()

    def test_active_file_not_removed(self, tmp_path):
        """超龄的活动文件（mtime 陈旧）不因年龄清理被误删——写入刷新 mtime"""
        log_api_error(_SDKStyleError(400, {"error": {"message": "m"}}), provider="p")
        assert (tmp_path / "api_error.log").exists()


class TestSanitizeAndTruncate:
    """凭据脱敏与单条截断测试"""

    def test_api_key_redacted(self, tmp_path):
        """错误体中的 sk- 形态密钥被脱敏"""
        exc = _SDKStyleError(400, {
            "error": {"message": "invalid request for key sk-abc123def456ghi789"},
        })
        log_api_error(exc, provider="openai", model="m")
        content = _read_log(tmp_path)
        assert "sk-abc123def456ghi789" not in content
        assert "[REDACTED]" in content

    def test_bearer_token_redacted(self, tmp_path):
        """Bearer 令牌与 Authorization 头被脱敏"""
        exc = _SDKStyleError(400, {
            "error": {"message": "Authorization: Bearer eyJhbGciOi.payload.sig rejected"},
        })
        log_api_error(exc, provider="openai", model="m")
        content = _read_log(tmp_path)
        assert "eyJhbGciOi" not in content
        assert "[REDACTED]" in content

    def test_long_body_truncated(self, tmp_path):
        """超长错误体截断到单条上限，保留截断标记"""
        exc = _SDKStyleError(400, {
            "error": {"message": "x" * 20000},
        })
        log_api_error(exc, provider="openai", model="m")
        content = _read_log(tmp_path)
        assert "...[truncated]" in content
        assert len(content) < 6000  # 远小于原始 20000
