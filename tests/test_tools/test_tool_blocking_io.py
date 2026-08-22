"""工具层同步 I/O 异步化测试。

验证 Task 18 的修复目标：所有工具的同步阻塞 I/O 已用 ``asyncio.to_thread`` 包装，
子进程执行改用 ``asyncio.create_subprocess_exec``，且 bash/powershell 的 I/O
不会在持有锁的同时阻塞事件循环。

测试策略：
    - 源码检查：通过 ``inspect.getsource`` 验证关键位置使用了 ``asyncio.to_thread``
      或 ``create_subprocess_exec``。
    - 行为检查：调用工具时不再直接调用同步 I/O 接口（通过 monkeypatch 注入假对象，
      验证调用了线程池路径）。
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
from pathlib import Path

import pytest

from illusion.tools import (
    bash_tool,
    enter_worktree_tool,
    exit_worktree_tool,
    lsp_tool,
    powershell_tool,
)
from illusion.tools.base import ToolExecutionContext
from illusion.tools.bash_tool import BashTool, BashToolInput
from illusion.tools.config_tool import ConfigTool, ConfigToolInput
from illusion.tools.enter_worktree_tool import EnterWorktreeTool, EnterWorktreeToolInput
from illusion.tools.exit_worktree_tool import ExitWorktreeTool, ExitWorktreeToolInput
from illusion.tools.file_edit_tool import FileEditTool, FileEditToolInput
from illusion.tools.file_read_tool import FileReadTool, FileReadToolInput
from illusion.tools.file_write_tool import FileWriteTool, FileWriteToolInput
from illusion.tools.skill_tool import SkillTool, SkillToolInput
from illusion.utils import ripgrep
from illusion.utils.file_state_cache import FileStateCache

# ---------------------------------------------------------------------------
# 源码检查：验证关键路径使用了 asyncio.to_thread / create_subprocess_exec
# ---------------------------------------------------------------------------


def _src(obj: object) -> str:
    """获取对象源码字符串。"""
    return inspect.getsource(obj)


def test_file_read_tool_uses_asyncio_to_thread():
    """FileReadTool.execute 应使用 asyncio.to_thread 包装 path.exists 等同步 I/O。"""
    src = _src(FileReadTool.execute)
    assert "asyncio.to_thread(path.exists)" in src
    assert "asyncio.to_thread(path.is_dir)" in src


def test_file_read_tool_helpers_are_async():
    """FileReadTool 的读取助手应转为 async 方法。"""
    for name in ("_read_image_file", "_read_text_file"):
        method = getattr(FileReadTool, name)
        assert inspect.iscoroutinefunction(method), f"{name} 应为 async 方法"


def test_file_edit_tool_uses_asyncio_to_thread():
    """FileEditTool.execute/_do_edit 应使用 asyncio.to_thread 包装同步 I/O。"""
    src = _src(FileEditTool.execute)
    assert "asyncio.to_thread(path.exists)" in src
    assert "asyncio.to_thread(atomic_write_text" in src
    assert "asyncio.to_thread(os.path.getmtime" in src
    # 已存在文件编辑逻辑提取到 _do_edit（execute 中加文件级互斥锁调用）
    do_src = _src(FileEditTool._do_edit)
    assert "asyncio.to_thread(path.read_text" in do_src
    assert "asyncio.to_thread(atomic_write_text" in do_src
    assert "asyncio.to_thread(os.path.getmtime" in do_src


def test_file_write_tool_uses_asyncio_to_thread():
    """FileWriteTool.execute 应使用 asyncio.to_thread 包装同步 I/O。"""
    src = _src(FileWriteTool.execute)
    assert "asyncio.to_thread(path.exists)" in src
    assert "asyncio.to_thread(atomic_write_text" in src
    assert "asyncio.to_thread(os.path.getmtime" in src
    assert "asyncio.to_thread(path.read_text" in src


def test_bash_tool_background_uses_to_thread_for_io():
    """BashTool 后台任务中文件追加写入应通过 asyncio.to_thread 完成。"""
    # 模块级 _append_chunk 助手存在
    assert hasattr(bash_tool, "_append_chunk")
    src = _src(bash_tool.BashTool.execute)
    # 在锁内调用 asyncio.to_thread(_append_chunk, ...)
    assert "asyncio.to_thread(_append_chunk" in src
    assert "asyncio.to_thread(record.output_file.write_text" in src


def test_powershell_tool_background_uses_to_thread_for_io():
    """PowerShellTool 后台任务中文件追加写入应通过 asyncio.to_thread 完成。"""
    assert hasattr(powershell_tool, "_append_chunk")
    src = _src(powershell_tool.PowerShellTool.execute)
    assert "asyncio.to_thread(_append_chunk" in src
    assert "asyncio.to_thread(record.output_file.write_text" in src


def test_bash_tool_no_sync_file_open_in_lock():
    """BashTool 后台等待协程中不应在锁内直接调用 record.output_file.open。"""
    src = _src(bash_tool.BashTool.execute)
    # 移除 asyncio.to_thread(_append_chunk, ...) 这一行后，源码中不应再出现
    # `with record.output_file.open("ab")` 这样的同步调用
    assert 'with record.output_file.open("ab")' not in src


def test_powershell_tool_no_sync_file_open_in_lock():
    """PowerShellTool 后台等待协程中不应在锁内直接调用 record.output_file.open。"""
    src = _src(powershell_tool.PowerShellTool.execute)
    assert 'with record.output_file.open("ab")' not in src


def test_enter_worktree_uses_create_subprocess_exec():
    """EnterWorktreeTool 应使用 asyncio.create_subprocess_exec 而非 subprocess.run。

    实现通过 ``_create_git_subprocess`` 助手调用 ``asyncio.create_subprocess_exec``，
    因此检查整个模块源码而非单个方法。
    """
    src = inspect.getsource(enter_worktree_tool)
    assert "create_subprocess_exec" in src
    assert "subprocess.run" not in src
    # _git_output 与 _create_git_subprocess 均应为 async 函数
    assert inspect.iscoroutinefunction(enter_worktree_tool._git_output)
    assert inspect.iscoroutinefunction(enter_worktree_tool._create_git_subprocess)


def test_exit_worktree_uses_create_subprocess_exec():
    """ExitWorktreeTool 应使用 asyncio.create_subprocess_exec 而非 subprocess.run。

    实现通过 ``_create_git_subprocess`` 助手调用 ``asyncio.create_subprocess_exec``，
    因此检查整个模块源码而非单个方法。
    """
    src = inspect.getsource(exit_worktree_tool)
    assert "create_subprocess_exec" in src
    assert "subprocess.run" not in src
    # _git_output 与 _create_git_subprocess 均应为 async 函数
    assert inspect.iscoroutinefunction(exit_worktree_tool._git_output)
    assert inspect.iscoroutinefunction(exit_worktree_tool._create_git_subprocess)


def test_ripgrep_ensure_ripgrep_uses_to_thread():
    """ripgrep.ensure_ripgrep 应使用 asyncio.to_thread 包装 find_rg_path 与 download_rg。"""
    src = _src(ripgrep.ensure_ripgrep)
    assert "asyncio.to_thread(find_rg_path)" in src
    assert "asyncio.to_thread(download_rg)" in src


def test_lsp_tool_workspace_symbol_uses_to_thread_for_rglob():
    """LspTool._workspace_symbol 应使用 asyncio.to_thread 包装 root.rglob。"""
    src = _src(lsp_tool.LspTool._workspace_symbol)
    assert "asyncio.to_thread" in src
    assert "rglob" in src


def test_lsp_tool_open_file_uses_to_thread_for_read():
    """LspTool._open_file 应使用 asyncio.to_thread 包装 file_path.read_text。"""
    src = _src(lsp_tool.LspTool._open_file)
    assert "asyncio.to_thread(file_path.read_text" in src


def test_config_tool_uses_to_thread():
    """ConfigTool.execute 应使用 asyncio.to_thread 包装 load_settings/save_settings。"""
    src = _src(ConfigTool.execute)
    assert "asyncio.to_thread(load_settings)" in src
    assert "asyncio.to_thread(save_settings" in src


def test_skill_tool_uses_to_thread():
    """SkillTool.execute 应使用 asyncio.to_thread 包装 load_skill_registry。"""
    src = _src(SkillTool.execute)
    assert "asyncio.to_thread(load_skill_registry" in src


# ---------------------------------------------------------------------------
# 行为检查：实际调用工具，验证不阻塞事件循环且功能正常
# ---------------------------------------------------------------------------


def _make_context(tmp_path: Path) -> ToolExecutionContext:
    """创建带文件状态缓存的执行上下文。"""
    cache = FileStateCache()
    return ToolExecutionContext(cwd=tmp_path, metadata={"file_state_cache": cache})


@pytest.mark.asyncio
async def test_file_read_tool_still_reads_file(tmp_path: Path):
    """FileReadTool 异步化后仍能正确读取文件内容。"""
    (tmp_path / "demo.txt").write_text("hello world\nline two\n", encoding="utf-8")
    result = await FileReadTool().execute(
        FileReadToolInput(path=str(tmp_path / "demo.txt")),
        _make_context(tmp_path),
    )
    assert result.is_error is False
    assert "hello world" in result.output


@pytest.mark.asyncio
async def test_file_write_tool_still_writes_file(tmp_path: Path):
    """FileWriteTool 异步化后仍能正确写入文件。"""
    result = await FileWriteTool().execute(
        FileWriteToolInput(path="out.txt", content="some content\n"),
        _make_context(tmp_path),
    )
    assert result.is_error is False
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "some content\n"


@pytest.mark.asyncio
async def test_file_edit_tool_still_edits_file(tmp_path: Path):
    """FileEditTool 异步化后仍能正确编辑文件。"""
    target = tmp_path / "edit.txt"
    # 使用同一个 ctx：FileWrite 创建新文件后会自动写入缓存，
    # 随后 FileEdit 复用同一缓存通过读后编辑校验
    ctx = _make_context(tmp_path)
    await FileWriteTool().execute(
        FileWriteToolInput(path="edit.txt", content="alpha\nbeta\n"),
        ctx,
    )
    result = await FileEditTool().execute(
        FileEditToolInput(path="edit.txt", old_str="beta", new_str="BETA"),
        ctx,
    )
    assert result.is_error is False
    assert "BETA" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_config_tool_show_returns_settings(tmp_path: Path, monkeypatch):
    """ConfigTool 异步化后 show 仍能返回当前配置。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    result = await ConfigTool().execute(
        ConfigToolInput(action="show"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    # 默认 Settings 序列化后应包含 ui_language 字段
    assert "ui_language" in result.output


@pytest.mark.asyncio
async def test_skill_tool_loads_skill(tmp_path: Path, monkeypatch):
    """SkillTool 异步化后仍能加载已注册技能。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = tmp_path / "config" / "skills"
    skills_dir.mkdir(parents=True)
    # 标题写作 "# Demo" — parse_skill_markdown 会以标题作为技能名（默认名为文件 stem "demo"）
    (skills_dir / "demo.md").write_text("# Demo\nHello world.\n", encoding="utf-8")
    result = await SkillTool().execute(
        SkillToolInput(name="Demo"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert "Hello world." in result.output


def _setup_git_repo(cwd: Path) -> None:
    """初始化一个临时 git 仓库并提交一个 demo 文件。"""
    subprocess.run(["git", "init"], cwd=cwd, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "illusion@example.com"],
        cwd=cwd, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "IllusionAgent Tests"],
        cwd=cwd, check=True, capture_output=True, text=True,
    )
    (cwd / "demo.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


@pytest.mark.asyncio
async def test_worktree_tools_end_to_end(tmp_path: Path):
    """EnterWorktreeTool / ExitWorktreeTool 异步化后仍能正确创建与移除工作树。"""
    # 准备一个真实的 git 仓库
    await asyncio.to_thread(_setup_git_repo, tmp_path)

    enter_result = await EnterWorktreeTool().execute(
        EnterWorktreeToolInput(name="feature-async"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert enter_result.is_error is False
    worktree_path = Path(enter_result.output.split("Path: ", 1)[1].strip())
    assert worktree_path.exists()

    # 进入工作树后退出（remove 模式）
    exit_result = await ExitWorktreeTool().execute(
        ExitWorktreeToolInput(action="remove"),
        ToolExecutionContext(cwd=worktree_path),
    )
    assert exit_result.is_error is False
    assert not worktree_path.exists()


# ---------------------------------------------------------------------------
# ripgrep ensure_ripgrep 异步路径验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_ripgrep_runs_find_rg_path_in_thread(monkeypatch, tmp_path: Path):
    """ensure_ripgrep 应通过 asyncio.to_thread 调用 find_rg_path。"""
    rg_name = "rg.exe" if __import__("sys").platform == "win32" else "rg"
    rg_path = str(tmp_path / rg_name)
    Path(rg_path).write_text("", encoding="utf-8")

    call_count = {"n": 0}

    def fake_find_rg_path() -> str:
        call_count["n"] += 1
        return rg_path

    monkeypatch.setattr("illusion.utils.ripgrep.find_rg_path", fake_find_rg_path)
    result = await ripgrep.ensure_ripgrep()
    assert result == rg_path
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_ensure_ripgrep_runs_download_rg_in_thread(monkeypatch, tmp_path: Path):
    """ensure_ripgrep 在 find_rg_path 失败时应通过 asyncio.to_thread 调用 download_rg。"""
    rg_path = str(tmp_path / "rg")

    def fake_find_rg_path() -> str:
        raise ripgrep.RipgrepNotFoundError("test")

    monkeypatch.setattr("illusion.utils.ripgrep.find_rg_path", fake_find_rg_path)

    call_count = {"n": 0}

    def fake_download_rg() -> str:
        call_count["n"] += 1
        return rg_path

    monkeypatch.setattr("illusion.utils.ripgrep.download_rg", fake_download_rg)
    result = await ripgrep.ensure_ripgrep()
    assert result == rg_path
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# BashTool 后台任务 I/O 路径验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bash_background_writes_use_to_thread(tmp_path: Path, monkeypatch):
    """BashTool 后台任务写日志时应通过 asyncio.to_thread 调用 _append_chunk。"""
    import asyncio as _asyncio
    import sys

    if sys.platform == "win32":
        from illusion.utils.shell import _resolve_windows_bash
        if not _resolve_windows_bash():
            pytest.skip("bash is not available on this Windows machine")

    from illusion.engine.query import BackgroundAgentTracker

    # 拦截 asyncio.to_thread 调用，记录 _append_chunk 的调用次数
    real_to_thread = _asyncio.to_thread
    append_calls: list[int] = []

    async def fake_to_thread(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "_append_chunk":
            append_calls.append(len(args))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr("illusion.tools.bash_tool.asyncio.to_thread", fake_to_thread)

    tracker = BackgroundAgentTracker()
    context = ToolExecutionContext(
        cwd=tmp_path,
        metadata={"bg_agent_tracker": tracker},
    )
    tool = BashTool()
    result = await tool.execute(
        BashToolInput(command="echo hello", run_in_background=True),
        context,
    )
    assert result.is_error is False

    # 等待后台任务写完输出
    task_id = result.output.split("task_id=", 1)[1].split(")")[0].strip()
    from illusion.tasks.manager import get_task_manager
    manager = get_task_manager()
    record = manager._tasks.get(task_id)
    assert record is not None
    # 等待异步任务完成
    if record.async_task is not None:
        try:
            await _asyncio.wait_for(record.async_task, timeout=5)
        except _asyncio.TimeoutError:
            pass

    # 至少调用过一次 _append_chunk（写入 stdout 字节）
    assert len(append_calls) > 0, "后台任务应通过 asyncio.to_thread 调用 _append_chunk"
