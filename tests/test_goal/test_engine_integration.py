"""checkpoint 持久化、round driver 与 /goal 命令测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from illusion.engine.messages import ConversationMessage
from illusion.engine.query_engine import QueryEngine
from illusion.goal.manager import GoalManager
from illusion.goal.prompts import render_goal_round_prompt
from illusion.goal.types import GoalSettings
from illusion.services.checkpoint_store import CheckpointStore


# ---------------------------------------------------------------------------
# checkpoint _goal 行持久化
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> CheckpointStore:
    return CheckpointStore(tmp_path / "session", "test-session")


async def _restore(store: CheckpointStore):
    return await store.restore()


def test_goal_row_roundtrip(store: CheckpointStore) -> None:
    async def run() -> None:
        manager = GoalManager(GoalSettings(default_max_goal_rounds=4))
        manager.current_source = "human"
        manager.create("objective")
        await store.append_goal(manager.persisted_state())

        result = await store.restore()
        assert result.goal_state is not None
        assert result.goal_state["snapshot"]["objective"] == "objective"

        # clear 墓碑
        manager.current_source = "human"
        manager.clear()
        await store.append_goal(manager.persisted_state())
        result = await store.restore()
        assert result.goal_state is None

    asyncio.run(run())


def test_goal_row_last_wins(store: CheckpointStore) -> None:
    async def run() -> None:
        manager = GoalManager(GoalSettings(default_max_goal_rounds=4))
        manager.current_source = "human"
        manager.create("v1")
        await store.append_goal(manager.persisted_state())
        manager.create  # noqa: B018 (intentional no-op for clarity)
        # 编辑后再次落盘 → last-wins
        view = manager.get_view()
        assert view is not None
        manager.edit(view.snapshot.id, view.snapshot.revision, objective="v2")
        await store.append_goal(manager.persisted_state())
        result = await store.restore()
        assert result.goal_state is not None
        assert result.goal_state["snapshot"]["objective"] == "v2"

    asyncio.run(run())


def test_goal_row_rewind_compatible(store: CheckpointStore) -> None:
    async def run() -> None:
        manager = GoalManager(GoalSettings(default_max_goal_rounds=4))
        manager.current_source = "human"
        manager.create("objective")
        await store.append_checkpoint()  # id 0
        await store.append_message(ConversationMessage.from_user_text("hello"))
        await store.append_checkpoint()  # id 1
        await store.append_message(ConversationMessage.from_user_text("world"))
        await store.append_goal(manager.persisted_state())

        # rewind 到 checkpoint 1：丢弃其后的内容（含 _goal 行）→ 恢复无目标
        result = await store.rewind_to(1)
        assert result.goal_state is None
        assert len(result.messages) == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# round driver（drive_goal_rounds）
# ---------------------------------------------------------------------------


class _FakeApiClient:
    """最小 API 客户端桩：不回调用具，直接产出一句最终文本。"""

    async def stream_message(self, *args, **kwargs):
        from illusion.api.client import ApiMessageCompleteEvent
        from illusion.api.usage import UsageSnapshot
        from illusion.engine.messages import ConversationMessage as CM
        from illusion.engine.messages import TextBlock

        msg = CM(role="assistant", content=[TextBlock(text="working")])
        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


class _FakeEngine(QueryEngine):
    """绕过 __init__ 的轻量桩（仅跑 drive_goal_rounds 需要的部分）。"""

    def __init__(self, manager: GoalManager | None, cwd: str = ".") -> None:
        self._goal_manager = manager
        self._cwd = Path(cwd)
        self._messages: list[ConversationMessage] = []
        self._max_turns = 10
        self._checkpoint_store = None
        from illusion.engine.cost_tracker import CostTracker

        self._cost_tracker = CostTracker()
        self._last_api_usage = None
        self._last_api_usage_message_count = 0
        self._api_client = _FakeApiClient()
        from illusion.tools.base import ToolRegistry

        self._tool_registry = ToolRegistry()
        self._permission_checker = None
        self._system_prompt = "system"
        self._model = "fake-model"
        self._max_tokens = 1024
        self._permission_prompt = None
        self._ask_user_prompt = None
        self._plan_approval_prompt = None
        self._print_mode = False
        self._sandbox_permission_prompt = None
        self._hook_executor = None
        self._tool_metadata: dict = {}
        self._effort = None
        self._bg_agent_tracker = None
        self._compact_state = None
        self._file_history = None
        self._file_state_cache = None
        self._session_id = ""


@pytest.mark.asyncio
async def test_drive_rounds_injects_goal_round_messages() -> None:
    manager = GoalManager(GoalSettings(default_max_goal_rounds=3))
    manager.current_source = "human"
    manager.create("do the thing")
    engine = _FakeEngine(manager)

    from illusion.engine.stream_events import GoalStatusEvent

    events = [ev async for ev in engine.drive_goal_rounds()]
    # 3 轮各注入 <goal_round> 用户消息 + GoalStatusEvent(round)
    goal_rounds = [
        m for m in engine.messages
        if m.role == "user" and m.text.startswith("<goal_round>")
    ]
    assert len(goal_rounds) == 3
    assert manager.rounds_started == 3
    rounds = [e for e in events if isinstance(e, GoalStatusEvent) and e.kind == "round"]
    assert [r.round for r in rounds] == [1, 2, 3]
    assert all(r.max_rounds == 3 for r in rounds)
    # 消息内容与渲染函数输出一致（含内嵌的精确 CAS ref）
    snap = manager.snapshot
    assert snap is not None
    assert goal_rounds[0].text == render_goal_round_prompt(
        "do the thing", 1, 3, goal_id=snap.id, revision=1
    )
    assert f"id={snap.id} revision=1" in goal_rounds[0].text
    # 轮次耗尽：round-limit 自动受阻 + limit 事件
    assert snap.phase == "blocked"
    assert snap.blocked_reason is not None
    assert snap.blocked_reason.code == "round-limit"
    assert any(isinstance(e, GoalStatusEvent) and e.kind == "limit" for e in events)


@pytest.mark.asyncio
async def test_drive_rounds_stops_when_disarmed() -> None:
    manager = GoalManager(GoalSettings(default_max_goal_rounds=5))
    manager.current_source = "human"
    manager.create("do the thing")
    engine = _FakeEngine(manager)
    manager.disarm()
    events = [ev async for ev in engine.drive_goal_rounds()]
    assert events == []
    assert manager.rounds_started == 0


@pytest.mark.asyncio
async def test_wrapup_injected_then_stops() -> None:
    from illusion.goal.types import PendingWrapup

    manager = GoalManager(GoalSettings(default_max_goal_rounds=5))
    manager.current_source = "human"
    manager.create("do the thing")
    view = manager.get_view()
    assert view is not None
    manager.complete(view.snapshot.id, view.snapshot.revision)
    manager.set_pending_wrapup(PendingWrapup(kind="complete", objective="do the thing"))
    engine = _FakeEngine(manager)

    await _drain(engine.drive_goal_rounds())
    wrapup = [
        m for m in engine.messages
        if m.role == "user" and m.text.startswith("<goal_complete>")
    ]
    assert len(wrapup) == 1
    # 终态后不再注入 goal round
    assert not any(m.text.startswith("<goal_round>") for m in engine.messages)


@pytest.mark.asyncio
async def test_drive_rounds_emits_wrapup_event() -> None:
    from illusion.engine.stream_events import GoalStatusEvent
    from illusion.goal.types import PendingWrapup

    manager = GoalManager(GoalSettings(default_max_goal_rounds=5))
    manager.current_source = "human"
    manager.create("do the thing")
    view = manager.get_view()
    assert view is not None
    manager.complete(view.snapshot.id, view.snapshot.revision)
    manager.set_pending_wrapup(PendingWrapup(kind="complete", objective="do the thing"))
    engine = _FakeEngine(manager)

    events = [ev async for ev in engine.drive_goal_rounds()]
    wrapups = [e for e in events if isinstance(e, GoalStatusEvent) and e.kind == "wrapup"]
    assert len(wrapups) == 1
    assert wrapups[0].phase == "complete"


async def _drain(agen) -> None:
    async for _ in agen:
        pass


# ---------------------------------------------------------------------------
# /goal 命令
# ---------------------------------------------------------------------------


class _FakeCommandEngine:
    """/goal 命令 handler 所需的最小引擎桩。"""

    def __init__(self, manager: GoalManager | None) -> None:
        self._goal_manager = manager
        self.messages: list[ConversationMessage] = []

    async def record_goal_command(self, text: str) -> None:
        """模拟 goal 命令原文入库（真实引擎会附加持久化）。"""
        self.messages.append(ConversationMessage.from_user_text(text))


def test_goal_command_create_and_status() -> None:
    from illusion.commands.goal import goal_handler
    from illusion.commands.types import CommandContext

    manager = GoalManager(GoalSettings(default_max_goal_rounds=5))
    engine = _FakeCommandEngine(manager)
    ctx = CommandContext(engine=engine)  # type: ignore[arg-type]

    async def run() -> None:
        result = await goal_handler("build the thing", ctx)
        assert result.drive_goal is True
        assert manager.snapshot is not None
        assert manager.snapshot.objective == "build the thing"
        assert manager.activation == "armed"
        # /goal 命令原文作为真实 user 消息入库（渲染/标题/轮次素材）
        assert [m.text for m in engine.messages] == ["/goal build the thing"]

        result = await goal_handler("", ctx)
        assert "Objective: build the thing" in (result.message or "")
        # 状态查看不重复入库
        assert len(engine.messages) == 1

        result = await goal_handler("pause", ctx)
        assert manager.snapshot is not None and manager.snapshot.phase == "paused"
        assert result.drive_goal is False

        result = await goal_handler("resume", ctx)
        assert manager.snapshot is not None and manager.snapshot.phase == "active"
        assert result.drive_goal is True

        result = await goal_handler("edit new objective", ctx)
        assert manager.snapshot is not None
        assert manager.snapshot.objective == "new objective"

        result = await goal_handler("clear", ctx)
        assert manager.snapshot is None

    asyncio.run(run())


def test_goal_command_no_goal() -> None:
    from illusion.commands.goal import goal_handler
    from illusion.commands.types import CommandContext

    manager = GoalManager()
    engine = _FakeCommandEngine(manager)
    ctx = CommandContext(engine=engine)  # type: ignore[arg-type]

    async def run() -> None:
        result = await goal_handler("", ctx)
        assert result.message == "No goal is currently set."
        result = await goal_handler("pause", ctx)
        assert "No goal is currently set." in (result.message or "")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# record_goal_command 持久化与轮次语义（真实引擎 + CheckpointStore）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_command_persisted_and_turn_count(tmp_path: Path) -> None:
    """/goal 命令原文作为真实 user 消息持久化，且不叠加 <goal_round> 轮次。

    集成验证（真实 QueryEngine + CheckpointStore）：
    - context.jsonl 中 /goal 命令原文恰好一行，且先于首个 <goal_round> 行
    - 驱动 goal 轮次后 turn_count 只计真实用户输入（/goal 1 轮），
      <goal_round> 注入消息不叠加（回归：之前直接加成 goal 轮次）
    """
    from illusion.commands.goal import goal_handler
    from illusion.commands.types import CommandContext
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import session_dir_for

    manager = GoalManager(GoalSettings(default_max_goal_rounds=2))
    engine = _FakeEngine(manager, cwd=str(tmp_path))
    store = CheckpointStore(session_dir_for(str(tmp_path), "goal-test"), "goal-test")
    engine._checkpoint_store = store

    async def run() -> None:
        ctx = CommandContext(engine=engine)  # type: ignore[arg-type]
        # 1. 执行 /goal 创建命令：命令原文入库
        result = await goal_handler("实现登录功能", ctx)
        assert result.drive_goal is True
        recorded = [m.text for m in engine.messages if m.role == "user"]
        assert recorded == ["/goal 实现登录功能"]

        # 2. 驱动 goal 自动续跑（2 轮注入 <goal_round>）
        async for _ in engine.drive_goal_rounds():
            pass
        user_texts = [m.text for m in engine.messages if m.role == "user"]
        assert user_texts[0] == "/goal 实现登录功能"
        assert sum(1 for t in user_texts if t.startswith("<goal_round>")) == 2

        # 3. checkpoint 持久化顺序：/goal 行在首个 <goal_round> 行之前，仅一行
        jsonl = store.session_dir.joinpath("context.jsonl").read_text(encoding="utf-8")
        round_idx = jsonl.find("<goal_round>")
        assert round_idx != -1
        goal_idx = jsonl.find("/goal 实现登录功能")
        assert goal_idx != -1 and goal_idx < round_idx
        assert jsonl.count("/goal 实现登录功能") == 1

        # 4. 轮次语义：turn_count 只计真实用户输入（/goal 1 轮），
        #    <goal_round> 不叠加（与 runtime._update_session_meta 同口径的最小复刻）
        from illusion.goal.prompts import is_goal_system_message
        from illusion.tasks.types import is_task_notification

        turn_count = sum(
            1
            for m in engine.messages
            if m.role == "user"
            and m.text.strip()
            and not is_task_notification(m.text)
            and not is_goal_system_message(m.text)
        )
        assert turn_count == 1

    await run()


@pytest.mark.asyncio
async def test_drive_goal_rounds_no_extra_checkpoint_for_command_first_session(
    tmp_path: Path,
) -> None:
    """命令优先会话的 goal 轮次驱动不追加额外 checkpoint。

    回归：首条消息即 /goal 时，file_history 初始快照若在
    drive_goal_rounds 中再 append 一次 checkpoint，checkpoint 数会大于
    用户可见轮数，rewind 的 turns 计数整体偏移（回退第一条消息需两次）。
    快照应复用 record_goal_command 已建立的最近 checkpoint。
    """
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import session_dir_for

    manager = GoalManager(GoalSettings(default_max_goal_rounds=1))
    manager.current_source = "human"
    manager.create("do the thing")
    engine = _FakeEngine(manager, cwd=str(tmp_path))
    store = CheckpointStore(session_dir_for(str(tmp_path), "goal-no-extra"), "goal-no-extra")
    engine._checkpoint_store = store

    # 命令优先路径：record_goal_command 建立 /goal 轮的唯一 checkpoint(id=0)
    await engine.record_goal_command("/goal do the thing")

    [ev async for ev in engine.drive_goal_rounds()]

    # checkpoint 数必须等于用户可见轮数（=1）：快照复用 id0，无新增行
    ids = []
    for line in store.session_dir.joinpath("context.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("role") == "_checkpoint":
                ids.append(record["id"])
    assert ids == [0], f"应只有 /goal 命令的 checkpoint(id=0)，实际 {ids}"
    assert store.next_checkpoint_id == 1


@pytest.mark.asyncio
async def test_goal_command_sets_checkpoint_boundary(tmp_path: Path) -> None:
    """/goal 命令路径显式建 checkpoint 边界：rewind 可回退到 /goal 之前。

    回归：命令路径不经过 submit_message 的 checkpoint 创建，若 record_goal_command
    不 append_checkpoint，rewind 1 轮会按 checkpoint 计数回退并越过 /goal
    直接删到第一条普通消息。
    """
    from illusion.commands.goal import goal_handler
    from illusion.commands.types import CommandContext
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import session_dir_for

    manager = GoalManager(GoalSettings(default_max_goal_rounds=2))
    engine = _FakeEngine(manager, cwd=str(tmp_path))
    store = CheckpointStore(session_dir_for(str(tmp_path), "goal-boundary"), "goal-boundary")
    engine._checkpoint_store = store

    async def run() -> None:
        # 第一条普通消息（真实用户轮次，走 submit_message 的 checkpoint 边界）
        await store.append_checkpoint()  # id 0
        await store.append_message(ConversationMessage.from_user_text("第一条普通消息"))
        # /goal 创建命令
        ctx = CommandContext(engine=engine)  # type: ignore[arg-type]
        result = await goal_handler("目标事项", ctx)
        assert result.drive_goal is True
        # record_goal_command 应建立新的 checkpoint 边界（id 1）
        assert store.next_checkpoint_id == 2, (
            f"/goal 应建立独立 checkpoint 边界，实际 next_checkpoint_id={store.next_checkpoint_id}"
        )

        # rewind 1 轮 → 回退到 /goal 之前：第一条普通消息保留
        restored = await store.rewind_to(1)
        restored_texts = [m.text for m in restored.messages]
        assert "/goal 目标事项" not in restored_texts
        assert "第一条普通消息" in restored_texts

    await run()


@pytest.mark.asyncio
async def test_last_assistant_text_anchored_after_goal_injection(tmp_path: Path) -> None:
    """/goal 开局晚于普通消息时，FINAL_RESPONSE 锚定 goal 轮次内。

    回归（对应验证器 FINAL_RESPONSE 检查 bug）：会话首条为普通消息、后续
    才 /goal 创建时，若 goal 轮 assistant 文本为空（纯工具轮），旧实现会
    回退到第一条普通消息的旧回复作为 FINAL_RESPONSE，导致验证永远失败。
    """
    from illusion.goal.verifier import _last_assistant_text

    messages = [
        ConversationMessage.from_user_text("帮我整理项目结构"),
        ConversationMessage(role="assistant", content=[], text="这是第一条消息的旧回复"),
        ConversationMessage.from_user_text("/goal 实现登录"),
        ConversationMessage.from_user_text("<goal_round>Round 1/2</goal_round>"),
        # goal 轮 assistant 仅工具调用、无文本输出
        ConversationMessage(role="assistant", content=[]),
    ]
    result = _last_assistant_text(_FakeMsgEngine(messages))
    # 空文本 assistant 是 goal 轮次内的最后一条，不应回退到旧回复
    assert result == ""


class _FakeMsgEngine:
    """仅暴露 messages 属性的引擎桩（供 _last_assistant_text 测试）。"""

    def __init__(self, messages: list[ConversationMessage]) -> None:
        self.messages = messages


# ---------------------------------------------------------------------------
# GoalManager 生命周期（full_reset / restore_from）
# ---------------------------------------------------------------------------


def test_engine_goal_lifecycle() -> None:
    manager = GoalManager(GoalSettings(default_max_goal_rounds=5))
    manager.current_source = "human"
    manager.create("objective")
    assert manager.snapshot is not None

    manager.reset()
    assert manager.snapshot is None
    assert manager.activation == "disarmed"

    manager.restore_from({
        "snapshot": {
            "id": "goal-x",
            "revision": 2,
            "objective": "restored",
            "phase": "active",
            "max_goal_rounds": 5,
        },
        "rounds_started": 1,
        "created_at": 1.0,
        "updated_at": 1.0,
    })
    assert manager.snapshot is not None
    assert manager.snapshot.objective == "restored"
    assert manager.rounds_started == 1
    assert manager.activation == "disarmed"  # 恢复后恒 disarmed
    assert not manager.should_continue()
