"""
主命令回调
==========

处理 Illusion Agent 的主命令逻辑，包括交互式会话、非交互式打印模式、工作目录切换等。

主要功能:
    - 启动交互式会话
    - 非交互式打印模式处理
    - 工作目录切换（基于 settings 或 --cwd 参数）
    - 渠道和 Cron 任务的自动激活
    - 会话恢复和管理
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import typer

from illusion.cli import _version_callback, app
from illusion.cli.shared import _ensure_language
from illusion.cli.workspace import validate_and_normalize
from illusion.config.i18n import t as _t


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit",
        callback=_version_callback,
        is_eager=True,
    ),
    # --- Session ---
    continue_session: bool = typer.Option(
        False,
        "--continue",
        "-c",
        help="Continue the most recent conversation in the current directory",
        rich_help_panel="Session",
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        "-r",
        help="Resume a conversation by session ID, or open picker",
        rich_help_panel="Session",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Set a display name for this session",
        rich_help_panel="Session",
    ),
    # --- Model & Effort ---
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model ID in env_N.model_N format (e.g. 'env_1.model_2')",
        rich_help_panel="Model & Effort",
    ),
    effort: str | None = typer.Option(
        None,
        "-e", "--effort",
        help="Effort level (low, medium, high, xhigh, max). Persists to settings.",
        rich_help_panel="Model & Effort",
    ),
    max_turns: int | None = typer.Option(
        None,
        "-t", "--max-turns",
        help="Maximum agentic turns. Persists to settings.",
        rich_help_panel="Model & Effort",
    ),
    # --- Output ---
    print_mode: str | None = typer.Option(
        None,
        "--print",
        "-p",
        help="Print response and exit. Pass your prompt as the value: -p 'your prompt'",
        rich_help_panel="Output",
    ),
    output_format: str | None = typer.Option(
        None,
        "--output-format",
        help="Output format with --print: text (default), json, or stream-json",
        rich_help_panel="Output",
    ),
    # --- Permissions ---
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Permission mode: default, plan, full_auto, or yolo",
        rich_help_panel="Permissions",
    ),
    dangerously_skip_permissions: bool = typer.Option(
        False,
        "--dangerously-skip-permissions",
        help="Bypass all permission checks (only for sandboxed environments)",
        rich_help_panel="Permissions",
    ),
    # --- Advanced ---
    cwd: str | None = typer.Option(
        None,
        "--cwd",
        help="Working directory for the session",
        hidden=True,
    ),
    backend_only: bool = typer.Option(
        False,
        "--backend-only",
        help="Run the structured backend host for the React terminal UI",
        hidden=True,
    ),
) -> None:
    """主入口函数：启动交互式会话或运行单个提示词

    支持多种运行模式：
    - 交互式会话模式（默认）
    - 非交互式打印模式（使用 -p/--print）
    - 继续会话（使用 --continue 或 --resume）

    Args:
        ctx: Typer 上下文对象
        version: 显示版本号选项
        continue_session: 继续最近会话选项
        resume: 通过会话 ID 恢复会话选项
        name: 会话显示名称
        model: 模型别名或完整模型 ID
        effort: 会话努力级别
        max_turns: 最大代理轮次数
        print_mode: 打印模式提示词
        output_format: 输出格式
        permission_mode: 权限模式
        dangerously_skip_permissions: 跳过权限检查
        cwd: 会话工作目录
        backend_only: 运行结构化后端主机
    """
    # 读取settings.json中的working_directory字段，切换工作目录（子命令也适用）
    from illusion.config import load_settings
    settings = load_settings()
    # 仅在用户未显式指定 --cwd 时，才使用 settings.working_directory
    if cwd is None and settings.working_directory:
        cwd = settings.working_directory
    if cwd:
        working_dir = Path(cwd).expanduser().resolve()
        if working_dir.exists() and working_dir.is_dir():
            os.chdir(working_dir)
            cwd = str(working_dir)
        else:
            import logging
            logging.getLogger(__name__).warning(
                _t("cwd_invalid", path=cwd)
            )

    if ctx.invoked_subcommand is not None:  # 如果调用了子命令，直接返回
        return

    # 渠道自动激活：有 enabled 渠道时 spawn 守护进程
    # 注意：backend_only 模式下不 spawn（launcher 已负责），只连接持有 ref
    _daemon_proc = None
    _daemon_client = None
    try:
        from illusion.channels import maybe_spawn_channel_daemon
        _daemon_proc, _daemon_client = maybe_spawn_channel_daemon(
            spawn_if_missing=not backend_only,
        )
    except (OSError, RuntimeError) as exc:
        import logging
        logging.getLogger(__name__).warning("渠道自动激活失败: %s", exc)

    # cron 自动激活：有启用任务时 spawn 守护进程
    _cron_proc = None
    _cron_client = None
    try:
        from illusion.services.cron_spawn import maybe_spawn_cron_daemon
        _cron_proc, _cron_client = maybe_spawn_cron_daemon(
            spawn_if_missing=not backend_only,
        )
    except (OSError, RuntimeError) as exc:
        import logging
        logging.getLogger(__name__).warning("cron 自动激活失败: %s", exc)

    # PC 端渠道感知：有 enabled 渠道时注入 channel_hint + channel_tools
    pc_channel_hint: str | None = None
    pc_channel_tools: list[Any] | None = None
    try:
        from illusion.channels.config import load_channels_config
        from illusion.prompts.channel_hints import (
            get_channel_hint,
            list_active_sessions,
        )
        _cfg = load_channels_config()
        if _cfg.has_enabled_channels():
            other_names = _cfg.enabled_channel_names()
            _active = {
                name: list_active_sessions(name, _cfg, limit=5)
                for name in other_names
            }
            pc_channel_hint = get_channel_hint(
                current_channel=None,
                channels_config=_cfg,
                active_sessions=_active,
            )
            # 注入跨渠道工具
            from illusion.channels.tools.cross_channel import (
                ListChannelSessionsTool,
                SendToChannelTool,
            )
            pc_channel_tools = [ListChannelSessionsTool(_cfg), SendToChannelTool(_cfg)]
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        import logging
        logging.getLogger(__name__).warning("PC 渠道感知加载失败: %s", exc)

    import asyncio  # 异步编程模块

    if dangerously_skip_permissions:  # 如果跳过权限检查
        permission_mode = "full_auto"  # 设置为完全自动模式

    from illusion.ui.app import run_print_mode, run_repl  # 导入 UI 模块

    # -c/-r 不带 -p 且非 --backend-only 时报错
    if (continue_session or resume is not None) and print_mode is None and not backend_only:
        print(_t("continue_requires_print"), file=sys.stderr)
        raise typer.Exit(1)

    # 处理 --continue 和 --resume 标志
    if (continue_session or resume is not None) and backend_only:
        # backend-only 模式：在当前进程加载会话（子进程路径）
        from illusion.services.checkpoint_store import CheckpointStore
        from illusion.services.session_storage import (
            list_session_snapshots,
            read_index,
            read_meta,
            session_dir_for,
        )

        async def _load_session(sid: str) -> dict[str, Any]:
            """用 CheckpointStore 加载会话数据（替代旧 load_session_by_id）。"""
            _cwd = cwd or "."
            meta = read_meta(_cwd, sid) or {}
            session_dir = session_dir_for(_cwd, sid)
            store = CheckpointStore(session_dir, sid)
            result = await store.restore()
            return {
                "session_id": sid,
                "model": meta.get("model", ""),
                "summary": meta.get("summary", ""),
                "messages": [m.model_dump(mode="json") for m in result.messages],
            }

        session_data = None
        assert cwd is not None
        if continue_session:
            index = read_index(cwd)
            if index is None or not index.get("latest_session_id"):
                print(_t("session_not_found_prev"), file=sys.stderr)
                raise typer.Exit(1)
            session_data = asyncio.run(_load_session(index["latest_session_id"]))
            print(_t("session_continuing", summary=session_data.get('summary', '(?)')[:60]))
        elif resume == "" or resume is None:
            sessions = list_session_snapshots(cwd, limit=10)
            if not sessions:
                print(_t("session_no_saved"), file=sys.stderr)
                raise typer.Exit(1)
            print(_t("session_saved_list"))
            for i, s in enumerate(sessions, 1):
                print(f"  {i}. [{s['session_id']}] {s.get('summary', '?')[:50]} ({_t('session_msg_count', n=s['message_count'])})")
            choice = typer.prompt(_t("session_enter_id"))
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(sessions):
                    session_data = asyncio.run(_load_session(sessions[idx]["session_id"]))
                else:
                    print(_t("invalid_selection"), file=sys.stderr)
                    raise typer.Exit(1)
            except ValueError:
                session_data = asyncio.run(_load_session(choice))
            if session_data is None:
                print(_t("session_not_found", id=choice), file=sys.stderr)
                raise typer.Exit(1)
        else:
            session_data = asyncio.run(_load_session(resume))
            if session_data is None:
                print(_t("session_not_found", id=resume), file=sys.stderr)
                raise typer.Exit(1)

        # 会话数据直接传入 backend host
        asyncio.run(
            run_repl(
                prompt=None,
                cwd=cwd,
                model=session_data.get("model") or model,
                backend_only=True,
                restore_messages=session_data.get("messages"),
                restore_session_id=session_data.get("session_id"),
                effort=effort,
                channel_hint=pc_channel_hint,
                channel_tools=pc_channel_tools,
                # 透传其他参数
                permission_mode=permission_mode,
                name=name,
            )
        )
        return
    # 非 backend_only 时 fall through 到 print_mode 分支
    # （Step 2 已拦截 -c/-r 不带 -p 的情况）

    # 打印模式处理
    if print_mode is not None:
        prompt = print_mode.strip()
        if not prompt:
            print(_t("print_requires_prompt"), file=sys.stderr)
            raise typer.Exit(1)
        # resume="" 在 print 模式下报错（先校验，避免持久化副作用）
        if resume == "":
            print(_t("session_resume_requires_id"), file=sys.stderr)
            raise typer.Exit(1)
        # cron 上下文：通过环境变量 ILLUSION_PERMISSION_MODE 临时指定权限模式，
        # 不持久化到 settings.json（避免 cron 子进程污染全局权限配置）。
        # CLI --permission-mode 仍用于用户主动切换并持久化。
        effective_permission_mode = permission_mode or os.environ.get("ILLUSION_PERMISSION_MODE")
        # 持久化 model/effort/max_turns/permission_mode 到 settings.json
        # 注意：仅持久化 CLI 显式传入的 --permission-mode，不含 cron 环境变量
        if any(v is not None for v in (model, effort, max_turns, permission_mode)):
            from illusion.config import load_settings, save_settings
            _settings = load_settings()
            if model is not None:
                _settings.model = model
            if effort is not None:
                _settings.effort = effort
            if max_turns is not None:
                _settings.max_turns = max_turns
            if permission_mode is not None:
                from illusion.permissions.modes import PermissionMode
                _settings.permission.mode = PermissionMode(permission_mode)
            save_settings(_settings)
        # 运行打印模式
        asyncio.run(
            run_print_mode(
                prompt=prompt,  # 提示词
                output_format=output_format or "text",  # 输出格式
                cwd=cwd,  # 工作目录
                model=model,  # 模型
                permission_mode=effective_permission_mode,  # 权限模式
                max_turns=max_turns,  # 最大轮次
                effort=effort,  # 推理强度级别
                continue_session=continue_session,
                resume=resume,
                name=name,
            )
        )
        return

    # 启动交互式 REPL 会话
    # Ctrl+C 触发 KeyboardInterrupt，进入 except → pass → finally 关闭 IPC 连接
    # 守护进程通过连接数自管理，无需弹确认提示：
    #   - 仍有其他主程序连接 → 守护进程保持运行
    #   - 连接归零 → 守护进程检测后自动退出（grace 期内）
    try:
        asyncio.run(
            run_repl(
                prompt=None,  # 无初始提示词
                cwd=cwd,  # 工作目录
                model=model,  # 模型
                max_turns=max_turns,  # 最大轮次
                backend_only=backend_only,  # 仅后端模式
                effort=effort,  # 推理强度级别
                channel_hint=pc_channel_hint,
                channel_tools=pc_channel_tools,
                # 透传其他 CLI 参数
                permission_mode=permission_mode,
                name=name,
                continue_session=continue_session,
                resume=resume,
            )
        )
    except KeyboardInterrupt:
        pass  # IPC 连接关闭即触发守护进程退出
    finally:
        # 关闭 IPC 连接（OS 也会在进程退出时自动关闭）
        for ref in (_cron_client, _daemon_client):
            if ref is not None:
                ref.close()


@app.command("set")
def set_cmd(
    working_directory: str | None = typer.Argument(None, help="工作目录路径"),
) -> None:
    """设置工作目录

    无参数时显示当前工作目录；有参数时校验并设置（目录不存在则新建）。
    """
    from illusion.config import load_settings, save_settings

    _ensure_language()
    settings = load_settings()

    if working_directory is None:
        if settings.working_directory:
            print(_t("set_current_working_directory", path=settings.working_directory))
        else:
            print(_t("set_no_working_directory"))
            print(_t("set_usage"))
        return

    resolved, err = validate_and_normalize(working_directory)
    if resolved is None and err:
        print(_t("set_invalid_path", path=working_directory), file=sys.stderr)
        raise typer.Exit(1)
    if resolved is None:
        print(_t("set_no_working_directory"))
        print(_t("set_usage"))
        return

    settings.working_directory = str(resolved)
    save_settings(settings)
    print(_t("set_saved", path=str(resolved)))
