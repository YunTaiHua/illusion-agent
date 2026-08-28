"""
MCP 客户端管理器模块
===================

本模块提供 MCP（Model Context Protocol）客户端管理功能。

主要功能：
    - 管理 MCP 服务器连接
    - 暴露 MCP 工具和资源
    - 支持 STDIO、HTTP（Streamable HTTP）、SSE 传输类型
    - 提供工具调用和资源读取接口

类说明：
    - McpClientManager: MCP 客户端管理器类

使用示例：
    >>> from illusion.mcp.client import McpClientManager
    >>> from illusion.mcp.types import McpStdioServerConfig
    >>> 
    >>> configs = {"my_server": McpStdioServerConfig(command="node", args=["server.js"])}
    >>> manager = McpClientManager(configs)
    >>> await manager.connect_all()
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, ReadResourceResult

from illusion.mcp.types import (
    McpConnectionStatus,
    McpHttpServerConfig,
    McpResourceInfo,
    McpSseServerConfig,
    McpStdioServerConfig,
    McpToolInfo,
)

if TYPE_CHECKING:
    # typing.Self 仅用于类型注解（from __future__ import annotations 使其不在运行时求值），
    # 通过 TYPE_CHECKING 守卫以兼容 Python 3.10
    from typing import Self

logger = logging.getLogger(__name__)

# MCP 服务器 stderr 日志保留天数
_MCP_LOG_TTL_DAYS = 7
# MCP 服务器 stderr 日志体积兜底阈值（10MB）：MCP 子进程持续写 stderr
# 会刷新 mtime，仅按年龄清理永不触发，需叠加体积阈值避免无限增长
_MCP_LOG_MAX_SIZE_BYTES = 10 * 1024 * 1024


class McpClientManager:
    """
    MCP 客户端管理器

    管理与 MCP 服务器的连接，并暴露服务器提供的工具和资源。
    支持 STDIO、HTTP（Streamable HTTP）、SSE 传输类型。

    Attributes:
        _server_configs: 服务器名称到配置的映射
        _statuses: 服务器名称到连接状态的映射
        _sessions: 服务器名称到客户端会话的映射
        _stacks: 服务器名称到异步退出栈的映射

    使用示例：
        >>> manager = McpClientManager(configs)
        >>> await manager.connect_all()
        >>> tools = manager.list_tools()
    """

    def __init__(self, server_configs: dict[str, object]) -> None:
        """
        初始化 MCP 客户端管理器
        
        Args:
            server_configs: 服务器名称到配置的映射字典
        """
        self._server_configs = server_configs
        # 初始化所有服务器状态为 pending（待连接）
        self._statuses: dict[str, McpConnectionStatus] = {
            name: McpConnectionStatus(
                name=name,
                state="pending",
                transport=getattr(config, "type", "unknown"),
            )
            for name, config in server_configs.items()
        }
        self._sessions: dict[str, ClientSession] = {}  # 存储活跃的客户端会话
        self._stacks: dict[str, AsyncExitStack] = {}   # 存储异步上下文管理器栈

    async def connect_all(self) -> None:
        """
        连接所有已配置的 MCP 服务器

        并行连接所有服务器以加速启动，支持 STDIO、HTTP（Streamable HTTP）、
        SSE 三种传输类型。
        """
        # 收集需要并行连接的任务
        connect_coros: list[Any] = []
        for name, config in self._server_configs.items():
            if isinstance(config, McpStdioServerConfig):
                connect_coros.append(self._connect_stdio(name, config))
            elif isinstance(config, (McpHttpServerConfig, McpSseServerConfig)):
                connect_coros.append(self._connect_remote(name, config))
            else:
                # 未知传输类型标记为失败
                transport_type = getattr(config, "type", "unknown")
                self._statuses[name] = McpConnectionStatus(
                    name=name,
                    state="failed",
                    transport=transport_type,
                    detail=f"Unsupported MCP transport: {transport_type}",
                )

        # 并行连接所有服务器
        if connect_coros:
            tasks = [asyncio.create_task(coro) for coro in connect_coros]
            # return_exceptions=True 确保单个服务器连接失败不影响其他服务器；
            # 各 _connect_* 方法已自行捕获异常并标记 failed status，
            # 此处仅记录非预期的逃逸异常。
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, (KeyboardInterrupt, SystemExit)
                ):
                    logger.warning("MCP connection task error: %s", result)

    async def reconnect_all(self) -> None:
        """
        重新连接所有已配置的服务器
        
        先关闭所有现有连接，然后重置状态并重新建立连接。
        """
        await self.close()
        # 重置所有服务器状态为 pending
        self._statuses = {
            name: McpConnectionStatus(name=name, state="pending", transport=getattr(config, "type", "unknown"))
            for name, config in self._server_configs.items()
        }
        await self.connect_all()

    def update_server_config(self, name: str, config: object) -> None:
        """
        替换内存中的服务器配置
        
        Args:
            name: 服务器名称
            config: 新的服务器配置对象
        """
        self._server_configs[name] = config

    def get_server_config(self, name: str) -> object | None:
        """
        获取指定的服务器配置
        
        Args:
            name: 服务器名称
        
        Returns:
            服务器配置对象，如果不存在则返回 None
        """
        return self._server_configs.get(name)

    async def close(self) -> None:
        """
        关闭所有活跃的 MCP 会话

        释放所有资源，包括关闭流和清理会话。
        """
        # 关闭所有异步上下文栈
        for stack in list(self._stacks.values()):
            try:
                await stack.aclose()
            except RuntimeError:
                # 当 connect_all 使用 asyncio.gather 并行连接时，
                # cancel scope 在子任务中进入但在主任务中退出，
                # anyio 会拒绝跨任务退出 cancel scope。
                # 此时子进程资源会随进程退出自动回收。
                pass
        self._stacks.clear()
        self._sessions.clear()

    async def __aenter__(self) -> Self:
        """异步上下文管理器入口，自动连接所有服务器。"""
        await self.connect_all()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """异步上下文管理器出口，自动关闭所有连接。"""
        await self.close()

    def list_statuses(self) -> list[McpConnectionStatus]:
        """
        获取所有已配置服务器的状态列表
        
        Returns:
            按服务器名称排序的连接状态列表
        """
        return [self._statuses[name] for name in sorted(self._statuses)]

    def list_tools(self) -> list[McpToolInfo]:
        """
        获取所有已连接 MCP 服务器提供的工具列表
        
        Returns:
            合并后的工具信息列表
        """
        tools: list[McpToolInfo] = []
        for status in self.list_statuses():
            tools.extend(status.tools)
        return tools

    def list_resources(self, *, server_name: str | None = None) -> list[McpResourceInfo]:
        """
        获取所有已连接 MCP 服务器提供的资源列表
         
        Returns:
            合并后的资源信息列表
        """
        if server_name is not None:
            status = self._statuses.get(server_name)
            if status is None:
                return []
            return list(status.resources)
        resources: list[McpResourceInfo] = []
        for status in self.list_statuses():
            resources.extend(status.resources)
        return resources

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        调用指定的 MCP 工具

        在指定服务器上调用工具并返回字符串形式的结果。

        Args:
            server_name: 服务器名称
            tool_name: 工具名称
            arguments: 工具参数字典

        Returns:
            工具执行结果的字符串形式
        """
        session = self._require_session(server_name)
        result: CallToolResult = await session.call_tool(tool_name, arguments)
        parts: list[str] = []
        # 处理返回的内容，支持文本、图片和其他类型。
        # 图片（base64）直接拼进结果会撑爆 LLM 上下文，改为落盘后返回本地路径。
        for item in result.content:
            item_type = getattr(item, "type", None)
            if item_type == "text":
                parts.append(getattr(item, "text", ""))
            elif item_type == "image":
                # 图片落盘（磁盘 I/O 放线程池，避免阻塞事件循环）
                parts.append(await asyncio.to_thread(_save_mcp_image, item, server_name))
            else:
                parts.append(item.model_dump_json())
        # 把 structured_content 的关键字段（如 cua-driver 快照的 snapshot_id）
        # 提供给 LLM：没有它，element_index 无法寻址（bare index 会失败）。
        # 不追加完整 structured_content——可能含 base64 截图等超大字段，
        # 直接拼进结果会撑爆上下文。
        structured_summary = _summarize_structured_content(result.structured_content)
        if structured_summary:
            parts.append(structured_summary)
        # 如果没有输出，返回默认消息
        if not parts:
            parts.append("(no output)")
        return "\n".join(parts).strip()

    async def read_resource(self, server_name: str, uri: str) -> str:
        """
        读取指定的 MCP 资源
        
        从指定服务器读取资源并返回字符串形式的内容。
        
        Args:
            server_name: 服务器名称
            uri: 资源统一标识符
        
        Returns:
            资源内容的字符串形式
        """
        session = self._require_session(server_name)
        result: ReadResourceResult = await session.read_resource(uri)
        parts: list[str] = []
        for item in result.contents:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(getattr(item, "blob", "")))
        return "\n".join(parts).strip()

    async def _finalize_session(
        self,
        name: str,
        config: Any,
        session: ClientSession,
        stack: AsyncExitStack,
        *,
        auth_configured: bool,
    ) -> None:
        """
        初始化会话并获取工具/资源列表，成功后记录连接状态

        所有传输类型（STDIO/HTTP/SSE）共用的公共逻辑：
        session.initialize() → list_tools → list_resources → 记录状态。
        失败时抛出异常，由调用方负责清理 stack 并标记 failed。

        Args:
            name: 服务器名称
            config: 服务器配置（用于读取 type 等元信息）
            session: 已建立的客户端会话
            stack: 关联的异步退出栈
            auth_configured: 是否配置了认证信息
        """
        await session.initialize()
        # 获取服务器提供的工具列表
        tool_result = await session.list_tools()
        tools = [
            McpToolInfo(
                server_name=name,
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.input_schema or {"type": "object", "properties": {}}),
            )
            for tool in tool_result.tools
        ]
        # 获取服务器提供的资源列表（可选能力，服务器可能不支持）
        resources: list[McpResourceInfo] = []
        try:
            resource_result = await session.list_resources()
            resources = [
                McpResourceInfo(
                    server_name=name,
                    name=resource.name or str(resource.uri),
                    uri=str(resource.uri),
                    description=resource.description or "",
                )
                for resource in resource_result.resources
            ]
        except MCPError as exc:
            # 服务器不支持 resources 能力（返回 JSON-RPC 错误）或请求超时
            logger.debug("Server %s does not support resources: %s", name, exc)
        # 获取资源模板（部分服务器只暴露模板，不暴露静态资源）
        try:
            template_result = await session.list_resource_templates()
            template_items = getattr(template_result, "resource_templates", None)
            if template_items is None:
                template_items = getattr(template_result, "resource_templates", [])
            for template in template_items or []:
                template_uri = str(
                    getattr(template, "uri_template", None)
                    or getattr(template, "uri_template", None)
                    or ""
                ).strip()
                if not template_uri:
                    continue
                if any(item.uri == template_uri for item in resources):
                    continue
                resources.append(
                    McpResourceInfo(
                        server_name=name,
                        name=getattr(template, "name", None) or template_uri,
                        uri=template_uri,
                        description=getattr(template, "description", "") or "",
                    )
                )
        except MCPError as exc:
            # 服务器不支持 resource templates 能力（返回 JSON-RPC 错误）或请求超时
            logger.debug("Server %s does not support resource templates: %s", name, exc)
        # 保存会话和栈
        self._sessions[name] = session
        self._stacks[name] = stack
        # 更新连接状态为已连接
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state="connected",
            transport=config.type,
            auth_configured=auth_configured,
            tools=tools,
            resources=resources,
        )

    async def _connect_stdio(self, name: str, config: McpStdioServerConfig) -> None:
        """
        连接 STDIO 类型的 MCP 服务器

        建立与 STDIO 服务器的连接，初始化会话，并获取服务器提供的工具和资源列表。

        Args:
            name: 服务器名称
            config: STDIO 服务器配置
        """
        stack = AsyncExitStack()
        try:
            # 确定 stderr 输出目标：默认重定向到统一日志目录下的文件，
            # 若用户配置了 log_file 则使用用户指定路径
            if config.log_file:
                log_path = Path(config.log_file)
            else:
                from illusion.config.paths import get_mcp_log_path
                log_path = get_mcp_log_path(name)
            # 先清理超龄/超大的旧 MCP 日志（统一走 log_cleanup 工具）。
            # MCP 子进程持续写 stderr 会刷新 mtime，故叠加体积阈值兜底。
            # 仅对默认日志目录下的 mcp_*.log 做批量清理；用户自定义路径
            # 只清理该单文件，避免误删用户目录下的其他文件。
            from illusion.utils.log_cleanup import cleanup_old_files
            cleanup_old_files(
                log_path.parent,
                "mcp_*.log" if config.log_file is None else log_path.name,
                max_age_days=_MCP_LOG_TTL_DAYS,
                max_size_bytes=_MCP_LOG_MAX_SIZE_BYTES,
            )
            # mkdir + open 是阻塞操作，通过 to_thread 在工作线程中执行
            def _open_log_file(p: Path) -> TextIO:
                p.parent.mkdir(parents=True, exist_ok=True)
                return open(p, "a", encoding="utf-8")
            errlog = await asyncio.to_thread(_open_log_file, log_path)
            stack.callback(errlog.close)

            # 创建 STDIO 客户端连接
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=config.command,
                        args=config.args,
                        env=config.env,
                        cwd=config.cwd,
                    ),
                    errlog=errlog,
                )
            )
            # 创建客户端会话并完成公共初始化流程
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await self._finalize_session(name, config, session, stack, auth_configured=bool(config.env))
        except (OSError, MCPError, ConnectionError, TimeoutError, RuntimeError) as exc:
            # 连接失败，清理资源并更新状态
            logger.debug("Failed to connect MCP stdio server %s: %s", name, exc)
            await stack.aclose()
            self._statuses[name] = McpConnectionStatus(
                name=name,
                state="failed",
                transport=config.type,
                auth_configured=bool(config.env),
                detail=str(exc),
            )

    async def _connect_remote(self, name: str, config: Any) -> None:
        """
        连接远程 MCP 服务器（HTTP/SSE/WebSocket）

        根据配置类型选择对应的传输方式建立连接，复用公共的会话初始化流程。

        Args:
            name: 服务器名称
            config: 远程服务器配置（McpHttpServerConfig/McpSseServerConfig）
        """
        headers_configured = bool(getattr(config, "headers", None))
        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await self._open_remote_transport(stack, config)
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await self._finalize_session(
                name, config, session, stack, auth_configured=headers_configured
            )
        except (
            OSError, MCPError, ConnectionError, TimeoutError, RuntimeError, asyncio.CancelledError,
        ) as exc:
            # 远程传输（streamablehttp/sse）内部使用 anyio task group，
            # 连接失败时会通过 cancel scope 取消任务产生 CancelledError（BaseException
            # 子类，绕过 except Exception），需显式捕获以确保标记为 failed。
            # stack.aclose() 可能因跨 task 退出 cancel scope 抛 RuntimeError，
            # 此时传输资源随 session 销毁自动回收。
            logger.debug("Failed to connect MCP remote server %s: %s", name, exc)
            try:
                await stack.aclose()
            except RuntimeError as close_exc:
                # 跨 task 退出 cancel scope 时 anyio 抛 RuntimeError，资源随 session 销毁自动回收
                logger.debug("stack.aclose() during remote connect cleanup raised: %s", close_exc)
            self._statuses[name] = McpConnectionStatus(
                name=name,
                state="failed",
                transport=config.type,
                auth_configured=headers_configured,
                detail=str(exc) or type(exc).__name__,
            )

    async def _open_remote_transport(self, stack: AsyncExitStack, config: Any) -> tuple[Any, Any]:
        """
        根据配置类型打开对应的远程传输流

        Args:
            stack: 异步退出栈，传输流的生命周期由其管理
            config: 远程服务器配置

        Returns:
            (read_stream, write_stream) 二元组

        Raises:
            ValueError: 不支持的远程传输类型
        """
        headers = dict(getattr(config, "headers", None) or {})
        if isinstance(config, McpHttpServerConfig):
            # MCP v2: streamable_http_client 返回 (read, write) 二元组
            # headers/timeout/auth 配置在 httpx2.AsyncClient 上
            http_client = httpx2.AsyncClient(
                headers=headers or None,
                timeout=httpx2.Timeout(30, read=300),
                follow_redirects=True,
            )
            read, write = await stack.enter_async_context(
                streamable_http_client(url=config.url, http_client=http_client)
            )
            return read, write
        if isinstance(config, McpSseServerConfig):
            read, write = await stack.enter_async_context(
                sse_client(url=config.url, headers=headers or None)
            )
            return read, write
        raise ValueError(f"Unsupported remote MCP transport: {type(config).__name__}")

    def _require_session(self, server_name: str) -> ClientSession:
        """获取服务器会话，不存在时抛出可读错误。"""
        session = self._sessions.get(server_name)
        if session is not None:
            return session
        status = self._statuses.get(server_name)
        if status is None:
            raise ValueError(f"Unknown MCP server: {server_name}")
        detail = f" ({status.detail})" if status.detail else ""
        raise ValueError(
            f"MCP server '{server_name}' is not connected (state={status.state}{detail})"
        )


# MIME 类型 -> 文件扩展名
_MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


def _mime_to_ext(mime_type: str) -> str:
    """把 MIME 类型映射为文件扩展名，未知类型默认 .png。"""
    key = mime_type.strip().lower().split(";")[0].strip()
    return _MIME_TO_EXT.get(key, ".png")


def _summarize_structured_content(sc: Any) -> str | None:
    """从 MCP structured_content 提取紧凑关键信息。

    仅提取对 LLM 有用的短字段（如 cua-driver 快照的 ``snapshot_id``），
    排除超大字段（base64 截图、完整 elements 数组等）以免撑爆上下文。

    Args:
        sc: MCP structured_content（dict 或 None）

    Returns:
        str | None: 提取到的关键信息；无可提取内容时返回 None
    """
    if not isinstance(sc, dict):
        return None
    summary: list[str] = []
    snapshot_id = sc.get("snapshot_id")
    if isinstance(snapshot_id, str) and snapshot_id:
        summary.append(f"snapshot_id={snapshot_id}")
    return "\n".join(summary) if summary else None


def _save_mcp_image(item: Any, server_name: str) -> str:
    """把 MCP 图片内容（base64）保存到缓存目录，返回本地路径描述。

    图片直接拼进工具结果会撑爆 LLM 上下文；此处解码落盘到
    ``~/.illusion/cache/<服务器>/``（插件服务器键形如 "插件名:服务器名"，
    取插件名作子目录，如 ``computer:cua`` -> ``~/.illusion/cache/computer``），
    并在结果中返回路径供 LLM 参考。

    该行为对所有 MCP 服务器生效（属于有意的通用优化：任何服务器的图片
    结果都会撑爆上下文），各服务器按各自子目录隔离。

    Args:
        item: MCP 图片内容对象（type="image"，含 base64 data 与 mime_type）
        server_name: MCP 服务器键

    Returns:
        str: 落盘结果描述；保存失败时返回降级描述
    """
    import base64
    import re
    import uuid

    from illusion.config.paths import get_config_dir
    from illusion.utils.log_cleanup import cleanup_old_files

    data = getattr(item, "data", "") or ""
    mime_type = getattr(item, "mime_type", "") or "image/png"
    ext = _mime_to_ext(mime_type)
    # 插件服务器键形如 "插件名:服务器名"，用插件名作子目录
    server_dir = re.sub(r"[^A-Za-z0-9_-]", "_", server_name.split(":", 1)[0]) or "mcp"
    cache_dir = get_config_dir() / "cache" / server_dir
    try:
        if not data:
            return "[Image content: empty data (unable to save)]"
        cache_dir.mkdir(parents=True, exist_ok=True)
        raw = base64.b64decode(data)
        path = cache_dir / f"{server_dir}_{uuid.uuid4().hex[:12]}{ext}"
        path.write_bytes(raw)
        # 截图缓存只增不删会持续占用磁盘：按 7 天 TTL + 单文件体积上限清理
        # 旧图（与日志清理同一策略；每次落盘扫描代价极低）
        cleanup_old_files(
            cache_dir,
            "*",
            max_age_days=7,
            max_size_bytes=50 * 1024 * 1024,
        )
        return f"[Image saved to {path}]"
    except (TypeError, ValueError, OSError) as exc:
        logger.warning("failed to save MCP image from %s: %s", server_name, exc)
        return f"[Image content: {len(data)} chars base64 (unable to save)]"
