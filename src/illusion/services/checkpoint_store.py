"""
检查点存储服务
==============

基于单文件 JSONL append-only 模式的会话持久化存储。

核心设计：
    - 3 种 role 行：_checkpoint / _usage / 普通消息
    - rewind 原地重写，restore 单遍扫描重建内存状态
    - 异步文件 I/O（aiofiles），避免阻塞事件循环

主要组件：
    - RestoreResult: restore 结果数据类
    - CheckpointStore: context.jsonl 读写管理器

使用示例：
    >>> store = CheckpointStore(Path("./.illusion/session"))
    >>> await store.append_message(message)
    >>> result = await store.restore()
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles

from illusion.api.usage import UsageSnapshot
from illusion.engine.messages import ConversationMessage


@dataclass
class RestoreResult:
    """CheckpointStore.restore() 的结果。

    Attributes:
        messages: 重建的对话消息列表
        usage_input: 最后一个 _usage 的 input_tokens（累积）
        usage_output: 最后一个 _usage 的 output_tokens（累积）
        usage_cache_read: 最后一个 _usage 的 cache_read_input_tokens（累积）
        usage_cache_creation: 最后一个 _usage 的 cache_creation_input_tokens（累积）
        last_usage: 最后一次 API 调用的单次用量（含缓存分项），无则 None
        last_usage_message_count: 最后一次 API 调用时的消息数快照
        checkpoint_count: _checkpoint 行数（用于 rewind 计数）
        goal_state: 最后一个 _goal 行的状态（goal 域 last-wins 快照），无则 None
    """
    messages: list[ConversationMessage]
    usage_input: int
    usage_output: int
    usage_cache_read: int
    usage_cache_creation: int
    last_usage: UsageSnapshot | None
    last_usage_message_count: int
    checkpoint_count: int
    goal_state: dict[str, Any] | None = None

    @classmethod
    def empty(cls) -> RestoreResult:
        """全零空结果（文件缺失 / 回退到头 / 加载降级等无历史场景）"""
        return cls(
            messages=[], usage_input=0, usage_output=0,
            usage_cache_read=0, usage_cache_creation=0,
            last_usage=None, last_usage_message_count=0,
            checkpoint_count=0,
        )


class CheckpointStore:
    """context.jsonl 的 append-only 持久化存储。

    单文件 JSONL，含 3 种 role：_checkpoint /
    _usage / 普通消息。append-only 保证崩溃安全，
    rewind 通过原地重写实现。

    Attributes:
        next_checkpoint_id: 下一个 _checkpoint 的 id（单调递增）
    """

    def __init__(self, session_dir: Path, session_id: str) -> None:
        """初始化 CheckpointStore。

        采用延迟创建策略：构造时不创建目录，第一次 _append_line 时才 mkdir。
        这样空会话（启动后未发消息）不会在磁盘留下空目录。

        Args:
            session_dir: 会话目录（含 context.jsonl）
            session_id: 会话 ID

        Raises:
            InvalidSessionIdError: 当 session_id 含路径遍历字符时
        """
        # 防御路径遍历：session_id 应为纯目录名
        from illusion.services.session_storage import _validate_session_id
        _validate_session_id(session_id)
        self._session_dir = session_dir
        self._session_id = session_id
        self._file = session_dir / "context.jsonl"
        self._io_lock = asyncio.Lock()
        self._next_checkpoint_id = 0
        self._dir_ensured = False  # 延迟创建标志

    @property
    def next_checkpoint_id(self) -> int:
        """返回下一个 checkpoint id。"""
        return self._next_checkpoint_id

    def align_checkpoint_id(self, disk_count: int) -> None:
        """按磁盘已有 checkpoint 数对齐 next_checkpoint_id。

        restore_messages 场景（Web/渠道每轮重建 runtime）：调用方已在
        外部完成 restore() 并传入消息，但本 store 是新建的、next 从 0
        起——若不对齐，后续 append 会写出重复 id 的 checkpoint 行，
        resume/rewind 按 id 定位时整体偏移（部分指令失效的根源）。

        Args:
            disk_count: 磁盘 context.jsonl 中已有的 checkpoint 行数
        """
        self._next_checkpoint_id = max(self._next_checkpoint_id, disk_count)

    def count_disk_checkpoints(self) -> int:
        """同步统计磁盘 context.jsonl 中已有的 checkpoint 行数。

        供 restore_messages 恢复路径的对齐兜底（文件缺失/损坏行跳过）。

        Returns:
            int: 已有的 checkpoint 行数（无文件时为 0）
        """
        if not self._file.exists():
            return 0
        count = 0
        try:
            with open(self._file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict) and record.get("role") == "_checkpoint":
                        count += 1
        except OSError:
            return 0
        return count

    @property
    def session_id(self) -> str:
        """返回会话 ID。"""
        return self._session_id

    @property
    def session_dir(self) -> Path:
        """返回会话数据目录（本 store 为唯一持有者）。

        会话内所有文件（context.jsonl / meta.json / file_history.json）
        均由此目录派生，调用方不得再用 cwd+session_id 自行重算路径，
        避免因 session_id 不同步导致文件散落到不同目录。
        """
        return self._session_dir

    async def append_checkpoint(self) -> int:
        """追加 _checkpoint 行，返回 checkpoint id。

        Returns:
            int: 新分配的 checkpoint id
        """
        checkpoint_id = self._next_checkpoint_id
        self._next_checkpoint_id += 1
        record = {"role": "_checkpoint", "id": checkpoint_id}
        await self._append_line(record)
        return checkpoint_id

    async def append_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        *,
        last_usage: UsageSnapshot | None = None,
        last_message_count: int = 0,
    ) -> None:
        """追加 _usage 行（累积值 + 最后一次调用的单次值）。

        Args:
            input_tokens: 累积 input tokens
            output_tokens: 累积 output tokens
            cache_read_input_tokens: 累积缓存命中 tokens
            cache_creation_input_tokens: 累积缓存写入 tokens
            last_usage: 最后一次 API 调用的单次用量（用于 rewind/resume 后恢复显示）
            last_message_count: 最后一次 API 调用时的消息数快照
        """
        record = {
            "role": "_usage",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
        }
        if last_usage is not None:
            record["last_input_tokens"] = last_usage.input_tokens
            record["last_output_tokens"] = last_usage.output_tokens
            record["last_cache_read_input_tokens"] = last_usage.cache_read_input_tokens
            record["last_cache_creation_input_tokens"] = (
                last_usage.cache_creation_input_tokens
            )
            record["last_message_count"] = last_message_count
        await self._append_line(record)

    async def append_message(self, message: ConversationMessage) -> None:
        """追加普通对话消息行。

        Args:
            message: 对话消息
        """
        record = {
            "role": message.role,
            "message": message.model_dump(mode="json"),
        }
        await self._append_line(record)

    async def append_goal(self, state: dict[str, Any] | None) -> None:
        """追加 _goal 行（goal 域 last-wins 快照；None 为 clear 墓碑）。

        Args:
            state: GoalManager.persisted_state() 的载荷，None 表示目标已清除
        """
        record: dict[str, Any] = {"role": "_goal", "state": state}
        await self._append_line(record)

    async def rewind_to(self, target_checkpoint_id: int) -> RestoreResult:
        """回退到指定 checkpoint id 之前的状态。

        保留 id < target_checkpoint_id 的 _checkpoint 及其后内容，
        原地重写 context.jsonl，返回重建后的 RestoreResult。

        Args:
            target_checkpoint_id: 目标 checkpoint id（该 id 及之后内容被丢弃）

        Returns:
            RestoreResult: 重建后的内存状态
        """
        async with self._io_lock:
            if not self._file.exists():
                return RestoreResult.empty()
            # 读所有行
            kept_lines: list[str] = []
            async with aiofiles.open(self._file, encoding="utf-8") as f:
                async for line in f:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # 命中目标 checkpoint → 停止拷贝
                    if (
                        record.get("role") == "_checkpoint"
                        and record.get("id") == target_checkpoint_id
                    ):
                        break
                    kept_lines.append(line)
            # 原地重写
            async with aiofiles.open(self._file, "w", encoding="utf-8") as f:
                for line in kept_lines:
                    await f.write(line + "\n")
            # 重置 next_checkpoint_id 并从保留行重建。
            # to_thread：全量 JSON 解析 + Pydantic 校验是 CPU 密集操作，
            # 长会话下直接在事件循环执行会阻塞所有会话收发；线程内仅读
            # kept_lines 快照并写 _next_checkpoint_id，后者由外层 _io_lock
            # 串行化保护，无线程安全问题（asyncio.Lock 本身不跨线程使用）
            self._next_checkpoint_id = 0
            return await asyncio.to_thread(self._build_result_from_lines, kept_lines)

    async def restore(self) -> RestoreResult:
        """单遍扫描 context.jsonl 重建内存状态。

        旧文件中的 _system_prompt / _system_overhead 行直接跳过（不解析）。

        Returns:
            RestoreResult: 重建后的内存状态
        """
        async with self._io_lock:
            if not self._file.exists():
                return RestoreResult.empty()
            lines: list[str] = []
            async with aiofiles.open(self._file, encoding="utf-8") as f:
                async for line in f:
                    line = line.rstrip("\n")
                    if line:
                        lines.append(line)
            # 重置 next_checkpoint_id
            self._next_checkpoint_id = 0
            # to_thread：全量 JSON 解析 + Pydantic 校验是 CPU 密集操作，
            # 渠道端每条消息都会 restore 长会话，直接在事件循环执行会阻塞
            # 所有并发会话收发；线程内仅读 lines 快照并写 _next_checkpoint_id，
            # 后者由外层 _io_lock 串行化保护（与 rewind_to 同范式）
            return await asyncio.to_thread(self._build_result_from_lines, lines)

    async def truncate_all(self) -> None:
        """清空 context.jsonl（用于 /new）。"""
        async with self._io_lock:
            if self._file.exists():
                self._file.unlink()
            self._next_checkpoint_id = 0

    async def rebuild_after_compact(
        self,
        messages: list[ConversationMessage],
        usage_input: int = 0,
        usage_output: int = 0,
        usage_cache_read: int = 0,
        usage_cache_creation: int = 0,
        *,
        last_usage: UsageSnapshot | None = None,
        last_message_count: int = 0,
    ) -> None:
        """压缩后重建 checkpoint：以压缩后的消息为新的持久化基线。

        压缩是不可逆的破坏性操作——旧消息已被摘要替代，append-only 的
        文件里若仍保留压缩前的完整消息，resume/rewind 会恢复到未压缩的
        对话。因此压缩后清空文件，写入压缩后消息 + 新的 checkpoint（id=0）。

        累积 usage 保留（CostTracker 不清零）。若压缩后（同一次 run_query 内）
        还有后续 API 调用，last_usage 保留其真实分项，resume 后状态栏
        立即恢复；否则为 None（回退估算）。

        Args:
            messages: 压缩后的消息列表
            usage_input: 累积 input tokens
            usage_output: 累积 output tokens
            usage_cache_read: 累积缓存命中 tokens
            usage_cache_creation: 累积缓存写入 tokens
            last_usage: 压缩后最后一次 API 调用的单次用量（若无后续调用则为 None）
            last_message_count: 该次调用时的消息数快照
        """
        # 原子重建：先写临时文件再 os.replace 替换。
        # 旧实现先 unlink 再 open("w")，且无跨进程锁——若与另一进程的
        # append 竞争失败，文件已被删除 → 0 字节数据丢失（会话恢复为空）。
        async with self._io_lock:
            self._session_dir.mkdir(parents=True, exist_ok=True)
            with self._cross_process_lock():
                tmp_file = self._file.with_suffix(self._file.suffix + ".tmp")
                async with aiofiles.open(tmp_file, "w", encoding="utf-8") as f:
                    await f.write(json.dumps({"role": "_checkpoint", "id": 0}) + "\n")
                    for msg in messages:
                        record = {
                            "role": msg.role,
                            "message": msg.model_dump(mode="json"),
                        }
                        await f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    usage_record: dict[str, Any] = {
                        "role": "_usage",
                        "input_tokens": usage_input,
                        "output_tokens": usage_output,
                        "cache_read_input_tokens": usage_cache_read,
                        "cache_creation_input_tokens": usage_cache_creation,
                    }
                    if last_usage is not None:
                        usage_record["last_input_tokens"] = last_usage.input_tokens
                        usage_record["last_output_tokens"] = last_usage.output_tokens
                        usage_record["last_cache_read_input_tokens"] = (
                            last_usage.cache_read_input_tokens
                        )
                        usage_record["last_cache_creation_input_tokens"] = (
                            last_usage.cache_creation_input_tokens
                        )
                        usage_record["last_message_count"] = last_message_count
                    await f.write(json.dumps(usage_record, ensure_ascii=False) + "\n")
                    # 确保文件句柄刷盘后再退出（关闭）——os.replace 要求 tmp
                    # 文件未被占用（Windows 上替换打开的文件会失败）
                    await f.flush()
                # 文件句柄已关闭，原子替换（失败时原文件保留，数据不丢）
                import os
                os.replace(tmp_file, self._file)
            self._next_checkpoint_id = 1

    async def _append_line(self, record: dict[str, Any]) -> None:
        """加锁追加一行 JSON。第一次调用时延迟创建会话目录。

        进程内用 _io_lock 互斥；跨进程（多实例/多标签页共用同一会话目录）
        用文件锁保护，避免并发 append 造成行交错损坏（损坏行在 restore
        时被跳过，表现为会话消息缺失/恢复后为空）。
        """
        async with self._io_lock:
            if not self._dir_ensured:
                self._session_dir.mkdir(parents=True, exist_ok=True)
                self._dir_ensured = True
            with self._cross_process_lock():
                async with aiofiles.open(self._file, "a", encoding="utf-8") as f:
                    await f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _cross_process_lock(self) -> Any:
        """跨进程文件锁（上下文管理器）。

        Windows 用 msvcrt.locking 锁 context.jsonl 首字节；POSIX 用
        fcntl.flock。锁与数据文件共用，避免额外文件；加锁失败或平台
        不支持时降级为无锁（保持原行为）。
        """
        import sys

        if sys.platform == "win32":
            import msvcrt

            class _WinLock:
                def __init__(self, path: Path) -> None:
                    self._fh: Any | None = None
                    # 独立锁文件：msvcrt 锁是字节范围锁，若直接锁数据文件，
                    # 同一进程内 aiofiles 写句柄与锁句柄会互相冲突
                    self._lock_path = path.with_suffix(path.suffix + ".lock")

                def __enter__(self) -> None:
                    try:
                        self._fh = self._lock_path.open("a+b")
                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                    except OSError:
                        if self._fh is not None:
                            self._fh.close()
                            self._fh = None

                def __exit__(self, *exc: object) -> None:
                    if self._fh is not None:
                        try:
                            self._fh.seek(0)
                            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                        self._fh.close()

            return _WinLock(self._file)

        try:
            import fcntl

            class _PosixLock:
                def __init__(self, path: Path) -> None:
                    self._fh: Any | None = None
                    # 独立锁文件（与 Windows 分支一致）
                    self._lock_path = path.with_suffix(path.suffix + ".lock")

                def __enter__(self) -> None:
                    try:
                        self._fh = self._lock_path.open("a")
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
                    except OSError:
                        if self._fh is not None:
                            self._fh.close()
                            self._fh = None

                def __exit__(self, *exc: object) -> None:
                    if self._fh is not None:
                        try:
                            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                        except OSError:
                            pass
                        self._fh.close()

            return _PosixLock(self._file)
        except ImportError:
            import contextlib
            return contextlib.nullcontext()

    def _build_result_from_lines(self, lines: list[str]) -> RestoreResult:
        """从 JSONL 行列表构建 RestoreResult（无锁，内部使用）。

        旧文件中的 _system_prompt / _system_overhead 行直接跳过（不读取）。

        Args:
            lines: JSON 字符串列表

        Returns:
            RestoreResult: 重建后的状态
        """
        messages: list[ConversationMessage] = []
        usage_input = 0
        usage_output = 0
        usage_cache_read = 0
        usage_cache_creation = 0
        last_usage: UsageSnapshot | None = None
        last_usage_message_count = 0
        checkpoint_count = 0
        goal_state: dict[str, Any] | None = None

        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = record.get("role")
            if role in ("_system_prompt", "_system_overhead"):
                # 旧文件遗留行：直接跳过
                continue
            elif role == "_checkpoint":
                checkpoint_count += 1
                self._next_checkpoint_id = record.get("id", -1) + 1
            elif role == "_usage":
                usage_input = record.get("input_tokens", 0)
                usage_output = record.get("output_tokens", 0)
                usage_cache_read = record.get("cache_read_input_tokens", 0)
                usage_cache_creation = record.get("cache_creation_input_tokens", 0)
                # 最后一次 API 调用的单次分项（rewind/resume 后恢复 StatusBar 显示）
                if "last_input_tokens" in record:
                    last_usage = UsageSnapshot(
                        input_tokens=record.get("last_input_tokens", 0),
                        output_tokens=record.get("last_output_tokens", 0),
                        cache_read_input_tokens=record.get(
                            "last_cache_read_input_tokens", 0
                        ),
                        cache_creation_input_tokens=record.get(
                            "last_cache_creation_input_tokens", 0
                        ),
                    )
                    last_usage_message_count = record.get("last_message_count", 0)
            elif role == "_goal":
                # goal 域 last-wins 快照：最后一个 _goal 行生效
                # （state 为 None 表示 clear 墓碑——无目标）
                goal_state = record.get("state")
            elif role in ("user", "assistant"):
                msg_data = record.get("message")
                if msg_data:
                    try:
                        messages.append(ConversationMessage.model_validate(msg_data))
                    except Exception as e:  # noqa: BLE001
                        # 损坏的消息行跳过，避免影响整次 restore：
                        # 跨进程并发写可能产生结构不完整的行（JSON 合法但
                        # 字段缺失/类型错误），单行损坏不应让整个会话恢复失败
                        logging.getLogger(__name__).warning(
                            "跳过损坏的 %s 消息行: %s", role, e
                        )

        return RestoreResult(
            messages=messages,
            usage_input=usage_input,
            usage_output=usage_output,
            usage_cache_read=usage_cache_read,
            usage_cache_creation=usage_cache_creation,
            last_usage=last_usage,
            last_usage_message_count=last_usage_message_count,
            checkpoint_count=checkpoint_count,
            goal_state=goal_state,
        )
