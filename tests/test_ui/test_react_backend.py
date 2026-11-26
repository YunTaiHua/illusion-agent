"""Tests for the React backend host protocol."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from illusion.api.client import ApiMessageCompleteEvent
from illusion.api.usage import UsageSnapshot
from illusion.engine.messages import ConversationMessage, TextBlock, ThinkingBlock
from illusion.ui.backend_host import BackendHostConfig, ReactBackendHost
from illusion.ui.protocol import BackendEvent
from illusion.ui.runtime import build_runtime, close_runtime, start_runtime


class StaticApiClient:
    """Fake streaming client for backend host tests."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=2, output_tokens=3),
            stop_reason=None,
        )


class StaticThinkingApiClient:
    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[
                    ThinkingBlock(thinking="先检查上下文"),
                    TextBlock(text="最终答案"),
                ],
            ),
            usage=UsageSnapshot(input_tokens=2, output_tokens=3),
            stop_reason=None,
        )


class FakeBinaryStdout:
    """Capture protocol writes through a binary stdout buffer."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_backend_host_processes_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("/version")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    user_items = [
        event.item
        for event in events
        if event.type == "transcript_item" and event.item and event.item.role == "user"
    ]
    assert user_items
    # 已注册命令 → 命令产物标记 is_command=True（前端据此过滤，不显示在会话中）
    for item in user_items:
        assert item.is_command is True
    assert any(
        event.type == "command_result"
        and "IllusionAgent" in event.command_result_data.get("message", "")
        for event in events
        if event.command_result_data
    )
    assert any(event.type == "state_snapshot" for event in events)


@pytest.mark.asyncio
async def test_plugin_toggle_refreshes_terminal_slash_resource_types(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    plugin = tmp_path / ".illusion" / "plugins" / "meta"
    (plugin / "commands").mkdir(parents=True)
    (plugin / "skills" / "review").mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"meta"}', encoding="utf-8")
    for path, name in [(plugin / "commands" / "check.md", "check"), (plugin / "skills" / "review" / "SKILL.md", "review")]:
        path.write_text(f"---\nname: {name}\ndescription: Fixture {name}\n---\nInstructions.", encoding="utf-8")

    client = StaticApiClient("unused")
    host = ReactBackendHost(BackendHostConfig(api_client=client))
    host._bundle = await build_runtime(api_client=client, cwd=str(tmp_path))
    events = []

    async def capture(event):
        events.append(event)

    host._emit = capture  # type: ignore[method-assign]
    try:
        await start_runtime(host._bundle)
        for action, expected in [("disable", {}), ("enable", {"/meta:check": "command", "/meta:review": "skill"})]:
            events.clear()
            await host._process_line(f"/plugin {action} meta")
            catalog = next(e for e in events if e.type == "state_snapshot" and e.slash_commands is not None)
            assert {e["name"]: e["kind"] for e in catalog.slash_commands if e["source"] == "plugin"} == expected
            assert {n for n in catalog.commands if n.startswith("/meta:")} == set(expected)
            assert "/exit" in catalog.commands
            preferences = tmp_path / ".illusion" / "permissions.json"
            assert json.loads(preferences.read_text(encoding="utf-8")) == {
                "enabled_plugins": {"meta": action == "enable"}
            }
            assert "meta" not in host._bundle.current_settings().enabled_plugins
            events.clear()
            await host._process_line("/plugin list")
            expected_state = "enabled" if action == "enable" else "disabled"
            assert any(
                f"meta [{expected_state}] [project]" in (e.command_result_data or {}).get("message", "")
                for e in events
            )
    finally:
        await close_runtime(host._bundle)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["project-only", "seeded", "denied"])
async def test_terminal_browser_plugin_scope_controls_native_tools(tmp_path, monkeypatch, scenario):
    from illusion.config.settings import Settings, load_settings, save_settings

    # cwd 与进程目录不同，避免命令用进程目录作隐式回退。
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    project = workspace / ".illusion" / "plugins" / "browser-use"
    project.mkdir(parents=True)
    (project / "plugin.json").write_text('{"name":"browser-use"}', encoding="utf-8")
    if scenario == "project-only":
        monkeypatch.setattr("illusion.browser.seeding.seed_builtin_browser_plugin", lambda: None)
    denied = scenario == "denied"
    policy = workspace / ".illusion" / "permissions.json"
    policy.write_text(json.dumps({
        "enabled_plugins": {"browser-use": True}, "denied_plugins": ["*"] if denied else [],
    }), encoding="utf-8")
    before = policy.read_bytes()
    save_settings(Settings(enabled_plugins={"browser-use": denied}))

    client = StaticApiClient("unused")
    host = ReactBackendHost(BackendHostConfig(api_client=client))
    bundle = host._bundle = await build_runtime(api_client=client, cwd=str(workspace))
    events = []

    async def capture(event):
        events.append(event)

    def has_browser_tools():
        return any(tool.name.startswith("browser_") for tool in bundle.tool_registry.list_tools())

    host._emit = capture  # type: ignore[method-assign]
    try:
        await start_runtime(bundle)
        assert bundle.browser_manager is None and not has_browser_tools()
        await host._process_line("/plugin enable browser-use")
        assert has_browser_tools() == (scenario == "seeded")
        assert (bundle.browser_manager is not None) == (scenario == "seeded")
        assert bundle.engine._tool_metadata.get("browser_manager") is bundle.browser_manager
        if denied:
            assert any("denied by project policy" in (e.command_result_data or {}).get("message", "") for e in events)
        await host._process_line("/plugin disable browser-use")
        assert not has_browser_tools()
        assert bundle.browser_manager is None and bundle.engine._tool_metadata.get("browser_manager") is None
        assert load_settings().enabled_plugins["browser-use"] == denied
        if scenario == "project-only":
            assert json.loads(policy.read_text(encoding="utf-8"))["enabled_plugins"] == {"browser-use": False}
        else:
            assert policy.read_bytes() == before
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
@pytest.mark.parametrize("enable", [False, True])
async def test_plugin_command_updates_session_end_before_immediate_exit(tmp_path, monkeypatch, enable):
    from illusion.hooks.types import HookResult

    plugin = tmp_path / ".illusion" / "plugins" / "ending"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps({"name": "ending", "enabled_by_default": not enable}), encoding="utf-8")
    (plugin / "hooks.json").write_text(json.dumps({
        "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": "fixture-exit-hook"}]}],
    }), encoding="utf-8")
    client = StaticApiClient("unused")
    host = ReactBackendHost(BackendHostConfig(api_client=client))
    bundle = host._bundle = await build_runtime(api_client=client, cwd=str(tmp_path))
    executor = bundle.hook_executor
    context, session_store = executor._context, executor._session_hook_store
    calls = []

    async def record(hook, event, payload):
        calls.append((hook.command, event.value, payload["cwd"]))
        return HookResult(hook_type=hook.type, success=True)

    monkeypatch.setattr(executor, "_run_command_hook", record)
    try:
        await start_runtime(bundle)
        await host._process_line(f"/plugin {'enable' if enable else 'disable'} ending")
        assert bundle.hook_executor is executor is bundle.engine._hook_executor
        assert executor._context is context and executor._session_hook_store is session_store
    finally:
        await close_runtime(bundle)
    assert calls == ([("fixture-exit-hook", "SessionEnd", str(tmp_path))] if enable else [])


@pytest.mark.asyncio
async def test_browser_reenabled_after_startup_closes_the_current_manager(tmp_path, monkeypatch):
    from illusion.browser import BrowserManager
    from illusion.config.settings import Settings, save_settings

    save_settings(Settings(enabled_plugins={"browser-use": True}))
    client = StaticApiClient("unused")
    host = ReactBackendHost(BackendHostConfig(api_client=client))
    bundle = host._bundle = await build_runtime(api_client=client, cwd=str(tmp_path))
    first = bundle.browser_manager
    closed = []

    async def record_close(manager, *, purge=False):
        closed.append((manager, purge))

    monkeypatch.setattr(BrowserManager, "aclose", record_close)
    try:
        await start_runtime(bundle)
        await host._process_line("/plugin disable browser-use")
        assert bundle.browser_manager is None and closed == [(first, True)]
        await host._process_line("/plugin enable browser-use")
        second = bundle.browser_manager
        assert second is not first and second is bundle.engine._tool_metadata["browser_manager"]
    finally:
        await close_runtime(bundle)
    assert closed == [(first, True), (second, False)]


@pytest.mark.asyncio
async def test_backend_host_slash_prefixed_user_message_not_command(tmp_path, monkeypatch):
    """Regression: 以 / 开头但未命中命令注册表的输入是真实用户消息。

    曾按文本前缀把这类消息当作命令产物过滤（实时/重放均被吞），
    修复后后端按 commands.lookup 判定 is_command=False，前端据此正常显示。
    用例取真实发生过的输入——"/feedback完全删掉…"（中文无空格拼接，
    未命中任何命令名，落入文本通道）。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("ok")))
    host._bundle = await build_runtime(api_client=StaticApiClient("ok"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        raw = "/feedback完全删掉，notebook_edit可以合并到其他工具中吗？工具描述也会浪费很多tokens"
        should_continue = await host._process_line(raw)
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    user_items = [
        event.item
        for event in events
        if event.type == "transcript_item" and event.item and event.item.role == "user"
    ]
    assert user_items
    for item in user_items:
        assert item.text == raw
        assert item.is_command is False
    # 消息应进入引擎消息（context.jsonl 可回放），而非作为命令执行
    assert any(
        m.role == "user" and m.text == raw for m in host._bundle.engine.messages
    )


@pytest.mark.asyncio
async def test_backend_host_goal_command_transcript_not_filtered(tmp_path, monkeypatch):
    """/goal 创建命令的转录不打 is_command 标记，前端按真实用户消息渲染。

    /goal 命令原文通过 record_goal_command 作为 user 消息入库（重放可见、
    自动标题可捕获），因此其实时转录也不应被命令产物过滤。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("ok")))
    host._bundle = await build_runtime(api_client=StaticApiClient("ok"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("/goal 实现登录功能")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    user_items = [
        event.item
        for event in events
        if event.type == "transcript_item" and event.item and event.item.role == "user"
    ]
    assert user_items
    for item in user_items:
        assert item.text == "/goal 实现登录功能"
        assert item.is_command is False
    # /goal 命令原文已作为真实 user 消息入库（标题/轮次/重放素材）
    assert any(
        m.role == "user" and m.text == "/goal 实现登录功能"
        for m in host._bundle.engine.messages
    )


@pytest.mark.asyncio
async def test_backend_host_processes_model_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("hello from react backend")))
    host._bundle = await build_runtime(api_client=StaticApiClient("hello from react backend"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("hi")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    assert any(
        event.type == "assistant_complete" and event.message == "hello from react backend"
        for event in events
    )
    assert any(
        event.type == "assistant_complete"
        and event.item
        and event.item.role == "assistant"
        and "hello from react backend" in event.item.text
        for event in events
    )


@pytest.mark.asyncio
async def test_backend_host_emits_assistant_reasoning(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticThinkingApiClient()))
    host._bundle = await build_runtime(api_client=StaticThinkingApiClient())
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("hi")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    complete_events = [event for event in events if event.type == "assistant_complete"]
    assert complete_events
    assert complete_events[0].reasoning == "先检查上下文"
    assert complete_events[0].item is not None
    assert complete_events[0].item.reasoning == "先检查上下文"


@pytest.mark.asyncio
async def test_backend_host_command_does_not_reset_cli_overrides(tmp_path, monkeypatch):
    """Regression: slash commands should not snap model back to persisted defaults.

    When the session is launched with CLI overrides (e.g. --api-format openai --model env_1.model_1),
    issuing a command like /thinking triggers a UI state refresh. That refresh must
    preserve the effective session settings, not reload ~/.illusion/settings.json
    verbatim.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    # 预设环境配置以匹配 env:model 格式
    from illusion.config.settings import Settings, save_settings
    save_settings(
        Settings().model_copy(
            update={
                "model": "env_1.model_1",
                "env_1": {"api_format": "openai", "model_1": {"name": "gpt-5.4", "capabilities": []}},
            }
        )
    )

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(
        api_client=StaticApiClient("unused"),
        model="env_1.model_1",
        api_format="openai",
    )
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        # Sanity: the initial session state reflects CLI overrides.
        assert host._bundle.app_state.get().model == "gpt-5.4"

        # Run a command that triggers sync_app_state.
        await host._process_line("/thinking show")

        # CLI overrides should remain in effect.
        assert host._bundle.app_state.get().model == "gpt-5.4"
    finally:
        await close_runtime(host._bundle)


@pytest.mark.asyncio
async def test_backend_host_emits_utf8_protocol_bytes():
    host = ReactBackendHost(BackendHostConfig())

    await host._emit(BackendEvent(type="assistant_delta", message="你好😊"))

    # _emit 入队事件，从 _write_queue 取出验证
    event = host._write_queue.get_nowait()
    assert event.type == "assistant_delta"
    assert event.message == "你好😊"

    # 验证 OHJSON 协议字节：UTF-8 编码 + OHJSON: 前缀
    payload = "OHJSON:" + event.model_dump_json() + "\n"
    raw = payload.encode("utf-8")
    assert raw.startswith(b"OHJSON:")
    decoded = raw.decode("utf-8").strip()
    parsed = json.loads(decoded.removeprefix("OHJSON:"))
    assert parsed["type"] == "assistant_delta"
    assert parsed["message"] == "你好😊"


@pytest.mark.asyncio
async def test_backend_host_phase_transitions_on_model_turn(tmp_path, monkeypatch):
    """纯文本对话（无工具）时 phase 流转: idle → thinking → idle。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("phase test")))
    host._bundle = await build_runtime(api_client=StaticApiClient("phase test"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._process_line("hello")
    finally:
        await close_runtime(host._bundle)

    # 最终 phase 应为 idle
    assert host._bundle.app_state.get().phase == "idle"
    # line_complete 事件必须存在
    line_complete_events = [e for e in events if e.type == "line_complete"]
    assert len(line_complete_events) == 1
    # state_snapshot 中应包含 phase
    snapshots = [e for e in events if e.type == "state_snapshot" and e.state]
    assert any(s.state.get("phase") == "idle" for s in snapshots)


@pytest.mark.asyncio
async def test_backend_host_session_allow_skips_modal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    await start_runtime(host._bundle)
    try:
        # 先加入会话级允许，再调用 _ask_permission 应直接放行，不弹模态框
        host._session_allowed_tools.add("bash")
        allowed = await host._ask_permission("bash", "test")
    finally:
        await close_runtime(host._bundle)
    assert allowed is True


@pytest.mark.asyncio
async def test_backend_host_high_risk_not_exempted_by_session_allow(tmp_path, monkeypatch):
    """高危操作（high_risk=True）不可被会话级允许豁免，仍须弹模态框确认。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events: list[object] = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        # 已会话级允许的工具，遇到高危操作仍须发请求卡确认
        host._session_allowed_tools.add("bash")
        # 直接调用会阻塞等待响应，改为在任务中执行
        task = asyncio.create_task(host._ask_permission("bash", "rm -rf /", high_risk=True))
        await asyncio.sleep(0)  # 让 _ask_permission 发出 modal_request
        # 应已发出模态框请求（未被会话级豁免短路）
        perm_events = [
            e
            for e in events
            if getattr(e, "type", None) == "modal_request"
            and getattr(e, "modal", {}).get("kind") == "permission"
        ]
        assert perm_events, "高危操作不应被会话级允许跳过确认"
        # 兑现响应，避免任务悬挂
        request_id = perm_events[0].modal["request_id"]
        host._permission_requests[request_id].set_result(True)
        assert await task is True
    finally:
        await close_runtime(host._bundle)


@pytest.mark.asyncio
async def test_backend_host_does_not_treat_stop_text_as_special_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("handled as normal text")))
    host._bundle = await build_runtime(api_client=StaticApiClient("handled as normal text"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("/stop")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    assert any(
        event.type == "assistant_complete" and event.message == "handled as normal text"
        for event in events
    )


@pytest.mark.asyncio
async def test_backend_resume_keeps_restored_session_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    import time as _time_mod

    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import (
        get_project_session_dir,
        write_index,
        write_meta,
    )
    from illusion.ui.backend_host import BackendHostConfig, ReactBackendHost

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    await start_runtime(host._bundle)
    try:
        host._bundle.engine.load_messages([
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
        ])
        sid = "sid-old-001"
        session_dir = get_project_session_dir(tmp_path) / sid
        store = CheckpointStore(session_dir, sid)
        await store.append_checkpoint()
        for m in host._bundle.engine.messages:
            await store.append_message(m)
        write_meta(tmp_path, sid, {
            "session_id": sid, "cwd": str(tmp_path), "model": "claude-test",
            "created_at": _time_mod.time(), "updated_at": _time_mod.time(),
            "summary": "", "message_count": len(host._bundle.engine.messages),
        })
        write_index(tmp_path, sid)
        await host._process_line("/resume sid-old-001")
        assert host._bundle.session_id == "sid-old-001"
    finally:
        await close_runtime(host._bundle)


@pytest.mark.asyncio
async def test_backend_resume_replay_keeps_assistant_reasoning(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    import time as _time_mod

    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_storage import (
        get_project_session_dir,
        write_index,
        write_meta,
    )

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        host._bundle.engine.load_messages([
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(
                role="assistant",
                content=[ThinkingBlock(thinking="先分析问题"), TextBlock(text="最终答案")],
            ),
        ])
        sid = "sid-thinking-001"
        session_dir = get_project_session_dir(tmp_path) / sid
        store = CheckpointStore(session_dir, sid)
        await store.append_checkpoint()
        for m in host._bundle.engine.messages:
            await store.append_message(m)
        write_meta(tmp_path, sid, {
            "session_id": sid, "cwd": str(tmp_path), "model": "claude-test",
            "created_at": _time_mod.time(), "updated_at": _time_mod.time(),
            "summary": "", "message_count": len(host._bundle.engine.messages),
        })
        write_index(tmp_path, sid)
        await host._process_line("/resume sid-thinking-001")
    finally:
        await close_runtime(host._bundle)

    replace_events = [e for e in events if e.type == "replace_transcript"]
    assert replace_events
    assert replace_events[0].items is not None
    assistant_items = [item for item in replace_events[0].items if item.role == "assistant"]
    assert assistant_items
    assert assistant_items[0].reasoning == "先分析问题"
    assert assistant_items[0].text == "最终答案"


@pytest.mark.asyncio
async def test_backend_rewind_replay_keeps_reasoning_only_assistant(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:

        turn1_user = ConversationMessage(role="user", content=[TextBlock(text="turn1")])
        turn1_asst = ConversationMessage(role="assistant", content=[ThinkingBlock(thinking="先检查上下文")])
        turn2_user = ConversationMessage(role="user", content=[TextBlock(text="turn2")])
        turn2_asst = ConversationMessage(role="assistant", content=[TextBlock(text="final")])
        host._bundle.engine.load_messages([turn1_user, turn1_asst, turn2_user, turn2_asst])
        # 在 CheckpointStore 中写入与 engine 一致的消息+checkpoint
        store = host._bundle.engine.checkpoint_store
        assert store is not None
        await store.append_checkpoint()  # id=0
        await store.append_message(turn1_user)
        await store.append_message(turn1_asst)
        await store.append_checkpoint()  # id=1
        await store.append_message(turn2_user)
        await store.append_message(turn2_asst)
        await host._process_line("/rewind 1")
    finally:
        await close_runtime(host._bundle)

    replace_events = [e for e in events if e.type == "replace_transcript"]
    assert replace_events
    assert replace_events[0].items is not None
    assistant_items = [item for item in replace_events[0].items if item.role == "assistant"]
    assert assistant_items
    assert assistant_items[0].text == ""
    assert assistant_items[0].reasoning == "先检查上下文"


@pytest.mark.asyncio
async def test_resume_replay_has_no_session_restored_system_banner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        import time as _time_mod

        from illusion.services.checkpoint_store import CheckpointStore
        from illusion.services.session_storage import (
            get_project_session_dir,
            write_index,
            write_meta,
        )
        host._bundle.engine.load_messages([
            ConversationMessage(role="user", content=[TextBlock(text="u")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="a")]),
        ])
        sid = "sid-banner-001"
        session_dir = get_project_session_dir(tmp_path) / sid
        store = CheckpointStore(session_dir, sid)
        await store.append_checkpoint()
        for m in host._bundle.engine.messages:
            await store.append_message(m)
        write_meta(tmp_path, sid, {
            "session_id": sid, "cwd": str(tmp_path), "model": "claude-test",
            "created_at": _time_mod.time(), "updated_at": _time_mod.time(),
            "summary": "", "message_count": len(host._bundle.engine.messages),
        })
        write_index(tmp_path, sid)
        await host._process_line("/resume sid-banner-001")
    finally:
        await close_runtime(host._bundle)

    assert not any(
        e.type == "transcript_item" and e.item and e.item.role == "system" and e.item.text == "Session restored:"
        for e in events
    )


@pytest.mark.asyncio
async def test_resume_replay_skips_empty_user_transcript_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        import time as _time_mod

        from illusion.engine.messages import ToolResultBlock
        from illusion.services.checkpoint_store import CheckpointStore
        from illusion.services.session_storage import (
            get_project_session_dir,
            write_index,
            write_meta,
        )

        host._bundle.engine.load_messages([
            ConversationMessage(role="assistant", content=[]),
            ConversationMessage(role="user", content=[ToolResultBlock(tool_use_id="x", content="ok", is_error=False)]),
        ])
        sid = "sid-empty-user-001"
        session_dir = get_project_session_dir(tmp_path) / sid
        store = CheckpointStore(session_dir, sid)
        await store.append_checkpoint()
        for m in host._bundle.engine.messages:
            await store.append_message(m)
        write_meta(tmp_path, sid, {
            "session_id": sid, "cwd": str(tmp_path), "model": "claude-test",
            "created_at": _time_mod.time(), "updated_at": _time_mod.time(),
            "summary": "", "message_count": len(host._bundle.engine.messages),
        })
        write_index(tmp_path, sid)
        await host._process_line("/resume sid-empty-user-001")
    finally:
        await close_runtime(host._bundle)

    assert not any(
        e.type == "transcript_item" and e.item and e.item.role == "user" and e.item.text.strip() == ""
        for e in events
    )
