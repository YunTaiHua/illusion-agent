"""
API 模块
========

本模块提供 IllusionAgent 与各种 LLM 提供商的 API 集成。

主要组件：
    - AnthropicApiClient: Anthropic API 客户端
    - OpenAICompatibleClient: OpenAI 兼容 API 客户端
    - CodexApiClient: OpenAI Codex 客户端
    - ResponsesApiClient: 通用 OpenAI Responses API 客户端
    - IllusionAgentApiError: API 异常基类
    - UsageSnapshot: 使用量追踪

使用示例：
    >>> from illusion.api import AnthropicApiClient
    >>> client = AnthropicApiClient(api_key="sk-...")
"""

from illusion.api.auth_status import auth_status
from illusion.api.client import AnthropicApiClient
from illusion.api.codex_client import CodexApiClient
from illusion.api.errors import IllusionAgentApiError
from illusion.api.openai_client import OpenAICompatibleClient
from illusion.api.responses_client import ResponsesApiClient
from illusion.api.usage import UsageSnapshot

__all__ = [
    "AnthropicApiClient",
    "CodexApiClient",
    "IllusionAgentApiError",
    "OpenAICompatibleClient",
    "ResponsesApiClient",
    "UsageSnapshot",
    "auth_status",
]
