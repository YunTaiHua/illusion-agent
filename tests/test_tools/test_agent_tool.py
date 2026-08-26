"""Tests for agent tool foreground/background behavior in team mode."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from illusion.state import AppState, AppStateStore
from illusion.swarm.agent_executor import AgentResult
from illusion.tools.agent_tool import AgentTool, AgentToolInput
from illusion.tools.base import ToolExecutionContext, ToolRegistry


def _query_engine(tmp_path: Path) -> SimpleNamespace:
    registry = ToolRegistry()
    return SimpleNamespace(
        _api_client=object(),
        _tool_registry=registry,
        _permission_checker=object(),
        _cwd=tmp_path,
        _model="demo-model",
        _system_prompt="demo-system",
        _max_tokens=1024,
        _max_turns=8,
        _permission_prompt=None,
        _ask_user_prompt=None,
        _hook_executor=None,
        _effort=None,
    )


def _context(tmp_path: Path, store: AppStateStore) -> ToolExecutionContext:
    registry = ToolRegistry()
    return ToolExecutionContext(
        cwd=tmp_path,
        metadata={
            "tool_registry": registry,
            "query_engine": _query_engine(tmp_path),
            "app_state_store": store,
            "session_id": "session-test",
        },
    )


@pytest.mark.asyncio
async def test_agent_tool_forces_foreground_for_team_lead(tmp_path: Path, monkeypatch):
    store = AppStateStore(
        AppState(
            model="demo-model",
            permission_mode="default",
            team_context={"teamName": "demo-team"},
        )
    )
    context = _context(tmp_path, store)
    calls: dict[str, bool] = {}

    async def _fake_run_agent_in_process(
        config,
        query_context,
        parent_registry,
        *,
        is_async: bool = False,
        existing_context=None,
        **kwargs,
    ):
        del config, query_context, parent_registry, existing_context, kwargs
        calls["is_async"] = is_async
        return AgentResult(agent_id="agent-test", success=True, result_text="agent done")

    monkeypatch.setattr(
        "illusion.swarm.agent_executor.run_agent_in_process",
        _fake_run_agent_in_process,
    )

    result = await AgentTool().execute(
        AgentToolInput(
            description="run teammate task",
            prompt="Do a short task and return.",
            run_in_background=True,
        ),
        context,
    )

    assert result.is_error is False
    assert "launched in background" in result.output

    await asyncio.sleep(0)
    assert calls["is_async"] is True


@pytest.mark.asyncio
async def test_agent_tool_keeps_background_outside_team_mode(tmp_path: Path, monkeypatch):
    store = AppStateStore(AppState(model="demo-model", permission_mode="default"))
    context = _context(tmp_path, store)
    calls: dict[str, bool] = {}

    async def _fake_run_agent_in_process(
        config,
        query_context,
        parent_registry,
        *,
        is_async: bool = False,
        existing_context=None,
        **kwargs,
    ):
        del config, query_context, parent_registry, existing_context, kwargs
        calls["is_async"] = is_async
        return AgentResult(agent_id="agent-test", success=True, result_text="agent done")

    monkeypatch.setattr(
        "illusion.swarm.agent_executor.run_agent_in_process",
        _fake_run_agent_in_process,
    )

    result = await AgentTool().execute(
        AgentToolInput(
            description="run background task",
            prompt="Do a short task and return.",
            run_in_background=True,
        ),
        context,
    )

    assert result.is_error is False
    assert "launched in background" in result.output

    await asyncio.sleep(0)
    assert calls["is_async"] is True


@pytest.mark.asyncio
async def test_agent_tool_subagent_gets_independent_file_state_cache(tmp_path: Path, monkeypatch):
    """子 agent 使用独立文件状态缓存，不继承父会话的"已读"标记。

    回归：继承父会话 file_state_cache 会让子 agent 的 read_file 命中
    父会话的已读标记而返回占位提示（子 agent 从未读过该文件）。
    """
    from illusion.utils.file_state_cache import FileState, FileStateCache

    store = AppStateStore(AppState(model="demo-model", permission_mode="default"))
    # 父引擎缓存预置一个"已读"条目
    engine = _query_engine(tmp_path)
    parent_cache = FileStateCache()
    parent_cache.set(str(tmp_path / "x.txt"), FileState(content="x", timestamp=1.0))
    engine._file_state_cache = parent_cache
    context = ToolExecutionContext(
        cwd=tmp_path,
        metadata={
            "tool_registry": ToolRegistry(),
            "query_engine": engine,
            "app_state_store": store,
            "session_id": "session-test",
        },
    )
    captured: dict[str, object] = {}

    async def _fake_run_agent_in_process(config, query_context, parent_registry, **kwargs):
        del config, parent_registry, kwargs
        captured["file_state_cache"] = query_context.file_state_cache
        return AgentResult(agent_id="agent-test", success=True, result_text="agent done")

    monkeypatch.setattr(
        "illusion.swarm.agent_executor.run_agent_in_process",
        _fake_run_agent_in_process,
    )

    result = await AgentTool().execute(
        AgentToolInput(
            description="run background task",
            prompt="Do a short task and return.",
            run_in_background=True,
        ),
        context,
    )
    assert result.is_error is False

    await asyncio.sleep(0)
    child_cache = captured["file_state_cache"]
    # 独立新缓存：非父缓存实例、初始为空（子 agent 的 read 不会被父的已读标记污染）
    assert isinstance(child_cache, FileStateCache)
    assert child_cache is not parent_cache
    assert child_cache.size == 0
