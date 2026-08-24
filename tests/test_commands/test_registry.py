"""Tests for slash command handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

import illusion.commands.helpers as helpers_module
from illusion.commands.registry import CommandContext, create_default_command_registry
from illusion.commands.types import CommandResult
from illusion.config.settings import Settings, load_settings, save_settings
from illusion.engine.messages import ConversationMessage, TextBlock
from illusion.engine.query_engine import QueryEngine
from illusion.mcp.types import McpHttpServerConfig, McpStdioServerConfig
from illusion.permissions import PermissionChecker
from illusion.state import AppState, AppStateStore
from illusion.tools import create_default_tool_registry


class FakeApiClient:
    async def stream_message(self, request):
        del request
        # 返回空异步生成器，使 compact_conversation 得到空摘要
        return
        yield  # 使其成为异步生成器


def _make_engine(tmp_path: Path) -> QueryEngine:
    return QueryEngine(
        api_client=FakeApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(load_settings().permission),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )


def _make_context(tmp_path: Path) -> CommandContext:
    tool_registry = create_default_tool_registry()
    return CommandContext(
        engine=QueryEngine(
            api_client=FakeApiClient(),
            tool_registry=tool_registry,
            permission_checker=PermissionChecker(load_settings().permission),
            cwd=tmp_path,
            model="claude-test",
            system_prompt="system",
        ),
        cwd=str(tmp_path),
        tool_registry=tool_registry,
        app_state=AppStateStore(
            AppState(
                model="claude-test",
                permission_mode="default",
                ui_language="en",
            )
        ),
    )


@pytest.mark.asyncio
async def test_permissions_command_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    command, args = registry.lookup("/permissions set full_auto")
    assert command is not None

    result = await command.handler(args, CommandContext(engine=_make_engine(tmp_path), cwd=str(tmp_path)))

    assert "Auto" in result.message
    assert load_settings().permission.mode == "full_auto"


@pytest.mark.asyncio
async def test_model_command_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    # 预设 env_1 环境和 model_a 模型
    save_settings(
        Settings().model_copy(
            update={
                "model": "env_1:model_a",
                "env_1": {"api_format": "anthropic", "model_a": "claude-opus-4-6", "model_b": "gpt-5.4"},
            }
        )
    )
    registry = create_default_command_registry()
    command, args = registry.lookup("/model set env_1:model_a")
    assert command is not None

    result = await command.handler(args, CommandContext(engine=_make_engine(tmp_path), cwd=str(tmp_path)))

    assert "env_1:model_a" in result.message
    assert load_settings().model == "env_1:model_a"


@pytest.mark.asyncio
async def test_model_command_accepts_direct_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    # 预设环境
    save_settings(
        Settings().model_copy(
            update={
                "model": "env_1:model_a",
                "env_1": {"api_format": "openai", "model_a": "gpt-5.4"},
            }
        )
    )
    registry = create_default_command_registry()
    command, args = registry.lookup("/model set env_1:model_a")
    assert command is not None

    result = await command.handler(args, CommandContext(engine=_make_engine(tmp_path), cwd=str(tmp_path)))

    assert "env_1:model_a" in result.message
    assert load_settings().model == "env_1:model_a"


@pytest.mark.asyncio
async def test_model_command_default_clears_profile_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    # 预设两个环境
    save_settings(
        Settings().model_copy(
            update={
                "model": "env_1.model_a",
                "env_1": {"api_format": "anthropic", "model_a": "claude-sonnet-4-6"},
                "env_2": {"api_format": "openai", "model_a": "gpt-5.4"},
            }
        )
    )
    registry = create_default_command_registry()
    command, args = registry.lookup("/model set env_2.model_a")
    assert command is not None

    result = await command.handler(args, CommandContext(engine=_make_engine(tmp_path), cwd=str(tmp_path)))

    assert "env_2.model_a" in result.message
    assert load_settings().model == "env_2.model_a"


@pytest.mark.asyncio
async def test_turns_show_reports_unlimited_engine_when_session_is_unbounded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)
    context.engine.set_max_turns(None)

    command, args = registry.lookup("/turns show")
    assert command is not None

    result = await command.handler(args, context)

    assert "Max turns (engine): None" in result.message


@pytest.mark.asyncio
async def test_turns_command_accepts_numeric_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    command, args = registry.lookup("/turns 42")
    assert command is not None

    result = await command.handler(args, context)

    assert "42" in result.message
    assert context.engine.max_turns == 42


@pytest.mark.asyncio
async def test_config_command_switches_active_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    save_settings(
        Settings().model_copy(
            update={
                "model": "env_1.model_1",
                "env_1": {"api_format": "anthropic", "model_1": "claude-sonnet-4-6"},
            }
        )
    )
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    command, args = registry.lookup("/config set model env_1.model_1")
    assert command is not None

    await command.handler(args, context)

    loaded = load_settings()
    assert loaded.model == "env_1.model_1"


@pytest.mark.asyncio
async def test_doctor_command_reports_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    command, args = registry.lookup("/doctor")
    assert command is not None

    result = await command.handler(
        args,
        CommandContext(
            engine=_make_engine(tmp_path),
            cwd=str(tmp_path),
            plugin_summary="Plugins:\n- demo [enabled] Example",
            mcp_summary="No MCP servers configured.",
            app_state=AppStateStore(AppState(model="claude-test", permission_mode="default", ui_language="en")),
        ),
    )

    assert "Doctor summary:" in result.message
    assert str(tmp_path) in result.message


@pytest.mark.asyncio
async def test_memory_command_manages_entries(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    add_command, add_args = registry.lookup("/memory add Pytest Tips :: use fixtures")
    add_result = await add_command.handler(add_args, context)
    assert "Added memory entry" in add_result.message

    list_command, list_args = registry.lookup("/memory list")
    list_result = await list_command.handler(list_args, context)
    assert "pytest_tips.md" in list_result.message

    show_command, show_args = registry.lookup("/memory show pytest_tips")
    show_result = await show_command.handler(show_args, context)
    assert "use fixtures" in show_result.message

    remove_command, remove_args = registry.lookup("/memory remove pytest_tips")
    remove_result = await remove_command.handler(remove_args, context)
    assert "Removed memory entry" in remove_result.message


@pytest.mark.asyncio
async def test_compact_command_works(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)
    context.engine.load_messages(
        [
            ConversationMessage(role="user", content=[TextBlock(text="alpha request")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="alpha reply")]),
            ConversationMessage(role="user", content=[TextBlock(text="beta request")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="beta reply")]),
        ]
    )

    compact_command, compact_args = registry.lookup("/compact 2")
    compact_result = await compact_command.handler(compact_args, context)
    # 消息可能是中文或英文（取决于 ui_language 设置）
    assert "Compacted conversation" in compact_result.message or "压缩对话" in compact_result.message
    # LLM compact 会失败（FakeApiClient），回退到传统方法
    # 传统方法会添加 boundary marker，所以消息数 >= 3
    assert len(context.engine.messages) >= 3


@pytest.mark.asyncio
async def test_ui_mode_commands_persist_and_update_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    language_command, language_args = registry.lookup("/language set en")
    language_result = await language_command.handler(language_args, context)
    assert "en" in language_result.message
    assert context.app_state.get().ui_language == "en"

    thinking_command, thinking_args = registry.lookup("/thinking off")
    thinking_result = await thinking_command.handler(thinking_args, context)
    assert "disabled" in thinking_result.message
    assert load_settings().show_thinking is False
    assert context.app_state.get().show_thinking is False

    effort_command, effort_args = registry.lookup("/effort high")
    effort_result = await effort_command.handler(effort_args, context)
    assert "high" in effort_result.message
    assert load_settings().effort == "high"
    assert context.app_state.get().effort == "high"


@pytest.mark.asyncio
async def test_thinking_command_without_args_toggles_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    thinking_command, thinking_args = registry.lookup("/thinking")
    first = await thinking_command.handler(thinking_args, context)
    assert "disabled" in first.message
    assert load_settings().show_thinking is False
    assert context.app_state.get().show_thinking is False

    thinking_command, thinking_args = registry.lookup("/thinking")
    second = await thinking_command.handler(thinking_args, context)
    assert "enabled" in second.message
    assert load_settings().show_thinking is True
    assert context.app_state.get().show_thinking is True


@pytest.mark.asyncio
async def test_version_context_and_share_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    version_command, version_args = registry.lookup("/version")
    version_result = await version_command.handler(version_args, context)
    assert "IllusionAgent" in version_result.message or "illusion" in version_result.message

    context_command, context_args = registry.lookup("/context")
    context_result = await context_command.handler(context_args, context)
    assert context_result.message  # default: show system prompt

    window_command, window_args = registry.lookup("/context window")
    window_result = await window_command.handler(window_args, context)
    assert "Context window" in window_result.message

    share_command, share_args = registry.lookup("/share")
    share_result = await share_command.handler(share_args, context)
    assert "shareable transcript snapshot" in share_result.message


@pytest.mark.asyncio
async def test_auth_and_project_context_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    login_command, login_args = registry.lookup("/login sk-test-123456")
    login_result = await login_command.handler(login_args, context)
    assert "Stored API key" in login_result.message or "API Key 已保存" in login_result.message
    assert load_settings().api_key == "sk-test-123456"

    logout_command, logout_args = registry.lookup("/logout")
    logout_result = await logout_command.handler(logout_args, context)
    assert "Cleared stored API key" in logout_result.message or "已清除已保存 API Key" in logout_result.message
    assert load_settings().api_key == ""


@pytest.mark.asyncio
async def test_agents_session_files_and_reload_plugins_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    plugin_root = tmp_path / "config" / "plugins" / "fixture-plugin"
    (plugin_root / "skills").mkdir(parents=True)
    (plugin_root / "plugin.json").write_text(
        '{"name":"fixture-plugin","version":"1.0.0","description":"Fixture plugin"}',
        encoding="utf-8",
    )
    reload_command, reload_args = registry.lookup("/reload-plugins")
    reload_result = await reload_command.handler(reload_args, context)
    assert "fixture-plugin" in reload_result.message


@pytest.mark.asyncio
async def test_init_command(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    init_command, init_args = registry.lookup("/init")
    init_result = await init_command.handler(init_args, context)
    assert "initialization complete" in init_result.message or "already initialized" in init_result.message or "初始化完成" in init_result.message or "已初始化" in init_result.message
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "ILLUSION.md").exists()
    # 记忆入口位于 user 级记忆目录（仅保留 user 级记忆入口，无项目级 .illusion/memory/）
    from illusion.memory.paths import get_memory_entrypoint
    assert get_memory_entrypoint(tmp_path).exists()
    assert not (tmp_path / ".illusion" / "memory" / "MEMORY.md").exists()
    assert (tmp_path / ".illusion" / "rules" / "project-structure.md").exists()


@pytest.mark.asyncio
async def test_copy_rewind_and_meta_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)
    msgs = [
        ConversationMessage.from_user_text("first prompt"),
        ConversationMessage(role="assistant", content=[TextBlock(text="first answer")]),
        ConversationMessage.from_user_text("second prompt"),
        ConversationMessage(role="assistant", content=[TextBlock(text="second answer")]),
    ]
    context.engine.load_messages(msgs)

    # 设置 CheckpointStore（替代旧 push_checkpoint 内存栈）
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import get_project_session_dir
    sid = "test-copy-rewind"
    session_dir = get_project_session_dir(tmp_path) / sid
    store = CheckpointStore(session_dir, sid)
    context.engine.set_checkpoint_store(store)
    await store.append_checkpoint()  # id=0 (turn 1)
    await store.append_message(msgs[0])
    await store.append_message(msgs[1])
    await store.append_checkpoint()  # id=1 (turn 2)
    await store.append_message(msgs[2])
    await store.append_message(msgs[3])

    copied: list[str] = []

    def _fake_copy(text: str) -> None:
        copied.append(text)

    monkeypatch.setattr(helpers_module.pyperclip, "copy", _fake_copy)

    copy_command, copy_args = registry.lookup("/copy")
    copy_result = await copy_command.handler(copy_args, context)
    assert "Copied" in copy_result.message
    assert copied == ["second answer"]

    rewind_command, rewind_args = registry.lookup("/rewind 1")
    rewind_result = await rewind_command.handler(rewind_args, context)
    assert "Rewound 1 turn(s)" in rewind_result.message
    assert len(context.engine.messages) == 2

    privacy_command, privacy_args = registry.lookup("/privacy-settings")
    privacy_result = await privacy_command.handler(privacy_args, context)
    assert "user_config_dir" in privacy_result.message


@pytest.mark.asyncio
async def test_mcp_and_language_commands_report_richer_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    settings = Settings(
        mcp_servers={
            "http-demo": McpHttpServerConfig(url="https://example.com/mcp"),
            "stdio-demo": McpStdioServerConfig(command="python", args=["-m", "demo"]),
        }
    )
    save_settings(settings)

    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    mcp_http_command, mcp_http_args = registry.lookup("/mcp auth http-demo secret-token")
    mcp_http_result = await mcp_http_command.handler(mcp_http_args, context)
    assert "Saved MCP auth for http-demo" in mcp_http_result.message
    assert load_settings().mcp_servers["http-demo"].headers["Authorization"] == "Bearer secret-token"

    mcp_stdio_command, mcp_stdio_args = registry.lookup("/mcp auth stdio-demo env DEMO_TOKEN")
    mcp_stdio_result = await mcp_stdio_command.handler(mcp_stdio_args, context)
    assert "Saved MCP auth for stdio-demo" in mcp_stdio_result.message
    assert load_settings().mcp_servers["stdio-demo"].env["MCP_AUTH_TOKEN"] == "DEMO_TOKEN"

    language_command, language_args = registry.lookup("/language show")
    language_result = await language_command.handler(language_args, context)
    assert "UI language:" in language_result.message or "界面语言" in language_result.message


@pytest.mark.asyncio
async def test_new_command_clears_messages_and_requests_new_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)
    context.engine.load_messages(
        [
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
        ]
    )

    command, args = registry.lookup("/new")
    assert command is not None

    result = await command.handler(args, context)

    assert len(context.engine.messages) == 0
    assert result.clear_screen is True
    assert result.reset_session is True


def test_stop_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    lookup = registry.lookup("/stop")
    assert lookup is None


@pytest.mark.asyncio
async def test_resume_command_returns_restored_session_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _make_context(tmp_path)

    import time

    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import (
        get_project_session_dir,
        write_index,
        write_meta,
    )

    context.engine.load_messages([
        ConversationMessage(role="user", content=[TextBlock(text="resume me")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
    ])
    sid = "resume-abc123"
    session_dir = get_project_session_dir(tmp_path) / sid
    store = CheckpointStore(session_dir, sid)
    await store.append_checkpoint()
    for m in context.engine.messages:
        await store.append_message(m)
    write_meta(tmp_path, sid, {
        "session_id": sid, "cwd": str(tmp_path), "model": "claude-test",
        "created_at": time.time(), "updated_at": time.time(),
        "summary": "", "message_count": len(context.engine.messages),
    })
    write_index(tmp_path, sid)

    command, args = registry.lookup("/resume resume-abc123")
    result = await command.handler(args, context)

    assert result.replay_messages is not None
    assert result.restored_session_id == "resume-abc123"


def test_cost_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    lookup = registry.lookup("/cost")
    assert lookup is None


def test_usage_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    lookup = registry.lookup("/usage")
    assert lookup is None


def test_stats_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    lookup = registry.lookup("/stats")
    assert lookup is None


def test_agents_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    lookup = registry.lookup("/agents")
    assert lookup is None


def test_tasks_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    lookup = registry.lookup("/tasks")
    assert lookup is None


def test_branch_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/branch") is None


def test_commit_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/commit") is None


def test_diff_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/diff") is None


def test_files_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/files") is None


def test_issue_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/issue") is None


def test_plan_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/plan") is None


def test_pr_comments_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/pr_comments") is None


def test_status_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/status") is None


def test_summary_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/summary") is None


def test_bridge_command_removed_from_registry() -> None:
    registry = create_default_command_registry()
    assert registry.lookup("/bridge") is None


def test_update_command_not_registered() -> None:
    """update 不再作为斜杠指令注册。"""
    registry = create_default_command_registry()
    assert registry.lookup("/update") is None


def test_slash_command_has_usage_field_default_none():
    """SlashCommand.usage 字段默认为 None。"""
    from illusion.commands.registry import SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    cmd = SlashCommand("test_cmd", "test description", _noop)
    assert cmd.usage is None


def test_slash_command_usage_field_set():
    """SlashCommand.usage 可在构造时传入。"""
    from illusion.commands.registry import SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    cmd = SlashCommand("test_cmd", "test description", _noop, usage="/test_cmd [show|set N]")
    assert cmd.usage == "/test_cmd [show|set N]"


def test_registry_get_usage_returns_registered_usage():
    """CommandRegistry.get_usage 返回已注册的 usage。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    registry = CommandRegistry()
    registry.register(SlashCommand("ctx", "ctx desc", _noop, usage="/ctx [usage|show]"))
    assert registry.get_usage("ctx") == "/ctx [usage|show]"
    assert registry.get_usage("nonexistent") is None


def test_help_text_includes_usage_when_set():
    """help_text 在 usage 设置时包含用法行。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    registry = CommandRegistry()
    registry.register(SlashCommand("test", "test description", _noop, usage="/test [show|set N]"))
    text = registry.help_text()
    assert "Usage: /test [show|set N]" in text


def test_help_text_excludes_usage_when_none():
    """help_text 在 usage 为 None 时不包含用法行。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    registry = CommandRegistry()
    registry.register(SlashCommand("test", "test description", _noop))
    text = registry.help_text()
    assert "Usage:" not in text


def test_help_text_excludes_usage_when_empty_string():
    """help_text 在 usage 为空字符串时不包含用法行（验证 if command.usage 真值检查）。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    registry = CommandRegistry()
    registry.register(SlashCommand("test", "test description", _noop, usage=""))
    text = registry.help_text()
    assert "Usage:" not in text


def test_help_text_usage_inline_with_command():
    """usage 内联在命令行中，不单独换行。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    registry = CommandRegistry()
    registry.register(SlashCommand("test", "test description", _noop, usage="/test [show|set N]"))
    text = registry.help_text()
    lines = text.split("\n")
    # 找到包含 /test 的行
    command_line = next(line for line in lines if line.startswith("/test"))
    # usage 应在同一行内联显示
    assert "Usage: /test [show|set N]" in command_line
    # 不应有单独的 usage 行
    usage_only_lines = [line for line in lines if line.strip().startswith("Usage:")]
    assert len(usage_only_lines) == 0


def test_help_text_with_multiple_commands():
    """多个命令的 usage 都出现在 help_text 中。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand

    async def _noop(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult()

    registry = CommandRegistry()
    registry.register(SlashCommand("alpha", "alpha desc", _noop, usage="/alpha [show|set]"))
    registry.register(SlashCommand("beta", "beta desc", _noop, usage="/beta [list|add]"))
    text = registry.help_text()
    assert "Usage: /alpha [show|set]" in text
    assert "Usage: /beta [list|add]" in text


@pytest.mark.asyncio
async def test_localized_handler_appends_usage(tmp_path: Path):
    """_localized_handler 在 command.usage 非空且 message 不含 Usage 时追加用法。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand
    from illusion.commands.types import CommandResult

    async def _handler(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult(message="Context window: 200000 tokens")

    registry = CommandRegistry()
    registry.register(SlashCommand("ctx", "ctx desc", _handler, usage="/ctx [usage|show|window|set N]"))
    command, args = registry.lookup("/ctx")
    context = _make_context(tmp_path)
    result = await command.handler(args, context)
    assert "Usage: /ctx [usage|show|window|set N]" in result.message
    assert "Context window: 200000 tokens" in result.message


@pytest.mark.asyncio
async def test_localized_handler_skips_usage_when_already_present(tmp_path: Path):
    """_localized_handler 在 message 已含 Usage 时不重复追加。"""
    from illusion.commands.registry import CommandRegistry, SlashCommand
    from illusion.commands.types import CommandResult

    async def _handler(args: str, context: CommandContext) -> CommandResult:
        del args, context
        return CommandResult(message="Usage: /ctx [usage|show]")

    registry = CommandRegistry()
    registry.register(SlashCommand("ctx", "ctx desc", _handler, usage="/ctx [usage|show|window|set N]"))
    command, args = registry.lookup("/ctx")
    context = _make_context(tmp_path)
    result = await command.handler(args, context)
    assert result.message.count("Usage:") == 1


@pytest.mark.asyncio
async def test_render_command_result_no_usage_when_none(tmp_path: Path):
    """_render_command_result 不再追加用法（已移至 _localized_handler）。"""
    from illusion.commands.types import CommandResult
    from illusion.ui.runtime import _render_command_result

    emitted: list[tuple[str, str]] = []

    async def _emitter(message: str, result_type: str) -> None:
        emitted.append((message, result_type))

    async def _print_system(message: str) -> None:
        pass

    async def _clear_output() -> None:
        pass

    result = CommandResult(message="Session cleared")
    await _render_command_result(
        result,
        print_system=_print_system,
        clear_output=_clear_output,
        command_result_emitter=_emitter,
    )
    assert len(emitted) == 1
    assert "Usage:" not in emitted[0][0]
