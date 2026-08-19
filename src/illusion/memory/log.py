"""
记忆子代理活动日志工具
======================

后台记忆提取/整合子代理执行时，其活动（工具调用、执行结果、模型结论）
记录到 `~/.illusion/logs/memory_{kind}.log`，实现过程透明——用户可随时
查看 LLM 在后台做了什么。

设计要点：
    - 专用文件日志器（RotatingFileHandler，5MB × 3 备份）
    - propagate=False：不传播到根 logger，避免在控制台刷屏
    - 路径通过 get_logs_dir() 解析（支持 ILLUSION_LOGS_DIR 覆盖，无硬编码）
    - 周期清理：超过 7 天或超过体积上限的记忆活动日志自动删除
      （统一走 log_cleanup 工具，覆盖活动文件与滚动备份）
    - 子代理事件流本身不渲染到主对话（无感），日志文件是唯一的可见通道

函数说明：
    - get_memory_logger: 获取记忆子代理专用文件日志器
    - truncate: 截断长文本并单行化
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from illusion.config.paths import get_logs_dir
from illusion.utils.log_cleanup import cleanup_old_files

_MAX_BYTES = 5 * 1024 * 1024  # 单文件上限 5MB
_BACKUP_COUNT = 3  # 滚动备份数
_LOG_TTL_DAYS = 7  # 记忆活动日志保留天数
# 体积兜底阈值：活动日志由 RotatingFileHandler 约束在 _MAX_BYTES 内，
# 年龄清理（mtime 超 TTL）对"持续写入、mtime 不断刷新"的活动文件永不触发，
# 故额外按单文件体积兜底：任一文件（活动文件或滚动备份）达到该阈值即删除，
# 防止异常情况下（如轮转失效）单个日志无限增长。
_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 体积兜底阈值 10MB

_loggers: dict[str, logging.Logger] = {}


def get_memory_logger(kind: str) -> logging.Logger:
    """获取记忆子代理专用文件日志器。

    首次创建时顺带清理超过 TTL 的旧记忆活动日志（进程级一次）。

    Args:
        kind: 日志种类（如 "extract" / "dream"），对应文件名 memory_{kind}.log

    Returns:
        logging.Logger: 已配置文件输出的日志器（propagate=False）
    """
    if kind in _loggers:
        return _loggers[kind]
    logger = logging.getLogger(f"illusion.memory.{kind}")
    # 移除可能残留的旧 handler（logging.getLogger 返回全局单例，
    # 测试间可能携带指向旧路径的 handler）
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    # 先清理超龄/超大的旧记忆活动日志（统一走 log_cleanup 工具）。
    # 顺序必须在创建 handler 之前：Windows 上被打开的文件无法删除，
    # 若 handler 先打开文件则清理会失败。
    # glob 用 "memory_*.log*" 以一并覆盖 RotatingFileHandler 的滚动备份
    # （memory_dream.log.1/.2/.3），并叠加体积阈值兜底。
    cleanup_old_files(
        get_logs_dir(),
        "memory_*.log*",
        max_age_days=_LOG_TTL_DAYS,
        max_size_bytes=_MAX_SIZE_BYTES,
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不传播到根 logger，避免刷屏控制台
    log_path = get_logs_dir() / f"memory_{kind}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    _loggers[kind] = logger
    return logger


def truncate(text: str, limit: int = 500) -> str:
    """截断长文本并单行化（用于日志输出）。"""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."
