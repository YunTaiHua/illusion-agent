"""Higher-level slash command integration flows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from illusion.commands.registry import CommandContext, create_default_command_registry
from illusion.config.settings import load_settings
from illusion.engine.messages import ConversationMessage, TextBlock
from illusion.engine.query_engine import QueryEngine
from illusion.permissions import PermissionChecker
from illusion.state import AppState, AppStateStore
from illusion.tools import create_default_tool_registry


class FakeApiClient:
    async def stream_message(self, request):
        del request
        # /compact 命令会调用此方法生成摘要；测试中模拟 API 不可用，
        # compact_handler 会捕获 RuntimeError 并回退到简单 compact。
        # 必须是 async generator（含 yield）才能被 async for 接受。
        raise RuntimeError("stream_message is not available in command flow tests")
        yield  # pragma: no cover - 使函数成为 async generator


def _build_context(tmp_path: Path) -> CommandContext:
    tool_registry = create_default_tool_registry()
    engine = QueryEngine(
        api_client=FakeApiClient(),
        tool_registry=tool_registry,
        permission_checker=PermissionChecker(load_settings().permission),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )
    engine.load_messages(
        [
            ConversationMessage(role="user", content=[TextBlock(text="first")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="second")]),
            ConversationMessage(role="user", content=[TextBlock(text="third")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="fourth")]),
        ]
    )
    return CommandContext(
        engine=engine,
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


def _write_fixture_plugin(root: Path) -> Path:
    plugin_dir = root / "fixture-plugin"
    (plugin_dir / "skills").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "fixture-plugin", "version": "1.0.0", "description": "Fixture plugin"}),
        encoding="utf-8",
    )
    (plugin_dir / "skills" / "fixture.md").write_text(
        "# FixtureSkill\nFixture command plugin content.\n",
        encoding="utf-8",
    )
    return plugin_dir


@pytest.mark.asyncio
async def test_command_flow_for_memory_modes_and_tasks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _build_context(tmp_path)

    for raw in [
        "/memory add Notes :: command flow note",
        "/memory list",
        "/compact 2",
        "/language set en",
    ]:
        command, args = registry.lookup(raw)
        result = await command.handler(args, context)
        assert result is not None

    doctor_command, doctor_args = registry.lookup("/doctor")
    doctor_result = await doctor_command.handler(doctor_args, context)
    assert "- ui_language: en" in doctor_result.message


@pytest.mark.asyncio
async def test_plugin_command_lifecycle_flow(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = create_default_command_registry()
    context = _build_context(tmp_path)
    plugin_source = _write_fixture_plugin(tmp_path / "plugin-source")

    install_command, install_args = registry.lookup(f"/plugin install {plugin_source}")
    install_result = await install_command.handler(install_args, context)
    assert "Installed plugin" in install_result.message

    disable_command, disable_args = registry.lookup("/plugin disable fixture-plugin")
    disable_result = await disable_command.handler(disable_args, context)
    assert "Disabled plugin" in disable_result.message
    assert load_settings().enabled_plugins["fixture-plugin"] is False

    enable_command, enable_args = registry.lookup("/plugin enable fixture-plugin")
    enable_result = await enable_command.handler(enable_args, context)
    assert "Enabled plugin" in enable_result.message
    assert load_settings().enabled_plugins["fixture-plugin"] is True

    uninstall_command, uninstall_args = registry.lookup("/plugin uninstall fixture-plugin")
    uninstall_result = await uninstall_command.handler(uninstall_args, context)
    assert "Uninstalled plugin" in uninstall_result.message


@pytest.mark.asyncio
async def test_resume_followed_by_session_tag_uses_restored_session_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_command_registry()
    context = _build_context(tmp_path)

    import time
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import (
        get_project_session_dir,
        read_meta,
        write_index,
        write_meta,
    )

    context.engine.load_messages([
        ConversationMessage(role="user", content=[TextBlock(text="first")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="second")]),
    ])
    sid = "sid-flow-001"
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

    resume_command, resume_args = registry.lookup("/resume sid-flow-001")
    resume_result = await resume_command.handler(resume_args, context)
    assert resume_result.restored_session_id == "sid-flow-001"
    assert read_meta(tmp_path, "sid-flow-001") is not None
