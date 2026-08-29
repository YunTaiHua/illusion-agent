"""
API 错误日志工具
================

将上游 API 的 400（invalid_request_error）错误体落盘到
`~/.illusion/logs/api_error.log`，用于诊断请求格式类错误——这类错误只在
UI 短暂展示，进程退出或会话恢复后无从取证（例如 DeepSeek 思考模式的
"content[].thinking must be passed back" 回传校验失败）。

设计要点（与 memory/log.py 等既有文件日志风格一致）：
    - 专用文件日志器（RotatingFileHandler，5MB × 3 备份）
    - propagate=False：不传播到根 logger，避免在控制台刷屏
    - 路径通过 get_logs_dir() 解析（支持 ILLUSION_LOGS_DIR 覆盖，无硬编码）
    - 周期清理：超过 7 天或超过体积兜底阈值的旧日志自动删除
      （统一走 log_cleanup 工具，glob 覆盖活动文件与滚动备份）

函数说明：
    - log_api_error: 记录一次上游 API 400 错误体
"""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from typing import Any

from illusion.config.paths import get_logs_dir
from illusion.utils.log_cleanup import cleanup_old_files

_MAX_BYTES = 5 * 1024 * 1024  # 单文件上限 5MB
_BACKUP_COUNT = 3  # 滚动备份数
_LOG_TTL_DAYS = 7  # API 错误日志保留天数
# 体积兜底阈值：活动日志由 RotatingFileHandler 约束在 _MAX_BYTES 内，
# 年龄清理（mtime 超 TTL）对"持续写入、mtime 不断刷新"的活动文件永不触发，
# 故额外按单文件体积兜底（与 memory/log.py 同一语义）。
_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 体积兜底阈值 10MB
# 单条记录截断上限：400 错误体可能回显大段请求内容（部分网关会回显
# messages），防止单条记录膨胀
_MAX_RECORD_CHARS = 4000
# 凭据脱敏（多遍有序：先令牌后头部形态，两遍后嵌套形态也收敛）：
# - Bearer 令牌 / sk- 形态 API key
# - Authorization / x-api-key 头（异常网关可能在错误体中回显请求头片段）
_REDACTION_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(authorization|x-?api-?key)\"?\s*[:=]\s*\"?\S+", re.IGNORECASE),
]


def _sanitize(text: str) -> str:
    """脱敏凭据片段并截断超长内容。"""
    redacted = text
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    if len(redacted) > _MAX_RECORD_CHARS:
        redacted = redacted[:_MAX_RECORD_CHARS] + "...[truncated]"
    return redacted

_logger: logging.Logger | None = None


def _get_error_logger() -> logging.Logger:
    """获取 API 错误专用文件日志器（进程级单例）。

    首次创建时顺带清理超龄/超大的旧 API 错误日志（统一走 log_cleanup
    工具）。清理必须在创建 handler 之前：Windows 上被打开的文件无法删除。
    glob 用 "api_error.log*" 以一并覆盖 RotatingFileHandler 的滚动备份。
    """
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("illusion.api.error")
    # 移除可能残留的旧 handler（logging.getLogger 返回全局单例，
    # 测试间可能携带指向旧路径的 handler）
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    cleanup_old_files(
        get_logs_dir(),
        "api_error.log*",
        max_age_days=_LOG_TTL_DAYS,
        max_size_bytes=_MAX_SIZE_BYTES,
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不传播到根 logger，避免刷屏控制台
    log_path = get_logs_dir() / "api_error.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    _logger = logger
    return logger


def _extract_status(exc: Exception) -> int | None:
    """从异常中提取 HTTP 状态码。

    兼容两种形态：anthropic/openai SDK 异常直接携带 ``status_code``；
    httpx 的 ``HTTPStatusError`` 通过 ``response.status_code`` 携带。
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status


def _extract_error_body(exc: Exception) -> str:
    """从异常中提取错误体文本。

    优先级：anthropic/openai SDK 的 ``body``（已解析的响应体）→
    httpx 异常的 ``response.text``（响应体已预读）→ 异常字符串。
    """
    body = getattr(exc, "body", None)
    if body:
        try:
            return json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(body)
    response = getattr(exc, "response", None)
    if response is not None:
        text = getattr(response, "text", "")
        if text:
            return text
    return str(exc)


def log_api_error(
    exc: Exception,
    *,
    provider: str,
    model: str = "",
) -> None:
    """记录一次上游 API 400 错误体（仅 400，格式类错误）。

    429/5xx 属瞬态错误（重试机制已覆盖），不落盘，避免故障期刷掉
    真正需要取证的格式错误。落盘失败静默忽略，不影响调用方。

    Args:
        exc: SDK/httpx 抛出的异常（期望 status_code == 400）
        provider: 提供商标识（如 "anthropic" / "openai" / "responses"）
        model: 目标模型名称
    """
    status = _extract_status(exc)
    if status != 400:
        return
    try:
        record: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "status": status,
            "error": _sanitize(_extract_error_body(exc)),
        }
        _get_error_logger().info(json.dumps(record, ensure_ascii=False))
    except OSError:
        # 日志落盘失败不影响主流程
        pass
