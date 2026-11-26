"""
会话压缩模块 — 微压缩、LLM 摘要和自动压缩
=============================================

本模块实现会话压缩功能：
- 微压缩（Microcompact）：清除旧工具结果内容以廉价方式减少 Token 数量
- 完整压缩（Full Compact）：调用 LLM 生成早期消息的结构化摘要
- 自动压缩（Auto-compact）：当 Token 数量超过阈值时自动触发压缩
- 响应式压缩（Reactive Compact）：API 返回 prompt-too-long 时触发压缩
- 上下文警告：接近阈值时通知用户

主要修复：
    - 修复压缩后消息结构混乱（连续 user 消息导致 API 报错）
    - 修复日志格式 bug（~d → ~%d）
    - 添加压缩边界标记（Compact Boundary Marker）
    - 添加图片剥离（压缩前移除图片数据）
    - 添加 PTL 重试（prompt-too-long 时截断重试）
    - 添加响应式压缩
    - 添加上下文警告系统
"""

from illusion.services.compact.auto_compact import auto_compact_if_needed, reactive_compact
from illusion.services.compact.compact_core import (
    compact_conversation,
    compact_messages,
    summarize_messages,
)
from illusion.services.compact.compact_prompt import (
    build_compact_summary_message,
    format_compact_summary,
    get_compact_prompt,
)
from illusion.services.compact.constants import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACT_BOUNDARY_PREFIX,
    COMPACTABLE_TOOLS,
    DEFAULT_GAP_THRESHOLD_MINUTES,
    DEFAULT_KEEP_RECENT,
    DEFAULT_PRESERVE_RECENT,
    MANUAL_COMPACT_BUFFER_TOKENS,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    MAX_PTL_RETRIES,
    TIME_BASED_MC_CLEARED_MESSAGE,
    TOKEN_ESTIMATION_PADDING,
    WARNING_THRESHOLD_BUFFER_TOKENS,
)
from illusion.services.compact.message_ops import (
    _ensure_message_alternation,  # noqa: F401
    _find_safe_split_index,  # noqa: F401
    _remove_orphaned_tool_results,  # noqa: F401
    create_compact_boundary_marker,
    get_messages_after_compact_boundary,
    is_compact_boundary_marker,
)
from illusion.services.compact.microcompact import microcompact_messages, strip_images_from_messages
from illusion.services.compact.models import AutoCompactState, TokenWarningState
from illusion.services.compact.token_utils import (
    calculate_token_warning_state,
    estimate_conversation_tokens,
    estimate_message_tokens,
    get_autocompact_threshold,
    get_context_window,
    should_autocompact,
)

__all__ = [
    "AUTOCOMPACT_BUFFER_TOKENS",
    "COMPACTABLE_TOOLS",
    "COMPACT_BOUNDARY_PREFIX",
    "DEFAULT_GAP_THRESHOLD_MINUTES",
    "DEFAULT_KEEP_RECENT",
    "DEFAULT_PRESERVE_RECENT",
    "MANUAL_COMPACT_BUFFER_TOKENS",
    "MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES",
    "MAX_OUTPUT_TOKENS_FOR_SUMMARY",
    "MAX_PTL_RETRIES",
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "TOKEN_ESTIMATION_PADDING",
    "WARNING_THRESHOLD_BUFFER_TOKENS",
    "AutoCompactState",
    "TokenWarningState",
    "auto_compact_if_needed",
    "build_compact_summary_message",
    "calculate_token_warning_state",
    "compact_conversation",
    "compact_messages",
    "create_compact_boundary_marker",
    "estimate_conversation_tokens",
    "estimate_message_tokens",
    "format_compact_summary",
    "get_autocompact_threshold",
    "get_compact_prompt",
    "get_context_window",
    "get_messages_after_compact_boundary",
    "is_compact_boundary_marker",
    "microcompact_messages",
    "reactive_compact",
    "should_autocompact",
    "strip_images_from_messages",
    "summarize_messages",
]
