"""
会话管理斜杠命令
================

/new, /compact, /rewind, /context, /resume, /delete, /rename
"""

from __future__ import annotations

from illusion.api.errors import IllusionAgentApiError
from illusion.commands.types import CommandContext, CommandResult
from illusion.config.i18n import t
from illusion.config.settings import load_settings, save_settings
from illusion.engine.messages import ConversationMessage
from illusion.goal.prompts import is_goal_system_message
from illusion.prompts import build_runtime_system_prompt
from illusion.services import (
    estimate_conversation_tokens,
    get_context_window,
)
from illusion.tasks.types import is_task_notification


async def new_handler(_: str, context: CommandContext) -> CommandResult:
    """启动新会话。

    不保存当前会话（每轮已 checkpoint），不清空 checkpoint 目录。
    full_reset 清空所有内存状态，由 runtime 生成新 session_id。
    """
    context.engine.full_reset()
    return CommandResult(
        message="Started a new conversation session.",
        clear_screen=True,
        reset_session=True,
        refresh_state=True,
    )


async def context_handler(args: str, context: CommandContext) -> CommandResult:
    """显示上下文使用量、系统提示词或管理上下文窗口"""
    settings = load_settings()
    tokens = args.split(maxsplit=1)
    subcommand = tokens[0] if tokens else "usage"

    if subcommand in ("usage", "__usage__"):
        # 上下文占用：最后一次 API 调用的真实值 + 新增消息估算
        estimated_used = context.engine.current_context_tokens()
        usage = context.engine.total_usage
        context_window = get_context_window()
        percentage = round(estimated_used * 100 / context_window) if context_window > 0 else 0
        remaining = max(0, context_window - estimated_used)
        last_usage = context.engine.last_api_usage
        if last_usage is not None:
            # 最后一次 API 调用的真实分项
            cache_read = last_usage.cache_read_input_tokens
            cache_creation = last_usage.cache_creation_input_tokens
            cached = cache_read + cache_creation
            uncached = last_usage.input_tokens
            output = last_usage.output_tokens
            cached_pct = round(cached * 100 / context_window) if context_window > 0 else 0
            uncached_pct = round(uncached * 100 / context_window) if context_window > 0 else 0
            output_pct = round(output * 100 / context_window) if context_window > 0 else 0
            # 缓存命中率 = cache_read / (cache_read + cache_creation + input_tokens)
            total_input = cached + uncached
            hit_rate = round(cache_read * 100 / total_input) if total_input > 0 else 0
            lines = [
                t("context_usage_title", context_window=context_window),
                t("context_input_cached", cached=cached, cached_pct=cached_pct),
                t("context_input_uncached", uncached=uncached, uncached_pct=uncached_pct),
                t("context_output_line", output_tokens=output, output_pct=output_pct),
                t("context_cache_hit_rate", hit_rate=hit_rate),
                t("context_used_total", used=estimated_used, percentage=percentage),
                t("context_remaining", remaining=remaining),
            ]
        else:
            lines = [
                t("context_usage_title", context_window=context_window),
                t("context_used_total", used=estimated_used, percentage=percentage),
                t("context_remaining", remaining=remaining),
            ]
        # 累积用量（缓存 = cache_read + cache_creation）
        cached_total = (
            usage.cache_read_input_tokens + usage.cache_creation_input_tokens
        )
        lines.append(t(
            "context_cumulative_detail",
            cache_read=cached_total,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        ))
        return CommandResult(message="\n".join(lines))
    if subcommand == "show":
        # 显示当前运行时完整的系统提示词
        system_prompt = context.engine._system_prompt
        return CommandResult(message=system_prompt or "(no system prompt)")
    if subcommand == "window":
        return CommandResult(message=f"Context window: {settings.context_window:,} tokens")
    if subcommand == "set" and len(tokens) == 2:
        try:
            value = int(tokens[1])
            if value <= 0:
                return CommandResult(message="Error: context window must be positive")
            settings.context_window = value
            save_settings(settings)
            return CommandResult(message=f"Context window set to {value:,} tokens")
        except ValueError:
            return CommandResult(message="Error: invalid number")
    return CommandResult(message="Usage: /context [usage|show|window|set N]")


async def compact_handler(args: str, context: CommandContext) -> CommandResult:
    """压缩对话历史"""
    from illusion.services.compact import compact_conversation, compact_messages

    preserve_recent = 6
    custom_instructions: str | None = None

    if args:
        stripped = args.strip()
        try:
            preserve_recent = max(1, int(stripped))
        except ValueError:
            custom_instructions = stripped

    before = len(context.engine.messages)
    before_tokens = estimate_conversation_tokens(context.engine.messages)

    try:
        settings = load_settings()
        system_prompt = build_runtime_system_prompt(settings, cwd=context.cwd, channel_hint=context.channel_hint)
        compacted = await compact_conversation(
            context.engine.messages,
            api_client=context.engine._api_client,
            model=context.engine._model,
            system_prompt=system_prompt,
            preserve_recent=preserve_recent,
            custom_instructions=custom_instructions,
            suppress_follow_up=False,
        )
    except (IllusionAgentApiError, OSError, ValueError, KeyError, RuntimeError) as exc:
        import logging
        logging.getLogger(__name__).warning("LLM compact failed, falling back to simple compact: %s", exc)
        compacted = compact_messages(context.engine.messages, preserve_recent=preserve_recent)

    context.engine.load_messages(compacted)
    # 压缩后清除 last_api_usage：压缩前的真实值已不代表压缩后的上下文，
    # 回退到估算模式直到下一次 API 调用（避免 context 显示虚高/重复压缩）
    context.engine.invalidate_last_api_usage()
    # 压缩是不可逆操作：重建 checkpoint，否则退出后 resume/rewind
    # 会恢复到压缩前的完整历史（旧消息已被摘要替代）
    checkpoint_store = context.engine.checkpoint_store
    if checkpoint_store is not None:
        total = context.engine.total_usage
        try:
            await checkpoint_store.rebuild_after_compact(
                compacted,
                usage_input=total.input_tokens,
                usage_output=total.output_tokens,
                usage_cache_read=total.cache_read_input_tokens,
                usage_cache_creation=total.cache_creation_input_tokens,
            )
        except OSError as exc:
            # 会话目录/context.jsonl 缺失（历史文件被清理等）时跳过重建：
            # 压缩已作用于内存引擎，checkpoint 缺失只影响重启后的恢复
            import logging
            logging.getLogger(__name__).warning(
                "compact checkpoint 重建失败（跳过）: %s", exc
            )
    after_tokens = estimate_conversation_tokens(compacted)
    saved = max(0, before_tokens - after_tokens)
    from illusion.config.i18n import t
    return CommandResult(
        message=t("compact_result", before=before, after=len(compacted), saved=f"{saved:,}"),
        # 返回压缩后的消息供前端替换转录：terminal 走 replace_transcript_items，
        # web 端 web_query 检测到 replay_messages 后发 transcript_replace，
        # 否则前端仍显示压缩前的完整历史，与后端状态不一致
        replay_messages=compacted,
        refresh_state=True,
    )


async def resume_handler(args: str, context: CommandContext) -> CommandResult:
    """恢复已保存的会话。

    通过 CheckpointStore.restore() 单遍扫描 context.jsonl 重建完整状态。
    """
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import (
        list_session_snapshots,
        read_index,
        read_meta,
        session_dir_for,
        write_index_to,
    )

    tokens = args.strip().split()

    # /resume <session_id> or /resume #<turn_number>
    if tokens:
        sid = tokens[0]
        # 支持轮次编号引用（如 #1, #2）
        if sid.startswith("#") and sid[1:].isdigit():
            turn_num = int(sid[1:])
            sessions = list_session_snapshots(context.cwd, limit=20)
            if 1 <= turn_num <= len(sessions):
                sid = sessions[turn_num - 1]["session_id"]
            else:
                return CommandResult(message=f"Invalid turn number: {sid}. Use /resume to see available sessions.")

        # 校验 session_id 合法性（防路径遍历）
        from illusion.services.session_storage import InvalidSessionIdError
        try:
            # 读 meta.json 验证存在
            meta = read_meta(context.cwd, sid)
        except InvalidSessionIdError:
            return CommandResult(message=f"Invalid session id: {sid}")
        if meta is None:
            return CommandResult(message=f"Session not found: {sid}")

        # 构造 CheckpointStore 并 restore
        # session_dir 经 session_dir_for 统一计算，attach_session 以
        # store 为唯一权威（session_id/file_history 由 store 派生），
        # 保证 context.jsonl / meta.json / file_history.json 同目录。
        session_dir = session_dir_for(context.cwd, sid)
        store = CheckpointStore(session_dir, sid)
        result = await store.restore()

        # 应用到 engine
        context.engine.attach_session(store)
        context.engine.apply_restore(result)

        # 加载文件历史（传入 checkpoint_count 做崩溃恢复对齐）
        context.engine.load_file_history(checkpoint_count=store.next_checkpoint_id)

        # 更新 index.json
        write_index_to(session_dir, sid)

        summary = meta.get("summary", "")[:60]
        return CommandResult(
            message=f"Restored {len(result.messages)} messages from session {sid}"
            + (f" ({summary})" if summary else ""),
            replay_messages=result.messages,
            restored_session_id=sid,
            refresh_state=True,
            clear_screen=True,
        )

    # /resume — 列出会话或加载 latest
    sessions = list_session_snapshots(context.cwd, limit=10)
    if not sessions:
        return CommandResult(message="No saved sessions found for this project.")

    # 无参时读 index.json 获取 latest
    index = read_index(context.cwd)
    if index is None:
        # 无 index 则列出会话供选择
        import time
        lines = ["Saved sessions:"]
        for i, s in enumerate(sessions, 1):
            ts = time.strftime("%m/%d %H:%M", time.localtime(s.get("updated_at", s.get("created_at", 0))))
            display = s.get("title") or s.get("summary", "")[:50] or "(no summary)"
            turn_count = s.get("turn_count", 0)
            lines.append(f"  #{i}  {s['session_id']}  {ts}  {turn_count}轮  {display}")
        lines.append("")
        lines.append("Usage: /resume #1 or /resume <session_id>")
        return CommandResult(message="\n".join(lines))

    # 有 index 直接恢复 latest
    sid = index.get("latest_session_id", "")
    if not sid:
        return CommandResult(message="No latest session in index.")

    meta = read_meta(context.cwd, sid)
    if meta is None:
        return CommandResult(message=f"Latest session {sid} not found.")

    session_dir = session_dir_for(context.cwd, sid)
    store = CheckpointStore(session_dir, sid)
    result = await store.restore()
    context.engine.attach_session(store)
    context.engine.apply_restore(result)

    # 加载文件历史（传入 checkpoint_count 做崩溃恢复对齐）
    context.engine.load_file_history(checkpoint_count=store.next_checkpoint_id)

    write_index_to(session_dir, sid)

    summary = meta.get("summary", "")[:60]
    return CommandResult(
        message=f"Restored {len(result.messages)} messages from the latest session."
        + (f" ({summary})" if summary else ""),
        replay_messages=result.messages,
        restored_session_id=sid,
        refresh_state=True,
        clear_screen=True,
    )


async def rewind_handler(args: str, context: CommandContext) -> CommandResult:
    """回退对话回合

    支持两种模式：
    - both（默认）：同时回退对话和文件
    - conversation：仅回退对话

    用法：/rewind [TURNS] [both|conversation]
    """

    parts = args.strip().split()
    turns = 1
    mode = "both"
    if parts:
        try:
            turns = max(1, int(parts[0]))
        except ValueError:
            return CommandResult(message="Usage: /rewind [TURNS] [both|conversation]")
        if len(parts) > 1:
            mode = parts[1].lower()
            if mode not in ("both", "conversation"):
                return CommandResult(message="Usage: /rewind [TURNS] [both|conversation]")

    store = context.engine.checkpoint_store
    if store is None or store.next_checkpoint_id == 0:
        return CommandResult(message="No checkpoint to rewind.")

    target_id = store.next_checkpoint_id - turns
    if target_id < 0:
        return CommandResult(
            message=f"Cannot rewind {turns} turns, only {store.next_checkpoint_id} available."
        )

    removed = 0
    restored_messages: list[ConversationMessage] | None = None

    # 记录被回退轮次中"恢复点"的那条 user 消息（回退后引擎消息已变，
    restored_text: str | None = None
    if turns > 0:
        removed_user_texts: list[str] = []
        for msg in reversed(context.engine.messages):
            if (
                msg.role == "user"
                and msg.text.strip()
                and not is_task_notification(msg.text)
                and not is_goal_system_message(msg.text)
            ):
                removed_user_texts.append(msg.text.strip())
                if len(removed_user_texts) >= turns:
                    break
        if removed_user_texts:
            # 倒序收集：列表末尾 = 时间顺序最前 = 倒数第 N 轮
            restored_text = removed_user_texts[-1]

    # 回退对话
    if mode in ("both", "conversation"):
        result = await store.rewind_to(target_id)
        context.engine.apply_restore(result)
        removed = turns  # 简化：回退的 turn 数
        restored_messages = result.messages

    # 回退文件（仅 both 模式）
    reverted_count = 0
    if mode == "both":
        fh = context.engine.file_history
        if fh is not None and fh.snapshots:
            from illusion.services.file_history import rewind_to
            # 复用预先计算的 target_id（= 原始 next_cp - turns）
            # 不能用 store.next_checkpoint_id，因为对话 rewind 后它已变小
            reverted_files = rewind_to(fh, target_id)
            reverted_count = len(reverted_files)

    lines = []
    if removed > 0:
        lines.append(f"Rewound {turns} turn(s); removed {removed} message(s).")
    if reverted_count > 0:
        lines.append(f"Reverted {reverted_count} file(s).")
    if not lines:
        lines.append("Nothing to rewind.")

    return CommandResult(
        clear_screen=True,
        replay_messages=restored_messages if mode in ("both", "conversation") else None,
        message="\n".join(lines),
        refresh_state=True,
        rewind_restored_text=restored_text if mode in ("both", "conversation") else None,
    )


async def delete_handler(args: str, context: CommandContext) -> CommandResult:
    """删除已保存的会话（rmtree 整个 {sid}/ 目录）"""
    from illusion.services.file_history import cleanup_file_history
    from illusion.services.session_storage import (
        delete_all_sessions,
        delete_session_by_id,
        list_session_snapshots,
    )

    tokens = args.strip().split()

    # /delete — 列出会话
    if not tokens:
        sessions = list_session_snapshots(context.cwd, limit=10)
        if not sessions:
            return CommandResult(message="No saved sessions found for this project.")
        import time
        lines = ["Saved sessions:"]
        for i, s in enumerate(sessions, 1):
            ts = time.strftime("%m/%d %H:%M", time.localtime(s.get("updated_at", s.get("created_at", 0))))
            display = s.get("title") or s.get("summary", "")[:50] or "(no summary)"
            turn_count = s.get("turn_count", 0)
            lines.append(f"  #{i}  {s['session_id']}  {ts}  {turn_count}轮  {display}")
        lines.append("")
        lines.append("Usage: /delete #1 or /delete <session_id>  — delete a specific session")
        lines.append("       /delete all                        — delete all sessions")
        return CommandResult(message="\n".join(lines))

    # /delete all
    if tokens[0] in ("all", "__all__"):
        sessions = list_session_snapshots(context.cwd, limit=1000)
        count = delete_all_sessions(context.cwd)
        # 仅清理当前工作目录下各会话的文件历史（file-history 按 session_id
        # 隔离存储；不能调 cleanup_all_file_histories，那会清掉其他目录的
        # 撤销/恢复历史）
        from illusion.services.file_history import cleanup_file_history
        for s in sessions:
            cleanup_file_history(s["session_id"])
        context.engine.full_reset()
        return CommandResult(
            message=f"Deleted {count} session(s).",
            clear_screen=True,
            reset_session=True,
            refresh_state=True,
        )

    # /delete <session_id> or /delete #<turn_number>
    sid = tokens[0]
    if sid.startswith("#") and sid[1:].isdigit():
        turn_num = int(sid[1:])
        sessions = list_session_snapshots(context.cwd, limit=20)
        if 1 <= turn_num <= len(sessions):
            sid = sessions[turn_num - 1]["session_id"]
        else:
            return CommandResult(message=f"Invalid turn number: {sid}. Use /delete to see available sessions.")
    # 校验 session_id 合法性（防路径遍历）
    from illusion.services.session_storage import InvalidSessionIdError
    try:
        deleted = delete_session_by_id(context.cwd, sid)
    except InvalidSessionIdError:
        return CommandResult(message=f"Invalid session id: {sid}")
    if deleted:
        cleanup_file_history(sid)
        if sid == context.session_id:
            context.engine.full_reset()
            return CommandResult(
                message=f"Deleted current session: {sid}",
                clear_screen=True,
                reset_session=True,
                refresh_state=True,
            )
        return CommandResult(message=f"Deleted session: {sid}")
    return CommandResult(message=f"Session not found: {sid}")


async def rename_handler(args: str, context: CommandContext) -> CommandResult:
    """重命名会话。

    用法：
        /rename <名称>              — 重命名当前会话
        /rename #N <名称>           — 重命名第 N 个会话
        /rename <session_id> <名称> — 重命名指定会话
        /rename --clear             — 清除当前会话的自定义名称
        /rename                     — 列出会话供选择（Terminal）

    直接操作 meta.json，不依赖 engine.checkpoint_store，
    使非活动会话（仅磁盘）也能被重命名。
    """
    import time

    from illusion.services.session_storage import (
        list_session_snapshots,
        read_meta,
        write_meta,
    )

    args = args.strip()

    # 无参数：列出会话供选择
    if not args:
        sessions = list_session_snapshots(context.cwd, limit=20)
        if not sessions:
            return CommandResult(message=t("rename_no_sessions"))
        lines = [t("rename_prompt_select")]
        for i, s in enumerate(sessions, 1):
            ts = time.strftime("%m/%d %H:%M", time.localtime(s.get("updated_at", s.get("created_at", 0))))
            display = s.get("title") or s.get("summary", "")[:50] or "(no summary)"
            turn_count = s.get("turn_count", 0)
            lines.append(f"  #{i}  {s['session_id']}  {ts}  {turn_count}轮  {display}")
        lines.append("")
        lines.append(t("rename_no_args"))
        return CommandResult(message="\n".join(lines))

    # --clear：清除当前会话的自定义名称
    if args == "--clear":
        meta = read_meta(context.cwd, context.session_id)
        if meta is None:
            return CommandResult(message=t("rename_not_found", sid=context.session_id))
        meta.pop("title", None)
        write_meta(context.cwd, context.session_id, meta)
        # 同步当前会话显示名称（应用状态关联字段）
        if context.app_state is not None:
            context.app_state.set(session_name="")
        return CommandResult(message=t("rename_cleared"), refresh_state=True)

    # 解析目标会话和名称
    tokens = args.split(None, 1)
    target_sid = context.session_id
    name = ""

    first = tokens[0]
    rest = tokens[1].strip() if len(tokens) > 1 else ""

    # #N 引用
    if first.startswith("#") and first[1:].isdigit():
        n = int(first[1:])
        sessions = list_session_snapshots(context.cwd, limit=20)
        if 1 <= n <= len(sessions):
            target_sid = sessions[n - 1]["session_id"]
            name = rest
        else:
            return CommandResult(message=f"Invalid session number: {first}")
    # session_id 引用（12 位 hex）
    elif len(first) == 12 and all(c in "0123456789abcdef" for c in first.lower()):
        target_sid = first
        name = rest
    else:
        # 整个 args 是当前会话的新名称
        name = args

    # 清理并校验名称
    name = name.strip()
    if not name:
        return CommandResult(message=t("rename_empty_name"))
    # 限制长度（与 summary 80 字符一致），防止终端列表/侧边栏溢出
    name = name[:80]

    # 读取 meta，设置 title，写回
    from illusion.services.session_storage import InvalidSessionIdError
    try:
        meta = read_meta(context.cwd, target_sid)
    except InvalidSessionIdError:
        return CommandResult(message=t("rename_not_found", sid=target_sid))
    if meta is None:
        return CommandResult(message=t("rename_not_found", sid=target_sid))
    meta["title"] = name
    write_meta(context.cwd, target_sid, meta)

    # 重命名的是当前会话：同步应用状态显示名称，使终端/Web 标题即时更新
    if (
        target_sid == context.session_id
        and context.app_state is not None
    ):
        context.app_state.set(session_name=name)

    return CommandResult(
        message=t("rename_set", title=name),
        refresh_state=True,
    )
