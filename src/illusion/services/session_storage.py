"""
会话持久化辅助模块
================

本模块提供会话状态持久化功能，支持会话目录管理与会话列表查询。
checkpoint 持久化由 CheckpointStore 负责，本模块仅维护 index.json /
meta.json 以及 pending 类文件。

主要功能：
    - 获取项目会话目录
    - 读写 index.json / meta.json
    - 列出会话快照
    - 删除会话
    - 导出会话记录为 Markdown

类说明：
    - get_project_session_dir: 获取项目会话目录
    - read_index / write_index: 父级 index.json 读写
    - read_meta / write_meta: {session_id}/meta.json 读写
    - list_session_snapshots: 列出会话快照
    - export_session_markdown: 导出为 Markdown

使用示例：
    >>> from illusion.services.session_storage import get_project_session_dir, write_meta
    >>> # 获取项目会话目录
    >>> session_dir = get_project_session_dir("/path/to/project")
    >>> # 写入会话元数据
    >>> write_meta("/path/to/project", "abc123", {"summary": "...", "updated_at": 0})
"""

from __future__ import annotations

import json
import re
import time
from hashlib import sha1
from pathlib import Path
from typing import Any

from illusion.config.paths import get_sessions_dir
from illusion.engine.messages import ConversationMessage
from illusion.utils.atomic_write import atomic_write_text

# 会话标题/摘要中的 fork 备份前缀（如 "[fork 3] 原标题"）
_FORK_PREFIX_RE = re.compile(r"^\[fork (\d+)\]")


class InvalidSessionIdError(ValueError):
    """会话 ID 非法（含路径遍历字符等）。"""


def _validate_session_id(session_id: str) -> None:
    """校验 session_id 合法性，防止路径遍历攻击。

    合法 session_id 应为纯目录名（hex 字符串），不含路径分隔符、
    上层目录引用或绝对路径前缀。

    Args:
        session_id: 待校验的会话 ID

    Raises:
        InvalidSessionIdError: 当 session_id 含非法字符时
    """
    if not session_id:
        raise InvalidSessionIdError("session_id 不能为空")
    # 拒绝路径遍历字符和路径分隔符
    if ".." in session_id or "/" in session_id or "\\" in session_id or "~" in session_id:
        raise InvalidSessionIdError(f"非法 session_id: {session_id!r}")
    # 拒绝绝对路径（Windows 盘符或 POSIX 绝对路径）
    if len(session_id) >= 2 and session_id[1] == ":":
        raise InvalidSessionIdError(f"非法 session_id: {session_id!r}")
    if session_id.startswith(("/", "\\")):
        raise InvalidSessionIdError(f"非法 session_id: {session_id!r}")


def get_project_session_dir(cwd: str | Path) -> Path:
    """返回项目的会话目录。"""
    path = Path(cwd).resolve()
    # 使用路径的 SHA1 哈希前 12 位作为目录名的一部分
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    session_dir = get_sessions_dir() / f"{path.name}-{digest}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_project_session_dir_no_create(cwd: str | Path) -> Path:
    """返回项目的会话目录路径，但不创建目录。

    公开 API：供只读/删除操作以及外部只读调用方使用，避免遗留空目录
    （违反懒创建约束）。需要立即创建目录的写路径请改用 get_project_session_dir。
    """
    path = Path(cwd).resolve()
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return get_sessions_dir() / f"{path.name}-{digest}"


def session_dir_for(cwd: str | Path, session_id: str) -> Path:
    """返回 {project}/{session_id}/ 会话目录（不创建）。

    统一路径计算入口：CheckpointStore 构造与所有"仅持有 cwd+session_id"
    的读路径都应经过此处，避免各调用方自行拼接导致目录不一致。
    """
    _validate_session_id(session_id)
    return get_project_session_dir_no_create(cwd) / session_id


def read_index(cwd: str | Path) -> dict[str, Any] | None:
    """读取父级 index.json。

    Args:
        cwd: 项目工作目录

    Returns:
        dict | None: {"latest_session_id": "...", "version": 1} 或 None
    """
    path = get_project_session_dir_no_create(cwd) / "index.json"
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def read_index_from(session_dir: Path) -> dict[str, Any] | None:
    """读取 session_dir 父级（项目级）index.json。

    供持有 CheckpointStore 的调用方使用：index 与 context.jsonl 的
    定位同源（session_dir 由 store 给出），避免自行重算路径。
    """
    path = session_dir.parent / "index.json"
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def write_index(cwd: str | Path, latest_session_id: str) -> None:
    """写入父级 index.json。

    Args:
        cwd: 项目工作目录
        latest_session_id: 最新活动的 session_id
    """
    path = get_project_session_dir(cwd) / "index.json"
    payload = {"latest_session_id": latest_session_id, "version": 1}
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def write_index_to(session_dir: Path, latest_session_id: str) -> None:
    """写入 session_dir 父级（项目级）index.json。

    供持有 CheckpointStore 的调用方使用：index 路径由 session_dir 派生，
    与 context.jsonl 定位同源，避免各写各的目录。
    """
    parent = session_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / "index.json"
    payload = {"latest_session_id": latest_session_id, "version": 1}
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def read_meta(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """读取 {session_id}/meta.json。

    Args:
        cwd: 项目工作目录
        session_id: 会话 ID

    Returns:
        dict | None: meta 字典或 None
    """
    return read_meta_from(session_dir_for(cwd, session_id), session_id)


def read_meta_from(session_dir: Path, session_id: str) -> dict[str, Any] | None:
    """读取 {session_dir}/meta.json（目录由 CheckpointStore 持有）。

    Args:
        session_dir: 会话数据目录（store.session_dir）
        session_id: 会话 ID

    Returns:
        dict | None: meta 字典或 None
    """
    _validate_session_id(session_id)
    path = session_dir / "meta.json"
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def write_meta(cwd: str | Path, session_id: str, meta: dict[str, Any]) -> None:
    """写入 {session_id}/meta.json。

    Args:
        cwd: 项目工作目录
        session_id: 会话 ID
        meta: 元数据字典
    """
    write_meta_to(session_dir_for(cwd, session_id), session_id, meta)


def write_meta_to(session_dir: Path, session_id: str, meta: dict[str, Any]) -> None:
    """写入 {session_dir}/meta.json（目录由 CheckpointStore 持有）。

    Args:
        session_dir: 会话数据目录（store.session_dir）
        session_id: 会话 ID
        meta: 元数据字典
    """
    _validate_session_id(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "meta.json"
    atomic_write_text(path, json.dumps(meta, indent=2) + "\n")


def list_session_snapshots(cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """列出项目的已保存会话，按最新优先排序。

    遍历 {session_id}/meta.json，按 updated_at 降序排序。
    过滤掉 message_count == 0 的空会话（兜底防御，正常流程不应产生空会话）。

    Args:
        cwd: 项目工作目录
        limit: 最大返回数量

    Returns:
        list[dict]: 会话元数据列表
    """
    session_dir = get_project_session_dir_no_create(cwd)
    if not session_dir.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for sub in session_dir.iterdir():
        if not sub.is_dir():
            continue
        meta_path = sub / "meta.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # 过滤空会话：无消息的会话不显示在列表中
        if data.get("message_count", 0) == 0:
            continue
        sessions.append({
            "session_id": data.get("session_id", sub.name),
            "summary": data.get("summary", ""),
            "message_count": data.get("message_count", 0),
            "turn_count": data.get("turn_count", 0),
            "model": data.get("model", ""),
            "created_at": data.get("created_at", 0),
            "updated_at": data.get("updated_at", 0),
            "title": data.get("title"),
        })
    sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
    return sessions[:limit]


def _is_real_turn_user_record(record: dict[str, Any]) -> bool:
    """判断 context.jsonl 的一行记录是否为"真实轮次"的用户消息。

    与前端轮次分组（build_replay_items 过滤 + useStableTurns 切分）口径
    一致：role=user、有非空文本、非后台任务完成通知、非 goal 注入消息。
    fork 截断与轮次大纲都以此为轮界。

    Args:
        record: 反序列化后的 JSONL 行

    Returns:
        bool: 是否为开轮的用户消息
    """
    if record.get("role") != "user":
        return False
    message = record.get("message")
    if not isinstance(message, dict):
        return False
    text = ""
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
    text = text.strip()
    if not text:
        return False
    from illusion.goal.prompts import is_goal_system_message
    from illusion.tasks.types import is_task_notification

    return not is_task_notification(text) and not is_goal_system_message(text)


def _fork_file_history(
    src_dir: Path,
    new_dir: Path,
    source_sid: str,
    new_sid: str,
    max_checkpoint_id: int | None = None,
) -> None:
    """复制文件历史状态与备份目录到新会话（fork 后 /rewind both 可用）。

    两部分缺一不可：
    - ``{会话目录}/file_history.json``：快照索引。内嵌的 session_id
      必须改写为新 id——load() 以它构建 FileHistoryState.session_id，
      rewind_to 用其定位备份目录，不改写会导致 fork 会话的文件回退
      误读源会话的备份。截断 fork 时（max_checkpoint_id 非 None）
      同步丢弃 checkpoint_id >= 上限的快照并重写 turn_counter，使
      落盘状态自洽——不能依赖载入方的 checkpoint_count 对齐，否则
      任何未对齐的载入路径（如 _ensure_file_history）都会让 rewind
      把文件恢复到 fork 点之后的状态；
    - ``~/.illusion/data/file-history/{sid}/``：文件内容备份。整目录
      复制（被裁掉快照引用的多余备份无害，后续 rewind 的清理逻辑
      会自然回收）。

    任一部分缺失/损坏都静默跳过：fork 会话降级为无文件历史（仅对话
    可回退），不阻断分叉主流程。

    Args:
        src_dir: 源会话数据目录
        new_dir: 新会话数据目录（已创建）
        source_sid: 源会话 ID
        new_sid: 新会话 ID
        max_checkpoint_id: 保留快照的 checkpoint_id 上界（不含）；
            None = 全量保留
    """
    import shutil as _shutil

    from illusion.config.paths import get_config_dir

    src_state = src_dir / "file_history.json"
    if src_state.is_file():
        try:
            data = json.loads(src_state.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            data = None
        if isinstance(data, dict):
            data["session_id"] = new_sid
            if max_checkpoint_id is not None:
                snapshots = [
                    s for s in data.get("snapshots", [])
                    if isinstance(s, dict)
                    and s.get("checkpoint_id", 0) < max_checkpoint_id
                ]
                data["snapshots"] = snapshots
                data["turn_counter"] = len(snapshots)
            try:
                atomic_write_text(
                    new_dir / "file_history.json",
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                )
            except OSError:
                pass
    src_backups = get_config_dir() / "file-history" / source_sid
    if src_backups.is_dir():
        try:
            _shutil.copytree(
                src_backups, get_config_dir() / "file-history" / new_sid,
                dirs_exist_ok=True,
            )
        except OSError:
            pass  # 备份复制失败不阻断 fork（rewind both 降级为仅回退对话）


def _next_fork_number(cwd: str | Path) -> int:
    """返回下一个可用的 fork 备份序号（全项目已有 "[fork N]" 最大值 + 1）。

    扫描项目全部会话 meta 的 title 与 summary（fork 无 title 时前缀写在
    summary 上，两处都要统计）。取最大而非计数，避免删除中间 fork 后
    序号复用产生重名。

    Args:
        cwd: 项目工作目录

    Returns:
        int: 新 fork 的序号（无任何已有 fork 时为 1）
    """
    project_dir = get_project_session_dir_no_create(cwd)
    if not project_dir.exists():
        return 1
    max_n = 0
    for sub in project_dir.iterdir():
        meta_path = sub / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for value in (data.get("title"), data.get("summary")):
            if isinstance(value, str):
                match = _FORK_PREFIX_RE.match(value.strip())
                if match:
                    max_n = max(max_n, int(match.group(1)))
    return max_n + 1


def _strip_fork_prefix(text: str) -> str:
    """去掉文本开头堆积的 "[fork N]" 前缀（fork of fork 场景只保留一层）。"""
    value = text.strip()
    while True:
        match = _FORK_PREFIX_RE.match(value)
        if not match:
            break
        value = value[match.end():].lstrip()
    return value


def _mark_meta_as_fork(meta: dict[str, Any], fork_no: int) -> None:
    """给 fork 会话的 meta 打上 "[fork N]" 备份前缀。

    有 title 加在 title 前（title 列表展示优先，且 /rename 前持续存在）；
    无 title 加在 summary 前（列表回退展示 summary）。源文本先剥离已
    有前缀，fork of fork 不产生 "[fork 3] [fork 2] ..." 叠加。
    """
    prefix = f"[fork {fork_no}] "
    title = str(meta.get("title") or "").strip()
    if title:
        meta["title"] = (prefix + _strip_fork_prefix(title))[:80]
        return
    summary = str(meta.get("summary") or "").strip()
    if summary:
        meta["summary"] = (prefix + _strip_fork_prefix(summary))[:80]


def fork_session(
    cwd: str | Path,
    source_session_id: str,
    turns_to_keep: int | None = None,
) -> str | None:
    """分叉会话：把源会话（可截断到前 N 轮）复制为一份新会话。

    逐行拷贝源 context.jsonl 到新 {sid}/ 目录；turns_to_keep=N 时在
    "第 N+1 个真实轮次的用户消息"处截断，并连同截去其轮首 _checkpoint
    行（checkpoint 由 query_engine 在每条用户消息前追加，紧邻 user 行），
    保证 fork 后 /rewind 的轮次计数不偏移。meta 基于源改写（新 id、
    时间戳、重算的消息/轮次计数、forked_from 溯源），title/summary 打上
    "[fork N]" 备份前缀（无 title 时写在 summary 前），文件历史
    （file_history.json + 备份目录）一并复制，fork 不争夺 index.json 的
    latest（激活/恢复新会话时才写入）。

    Args:
        cwd: 项目工作目录
        source_session_id: 源会话 ID
        turns_to_keep: 保留前 N 个真实轮次（None = 全量复制）

    Returns:
        str | None: 新会话 ID；源会话不存在时返回 None
    """
    _validate_session_id(source_session_id)
    src_dir = session_dir_for(cwd, source_session_id)
    src_file = src_dir / "context.jsonl"
    if not src_file.is_file():
        return None

    from uuid import uuid4

    new_session_id = uuid4().hex[:12]
    new_dir = get_project_session_dir(cwd) / new_session_id
    new_dir.mkdir(parents=True, exist_ok=True)

    kept_lines: list[str] = []
    message_count = 0
    turn_count = 0
    turns_seen = 0
    checkpoint_count = 0
    # 截断时需要回退的最后 一个_checkpoint 行位置（属于第 N+1 轮轮首）
    last_checkpoint_idx = -1
    truncated = False
    try:
        with open(src_file, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("role") == "_checkpoint":
                    last_checkpoint_idx = len(kept_lines)
                elif _is_real_turn_user_record(record):
                    turns_seen += 1
                    if turns_to_keep is not None and turns_seen > turns_to_keep:
                        truncated = True
                        break
                if record.get("role") in ("user", "assistant"):
                    message_count += 1
                if record.get("role") == "_checkpoint":
                    checkpoint_count += 1
                kept_lines.append(stripped)
    except OSError:
        # 读源失败：清理半成品目录，返回 None（不产生损坏的新会话）
        import shutil

        shutil.rmtree(new_dir, ignore_errors=True)
        return None

    if truncated and last_checkpoint_idx >= 0:
        # 截掉第 N+1 轮轮首的 _checkpoint 行（已拷入 kept_lines 尾部）
        kept_lines = kept_lines[:last_checkpoint_idx]
        checkpoint_count -= 1
        turn_count = turns_to_keep or 0
    else:
        turn_count = turns_seen

    with open(new_dir / "context.jsonl", "w", encoding="utf-8") as f:
        f.writelines(kept + "\n" for kept in kept_lines)

    now = time.time()
    meta: dict[str, Any] = dict(read_meta_from(src_dir, source_session_id) or {})
    meta.update({
        "session_id": new_session_id,
        "created_at": now,
        "updated_at": now,
        "message_count": message_count,
        "turn_count": turn_count,
        "forked_from": source_session_id,
    })
    # 备份标记：title/summary 加 "[fork N]" 前缀（N 为全项目 fork 序号）
    _mark_meta_as_fork(meta, _next_fork_number(cwd))
    write_meta_to(new_dir, new_session_id, meta)
    # 文件历史（快照索引 + 内容备份）一并复制并按保留的 checkpoint 截断，
    # fork 后 /rewind both 可用且不会恢复到 fork 点之后的状态
    _fork_file_history(
        src_dir, new_dir, source_session_id, new_session_id,
        max_checkpoint_id=checkpoint_count if truncated else None,
    )
    return new_session_id


def delete_session_by_id(cwd: str | Path, session_id: str) -> bool:
    """按 ID 删除特定会话（rmtree 整个 {sid}/ 目录）。

    Args:
        cwd: 项目工作目录
        session_id: 会话 ID

    Returns:
        bool: 是否成功删除
    """
    _validate_session_id(session_id)
    import shutil
    session_dir = session_dir_for(cwd, session_id)
    if session_dir.exists() and session_dir.is_dir():
        shutil.rmtree(session_dir)
        # 若删除的是 latest，更新或清空 index.json
        index = read_index(cwd)
        if index and index.get("latest_session_id") == session_id:
            sessions = list_session_snapshots(cwd, limit=1)
            if sessions:
                write_index(cwd, sessions[0]["session_id"])
            else:
                index_path = get_project_session_dir_no_create(cwd) / "index.json"
                if index_path.exists():
                    index_path.unlink()
        return True
    return False


def delete_all_sessions(cwd: str | Path) -> int:
    """删除项目的所有会话快照（rmtree 所有 {sid}/ 子目录 + 删 index.json）。

    Args:
        cwd: 项目工作目录

    Returns:
        int: 删除的会话数量
    """
    import shutil
    session_dir = get_project_session_dir_no_create(cwd)
    count = 0
    if not session_dir.exists():
        return count
    for sub in session_dir.iterdir():
        if sub.is_dir():
            shutil.rmtree(sub)
            count += 1
    index_path = session_dir / "index.json"
    if index_path.exists():
        index_path.unlink()
    return count


def export_session_markdown(
    *,
    cwd: str | Path,
    messages: list[ConversationMessage],
) -> Path:
    """将会话记录导出为 Markdown。"""
    session_dir = get_project_session_dir_no_create(cwd)
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "transcript.md"
    parts: list[str] = ["# IllusionAgent Session Transcript"]
    for message in messages:
        parts.append(f"\n## {message.role.capitalize()}\n")
        text = message.text.strip()
        if text:
            parts.append(text)
        for block in message.tool_uses:
            parts.append(f"\n```tool\n{block.name} {json.dumps(block.input, ensure_ascii=True)}\n```")
        for content_block in message.content:
            if getattr(content_block, "type", "") == "tool_result":
                parts.append(f"\n```tool-result\n{getattr(content_block, 'content', '')}\n```")
    atomic_write_text(path, "\n".join(parts).strip() + "\n")
    return path


def count_turns(messages: list[dict[str, Any]]) -> int:
    """统计消息列表中的轮次数

    一个轮次定义为一个非空的、非斜杠命令的用户消息。
    这与 /rewind 命令的定义一致。

    Args:
        messages: 消息列表

    Returns:
        int: 轮次数
    """
    turn_count = 0
    for msg in messages:
        if msg.get("role") == "user":
            # 获取消息文本
            text = ""
            if isinstance(msg.get("text"), str):
                text = msg["text"].strip()
            elif isinstance(msg.get("content"), list):
                # 从 content 数组中提取文本
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block.get("text", "")
                text = text.strip()

            # 统计非空用户消息（命令不进 messages；真实 / 前缀消息计入轮次）
            if text:
                turn_count += 1

    return turn_count


# ---------------------------------------------------------------------------
# Pending Question 持久化
# ---------------------------------------------------------------------------

def _pending_question_path(cwd: str | Path, session_id: str) -> Path:
    """返回指定会话的 pending question 文件路径"""
    session_dir = get_project_session_dir_no_create(cwd)
    return session_dir / f"pending-question-{session_id}.json"


def save_pending_question(
    *,
    cwd: str | Path,
    session_id: str,
    tool_use_id: str,
    questions: list[dict[str, Any]],
    question_text: str,
) -> Path:
    """保存待回答的 ask_user_question 问题

    Args:
        cwd: 工作目录
        session_id: 会话 ID
        tool_use_id: 触发问题的 tool_use ID（用于恢复时匹配 tool_result）
        questions: 结构化问题数据（list[dict]）
        question_text: 格式化后的问题文本（用于显示给用户）

    Returns:
        Path: 持久化文件路径
    """
    payload = {
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "questions": questions,
        "question_text": question_text,
        "created_at": time.time(),
    }
    path = _pending_question_path(cwd, session_id)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_pending_question(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """加载指定会话的 pending question

    Returns:
        dict | None: 问题数据，无则 None
    """
    path = _pending_question_path(cwd, session_id)
    if not path.exists():
        return None
    try:
        result: dict[str, Any] | None = json.loads(path.read_text(encoding="utf-8"))
        return result
    except (json.JSONDecodeError, OSError):
        return None


def delete_pending_question(cwd: str | Path, session_id: str) -> bool:
    """删除指定会话的 pending question

    Returns:
        bool: 是否成功删除
    """
    path = _pending_question_path(cwd, session_id)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Pending Plan Approval 持久化
# ---------------------------------------------------------------------------

def _pending_plan_approval_path(cwd: str | Path, session_id: str) -> Path:
    """返回指定会话的 pending plan approval 文件路径"""
    session_dir = get_project_session_dir_no_create(cwd)
    return session_dir / f"pending-plan-approval-{session_id}.json"


def save_pending_plan_approval(
    *,
    cwd: str | Path,
    session_id: str,
    plan: str,
    plan_path: str,
) -> Path:
    """保存待审批的计划内容（print 模式跨轮次审批机制）

    Args:
        cwd: 工作目录
        session_id: 会话 ID
        plan: 计划内容文本
        plan_path: 计划文件路径（用于恢复时引用）

    Returns:
        Path: 持久化文件路径
    """
    payload = {
        "session_id": session_id,
        "plan": plan,
        "plan_path": plan_path,
        "created_at": time.time(),
    }
    path = _pending_plan_approval_path(cwd, session_id)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_pending_plan_approval(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """加载指定会话的 pending plan approval

    Returns:
        dict | None: 计划审批数据，无则 None
    """
    path = _pending_plan_approval_path(cwd, session_id)
    if not path.exists():
        return None
    try:
        result: dict[str, Any] | None = json.loads(path.read_text(encoding="utf-8"))
        return result
    except (json.JSONDecodeError, OSError):
        return None


def delete_pending_plan_approval(cwd: str | Path, session_id: str) -> bool:
    """删除指定会话的 pending plan approval

    Returns:
        bool: 是否成功删除
    """
    path = _pending_plan_approval_path(cwd, session_id)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Pending Permission 持久化
# ---------------------------------------------------------------------------

def _pending_permission_path(cwd: str | Path, session_id: str) -> Path:
    """获取 pending-permission 文件路径

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID

    Returns:
        Path: pending-permission 文件路径
    """
    session_dir = get_project_session_dir_no_create(cwd)
    return session_dir / f"pending-permission-{session_id}.json"


def save_pending_permission(
    *,
    cwd: str | Path,
    session_id: str,
    tool_name: str,
    reason: str,
) -> Path:
    """保存 pending-permission 到会话目录

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID
        tool_name: 被请求权限的工具名称
        reason: 权限请求原因

    Returns:
        Path: 保存的文件路径
    """
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "reason": reason,
        "approved": False,
        "created_at": time.time(),
    }
    path = _pending_permission_path(cwd, session_id)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_pending_permission(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """加载 pending-permission

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID

    Returns:
        dict | None: pending-permission 数据，不存在返回 None
    """
    path = _pending_permission_path(cwd, session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def delete_pending_permission(cwd: str | Path, session_id: str) -> None:
    """删除 pending-permission

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID
    """
    path = _pending_permission_path(cwd, session_id)
    if path.exists():
        path.unlink()


def _pending_sandbox_path(cwd: str | Path, session_id: str) -> Path:
    """获取 pending-sandbox 文件路径

    print 模式下沙箱权限确认使用独立的 pending 文件，仅支持两选项
    （允许/拒绝），与通用权限（print 模式 Y/N，交互模式三选项）区分开。

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID

    Returns:
        Path: pending-sandbox 文件路径
    """
    session_dir = get_project_session_dir_no_create(cwd)
    return session_dir / f"pending-sandbox-{session_id}.json"


def save_pending_sandbox(
    *,
    cwd: str | Path,
    session_id: str,
    tool_name: str,
    reason: str,
) -> Path:
    """保存 pending-sandbox 到会话目录

    print 模式沙箱权限请求（两选项：允许/拒绝）。

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID
        tool_name: 被请求权限的工具名称
        reason: 权限请求原因

    Returns:
        Path: 保存的文件路径
    """
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "reason": reason,
        "approved": False,
        "created_at": time.time(),
    }
    path = _pending_sandbox_path(cwd, session_id)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_pending_sandbox(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """加载 pending-sandbox

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID

    Returns:
        dict | None: pending-sandbox 数据，不存在返回 None
    """
    path = _pending_sandbox_path(cwd, session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def delete_pending_sandbox(cwd: str | Path, session_id: str) -> None:
    """删除 pending-sandbox

    Args:
        cwd: 工作目录路径
        session_id: 会话 ID
    """
    path = _pending_sandbox_path(cwd, session_id)
    if path.exists():
        path.unlink()