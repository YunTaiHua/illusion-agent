"""Tests for built-in tools."""

from __future__ import annotations

import asyncio
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
    assert "repl" in names
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
