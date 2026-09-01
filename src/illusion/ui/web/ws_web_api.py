"""
Web 专属请求分发层模块
======================

本模块实现 WebApiDispatcher，专门处理所有 ``web_*`` 前缀的前端请求类型。
与 ws_host.WebBackendHost 的 terminal 共用路径（submit_line/apply_select_command 等）
隔离，避免 web 端操作与 terminal 端命令流程相互干扰。

设计要点：
    - 持有 host 引用（共享 bundle、emit、状态锁等基础设施）
    - 每类 web_* 请求对应一个 handle_* 方法，保持单一职责
    - 设置类写入复用统一的 _apply_setting 私有函数（DRY）
    - 不经过 handle_line，避免触发 transcript_item/hook reload 等 terminal 副作用

类说明：
    - WebApiDispatcher: Web 专属请求分发器

使用示例：
    >>> dispatcher = WebApiDispatcher(host)
    >>> await dispatcher.handle(request)
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from illusion.commands.registry import create_default_command_registry
from illusion.commands.session import resume_handler as _resume_handler
from illusion.commands.types import CommandContext, CommandResult
from illusion.config.settings import (
    load_settings as _load_settings,
)
from illusion.config.settings import (
    save_settings as _save_settings,
)
from illusion.permissions import PermissionMode
from illusion.services.file_history import (
    cleanup_file_history as _cleanup_file_history,
)
from illusion.services.session_storage import (
    delete_session_by_id as _delete_session_by_id,
)
from illusion.services.session_storage import (
    list_session_snapshots as _list_session_snapshots,
)

# @ 提及补全候选收集（terminal 与 web 共享，见 illusion.ui.file_mentions）
from illusion.ui.file_mentions import (
    file_mention_candidates as _file_mention_candidates,
)
from illusion.ui.file_mentions import (
    normalize_mention_query as _normalize_mention_query,
)
from illusion.ui.file_mentions import (
    skill_mention_candidates as _skill_mention_candidates,
)
from illusion.ui.file_mentions import (
    tree_entry_visible as _tree_entry_visible,
)
from illusion.ui.protocol import BackendEvent, FrontendRequest
from illusion.ui.runtime import RuntimeBundle, build_session_engine
from illusion.ui.web.session_runtime import SessionRuntime

if TYPE_CHECKING:
    from illusion.ui.web.ws_host import WebBackendHost


def build_replay_items(replay_messages: list[Any] | None) -> list[dict[str, Any]]:
    """将重放消息转换为 TranscriptItem 载荷列表。

    供 WebApiDispatcher.handle_web_restore_session 和
    ws_host.WebBackendHost._restore_session 共用，消除重复逻辑。

    Args:
        replay_messages: ConversationMessage 列表（可能为 None）

    Returns:
        list[Any]: 转录项字典列表（role/text/reasoning/tool_name 等）
    """
    if not replay_messages:
        return []
    from illusion.engine.messages import ToolResultBlock, ToolUseBlock
    from illusion.goal.prompts import is_goal_system_message
    from illusion.tasks.types import is_task_notification
    items: list[dict[str, Any]] = []
    # 保存 tool_use_id -> tool_name 的映射
    tool_name_map: dict[str, str] = {}
    for msg in replay_messages:
        if msg.role == "user":
            # 跳过后台任务完成通知与 goal harness 注入消息：仅注入 LLM，
            # 不参与前端重放渲染
            if msg.text.strip() and not is_task_notification(msg.text) and not is_goal_system_message(msg.text):
                items.append({"role": "user", "text": msg.text})
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    items.append({
                        "role": "tool_result",
                        "text": block.text_content,
                        "tool_use_id": block.tool_use_id,
                        "tool_name": tool_name_map.get(block.tool_use_id, "tool"),
                        "is_error": block.is_error,
                    })
        elif msg.role == "assistant":
            reasoning = msg.thinking_text.strip()
            assistant_text = msg.text.strip()
            has_tool_use = any(isinstance(b, ToolUseBlock) for b in msg.content)
            if has_tool_use:
                # 保留 reasoning 与工具前导 text（原实现 text 置空，
                # 恢复会话后工具前导 text 丢失）；先 assistant 后 tool，
                # 与 runtime.py 重放顺序及直播时序一致
                if reasoning or assistant_text:
                    item: dict[str, Any] = {"role": "assistant", "text": assistant_text}
                    if reasoning:
                        item["reasoning"] = reasoning
                    items.append(item)
                # 添加工具调用信息
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_name_map[block.id] = block.name
                        items.append({
                            "role": "tool",
                            "text": block.name,
                            "tool_name": block.name,
                            "tool_input": block.input,
                            "tool_use_id": block.id,
                        })
            elif assistant_text or reasoning:
                items.append({
                    "role": "assistant",
                    "text": assistant_text,
                    **({"reasoning": reasoning} if reasoning else {}),
                })
    return items

log = logging.getLogger(__name__)

# === 长会话分页恢复（左侧轮次导航数据源）===

# 恢复会话时下发最近 N 轮（更早轮次由 web_request_history 按页拉取）
RESTORE_PAGE_TURNS = 10
# 单页历史轮次拉取量（"加载更多"每次一页）
HISTORY_PAGE_TURNS = 5
_OUTLINE_PROMPT_LIMIT = 80
_OUTLINE_RESPONSE_LIMIT = 120


def _clip_preview(text: str, limit: int) -> str:
    """折叠空白并截断到预览预算（超长补省略号）。"""
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)] + "…"


def build_turn_outline(replay_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从全量重放条目构建轮次大纲（含未载入轮次的预览）。

    轮界与前端 useStableTurns 一致：user 条目开新轮。prompt 取该轮
    首条用户消息，response 取该轮最后一条有文本的助手回复（流式期间
    前端用本地已载入数据覆盖后缀，大纲只承担"未载入前缀"的预览）。

    Args:
        replay_items: build_replay_items 的输出（已过滤命令/通知消息）

    Returns:
        list[dict[str, Any]]: [{turn, prompt, response}]（turn 从 1 起）
    """
    entries: list[dict[str, Any]] = []
    for item in replay_items:
        role = item.get("role")
        if role == "user":
            entries.append({
                "turn": len(entries) + 1,
                "prompt": _clip_preview(str(item.get("text") or ""), _OUTLINE_PROMPT_LIMIT),
                "response": "",
            })
        elif role == "assistant" and entries:
            text = str(item.get("text") or "").strip()
            if text:
                entries[-1]["response"] = _clip_preview(text, _OUTLINE_RESPONSE_LIMIT)
    return entries


def slice_replay_items_by_turns(
    replay_items: list[dict[str, Any]], first_turn: int, last_turn: int
) -> list[dict[str, Any]]:
    """按轮号区间（1-based，闭区间）切片重放条目。

    user 条目开新轮并归属新轮；切片不会把一轮从中间切开。

    Args:
        replay_items: 全量重放条目
        first_turn: 起始轮号（含）
        last_turn: 结束轮号（含）

    Returns:
        list[dict[str, Any]]: 区间内的条目（保持原顺序）
    """
    if last_turn < first_turn:
        return []
    turns = 0
    out: list[dict[str, Any]] = []
    for item in replay_items:
        if item.get("role") == "user":
            turns += 1
            if turns > last_turn:
                break
        if first_turn <= turns:
            out.append(item)
    return out


def paginate_replay_page(
    replay_items: list[dict[str, Any]], page_turns: int = RESTORE_PAGE_TURNS
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """计算恢复下发的最近一页：返回 (全量大纲, 页内条目, 已载入最小轮号)。

    Args:
        replay_items: 全量重放条目
        page_turns: 页大小（轮数）

    Returns:
        tuple[list, list, int]: (turn_outline, page_items, first_loaded_turn)
    """
    outline = build_turn_outline(replay_items)
    total = len(outline)
    first = max(1, total - page_turns + 1)
    page = slice_replay_items_by_turns(replay_items, first, total)
    return outline, page, first


class WebApiDispatcher:
    """Web 专属请求分发器。

    处理所有 ``web_*`` 前缀的前端请求。持有 host 引用以复用 bundle、
    emit 写锁、状态快照等基础设施，但请求处理逻辑独立于 terminal 路径。

    Attributes:
        _host: WebBackendHost 实例（提供 bundle/emit/_busy 等访问）
    """

    def __init__(self, host: WebBackendHost) -> None:
        """初始化分发器。

        Args:
            host: WebBackendHost 实例
        """
        self._host = host

    async def handle(self, request: FrontendRequest) -> None:
        """分发 web_* 请求到对应的 handle_* 方法。

        所有 handler 异常在此隔离，转译为 error 事件发送给前端，不向主循环冒泡——
        否则任一 web_* 处理异常都会拖垮整个 WebSocket host（表现为后续 emit 报
        "WebSocket write error"）。

        Args:
            request: 前端请求（type 以 web_ 开头）
        """
        handler = self._dispatch_table().get(request.type)
        if handler is None:
            await self._emit(BackendEvent(
                type="error",
                message=f"未实现的 web 请求类型: {request.type}",
            ))
            return
        try:
            await handler(request)
        except Exception as exc:
            log.exception("处理 web 请求 %s 时发生异常", request.type)
            # 异常隔离：发 error 事件而非冒泡，避免拖垮 host
            try:
                await self._emit(BackendEvent(
                    type="error",
                    message=f"处理 {request.type} 失败: {exc}",
                ))
                # 尝试恢复 busy 态，避免前端卡在 loading
                await self._emit(BackendEvent(type="line_complete"))
            except Exception:
                # 连发 error 都失败时只能记录，不再冒泡
                log.exception("发送 web 异常 error 事件也失败")

    def _dispatch_table(self) -> dict[str, Callable[[FrontendRequest], Awaitable[None]]]:
        """返回请求类型到处理方法的映射表。

        Returns:
            dict[str, Any]: {请求类型字符串: 异步处理方法}
        """
        return {
            "web_new_session": self.handle_web_new_session,
            "web_restore_session": self.handle_web_restore_session,
            "web_fork_session": self.handle_web_fork_session,
            "web_request_history": self.handle_web_request_history,
            "web_delete_sessions": self.handle_web_delete_sessions,
            "web_set_setting": self.handle_web_set_setting,
            "web_request_sessions": self.handle_web_request_sessions,
            "web_request_models": self.handle_web_request_models,
            "web_refresh_status": self.handle_web_refresh_status,
            "web_request_resources": self.handle_web_request_resources,
            "web_request_file_tree": self.handle_web_request_file_tree,
            "web_request_git_status": self.handle_web_request_git_status,
            "web_read_file": self.handle_web_read_file,
            "web_file_diff": self.handle_web_file_diff,
            "web_request_agent_tasks": self.handle_web_request_agent_tasks,
            "web_request_session_files": self.handle_web_request_session_files,
            "web_read_session_file": self.handle_web_read_session_file,
            "web_request_file_mentions": self.handle_web_request_file_mentions,
            "web_query": self.handle_web_query,
            "web_request_workspaces": self.handle_web_request_workspaces,
            "web_add_workspace": self.handle_web_add_workspace,
            "web_remove_workspace": self.handle_web_remove_workspace,
            # agent 管理（web 设置表单 AgentsTab）
            "web_request_agents": self.handle_web_request_agents,
            "web_update_agent": self.handle_web_update_agent,
            "web_delete_agent": self.handle_web_delete_agent,
        }

    # === emit 辅助：委托给 host ===
    async def _emit(self, event: BackendEvent, *, session_id: str | None = None) -> None:
        """通过 host 发送后端事件。

        Args:
            event: 要发送的后端事件
            session_id: 可选：标记事件归属会话（前端按此路由到会话视图）
        """
        await self._host._emit(event, session_id=session_id)

    # === 以下方法在后续 Task 中实现，骨架阶段先返回 error 占位 ===

    async def handle_web_new_session(self, request: FrontendRequest) -> None:
        """新建会话（多会话并发，多工作区支持）。

        创建全新的会话运行时（独立引擎 + 独立 CheckpointStore），
        设为活跃会话。目标工作区由 request.cwd 指定（缺省默认工作区）；
        工作区 bundle 未构建时懒构建（按该目录的项目级配置初始化）。
        旧会话的运行时与行任务不受影响，可继续运行。

        Args:
            request: 前端请求（cwd 可选：目标工作区目录）
        """
        host = self._host
        bundle = host._bundle
        if bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        try:
            session = await host._create_session(request.cwd)
        except Exception as exc:
            log.exception("新建会话失败: cwd=%s", request.cwd)
            await self._emit(BackendEvent(type="error", message=f"新建会话失败: {exc}"))
            return
        host._set_active_session(session.session_id)
        # 发送空 transcript 的恢复完成事件，前端据此切换到新会话视图
        await self._emit(BackendEvent(
            type="web_restore_completed",
            session_id=session.session_id,
            items=[],
            state=host._session_state_payload(session),
        ), session_id=session.session_id)
        await host._push_sessions()
        # 新会话所在工作区的资源/模型选项随激活同步刷新（右栏配置联动）
        await self._push_resources(session.bundle)
        await self._push_models(session.bundle)
        await self._emit(host._status_snapshot())

    async def handle_web_restore_session(self, request: FrontendRequest) -> None:
        """恢复指定会话（多会话并发，跨工作区）。

        直接调用 resume_handler，不经过 handle_line，避免触发
        select_request/command_result/transcript_item 等 terminal 副作用。
        通过 web_restore_started/completed 显式标注，前端据此显示加载动画。

        内存中已有运行时（前端切走但运行时保留）→ 直接从引擎重建转录，
        不触碰磁盘；否则创建独立引擎并载入会话历史。恢复操作只影响目标
        会话，其他会话的运行时与行任务不受影响。

        多工作区：request.cwd 指定会话所属目录（缺省时按内存/注册表扫描
        定位），目标工作区 bundle 懒构建。

        每个 emit 调用前检查 _ws_closed——WebSocket 已关闭时 _emit 静默返回
        不抛异常，导致 handle() 的 try/except 不触发，前端永远收不到
        web_restore_completed，restoringSessionId 不被清除，页面白屏。

        Args:
            request: 前端请求（session_id 必填，cwd 可选：会话所属工作区）
        """
        host = self._host
        bundle = host._bundle
        if bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        session_id = request.session_id or ""

        # WebSocket 已关闭：直接返回，不尝试 emit
        if host._ws_closed:
            return

        error_msg = None
        replay_items = []
        session = host._sessions.get(session_id)

        # 1. 发送恢复开始事件（前端据此显示动画）
        await self._emit(BackendEvent(type="web_restore_started", session_id=session_id),
                         session_id=session_id)

        # 2. 运行时已存在（内存会话）：直接重建转录，无需读盘
        if session is not None:
            try:
                replay_items = build_replay_items(session.engine.messages)
            except Exception as exc:
                # 罕见：引擎消息结构异常时仍发 completed（带错误），
                # 避免前端 restoring 加载动画永久挂起
                log.exception("重建内存会话 %s 转录失败", session_id)
                error_msg = str(exc)
        else:
            # 2'. 运行时不存在：定位所属工作区并创建独立引擎载入会话历史
            try:
                session = await self._materialize_session(session_id, request.cwd)
                replay_items = build_replay_items(session.engine.messages)
            except Exception as exc:
                log.exception("恢复会话 %s 失败", session_id)
                error_msg = str(exc)
                session = None

        # 3. 恢复成功（或已存在）：设为活跃会话
        if error_msg is None and session is not None:
            host._set_active_session(session.session_id)
            session_id = session.session_id
            host._refresh_session_display(session)

        # 4. WebSocket 在恢复过程中关闭：跳过 emit，直接返回
        if host._ws_closed:
            return

        # 4.5 长会话分页：只下发最近 RESTORE_PAGE_TURNS 轮，更早轮次由
        # web_request_history 按页拉取；全量轻量轮次大纲随事件下发，
        # 供前端左侧轮次导航预览与跳转"尚未载入"的轮次
        outline: list[dict[str, Any]] = []
        page_items = replay_items
        first_loaded_turn = 1
        if error_msg is None:
            outline, page_items, first_loaded_turn = paginate_replay_page(replay_items)

        # 5. 始终发 web_restore_completed——前端据此清除 restoringSessionId
        await self._emit(BackendEvent(
            type="web_restore_completed",
            session_id=session_id,
            items=page_items,  # type: ignore[arg-type]
            state=host._session_state_payload(session) if session is not None else {},
            web_error=error_msg,
            turn_outline=outline,
            first_loaded_turn=first_loaded_turn,
            total_turns=len(outline),
        ), session_id=session_id)
        # 6. 推送会话列表刷新
        await host._push_sessions()
        # 7. 活跃工作区资源联动刷新（右栏项目配置随目录切换）
        if error_msg is None and session is not None:
            await self._push_resources(session.bundle)
            await self._push_models(session.bundle)
        # 8. 发送任务快照与状态快照
        from illusion.tasks import get_task_manager
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
        await self._emit(self._host._status_snapshot())

    # _build_replay_items 已提取为模块级函数 build_replay_items()，供本类和 ws_host 共用

    async def _materialize_session(
        self, session_id: str, cwd: str | None
    ) -> SessionRuntime:
        """为磁盘会话创建独立引擎运行时并载入历史（恢复/fork/历史分页共用）。

        定位所属工作区（cwd 缺省时按内存/注册表扫描）→ 懒构建工作区
        bundle → 构建引擎 → resume_handler 载入全量历史。失败时清理
        半成品运行时（pop + 关引擎）后向上抛出，由调用方决定错误呈现。

        Args:
            session_id: 目标会话 ID
            cwd: 会话所属工作区目录（可选）

        Returns:
            SessionRuntime: 已载入历史的会话运行时
        """
        host = self._host
        target_cwd = cwd or host._locate_session_workspace(session_id)
        if target_cwd is None:
            raise FileNotFoundError(f"Session not found: {session_id}")
        from illusion.services.session_storage import read_meta as _read_meta

        if not _read_meta(target_cwd, session_id):
            raise FileNotFoundError(f"Session not found: {session_id}")
        ws_bundle = await host._get_or_build_bundle(target_cwd)
        engine = build_session_engine(
            ws_bundle,
            session_id,
            permission_prompt=host._make_permission_prompt(session_id),
            ask_user_prompt=host._make_ask_user_prompt(session_id),
            plan_approval_prompt=host._make_plan_approval_prompt(session_id),
        )
        from illusion.ui.runtime import build_session_bundle
        session = SessionRuntime(
            session_id=session_id,
            bundle=build_session_bundle(ws_bundle, session_id, engine),
            workspace_cwd=ws_bundle.cwd,
        )
        host._sessions[session_id] = session
        host._maybe_evict_sessions()
        context = CommandContext(
            engine=session.engine,
            hooks_summary=session.bundle.hook_summary(),
            mcp_summary=session.bundle.mcp_summary(),
            plugin_summary=session.bundle.plugin_summary(),
            cwd=session.bundle.cwd,
            tool_registry=session.bundle.tool_registry,
            app_state=session.bundle.app_state,
            session_id=session_id,
        )
        try:
            result = await _resume_handler(session_id, context)
        except Exception:
            # 载入失败：释放刚注册的运行时（关引擎），向上抛出
            host._sessions.pop(session_id, None)
            try:
                await session.engine.aclose()
            except Exception:
                log.exception("关闭恢复失败的会话 %s 引擎出错", session_id)
            raise
        if result.restored_session_id and result.restored_session_id != session_id:
            # resume_handler 可能规范化会话 id（如 # 轮次引用）：
            # 同步注册表 key，避免 dict key 与 session_id 不一致
            # 导致后续请求按新 id 路由时找不到运行时
            host._sessions.pop(session_id, None)
            session.session_id = result.restored_session_id
            session.bundle.session_id = result.restored_session_id
            host._sessions[session.session_id] = session
        return session

    async def handle_web_fork_session(self, request: FrontendRequest) -> None:
        """分叉会话：复制源会话（可截断到前 N 轮）并切换到新会话。

        源会话的运行时与磁盘内容保持原样（fork 只读取）；运行中的
        会话（busy）拒绝分叉——最后一轮尚未落盘，副本会缺最新内容。
        新会话物化后设为活跃，按分页恢复的同一格式推送
        web_restore_completed（前端据此切换视图），并刷新会话列表。

        Args:
            request: 前端请求（session_id 缺省为活跃会话；turns 可选：
                保留前 N 轮；cwd 可选：源会话所属工作区）
        """
        from illusion.services.session_storage import fork_session as _fork_session

        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        source_sid = request.session_id or host._active_session_id
        if not source_sid:
            await self._emit(BackendEvent(type="error", message="缺少源会话 ID"))
            return
        src_session = host._sessions.get(source_sid)
        if src_session is not None and src_session.busy:
            await self._emit(BackendEvent(
                type="error", message="会话正在运行任务，无法分叉（请等待任务完成）"))
            return
        cwd = request.cwd or (
            src_session.bundle.cwd if src_session is not None
            else host._locate_session_workspace(source_sid)
        )
        if cwd is None:
            await self._emit(BackendEvent(type="error", message=f"Session not found: {source_sid}"))
            return
        turns = request.turns
        if turns is not None:
            turns = max(1, int(turns))
        try:
            new_sid = await asyncio.to_thread(_fork_session, cwd, source_sid, turns)
        except Exception as exc:
            log.exception("分叉会话 %s 失败", source_sid)
            await self._emit(BackendEvent(type="error", message=f"分叉会话失败: {exc}"))
            return
        if not new_sid:
            await self._emit(BackendEvent(type="error", message=f"Session not found: {source_sid}"))
            return

        # 物化新会话并激活（物化失败时保持源会话不动）
        try:
            session = await self._materialize_session(new_sid, cwd)
        except Exception as exc:
            log.exception("载入分叉会话 %s 失败", new_sid)
            await self._emit(BackendEvent(type="error", message=f"分叉会话失败: {exc}"))
            return
        host._set_active_session(session.session_id)
        host._refresh_session_display(session)
        sid = session.session_id

        replay_items = build_replay_items(session.engine.messages)
        outline, page_items, first_turn = paginate_replay_page(replay_items)
        await self._emit(BackendEvent(
            type="web_restore_completed",
            session_id=sid,
            items=page_items,  # type: ignore[arg-type]
            state=host._session_state_payload(session),
            turn_outline=outline,
            first_loaded_turn=first_turn,
            total_turns=len(outline),
        ), session_id=sid)
        await host._push_sessions()
        await self._push_resources(session.bundle)
        await self._push_models(session.bundle)
        from illusion.tasks import get_task_manager
        await self._emit(BackendEvent.tasks_snapshot(get_task_manager().list_tasks()))
        await self._emit(self._host._status_snapshot())

    async def handle_web_request_history(self, request: FrontendRequest) -> None:
        """加载更早的轮次分页并推送 web_history 事件（长会话导航跳转数据源）。

        请求携带 before_turn（前端当前已载入的最小轮号，1-based），
        返回紧邻其前的一页（HISTORY_PAGE_TURNS 轮）。数据源优先内存
        运行时；已被淘汰的会话先物化（全量载入后切片）。

        Args:
            request: 前端请求（before_turn 必填；session_id 缺省为活跃会话；
                cwd 可选：会话所属工作区）
        """
        host = self._host
        if host._bundle is None:
            return
        session_id = request.session_id or host._active_session_id
        session = host._sessions.get(session_id) if session_id else None
        if session is None and session_id:
            try:
                session = await self._materialize_session(session_id, request.cwd)
            except Exception as exc:
                log.exception("物化会话 %s 以加载历史失败", session_id)
                await self._emit(BackendEvent(
                    type="web_history", session_id=session_id,
                    web_history={"items": [], "first_turn": None, "has_more": False,
                                 "total_turns": 0, "error": str(exc)},
                ), session_id=session_id)
                return
        if session is None:
            await self._emit(BackendEvent(
                type="web_history", session_id=None,
                web_history={"items": [], "first_turn": None, "has_more": False,
                             "total_turns": 0, "error": "session_not_found"},
            ))
            return

        sid = session.session_id
        replay_items = build_replay_items(session.engine.messages)
        outline = build_turn_outline(replay_items)
        total = len(outline)
        before = request.before_turn or (total + 1)
        page_last = max(0, before - 1)
        page_first = max(1, page_last - HISTORY_PAGE_TURNS + 1)
        items = slice_replay_items_by_turns(replay_items, page_first, page_last)
        await self._emit(BackendEvent(
            type="web_history",
            session_id=sid,
            web_history={
                "items": items,
                "first_turn": page_first if items else None,
                "has_more": page_first > 1,
                "total_turns": total,
            },
        ), session_id=sid)

    async def handle_web_delete_sessions(self, request: FrontendRequest) -> None:
        """批量删除会话。

        支持指定 session_ids 列表或 delete_all 删除全部。
        当删除当前会话或全部会话时，后端原子化地新建一个空会话并推送
        web_restore_completed，避免前端"先删后建"两阶段逻辑的竞态
        （delete_all 会误删刚建的新会话，导致状态不一致）。

        Args:
            request: 前端请求（session_ids 或 delete_all）
        """
        host = self._host
        bundle = host._bundle
        if bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        deleted_ids: set[str] = set()
        # 运行中的会话（busy）不删除：整组删除/清除全部时保留进行中的任务，
        # 仅清理其余会话
        busy_ids = {sr.session_id for sr in host._sessions.values() if sr.busy}
        # 删除范围的目标工作区：优先请求携带 cwd（delete_all 限定该目录），
        # 缺省回退活跃会话所属目录，再回退默认工作区
        active_session = host._active_session()
        scope_cwd = host._resolve_workspace_cwd(
            request.cwd or (active_session.bundle.cwd if active_session is not None else None)
        )
        if request.delete_all:
            # delete_all 限定在单个工作区内（多目录空间下互不影响）
            sessions = await asyncio.to_thread(_list_session_snapshots, scope_cwd, 1000)
            sessions = [s for s in sessions if s["session_id"] not in busy_ids]
            # 并行删除：每个 _delete_session_by_id 是同步文件 I/O，用 to_thread 隔离，
            # return_exceptions=True 吞掉单个删除失败，避免一次失败导致整批回滚
            await asyncio.gather(
                *(
                    asyncio.to_thread(_delete_session_by_id, scope_cwd, s["session_id"])
                    for s in sessions
                ),
                return_exceptions=True,
            )
            deleted_ids = {s["session_id"] for s in sessions}
            # 按本工作区会话逐个清理文件历史备份（file-history 独立于会话目录树，
            # 需显式删除；不能调 _cleanup_all_file_histories——它会清掉所有
            # 工作区的撤销/恢复历史，误伤其他目录）
            await asyncio.gather(
                *(asyncio.to_thread(_cleanup_file_history, sid) for sid in deleted_ids),
                return_exceptions=True,
            )
        elif request.session_ids:
            # 运行中的会话跳过，仅删除其余
            target_ids = [sid for sid in request.session_ids if sid not in busy_ids]
            if not target_ids:
                deleted_ids = set()
            else:
                # 逐会话定位所属工作区（跨目录删除时各归各区）；
                # 定位涉及磁盘 meta 读取，移入线程池避免阻塞事件循环。
                # 线程内使用快照：_sessions/_workspaces 会被事件循环并发修改
                # （materialize/registry 同步），直接迭代可能在遍历中抛 RuntimeError。
                def _locate_all(sids: list[str]) -> dict[str, str]:
                    from illusion.services.session_storage import read_meta

                    sessions_snapshot = dict(host._sessions)
                    workspaces_snapshot = list(host._workspaces.values())
                    result: dict[str, str] = {}
                    for sid in sids:
                        session = sessions_snapshot.get(sid)
                        if session is not None:
                            result[sid] = session.bundle.cwd
                            continue
                        for state in workspaces_snapshot:
                            if read_meta(state.cwd, sid):
                                result[sid] = state.cwd
                                break
                        result.setdefault(sid, scope_cwd)
                    return result

                sid_cwds: dict[str, str] = await asyncio.to_thread(
                    _locate_all, target_ids
                )
                await asyncio.gather(
                    *(
                        asyncio.to_thread(_delete_session_by_id, sid_cwds[sid], sid)
                        for sid in target_ids
                    ),
                    return_exceptions=True,
                )
                deleted_ids = set(target_ids)
                # 逐个清理对应会话的文件历史备份目录（独立于会话目录树，需显式删除）
                await asyncio.gather(
                    *(asyncio.to_thread(_cleanup_file_history, sid) for sid in target_ids),
                    return_exceptions=True,
                )
        # 释放被删会话的运行时（取消行任务、关闭引擎）；若删除了活跃会话，
        # 后端原子化地新建一个空会话并推送 web_restore_completed（空转录），
        # 使前端主区域即时进入新会话，无需前端编排两阶段删除。
        # 新会话建在被删活跃会话的同目录（保持用户所在工作区不跳走）。
        active_deleted = host._active_session_id in deleted_ids
        fallback_cwd = scope_cwd if active_session is None else active_session.bundle.cwd
        for sid in list(deleted_ids):
            if sid in host._sessions:
                await host._dispose_session(sid)
        if active_deleted:
            session = await host._create_session(fallback_cwd)
            host._set_active_session(session.session_id)
            await self._emit(BackendEvent(
                type="web_restore_completed",
                session_id=session.session_id,
                items=[],
                state=host._session_state_payload(session),
            ), session_id=session.session_id)
        # 删除后推送刷新的会话列表与状态快照
        await host._push_sessions()
        await self._emit(host._status_snapshot())

    async def handle_web_set_setting(self, request: FrontendRequest) -> None:
        """统一设置标量（A 通道：工具栏/会话控件触发）。

        复用 _apply_setting 私有函数，
        设置成功后发送 web_setting_changed + state_snapshot 强同步事件。
        若 key == model 额外发送 web_models 推送。

        多工作区：设置写入全局 settings.json；app_state/引擎侧变更同步到
        所有已构建工作区 bundle（各 bundle 的 app_state 独立）。

        Args:
            request: 前端请求（setting_key/setting_value 必填）
        """
        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        bundle = host._active_bundle() or host._bundle
        key = request.setting_key or ""
        value = request.setting_value
        ok, error = await self._apply_setting(bundle, key, value)
        if not ok:
            await self._emit(BackendEvent(type="error", message=error or f"设置 {key} 失败"))
            return
        # 全局标量同步到所有工作区 bundle 的 app_state（语言等 UI 态）
        if key in ("ui_language", "context_window", "effort", "permission_mode"):
            for ws_bundle in host._workspace_bundles():
                if ws_bundle is not bundle:
                    try:
                        ws_bundle.app_state.set(**{key: value})
                    except Exception:
                        log.exception("同步设置 %s 到工作区 %s 失败", key, ws_bundle.cwd)
        # 1. 发送单项变更事件（前端工具栏即时更新）
        await self._emit(BackendEvent(
            type="web_setting_changed",
            setting_key=key,
            setting_value=value,
        ))
        # 2. 发送完整状态快照（兜底，保证派生字段一致）
        await self._emit(self._host._status_snapshot())
        # 3. 若是 model 切换：先推送模型选项让 UI 即时更新 active 态，
        #    再重建 API 客户端（重建可能耗时，如 copilot token 刷新，放最后避免阻塞 UI）
        if key == "model":
            await self._push_models(bundle)
            try:
                from illusion.ui.runtime import _rebuild_api_client
                new_settings = _load_settings()
                # 多工作区：每个已构建 bundle 各自重建客户端并同步其会话引擎
                for ws_bundle in host._workspace_bundles():
                    _rebuild_api_client(ws_bundle, new_settings)
                    new_client = ws_bundle.api_client

                    def _sync_engine(e: Any, _client: Any = new_client) -> None:
                        e.set_api_client(_client)
                        e.set_model(new_settings.active_model_name)
                    self._apply_engine_setting_for_bundle(ws_bundle, _sync_engine)
            except Exception as exc:
                log.exception("重建 API 客户端失败")
                await self._emit(BackendEvent(
                    type="error",
                    message=f"模型已切换但 API 客户端重建失败: {exc}",
                ))

    def _apply_engine_setting(self, fn: Callable[[Any], None]) -> None:
        """将引擎级设置应用到所有会话引擎（含各工作区初始引擎）。

        多会话架构下每个会话持有独立引擎；设置类变更（权限检查器/模型/
        effort）必须广播到全部引擎，否则只对初始会话生效——例如切换到
        计划模式后，其他已物化会话仍持有旧的 PermissionChecker，权限
        限制被绕过（安全相关）。

        Args:
            fn: 对单个引擎执行的设置回调
        """
        host = self._host
        seen: set[int] = set()
        for ws_bundle in host._workspace_bundles():
            fn(ws_bundle.engine)
            seen.add(id(ws_bundle.engine))
        for session in host._sessions.values():
            if id(session.engine) not in seen:
                fn(session.engine)
                seen.add(id(session.engine))

    def _apply_engine_setting_for_bundle(
        self, bundle: RuntimeBundle, fn: Callable[[Any], None]
    ) -> None:
        """将引擎级设置应用到单个工作区 bundle 的初始引擎及其会话引擎。"""
        # 延迟导入避免与 ws_host（模块级导入 build_replay_items）循环导入
        from illusion.ui.web.ws_host import _cwd_key as _ws_cwd_key

        host = self._host
        fn(bundle.engine)
        bundle_key = _ws_cwd_key(bundle.cwd)
        for session in host._sessions.values():
            if _ws_cwd_key(session.bundle.cwd) == bundle_key:
                fn(session.engine)

    async def _apply_setting(self, bundle: RuntimeBundle, key: str, value: Any) -> tuple[bool, str | None]:
        """应用设置到 settings 与 app_state（A/B 通道共用）。

        复用各设置项的写入模式：settings.<field> = value → save_settings →
        app_state.set。model 跨 env 切换时重建 API 客户端。

        Args:
            bundle: 运行时 bundle
            key: 设置键名（effort/permission_mode/model/context_window/
                 ui_language/turns）
            value: 设置值

        Returns:
            tuple[bool, str | None]: (是否成功, 错误消息)
        """
        settings = _load_settings()
        # 键名 → app_state 字段名映射（settings 字段名可能与 key 不同）
        if key not in (
            "effort", "permission_mode", "model", "context_window",
            "ui_language", "turns",
        ):
            return False, f"不支持的设置键: {key}"

        try:
            if key == "permission_mode":
                # PermissionMode 是枚举，必须整体赋值（.value 只读，不能直接设）
                settings.permission.mode = PermissionMode(str(value))
                _save_settings(settings)
                bundle.app_state.set(permission_mode=settings.permission.mode.value)
                # 更新所有会话引擎的权限检查器——引擎初始化时创建的 PermissionChecker
                # 持有旧的 PermissionSettings 引用，必须重建并注入，否则计划模式等
                # 权限限制不生效（多会话下仅更新初始引擎会绕过其他会话的权限限制）。
                # 多工作区：沙箱限制按引擎所属工作区目录分别锚定
                from illusion.permissions import PermissionChecker

                def _rebuild_checker(e: Any) -> None:
                    engine_cwd = getattr(e, "_cwd", None)
                    anchor = str(engine_cwd) if engine_cwd else (settings.working_directory or bundle.cwd)
                    checker = PermissionChecker(settings.permission)
                    checker.sync_sandbox_restrictions(settings.sandbox, working_directory=anchor)
                    e.set_permission_checker(checker)

                self._apply_engine_setting(_rebuild_checker)
            elif key == "turns":
                # turns: unlimited → None，否则 int；影响 engine.max_turns
                turns_val: int | None
                if str(value) == "unlimited":
                    turns_val = None
                else:
                    turns_val = int(value)
                bundle.engine.set_max_turns(turns_val)
            elif key == "model":
                settings.model = str(value)
                _save_settings(settings)
                bundle.app_state.set(model=str(value))
                # 修复：同步更新 settings_overrides，避免 current_settings() 返回缓存的旧值
                bundle.settings_overrides["model"] = str(value)
                # 注：API 客户端重建（_rebuild_api_client）延迟到 emit 之后执行，
                # 避免重建耗时（如 copilot token 刷新）阻塞前端 UI 反馈
            else:
                # effort / context_window / ui_language
                # settings 字段名与 key 相同（ui_language / context_window / effort）
                setattr(settings, key, value)
                _save_settings(settings)
                # app_state 字段名与 key 相同
                bundle.app_state.set(**{key: value})
                # 修复：同步更新 settings_overrides，避免 current_settings() 返回缓存的旧值
                if key in ("effort", "model", "max_turns", "base_url", "api_key", "api_format"):
                    bundle.settings_overrides[key] = value
                # 修复：effort 需要同步到所有会话引擎，确保后续请求使用正确的 effort 级别
                if key == "effort":
                    from illusion.api.effort import EffortLevel
                    try:
                        effort_level = EffortLevel(str(value))
                    except ValueError:
                        effort_level = None
                        log.warning("无效的 effort 值: %s", value)
                    if effort_level is not None:
                        self._apply_engine_setting(lambda e: setattr(e, "effort", effort_level))
        except Exception as exc:
            log.exception("应用设置 %s 失败", key)
            return False, f"设置 {key} 失败: {exc}"
        return True, None

    async def handle_web_request_sessions(self, request: FrontendRequest) -> None:
        """拉取会话列表并推送 web_sessions 事件。

        Args:
            request: 前端请求（limit/offset 可选）
        """
        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        await host._push_sessions()

    async def handle_web_refresh_status(self, request: FrontendRequest) -> None:
        """设置保存后的状态刷新脉冲（前端主动触发）。

        重新读取 settings.json，把 context_window/max_tokens/max_turns 热应用到
        本主机的会话引擎与 app_state，并推送全局状态快照——保证右栏上下文窗口
        与后续实际请求参数立即生效（与 REST /api/settings/model-params 的
        落盘热应用互备，双保险确保前端在保存后总能拿到最新状态）。

        Args:
            request: 前端请求（无额外载荷）
        """
        host = self._host
        if host._bundle is None:
            return
        host.apply_runtime_settings_sync()
        # apply_runtime_settings_sync 内部已推送状态快照（后台任务），无需重复推送

    async def handle_web_request_models(self, request: FrontendRequest) -> None:
        """拉取模型选项并发送 web_models 事件。

        Args:
            request: 前端请求（无额外载荷）
        """
        bundle = self._host._bundle
        if bundle is None:
            return
        await self._push_models(bundle)

    async def _push_models(self, bundle: RuntimeBundle) -> None:
        """推送模型选项列表（供多处复用）。

        复用 ws_host._model_select_options 生成选项（含 active 态）。

        Args:
            bundle: 运行时 bundle
        """
        settings = bundle.current_settings()
        current_model = settings.active_model_name
        options = self._host._model_select_options(current_model)
        await self._emit(BackendEvent(type="web_models", web_models=options))

    async def handle_web_request_resources(self, request: FrontendRequest) -> None:
        """拉取右侧栏资源快照并发送 web_resources 事件。

        多工作区：资源随目标会话/工作区切换（skills/mcp/plugins/rules
        为"用户全局 + 该目录项目级"的合并视图）。

        Args:
            request: 前端请求（session_id/cwd 可选：目标会话或工作区；
                缺省为活跃会话所在工作区）
        """
        host = self._host
        if host._bundle is None:
            return
        bundle = self._resolve_resource_bundle(request)
        await self._push_resources(bundle)

    def _resolve_resource_bundle(self, request: FrontendRequest) -> RuntimeBundle:
        """解析资源推送的目标 bundle（会话优先，其次 cwd，最后活跃/默认）。"""
        host = self._host
        assert host._bundle is not None
        if request.session_id:
            session = host._sessions.get(request.session_id)
            if session is not None:
                return session.bundle
        if request.cwd:
            bundle = host._workspace_bundle_for(request.cwd)
            if bundle is not None:
                return bundle
        return host._active_bundle() or host._bundle

    async def _push_resources(self, bundle: RuntimeBundle) -> None:
        """推送资源快照（供多处复用）。

        复用 _collect_resources 收集 skills/plugins/rules/mcp_servers，
        废弃旧的命令文本正则解析（_parseSkillsResult 等）。
        事件携带 cwd，前端据此判断资源所属工作区。

        Args:
            bundle: 运行时 bundle
        """
        resources = _collect_resources(bundle)
        await self._emit(BackendEvent(
            type="web_resources",
            web_resources=resources,
            cwd=bundle.cwd,
        ))

    # === 右栏扩展：文件树 / Git 状态 / 文件预览 ===

    async def handle_web_request_file_tree(self, request: FrontendRequest) -> None:
        """列出目标目录的一层可见条目并推送 web_file_tree 事件。

        前端树形导航按需逐层拉取（path 为工作区内相对路径，空串表示根）。
        过滤规则见 _tree_entry_visible（隐藏生成产物/缓存目录，点文件仅
        保留项目配置相关的少量目录）。单层超过 500 条时截断并标记。

        Args:
            request: 前端请求（path 可选：目标子目录；session_id/cwd 同资源解析）
        """
        host = self._host
        if host._bundle is None:
            return
        bundle = self._resolve_resource_bundle(request)
        rel = (request.path or "").strip().replace("\\", "/")
        base = _resolve_within_root(bundle.cwd, rel)
        if base is None or not base.is_dir():
            await self._emit(BackendEvent(type="error", message="目录路径无效或超出工作区范围"))
            return
        entries, truncated = await asyncio.to_thread(_list_dir_entries, base, bundle.cwd)
        await self._emit(BackendEvent(
            type="web_file_tree",
            cwd=bundle.cwd,
            web_file_tree={"path": rel, "entries": entries, "truncated": truncated},
        ))

    async def handle_web_request_git_status(self, request: FrontendRequest) -> None:
        """采集目标工作区的 Git 状态快照并推送 web_git_status 事件。

        快照包含分支/上游/领先落后计数与变更文件列表
        （porcelain -z -uall + numstat，行级增删统计；非 Git 目录返回
        is_repo=False，前端隐藏区块）。

        Args:
            request: 前端请求（session_id/cwd 同资源解析）
        """
        host = self._host
        if host._bundle is None:
            return
        bundle = self._resolve_resource_bundle(request)
        snapshot = await asyncio.to_thread(_git_status_snapshot, bundle.cwd)
        await self._emit(BackendEvent(
            type="web_git_status",
            cwd=bundle.cwd,
            web_git_status=snapshot,
        ))

    async def handle_web_read_file(self, request: FrontendRequest) -> None:
        """读取工作区内文本文件内容并推送 web_file_content 事件（预览）。

        安全与限制：路径解析限定在工作区内（拒绝 ../ 穿越）；二进制
        （前 8KB 含 NUL）不返回内容；超过 512KB / 4000 行截断并标记。

        Args:
            request: 前端请求（path 必填：工作区内相对路径）
        """
        host = self._host
        if host._bundle is None:
            return
        bundle = self._resolve_resource_bundle(request)
        # 请求原串用于回显：前端以「content|path」精确关联响应与请求，
        # path 须与发起时字符串一致，否则载荷被丢弃导致永久加载中
        requested = (request.path or "").strip()
        rel = requested.replace("\\", "/")
        target = _resolve_within_root(bundle.cwd, rel)
        if target is None or not target.is_file():
            await self._emit(BackendEvent(
                type="web_file_content", cwd=bundle.cwd,
                web_file_content={"path": requested, "error": "文件不存在或超出工作区范围"},
            ))
            return
        payload = await asyncio.to_thread(_read_file_payload, target, rel)
        # 回显请求原串（内部 rel 仅作读取参数），保证前端 key 关联命中
        if isinstance(payload, dict):
            payload = {**payload, "path": requested}
        await self._emit(BackendEvent(
            type="web_file_content",
            cwd=bundle.cwd,
            web_file_content=payload,
        ))

    async def handle_web_file_diff(self, request: FrontendRequest) -> None:
        """读取单个文件相对 HEAD 的 diff 并推送 web_file_content 事件。

        变更视图（kind=diff）：跟踪文件取 ``git diff HEAD -- <path>``
        （暂存 + 工作区合并差异）；未跟踪/新文件用 ``git diff --no-index``
        合成全新增 diff。已删除文件同样可取 diff（无需文件存在）。
        path 支持工作区内相对路径或绝对路径（绝对路径须位于工作区内，
        单轮变更条统一下发绝对路径）。

        Args:
            request: 前端请求（path 必填：工作区内相对路径或工作区内绝对路径）
        """
        host = self._host
        if host._bundle is None:
            return
        bundle = self._resolve_resource_bundle(request)
        # 请求原串用于回显：前端以「kind|path」精确关联响应与请求，
        # path 必须与发起时的字符串完全一致（含分隔符形式），否则
        # 载荷被丢弃导致预览面板永久加载中
        requested = (request.path or "").strip()
        raw = requested.replace("\\", "/")
        # 只做边界校验（不要求文件存在：已删除文件也有 diff）。
        # _resolve_within_root 对区内绝对路径同样放行（pathlib 的 root/abs
        # 语义即 abs 本身），出界/穿越返回 None，无需单独的绝对路径分支
        target = _resolve_within_root(bundle.cwd, raw)
        if target is None or not raw:
            await self._emit(BackendEvent(
                type="web_file_content", cwd=bundle.cwd,
                web_file_content={"path": requested, "kind": "diff", "error": "路径无效或超出工作区范围"},
            ))
            return
        rel_final = target.relative_to(Path(bundle.cwd).resolve()).as_posix()
        payload = await asyncio.to_thread(_git_file_diff, bundle.cwd, rel_final)
        # 回显请求原串（内部 rel 仅作 git 参数），保证前端 key 关联命中
        if isinstance(payload, dict):
            payload = {**payload, "path": requested}
        await self._emit(BackendEvent(
            type="web_file_content",
            cwd=bundle.cwd,
            web_file_content=payload,
        ))

    async def handle_web_request_agent_tasks(self, request: FrontendRequest) -> None:
        """收集会话内的智能体与后台任务并推送 web_agent_tasks 事件。

        复用 /agent 指令的双数据源（前台 agent 工具结果 + transcript 的
        task-notification），按会话隔离（切换会话时前端重新拉取）。
        行点击查看摘要同样复用 /agent（web_query command=agent args=<id>）。

        Args:
            request: 前端请求（session_id 可选：目标会话；缺省为活跃会话）
        """
        host = self._host
        if host._bundle is None:
            return
        session_id = request.session_id or host._active_session_id
        session = host._sessions.get(session_id) if session_id else None
        if session is None:
            await self._emit(BackendEvent(
                type="web_agent_tasks", session_id=session_id, web_agent_tasks=[]))
            return
        items = await asyncio.to_thread(_collect_agent_tasks, session.engine.messages)
        await self._emit(BackendEvent(
            type="web_agent_tasks", session_id=session_id, web_agent_tasks=items))

    async def handle_web_request_session_files(self, request: FrontendRequest) -> None:
        """收集会话内变更工具修改的文件并推送 web_session_files 事件。

        会话文件区块的数据源：从会话转录的 assistant 消息中提取
        edit_file/write_file 等直接修改文件的工具调用，收集其目标路径。
        该列表独立于 Git 与工作区边界：可包含未纳入 Git 追踪、项目目录
        之外、以及无 Git 环境下的文件（均可直接预览）。随会话隔离
        （切换会话 / 跨目录时前端清空并重新拉取）。

        Args:
            request: 前端请求（session_id 可选：目标会话；缺省为活跃会话）
        """
        host = self._host
        if host._bundle is None:
            return
        session_id = request.session_id or host._active_session_id
        session = host._sessions.get(session_id) if session_id else None
        if session is None:
            await self._emit(BackendEvent(
                type="web_session_files", session_id=session_id,
                cwd=host._bundle.cwd, web_session_files=[]))
            return
        files = await asyncio.to_thread(
            _collect_session_files, session.engine.messages, session.bundle.cwd)
        await self._emit(BackendEvent(
            type="web_session_files", session_id=session_id,
            cwd=session.bundle.cwd, web_session_files=files))

    async def handle_web_read_session_file(self, request: FrontendRequest) -> None:
        """读取会话内修改过的文件内容并推送 web_file_content 事件（预览）。

        会话文件可能位于工作区之外或不被 Git 追踪，不能复用限定在工作区内
        的 handle_web_read_file。安全模型：仅允许读取"当前会话确实修改过的
        文件"（由 _collect_session_files 界定），杜绝任意绝对路径读取与
        路径穿越。预览限制（二进制/大小/行数）与普通文件读取一致。

        Args:
            request: 前端请求（path 必填：会话内修改文件的路径；
                session_id 可选：目标会话）
        """
        host = self._host
        if host._bundle is None:
            return
        session_id = request.session_id or host._active_session_id
        session = host._sessions.get(session_id) if session_id else None
        if session is None:
            await self._emit(BackendEvent(
                type="web_file_content", session_id=session_id,
                cwd=host._bundle.cwd,
                # 错误码交由前端 i18n 本地化展示
                web_file_content={"path": request.path or "", "error": "session_not_found"}))
            return
        tracked = {
            f["path"]
            for f in await asyncio.to_thread(
                _collect_session_files, session.engine.messages, session.bundle.cwd)
        }
        raw = (request.path or "").strip()
        if not raw or raw not in tracked:
            # 错误码交由前端 i18n 本地化展示
            await self._emit(BackendEvent(
                type="web_file_content", session_id=session_id,
                cwd=session.bundle.cwd,
                web_file_content={"path": raw, "error": "not_in_session"}))
            return
        payload = await asyncio.to_thread(_read_session_file_payload, raw)
        await self._emit(BackendEvent(
            type="web_file_content", session_id=session_id,
            cwd=session.bundle.cwd, web_file_content=payload))

    async def handle_web_request_file_mentions(self, request: FrontendRequest) -> None:
        """收集 @ 提及补全候选并推送 web_file_mentions 事件。

        仅返回工作区内路径与技能名候选（不读内容），选中后的提及文本保持
        普通 prompt 文本，内容由模型自行调用 read 工具获取。
        安全边界与 web_request_file_tree 一致：BFS 限定在目标工作区
        根内，过滤规则复用文件树可见性。

        Args:
            request: 前端请求（query 可选：@ 后的路径片段；
                session_id/cwd 同资源解析；request_id 原样回显供前端丢弃过期响应）
        """
        host = self._host
        if host._bundle is None:
            return
        bundle = self._resolve_resource_bundle(request)
        query = _normalize_mention_query(request.query)
        candidates, truncated = await asyncio.to_thread(_file_mention_candidates, bundle.cwd, query)
        skills = await asyncio.to_thread(_skill_mention_candidates, bundle.cwd, query)
        await self._emit(BackendEvent(
            type="web_file_mentions",
            cwd=bundle.cwd,
            request_id=request.request_id,
            web_file_mentions={
                "query": query,
                "candidates": candidates,
                "skills": skills,
                "truncated": truncated,
            },
        ))

    # === 工作区（目录空间）管理 ===

    async def handle_web_request_workspaces(self, request: FrontendRequest) -> None:
        """拉取工作区列表并推送 web_workspaces 事件。"""
        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        await host._push_workspaces()

    async def handle_web_add_workspace(self, request: FrontendRequest) -> None:
        """注册一个新的目录空间。

        校验与 working_directory 设置一致（expanduser、Windows 非法字符、
        缺失目录自动创建），注册成功后同步宿主工作区状态并推送
        web_workspaces + web_sessions（新目录的磁盘会话立即可见）。

        Args:
            request: 前端请求（path 必填：目录路径）
        """
        from illusion.services import workspace_registry

        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        raw = (request.path or "").strip()
        if not raw:
            await self._emit(BackendEvent(type="error", message="目录路径不能为空"))
            return

        def _register() -> tuple[Any, str | None]:
            from illusion.cli.workspace import validate_and_normalize

            resolved, err = validate_and_normalize(raw)
            if resolved is None:
                return None, err or "目录路径非法"
            return workspace_registry.register_workspace(str(resolved))

        entry, err = await asyncio.to_thread(_register)
        if entry is None or err:
            await self._emit(BackendEvent(type="error", message=err or "注册目录失败"))
            return
        host._sync_workspace_states_from_registry()
        await host._push_workspaces()
        await host._push_sessions()

    async def handle_web_remove_workspace(self, request: FrontendRequest) -> None:
        """移除一个已注册的目录空间（默认工作区不可移除，由设置管理）。

        移除即连带删除该目录的全部会话（磁盘快照 + 内存运行时 + 文件历史），
        会话列表随之清空——符合"移除目录"的语义。
        目录存在运行中的会话（busy）时拒绝移除，避免中断进行中的任务。

        Args:
            request: 前端请求（path 必填：目录路径）
        """
        from illusion.services import workspace_registry

        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        raw = (request.path or "").strip()
        if not raw:
            await self._emit(BackendEvent(type="error", message="目录路径不能为空"))
            return

        def _do_remove() -> tuple[bool, str | None]:
            from illusion.services.workspace_registry import normalize_workspace_path
            from illusion.ui.web.ws_host import _cwd_key as _ws_cwd_key

            target_cwd = normalize_workspace_path(raw)
            # 运行中的会话禁删：目录有 busy 会话时拒绝移除
            busy_sessions = [
                sr for sr in host._sessions.values()
                if _ws_cwd_key(sr.bundle.cwd) == _ws_cwd_key(target_cwd) and sr.busy
            ]
            if busy_sessions:
                return False, "目录存在运行中的会话，无法移除（请先停止任务）"
            removed = workspace_registry.unregister_workspace(raw)
            if not removed:
                return False, "该目录未注册（或为默认目录）"
            # 连带删除该目录的全部会话（磁盘 + 文件历史）
            sessions = _list_session_snapshots(target_cwd, limit=1000)
            for s in sessions:
                _delete_session_by_id(target_cwd, s["session_id"])
                _cleanup_file_history(s["session_id"])
            return True, None

        ok, err = await asyncio.to_thread(_do_remove)
        if not ok:
            await self._emit(BackendEvent(type="error", message=err or "移除目录失败"))
            return
        # 释放该目录的内存会话运行时（含活跃会话；目录已删除，会话随之清理）
        from illusion.ui.web.ws_host import _cwd_key as _ws_cwd_key

        target_key = _ws_cwd_key(await asyncio.to_thread(workspace_registry.normalize_workspace_path, raw))
        for sr in list(host._sessions.values()):
            if _ws_cwd_key(sr.bundle.cwd) == target_key:
                await host._dispose_session(sr.session_id)
        host._sync_workspace_states_from_registry()
        await host._push_workspaces()
        await host._push_sessions()

    # === Agent 管理（Web 设置表单 AgentsTab）===

    def _serialize_agent_entry(
        self,
        agent: Any,
        settings: Any,
        scope: str,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """将代理定义序列化为 AgentsTab 条目。

        条目携带生效模型（内置代理含 settings.agent_models 固化覆盖）与
        该模型的多模态声明（supports_images，供前端展示徽标）。

        Args:
            agent: 代理定义
            settings: 当前 Settings（用于模型引用解析与能力查询）
            scope: 作用域（builtin / user / project / plugin）
            workspace: 项目级代理所属工作区路径（其余作用域为 None）

        Returns:
            dict[str, Any]: 可直接 JSON 序列化的代理条目
        """
        from illusion.coordinator.agent_definitions import GOAL_VERIFIER_AGENT_NAME

        raw_model = str(agent.model or "").strip()
        override = ""
        if scope == "builtin":
            override = str(settings.agent_models.get(agent.name, "") or "").strip()
            if override.lower() == "inherit":
                override = ""

        model_ref: str | None = None
        model_resolved: str | None = None
        supports_images: bool | None = None
        candidate = override or raw_model
        if candidate and candidate.lower() != "inherit":
            env_key, model_name, ref = settings.resolve_agent_model_spec(candidate)
            if ref and env_key and model_name:
                model_ref = ref
                model_resolved = model_name
                supports_images = settings.get_model_capabilities(ref).supports_images

        return {
            "name": agent.name,
            "description": agent.description or "",
            "source": agent.source,
            "scope": scope,
            "workspace": workspace,
            "color": agent.color,
            "background": agent.background,
            # goal 专用标记（前端展示"Goal 专用"徽标；仅内置 goal-verifier 为 True）
            "goal_specific": agent.name == GOAL_VERIFIER_AGENT_NAME,
            # 生效模型引用（env_N.model_M）；None 表示继承当前会话模型
            "model": model_ref,
            "model_resolved": model_resolved,
            "supports_images": supports_images,
            "base_dir": agent.base_dir,
            "filename": agent.filename,
            "tools": agent.tools,
            "effort": str(agent.effort) if agent.effort is not None else None,
            "permission_mode": agent.permission_mode,
            "max_turns": agent.max_turns,
            "system_prompt": agent.system_prompt,
        }

    def _collect_agents_catalog(self) -> dict[str, Any]:
        """收集全部代理并按作用域分组（同步，供 to_thread 调用）。

        分组：
            - global：内置 + 用户级（~/.illusion/agents）+ 插件
            - projects：各注册工作区的项目级代理（{ws}/.illusion/agents）

        Returns:
            dict[str, Any]: web_agents 事件载荷
        """
        from illusion.config.paths import get_config_dir
        from illusion.config.settings import load_settings
        from illusion.coordinator.agent_definitions import (
            GOAL_VERIFIER_AGENT_NAME,
            get_builtin_agent_definitions,
            get_goal_verifier_definition,
            load_agents_dir,
        )
        from illusion.services import workspace_registry

        settings = load_settings()
        global_entries: list[dict[str, Any]] = []

        for agent in get_builtin_agent_definitions():
            global_entries.append(
                self._serialize_agent_entry(agent, settings, "builtin"))

        # 内置 goal-verifier（内部专用，独立配置模型）：作为内置条目展示。
        # 用户/项目级创建了同名定义时不附加（以用户定义为准，避免重复条目）
        for agent in load_agents_dir(get_config_dir() / "agents"):
            global_entries.append(
                self._serialize_agent_entry(agent, settings, "user"))
        if not any(
            e.get("name") == GOAL_VERIFIER_AGENT_NAME
            for e in global_entries if e.get("scope") == "user"
        ):
            global_entries.append(
                self._serialize_agent_entry(
                    get_goal_verifier_definition(), settings, "builtin"))

        try:
            import os as _os

            from illusion.config.settings import load_settings as _load_settings
            from illusion.coordinator.agent_definitions import AgentDefinition
            from illusion.plugins.loader import load_plugins as _load_plugins

            for plugin in _load_plugins(_load_settings(), _os.getcwd()):
                if not getattr(plugin, "enabled", True):
                    continue
                for agent_def in getattr(plugin, "agents", []) or []:
                    if isinstance(agent_def, AgentDefinition):
                        global_entries.append(
                            self._serialize_agent_entry(agent_def, settings, "plugin"))
        except Exception:
            log.exception("收集插件代理失败")

        projects: list[dict[str, Any]] = []
        for view in workspace_registry.resolve_workspace_views():
            agents_dir = Path(view["path"]) / ".illusion" / "agents"
            entries = [
                self._serialize_agent_entry(agent, settings, "project", view["path"])
                for agent in load_agents_dir(agents_dir)
            ]
            projects.append({
                "workspace": view["path"],
                "name": view["name"],
                "is_default": view["is_default"],
                "available": view["available"],
                "agents": entries,
            })

        return {"global": global_entries, "projects": projects}

    async def _push_agents(self) -> None:
        """收集并推送 web_agents 事件（供多处复用）。"""
        catalog = await asyncio.to_thread(self._collect_agents_catalog)
        await self._emit(BackendEvent(type="web_agents", web_agents=catalog))

    async def handle_web_request_agents(self, request: FrontendRequest) -> None:
        """拉取代理列表（内置/全局/项目级分组）并推送 web_agents 事件。

        Args:
            request: 前端请求（无附加字段）
        """
        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        await self._push_agents()

    def _allowed_agents_dirs(self) -> set[str]:
        """返回允许读写的 agents 目录集合（规范化）。

        用户级：``~/.illusion/agents``；项目级：各注册工作区的
        ``{workspace}/.illusion/agents``。base_dir 必须命中集合才允许
        更新/删除——防止 `"内置/插件不可删"` 只在前端隐藏按钮、服务端
        可传任意目录删除/改写任意 .md 文件的漏洞。

        Returns:
            set[str]: 经过 normcase 规范化的目录绝对路径集合
        """
        import os as _os

        from illusion.config.paths import get_config_dir
        from illusion.services import workspace_registry

        dirs: set[str] = {str((get_config_dir() / "agents").resolve())}
        # 默认工作区（settings.working_directory）未注册时也属于项目级候选
        try:
            for view in workspace_registry.resolve_workspace_views():
                dirs.add(str((Path(view["path"]) / ".illusion" / "agents").resolve()))
        except Exception:
            log.exception("读取工作区注册表失败，仅限用户级目录")
        return {_os.path.normcase(p) for p in dirs}

    def _validate_model_value(self, model: Any) -> tuple[bool, str]:
        """规范化模型更新值：inherit/空为继承；其余必须是合法 env 引用。

        Returns:
            tuple[bool, str]: (是否合法, 规范值或错误信息)
        """
        from illusion.config.settings import load_settings

        model_str = str(model if model is not None else "").strip()
        if model_str.lower() in ("", "inherit"):
            return True, "inherit"
        settings = load_settings()
        env_key, _mn, ref = settings.resolve_agent_model_spec(model_str)
        if not ref or not env_key:
            return False, (
                f"未知模型 '{model_str}'：请使用 env_N.model_M 引用或 'inherit'"
            )
        return True, ref

    def _is_allowed_agents_dir(self, base_dir: str) -> bool:
        """base_dir 是否命中允许的 agents 目录列表。"""
        import os as _os

        return _os.path.normcase(str(Path(base_dir))) in self._allowed_agents_dirs()

    def _update_agent_on_disk(
        self, fields: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """按字段定位并更新一个 .md 代理定义（同步，供 to_thread 调用）。

        安全约束：
            - base_dir 必须命中允许的 agents 目录（用户级/已注册工作区）
            - 模型值必须为合法的 env_N.model_M 引用或 inherit（与内置分支
              同一校验，避免裸模型名落盘后再 404）

        Args:
            fields: 请求载荷（name/base_dir 必填；model 及其余受管字段可选）

        Returns:
            tuple[bool, str | None]: (是否成功, 错误信息)
        """

        from illusion.coordinator.agent_definitions import load_agents_dir

        name = str(fields.get("name", "")).strip()
        base_dir = str(fields.get("base_dir", "")).strip()
        if not name or not base_dir:
            return False, "缺少代理名称或定义目录"
        if not self._is_allowed_agents_dir(base_dir):
            return False, f"目录 {base_dir} 不在允许的子智能体定义目录列表中"

        model_ok, model_value = self._validate_model_value(fields.get("model"))
        if not model_ok:
            return False, model_value

        target = next(
            (a for a in load_agents_dir(Path(base_dir)) if a.name == name), None)
        if target is None:
            return False, f"目录 {base_dir} 下未找到代理 '{name}'"

        from illusion.services.agent_creator import update_agent_definition_file

        updates: dict[str, Any] = {}
        for key in ("model", "description", "system_prompt", "tools",
                    "effort", "permission_mode", "max_turns"):
            if key in fields:
                updates[key] = fields[key]
        if "model" in updates:
            updates["model"] = model_value
        try:
            update_agent_definition_file(target, updates)
        except (ValueError, OSError) as exc:
            return False, str(exc)
        return True, None

    async def handle_web_update_agent(self, request: FrontendRequest) -> None:
        """更新代理配置并推送刷新后的 web_agents。

        内置代理（source=builtin）仅允许改模型：写 settings.agent_models
        固化到 settings.json（"inherit" 清除覆盖）；用户/项目级代理直接
        外科手术式改写其 .md frontmatter（模型值校验与内置分支一致）。

        Args:
            request: 前端请求（fields 携带 name/source/base_dir/model 等）
        """
        from illusion.config.settings import load_settings, save_settings

        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        fields = request.fields or {}
        name = str(fields.get("name", "")).strip()
        source = str(fields.get("source", "user")).strip()

        if source == "builtin":
            if not name:
                await self._emit(BackendEvent(
                    type="web_agent_op_result", web_agent_op="update",
                    success=False, error="缺少代理名称"))
                return
            from illusion.coordinator.agent_definitions import (
                get_builtin_agent_definitions,
                get_goal_verifier_definition,
            )

            builtin_names = {
                a.name
                for a in get_builtin_agent_definitions()
            } | {get_goal_verifier_definition().name}
            if name not in builtin_names:
                await self._emit(BackendEvent(
                    type="web_agent_op_result", web_agent_op="update",
                    success=False, error=f"'{name}' 不是内置子智能体"))
                return
            model_ok, model_value = self._validate_model_value(fields.get("model"))
            if not model_ok:
                await self._emit(BackendEvent(
                    type="web_agent_op_result", web_agent_op="update",
                    success=False, error=model_value))
                return
            settings = load_settings()
            if model_value == "inherit":
                settings.agent_models.pop(name, None)
            else:
                settings.agent_models[name] = model_value
            save_settings(settings)
            await self._push_agents()
            await self._refresh_resources_after_agent_op()
            await self._emit(BackendEvent(
                type="web_agent_op_result", web_agent_op="update",
                success=True))
            return

        ok, err = await asyncio.to_thread(self._update_agent_on_disk, fields)
        await self._push_agents()
        if ok:
            await self._refresh_resources_after_agent_op()
        await self._emit(BackendEvent(
            type="web_agent_op_result", web_agent_op="update",
            success=ok, error=err))

    def _delete_agent_on_disk(
        self, fields: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """按字段定位并删除一个用户创建的 .md 代理定义（同步）。"""
        from illusion.coordinator.agent_definitions import load_agents_dir
        from illusion.services.agent_creator import delete_agent_definition_file

        name = str(fields.get("name", "")).strip()
        base_dir = str(fields.get("base_dir", "")).strip()
        if not name or not base_dir:
            return False, "缺少代理名称或定义目录"
        if not self._is_allowed_agents_dir(base_dir):
            return False, f"目录 {base_dir} 不在允许的子智能体定义目录列表中"
        target = next(
            (a for a in load_agents_dir(Path(base_dir)) if a.name == name), None)
        if target is None:
            return False, f"目录 {base_dir} 下未找到代理 '{name}'"
        try:
            delete_agent_definition_file(target)
        except (ValueError, OSError) as exc:
            return False, str(exc)
        return True, None

    async def handle_web_delete_agent(self, request: FrontendRequest) -> None:
        """删除用户创建的代理定义文件并推送刷新后的 web_agents。

        仅用户创建的代理可删除（内置/插件拒绝）。

        Args:
            request: 前端请求（fields 携带 name/base_dir）
        """
        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        ok, err = await asyncio.to_thread(
            self._delete_agent_on_disk, request.fields or {})
        await self._push_agents()
        if ok:
            await self._refresh_resources_after_agent_op()
        await self._emit(BackendEvent(
            type="web_agent_op_result", web_agent_op="delete",
            success=ok, error=err))

    async def _refresh_resources_after_agent_op(self) -> None:
        """代理增删改后刷新右栏资源快照（agents 区块与派发描述同步）。"""
        host = self._host
        bundle = host._active_bundle() or host._bundle
        if bundle is not None:
            await self._push_resources(bundle)

    async def handle_web_query(self, request: FrontendRequest) -> None:
        """B 通道精细化指令处理。

        复用 CommandRegistry 的 handler 拿到 CommandResult，但渲染层映射到
        web_query_result（不产生 command_result 事件）。

        不经过 _process_line/handle_line，避免 transcript_item/hook reload 副作用。

        Args:
            request: 前端请求（command/args/request_id 必填）
        """
        host = self._host
        if host._bundle is None:
            await self._emit(BackendEvent(type="error", message="运行时未就绪"))
            return
        session = host._resolve_session(request.session_id)
        if session is None:
            return
        bundle = session.bundle
        command = request.command or ""
        args = request.args or ""
        request_id = request.request_id or ""
        session_id = session.session_id

        # rename 目标会话可能已从内存淘汰或位于其他工作区：_resolve_session 会
        # 回退到活跃会话，导致 rename_handler 用错目录读 meta（报"会话不存在"）。
        # 按 sid 定位目标所属工作区并改用该 bundle 执行（read_meta 依赖 context.cwd）。
        if (
            command == "rename"
            and request.session_id
            and session.session_id != request.session_id
        ):
            located = host._locate_session_workspace(request.session_id)
            if located:
                try:
                    ws_bundle = host._workspace_bundle_for(located) or await host._get_or_build_bundle(located)
                except Exception:
                    log.exception("重命名定位工作区失败: cwd=%s", located)
                else:
                    bundle = ws_bundle
                    session_id = request.session_id

        # 执行型/查询型（compact/export/init 及无参查询）：复用 registry handler
        # （rename 跨工作区时 bundle 已切换为目标会话所属工作区）
        result = await _run_command_via_registry(f"/{command} {args}".strip(), bundle)
        if result is None:
            # 已通过 select_request 或其他机制处理
            return
        # 命令返回了 replay_messages（如 compact 压缩后的历史）：
        # 先发文本结果（toast 提示压缩前后数量），再发 transcript_replace
        # 让前端一次性替换转录，与后端引擎状态对齐
        if result.replay_messages:
            if result.message:
                await self._emit(BackendEvent(
                    type="web_query_result", web_request_id=request_id, web_command=command,
                    web_query_kind="text", web_query_payload=result.message,
                ), session_id=session_id)
            replay_items = build_replay_items(result.replay_messages)
            await self._emit(BackendEvent(
                type="web_query_result", web_request_id=request_id, web_command=command,
                web_query_kind="transcript_replace", web_query_payload=replay_items,
            ), session_id=session_id)
            return
        payload = result.message or ""
        await self._emit(BackendEvent(
            type="web_query_result", web_request_id=request_id, web_command=command,
            web_query_kind="text", web_query_payload=payload,
        ), session_id=session_id)
        # rename 后刷新会话列表（title 变化需更新侧边栏）
        if command == "rename":
            for sr in host._sessions.values():
                host._refresh_session_display(sr)
            await host._push_sessions()


async def _run_command_via_registry(line: str, bundle: RuntimeBundle) -> CommandResult | None:
    """通过 CommandRegistry 执行命令并返回结果（不经过 handle_line）。

    B 通道（web_query）的执行型/查询型指令复用此函数，避免触发
    transcript_item/hook reload 等 terminal 副作用。

    Args:
        line: 完整命令行（如 "/compact"）
        bundle: 运行时 bundle

    Returns:
        CommandResult | None: 命令结果，None 表示命令未识别或已通过其他机制处理
    """
    registry = create_default_command_registry()
    parsed = registry.lookup(line)
    if parsed is None:
        return None
    command, args = parsed
    context = CommandContext(
        engine=bundle.engine,
        hooks_summary=bundle.hook_summary(),
        mcp_summary=bundle.mcp_summary(),
        plugin_summary=bundle.plugin_summary(),
        cwd=bundle.cwd,
        tool_registry=bundle.tool_registry,
        app_state=bundle.app_state,
        session_id=bundle.session_id,
    )
    return await command.handler(args, context)


def _collect_resources(bundle: RuntimeBundle) -> dict[str, Any]:
    """收集右侧栏资源快照（skills/agents/plugins/rules/mcp_servers）。

    直接调用各注册表/管理器的结构化接口，废弃旧的命令文本正则解析
    （_parseSkillsResult / _parsePluginsResult / _parseRulesResult）。

    Args:
        bundle: 运行时 bundle

    Returns:
        dict[str, Any]: {skills, agents, plugins, rules, mcp_servers} 结构化快照
    """
    # skills：从技能注册表读取结构化数据
    from illusion.skills.loader import load_skill_registry
    skill_registry = load_skill_registry(bundle.cwd)
    skills = [
        {"name": s.name, "description": s.description or "", "source": s.source}
        for s in skill_registry.list_skills()
    ]

    # agents：内置 + 用户级 + 项目级（随 bundle.cwd）+ 插件的合并视图
    agents = []
    try:
        from illusion.coordinator.agent_definitions import get_all_agent_definitions
        for agent in get_all_agent_definitions(cwd=bundle.cwd):
            agents.append({
                "name": agent.name,
                "description": agent.description or "",
                "source": agent.source,
                "color": agent.color,
                "model": agent.model,
                "background": agent.background,
            })
    except Exception:
        log.exception("收集代理快照失败")

    # plugins：从当前可见插件读取（复用 bundle.current_plugins）
    plugins = []
    try:
        for plugin in bundle.current_plugins():
            manifest = getattr(plugin, "manifest", None)
            name = getattr(manifest, "name", "") if manifest else ""
            description = getattr(manifest, "description", "") if manifest else ""
            plugins.append({
                "name": name,
                "description": description,
                "enabled": bool(getattr(plugin, "enabled", False)),
                "skill_count": 0,
                "mcp_count": 0,
                "command_count": 0,
            })
    except Exception:
        log.exception("收集插件快照失败")

    # rules：从项目规则目录读取，过滤被权限禁用的规则
    rules = []
    try:
        from illusion.permissions.loader import (
            filter_rules_by_permissions,
            is_rules_disabled,
            load_project_permissions,
        )
        from illusion.skills.loader import get_project_rules_dir
        project_permissions = load_project_permissions(bundle.cwd)
        if not is_rules_disabled(project_permissions):
            rules_dir = get_project_rules_dir(bundle.cwd)
            if rules_dir.exists():
                rule_files = filter_rules_by_permissions(
                    sorted(rules_dir.glob("*.md")), project_permissions
                )
                for path in rule_files:
                    rules.append({"name": path.stem, "source": "project"})
    except Exception:
        log.exception("收集规则快照失败")

    # mcp_servers：复用 mcp_manager 的连接状态
    mcp_servers = []
    try:
        for server in bundle.mcp_manager.list_statuses():
            mcp_servers.append({
                "name": server.name,
                "state": server.state,
                "tool_count": len(server.tools) if hasattr(server, "tools") else 0,
            })
    except Exception:
        log.exception("收集 MCP 服务器快照失败")

    return {"skills": skills, "agents": agents, "plugins": plugins, "rules": rules, "mcp_servers": mcp_servers}


# ---------------------------------------------------------------------------
# 右栏扩展纯函数辅助：文件树过滤 / Git 解析 / 文件预览
# （独立于 dispatcher，便于单元测试）
# ---------------------------------------------------------------------------

# 单层目录条目上限（超出截断，前端显示省略行）
_TREE_MAX_ENTRIES = 500
# 文件预览限制
_PREVIEW_MAX_BYTES = 512 * 1024
_PREVIEW_MAX_LINES = 4000
# git 子进程超时（秒）
_GIT_TIMEOUT = 5.0


def _resolve_within_root(root: str, rel: str) -> Path | None:
    """将相对路径解析到 root 内的绝对路径，越界/穿越返回 None。

    Args:
        root: 工作区根目录（绝对路径）
        rel: 工作区内相对路径（空串表示根；支持 / 或 \\ 分隔）

    Returns:
        Path | None: 解析后的绝对路径；超出 root 或解析失败返回 None
    """
    try:
        root_path = Path(root).resolve()
        target = (root_path / rel).resolve() if rel else root_path
        target.relative_to(root_path)
    except (OSError, ValueError):
        return None
    return target


def _list_dir_entries(directory: Path, root: str) -> tuple[list[dict[str, Any]], bool]:
    """列出目录一层的可见条目（目录优先，名称不区分大小写排序）。

    Args:
        directory: 目标目录（绝对路径）
        root: 工作区根目录（条目 path 字段以根为基准的相对路径，/ 分隔）

    Returns:
        tuple[list[dict[str, Any]], bool]: (条目列表, 是否因超过上限截断)；
        条目为 {name, path, kind: dir|file, size: 文件字节数}
    """
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(directory) as it:
            for e in it:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if not _tree_entry_visible(e.name, is_dir):
                    continue
                if len(entries) >= _TREE_MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    rel = os.path.relpath(e.path, root).replace("\\", "/")
                except ValueError:
                    continue
                entry: dict[str, Any] = {"name": e.name, "path": rel, "kind": "dir" if is_dir else "file"}
                if not is_dir:
                    try:
                        entry["size"] = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        entry["size"] = 0
                entries.append(entry)
    except OSError:
        return [], False
    entries.sort(key=lambda x: (x["kind"] != "dir", x["name"].lower()))
    return entries, truncated


def _run_git(cwd: str, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str | None:
    """在工作区目录执行 git 子命令，成功返回 stdout，失败/超时返回 None。

    Args:
        cwd: 工作区目录
        *args: git 子命令参数
        ok_codes: 视为成功的退出码集合（如 --no-index 有差异时退出码为 1）

    Returns:
        str | None: stdout 原文（含换行）；退出码不在 ok_codes、git 缺失或超时返回 None
    """
    try:
        # 非零退出码（如无上游/空仓库）由调用方按返回值降级，无需抛异常
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode in ok_codes else None


# porcelain 状态字母 → 展示状态
_GIT_STATUS_MAP = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "modified",
    "T": "modified",
    "U": "unmerged",
    "?": "untracked",
}


def _parse_git_porcelain(raw: str) -> list[dict[str, Any]]:
    """解析 ``git status --porcelain=v1 -z -uall`` 输出。

    -z 模式条目以 NUL 分隔（路径内空格/引号不转义）；R/C 条目
    后跟一条原始路径记录。XY 双字母：X=暂存区，Y=工作区。

    Args:
        raw: porcelain -z 原始输出

    Returns:
        list[dict[str, Any]]: [{path, status, staged, orig_path?, insertions, deletions}]
    """
    files: list[dict[str, Any]] = []
    fields = [f for f in raw.split("\0") if f]
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if len(field) < 4:
            continue
        xy, path = field[:2], field[3:]
        orig: str | None = None
        if xy[0] in ("R", "C") and i < len(fields):
            orig = fields[i]
            i += 1
        x, y = xy[0], xy[1]
        letter = x if x not in (" ", "?", "!") else y
        files.append({
            "path": path,
            "status": _GIT_STATUS_MAP.get(letter, "modified"),
            "staged": x not in (" ", "?", "!"),
            "orig_path": orig,
            "insertions": None,
            "deletions": None,
        })
    return files


def _parse_git_numstat(raw: str) -> dict[str, tuple[int | None, int | None]]:
    """解析 ``git diff --numstat -z`` 输出为 路径 → (增行, 删行) 映射。

    二进制文件的增删为 "-"（映射为 None）；重命名条目附带的原始路径
    记录无制表符分隔结构，自然跳过。

    Args:
        raw: numstat -z 原始输出

    Returns:
        dict[str, tuple[int | None, int | None]]: 路径 → (insertions, deletions)
    """
    result: dict[str, tuple[int | None, int | None]] = {}
    for field in raw.split("\0"):
        if not field:
            continue
        parts = field.split("\t")
        if len(parts) != 3:
            continue
        added_s, deleted_s, path = parts
        result[path] = (
            int(added_s) if added_s.isdigit() else None,
            int(deleted_s) if deleted_s.isdigit() else None,
        )
    return result


def _git_status_snapshot(cwd: str) -> dict[str, Any]:
    """采集工作区 Git 状态快照（分支/上游/领先落后/变更文件）。

    所有 git 调用失败安全降级：非仓库返回 {"is_repo": False}；
    无上游/空仓库/分离 HEAD 等缺省字段为 None。

    Args:
        cwd: 工作区目录

    Returns:
        dict[str, Any]: {is_repo, branch?, upstream?, ahead?, behind?, files?}
    """
    inside = _run_git(cwd, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.strip() != "true":
        return {"is_repo": False}

    branch = (_run_git(cwd, "branch", "--show-current") or "").strip()
    if not branch:
        head = _run_git(cwd, "rev-parse", "--short", "HEAD")
        if head:
            branch = f"({head.strip()})"  # 分离 HEAD，展示短提交号

    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    up = _run_git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up and up.strip():
        upstream = up.strip()
        counts = _run_git(cwd, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
        if counts:
            parts = counts.split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                except ValueError:
                    pass

    files = _parse_git_porcelain(_run_git(cwd, "status", "--porcelain=v1", "-z", "-uall") or "")
    stats = _parse_git_numstat(_run_git(cwd, "diff", "--numstat", "-z", "--relative", "HEAD") or "")
    for f in files:
        stat = stats.get(f["path"])
        if stat is not None:
            f["insertions"], f["deletions"] = stat
    files.sort(key=lambda f: f["path"].lower())

    return {
        "is_repo": True,
        "branch": branch or None,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "files": files,
    }


def _collect_agent_tasks(messages: list[Any]) -> list[dict[str, Any]]:
    """从会话 transcript 收集智能体与后台任务（复用 /agent 指令的双数据源）。

    数据源与 /agent handler 保持一致：
    1. 前台 agent：assistant 消息中 name=agent 的 ToolUseBlock（标题取
       description/name/subagent_type 入参）+ user 消息中对应 tool_result
       （跳过 "launched in background/as subprocess" 启动通知）；
    2. 后台任务：user 消息 TextBlock 的 task-notification
       （TASK_NOTIFICATION_RE：task-id/status/summary/task-name/result），
       task-name/task-id 含 agent 判为智能体，否则为任务。

    Args:
        messages: 会话引擎消息列表（engine.messages）

    Returns:
        list[dict[str, Any]]: [{id, title, type: agent|task, status, summary}]，
        按出现顺序倒排（最近的在最前）
    """
    from illusion.engine.messages import TextBlock, ToolResultBlock, ToolUseBlock
    from illusion.swarm.agent_executor import agent_type_display
    from illusion.tasks.types import TASK_NOTIFICATION_RE

    def _is_agent_id(task_id: str, task_name: str) -> bool:
        # task_id 前缀即类型权威（tasks/manager._task_id）：
        #   a/r = 后台 agent（in_process_agent/local_agent/remote_agent）
        #   t = in_process_teammate（团队队友，同为智能体）
        #   b = local_bash（后台命令任务）
        # 未知格式回退：仅按 task_name 判断（task_id 为随机 hex 不含语义，
        # 旧实现把含 "agent" 字样的 bash 命令误判为智能体、把类型段不含
        # "agent" 的后台智能体误判为任务——正是分类混乱的根源）。
        prefix = task_id[:1].lower()
        if prefix in ("a", "r", "t"):
            return True
        if prefix == "b":
            return False
        return "agent" in task_name.lower()

    items: list[dict[str, Any]] = []

    # 1. 前台 agent 工具调用 → 匹配 tool_result
    front_results: dict[str, ToolResultBlock] = {}
    for msg in messages:
        if msg.role != "user":
            continue
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                front_results[block.tool_use_id] = block
    for msg in messages:
        if msg.role != "assistant":
            continue
        for block in msg.content:
            if not isinstance(block, ToolUseBlock) or block.name != "agent":
                continue
            result = front_results.get(block.id)
            if result is None:
                continue
            text = result.text_content or ""
            if "launched in background" in text or "launched as subprocess" in text:
                continue  # 启动通知非摘要，与 /agent 过滤一致
            inputs = block.input if isinstance(block.input, dict) else {}
            title = str(
                inputs.get("description") or inputs.get("name") or inputs.get("subagent_type") or "agent"
            )
            items.append({
                "id": block.id,
                "title": title,
                "type": "agent",
                "status": "failed" if result.is_error else "completed",
                "summary": " ".join(text.split())[:160],
            })

    # 2. 后台任务通知（agent / bash / powershell 等）
    for msg in messages:
        if msg.role != "user":
            continue
        for block in msg.content:
            if not isinstance(block, TextBlock):
                continue
            match = TASK_NOTIFICATION_RE.search(block.text)
            if not match:
                continue
            task_id = match.group("task_id").strip()
            task_name = (match.group("task_name") or "").strip()
            # 类型段统一规范化为 PascalCase（旧通知可能是原始 subagent_type；
            # agent_type_display 对已是驼峰的输入幂等），与 /agent 列表一致
            if " · " in task_name:
                name_part, _, type_part = task_name.rpartition(" · ")
                task_name = f"{name_part} · {agent_type_display(type_part)}"
            items.append({
                "id": task_id,
                "title": task_name or task_id,
                "type": "agent" if _is_agent_id(task_id, task_name) else "task",
                "status": match.group("status").strip(),
                "summary": " ".join((match.group("summary") or "").split())[:160],
            })

    items.reverse()  # 最近的在最前
    return items


# 会话内直接修改文件的工具（输入含 file_path/path 字段，路径可界定）
_SESSION_FILE_TOOLS = ("edit_file", "write_file")


def _collect_session_files(
    messages: list[Any], cwd: str
) -> list[dict[str, Any]]:
    """从会话转录收集变更工具修改过的文件列表（会话文件区块数据源）。

    遍历 assistant 消息中的 edit_file/write_file 工具调用，提取目标路径
    并解析为绝对路径（相对 cwd 或绝对均可），按首次出现顺序去重。
    只会收录"调用成功"的文件：对应该次调用的 ToolResultBlock 若标记
    失败（is_error=True）则跳过，避免把报错路径误当已修改文件。
    该列表独立于 Git 与工作区边界：可包含工作区之外、不被 Git 追踪、
    无 Git 环境下的文件（均可直接预览）。

    Args:
        messages: 会话引擎消息列表（engine.messages）
        cwd: 工作区目录（相对路径解析基准）

    Returns:
        list[dict[str, Any]]: [{path, display, tool}]
        path 与 display 均为绝对路径（/ 分隔；读取与安全校验键，
        展示统一绝对路径样式）
    """
    from illusion.config.paths import resolve_relative_path
    from illusion.engine.messages import ToolResultBlock, ToolUseBlock

    # 失败的工具调用 id 集：user 消息里对应调用返回 is_error=True
    failed_use_ids: set[str] = set()
    for msg in messages:
        if getattr(msg, "role", None) != "user":
            continue
        for block in getattr(msg, "content", []):
            if isinstance(block, ToolResultBlock) and block.is_error and block.tool_use_id:
                failed_use_ids.add(block.tool_use_id)

    seen: set[str] = set()
    root = Path(cwd).resolve()
    result: list[dict[str, Any]] = []
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        for block in getattr(msg, "content", []):
            if not isinstance(block, ToolUseBlock) or block.name not in _SESSION_FILE_TOOLS:
                continue
            if block.id in failed_use_ids:
                continue
            inputs = block.input if isinstance(block.input, dict) else {}
            raw = inputs.get("file_path") or inputs.get("path")
            if not raw or not isinstance(raw, str) or not raw.strip():
                continue
            try:
                abs_path = str(resolve_relative_path(root, raw.strip()))
            except (ValueError, OSError):
                continue
            if abs_path in seen:
                continue
            seen.add(abs_path)
            # 展示统一绝对路径（/ 分隔），与单轮变更条样式一致
            result.append({
                "path": abs_path,
                "display": Path(abs_path).as_posix(),
                "tool": block.name,
            })
    return result


def _read_session_file_payload(path: str) -> dict[str, Any]:
    """读取会话内修改文件生成预览载荷（复用 _read_file_payload 限制）。

    文件不存在（被用户手动删除或工具在会话中删除）时返回结构化错误码
    ``file_deleted``，交由前端 i18n 本地化展示，避免裸 OSError 英文串；
    其余读取失败沿用 _read_file_payload 的错误载荷。

    Args:
        path: 文件绝对路径（已经过会话修改记录校验）

    Returns:
        dict[str, Any]: 与 _read_file_payload 相同的预览载荷结构
    """
    target = Path(path)
    if not target.exists():
        return {"path": path, "error": "file_deleted"}
    return _read_file_payload(target, path)


def _cap_preview_text(text: str) -> tuple[str, bool]:
    """按预览上限截断文本（字节 + 行数），返回 (文本, 是否截断)。"""
    truncated = False
    data = text.encode("utf-8", errors="replace")
    if len(data) > _PREVIEW_MAX_BYTES:
        text = data[:_PREVIEW_MAX_BYTES].decode("utf-8", errors="replace")
        truncated = True
    lines = text.split("\n")
    if len(lines) > _PREVIEW_MAX_LINES:
        text = "\n".join(lines[:_PREVIEW_MAX_LINES])
        truncated = True
    return text, truncated


def _git_file_diff(cwd: str, rel: str) -> dict[str, Any]:
    """获取单个文件相对 HEAD 的局部 diff（少量上下文的 hunk 结构）。

    -U3 让每个 hunk 只带极少量上下文，前端据此只展示局部增减，并在
    hunk 头标注新旧文件起点行号，删除行/新增行各自对应正确的行号；未跟踪/
    新文件用 --no-index 合成全新增 diff。
    判定顺序：
    1. ``git diff HEAD -- <path>`` 非空 → 直接返回（暂存 + 工作区合并差异）；
    2. diff HEAD 成功但为空：文件被跟踪（ls-files 可匹配）且无变更 → 空 diff；
       未被跟踪则继续（diff HEAD 不含 untracked 文件）；
    3. 未跟踪/新文件/空仓库（无 HEAD，diff 失败）→ ``--no-index`` 合成全新增 diff。

    Args:
        cwd: 工作区目录
        rel: 工作区内相对路径（/ 分隔；文件可能已被删除）

    Returns:
        dict[str, Any]: {path, kind: diff, content, truncated} 或 {path, kind, error}
    """
    # git 不可用 / 非仓库：--no-index 无需仓库也能成功，需前置判定
    inside = _run_git(cwd, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.strip() != "true":
        return {"path": rel, "kind": "diff", "error": "无法获取 diff（非 Git 仓库或 git 不可用）"}
    tracked = _run_git(cwd, "diff", "-U3", "HEAD", "--", rel)
    if tracked is not None and tracked.strip():
        content, truncated = _cap_preview_text(tracked)
        return {"path": rel, "kind": "diff", "content": content, "truncated": truncated}
    if tracked is not None:
        # diff HEAD 为空：被跟踪且无变更（untracked 不会出现在 diff HEAD 中，需判别）
        matched = _run_git(cwd, "ls-files", "--error-unmatch", "--", rel)
        if matched is not None:
            return {"path": rel, "kind": "diff", "content": "", "truncated": False}
    # 未跟踪 / 新文件 / 空仓库：--no-index 与空设备比较合成全新增 diff
    synth = _run_git(cwd, "diff", "--no-index", "-U3", "--", os.devnull, rel, ok_codes=(0, 1))
    if synth is not None and synth.strip():
        # 头部的 a/<devnull> 规整为 a/dev/null，b/ 侧保持相对路径
        devnull_side = f"a/{os.devnull}"
        if devnull_side in synth:
            synth = synth.replace(devnull_side, "a/dev/null")
        content, truncated = _cap_preview_text(synth)
        return {"path": rel, "kind": "diff", "content": content, "truncated": truncated}
    if synth is not None:
        return {"path": rel, "kind": "diff", "content": "", "truncated": False}
    return {"path": rel, "kind": "diff", "error": "无法获取 diff"}


def _read_file_payload(target: Path, rel: str) -> dict[str, Any]:
    """读取文本文件生成预览载荷（二进制嗅探 + 大小/行数截断）。

    Args:
        target: 文件绝对路径（已经过工作区边界校验）
        rel: 工作区内相对路径（回显给前端）

    Returns:
        dict[str, Any]: {path, binary, size, truncated, content} 或 {path, error}
    """
    try:
        size = target.stat().st_size
        with open(target, "rb") as fh:
            if b"\0" in fh.read(8192):
                return {"path": rel, "binary": True, "size": size, "truncated": False, "content": ""}
            fh.seek(0)
            data = fh.read(_PREVIEW_MAX_BYTES + 1)
    except OSError as exc:
        return {"path": rel, "error": str(exc)}
    truncated = len(data) > _PREVIEW_MAX_BYTES
    if truncated:
        data = data[:_PREVIEW_MAX_BYTES]
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if len(lines) > _PREVIEW_MAX_LINES:
        lines = lines[:_PREVIEW_MAX_LINES]
        truncated = True
    return {
        "path": rel,
        "binary": False,
        "size": size,
        "truncated": truncated,
        "content": "\n".join(lines),
    }


__all__ = ["WebApiDispatcher"]
