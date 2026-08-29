"""
MCP 配置和状态模型
==================

本模块定义 MCP（Model Context Protocol）相关的配置和数据类型。

主要功能：
    - 定义 MCP 服务器配置（STDIO、HTTP）
    - 定义 MCP 工具和资源信息
    - 定义 MCP 连接状态

类说明：
    - McpStdioServerConfig: STDIO 类型 MCP 服务器配置
    - McpHttpServerConfig: HTTP 类型 MCP 服务器配置（Streamable HTTP）
    - McpSseServerConfig: SSE 类型 MCP 服务器配置
    - McpServerConfig: MCP 服务器配置联合类型（discriminated union）
    - McpJsonConfig: 配置文件格式（用于插件和项目文件）
    - McpToolInfo: MCP 工具元数据
    - McpResourceInfo: MCP 资源元数据
    - McpConnectionStatus: MCP 服务器运行时状态

使用示例：
    >>> from illusion.mcp.types import McpStdioServerConfig
    >>> config = McpStdioServerConfig(command="node", args=["server.js"])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, Field, model_validator

# MCP 工具调用时需要捕获的异常类型，统一在此定义避免散落在各工具中。
# ValueError: 配置/参数错误（如服务器未找到、URI 格式错误）
# MCPError: MCP 协议层错误（服务器返回的错误响应）
MCP_TOOL_EXCEPTIONS: tuple[type[BaseException], ...] = (ValueError, MCPError)


def _normalize_server_config_type(config: Any) -> Any:
    """当配置 dict 缺少 type 字段时，默认设为 stdio。

    大部分 MCP 服务器为 stdio 类型，省略 type 字段时按 stdio 处理，
    避免用户在每个配置中重复填写 ``"type": "stdio"``。

    Args:
        config: 单个 MCP 服务器配置（dict 或其他形式）

    Returns:
        补全 type 字段后的配置（返回副本，不修改原始数据）
    """
    if isinstance(config, dict) and "type" not in config:
        return {**config, "type": "stdio"}
    return config


class McpStdioServerConfig(BaseModel):
    """
    STDIO 类型 MCP 服务器配置

    通过标准输入输出流与 MCP 服务器通信的配置。
    此为默认类型：当配置省略 type 字段时按 stdio 处理。

    Attributes:
        type: 服务器类型，固定为 "stdio"（省略时默认 stdio）
        command: 要执行的命令
        args: 命令参数列表
        env: 环境变量字典
        cwd: 工作目录
        log_file: stderr 日志重定向文件路径，设置后 MCP 服务器的 stderr 输出将写入该文件
    """

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    log_file: str | None = None
    enabled: bool = True


class McpHttpServerConfig(BaseModel):
    """
    HTTP 类型 MCP 服务器配置（Streamable HTTP 传输）
    
    通过 Streamable HTTP 协议与 MCP 服务器通信的配置。
    兼容多种 type 别名：http、streamable-http、streamableHttp、streamable_http、streamablehttp。
    
    Attributes:
        type: 服务器类型，支持 "http"/"streamable-http"/"streamableHttp"/"streamable_http"/"streamablehttp"
        url: 服务器 URL 地址
        headers: HTTP 请求头字典
    """

    type: Literal["http", "streamable-http", "streamableHttp", "streamable_http", "streamablehttp"] = "http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class McpSseServerConfig(BaseModel):
    """
    SSE 类型 MCP 服务器配置
    
    通过 Server-Sent Events 协议与 MCP 服务器通信的配置。
    
    Attributes:
        type: 服务器类型，固定为 "sse"
        url: 服务器 URL 地址
        headers: HTTP 请求头字典
    """

    type: Literal["sse"] = "sse"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


# MCP 服务器配置联合类型，使用 discriminator 按 type 字段精确分发，
# 避免 Pydantic smart union 在字段缺失时产生歧义。
# 支持 STDIO、HTTP（Streamable HTTP）、SSE 三种传输方式。
# 注意：discriminator 要求 type 字段存在，省略 type 时由上层
# （McpJsonConfig/Settings/配置加载器）预处理补全为 "stdio"。
McpServerConfig = Annotated[
    McpStdioServerConfig | McpHttpServerConfig | McpSseServerConfig,
    Field(discriminator="type"),
]


class McpJsonConfig(BaseModel):
    """
    MCP 配置文件格式

    用于插件和项目文件中的 MCP 服务器配置格式。
    省略 type 字段的服务器配置默认按 stdio 类型处理。

    Attributes:
        mcp_servers: MCP 服务器名称到配置的映射字典
    """

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _default_type_to_stdio(cls, data: Any) -> Any:
        """解析前为缺少 type 字段的服务器配置补全 ``type: "stdio"``。"""
        if not isinstance(data, dict):
            return data
        key = "mcpServers" if "mcpServers" in data else "mcp_servers" if "mcp_servers" in data else None
        if key is None:
            return data
        servers = data[key]
        if not isinstance(servers, dict):
            return data
        data[key] = {
            name: _normalize_server_config_type(cfg)
            for name, cfg in servers.items()
        }
        return data


@dataclass
class McpImageContent:
    """MCP 图片内容块（如 Browser Use 截图）。

    Attributes:
        data: base64 编码的图片数据
        mime_type: MIME 类型（image/png 等）
    """

    data: str
    mime_type: str = "image/png"


@dataclass
class McpToolCallResult:
    """MCP 工具调用结果（保留内容块细节）。

    Attributes:
        text: 文本内容（非文本块序列化为 JSON 文本）
        images: 图片内容块列表（call_tool 会将其折叠进文本，此接口保留）
        is_error: 服务器是否标记为错误
    """

    text: str
    images: list[McpImageContent] = field(default_factory=list)
    is_error: bool = False


@dataclass(frozen=True)
class McpToolInfo:
    """
    MCP 工具信息
    
    MCP 服务器暴露的工具元数据，包含工具名称、描述和输入模式。
    
    Attributes:
        server_name: 所属服务器名称
        name: 工具名称
        description: 工具描述
        input_schema: 工具输入参数的 JSON Schema 定义
    """

    server_name: str
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class McpResourceInfo:
    """
    MCP 资源信息
    
    MCP 服务器暴露的资源元数据，包含资源名称、URI 和描述。
    
    Attributes:
        server_name: 所属服务器名称
        name: 资源名称
        uri: 资源统一标识符
        description: 资源描述
    """

    server_name: str
    name: str
    uri: str
    description: str = ""


@dataclass
class McpConnectionStatus:
    """
    MCP 连接状态
    
    MCP 服务器的运行时状态信息，包含连接状态、传输类型、认证配置、工具和资源列表。
    
    Attributes:
        name: 服务器名称
        state: 连接状态（connected/failed/pending/disabled）
        detail: 状态详情或错误信息
        transport: 传输类型（stdio/http/ws）
        auth_configured: 是否配置了认证
        tools: 该服务器提供的工具列表
        resources: 该服务器提供的资源列表
    """

    name: str
    state: Literal["connected", "failed", "pending", "disabled"]
    detail: str = ""
    transport: str = "unknown"
    auth_configured: bool = False
    tools: list[McpToolInfo] = field(default_factory=list)
    resources: list[McpResourceInfo] = field(default_factory=list)
