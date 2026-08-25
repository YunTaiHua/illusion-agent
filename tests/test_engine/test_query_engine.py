"""Tests for the query engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from illusion.api.client import ApiMessageCompleteEvent, ApiRetryEvent, ApiTextDeltaEvent
from illusion.api.usage import UsageSnapshot
from illusion.config.settings import PermissionSettings
from illusion.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from illusion.engine.query_engine import QueryEngine
from illusion.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StatusEvent,
    ToolChainCompleted,
    ToolChainStarted,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from illusion.hooks import HookEvent, HookExecutionContext, HookExecutor
from illusion.hooks.loader import HookRegistry
from illusion.hooks.schemas import PromptHookDefinition
from illusion.permissions import PermissionChecker, PermissionMode
from illusion.tools import create_default_tool_registry


@dataclass
class _FakeResponse:
    message: ConversationMessage
    usage: UsageSnapshot


class FakeApiClient:
    """Deterministic streaming client used by query tests."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)

    async def stream_message(self, request):
        del request
        response = self._responses.pop(0)
        for block in response.message.content:
            if isinstance(block, TextBlock) and block.text:
                yield ApiTextDeltaEvent(text=block.text)
        yield ApiMessageCompleteEvent(
            message=response.message,
            usage=response.usage,
            stop_reason=None,
        )


class StaticApiClient:
    """Fake client that always returns one fixed assistant message."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class RetryThenSuccessApiClient:
    async def stream_message(self, request):
        del request
        yield ApiRetryEvent(message="rate limited", attempt=1, max_attempts=4, delay_seconds=1.5)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="after retry")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class ReasoningDeltaApiClient:
    async def stream_message(self, request):
        del request
        yield ApiTextDeltaEvent(text="", reasoning="先分析问题。")
        yield ApiTextDeltaEvent(text="最终答案")
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="最终答案")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


@pytest.mark.asyncio
async def test_query_engine_plain_text_reply(tmp_path: Path):
    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Hello from the model.")],
                    ),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                )
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("hello")]

    assert isinstance(events[0], AssistantTextDelta)
    assert events[0].text == "Hello from the model."
    assert isinstance(events[-1], AssistantTurnComplete)
    assert engine.total_usage.input_tokens == 10
    assert engine.total_usage.output_tokens == 5
    assert len(engine.messages) == 2


@pytest.mark.asyncio
async def test_query_engine_emits_reasoning_delta(tmp_path: Path):
    engine = QueryEngine(
        api_client=ReasoningDeltaApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )
    events = [event async for event in engine.submit_message("hello")]
    reasoning_deltas = [event for event in events if isinstance(event, AssistantTextDelta) and event.reasoning]
    assert reasoning_deltas
    assert reasoning_deltas[0].reasoning == "先分析问题。"


@pytest.mark.asyncio
async def test_query_engine_executes_tool_calls(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will inspect the file."),
                            ToolUseBlock(
                                id="toolu_123",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="The file contains alpha and beta.")],
                    ),
                    usage=UsageSnapshot(input_tokens=8, output_tokens=6),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read the file")]

    assert any(isinstance(event, ToolExecutionStarted) for event in events)
    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert len(tool_results) == 1
    assert "alpha" in tool_results[0].output
    assert isinstance(events[-1], AssistantTurnComplete)
    assert "alpha and beta" in events[-1].message.text
    assert len(engine.messages) == 4


@pytest.mark.asyncio
async def test_query_engine_allows_unbounded_turns_when_max_turns_is_none(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will inspect the file."),
                            ToolUseBlock(
                                id="toolu_123",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="The file contains alpha and beta.")],
                    ),
                    usage=UsageSnapshot(input_tokens=8, output_tokens=6),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_turns=None,
    )

    events = [event async for event in engine.submit_message("read the file")]

    assert isinstance(events[-1], AssistantTurnComplete)
    assert "alpha and beta" in events[-1].message.text
    assert engine.max_turns is None


@pytest.mark.asyncio
async def test_query_engine_surfaces_retry_status_events(tmp_path: Path):
    engine = QueryEngine(
        api_client=RetryThenSuccessApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("hello")]

    assert any(isinstance(event, StatusEvent) and "retrying in 1.5s" in event.message for event in events)
    assert isinstance(events[-1], AssistantTurnComplete)


@pytest.mark.asyncio
async def test_query_engine_respects_pre_tool_hook_blocks(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\n", encoding="utf-8")
    registry = HookRegistry()
    registry.register_hook(
        HookEvent.PRE_TOOL_USE,
        PromptHookDefinition(prompt="reject", matcher="read_file"),
        matcher="read_file",
    )

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_999",
                                name="read_file",
                                input={"path": str(sample)},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="blocked")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        hook_executor=HookExecutor(
            registry,
            HookExecutionContext(
                cwd=tmp_path,
                api_client=StaticApiClient('{"decision": "block", "reason": "no reading"}'),
                default_model="claude-test",
            ),
        ),
    )

    events = [event async for event in engine.submit_message("read file")]

    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert tool_results
    assert tool_results[0].is_error is True
    assert "no reading" in tool_results[0].output


@pytest.mark.asyncio
async def test_query_engine_executes_ask_user_tool(tmp_path: Path):
    async def _answer(question: str, questions_data: object = None) -> str:
        assert "Which color?" in question
        return "green"

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_ask",
                                name="ask_user_question",
                                input={
                                    "questions": [
                                        {
                                            "question": "Which color?",
                                            "header": "Color",
                                            "options": [
                                                {"label": "Green", "description": "Choose green"},
                                                {"label": "Blue", "description": "Choose blue"},
                                            ],
                                        }
                                    ]
                                },
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Picked green.")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        ask_user_prompt=_answer,
    )

    events = [event async for event in engine.submit_message("pick a color")]

    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert tool_results
    assert tool_results[0].output == "green"
    assert isinstance(events[-1], AssistantTurnComplete)
    assert events[-1].message.text == "Picked green."


async def _async_false() -> bool:
    return False


@pytest.mark.asyncio
async def test_query_engine_applies_path_rules_to_relative_read_file_targets(tmp_path: Path):
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    secret = blocked_dir / "secret.txt"
    secret.write_text("top-secret\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_blocked_read",
                                name="read_file",
                                input={"path": "blocked/secret.txt", "offset": 0, "limit": 1},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="blocked")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(
                mode=PermissionMode.DEFAULT,
                path_rules=[{"pattern": str((blocked_dir / "*").resolve()), "allow": False}],
            )
        ),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read blocked file")]

    # 权限拒绝不终止查询循环：以 error 工具结果返回原因，LLM 继续生成回复
    from illusion.engine.stream_events import ToolExecutionCompleted
    completed = [e for e in events if isinstance(e, ToolExecutionCompleted)]
    assert completed, "被拒工具应有 ToolExecutionCompleted"
    blocked_result = next(e for e in completed if e.tool_name == "read_file")
    assert blocked_result.is_error
    assert "Permission denied" in blocked_result.output
    # 后续 LLM 回复正常产生（循环未被终止）
    assert any(getattr(e, "text", "") == "blocked" for e in events)


@pytest.mark.asyncio
async def test_query_engine_applies_path_rules_to_write_file_targets_in_full_auto(tmp_path: Path):
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    target = blocked_dir / "output.txt"

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_blocked_write",
                                name="write_file",
                                input={"path": "blocked/output.txt", "content": "poc"},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="blocked")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(
                mode=PermissionMode.FULL_AUTO,
                path_rules=[{"pattern": str((blocked_dir / "*").resolve()), "allow": False}],
            )
        ),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("write blocked file")]

    # 权限拒绝不终止查询循环：以 error 工具结果返回原因，LLM 继续生成回复
    from illusion.engine.stream_events import ToolExecutionCompleted
    completed = [e for e in events if isinstance(e, ToolExecutionCompleted)]
    assert completed, "被拒工具应有 ToolExecutionCompleted"
    blocked_result = next(e for e in completed if e.tool_name == "write_file")
    assert blocked_result.is_error
    assert "Permission denied" in blocked_result.output
    assert target.exists() is False  # 拒绝生效：文件未写入


@pytest.mark.asyncio
async def test_query_engine_default_mode_denial_returns_error_result(tmp_path: Path):
    """回归：default 模式人工拒绝权限不终止查询循环，以 error 工具结果返回原因。

    与 full_auto 的 path_rules 测试对称：DEFAULT 模式下 MEDIUM 变更操作
    requires_confirmation=True，permission_prompt 拒绝后 LLM 收到
    "[Permission denied] ..." error 结果并继续生成回复。
    """
    target = tmp_path / "out.txt"

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_default_deny",
                                name="write_file",
                                input={"path": "out.txt", "content": "poc"},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="denied, moving on")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT)),
        permission_prompt=lambda tool, desc, high_risk=False: _async_false(),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("write a file")]

    from illusion.engine.stream_events import ErrorEvent, ToolExecutionCompleted

    # 不终止：无 ErrorEvent，被拒工具有 completed 事件且为 error
    assert not [e for e in events if isinstance(e, ErrorEvent)]
    completed = [e for e in events if isinstance(e, ToolExecutionCompleted)]
    blocked = next(e for e in completed if e.tool_name == "write_file")
    assert blocked.is_error
    assert "[Permission denied]" in blocked.output
    assert "confirmation" in blocked.output.lower() or "denied" in blocked.output.lower()
    assert target.exists() is False  # 拒绝生效：文件未写入
    # 循环继续：后续回复正常产生
    assert any(getattr(e, "text", "") == "denied, moving on" for e in events)


@pytest.mark.asyncio
async def test_query_engine_emits_tool_chain_events(tmp_path: Path):
    """工具调用时必须发出 ToolChainStarted 和 ToolChainCompleted 事件。"""
    sample = tmp_path / "chain.txt"
    sample.write_text("chain-data\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="Reading file."),
                            ToolUseBlock(id="toolu_chain", name="read_file", input={"path": str(sample)}),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Done reading.")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read")]

    chain_started = [e for e in events if isinstance(e, ToolChainStarted)]
    chain_completed = [e for e in events if isinstance(e, ToolChainCompleted)]

    assert len(chain_started) == 1
    assert chain_started[0].tool_count == 1

    assert len(chain_completed) == 1
    assert len(chain_completed[0].results_summary) == 1
    assert chain_completed[0].results_summary[0]["name"] == "read_file"
    assert chain_completed[0].results_summary[0]["is_error"] is False

    type_sequence = [type(e).__name__ for e in events]
    assert "ToolChainStarted" in type_sequence
    assert "ToolChainCompleted" in type_sequence
    started_idx = type_sequence.index("ToolChainStarted")
    completed_idx = type_sequence.index("ToolChainCompleted")
    turn_idx = len(type_sequence) - 1
    assert started_idx < completed_idx < turn_idx


@pytest.mark.asyncio
async def test_query_engine_no_chain_events_without_tools(tmp_path: Path):
    """无工具调用时不应发出 chain 事件。"""
    engine = QueryEngine(
        api_client=StaticApiClient("no tools needed"),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("hello")]
    assert not any(isinstance(e, ToolChainStarted) for e in events)
    assert not any(isinstance(e, ToolChainCompleted) for e in events)
