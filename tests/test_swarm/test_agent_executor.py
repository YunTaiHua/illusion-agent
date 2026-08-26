"""Tests for the agent_executor module: core agent execution logic."""

from __future__ import annotations

from illusion.swarm.agent_executor import (
    AgentAbortController,
    AgentExecutionContext,
    TaskNotification,
    TeammateMessage,
    _register_agent,
    _unregister_agent,
    format_task_notification,
    get_active_agent,
    get_active_agent_by_name,
    get_agent_context,
    list_active_agents,
    parse_task_notification,
    resolve_agent_tools,
    set_agent_context,
)
from illusion.tools.base import ToolRegistry

# ---------------------------------------------------------------------------
# AgentAbortController
# ---------------------------------------------------------------------------


class TestAgentAbortController:
    def test_initial_state(self):
        ctrl = AgentAbortController()
        assert not ctrl.is_cancelled
        assert ctrl.reason is None

    def test_graceful_cancel(self):
        ctrl = AgentAbortController()
        ctrl.request_cancel(reason="test reason")
        assert ctrl.is_cancelled
        assert ctrl.cancel_event.is_set()
        assert not ctrl.force_cancel.is_set()
        assert ctrl.reason == "test reason"

    def test_force_cancel(self):
        ctrl = AgentAbortController()
        ctrl.request_cancel(reason="force", force=True)
        assert ctrl.is_cancelled
        assert ctrl.cancel_event.is_set()
        assert ctrl.force_cancel.is_set()
        assert ctrl.reason == "force"


# ---------------------------------------------------------------------------
# AgentExecutionContext
# ---------------------------------------------------------------------------


class TestAgentExecutionContext:
    def test_defaults(self):
        ctx = AgentExecutionContext(agent_id="test-123", agent_name="researcher")
        assert ctx.agent_id == "test-123"
        assert ctx.agent_name == "researcher"
        assert ctx.agent_definition is None
        assert ctx.status == "starting"
        assert ctx.tool_use_count == 0
        assert ctx.total_tokens == 0

    def test_abort_controller_integration(self):
        ctx = AgentExecutionContext(agent_id="test-123", agent_name="researcher")
        assert not ctx.abort_controller.is_cancelled
        ctx.abort_controller.request_cancel(reason="test")
        assert ctx.abort_controller.is_cancelled


# ---------------------------------------------------------------------------
# ContextVar
# ---------------------------------------------------------------------------


class TestContextVar:
    def test_get_returns_none_outside_task(self):
        assert get_agent_context() is None

    def test_set_and_get(self):
        ctx = AgentExecutionContext(agent_id="test-123", agent_name="researcher")
        set_agent_context(ctx)
        assert get_agent_context() is ctx
        # Clean up to avoid leaking into other tests
        from illusion.swarm.agent_executor import _agent_context_var
        _agent_context_var.set(None)


# ---------------------------------------------------------------------------
# Active agent registry
# ---------------------------------------------------------------------------


class TestActiveAgentRegistry:
    def setup_method(self):
        # Clean up any leftover agents
        for agent in list_active_agents():
            _unregister_agent(agent.agent_id)

    def test_register_and_get(self):
        ctx = AgentExecutionContext(agent_id="agent-123", agent_name="researcher")
        _register_agent(ctx)
        assert get_active_agent("agent-123") is ctx
        _unregister_agent("agent-123")

    def test_get_by_name(self):
        ctx = AgentExecutionContext(agent_id="agent-123", agent_name="researcher")
        _register_agent(ctx)
        assert get_active_agent_by_name("researcher") is ctx
        _unregister_agent("agent-123")

    def test_unregister(self):
        ctx = AgentExecutionContext(agent_id="agent-123", agent_name="researcher")
        _register_agent(ctx)
        _unregister_agent("agent-123")
        assert get_active_agent("agent-123") is None

    def test_list_active(self):
        ctx1 = AgentExecutionContext(agent_id="agent-1", agent_name="a")
        ctx2 = AgentExecutionContext(agent_id="agent-2", agent_name="b")
        _register_agent(ctx1)
        _register_agent(ctx2)
        active = list_active_agents()
        assert len(active) == 2
        _unregister_agent("agent-1")
        _unregister_agent("agent-2")


# ---------------------------------------------------------------------------
# TaskNotification
# ---------------------------------------------------------------------------


class TestTaskNotification:
    def test_format_and_parse_roundtrip(self):
        original = TaskNotification(
            task_id="agent-123",
            status="completed",
            summary="Agent 'researcher' completed",
            result="Found 3 files.",
            usage={
                "total_tokens": 1500,
                "tool_uses": 5,
                "duration_ms": 3000,
            },
        )
        xml = format_task_notification(original)
        parsed = parse_task_notification(xml)

        assert parsed.task_id == original.task_id
        assert parsed.status == original.status
        assert parsed.summary == original.summary
        assert parsed.result == original.result
        assert parsed.usage == original.usage

    def test_format_minimal(self):
        n = TaskNotification(task_id="a", status="failed", summary="error")
        xml = format_task_notification(n)
        assert "<task-id>a</task-id>" in xml
        assert "<status>failed</status>" in xml
        assert "<result>" not in xml
        assert "<usage>" not in xml

    def test_parse_minimal(self):
        xml = """<task-notification>
<task-id>x</task-id>
<status>killed</status>
<summary>stopped</summary>
</task-notification>"""
        n = parse_task_notification(xml)
        assert n.task_id == "x"
        assert n.status == "killed"
        assert n.result is None
        assert n.usage is None


# ---------------------------------------------------------------------------
# TeammateMessage
# ---------------------------------------------------------------------------


class TestTeammateMessage:
    def test_required_fields(self):
        msg = TeammateMessage(text="hello", from_agent="leader")
        assert msg.text == "hello"
        assert msg.from_agent == "leader"
        assert msg.color is None
        assert msg.timestamp is None

    def test_optional_fields(self):
        msg = TeammateMessage(
            text="do this",
            from_agent="boss",
            color="green",
            timestamp="2026-01-01T00:00:00",
            summary="a task",
        )
        assert msg.color == "green"
        assert msg.summary == "a task"


# ---------------------------------------------------------------------------
# resolve_agent_tools
# ---------------------------------------------------------------------------


class TestResolveAgentTools:
    def _make_registry(self, *names: str) -> ToolRegistry:
        """Create a ToolRegistry with dummy tools."""
        from pydantic import BaseModel

        from illusion.tools.base import BaseTool

        class DummyInput(BaseModel):
            pass

        class DummyTool(BaseTool):
            def __init__(self, name: str):
                self.name = name
                self.description = f"Tool {name}"
                self.input_model = DummyInput

            async def execute(self, arguments, context):
                return None

        registry = ToolRegistry()
        for name in names:
            registry.register(DummyTool(name))
        return registry

    def test_no_agent_def_returns_all(self):
        parent = self._make_registry("bash", "read", "write", "glob")
        result = resolve_agent_tools(None, parent)
        assert len(result.list_tools()) == 4

    def test_agent_def_with_star_tools(self):
        from illusion.coordinator.agent_definitions import AgentDefinition

        agent_def = AgentDefinition(
            name="test",
            description="test",
            tools=["*"],
        )
        parent = self._make_registry("bash", "read", "write", "glob")
        result = resolve_agent_tools(agent_def, parent)
        # agent tool is not in parent, so all 4 should be returned
        assert len(result.list_tools()) == 4

    def test_agent_def_with_specific_tools(self):
        from illusion.coordinator.agent_definitions import AgentDefinition

        agent_def = AgentDefinition(
            name="test",
            description="test",
            tools=["bash", "read"],
        )
        parent = self._make_registry("bash", "read", "write", "glob")
        result = resolve_agent_tools(agent_def, parent)
        names = {t.name for t in result.list_tools()}
        assert names == {"bash", "read"}

    def test_agent_def_with_disallowed_tools(self):
        from illusion.coordinator.agent_definitions import AgentDefinition

        agent_def = AgentDefinition(
            name="test",
            description="test",
            disallowed_tools=["write"],
        )
        parent = self._make_registry("bash", "read", "write", "glob", "agent")
        result = resolve_agent_tools(agent_def, parent)
        names = {t.name for t in result.list_tools()}
        # "agent" is in default disallowed set, "write" is agent-specific disallowed
        assert "write" not in names
        assert "agent" not in names
        assert "bash" in names
        assert "read" in names


class TestAgentTypeDisplay:
    """agent_type_display：subagent_type → PascalCase 展示名。"""

    def test_hyphenated(self):
        from illusion.swarm.agent_executor import agent_type_display

        assert agent_type_display("general-purpose") == "GeneralPurpose"

    def test_underscored(self):
        from illusion.swarm.agent_executor import agent_type_display

        assert agent_type_display("statusline_setup") == "StatuslineSetup"

    def test_single_word(self):
        from illusion.swarm.agent_executor import agent_type_display

        assert agent_type_display("explore") == "Explore"

    def test_none_falls_back_to_default(self):
        from illusion.swarm.agent_executor import agent_type_display

        assert agent_type_display(None) == "GeneralPurpose"
        assert agent_type_display("") == "GeneralPurpose"

    def test_preserves_inner_case(self):
        """词内已有大写（自定义类型）不被 title() 降为小写"""
        from illusion.swarm.agent_executor import agent_type_display

        assert agent_type_display("myAgent-type") == "MyAgentType"
