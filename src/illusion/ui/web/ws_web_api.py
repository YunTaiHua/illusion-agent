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
from collections.abc import Awaitable, Callable
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
            "web_delete_sessions": self.handle_web_delete_sessions,
            "web_set_setting": self.handle_web_set_setting,
            "web_request_sessions": self.handle_web_request_sessions,
            "web_request_models": self.handle_web_request_models,
            "web_request_resources": self.handle_web_request_resources,
            "web_query": self.handle_web_query,
            "web_request_workspaces": self.handle_web_request_workspaces,
            "web_add_workspace": self.handle_web_add_workspace,
            "web_remove_workspace": self.handle_web_remove_workspace,
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
                target_cwd = request.cwd or host._locate_session_workspace(session_id)
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
                result = await _resume_handler(session_id, context)
                if result.restored_session_id and result.restored_session_id != session_id:
                    # resume_handler 可能规范化会话 id（如 # 轮次引用）：
                    # 同步注册表 key，避免 dict key 与 session_id 不一致
                    # 导致后续请求按新 id 路由时找不到运行时
                    host._sessions.pop(session_id, None)
                    session.session_id = result.restored_session_id
                    session.bundle.session_id = result.restored_session_id
                    host._sessions[session.session_id] = session
                    session_id = session.session_id
                replay_items = build_replay_items(result.replay_messages)
            except Exception as exc:
                log.exception("恢复会话 %s 失败", session_id)
                error_msg = str(exc)
                session = host._sessions.pop(session_id, None)
                # 关闭失败恢复的引擎，避免每次失败泄漏一个运行时
                if session is not None:
                    try:
                        await session.engine.aclose()
                    except Exception:
                        log.exception("关闭失败恢复的会话 %s 引擎出错", session_id)

        # 3. 恢复成功（或已存在）：设为活跃会话
        if error_msg is None and session is not None:
            host._set_active_session(session.session_id)
            session_id = session.session_id
            host._refresh_session_display(session)

        # 4. WebSocket 在恢复过程中关闭：跳过 emit，直接返回
        if host._ws_closed:
            return

        # 5. 始终发 web_restore_completed——前端据此清除 restoringSessionId
        await self._emit(BackendEvent(
            type="web_restore_completed",
            session_id=session_id,
            items=replay_items,  # type: ignore[arg-type]
            state=host._session_state_payload(session) if session is not None else {},
            web_error=error_msg,
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

        复用 _apply_setting 私有函数（B 通道的 web_query 设置类指令也调用它），
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
        if key in ("ui_language", "output_style", "context_window", "effort", "permission_mode"):
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
                 ui_language/turns/output_style）
            value: 设置值

        Returns:
            tuple[bool, str | None]: (是否成功, 错误消息)
        """
        settings = _load_settings()
        # 键名 → app_state 字段名映射（settings 字段名可能与 key 不同）
        if key not in (
            "effort", "permission_mode", "model", "context_window",
            "ui_language", "turns", "output_style",
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
                # effort / context_window / ui_language / output_style
                # settings 字段名与 key 相同（output_style / ui_language / context_window / effort）
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

    async def handle_web_query(self, request: FrontendRequest) -> None:
        """B 通道精细化指令处理。

        复用 CommandRegistry 的 handler 拿到 CommandResult，但渲染层映射到
        web_query_result（不产生 command_result 事件）。设置类指令（turns/
        output-style/language）内部调用 _apply_setting，触发与 A 通道相同的
        web_setting_changed + state_snapshot 同步。

        不经过 _process_line/handle_line，避免 transcript_item/hook reload 副作用。

        rewind/context 需要多步选择，仍走 select_request 机制。

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

        # rewind/context/max-tokens 需要多步选择，仍走 select_request 机制（保留旧 _handle_select_command）
        if command in ("rewind", "context", "max-tokens"):
            await host._handle_select_command(command, session)
            return

        # 设置类指令：内部走 _apply_setting（与 A 通道共用写入逻辑，DRY）
        setting_commands = {
            "turns": "turns",
            "output-style": "output_style",
            "language": "ui_language",
        }
        if command in setting_commands and args:
            key = setting_commands[command]
            tokens = args.split()
            # 参数解析：language set zh-CN → "zh-CN"；turns/output-style → 首个 token
            if command == "language" and len(tokens) >= 2 and tokens[0] == "set":
                value = " ".join(tokens[1:])
            else:
                value = tokens[0] if tokens else ""
            ok, error = await self._apply_setting(bundle, key, value)
            if ok:
                await self._emit(BackendEvent(
                    type="web_setting_changed", setting_key=key, setting_value=value,
                ))
                await self._emit(host._status_snapshot())
                payload = "已更新"
            else:
                payload = error or "设置失败"
            await self._emit(BackendEvent(
                type="web_query_result", web_request_id=request_id, web_command=command,
                web_query_kind="text", web_query_payload=payload,
            ), session_id=session_id)
            return

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
    """收集右侧栏资源快照（skills/plugins/rules/mcp_servers）。

    直接调用各注册表/管理器的结构化接口，废弃旧的命令文本正则解析
    （_parseSkillsResult / _parsePluginsResult / _parseRulesResult）。

    Args:
        bundle: 运行时 bundle

    Returns:
        dict[str, Any]: {skills, plugins, rules, mcp_servers} 结构化快照
    """
    # skills：从技能注册表读取结构化数据
    from illusion.skills.loader import load_skill_registry
    skill_registry = load_skill_registry(bundle.cwd)
    skills = [
        {"name": s.name, "description": s.description or "", "source": s.source}
        for s in skill_registry.list_skills()
    ]

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

    return {"skills": skills, "plugins": plugins, "rules": rules, "mcp_servers": mcp_servers}


__all__ = ["WebApiDispatcher"]
