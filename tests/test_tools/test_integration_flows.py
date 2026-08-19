"""Higher-level integration flows across multiple built-in tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from illusion.tasks.manager import get_task_manager
from illusion.tools import create_default_tool_registry
from illusion.tools.base import ToolExecutionContext
from illusion.utils.file_state_cache import FileStateCache


@pytest.mark.asyncio
async def test_search_edit_flow_across_registry(tmp_path: Path):
    registry = create_default_tool_registry()
    cache = FileStateCache()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry, "file_state_cache": cache})

    write = registry.get("write_file")
    glob = registry.get("glob")
    grep = registry.get("grep")
    edit = registry.get("edit_file")
    read = registry.get("read_file")

    await write.execute(
        write.input_model(path="src/demo.py", content="alpha\nbeta\n"),
        context,
    )
    await read.execute(read.input_model(path="src/demo.py"), context)
    # Read 工具会自动写入缓存，无需手动 mark_file_read
    glob_result = await glob.execute(glob.input_model(pattern="**/*.py"), context)
    assert "src" in glob_result.output and "demo.py" in glob_result.output

    grep_result = await grep.execute(
        grep.input_model(pattern="beta", glob="**/*.py"),
        context,
    )
    assert "demo.py" in grep_result.output

    edit_result = await edit.execute(
        edit.input_model(path="src/demo.py", old_str="beta", new_str="gamma"),
        context,
    )
    assert edit_result.is_error is False
    read_result = await read.execute(read.input_model(path="src/demo.py"), context)
    assert "gamma" in read_result.output
    assert "beta" not in (tmp_path / "src" / "demo.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_task_and_todo_flow_across_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_tool_registry()
    cache = FileStateCache()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry, "file_state_cache": cache})

    todo_write = registry.get("todo_write")
    task_output = registry.get("task_output")

    # task_create/task_get/task_update 工具已删除，仅保留 task_output
    await todo_write.execute(todo_write.input_model(todos=[{"content": "integration flow item", "status": "pending", "activeForm": "integrating flow item"}]), context)

    # 通过 task manager 直接创建后台任务（TaskCreateTool 已删除）
    manager = get_task_manager()
    record = await manager.create_shell_task(
        description="integration flow task",
        cwd=str(tmp_path),
        command="printf 'flow ok'",
    )
    await asyncio.wait_for(manager._waiters[record.id], timeout=5)  # type: ignore[attr-defined]

    output = await task_output.execute(task_output.input_model(task_id=record.id), context)
    assert "flow ok" in output.output


@pytest.mark.asyncio
async def test_skill_and_config_flow_across_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = tmp_path / "config" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "pytest.md").write_text(
        "# Pytest\nPytest fixtures help reuse setup.\n",
        encoding="utf-8",
    )

    registry = create_default_tool_registry()
    cache = FileStateCache()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry, "file_state_cache": cache})

    config = registry.get("config")
    skill = registry.get("skill")

    set_result = await config.execute(
        config.input_model(action="set", key="ui_language", value="en"),
        context,
    )
    assert set_result.output == "Updated ui_language"

    show_result = await config.execute(config.input_model(action="show"), context)
    assert "en" in show_result.output

    skill_result = await skill.execute(skill.input_model(name="Pytest"), context)
    assert "fixtures" in skill_result.output


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Flaky timing-dependent test", strict=False)
async def test_agent_send_message_flow_restarts_completed_agent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_tool_registry()
    cache = FileStateCache()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry, "file_state_cache": cache})

    agent = registry.get("agent")
    send_message = registry.get("send_message")
    task_output = registry.get("task_output")

    create_result = await agent.execute(
        agent.input_model(
            description="echo agent",
            prompt="ready",
            command="python -u -c \"import sys; print('AGENT_ECHO:' + sys.stdin.readline().strip())\"",
        ),
        context,
    )
    task_id = create_result.output.split()[-1]

    for _ in range(80):
        output = await task_output.execute(task_output.input_model(task_id=task_id), context)
        if "AGENT_ECHO:ready" in output.output:
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("initial agent output did not become available in time")

    send_result = await send_message.execute(
        send_message.input_model(task_id=task_id, message="agent ping"),
        context,
    )
    assert send_result.is_error is False

    await asyncio.sleep(0.2)
    for _ in range(80):
        output = await task_output.execute(task_output.input_model(task_id=task_id), context)
        if "AGENT_ECHO:agent ping" in output.output:
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("agent follow-up output did not become available in time")

    assert "AGENT_ECHO:ready" in output.output
    assert "AGENT_ECHO:agent ping" in output.output


@pytest.mark.asyncio
async def test_ask_user_question_flow_across_registry(tmp_path: Path):
    registry = create_default_tool_registry()
    cache = FileStateCache()

    async def _answer(question: str, questions_data: object = None) -> str:
        assert "favorite color" in question
        return {"question-1": "green"}

    context = ToolExecutionContext(
        cwd=tmp_path,
        metadata={"tool_registry": registry, "ask_user_prompt": _answer, "file_state_cache": cache},
    )
    ask_user = registry.get("ask_user_question")
    write = registry.get("write_file")
    read = registry.get("read_file")

    answer_result = await ask_user.execute(
        ask_user.input_model(questions=[{"header": "Color", "question": "What is your favorite color?", "options": [{"label": "Green", "description": "choose green"}, {"label": "Blue", "description": "choose blue"}]}]),
        context,
    )
    assert "green" in answer_result.output

    await write.execute(
        write.input_model(path="answer.txt", content=answer_result.output),
        context,
    )
    read_result = await read.execute(read.input_model(path="answer.txt"), context)
    assert "green" in read_result.output


@pytest.mark.asyncio
async def test_cron_flow_across_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_tool_registry()
    cache = FileStateCache()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry, "file_state_cache": cache})

    cron = registry.get("cron")

    # 使用统一 cron 工具创建任务
    create_result = await cron.execute(
        cron.input_model(action="add", name="flow-test", schedule="0 0 * * *", prompt="printf 'FLOW_CRON_OK'"),
        context,
    )
    assert create_result.is_error is False

    # 列出任务
    list_result = await cron.execute(cron.input_model(action="list"), context)
    assert "0 0 * * *" in list_result.output

    # 删除任务
    delete_result = await cron.execute(cron.input_model(action="remove", name="flow-test"), context)
    assert delete_result.is_error is False


@pytest.mark.asyncio
async def test_lsp_flow_across_registry(tmp_path: Path):
    registry = create_default_tool_registry()
    cache = FileStateCache()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry, "file_state_cache": cache})

    write = registry.get("write_file")
    lsp = registry.get("lsp")

    await write.execute(
        write.input_model(
            path="pkg/utils.py",
            content='def greet(name):\n    """Return a greeting."""\n    return f"hi {name}"\n',
        ),
        context,
    )

    # 测试不支持的文件类型
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    result = await lsp.execute(
        lsp.input_model(operation="documentSymbol", filePath="readme.txt"),
        context,
    )
    assert result.is_error is True
    assert "Unsupported file type" in result.output

    # 测试文件不存在
    result = await lsp.execute(
        lsp.input_model(operation="goToDefinition", filePath="pkg/nonexistent.py", line=1, character=1),
        context,
    )
    assert result.is_error is True
    assert "File not found" in result.output
