"""
Runtime 运行时模块
================

本模块实现无 UI 和 Textual UI 共享的运行时程序集。

主要功能：
    - 运行时数据 bundle 管理
    - API 客户端初始化和配置
    - 工具注册和权限检查
    - 会话状态管理
    - 命令处理和执行
    - 会话快照保存

类说明：
    - RuntimeBundle: 共享运行时数据bundle
    - build_runtime: 构建运行时
    - start_runtime: 启动运行时（执行会话开始钩子）
    - close_runtime: 关闭运行时并清理资源
    - handle_line: 处理用户输入行
    - sync_app_state: 同步应用状态

使用示例：
    >>> from illusion.ui.runtime import build_runtime, handle_line, start_runtime, close_runtime
    >>> 
    >>> # 构建运行时
    >>> bundle = await build_runtime(model="claude-sonnet-4-20250514")
    >>> await start_runtime(bundle)
    >>> 
    >>> # 处理输入行
    >>> await handle_line(
    ...     bundle,
    ...     "帮我写一个 hello world 程序",
    ...     print_system=print_system,
    ...     render_event=render_event,
    ...     clear_output=clear_output,
    ... )
    >>> 
    >>> # 关闭运行时
    >>> await close_runtime(bundle)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

from illusion.api.auth_status import auth_status
from illusion.api.client import AnthropicApiClient, SupportsStreamingMessages
from illusion.api.effort import EffortMapper
from illusion.api.openai_client import OpenAICompatibleClient
from illusion.commands import CommandContext, CommandResult, create_default_command_registry
from illusion.commands.registry import CommandRegistry
from illusion.config import load_settings
from illusion.config.settings import Settings
from illusion.engine import QueryEngine
from illusion.engine.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from illusion.engine.query import BackgroundAgentTracker, MaxTurnsExceeded
from illusion.engine.stream_events import StreamEvent
from illusion.goal.manager import GoalManager
from illusion.goal.prompts import is_goal_system_message
from illusion.hooks import HookEvent, HookExecutionContext, HookExecutor
from illusion.hooks.loader import load_hook_registry
from illusion.hooks.types import AggregatedHookResult
from illusion.mcp.client import McpClientManager
from illusion.mcp.config import load_mcp_server_configs
from illusion.permissions import PermissionChecker
from illusion.plugins.loader import load_plugins
from illusion.plugins.types import LoadedPlugin
from illusion.prompts import build_runtime_system_prompt
from illusion.state import AppState, AppStateStore
from illusion.tasks.types import TaskRecord, is_task_notification
from illusion.tools import ToolRegistry, create_default_tool_registry

# 类型别名定义
PermissionPrompt = Callable[[str, str, bool], Awaitable[bool]]  # 权限确认回调（tool_name, question, high_risk）
AskUserPrompt = Callable[[str, object], Awaitable[str]]  # 用户问答回调
PlanApprovalPrompt = Callable[[str], Awaitable[tuple[bool, str]]]  # 计划审批回调
SystemPrinter = Callable[[str], Awaitable[None]]  # 系统消息打印回调
StreamRenderer = Callable[[StreamEvent], Awaitable[None]]  # 流式事件渲染回调
ClearHandler = Callable[[], Awaitable[None]]  # 清空输出回调
TranscriptItemSender = Callable[[dict[str, Any]], Awaitable[None]]  # 发送 transcript_item 的回调
CommandResultEmitter = Callable[[str, str], Awaitable[None]]  # 指令结果发射回调（message, type）
ReplaceTranscriptItems = Callable[[list[dict[str, Any]]], Awaitable[None]]  # 替换转录项列表的回调


@dataclass
class RuntimeBundle:
    """共享运行时数据bundle。

    用于存储一次交互式会话的所有运行时对象。
    包括 API 客户端、工具注册器、引擎、状态管理等。

    Attributes:
        api_client: 流式 API 客户端实例
        cwd: 当前工作目录
        mcp_manager: MCP 客户端管理器
        tool_registry: 工具注册器
        app_state: 应用状态存储
        hook_executor: 钩子执行器
        engine: 查询引擎
        commands: 命令注册表
        external_api_client: 是否使用外部 API 客户端
        session_id: 会话 ID
        settings_overrides: 设置覆盖字典
    """

    api_client: SupportsStreamingMessages
    cwd: str
    mcp_manager: McpClientManager
    tool_registry: ToolRegistry
    app_state: AppStateStore
    hook_executor: HookExecutor
    engine: QueryEngine
    commands: CommandRegistry
    external_api_client: bool
    session_id: str = ""
    settings_overrides: dict[str, Any] = field(default_factory=dict[str, Any])
    # 钩子注入的 additionalContext（在 start_runtime 中设置，每次 handle_line 重建系统提示词后追加）
    hook_additional_contexts: list[str] = field(default_factory=list[Any])
    # 渠道感知提示词（PC 终端或渠道端注入），handle_line 重建系统提示词时复用
    channel_hint: str | None = None

    def current_settings(self) -> Settings:
        """返回会话的有效设置。

        大多数设置持久化到磁盘（~/.illusion/settings.json），
        但 CLI 选项如 --model/--api-format 在进程生命周期内保持有效。
        没有此覆盖，发送任何斜杠命令（如 /thinking）会从磁盘刷新 UI 状态，
        并将 model/provider " snap back" 到配置文件中的值。
        """
        return load_settings().merge_cli_overrides(**self.settings_overrides)

    def current_plugins(self) -> list[LoadedPlugin]:
        """返回当前工作树的可见插件。"""
        return load_plugins(self.current_settings(), self.cwd)

    def hook_summary(self) -> str:
        """返回当前钩子摘要。"""
        return load_hook_registry(self.current_settings(), self.current_plugins()).summary()

    def plugin_summary(self) -> str:
        """返回当前插件摘要。"""
        plugins = self.current_plugins()
        if not plugins:
            return "No plugins discovered."
        lines = ["Plugins:"]
        for plugin in plugins:
            state = "enabled" if plugin.enabled else "disabled"
            lines.append(f"- {plugin.manifest.name} [{state}] {plugin.manifest.description}")
        return "\n".join(lines)

    def mcp_summary(self) -> str:
        """返回当前 MCP 摘要。"""
        statuses = self.mcp_manager.list_statuses()
        if not statuses:
            return "No MCP servers configured."
        lines = ["MCP servers:"]
        for status in statuses:
            suffix = f" - {status.detail}" if status.detail else ""
            lines.append(f"- {status.name}: {status.state}{suffix}")
            if status.tools:
                lines.append(f"  tools: {', '.join(tool.name for tool in status.tools)}")
            if status.resources:
                lines.append(f"  resources: {', '.join(resource.uri for resource in status.resources)}")
        return "\n".join(lines)


def _build_goal_manager(settings: Settings) -> GoalManager | None:
    """按 settings.goal 构建 GoalManager（未启用时 None）。

    Args:
        settings: 当前 Settings

    Returns:
        GoalManager | None: goal 域管理器
    """
    goal = getattr(settings, "goal", None)
    if goal is None or not goal.enabled:
        return None
    from illusion.goal.types import GoalSettings as _GoalRuntimeSettings

    return GoalManager(_GoalRuntimeSettings(
        enabled=goal.enabled,
        default_max_goal_rounds=goal.default_max_goal_rounds,
        blocked_after_consecutive_rounds=goal.blocked_after_consecutive_rounds,
        verification_enabled=goal.verification_enabled,
        verification_max_attempts=goal.verification_max_attempts,
    ))


def build_session_engine(
    bundle: RuntimeBundle,
    session_id: str,
    *,
    permission_prompt: PermissionPrompt | None = None,
    ask_user_prompt: AskUserPrompt | None = None,
    plan_approval_prompt: PlanApprovalPrompt | None = None,
    print_mode: bool = False,
) -> QueryEngine:
    """构建与共享运行时隔离的会话引擎（Web 多会话并发用）。

    Web 多会话模式下，每个会话持有独立的 QueryEngine（消息历史、
    checkpoint、cost、bg_agent_tracker 完全隔离），但共享 bundle 的
    api_client / tool_registry / permission_checker / mcp_manager /
    hook_executor 等基础设施。行任务在各自引擎上并发执行互不干扰。

    与 build_runtime 的引擎构造保持同构（工具元数据、CheckpointStore
    attach、file_history 懒加载均一致），区别在于：
    - 不注册全局 on_task_complete 回调（由 WebBackendHost 统一按归属路由）
    - 复用 bundle 已解析的共享组件，不做重复初始化

    Args:
        bundle: 共享运行时 bundle（承载共享基础设施）
        session_id: 目标会话 ID
        permission_prompt: 权限确认回调（Web 端按会话绑定）
        ask_user_prompt: 用户问答回调（Web 端按会话绑定）
        plan_approval_prompt: 计划审批回调（Web 端按会话绑定）

    Returns:
        QueryEngine: 绑定新会话的独立引擎
    """
    settings = bundle.current_settings()
    # 工具元数据：共享 mcp_manager / app_state_store / session_hook_store，
    # 会话专属 query_engine / bg_agent_tracker 由新引擎自身提供
    tool_metadata = dict(bundle.engine.tool_metadata)
    tool_metadata["session_id"] = session_id
    engine = QueryEngine(
        api_client=bundle.api_client,
        tool_registry=bundle.tool_registry,
        permission_checker=bundle.engine.permission_checker,
        cwd=bundle.cwd,
        model=settings.active_model_name,
        system_prompt=bundle.engine.system_prompt,
        max_tokens=settings.max_tokens,
        max_turns=settings.max_turns,
        permission_prompt=permission_prompt,
        ask_user_prompt=ask_user_prompt,
        plan_approval_prompt=plan_approval_prompt,
        hook_executor=bundle.hook_executor,
        tool_metadata=tool_metadata,
        effort=bundle.engine.effort,
        session_id=session_id,
        print_mode=print_mode,
        goal_manager=_build_goal_manager(settings),
        # 媒体能力惰性解析：每轮查询时现读盘解析当前模型能力，
        # /model set 后无需重建引擎即可生效
        capabilities_resolver=lambda: bundle.current_settings().get_model_capabilities(),
    )
    # 将引擎自身与后台代理追踪器加入工具元数据（与 build_runtime 同构）
    engine._tool_metadata["query_engine"] = engine
    engine._tool_metadata["bg_agent_tracker"] = engine._bg_agent_tracker
    if engine.goal_manager is not None:
        engine._tool_metadata["goal_manager"] = engine.goal_manager
    # 构造 CheckpointStore 并 attach（懒创建策略与 build_runtime 一致：
    # 不立即 mkdir，第一条用户消息提交时才落盘）
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import session_dir_for
    checkpoint_store = CheckpointStore(session_dir_for(bundle.cwd, session_id), session_id)
    engine.attach_session(checkpoint_store)
    engine.load_file_history()
    return engine


def build_session_bundle(
    bundle: RuntimeBundle,
    session_id: str,
    engine: QueryEngine,
) -> RuntimeBundle:
    """构造持有独立引擎的会话 bundle（Web 多会话并发用）。

    基于共享 bundle 浅拷贝出会话级 bundle：engine 与 session_id 替换为
    会话专属值，其余（app_state / mcp_manager / tool_registry / hooks /
    settings_overrides）与共享 bundle 共用同一对象。

    Args:
        bundle: 共享运行时 bundle
        session_id: 会话 ID
        engine: 该会话的独立引擎

    Returns:
        RuntimeBundle: 会话级 bundle
    """
    return replace(bundle, engine=engine, session_id=session_id)


def _on_task_complete(
    task_id: str,
    task: TaskRecord,
    tracker: BackgroundAgentTracker,
) -> None:
    """后台任务完成后，通过 bg_agent_tracker 注入通知 XML。

    支持 agent 类任务（local_agent/remote_agent/in_process_teammate）
    和 bash/powershell 后台命令（local_bash）。其他类型忽略。

    Args:
        task_id: 任务 ID
        task: 任务记录
        tracker: 后台代理追踪器，用于注入 <task-notification> XML
    """
    from illusion.swarm.agent_executor import (
        TaskNotification,
        agent_type_display,
        format_task_notification,
    )

    # 读取任务实际输出（从 output_file 或内存 result）
    result_text = ""
    try:
        if task.type == "in_process_agent" and task.result:
            result_text = task.result
        elif task.output_file:
            content = task.output_file.read_text(encoding="utf-8", errors="replace")
            result_text = content[-12000:] if len(content) > 12000 else content
    except OSError:
        pass

    if task.type in {"local_agent", "remote_agent", "in_process_teammate"}:
        agent_id = task.metadata.get("agent_id", task_id)
        # 被用户停止（killed）的任务不注入通知：stop 结果对 LLM 无意义，
        # 且会触发 _auto_resume_bg 自动恢复，导致 Ctrl+X 后 LLM 被无意义调用
        if task.status == "killed":
            tracker.discard(agent_id)
            return
        # task_name 格式：任务名 · agent类型（PascalCase，与 /agent 列表一致），
        # 类型为空时默认 "GeneralPurpose"
        task_name_raw = task.metadata.get("name") or task.description or ""
        agent_type = agent_type_display(task.metadata.get("subagent_type"))
        task_name = f"{task_name_raw} · {agent_type}"
        notification = TaskNotification(
            task_id=agent_id,
            status=task.status,
            summary=task.description or f"Agent {agent_id} {task.status}",
            task_name=task_name,
            result=result_text or None,
            usage=None,
        )
        notification_xml = format_task_notification(notification)
        tracker.notify_completed(agent_id, notification_xml)
    elif task.type == "local_bash":
        # 被用户停止（killed）的任务不注入通知（同上）
        if task.status == "killed":
            tracker.discard(task_id)
            return
        # 后台 Bash/PowerShell 命令完成后通知 LLM
        summary = f'Background command "{task.description}" {task.status}'
        if task.return_code is not None:
            summary += f" (exit code {task.return_code})"
        # task_name 格式：命令描述 · task
        task_name = f"{task.description or ''} · task"
        notification = TaskNotification(
            task_id=task_id,
            status=task.status,
            summary=summary,
            task_name=task_name,
            result=result_text or None,
            usage=None,
        )
        notification_xml = format_task_notification(notification)
        tracker.notify_completed(task_id, notification_xml)


def _build_system_prompt_with_append(
    settings: Any,
    *,
    cwd: str,
    latest_user_prompt: str | None,
    channel_hint: str | None,
    append_system_prompt: str | None,
) -> str:
    """构建系统提示词，并可选追加用户指定内容。

    Args:
        settings: 配置实例
        cwd: 工作目录
        latest_user_prompt: 最新的用户提示词
        channel_hint: 渠道感知提示词
        append_system_prompt: 追加到系统提示词末尾的内容

    Returns:
        str: 完整的系统提示词
    """
    _base_prompt = build_runtime_system_prompt(
        settings,
        cwd=cwd,
        latest_user_prompt=latest_user_prompt,
        channel_hint=channel_hint,
    )
    if append_system_prompt:
        _base_prompt = _base_prompt + "\n\n" + append_system_prompt
    return _base_prompt


async def build_runtime(
    *,
    prompt: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_prompt: PermissionPrompt | None = None,
    ask_user_prompt: AskUserPrompt | None = None,
    plan_approval_prompt: PlanApprovalPrompt | None = None,
    print_mode: bool = False,
    sandbox_permission_prompt: PermissionPrompt | None = None,
    restore_messages: list[dict[str, Any]] | None = None,
    restore_session_id: str | None = None,
    restore_checkpoint_count: int | None = None,
    effort: str | None = None,
    channel_hint: str | None = None,
    channel_tools: list[Any] | None = None,
    settings_file: str | None = None,
    permission_mode: str | None = None,
    append_system_prompt: str | None = None,
    verbose: bool = False,
    debug: bool = False,
    bare: bool = False,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    mcp_config: list[str] | None = None,
    name: str | None = None,
    cwd: str | None = None,
) -> RuntimeBundle:
    """构建 IllusionAgent 会话的共享运行时。

    初始化所有运行时对象，包括 API 客户端、插件、工具注册器、引擎等。

    Args:
        prompt: 初始用户提示词
        model: 使用的模型名称
        max_turns: 最大对话轮次
        base_url: API 基础 URL
        system_prompt: 系统提示词
        api_key: API 密钥
        api_format: API 格式（openai/anthropic）
        api_client: 流式 API 客户端实例
        permission_prompt: 权限确认回调函数
        ask_user_prompt: 用户问答回调函数
        restore_messages: 恢复的会话消息列表
        restore_checkpoint_count: 磁盘已有 checkpoint 行数（restore_messages 场景
            对齐新建 CheckpointStore 的 next_checkpoint_id，避免写出重复 id 行）
        effort: 推理强度级别（low/medium/high/xhigh/max）
        verbose: 启用 INFO 级别日志（CLI --verbose）
        debug: 启用 DEBUG 级别日志（CLI --debug）
        bare: 纯净模式，跳过 plugins/MCP auto-discovery（CLI --bare）
        allowed_tools: 工具白名单，仅保留指定名称的工具（CLI --allowed-tools）
        disallowed_tools: 工具黑名单，移除指定名称的工具（CLI --disallowed-tools）
        mcp_config: 额外 MCP 服务器配置（JSON 字符串或文件路径列表，CLI --mcp-config）
        name: 会话显示名称，存入 tool_metadata（CLI --name）
        cwd: 工作目录（Web 多工作区场景按目录传入；缺省取进程当前目录，
            terminal/print 模式行为不变）

    Returns:
        RuntimeBundle: 运行时数据 bundle
    """
    # 配置日志级别（CLI --verbose / --debug）
    import logging
    if debug:
        logging.getLogger("illusion").setLevel(logging.DEBUG)
    elif verbose:
        logging.getLogger("illusion").setLevel(logging.INFO)
    # 计划文件清理：与日志/任务清理对称，统一在运行时构建入口显式触发。
    # 三端（terminal / print / web）均经 build_runtime 启动，此处调用保证
    # 每端都能清理超 TTL 的旧计划文件；用 once 标志避免多工作区/多会话
    # 反复清理。放后台线程执行，不阻塞会话初始化。
    # 惰性触发（挂在 get_plans_dir 上）在 Web/桌面等不 import plan_file 的
    # 启动路径上永远不执行，故改为显式统一入口。
    try:
        from illusion.config.plan_file import get_plans_dir
        from illusion.utils.log_cleanup import run_plans_cleanup_once

        loop = asyncio.get_running_loop()

        def _run_plan_cleanup() -> None:
            # 目录 mkdir 与清理都在后台线程执行，避免事件循环线程同步建目录
            run_plans_cleanup_once(get_plans_dir())

        # 后台线程清理，避免阻塞会话初始化；目录不存在时清理函数静默返回。
        # 消费 Future 的异常，避免后台线程抛异常时触发
        # "exception was never retrieved" 告警。
        future = loop.run_in_executor(None, _run_plan_cleanup)
        future.add_done_callback(
            lambda f: f.exception() if not f.cancelled() else None
        )
    except Exception:
        # 计划清理失败不影响会话启动（与日志清理容忍单文件失败一致）
        log.exception("计划文件清理调度失败")
    # 构建设置覆盖字典
    settings_overrides: dict[str, Any] = {
        "model": model,
        "max_turns": max_turns,
        "base_url": base_url,
        "system_prompt": system_prompt,
        "api_key": api_key,
        "api_format": api_format,
        "effort": effort,
    }
    settings = load_settings(
        config_path=Path(settings_file) if settings_file else None
    ).merge_cli_overrides(**settings_overrides)
    # 覆盖权限模式（CLI --permission-mode / --dangerously-skip-permissions）
    if permission_mode is not None:
        from illusion.permissions.modes import PermissionMode
        try:
            settings = settings.model_copy(update={
                "permission": settings.permission.model_copy(
                    update={"mode": PermissionMode(permission_mode)}
                )
            })
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                f"Invalid permission_mode: {permission_mode}, ignoring"
            )
    session_id = restore_session_id or uuid4().hex[:12]
    # 获取工作目录（Web 多工作区显式传入；缺省取进程当前目录）
    cwd = str(Path(str(cwd)).expanduser().resolve()) if cwd else str(Path.cwd())
    # 加载插件（--bare 模式跳过）
    if not bare:
        plugins = load_plugins(settings, cwd)
    else:
        plugins = []
    # 解析 API 客户端
    resolved_api_client: SupportsStreamingMessages
    _web_auth_missing = False
    try:
        if api_client:
            resolved_api_client = api_client
        elif settings.api_format == "copilot":
            from illusion.auth.copilot import CopilotAuth, copilot_extra_headers
            _copilot = CopilotAuth()
            _copilot_token = _copilot.get_valid_token()
            resolved_api_client = OpenAICompatibleClient(  # type: ignore[assignment]
                api_key=_copilot_token,
                base_url=settings.base_url or "https://api.githubcopilot.com",
                extra_headers=copilot_extra_headers(),
            )
        elif settings.api_format == "codex":
            from illusion.api.codex_client import CodexApiClient
            from illusion.auth.codex_oauth import CodexOAuth
            resolved_api_client = CodexApiClient(  # type: ignore[assignment]
                auth_token_resolver=CodexOAuth().get_valid_token,
                base_url=settings.base_url,
            )
        elif settings.api_format == "response":
            from illusion.api.responses_client import ResponsesApiClient

            auth = settings.resolve_auth()
            resolved_api_client = ResponsesApiClient(  # type: ignore[assignment]
                api_key=auth.value if auth.auth_kind == "api_key" else None,
                base_url=settings.base_url,
                auth_token=auth.value if auth.auth_kind == "auth_token" else None,
            )
        elif settings.api_format == "anthropic":
            auth = settings.resolve_auth()
            resolved_api_client = AnthropicApiClient(  # type: ignore[assignment]
                api_key=auth.value if auth.auth_kind == "api_key" else None,
                base_url=settings.base_url,
                auth_token=auth.value if auth.auth_kind == "auth_token" else None,
            )
        else:  # "openai" 及其他 OpenAI 兼容格式
            resolved_api_client = OpenAICompatibleClient(  # type: ignore[assignment]
                api_key=settings.resolve_api_key(),
                base_url=settings.base_url,
            )
    except (ValueError, RuntimeError) as exc:
        # 友好提示而非让异常冒泡成 "后端无法连接"
        import sys

        import click

        from illusion.config.i18n import t as _t

        # Web 模式：优雅降级 — 用占位客户端继续启动，auth_status="missing"
        # 前端检测到 missing 会自动弹出 SettingsModal 引导用户配置
        # 终端模式：打印简短提示后 sys.exit(1)
        if "illusion.ui.web.ws_host" in sys.modules:
            logging.getLogger(__name__).warning("API client init failed (web mode, degraded): %s", exc)
            # 占位客户端：实际不会调用，用户配置后 _rebuild_api_client 会替换
            resolved_api_client = OpenAICompatibleClient(  # type: ignore[assignment]
                api_key="",
                base_url=settings.base_url,
            )
            _web_auth_missing = True
        else:
            click.echo(str(exc), err=True)
            click.echo(_t("terminal_auth_hint"), err=True)
            sys.exit(1)
    # 创建 MCP 客户端管理器（--bare 模式跳过自动发现，但 --mcp-config 仍生效）
    if not bare:
        server_configs: dict[str, object] = load_mcp_server_configs(settings, plugins, cwd)
    else:
        server_configs = {}
    # 加载 CLI 指定的额外 MCP 配置（--bare 模式也允许显式指定）
    if mcp_config:
        from pydantic import ValidationError

        from illusion.mcp.config import load_mcp_config_from_string
        for cfg in mcp_config:
            try:
                extra = load_mcp_config_from_string(cfg)
                server_configs.update(extra)
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                logging.getLogger(__name__).warning(
                    f"Failed to load MCP config from {cfg}: {exc}"
                )
    mcp_manager = McpClientManager(server_configs)
    if not bare or server_configs:
        await mcp_manager.connect_all()
    # 创建工具注册器（goal 工具随 settings.goal.enabled 注册）
    tool_registry = create_default_tool_registry(
        mcp_manager,
        channel_tools=channel_tools,
        goal_enabled=settings.goal.enabled,
    )
    # 应用 CLI 工具过滤（--allowed-tools / --disallowed-tools）
    if allowed_tools is not None or disallowed_tools is not None:
        filtered = ToolRegistry()
        for tool in tool_registry.list_tools():
            if disallowed_tools and tool.name in disallowed_tools:
                continue
            if allowed_tools is not None and tool.name not in allowed_tools:
                continue
            filtered.register(tool)
        tool_registry = filtered
    # 创建应用状态存储
    # 会话显示名称：恢复会话时优先读取磁盘 meta 的自定义 title（/rename 写入），
    # 否则回退到 CLI --name 传入的会话名称。
    session_name = name or ""
    if restore_session_id and not session_name:
        try:
            from illusion.services.session_storage import read_meta
            meta = read_meta(cwd, restore_session_id)
            session_name = ((meta or {}).get("title")) or ""
        except (OSError, ValueError):
            session_name = ""
    app_state = AppStateStore(
        AppState(
            model=settings.active_model_name,
            permission_mode=settings.permission.mode.value,
            ui_language=settings.ui_language,
            cwd=cwd,
            auth_status="missing" if _web_auth_missing else auth_status(settings),
            base_url=settings.base_url or "",
            effort=settings.effort,
            mcp_connected=sum(1 for status in mcp_manager.list_statuses() if status.state == "connected"),
            mcp_failed=sum(1 for status in mcp_manager.list_statuses() if status.state == "failed"),
            show_thinking=settings.show_thinking,
            phase="idle",
            session_id=session_id,
            session_name=session_name,
        )
    )
    # 创建会话钩子存储和钩子执行器
    from illusion.hooks.session_hooks import SessionHookStore
    session_hook_store = SessionHookStore()
    hook_executor = HookExecutor(
        load_hook_registry(settings, plugins),
        HookExecutionContext(
            cwd=Path(cwd).resolve(),
            api_client=resolved_api_client,
            default_model=settings.active_model_name,
        ),
        session_hook_store=session_hook_store,
    )
    # 创建权限检查器并同步沙箱限制
    permission_checker = PermissionChecker(settings.permission)
    permission_checker.sync_sandbox_restrictions(settings.sandbox, working_directory=cwd)

    # 创建查询引擎
    engine = QueryEngine(
        api_client=resolved_api_client,
        tool_registry=tool_registry,
        permission_checker=permission_checker,
        cwd=cwd,
        model=settings.active_model_name,
        system_prompt=_build_system_prompt_with_append(
            settings,
            cwd=cwd,
            latest_user_prompt=prompt,
            channel_hint=channel_hint,
            append_system_prompt=append_system_prompt,
        ),
        max_tokens=settings.max_tokens,
        max_turns=settings.max_turns,
        permission_prompt=permission_prompt,
        ask_user_prompt=ask_user_prompt,
        plan_approval_prompt=plan_approval_prompt,
        hook_executor=hook_executor,
        tool_metadata={
            "mcp_manager": mcp_manager,
            "app_state_store": app_state,
            "session_id": session_id,
            "session_hook_store": session_hook_store,
            "session_name": name,
        },
        effort=EffortMapper.normalize(settings.effort),
        session_id=session_id,
        print_mode=print_mode,
        sandbox_permission_prompt=sandbox_permission_prompt,
        goal_manager=_build_goal_manager(settings),
        # 媒体能力惰性解析：每轮查询时现读盘解析当前模型能力，
        # /model set 后无需重建引擎即可生效
        capabilities_resolver=lambda: (
            load_settings().merge_cli_overrides(**settings_overrides).get_model_capabilities()
        ),
    )
    # 将引擎自身添加到工具元数据中，供子 agent 使用
    engine._tool_metadata["query_engine"] = engine
    # 将后台代理追踪器添加到工具元数据中，供 AgentTool 使用
    engine._tool_metadata["bg_agent_tracker"] = engine._bg_agent_tracker
    # 将 goal 域管理器添加到工具元数据中，供 goal 工具使用
    if engine.goal_manager is not None:
        engine._tool_metadata["goal_manager"] = engine.goal_manager

    # 注册 on_task_complete 回调：后台任务完成后通知 bg_agent_tracker
    # 闭包仅捕获 engine._bg_agent_tracker，实际逻辑委托给模块级 _on_task_complete
    def _on_task_complete_callback(task_id: str, task: TaskRecord) -> None:
        _on_task_complete(task_id, task, engine._bg_agent_tracker)

    from illusion.tasks.manager import get_task_manager
    get_task_manager().on_task_complete = _on_task_complete_callback
    # 从保存的会话恢复消息（如果提供）
    if restore_messages:
        restored = [
            ConversationMessage.model_validate(m) for m in restore_messages
        ]
        engine.load_messages(restored)
    # 构造 CheckpointStore 并注入 engine
    # 延迟创建策略：不立即 mkdir/write_index/write_meta，
    # 只有第一条用户消息真正提交时才在磁盘创建会话目录。
    # 这样启动后未发消息就退出不会留下"0轮无摘要"的空会话。
    # attach_session 以 store 为唯一权威：session_id/file_history
    # 均由 store 派生，context.jsonl / meta.json / file_history.json
    # 必然落在同一会话目录。
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import session_dir_for
    session_dir = session_dir_for(cwd, session_id)
    checkpoint_store = CheckpointStore(session_dir, session_id)
    # restore_messages 恢复路径的对齐：调用方已在外部 restore 过磁盘状态，
    # 新建 store 若不对齐，每轮 append 会从 id=0 重复写 checkpoint 行，
    # resume/rewind 按 id 定位时整体偏移（部分指令失效的根源）。
    # 显式传入 restore_checkpoint_count 优先；否则按磁盘实况自动对齐
    # （覆盖 -r/-c/--print resume 等所有未传计数的恢复入口）。
    if restore_checkpoint_count is not None:
        checkpoint_store.align_checkpoint_id(restore_checkpoint_count)
    elif restore_messages:
        checkpoint_store.align_checkpoint_id(
            await asyncio.to_thread(checkpoint_store.count_disk_checkpoints)
        )
    engine.attach_session(checkpoint_store)
    # 加载文件历史（若磁盘存在）。
    # restore_messages 场景：调用方已在外部完成 CheckpointStore.restore()
    # 并传入 restore_messages，但此处 checkpoint_store 是新建的、未 restore，
    # next_checkpoint_id 为 0，不能作为对齐依据，故不传 checkpoint_count。
    # 此处加载后 submit_message 的懒加载分支不会重复触发；
    # 若磁盘无历史则保持 None，由 submit_message 兜底新建。
    engine.load_file_history()
    # index.json 和 meta.json 的写入由 _update_session_meta 在第一条消息后负责。

    return RuntimeBundle(
        api_client=resolved_api_client,
        cwd=cwd,
        mcp_manager=mcp_manager,
        tool_registry=tool_registry,
        app_state=app_state,
        hook_executor=hook_executor,
        engine=engine,
        commands=create_default_command_registry(),
        external_api_client=api_client is not None,
        session_id=session_id,
        settings_overrides=settings_overrides,
        channel_hint=channel_hint,
    )


async def start_runtime(bundle: RuntimeBundle) -> AggregatedHookResult:
    """运行会话开始钩子。

    执行 SESSION_START 钩子事件，提取 additional_contexts
    并注入到系统提示词中。

    Args:
        bundle: 运行时数据 bundle

    Returns:
        AggregatedHookResult: 钩子执行结果
    """
    result = await bundle.hook_executor.execute(
        HookEvent.SESSION_START,
        {"cwd": str(bundle.cwd), "source": "startup"},
    )
    # 存储 additionalContext，在 handle_line 中每次重建系统提示词后追加
    bundle.hook_additional_contexts = result.additional_contexts
    # 首次注入
    for ctx in result.additional_contexts:
        if ctx:
            current_prompt = bundle.engine._system_prompt
            bundle.engine.set_system_prompt(
                current_prompt + "\n\n" + _wrap_in_system_reminder(ctx)
            )
    return result


def _wrap_in_system_reminder(content: str) -> str:
    """向后兼容别名。"""
    from illusion.hooks.utils import wrap_in_system_reminder
    return wrap_in_system_reminder(content)


async def close_runtime(bundle: RuntimeBundle) -> None:
    """关闭运行时拥有的资源。

    关闭 MCP 管理器并执行 SESSION_END 钩子。

    Args:
        bundle: 运行时数据 bundle
    """
    from illusion.swarm.team_helpers import cleanup_session_teams

    await cleanup_session_teams()
    # 关闭 MCP 管理器
    await bundle.mcp_manager.close()
    # 执行会话结束钩子
    await bundle.hook_executor.execute(
        HookEvent.SESSION_END,
        {"cwd": str(bundle.cwd), "reason": "other"},
    )


def _last_user_text(messages: list[ConversationMessage]) -> str:
    """获取最后一条用户消息的文本。

    跳过后台任务完成通知（<task-notification>）：通知是系统注入给 LLM 的
    XML，不应作为 latest_user_prompt 进入系统提示词。

    Args:
        messages: 会话消息列表

    Returns:
        str: 最后一条用户消息文本（如果不存在则返回空字符串）
    """
    for msg in reversed(messages):
        if msg.role == "user" and msg.text.strip():
            if is_task_notification(msg.text):
                continue
            return msg.text.strip()
    return ""


def _truncate(text: str, limit: int) -> str:
    """截断文本到指定长度。

    Args:
        text: 要截断的文本
        limit: 最大长度

    Returns:
        str: 截断后的文本
    """
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_pending_tool_results(messages: list[ConversationMessage]) -> str | None:
    """在工具执行后停止时呈现紧凑摘要。

    在模型有机会响应之前呈现待处理结果的摘要。

    Args:
        messages: 会话消息列表

    Returns:
        str | None: 摘要文本（如果没有待处理结果则返回 None）
    """
    if not messages:
        return None

    last = messages[-1]
    if last.role != "user":
        return None
    tool_results = [block for block in last.content if isinstance(block, ToolResultBlock)]
    if not tool_results:
        return None

    # 构建工具使用 ID 到工具使用的映射
    tool_uses_by_id: dict[str, ToolUseBlock] = {}
    assistant_text = ""
    for msg in reversed(messages[:-1]):
        if msg.role != "assistant":
            continue
        if not msg.tool_uses:
            continue
        assistant_text = msg.text.strip()
        for tu in msg.tool_uses:
            tool_uses_by_id[tu.id] = tu
        break

    lines: list[str] = [
        "Pending continuation: tool results were produced, but the model did not get a chance to respond yet."
    ]
    if assistant_text:
        lines.append(f"Last assistant message: {_truncate(assistant_text, 400)}")

    max_results = 3
    for tr in tool_results[:max_results]:
        matching_tu: ToolUseBlock | None = tool_uses_by_id.get(tr.tool_use_id)
        if matching_tu is not None:
            raw_input = json.dumps(matching_tu.input, ensure_ascii=True, sort_keys=True)
            lines.append(
                f"- {matching_tu.name} {_truncate(raw_input, 200)} -> {_truncate(tr.content.strip() if isinstance(tr.content, str) else str(tr.content), 400)}"
            )
        else:
            lines.append(
                f"- tool_result[{tr.tool_use_id}] -> {_truncate(tr.content.strip() if isinstance(tr.content, str) else str(tr.content), 400)}"
            )

    if len(tool_results) > max_results:
        lines.append(f"(+{len(tool_results) - max_results} more tool results)")

    lines.append("To continue from these results, run: /continue 32 (or any count).")
    return "\n".join(lines)


def sync_app_state(bundle: RuntimeBundle) -> None:
    """从当前设置和动态键绑定刷新 UI 状态。

    Args:
        bundle: 运行时数据 bundle
    """
    settings = bundle.current_settings()
    bundle.engine.set_max_turns(settings.max_turns)
    # 上下文占用：最后一次 API 调用的真实值 + 新增消息估算
    # （压缩后 last_api_usage 被清除，回退到纯估算直到下次 API 调用）
    context_tokens = bundle.engine.current_context_tokens()
    # 最后一次 API 调用的真实分项（供 Web 前端展示；无数据时为 0）
    last_usage = bundle.engine.last_api_usage
    usage = bundle.engine.total_usage
    bundle.app_state.set(
        model=settings.active_model_name,
        permission_mode=settings.permission.mode.value,
        ui_language=settings.ui_language,
        cwd=bundle.cwd,
        auth_status=auth_status(settings),
        base_url=settings.base_url or "",
        effort=settings.effort,
        max_tokens=settings.max_tokens,
        mcp_connected=sum(1 for status in bundle.mcp_manager.list_statuses() if status.state == "connected"),
        mcp_failed=sum(1 for status in bundle.mcp_manager.list_statuses() if status.state == "failed"),
        show_thinking=settings.show_thinking,
        phase=bundle.app_state.get().phase,
        session_id=bundle.session_id,
        context_window=settings.context_window,
        context_tokens=context_tokens,
        context_cache_read=last_usage.cache_read_input_tokens if last_usage else 0,
        context_cache_creation=last_usage.cache_creation_input_tokens if last_usage else 0,
        context_input=last_usage.input_tokens if last_usage else 0,
        context_output=last_usage.output_tokens if last_usage else 0,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        goal=bundle.engine.goal_status_payload(),
    )


def refresh_session_state(bundle: RuntimeBundle) -> None:
    """会话指令后刷新状态（统一入口）。

    在 /new /resume /rewind /delete /compact 等改变消息历史或会话状态的
    命令处理后调用，确保前端立即收到最新 context_tokens / usage。

    Args:
        bundle: 运行时数据 bundle
    """
    sync_app_state(bundle)


def _rebuild_api_client(bundle: RuntimeBundle, settings: Settings) -> None:
    """根据当前设置重建 API 客户端（跨 env 切换模型时调用）

    当 API key 缺失或无效时，设置 auth_status="missing" 并返回，
    而非抛出异常导致后端崩溃。

    Args:
        bundle: 运行时数据 bundle
        settings: 当前设置
    """
    try:
        _api_format = settings.api_format
        if _api_format == "copilot":
            from illusion.auth.copilot import CopilotAuth, copilot_extra_headers
            _copilot = CopilotAuth()
            _copilot_token = _copilot.get_valid_token()
            new_client = OpenAICompatibleClient(
                api_key=_copilot_token,
                base_url=settings.base_url or "https://api.githubcopilot.com",
                extra_headers=copilot_extra_headers(),
            )
        elif _api_format == "codex":
            from illusion.api.codex_client import CodexApiClient
            from illusion.auth.codex_oauth import CodexOAuth
            new_client = CodexApiClient(  # type: ignore[assignment]
                auth_token_resolver=CodexOAuth().get_valid_token,
                base_url=settings.base_url,
            )
        elif _api_format == "response":
            from illusion.api.responses_client import ResponsesApiClient

            auth = settings.resolve_auth()
            new_client = ResponsesApiClient(  # type: ignore[assignment]
                api_key=auth.value if auth.auth_kind == "api_key" else None,
                base_url=settings.base_url,
                auth_token=auth.value if auth.auth_kind == "auth_token" else None,
            )
        elif _api_format == "anthropic":
            auth = settings.resolve_auth()
            new_client = AnthropicApiClient(  # type: ignore[assignment]
                api_key=auth.value if auth.auth_kind == "api_key" else None,
                base_url=settings.base_url,
                auth_token=auth.value if auth.auth_kind == "auth_token" else None,
            )
        else:  # "openai" 及其他 OpenAI 兼容格式
            new_client = OpenAICompatibleClient(
                api_key=settings.resolve_api_key(),
                base_url=settings.base_url,
            )
    except (ValueError, RuntimeError) as exc:
        # 不退出，设置 auth_status 为 missing 并返回
        # web 前端会检测到 missing 状态并弹出设置弹窗
        logging.getLogger(__name__).warning("API client rebuild failed: %s", exc)
        try:
            bundle.app_state.get().auth_status = "missing"
        except (AttributeError, RuntimeError) as exc:
            logging.getLogger(__name__).debug("设置 auth_status=missing 失败: %s", exc)
        return

    bundle.api_client = new_client  # type: ignore[assignment]
    bundle.engine.set_api_client(new_client)  # type: ignore[arg-type]
    bundle.hook_executor._context.api_client = new_client  # type: ignore[assignment]


def _update_session_meta(bundle: RuntimeBundle) -> None:
    """更新会话 meta.json 和 index.json（替代旧 save_session_snapshot）。

    首次调用时同时写入 index.json（标记 latest session）和 meta.json。
    后续调用保留 created_at，仅更新其他字段。

    空会话（engine.messages 为空）不写入 meta/index，避免磁盘留下空会话目录。

    路径来源：优先 engine.checkpoint_store（会话目录唯一权威），
    store 不存在时兜底用 cwd+session_id 计算（仅向前兼容防御）。
    """
    import time

    from illusion.services.session_storage import (
        read_meta_from,
        session_dir_for,
        write_index_to,
        write_meta_to,
    )
    store = bundle.engine.checkpoint_store
    if store is not None:
        session_dir = store.session_dir
        session_id = store.session_id
    else:
        # 兜底：无 store 且无 session_id 时（如 /delete all 清理路径），
        # session_dir_for 会拒绝空串抛 InvalidSessionIdError，直接跳过
        if not bundle.session_id:
            return
        session_dir = session_dir_for(bundle.cwd, bundle.session_id)
        session_id = bundle.session_id
    # 空会话处理：从未发过消息的懒创建会话不写 meta/index（避免残留空目录）；
    # 但会话曾有 meta（如 rewind 回退全部轮次后 messages 为空），必须把
    # message_count/turn_count 归零——否则列表仍显示该会话（旧计数），
    # resume 后却是空对话，形成幽灵条目。
    if not bundle.engine.messages:
        existing = read_meta_from(session_dir, session_id)
        if existing is not None:
            existing["message_count"] = 0
            existing["turn_count"] = 0
            existing["summary"] = ""
            existing["updated_at"] = time.time()
            write_meta_to(session_dir, session_id, existing)
        return
    settings = bundle.current_settings()
    summary = ""
    for msg in bundle.engine.messages:
        if msg.role == "user" and msg.text.strip():
            # 跳过后台任务完成通知与 goal harness 注入消息（避免成为会话摘要）。
            # 命令从不进入 engine.messages（handle_line 命令分支直接拦截），
            # 因此 messages 中以 / 开头的 user 消息必为真实用户输入，
            # 不能再按 / 前缀排除——否则真斜杠消息会被误吞。
            if is_task_notification(msg.text) or is_goal_system_message(msg.text):
                continue
            summary = msg.text.strip()[:80]
            break
    # 回退：首条真实用户消息不存在（如 goal 注入消息开局）时摘要为空，
    # 用当前 goal 的 objective 兜底，避免会话列表标题为空
    if not summary and bundle.engine.goal_manager is not None:
        snap = bundle.engine.goal_manager.snapshot
        if snap is not None:
            summary = snap.objective.strip()[:80]
    # 计算 turn_count：按真正由用户输入的消息数计算（排除 tool_result 等）
    # 一轮对话 = 一个用户输入 + 一个或多个 assistant 回复（含工具调用的中间回复）。
    # /goal 命令原文已作为真实 user 消息入库（record_goal_command），计入轮次；
    # goal 自动续跑的 <goal_round> 注入消息非用户输入，不再单独加成
    from illusion.engine.messages import ToolResultBlock
    turn_count = sum(
        1 for m in bundle.engine.messages
        if m.role == "user"
        and not any(isinstance(b, ToolResultBlock) for b in m.content)
        and m.text.strip()
        and not is_task_notification(m.text)
        and not is_goal_system_message(m.text)
    )
    # 保留原始 created_at（首次调用时不存在则用当前时间）
    existing = read_meta_from(session_dir, session_id) or {}
    created_at = existing.get("created_at", time.time())
    # 首次写入 index.json（标记为 latest session）
    write_index_to(session_dir, session_id)
    # 首次写入时把 CLI --name 的会话显示名称持久化为 title（仅在无自定义名称时），
    # 使纯 --name 启动的会话在 /resume、/delete 列表中也能显示该名称；
    # 已存在 title（/rename 设置的）则保留。
    title = existing.get("title") or (bundle.app_state.get().session_name or "")
    write_meta_to(session_dir, session_id, {
        "session_id": session_id,
        "cwd": bundle.cwd,
        "model": settings.active_model_name,
        "created_at": created_at,
        "updated_at": time.time(),
        "summary": summary,
        "message_count": len(bundle.engine.messages),
        "turn_count": turn_count,
        "title": title,
    })


async def handle_line(
    bundle: RuntimeBundle,
    line: str,
    *,
    print_system: SystemPrinter,
    render_event: StreamRenderer,
    clear_output: ClearHandler,
    replay_transcript_item: TranscriptItemSender | None = None,
    command_result_emitter: CommandResultEmitter | None = None,
    replace_transcript_items: ReplaceTranscriptItems | None = None,
    rewind_restored_emitter: Callable[[str], Awaitable[None]] | None = None,
) -> bool:
    """处理提交的一行输入（用于无头或 TUI 渲染）。

    处理命令或用户消息，更新引擎，渲染事件，并保存会话快照。

    Args:
        bundle: 运行时数据 bundle
        line: 用户输入的行
        print_system: 系统消息打印回调
        render_event: 流式事件渲染回调
        clear_output: 清空输出回调
        replay_transcript_item: 重播 transcript_item 的回调（用于 /resume）
        command_result_emitter: 指令结果发射回调
        replace_transcript_items: 替换转录项列表的回调（用于 /rewind 等，避免 Ink Static 重复渲染）
        rewind_restored_emitter: rewind 被回退的 user 消息回调（前端回填输入框）

    Returns:
        bool: 是否继续会话
    """
    # 更新钩子注册表（如果不是外部 API 客户端）
    if not bundle.external_api_client:
        bundle.hook_executor.update_registry(
            load_hook_registry(bundle.current_settings(), bundle.current_plugins())
        )

    # 解析命令
    parsed = bundle.commands.lookup(line)
    if parsed is not None:
        command, args = parsed
        result = await command.handler(
            args,
            CommandContext(
                engine=bundle.engine,
                hooks_summary=bundle.hook_summary(),
                mcp_summary=bundle.mcp_summary(),
                plugin_summary=bundle.plugin_summary(),
                cwd=bundle.cwd,
                tool_registry=bundle.tool_registry,
                app_state=bundle.app_state,
                session_id=bundle.session_id,
                channel_hint=bundle.channel_hint,
                # TUI 终端支持交互式命令输入（如 /model set 的能力勾选）
                interactive=True,
            ),
        )
        if result.reset_session:
            bundle.session_id = uuid4().hex[:12]
            # 清除共享 session_name：reset 后是全新会话，上一会话的重命名名称
            # 不应被 _update_session_meta 兜底写入新会话 meta.title（Web/Terminal
            # 共用同一落盘逻辑，这里与 Web 端 _create_session/_set_active_session
            # 的清除保持一致）
            bundle.app_state.set(session_name="")
            # 构造新 CheckpointStore（延迟创建，不立即 mkdir/write_index/write_meta）
            # system_prompt 的持久化由 query_engine.submit_message 在第一条消息时负责，
            # index/meta 由 _update_session_meta 在第一条消息后负责。
            # 这样 /new 后未发消息就退出不会留下空会话目录。
            # attach_session 以 store 为唯一权威（session_id 由 store 派生），
            # 杜绝 bundle.session_id 与 engine._session_id 不同步导致文件散落。
            from illusion.services.checkpoint_store import CheckpointStore
            from illusion.services.session_storage import session_dir_for
            new_store = CheckpointStore(
                session_dir_for(bundle.cwd, bundle.session_id), bundle.session_id
            )
            bundle.engine.attach_session(new_store)
            bundle.session_id = bundle.engine.session_id
            locale = str(bundle.app_state.get().ui_language or bundle.current_settings().ui_language)
            prefix = "新会话已开启，任务 ID：" if locale.lower().startswith("zh") else "Started new session. Task ID: "
            suffix = result.message or ""
            detail = f"\n{suffix}" if suffix else ""
            result.message = f"{prefix}{bundle.session_id}{detail}"
        await _render_command_result(
            result, print_system, clear_output, render_event,
            replay_transcript_item, command_result_emitter,
            replace_transcript_items, rewind_restored_emitter,
        )
        if result.restored_session_id:
            bundle.session_id = result.restored_session_id
        # 会话指令后刷新状态（context_tokens / usage / overhead）
        # 同时更新 meta.json（rewind/resume 后 engine.messages 已变化，
        # 需同步更新 message_count/turn_count，否则会话列表显示旧值）
        if result.refresh_state:
            _update_session_meta(bundle)
            sync_app_state(bundle)
        # 跨 env 切换模型时重建 API 客户端
        if result.needs_api_rebuild:
            _rebuild_api_client(bundle, bundle.current_settings())
        # 处理待继续标志
        if result.continue_pending:
            settings = bundle.current_settings()
            bundle.engine.set_max_turns(settings.max_turns)
            system_prompt = build_runtime_system_prompt(
                settings,
                cwd=bundle.cwd,
                latest_user_prompt=_last_user_text(bundle.engine.messages),
                channel_hint=bundle.channel_hint,
            )
            for ctx in bundle.hook_additional_contexts:
                if ctx:
                    system_prompt = system_prompt + "\n\n" + _wrap_in_system_reminder(ctx)
            bundle.engine.set_system_prompt(system_prompt)
            turns = result.continue_turns if result.continue_turns is not None else bundle.engine.max_turns
            try:
                async for event in bundle.engine.continue_pending(max_turns=turns):
                    await render_event(event)
            except MaxTurnsExceeded as exc:
                await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
                pending = _format_pending_tool_results(bundle.engine.messages)
                if pending:
                    await print_system(pending)
            # 更新会话 meta（替代旧 save_session_snapshot）
            _update_session_meta(bundle)
        # 处理 goal 驱动标志（/goal 创建 / resume 后立即续跑 goal 轮次）
        if result.drive_goal and bundle.engine.goal_manager is not None:
            try:
                async for event in bundle.engine.drive_goal_rounds():
                    await render_event(event)
            except MaxTurnsExceeded as exc:
                await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
        # 命令路径（不走 submit_message）补触发会话自动标题：/goal 创建目标的
        # 命令原文已作为 user 消息入库，需在回合结束后调度标题生成
        try:
            from illusion.title.auto_title import maybe_schedule_title

            maybe_schedule_title(bundle.engine)
        except Exception:
            logging.getLogger(__name__).exception("命令路径自动标题调度失败")
        sync_app_state(bundle)
        return not result.should_exit

    # 处理普通用户消息
    settings = bundle.current_settings()
    bundle.engine.set_max_turns(settings.max_turns)
    system_prompt = build_runtime_system_prompt(settings, cwd=bundle.cwd, latest_user_prompt=line, channel_hint=bundle.channel_hint)
    # 追加钩子注入的 additionalContext
    for ctx in bundle.hook_additional_contexts:
        if ctx:
            system_prompt = system_prompt + "\n\n" + _wrap_in_system_reminder(ctx)
    bundle.engine.set_system_prompt(system_prompt)
    try:
        async for event in bundle.engine.submit_message(line):
            await render_event(event)
    except MaxTurnsExceeded as exc:
        await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
        pending = _format_pending_tool_results(bundle.engine.messages)
        if pending:
            await print_system(pending)
        # 更新会话 meta（替代旧 save_session_snapshot）
        _update_session_meta(bundle)
        sync_app_state(bundle)
        return True
    # 更新会话 meta（替代旧 save_session_snapshot）
    _update_session_meta(bundle)
    sync_app_state(bundle)
    return True


async def stop_all_tasks(
    bundle: RuntimeBundle, *, session_ids: list[str] | None = None
) -> None:
    """停止所有运行中或待处理的后台任务，清理 tracker 状态。

    Ctrl+X（或 Web 端按会话停止）时调用，确保 agent / bash / powershell
    等子进程被终止，并防止 kill 通知触发 auto_resume_bg 错误恢复。

    Args:
        bundle: 运行时数据 bundle
        session_ids: 可选：仅停止归属于这些会话 ID 的任务（Web 多会话
            模式下按会话停止；None 表示停止全部）
    """
    from illusion.tasks.manager import get_task_manager
    manager = get_task_manager()
    running = [t for t in manager.list_tasks() if t.status in ("running", "pending")]
    if session_ids is not None:
        running = [
            t for t in running
            if t.metadata.get("owner_session_id", "") in session_ids
        ]
    for t in running:
        try:
            await manager.stop_task(t.id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("停止任务 %s 时出错", t.id)
    # 清理 tracker，防止已 kill 任务触发的 notify_completed 导致 auto_resume_bg 恢复
    tracker = bundle.engine._bg_agent_tracker
    if tracker is not None:
        tracker.clear()


async def handle_background_completions(
    bundle: RuntimeBundle,
    *,
    print_system: SystemPrinter,
    render_event: StreamRenderer,
) -> bool:
    """后台任务完成后自动恢复处理：注入积压通知并让模型继续。

    主循环空闲期间（idle 超时或用户主动退出 busy）后台任务完成时，
    host 层调用本函数自动进入 busy：不追加用户输入，先 drain 积压的
    <task-notification> 通知注入为 user 消息，再让模型继续处理。

    Args:
        bundle: 运行时数据 bundle
        print_system: 系统消息打印回调
        render_event: 流式事件渲染回调

    Returns:
        bool: 是否继续会话（始终为 True）
    """
    tracker = bundle.engine._bg_agent_tracker
    # 仅在有实际完成通知时才处理；任务仍在运行时（pending 但无 completion）
    # 直接返回，避免发起无意义的 LLM 调用
    if tracker is None or not tracker.has_completions():
        return True
    settings = bundle.current_settings()
    bundle.engine.set_max_turns(settings.max_turns)
    system_prompt = build_runtime_system_prompt(
        settings,
        cwd=bundle.cwd,
        latest_user_prompt=_last_user_text(bundle.engine.messages),
        channel_hint=bundle.channel_hint,
    )
    # 追加钩子注入的 additionalContext
    for ctx in bundle.hook_additional_contexts:
        if ctx:
            system_prompt = system_prompt + "\n\n" + _wrap_in_system_reminder(ctx)
    bundle.engine.set_system_prompt(system_prompt)
    try:
        async for event in bundle.engine.process_background_completions():
            await render_event(event)
    except MaxTurnsExceeded as exc:
        await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
    # 更新会话 meta（替代旧 save_session_snapshot）
    _update_session_meta(bundle)
    sync_app_state(bundle)
    return True


async def _render_command_result(
	result: CommandResult,
	print_system: SystemPrinter,
	clear_output: ClearHandler,
	render_event: StreamRenderer | None = None,
	replay_transcript_item: TranscriptItemSender | None = None,
	command_result_emitter: CommandResultEmitter | None = None,
	replace_transcript_items: ReplaceTranscriptItems | None = None,
	rewind_restored_emitter: Callable[[str], Awaitable[None]] | None = None,
) -> None:
	"""渲染命令执行结果。

	Args:
		result: 命令执行结果
		print_system: 系统消息打印回调
		clear_output: 清空输出回调
		render_event: 流式事件渲染回调
		replay_transcript_item: 重播 transcript_item 的回调
		command_result_emitter: 指令结果发射回调
		replace_transcript_items: 替换转录项列表的回调
		rewind_restored_emitter: rewind 被回退的 user 消息回调（前端回填输入框）
	"""
	# rewind 被回退的 user 消息：通知前端回填输入框（重新编辑）。
	# 必须在分支前调用——rewind 返回 replay_messages（回退后的消息），
	# 会命中下方 replay 分支并提前 return，末尾调用会被跳过。
	if result.rewind_restored_text and rewind_restored_emitter is not None:
		await rewind_restored_emitter(result.rewind_restored_text)

	if result.replay_messages and replace_transcript_items is not None:
		from illusion.engine.messages import ToolResultBlock, ToolUseBlock

		tool_uses_by_id: dict[str, dict[str, Any]] = {}
		# 第一遍：收集所有 tool_use_id 和 tool_result 的 tool_use_id
		all_tool_use_ids: set[str] = set()
		all_tool_result_ids: set[str] = set()
		for msg in result.replay_messages:
			for block in msg.content:
				if isinstance(block, ToolUseBlock):
					all_tool_use_ids.add(block.id)
				elif isinstance(block, ToolResultBlock):
					all_tool_result_ids.add(block.tool_use_id)

		replay_items: list[dict[str, Any]] = []
		for msg in result.replay_messages:
			if msg.role == "user":
				# 跳过后台任务完成通知与 goal harness 注入消息：
				# 仅注入 LLM，不参与前端重放渲染
				if msg.text.strip() and not is_task_notification(msg.text) and not is_goal_system_message(msg.text):
					replay_items.append({"role": "user", "text": msg.text})
				for block in msg.content:
					if isinstance(block, ToolResultBlock):
						tool_info = tool_uses_by_id.get(block.tool_use_id, {})
						replay_items.append({
							"role": "tool_result",
							"text": block.text_content,
							"tool_name": tool_info.get("name"),
							"tool_use_id": block.tool_use_id,
							"is_error": block.is_error,
						})
			elif msg.role == "assistant":
				reasoning = msg.thinking_text.strip()
				assistant_text = msg.text.strip()
				has_tool_use = any(isinstance(b, ToolUseBlock) for b in msg.content)
				# 保留 reasoning 与工具前导 text（原实现把前导 text 置空，
				# rewind 重放后工具前导 text 丢失；重放不会重新触发
				# tool_started，不存在重复显示问题）
				if has_tool_use:
					if reasoning or assistant_text:
						item = {"role": "assistant", "text": assistant_text}
						if reasoning:
							item["reasoning"] = reasoning
						replay_items.append(item)
				elif assistant_text or reasoning:
					item = {"role": "assistant", "text": assistant_text}
					if reasoning:
						item["reasoning"] = reasoning
					replay_items.append(item)
				for block in msg.content:
					if isinstance(block, ToolUseBlock):
						# 跳过孤立的 tool_use（没有对应 tool_result 的）
						if block.id not in all_tool_result_ids:
							continue
						tool_uses_by_id[block.id] = {"name": block.name, "input": block.input}
						replay_items.append({
							"role": "tool",
							"text": f"{block.name} {json.dumps(block.input, ensure_ascii=True)}",
							"tool_name": block.name,
							"tool_input": block.input,
							"tool_use_id": block.id,
						})
		await replace_transcript_items(replay_items)
		if result.message and command_result_emitter is not None:
			await command_result_emitter(result.message, "info")
		return
	elif result.clear_screen:
		await clear_output()
		if result.replay_messages and render_event is not None:
			from illusion.api.usage import UsageSnapshot
			from illusion.engine.messages import ToolResultBlock, ToolUseBlock
			from illusion.engine.stream_events import AssistantTurnComplete

			await clear_output()
			# 收集所有 tool_use_id 和 tool_result 的 tool_use_id，用于过滤孤立 tool_use
			all_tool_result_ids2: set[str] = set()
			for msg in result.replay_messages:
				for block in msg.content:
					if isinstance(block, ToolResultBlock):
						all_tool_result_ids2.add(block.tool_use_id)

			tool_uses_by_id2: dict[str, dict[str, Any]] = {}
			for msg in result.replay_messages:
				if msg.role == "user":
					# 跳过后台任务完成通知与 goal harness 注入消息：
					# 仅注入 LLM，不参与前端重放渲染
					if msg.text.strip() and not is_task_notification(msg.text) and not is_goal_system_message(msg.text):
						if replay_transcript_item is not None:
							await replay_transcript_item({"role": "user", "text": msg.text})
						else:
							await print_system(f"> {msg.text}")
					for block in msg.content:
						if isinstance(block, ToolResultBlock) and replay_transcript_item is not None:
							tool_info = tool_uses_by_id2.get(block.tool_use_id, {})
							await replay_transcript_item({
								"role": "tool_result",
								"text": block.text_content,
								"tool_name": tool_info.get("name"),
								"tool_use_id": block.tool_use_id,
								"is_error": block.is_error,
							})
				elif msg.role == "assistant":
					reasoning = msg.thinking_text.strip()
					assistant_text = msg.text.strip()
					has_tool_use = any(isinstance(b, ToolUseBlock) for b in msg.content)
					# 保留 reasoning 与工具前导 text（原实现把前导 text 置空，
					# resume 重放后工具前导 text 丢失；重放不会重新触发
					# tool_started，不存在重复显示问题）
					if has_tool_use:
						if replay_transcript_item is not None and (reasoning or assistant_text):
							item = {"role": "assistant", "text": assistant_text}
							if reasoning:
								item["reasoning"] = reasoning
							await replay_transcript_item(item)
					else:
						if replay_transcript_item is not None and (assistant_text or reasoning):
							item = {"role": "assistant", "text": assistant_text}
							if reasoning:
								item["reasoning"] = reasoning
							await replay_transcript_item(item)
						elif assistant_text:
							await render_event(AssistantTurnComplete(message=msg, usage=UsageSnapshot()))
					for block in msg.content:
						if isinstance(block, ToolUseBlock):
							# 跳过孤立的 tool_use（没有对应 tool_result 的）
							if block.id not in all_tool_result_ids2:
								continue
							tool_uses_by_id2[block.id] = {"name": block.name, "input": block.input}
							if replay_transcript_item is not None:
								await replay_transcript_item({
									"role": "tool",
									"text": f"{block.name} {json.dumps(block.input, ensure_ascii=True)}",
									"tool_name": block.name,
									"tool_input": block.input,
									"tool_use_id": block.id,
								})
	elif result.clear_screen:
		await clear_output()
	if result.message and not result.replay_messages:
		if result.drive_goal and command_result_emitter is not None:
			# goal 驱动行（创建/resume）：轮次提示（goal_status → 第x轮）随即
			# 到达并顶掉本回执，web 端再弹一次只会产生"看不清的闪烁"——
			# 跳过 emitter；终端无 emitter，仍经 print_system 保留回执
			pass
		elif command_result_emitter is not None:
			await command_result_emitter(result.message, "info")
		else:
			await print_system(result.message)
