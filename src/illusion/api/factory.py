"""
API 客户端工厂
==============

按 env 配置独立构建 API 客户端，供跨环境场景使用（记忆提取/整合
子代理等指定了其他 env 的模型时，必须使用该 env 的
端点与凭据构建 client，而不是复用主对话的 client——否则会出现
"Unsupported model" 400 错误）。

构建逻辑与 runtime._rebuild_api_client 对齐（copilot/codex/anthropic/
openai 兼容四种格式）。

函数说明：
    - build_api_client_for_env: 按指定 env 构建 API 客户端
"""

from __future__ import annotations

import logging
from typing import Any

from illusion.api.client import AnthropicApiClient, SupportsStreamingMessages
from illusion.api.openai_client import OpenAICompatibleClient

logger = logging.getLogger(__name__)


def build_api_client_for_env(settings: Any, env_key: str) -> SupportsStreamingMessages:
    """为指定 env 构建独立 API 客户端。

    Args:
        settings: Settings 实例
        env_key: 环境键名（如 env_1）

    Returns:
        SupportsStreamingMessages: 按 env 配置构建的客户端

    Raises:
        ValueError: env 不存在或认证配置缺失时抛出
    """
    env = settings.get_env(env_key)
    if env is None:
        raise ValueError(f"Unknown env: {env_key}")

    api_format = env.api_format
    base_url = env.base_url

    if api_format == "copilot":
        from illusion.auth.copilot import CopilotAuth, copilot_extra_headers

        _copilot = CopilotAuth()
        _copilot_token = _copilot.get_valid_token()
        return OpenAICompatibleClient(  # type: ignore[return-value]
            api_key=_copilot_token,
            base_url=base_url or "https://api.githubcopilot.com",
            extra_headers=copilot_extra_headers(),
        )
    if api_format == "codex":
        from illusion.api.codex_client import CodexApiClient
        from illusion.auth.codex_oauth import CodexOAuth

        return CodexApiClient(  # type: ignore[return-value]
            auth_token_resolver=CodexOAuth().get_valid_token,
            base_url=base_url,
        )
    # 仅 anthropic/openai 兼容格式需要 env 凭据解析；
    # copilot/codex 是 token 认证流程（OAuth 全局单例），解析会抛 ValueError。
    # 对齐 runtime._rebuild_api_client：auth 解析放格式分支内。
    auth = settings.resolve_auth_for(env_key)
    if api_format == "anthropic":
        return AnthropicApiClient(  # type: ignore[return-value]
            api_key=auth.value if auth.auth_kind == "api_key" else None,
            base_url=base_url,
            auth_token=auth.value if auth.auth_kind == "auth_token" else None,
        )
    # "openai" 及其他 OpenAI 兼容格式之前：通用 Responses API 格式
    if api_format == "response":
        from illusion.api.responses_client import ResponsesApiClient

        return ResponsesApiClient(  # type: ignore[return-value]
            api_key=auth.value if auth.auth_kind == "api_key" else None,
            base_url=base_url,
            auth_token=auth.value if auth.auth_kind == "auth_token" else None,
        )
    # "openai" 及其他 OpenAI 兼容格式
    return OpenAICompatibleClient(  # type: ignore[return-value]
        api_key=auth.value,
        base_url=base_url,
    )
