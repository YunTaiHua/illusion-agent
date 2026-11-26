"""斜杠技能命令解析测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from illusion.commands.slash_skills import (
    format_prompt_entry,
    list_slash_commands,
    prepare_slash_submission,
    resolve_slash_resources,
    resolve_slash_skill,
)
from illusion.config.settings import Settings
from illusion.plugins import load_plugins
from illusion.skills.loader import load_skill_registry
from illusion.skills.types import SkillDefinition


def _write_plugin(root: Path) -> None:
    """写入含技能 + 插件命令的插件目录（命令内容带 $ARGUMENTS 与资源根占位）。"""
    plugin_dir = root / "cmd-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "cmdplug", "version": "1.0.0", "description": "c"}),
        encoding="utf-8",
    )
    (plugin_dir / "skills" / "alpha" / "SKILL.md").parent.mkdir(parents=True)
    (plugin_dir / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Real skill\n---\n\n# Alpha\n",
        encoding="utf-8",
    )
    (plugin_dir / "commands").mkdir()
    (plugin_dir / "commands" / "greet.md").write_text(
        "---\nname: greet\ndescription: Greet someone\n---\n\n"
        "Greet $ARGUMENTS from ${CLAUDE_PLUGIN_ROOT}/assets\n",
        encoding="utf-8",
    )
    # 另一个插件的同名命令：制造后缀歧义
    other = root / "other-plugin"
    other.mkdir(parents=True)
    (other / "plugin.json").write_text(
        json.dumps({"name": "otherplug", "version": "1.0.0", "description": "o"}),
        encoding="utf-8",
    )
    (other / "commands").mkdir()
    (other / "commands" / "greet.md").write_text(
        "---\nname: greet\ndescription: Other greet\n---\n\nOther greet\n",
        encoding="utf-8",
    )


def _setup(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    plugins_root = tmp_path / "repo" / ".illusion" / "plugins"
    plugins_root.mkdir(parents=True)
    _write_plugin(plugins_root)
    return config


def test_resolve_exact_and_suffix_match(tmp_path: Path):
    """精确名与去命名空间后缀名都可命中；变量替换生效。"""
    _setup(tmp_path)
    project = tmp_path / "repo"
    load_plugins(Settings(), project)  # 触发目录创建

    cwd = str(project)
    # 精确注册名（两个插件同名 greet 存在，全名仍可唯一命中）
    match = resolve_slash_skill("/cmdplug:greet world", cwd)
    assert match is not None
    assert match.name == "cmdplug:greet"
    assert match.kind == "command"
    assert match.args == "world"
    assert "Greet world from" in match.expanded
    assert "${CLAUDE_PLUGIN_ROOT}" not in match.expanded
    assert "<command-name>/cmdplug:greet</command-name>" in match.expanded

    # 后缀唯一命中：仅保留单插件时 /greet 消歧成功
    from illusion.skills.registry import SkillRegistry

    single = SkillRegistry()
    source_registry = load_skill_registry(cwd)
    for s in source_registry.list_commands():
        if s.name.startswith("cmdplug:"):
            single.register_command(s)
    for s in source_registry.list_skills():
        if s.name.startswith("cmdplug:"):
            single.register(s)
    match = resolve_slash_skill("/greet everyone", cwd, registry=single)
    assert match is not None
    assert match.name == "cmdplug:greet"
    assert match.args == "everyone"
    # 真技能同样可斜杠激活（kind="skill"）
    match = resolve_slash_skill("/cmdplug:alpha", cwd, registry=single)
    assert match is not None
    assert match.kind == "skill"


def test_list_slash_commands_prefixes_names(tmp_path: Path):
    """补全列表条目带 / 前缀且含 kind 标注。"""
    _setup(tmp_path)
    project = tmp_path / "repo"
    entries = list_slash_commands(str(project))
    names = {e["name"] for e in entries}
    assert "/cmdplug:alpha" in names
    assert "/cmdplug:greet" in names
    assert all(e["name"].startswith("/") for e in entries)
    assert all(e["kind"] in ("skill", "command") for e in entries)
    assert next(e for e in entries if e["name"] == "/cmdplug:greet")["source"] == "plugin"


def test_resolve_ambiguous_suffix_returns_none(tmp_path: Path):
    """多插件同名命令时后缀匹配存在歧义，回落普通消息路径。"""
    _setup(tmp_path)
    project = tmp_path / "repo"
    registry = load_skill_registry(str(project))
    # 两个插件都声明 greet → 后缀歧义
    match = resolve_slash_skill("/greet hi", str(project), registry=registry)
    assert match is None
    # 全名可消歧
    match = resolve_slash_skill("/cmdplug:greet hi", str(project), registry=registry)
    assert match is not None
    assert match.args == "hi"


def test_resolve_miss_falls_through(tmp_path: Path):
    """未命中/非斜杠输入返回 None。"""
    _setup(tmp_path)
    project = tmp_path / "repo"
    registry = load_skill_registry(str(project))
    assert resolve_slash_skill("/no-such-cmd x", str(project), registry=registry) is None
    assert resolve_slash_skill("普通消息 /cmdplug:greet", str(project), registry=registry) is None
    assert resolve_slash_skill("//double", str(project), registry=registry) is None


def test_list_slash_commands_and_prompt_truncation():
    """补全列表带 / 前缀；提示词条目描述截断到 250 字符。"""
    long_desc = "x" * 400
    skill = SkillDefinition(
        name="s", description=long_desc, content="c", source="user"
    )
    entry = format_prompt_entry(skill)
    assert len(entry) < len(long_desc)
    assert entry.endswith("…")

    skill_short = SkillDefinition(name="s2", description="短", content="c", source="user")
    assert format_prompt_entry(skill_short) == "- **s2**: 短"


def test_user_and_project_commands_are_discovered_with_nested_names(tmp_path: Path, monkeypatch):
    """用户命令优先于项目命令，嵌套目录使用冒号命名。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    user_commands = tmp_path / "config" / "commands"
    user_commands.mkdir(parents=True)
    (user_commands / "review.md").write_text(
        "---\ndescription: User review\n---\nUser $ARGUMENTS\n",
        encoding="utf-8",
    )
    project_commands = project / ".illusion" / "commands"
    (project_commands / "ops").mkdir(parents=True)
    (project_commands / "review.md").write_text(
        "---\ndescription: Project review\n---\nProject review\n",
        encoding="utf-8",
    )
    (project_commands / "ops" / "deploy.md").write_text(
        "---\ndescription: Deploy\n---\nDeploy $1\n",
        encoding="utf-8",
    )

    registry = load_skill_registry(project)
    commands = registry.list_commands()
    assert [command.name for command in commands] == ["ops:deploy", "review"]
    assert registry.get_command("review").description == "User review"
    assert registry.list_skills() == [
        skill for skill in registry.list_skills() if not skill.is_command
    ]

    review = resolve_slash_skill("/review hello", str(project), registry=registry)
    assert review is not None
    assert "User hello" in review.expanded
    assert "User arguments:" not in review.expanded
    assert "description: User review" not in review.expanded

    deploy = resolve_slash_skill("/ops:deploy release", str(project), registry=registry)
    assert deploy is not None
    assert "Deploy release" in deploy.expanded


def test_skill_and_command_same_name_use_separate_registry_spaces(tmp_path: Path, monkeypatch):
    """同名技能和命令都保留，斜杠输入按命令入口激活。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    config = tmp_path / "config"
    (config / "skills" / "same").mkdir(parents=True)
    (config / "skills" / "same" / "SKILL.md").write_text(
        "---\nname: same\ndescription: Skill\n---\nSkill body\n",
        encoding="utf-8",
    )
    (config / "commands").mkdir(parents=True)
    (config / "commands" / "same.md").write_text(
        "---\ndescription: Command\n---\nCommand body\n",
        encoding="utf-8",
    )

    registry = load_skill_registry(tmp_path)
    assert registry.get("same").kind == "skill"
    assert registry.get_command("same").kind == "command"
    skill_names = [skill.name for skill in registry.list_skills()]
    assert skill_names.count("same") == 1
    assert all(not skill.is_command for skill in registry.list_skills())
    assert [command.name for command in registry.list_commands()] == ["same"]
    match = resolve_slash_skill("/same", str(tmp_path), registry=registry)
    assert match is not None
    assert match.kind == "command"
    assert "Command body" in match.expanded


def test_command_deny_and_argument_text_are_not_reexpanded(tmp_path: Path, monkeypatch):
    """禁用命令不可见，参数中的占位符保持原文。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    commands = project / ".illusion" / "commands"
    commands.mkdir(parents=True)
    (commands / "blocked.md").write_text("# blocked\n", encoding="utf-8")
    (commands / "run.md").write_text(
        "---\ndescription: Run\n---\nfirst=$1 all=$ARGUMENTS dir=${CLAUDE_SKILL_DIR}\n",
        encoding="utf-8",
    )
    permissions = project / ".illusion" / "permissions.json"
    permissions.write_text('{"denied_skills": ["blocked"]}', encoding="utf-8")

    registry = load_skill_registry(project)
    assert registry.get_command("blocked") is None
    match = resolve_slash_skill("/run one '${CLAUDE_SKILL_DIR}'", str(project), registry=registry)
    assert match is not None
    assert "first=one" in match.expanded
    assert "all=one '${CLAUDE_SKILL_DIR}'" in match.expanded
    assert f"dir={commands}" in match.expanded


def test_empty_command_is_not_discoverable(tmp_path: Path, monkeypatch):
    """只有 frontmatter 的命令不进入可执行列表。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    commands = tmp_path / "config" / "commands"
    commands.mkdir(parents=True)
    (commands / "empty.md").write_text(
        "---\ndescription: Empty\n---\n", encoding="utf-8"
    )
    registry = load_skill_registry(tmp_path)
    assert registry.get_command("empty") is None


def test_inline_resources_keep_order_deduplicate_and_preserve_head_arguments(tmp_path):
    _setup(tmp_path)
    cwd = str(tmp_path / "repo")
    resources = resolve_slash_resources(
        "请用「/CMDPLUG:alpha」和 /cmdplug:greet，最后再用 /alpha。", cwd,
    )
    assert [(r.name, r.args) for r in resources] == [
        ("cmdplug:alpha", ""), ("cmdplug:greet", ""),
    ]
    assert resources[0].expanded.rstrip().endswith("# Alpha")
    plugin_root = tmp_path / "repo" / ".illusion" / "plugins" / "cmd-plugin"
    assert resources[1].expanded.rstrip().endswith(f"Greet  from {plugin_root}/assets")
    args = '"two words" @src/test.py and /alpha'
    resources = resolve_slash_resources(f"/cmdplug:greet {args}", cwd)
    assert [(r.name, r.args) for r in resources] == [
        ("cmdplug:greet", args), ("cmdplug:alpha", ""),
    ]
    assert f"Greet {args} from" in resources[0].expanded


def test_only_standalone_registered_resources_in_prose_activate(tmp_path):
    _setup(tmp_path)
    cwd = str(tmp_path / "repo")
    line = (
        "Unregistered /unknown and ambiguous /greet. Paths: /alpha/data ./alpha ../alpha "
        "~/alpha /alpha.md /alpha?arg=1 /alpha#anchor C:/alpha C:\\alpha file/alpha //alpha "
        "[link](/alpha) https://example.com/alpha https://example.com/x?y=(/alpha) "
        '@src/alpha @"named dir /alpha" @[label /alpha](illusion-session:123abc456def).\n'
        "Inline `use /alpha` and ``literal ` /alpha``.\n"
        "   ````py\n/alpha\n```\n/alpha\n````\n"
        "~~~md\n/alpha\n~~~\n正文：/cmdplug:greet!\n```unclosed\n/alpha"
    )
    assert [r.name for r in resolve_slash_resources(line, cwd)] == ["cmdplug:greet"]
    permissions = tmp_path / "repo" / ".illusion" / "permissions.json"
    permissions.write_text('{"denied_skills": ["cmdplug:alpha"]}', encoding="utf-8")
    assert not resolve_slash_resources("用 /cmdplug:alpha 或 /alpha 分析", cwd)
    assert not resolve_slash_resources("unfinished `example /cmdplug:greet", cwd)
    assert not resolve_slash_resources("test @\"unfinished /cmdplug:greet", cwd)
    assert not resolve_slash_resources("see //host?q=(/cmdplug:greet)", cwd)


def test_markdown_links_and_reference_definitions_are_literal(tmp_path, monkeypatch):
    _setup(tmp_path)
    cwd = str(tmp_path / "repo")
    registry = load_skill_registry(cwd)
    lookup = MagicMock(wraps=registry.list_entries)
    monkeypatch.setattr(registry, "list_entries", lookup)
    prompt = (
        'See [docs](/guide(v1) "/alpha"). '
        r'''[docs](</guide(v2> 'a ) /alpha') ![image](/a(b\)c) "/alpha").'''
        '\n[ref]: /alpha\n[other]: <any/path>\n  "/alpha"'
        '\n> [nested]: /alpha (use /alpha)\nThen use /cmdplug:greet.'
    )
    activation = prepare_slash_submission(prompt, cwd, registry=registry)
    assert activation.references == {"/cmdplug:greet": "command"}
    assert [r.name for r in activation.matches] == ["cmdplug:greet"]
    lookup.assert_called_once_with()


@pytest.mark.parametrize("surface", ["terminal", "web"])
async def test_slash_submission_transcript_restore_and_rewind(tmp_path, monkeypatch, surface):
    """从 host 的 submit_line 消费者到假模型，以及真实 JSONL 恢复/回退。"""
    from illusion.api.client import ApiMessageCompleteEvent
    from illusion.api.usage import UsageSnapshot
    from illusion.engine.messages import ConversationMessage, TextBlock
    from illusion.services.checkpoint_store import CheckpointStore
    from illusion.services.session_reference import format_session_mention
    from illusion.services.session_storage import read_meta, session_dir_for
    from illusion.ui.backend_host import BackendHostConfig, ReactBackendHost
    from illusion.ui.protocol import FrontendRequest
    from illusion.ui.runtime import build_runtime, close_runtime
    from illusion.ui.web.session_runtime import SessionRuntime
    from illusion.ui.web.ws_host import WebBackendHost, WebHostConfig
    from illusion.ui.web.ws_web_api import build_replay_items

    cwd = str(tmp_path / "repo")
    _setup(tmp_path)
    ref_id = "123abc456def"
    source = CheckpointStore(session_dir_for(cwd, ref_id), ref_id)
    await source.append_message(ConversationMessage.from_user_text("original session detail"))
    # 内置名碰撞，即使是 Web 文本通道或在句中，也不能执行这份资源。
    command_dir = tmp_path / "repo" / ".illusion" / "commands"
    command_dir.mkdir()
    (command_dir / "exit.md").write_text("SHADOWED EXIT", encoding="utf-8")
    # 正文里的 @ 或 / 只是资源文本，不能递归激活第二个引用或资源。
    skill_file = tmp_path / "repo/.illusion/plugins/cmd-plugin/skills/alpha/SKILL.md"
    skill_file.write_text(
        "---\nname: alpha\ndescription: Skill\n"
        "hooks:\n  Stop:\n    - hooks: [{type: prompt, prompt: fixture}]\n---\n"
        "# Alpha instructions\nexample @[ignore](illusion-session:aaaaaaaaaaaa) and /otherplug:greet",
        encoding="utf-8",
    )
    requests = []

    class RecordingClient:
        async def stream_message(self, request):
            requests.append([msg.text for msg in request.messages if msg.role == "user"])
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
                usage=UsageSnapshot(), stop_reason="end_turn",
            )

    client = RecordingClient()
    bundle = await build_runtime(api_client=client, cwd=cwd)
    emit = AsyncMock()
    if surface == "terminal":
        host = ReactBackendHost(BackendHostConfig(api_client=client))

        async def submit(line, *, text=True):
            request = FrontendRequest(type="submit_line", line=line)
            host._dispatch_stdin_line(request.model_dump_json())
            await host._process_request(host._request_queue.get_nowait())
    else:
        host = WebBackendHost(WebHostConfig(api_client=client), MagicMock())
        session = SessionRuntime(session_id=bundle.session_id, bundle=bundle, workspace_cwd=cwd)
        host._sessions[session.session_id] = session
        host._active_session_id = session.session_id

        async def submit(line, *, text=True):
            await host._dispatch_request(FrontendRequest(
                type="submit_line", line=line, session_id=session.session_id, treat_as_text=text,
            ))
            assert session.active_line_task is not None
            await session.active_line_task

    host._bundle = bundle
    monkeypatch.setattr(host, "_emit", emit)
    raw = f"请用 /cmdplug:alpha 和 /AlPhA 检查 @file.py 和 {format_session_mention(ref_id, '资料')}"
    references = {"/cmdplug:alpha": "skill", "/AlPhA": "skill", "/cmdplug:greet": "command"}
    # 单独出现与附件同名的标题也是用户输入，标题后的资源应正确激活。
    raw += "\n\n# Web page elements:\n\n这只是普通标题，再用 /cmdplug:greet。"
    attachment = (
        "\n\n# Web page elements:\n\n## Element 1\nURL: https://example.org/\n"
        "Title: use /otherplug:greet now\nTag: button\nAccessible name: /otherplug:greet"
    )
    if surface == "web":
        raw += attachment
    try:
        await submit(raw)
        model_user, snapshot = requests[-1]
        assert model_user.startswith(raw)
        assert "# Alpha instructions" in model_user and "Greet  from" in model_user
        assert str(skill_file) not in model_user
        assert "<skill-location>" not in model_user and "<command-location>" not in model_user
        assert "<command-name>/otherplug:greet" not in model_user
        assert "original session detail" in snapshot and "aaaaaaaaaaaa" not in snapshot
        assert load_skill_registry(cwd).get("cmdplug:alpha").hooks
        assert bundle.engine.tool_metadata["session_hook_store"].get(bundle.session_id) == {}
        store = bundle.engine.checkpoint_store
        saved = await store.restore()
        assert build_replay_items(saved.messages)[0] == {
            "role": "user", "text": raw, "slash_references": references,
        }
        assert build_replay_items([ConversationMessage.model_validate({
            "role": "user", "content": [{"type": "text", "text": "old /AlPhA"}],
        })]) == [{"role": "user", "text": "old /AlPhA"}]
        assert read_meta(cwd, bundle.session_id)["turn_count"] == 1
        assert read_meta(cwd, bundle.session_id)["summary"] == raw[:80]
        # 模拟重启：磁盘上的资源已变化，历史发给模型的正文保持提交时的版本。
        skill_file.write_text("---\nname: alpha\n---\nChanged instructions", encoding="utf-8")
        bundle.engine.apply_restore(saved)
        second = "/cmdplug:greet @file.py"
        if surface == "web":
            second += " /alpha /exit" + attachment
        await submit(second)
        latest = requests[-1][-1]
        assert latest.startswith(second) and "Greet @file.py" in latest
        assert "# Alpha instructions" in requests[-1][0]
        if surface == "web":
            assert "<command-args>@file.py /alpha /exit</command-args>" in latest
            assert "Changed instructions" in latest and "SHADOWED EXIT" not in latest
            assert "<command-name>/otherplug:greet" not in latest
        await submit("/rewind 1 conversation", text=False)
        events = [call.args[0] for call in emit.call_args_list]
        live = [ev.item.text for ev in events if ev.type == "transcript_item"
                and ev.item.role == "user" and not ev.item.is_command]
        assert live == [raw, second]
        first_live = next(ev.item for ev in events if ev.type == "transcript_item" and ev.item.text == raw)
        assert first_live.slash_references == references
        restored = next(ev.restored_text for ev in events if ev.type == "session_rewind")
        assert restored == second
        replay = next(ev for ev in events if ev.type == "replace_transcript")
        assert [item.text for item in replay.items if item.role == "user"] == [raw]
        if surface == "web":
            await submit(restored)  # 重新生成走文本通道，用相同的正文边界
            assert requests[-1][-1] == latest
            await submit("/exit")
            assert requests[-1][-1] == "/exit"
            assert bundle.engine.messages[-2].slash_references == {}

        # 卸载命令、让技能短名产生歧义，再走宿主的实际磁盘恢复和重放。
        plugins = tmp_path / "repo/.illusion/plugins"
        (plugins / "cmd-plugin/commands/greet.md").unlink()
        (plugins / "cmd-plugin/commands/alpha.md").write_text("Same name, now a command", encoding="utf-8")
        current = prepare_slash_submission(raw, cwd).references
        assert current["/cmdplug:alpha"] == "command" and "/AlPhA" not in current
        assert resolve_slash_skill("/cmdplug:greet", cwd) is None
        emit.reset_mock()
        await submit(f"/resume {bundle.session_id}", text=False)
        replay = next(call.args[0] for call in emit.call_args_list if call.args[0].type == "replace_transcript")
        assert next(item for item in replay.items if item.text == raw).slash_references == references
        assert build_replay_items(bundle.engine.messages)[0]["slash_references"] == references
    finally:
        await close_runtime(bundle)
