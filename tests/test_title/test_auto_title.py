"""会话自动标题模块测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from illusion.engine.messages import ConversationMessage
from illusion.title.auto_title import (
    _clean_title,
    _extract_title_source,
    _goal_objective,
    _title_system_prompt,
    _user_messages,
    _write_title_meta,
    maybe_schedule_title,
)


class FakeEngine:
    """最小 QueryEngine 桩：仅提供标题生成所需字段。"""

    def __init__(
        self,
        cwd: str | Path,
        messages: list[ConversationMessage] | None = None,
        goal_manager: object | None = None,
    ) -> None:
        self.cwd = str(cwd)
        self._messages = messages if messages is not None else []
        self.goal_manager = goal_manager
        self.checkpoint_store = SimpleNamespace(session_id="ses123")
        self.api_client = None
        self.model = "m"
        self._is_memory_subagent = False
        self._is_title_subagent = False

    @property
    def messages(self) -> list[ConversationMessage]:
        return self._messages


def _enable_title(monkeypatch, tmp_path: Path, enabled: bool = True) -> None:
    """写入 settings.json 开启自动标题（模拟用户显式开启）。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"title": {"enabled": enabled}}),
        encoding="utf-8",
    )


def _capture_tasks(monkeypatch) -> list:
    """用记录式 create_task 替换，避免真正触发后台模型调用。"""
    captured: list = []

    def fake_create_task(coro):
        captured.append(coro)
        try:
            coro.close()  # 不运行：既满足断言，又避免 "never awaited" 告警
        except Exception:
            pass
        return SimpleNamespace()

    monkeypatch.setattr("illusion.title.auto_title.asyncio.create_task", fake_create_task)
    return captured


# ---- 组件级测试 ----

def test_clean_title_strips_thinking_and_takes_first_line():
    assert _clean_title("Debugging production 500 errors") == "Debugging production 500 errors"
    assert _clean_title("<thinking>hmm</thinking>Refactoring user service") == "Refactoring user service"
    assert _clean_title("\n  App.js failure investigation\n") == "App.js failure investigation"
    assert _clean_title("") == ""
    assert _clean_title("  \n  \n  ") == ""


def test_title_system_prompt_contains_core_rules():
    prompt = _title_system_prompt()
    assert "thread title" in prompt
    assert "50 characters" in prompt
    assert "<examples>" in prompt
    assert "same language" in prompt


def test_user_messages_filters_goal_injection_only():
    # 命令不进 engine.messages（handle_line 拦截）；messages 中以 / 开头的
    # user 消息都是真实用户输入（如未知命令落入文本通道），应被收集
    engine = FakeEngine(
        ".",
        [
            ConversationMessage.from_user_text("你好"),
            ConversationMessage.from_user_text("/feedback完全删掉"),
            ConversationMessage.from_user_text("<goal_round>1/2</goal_round> 目标"),
        ],
    )
    assert _user_messages(engine) == ["你好", "/feedback完全删掉"]


def test_extract_title_source_falls_back_to_goal():
    # 首条为 goal harness 注入消息（<goal_round>，非真实用户输入）：
    # 无真实用户消息，回退到 goal objective
    goal = SimpleNamespace(snapshot=SimpleNamespace(objective="实现登录功能"))
    engine = FakeEngine(".", [ConversationMessage.from_user_text("<goal_round>1/2</goal_round> 目标")], goal_manager=goal)
    assert _extract_title_source(engine) == "实现登录功能"


def test_extract_title_source_uses_goal_command_text():
    # /goal 命令原文已作为真实 user 消息入库（record_goal_command）：
    # 标题素材应捕获 /goal xxx 原文本身，而非回退到 goal objective
    goal = SimpleNamespace(snapshot=SimpleNamespace(objective="实现登录功能"))
    engine = FakeEngine(
        ".",
        [ConversationMessage.from_user_text("/goal 实现登录功能，支持第三方登录")],
        goal_manager=goal,
    )
    assert _extract_title_source(engine) == "/goal 实现登录功能，支持第三方登录"


def test_goal_objective_absent_returns_empty():
    engine = FakeEngine(".", [ConversationMessage.from_user_text("hi")], goal_manager=None)
    assert _goal_objective(engine) == ""


# ---- 调度守卫测试 ----

@pytest.mark.asyncio
async def test_schedule_skipped_when_disabled(tmp_path, monkeypatch):
    _enable_title(monkeypatch, tmp_path, enabled=False)
    captured = _capture_tasks(monkeypatch)
    engine = FakeEngine(str(tmp_path), [ConversationMessage.from_user_text("你好")])
    maybe_schedule_title(engine)
    assert captured == []


@pytest.mark.asyncio
async def test_schedule_skipped_for_title_subagent(tmp_path, monkeypatch):
    _enable_title(monkeypatch, tmp_path)
    captured = _capture_tasks(monkeypatch)
    engine = FakeEngine(str(tmp_path), [ConversationMessage.from_user_text("你好")])
    engine._is_title_subagent = True
    maybe_schedule_title(engine)
    assert captured == []


@pytest.mark.asyncio
async def test_schedule_skipped_when_session_already_titled(tmp_path, monkeypatch):
    _enable_title(monkeypatch, tmp_path)
    captured = _capture_tasks(monkeypatch)
    from illusion.services.session_storage import write_meta

    write_meta(str(tmp_path), "ses123", {"title": "手动命名"})
    engine = FakeEngine(str(tmp_path), [ConversationMessage.from_user_text("你好")])
    maybe_schedule_title(engine)
    assert captured == []


@pytest.mark.asyncio
async def test_schedule_skipped_past_first_turn(tmp_path, monkeypatch):
    _enable_title(monkeypatch, tmp_path)
    captured = _capture_tasks(monkeypatch)
    engine = FakeEngine(
        str(tmp_path),
        [
            ConversationMessage.from_user_text("第一条"),
            ConversationMessage.from_user_text("第二条"),
        ],
    )
    maybe_schedule_title(engine)
    assert captured == []


@pytest.mark.asyncio
async def test_schedule_skipped_when_no_source(tmp_path, monkeypatch):
    # 仅 goal harness 注入消息（非真实用户输入）且 goal objective 尚未落地
    # → 素材为空，留待后续回合
    _enable_title(monkeypatch, tmp_path)
    captured = _capture_tasks(monkeypatch)
    engine = FakeEngine(str(tmp_path), [ConversationMessage.from_user_text("<goal_round>1/2</goal_round> 目标")], goal_manager=None)
    maybe_schedule_title(engine)
    assert captured == []


@pytest.mark.asyncio
async def test_schedule_schedules_on_first_real_turn(tmp_path, monkeypatch):
    _enable_title(monkeypatch, tmp_path)
    captured = _capture_tasks(monkeypatch)
    engine = FakeEngine(str(tmp_path), [ConversationMessage.from_user_text("检查当前 git 状态")])
    maybe_schedule_title(engine)
    assert len(captured) == 1


# ---- 写入守卫测试 ----

@pytest.mark.asyncio
async def test_write_title_meta_skips_when_already_titled(tmp_path):
    from illusion.services.session_storage import write_meta

    write_meta(str(tmp_path), "ses123", {"title": "已有标题"})
    engine = FakeEngine(str(tmp_path))
    wrote = await _write_title_meta(engine, "ses123", "新标题")
    assert wrote is False
    from illusion.services.session_storage import read_meta

    assert read_meta(str(tmp_path), "ses123")["title"] == "已有标题"


@pytest.mark.asyncio
async def test_write_title_meta_writes_when_no_title(tmp_path):
    # 模拟真实会话：已有会话目录与 meta（后台标题写入前 _update_session_meta 已落盘）
    from illusion.services.session_storage import write_meta

    write_meta(str(tmp_path), "ses123", {})
    engine = FakeEngine(str(tmp_path))
    wrote = await _write_title_meta(engine, "ses123", "新标题")
    assert wrote is True
    from illusion.services.session_storage import read_meta

    assert read_meta(str(tmp_path), "ses123")["title"] == "新标题"


@pytest.mark.asyncio
async def test_write_title_meta_skips_when_session_deleted(tmp_path):
    # 后台生成期间用户删除了会话：目录不存在 → 跳过写入，且不复活残留在磁盘
    engine = FakeEngine(str(tmp_path))
    wrote = await _write_title_meta(engine, "ses123", "新标题")
    assert wrote is False
    from illusion.services.session_storage import session_dir_for

    assert not session_dir_for(str(tmp_path), "ses123").exists()