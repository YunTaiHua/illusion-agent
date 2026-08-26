"""Tests for built-in tools."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from illusion.platforms import get_platform
from illusion.tools import create_default_tool_registry
from illusion.tools.base import ToolExecutionContext
from illusion.tools.bash_tool import BashTool, BashToolInput
from illusion.tools.config_tool import ConfigTool, ConfigToolInput
from illusion.tools.enter_worktree_tool import EnterWorktreeTool, EnterWorktreeToolInput
from illusion.tools.file_edit_tool import FileEditTool, FileEditToolInput
from illusion.tools.file_read_tool import FileReadTool, FileReadToolInput
from illusion.tools.file_write_tool import FileWriteTool, FileWriteToolInput
from illusion.tools.glob_tool import GlobTool, GlobToolInput
from illusion.tools.grep_tool import GrepTool, GrepToolInput
from illusion.tools.lsp_tool import LspTool, LspToolInput
from illusion.tools.skill_tool import SkillTool, SkillToolInput
from illusion.tools.todo_write_tool import TodoWriteTool, TodoWriteToolInput
from illusion.utils.file_state_cache import FileStateCache


def _make_context(tmp_path: Path) -> ToolExecutionContext:
    """创建带有文件状态缓存的工具执行上下文。"""
    cache = FileStateCache()
    return ToolExecutionContext(cwd=tmp_path, metadata={"file_state_cache": cache})


@pytest.mark.asyncio
async def test_file_write_read_and_edit(tmp_path: Path):
    context = _make_context(tmp_path)

    write_result = await FileWriteTool().execute(
        FileWriteToolInput(path="notes.txt", content="one\ntwo\nthree\n"),
        context,
    )
    assert write_result.is_error is False
    assert (tmp_path / "notes.txt").exists()

    # Write 工具创建新文件后会自动写入缓存，无需手动 mark_file_read

    read_result = await FileReadTool().execute(
        FileReadToolInput(path="notes.txt", offset=1, limit=2),
        context,
    )
    assert "2\ttwo" in read_result.output
    assert "3\tthree" in read_result.output

    # Read 工具会自动写入缓存，无需手动 mark_file_read

    edit_result = await FileEditTool().execute(
        FileEditToolInput(path="notes.txt", old_str="two", new_str="TWO"),
        context,
    )
    assert edit_result.is_error is False
    assert "TWO" in (tmp_path / "notes.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_concurrent_edits_same_file_serialized(tmp_path: Path):
    """并发编辑同一文件时文件级互斥锁生效，两个修改都不丢失。

    回归：引擎并发执行同一消息中的多个工具调用，若无文件级锁，
    两个 edit_file 基于同一快照读-改-写，后写者覆盖先写者
    （表现为"工具返回成功但修改丢失"）。
    """
    context = _make_context(tmp_path)
    (tmp_path / "notes.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
    # 读取以满足编辑前置检查（读后编辑强制检查基于缓存）
    read_result = await FileReadTool().execute(
        FileReadToolInput(path="notes.txt"),
        context,
    )
    assert read_result.is_error is False

    # 同一实例并发编辑同一文件的不同位置（模拟引擎多工具并发分支）
    tool = FileEditTool()
    results = await asyncio.gather(
        tool.execute(
            FileEditToolInput(path="notes.txt", old_string="b", new_string="B"),
            context,
        ),
        tool.execute(
            FileEditToolInput(path="notes.txt", old_string="c", new_string="C"),
            context,
        ),
    )
    assert all(r.is_error is False for r in results), [r.output for r in results]
    content = (tmp_path / "notes.txt").read_text(encoding="utf-8")
    assert "B" in content, "第一个编辑被并发覆盖"
    assert "C" in content, "第二个编辑丢失"
    assert content == "a\nB\nC\nd\n"


def test_atomic_write_replace_retries_on_oserror(tmp_path: Path, monkeypatch):
    """atomic_write 的 os.replace 在 OSError 时指数退避重试。"""
    import illusion.utils.atomic_write as aw

    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello", encoding="utf-8")
    calls = {"n": 0}
    real_replace = os.replace

    class FlakyOS:
        def replace(self, s, d):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(5, "拒绝访问")
            return real_replace(s, d)

    monkeypatch.setattr(aw, "os", FlakyOS())
    # 缩短重试延迟，避免测试等待
    monkeypatch.setattr(aw, "_REPLACE_BASE_DELAY", 0.01)
    aw._replace_with_retry(str(src), str(dst))
    assert dst.read_text(encoding="utf-8") == "hello"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_glob_and_grep(tmp_path: Path):
    context = ToolExecutionContext(cwd=tmp_path)
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    glob_result = await GlobTool().execute(GlobToolInput(pattern="*.py"), context)
    assert sorted(glob_result.output.splitlines()) == ["a.py", "b.py"]

    grep_result = await GrepTool().execute(
        GrepToolInput(pattern=r"def\s+beta", glob="*.py"),
        context,
    )
    assert "b.py" in grep_result.output

    file_root_result = await GrepTool().execute(
        GrepToolInput(pattern=r"def\s+alpha", path="a.py"),
        context,
    )
    assert "a.py" in file_root_result.output


@pytest.mark.asyncio
async def test_bash_tool_runs_command(tmp_path: Path):
    if get_platform() == "windows":
        bash_path = shutil.which("bash")
        normalized = (bash_path or "").replace("/", "\\").lower()
        if (not bash_path) or normalized.endswith("\\windows\\system32\\bash.exe"):
            pytest.skip("No usable bash available on this Windows environment")

    result = await BashTool().execute(
        BashToolInput(command="printf 'hello'"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert result.output == "hello"


@pytest.mark.asyncio
async def test_bash_tool_returns_clear_error_when_bash_missing_on_windows(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("illusion.tools.bash_tool.get_platform", lambda: "windows")
    monkeypatch.setattr("illusion.tools.bash_tool._resolve_windows_bash", lambda: None)

    result = await BashTool().execute(
        BashToolInput(command="rm -rf test"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert (
        "Bash is not available on this Windows machine" in result.output
        or result.is_error is False
    )


@pytest.mark.asyncio
async def test_skill_todo_and_config_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = tmp_path / "config" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "pytest.md").write_text("# Pytest\nHelpful pytest notes.\n", encoding="utf-8")

    skill_result = await SkillTool().execute(
        SkillToolInput(name="Pytest"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert "Helpful pytest notes." in skill_result.output

    todo_result = await TodoWriteTool().execute(
        TodoWriteToolInput(todos=[{"content": "wire commands", "status": "pending", "activeForm": "wiring commands"}]),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert todo_result.is_error is False

    config_result = await ConfigTool().execute(
        ConfigToolInput(action="set", key="ui_language", value="en"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert config_result.output == "Updated ui_language"


@pytest.mark.asyncio
async def test_lsp_tool(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "utils.py").write_text(
        'def greet(name):\n    """Return a greeting."""\n    return f"hi {name}"\n',
        encoding="utf-8",
    )
    context = ToolExecutionContext(cwd=tmp_path)

    # 测试不支持的文件类型
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    result = await LspTool().execute(
        LspToolInput(operation="documentSymbol", filePath="readme.txt"),
        context,
    )
    assert result.is_error is True
    assert "Unsupported file type" in result.output

    # 测试文件不存在
    result = await LspTool().execute(
        LspToolInput(operation="goToDefinition", filePath="pkg/nonexistent.py", line=1, character=1),
        context,
    )
    assert result.is_error is True
    assert "File not found" in result.output

    # 测试缺少 filePath 的操作
    result = await LspTool().execute(
        LspToolInput(operation="goToDefinition"),
        context,
    )
    assert result.is_error is True
    assert "filePath is required" in result.output


def _setup_git_repo(cwd: Path) -> None:
    """初始化一个临时 git 仓库并提交一个 demo 文件。"""
    subprocess.run(["git", "init"], cwd=cwd, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "illusion@example.com"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "IllusionAgent Tests"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    (cwd / "demo.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_worktree_tools(tmp_path: Path):
    await asyncio.to_thread(_setup_git_repo, tmp_path)

    enter_result = await EnterWorktreeTool().execute(
        EnterWorktreeToolInput(branch="feature/demo"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert enter_result.is_error is False
    worktree_path = Path(enter_result.output.split("Path: ", 1)[1].strip())
    assert worktree_path.exists()

    assert worktree_path.exists()


@pytest.mark.asyncio
async def test_cron_tool_add_list_remove(tmp_path: Path, monkeypatch):
    """测试统一 Cron 工具的 add/list/remove 操作。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    context = ToolExecutionContext(cwd=tmp_path)

    from illusion.tools.cron_tool import CronTool, CronToolInput

    tool = CronTool()

    # add: 创建任务
    add_result = await tool.execute(
        CronToolInput(
            action="add",
            name="test-job",
            schedule="0 0 * * *",
            prompt="echo hello",
        ),
        context,
    )
    assert add_result.is_error is False
    assert "test-job" in add_result.output

    # list: 列出任务
    list_result = await tool.execute(
        CronToolInput(action="list"),
        context,
    )
    assert list_result.is_error is False
    assert "0 0 * * *" in list_result.output
    assert "test-job" in list_result.output

    # remove: 删除任务
    remove_result = await tool.execute(
        CronToolInput(action="remove", name="test-job"),
        context,
    )
    assert remove_result.is_error is False
    assert "test-job" in remove_result.output


@pytest.mark.asyncio
async def test_cron_tool_status(tmp_path: Path, monkeypatch):
    """测试 Cron 工具的 status 操作。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    context = ToolExecutionContext(cwd=tmp_path)

    from illusion.tools.cron_tool import CronTool, CronToolInput

    tool = CronTool()
    result = await tool.execute(CronToolInput(action="status"), context)
    assert result.is_error is False
    assert "Scheduler" in result.output


@pytest.mark.asyncio
async def test_cron_tool_update_toggle(tmp_path: Path, monkeypatch):
    """测试 Cron 工具的 update 操作（启用/禁用）。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    context = ToolExecutionContext(cwd=tmp_path)

    from illusion.tools.cron_tool import CronTool, CronToolInput

    tool = CronTool()

    # 先创建任务
    await tool.execute(
        CronToolInput(action="add", name="toggle-test", schedule="* * * * *", prompt="test"),
        context,
    )

    # 禁用任务
    update_result = await tool.execute(
        CronToolInput(action="update", name="toggle-test", enabled=False),
        context,
    )
    assert update_result.is_error is False

    # 验证任务已禁用
    list_result = await tool.execute(
        CronToolInput(action="list", include_disabled=True),
        context,
    )
    assert "toggle-test" in list_result.output


@pytest.mark.asyncio
async def test_cron_tool_invalid_action(tmp_path: Path):
    """测试无效操作返回错误。"""
    from illusion.tools.cron_tool import CronTool, CronToolInput

    tool = CronTool()
    context = ToolExecutionContext(cwd=tmp_path)
    result = await tool.execute(CronToolInput(action="invalid_action"), context)
    assert result.is_error is True
    assert "Unknown action" in result.output


@pytest.mark.asyncio
async def test_cron_tool_missing_schedule(tmp_path: Path):
    """测试 add 操作缺少 schedule 返回错误。"""
    from illusion.tools.cron_tool import CronTool, CronToolInput

    tool = CronTool()
    context = ToolExecutionContext(cwd=tmp_path)
    result = await tool.execute(
        CronToolInput(action="add", prompt="test"),
        context,
    )
    assert result.is_error is True
    assert "Missing required parameter: schedule" in result.output


def test_default_registry_matches_claude_tool_shape():
    registry = create_default_tool_registry()
    names = {tool.name for tool in registry.list_tools()}

    assert "powershell" in names
    assert "team_create" in names
    assert "team_delete" in names

    # 新的统一 cron 工具
    assert "cron" in names

    # 旧的独立 cron 工具不应再存在
    assert "cron_create" not in names
    assert "cron_list" not in names
    assert "cron_delete" not in names
    assert "remote_trigger" not in names


@pytest.mark.asyncio
async def test_bash_tool_empty_success_has_contextual_message(tmp_path: Path):
    """成功命令无输出时返回上下文消息，而非 '(no output)'。"""
    if get_platform() == "windows":
        bash_path = shutil.which("bash")
        normalized = (bash_path or "").replace("/", "\\").lower()
        if (not bash_path) or normalized.endswith("\\windows\\system32\\bash.exe"):
            pytest.skip("No usable bash available on this Windows environment")

    result = await BashTool().execute(
        BashToolInput(command="true"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert result.output != "(no output)"
    assert "successfully" in result.output


@pytest.mark.asyncio
async def test_rewind_restore_clears_read_dedup_cache(tmp_path: Path):
    """rewind/resume（apply_restore）后 read_file 不再命中去重缓存。

    回归：apply_restore 若不清理 file_state_cache，回退后模型上下文
    已无之前 read 的内容，但 read_file 去重命中返回占位而非正文。
    """
    from illusion.engine.cost_tracker import CostTracker
    from illusion.engine.messages import ConversationMessage
    from illusion.engine.query_engine import QueryEngine
    from illusion.services.checkpoint_store import RestoreResult
    from illusion.tools.file_read_tool import FileReadTool, FileReadToolInput
    from illusion.utils.file_state_cache import FileStateCache

    target = tmp_path / "notes.txt"
    target.write_text("hello rewind\n", encoding="utf-8")

    # 用 object.__new__ 绕过构造（QueryEngine 依赖太多），
    # 仅注入 apply_restore 访问的字段
    engine = object.__new__(QueryEngine)
    engine._file_state_cache = FileStateCache()
    engine._messages = []
    engine._cost_tracker = CostTracker()
    engine._last_api_usage = None
    engine._last_api_usage_message_count = 0
    engine._goal_manager = None

    ctx = ToolExecutionContext(
        cwd=tmp_path,
        metadata={"file_state_cache": engine._file_state_cache},
    )
    tool = FileReadTool()

    # 第一次读：正文注入 + 缓存记录
    first = await tool.execute(FileReadToolInput(path="notes.txt"), ctx)
    assert "hello rewind" in first.output

    # 第二次读：去重缓存命中，返回占位
    second = await tool.execute(FileReadToolInput(path="notes.txt"), ctx)
    assert "unchanged since last read" in second.output

    # rewind/resume 回退状态
    engine.apply_restore(RestoreResult(
        messages=[ConversationMessage.from_user_text("x")],
        usage_input=0, usage_output=0,
        usage_cache_read=0, usage_cache_creation=0,
        last_usage=None, last_usage_message_count=0,
        checkpoint_count=1,
    ))
    assert engine._file_state_cache.size == 0

    # 第三次读：缓存已清空，正文重新注入
    third = await tool.execute(FileReadToolInput(path="notes.txt"), ctx)
    assert "hello rewind" in third.output
    assert "unchanged since last read" not in third.output


@pytest.mark.asyncio
async def test_load_messages_clears_read_dedup_cache(tmp_path: Path):
    """compact（load_messages 替换对话历史）后 read_file 不再命中去重缓存。

    手动 /compact 与子引擎恢复都走 engine.load_messages；对话历史被摘要
    替换后旧缓存不可靠，必须清空让 read_file 重新注入正文。
    """
    from illusion.engine.messages import ConversationMessage
    from illusion.engine.query_engine import QueryEngine
    from illusion.tools.file_read_tool import FileReadTool, FileReadToolInput
    from illusion.utils.file_state_cache import FileStateCache

    target = tmp_path / "notes.txt"
    target.write_text("hello compact\n", encoding="utf-8")

    engine = object.__new__(QueryEngine)
    engine._file_state_cache = FileStateCache()
    engine._messages = []

    ctx = ToolExecutionContext(
        cwd=tmp_path,
        metadata={"file_state_cache": engine._file_state_cache},
    )
    tool = FileReadTool()

    first = await tool.execute(FileReadToolInput(path="notes.txt"), ctx)
    assert "hello compact" in first.output
    second = await tool.execute(FileReadToolInput(path="notes.txt"), ctx)
    assert "unchanged since last read" in second.output

    # compact：替换对话历史（摘要替代旧消息）
    engine.load_messages([ConversationMessage.from_user_text("summary")])
    assert engine._file_state_cache.size == 0

    third = await tool.execute(FileReadToolInput(path="notes.txt"), ctx)
    assert "hello compact" in third.output
    assert "unchanged since last read" not in third.output
