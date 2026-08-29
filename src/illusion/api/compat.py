"""
API 兼容辅助模块
================

本模块提供不同模型供应商之间的兼容处理辅助函数。

主要功能：
    - 解析非标准工具参数字符串
    - 清理模型输出中的工具调用残留标签
    - 提取并拆分 `<think>` 思考内容
    - 合并去重多来源推理文本
    - 思考内容回传（thinking passback）的门控与占位
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_THINK_BLOCK_RE = re.compile(r"<(?:think|thought)\b[^>]*>([\s\S]*?)</(?:think|thought)\b[^>]*>", re.IGNORECASE)
_THINK_OPEN_TAG_RE = re.compile(r"<(?:think|thought)\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_TAG_RE = re.compile(r"</(?:think|thought)\b[^>]*>", re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$", re.IGNORECASE)
_DSML_TOOL_CALL_PREFIX_RE = re.compile(
    r"<\s*[|｜]\s*DSML\s*[|｜]\s*tool_calls[^\n>]*>?",
    re.IGNORECASE,
)
_TOOL_CALL_XML_BLOCK_RE = re.compile(r"<tool_call\b[^>]*>[\s\S]*?</tool_call\b[^>]*>", re.IGNORECASE)
_TOOL_CALL_XML_TAG_RE = re.compile(r"</?(?:tool_call|arg_key|arg_value)\b[^>]*>", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

# DeepSeek v4 思考模式要求请求历史中每条 assistant 消息都回传思考内容
# （Anthropic 线格式为 content[].thinking 块，OpenAI 线格式为 reasoning_content
# 字段；官方文档：带 tools 时所有前序轮次必须回传，缺失返回 400
# "The `content[].thinking` in the thinking mode must be passed back to the API"）。
# 上游只在部分请求上强制校验（与其上下文缓存状态相关），因此缺块的历史会
# 表现为间歇性 400。对确已丢失思考内容的历史轮次，用占位文本补齐以满足
# 存在性校验；DeepSeek 自身返回的 signature 即为 null，不做签名校验。
THINKING_PASSBACK_PLACEHOLDER = "[previous turn's thinking content was not retained by the client]"


def model_requires_reasoning_field(model: str) -> bool:
    """判断目标模型是否要求所有 assistant 轮携带 reasoning_content 字段（空串合法）。

    Kimi（K2 Thinking 系列，保留式思考）：官方 kimi-cli 注明保留式思考后端
    要求每个 assistant 轮都有 reasoning_content——空串表示"思考了但为空"，
    同样必须存在，缺失字段才会被拒绝。与 DeepSeek 的差异：DeepSeek 要求
    回传思考内容本身（占位文本补齐，见 THINKING_PASSBACK_PLACEHOLDER），
    Kimi 只要求字段存在。

    与官方 kimi-cli 的已知偏差：官方仅在轮次携带 ThinkPart 时输出该字段
    （空 ThinkPart 回放为 ""）；本实现按模型名对**所有** assistant 轮合成
    空串字段——比官方契约更严格，对按官方语义实现的端点无副作用。

    Args:
        model: 模型名称

    Returns:
        bool: 是否为保留式思考（要求字段存在）的模型
    """
    return "kimi" in (model or "").lower()


def is_reasoning_passback_error(message: str) -> bool:
    """按错误文案检测思考/推理内容回传校验失败（保留式思考模型 400）。

    已知文案变体：
    - DeepSeek: "The `content[].thinking`/`reasoning_content` in the thinking
      mode must be passed back to the API"
    - Kimi 等保留式思考后端：含 tool_calls 的 assistant 消息缺
      reasoning_content 时要求补齐（各网关措辞不一，按关键词匹配）

    刻意收紧匹配面：必须明确提到回传字段名（reasoning_content /
    content[].thinking），避免 "reasoning_effort is required when thinking is
    enabled" 之类的参数校验错误被误判——本检测器触发的自愈会永久降级
    （关闭思考），假阳性代价高。

    Args:
        message: 错误消息文本

    Returns:
        bool: 是否为回传校验失败
    """
    lowered = message.lower()
    mentions_field = (
        "reasoning_content" in lowered
        or "content[].thinking" in lowered
    )
    demands_passback = (
        "passed back" in lowered
        or (
            "must" in lowered
            and any(
                word in lowered
                for word in ("include", "contain", "provide", "carry", "have")
            )
        )
        or "required" in lowered
    )
    return mentions_field and demands_passback


def is_thinking_passback_error(exc: Exception) -> bool:
    """检查错误是否为思考内容回传校验失败（DeepSeek 思考模式 400）。

    已知错误文案（官方与各网关措辞略有差异）：
    - "The `content[].thinking` in the thinking mode must be passed back..."
    - "The `reasoning_content` in the thinking mode must be passed back..."

    Args:
        exc: 待检查的异常

    Returns:
        bool: 是否为思考回传缺失导致的 400
    """
    error_msg = str(exc).lower()
    return "thinking" in error_msg and "passed back" in error_msg


def is_reasoning_item_passback_error(message: str) -> bool:
    """检测是否为 reasoning item 回传校验失败（Responses 思考回传 400）。

    已知错误文案（OpenAI 官方及兼容端点措辞略有差异）：
    - "Item 'rs_...' of type 'reasoning' was provided without its required
      following item"
    - "Item 'fc_...' of type 'function_call' was provided without its
      required 'rs_...' item"

    Args:
        message: 错误消息文本

    Returns:
        bool: 是否为 reasoning item 回传校验失败
    """
    lowered = message.lower()
    if "reasoning" not in lowered and "rs_" not in lowered:
        return False
    return (
        "without its required" in lowered
        or "required following item" in lowered
        or "must be passed back" in lowered
        or ("function_call" in lowered and "reasoning item" in lowered)
    )


def model_consumes_thinking_passback(model: str) -> bool:
    """判断目标模型是否要求思考内容回传（DeepSeek 家族思考模型）。

    仅门控 DeepSeek：Claude 等原生 Anthropic 模型对 thinking 块校验
    signature，合成占位块会被拒绝；GLM/Qwen 等 Anthropic 兼容端点不要求
    回传，无需干预。

    已知局限：按模型名识别，若某网关把无需回传的模型别名成 deepseek 系
    名称，会主动补入无签名的占位块——该端点若校验签名（Claude 风格），
    请求会被拒且错误不匹配回传启发式。实践中 DeepSeek 系端点不校验
    签名（自身返回的 signature 即为 null），风险可接受；reactive 自愈
    （_is_thinking_passback_error）仅在端点真实返回回传错误时触发。

    Args:
        model: 模型名称

    Returns:
        bool: 是否为要求思考回传的 DeepSeek 家族模型
    """
    return "deepseek" in (model or "").lower()


def sanitize_tool_artifacts(raw: str) -> str:
    """清理模型输出中的工具调用残留标签。"""
    if not raw:
        return ""
    return (
        raw.replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
        .replace("\u0000", "")
    ).replace("\t", "    ")


def strip_tool_call_artifacts(raw: str) -> str:
    """移除 DeepSeek/类 XML 工具调用残留，避免污染用户可见文本。"""
    if not raw:
        return ""
    cleaned = _DSML_TOOL_CALL_PREFIX_RE.sub("", raw)
    cleaned = _TOOL_CALL_XML_BLOCK_RE.sub("", cleaned)
    cleaned = _TOOL_CALL_XML_TAG_RE.sub("", cleaned)
    return cleaned


def split_thinking_from_text(raw: str) -> tuple[str, str]:
    """从文本中提取 `<think>` 内容，并返回正文与思考文本。"""
    if not raw:
        return "", ""
    source = strip_tool_call_artifacts(sanitize_tool_artifacts(raw))
    thinking_parts = [m.group(1).strip() for m in _THINK_BLOCK_RE.finditer(source) if m.group(1).strip()]
    without_full_blocks = _THINK_BLOCK_RE.sub("", source)

    dangling_open = _THINK_OPEN_TAG_RE.search(without_full_blocks)
    if dangling_open:
        tail = without_full_blocks[dangling_open.end():].strip()
        if tail:
            thinking_parts.append(tail)
        without_full_blocks = without_full_blocks[:dangling_open.start()]

    plain = _THINK_OPEN_TAG_RE.sub("", without_full_blocks)
    plain = _THINK_CLOSE_TAG_RE.sub("", plain).strip()
    thinking = merge_reasoning_text(*thinking_parts)
    return plain, thinking


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """将工具参数解析为字典，兼容常见非标准格式。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}

    text = raw.strip()
    if not text:
        return {}

    fenced = _JSON_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    parsed = _parse_json_dict(text)
    if parsed:
        return parsed

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        parsed = _parse_json_dict(text[first_brace : last_brace + 1].strip())
        if parsed:
            return parsed

    try:
        literal = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return {}
    return literal if isinstance(literal, dict) else {}


def merge_reasoning_text(*parts: str) -> str:
    """合并多个推理文本片段并去重。"""
    merged: list[str] = []
    for part in parts:
        cleaned = strip_tool_call_artifacts(sanitize_tool_artifacts(part)).strip()
        if not cleaned:
            continue
        candidate = _normalize_compare_text(cleaned)
        if not candidate:
            continue
        normalized_existing = [_normalize_compare_text(value) for value in merged]
        if any(existing == candidate or candidate in existing for existing in normalized_existing):
            continue
        merged = [value for value in merged if _normalize_compare_text(value) not in candidate]
        merged.append(cleaned)
    return "\n\n".join(merged).strip()


def _parse_json_dict(text: str) -> dict[str, Any]:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _normalize_compare_text(raw: str) -> str:
    return _WHITESPACE_RE.sub(" ", raw).strip()

