"""后台记忆提取状态机测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from illusion.memory.extract import (
    MemoryExtractState,
    _memory_extract_prompt,
    _snapshot_memory_dir,
    build_extract_tool_registry,
)


class FakeEngine:
    """最小 QueryEngine 桩：仅需 cwd 与 messages。"""

    def __init__(self, cwd: str | Path, messages: list | None = None) -> None:
        self.cwd = str(cwd)
        self._messages = messages if messages is not None else []

    @property
    def messages(self) -> list:
        return self._messages


def _enable_auto_extract(monkeypatch, tmp_path: Path) -> None:
    """写入 settings.json 开启 auto_extract（模拟用户显式开启）。"""
    import json

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"memory": {"enabled": True, "auto_extract": True}}),
        encoding="utf-8",
    )


def test_extract_state_initial():
    state = MemoryExtractState(".")
    assert state.last_extracted_index == 0
    assert state.turns_since_extract == 0
    assert state.snapshot is None
    assert state.running is False


@pytest.mark.asyncio
async def test_extract_subagent_uses_configured_model(tmp_path: Path, monkeypatch):
    """提取子代理应使用配置的 extract_model（env_N.model_M 解析）。"""
    import json

    from illusion.memory.extract import _run_extract_task

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    # settings.json：配置 extract_model 指向 env_1.model_2
    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env_1": {
                    "api_format": "openai",
                    "base_url": "https://api.example.com",
                    "model_1": {"name": "gpt-5.4", "capabilities": []},
                    "model_2": {"name": "deepseek-v4-flash", "capabilities": []},
                },
                "memory": {
                    "enabled": True,
                    "auto_extract": True,
                    "extract_model": "env_1.model_2",
                },
            }
        ),
        encoding="utf-8",
    )

    captured: dict = {}

    class CaptureEngine(FakeEngine2):
        """捕获 QueryEngine 构造参数的引擎桩。"""

        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            # 只读/写工具不真正执行，minimal 桩即可
            self._messages = kwargs.get("_messages", [])

        async def submit_message(self, prompt):
            if False:
                yield None  # pragma: no cover

        def load_messages(self, msgs) -> None:
            pass

    engine = CaptureEngine()
    engine.cwd = str(tmp_path / "repo")
    engine.api_client = object()
    engine.model = "default-model"

    from illusion.engine.messages import ConversationMessage

    state = MemoryExtractState(engine.cwd)
    messages = [ConversationMessage.from_user_text("hi")]
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()

    await _run_extract_task(engine, messages, memory_dir, state)

    assert captured.get("model") == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_extract_subagent_inherits_model_when_unset(tmp_path: Path, monkeypatch):
    """未配置 extract_model 时继承当前引擎模型。"""
    from illusion.memory.extract import _run_extract_task

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    captured: dict = {}

    class CaptureEngine(FakeEngine2):
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self._messages = kwargs.get("_messages", [])

        async def submit_message(self, prompt):
            if False:
                yield None  # pragma: no cover

        def load_messages(self, msgs) -> None:
            pass

    engine = CaptureEngine()
    engine.cwd = str(tmp_path / "repo")
    engine.api_client = object()
    engine.model = "current-model"

    from illusion.engine.messages import ConversationMessage

    state = MemoryExtractState(engine.cwd)
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()

    await _run_extract_task(
        engine, [ConversationMessage.from_user_text("hi")], memory_dir, state
    )

    assert captured.get("model") == "current-model"


@pytest.mark.asyncio
async def test_extract_subagent_cross_env_builds_client(tmp_path: Path, monkeypatch):
    """跨 env 模型应独立构建 client（client 与 model 均用配置值）。"""
    import json

    from illusion.memory.extract import _run_extract_task

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    # active env = env_2（model 字段指向），extract_model 指向 env_1 → 跨环境
    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env_1": {
                    "api_format": "openai",
                    "base_url": "https://api.a.com",
                    "api_key": "key-a",
                    "model_1": {"name": "gpt-5.4", "capabilities": []},
                    "model_2": {"name": "deepseek-v4-flash", "capabilities": []},
                },
                "env_2": {
                    "api_format": "anthropic",
                    "base_url": "https://api.b.com",
                    "api_key": "key-b",
                    "model_1": {"name": "claude-x", "capabilities": []},
                },
                "model": "env_2.model_1",
                "memory": {
                    "enabled": True,
                    "auto_extract": True,
                    "extract_model": "env_1.model_2",
                },
            }
        ),
        encoding="utf-8",
    )

    captured: dict = {}
    built_client = object()

    class CaptureEngine(FakeEngine2):
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self._messages = kwargs.get("_messages", [])

        async def submit_message(self, prompt):
            if False:
                yield None  # pragma: no cover

        def load_messages(self, msgs) -> None:
            pass

    engine = CaptureEngine()
    engine.cwd = str(tmp_path / "repo")
    engine.api_client = object()
    engine.model = "default-model"

    import illusion.api.factory as factory_mod
    from illusion.engine.messages import ConversationMessage

    monkeypatch.setattr(factory_mod, "build_api_client_for_env", lambda s, k: built_client)

    state = MemoryExtractState(engine.cwd)
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()

    await _run_extract_task(
        engine, [ConversationMessage.from_user_text("hi")], memory_dir, state
    )

    # 跨 env：client 是独立构建的（非主 client），model 为配置值
    assert captured.get("api_client") is built_client
    assert captured.get("api_client") is not engine.api_client
    assert captured.get("model") == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_extract_subagent_cross_env_build_failure_falls_back(tmp_path: Path, monkeypatch):
    """跨 env client 构建失败时 client 与 model 均回退当前（原子回退）。"""
    import json

    from illusion.memory.extract import _run_extract_task

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env_1": {
                    "api_format": "openai",
                    "base_url": "https://api.a.com",
                    "api_key": "key-a",
                    "model_1": {"name": "gpt-5.4", "capabilities": []},
                    "model_2": {"name": "deepseek-v4-flash", "capabilities": []},
                },
                "env_2": {
                    "api_format": "anthropic",
                    "base_url": "https://api.b.com",
                    "api_key": "key-b",
                    "model_1": {"name": "claude-x", "capabilities": []},
                },
                "model": "env_2.model_1",
                "memory": {
                    "enabled": True,
                    "auto_extract": True,
                    "extract_model": "env_1.model_2",
                },
            }
        ),
        encoding="utf-8",
    )

    captured: dict = {}

    class CaptureEngine(FakeEngine2):
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self._messages = kwargs.get("_messages", [])

        async def submit_message(self, prompt):
            if False:
                yield None  # pragma: no cover

        def load_messages(self, msgs) -> None:
            pass

    engine = CaptureEngine()
    engine.cwd = str(tmp_path / "repo")
    engine.api_client = object()
    engine.model = "default-model"

    import illusion.api.factory as factory_mod
    from illusion.engine.messages import ConversationMessage

    def _fail_build(settings, env_key):
        raise ValueError("no credentials")

    monkeypatch.setattr(factory_mod, "build_api_client_for_env", _fail_build)

    state = MemoryExtractState(engine.cwd)
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()

    await _run_extract_task(
        engine, [ConversationMessage.from_user_text("hi")], memory_dir, state
    )

    # 构建失败：client 与 model 均回退当前（避免用主 client 调跨 provider 模型导致 400）
    assert captured.get("api_client") is engine.api_client
    assert captured.get("model") == "default-model"


def test_snapshot_memory_dir(tmp_path: Path):
    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    (memory_dir / "a.md").write_text("a", encoding="utf-8")
    (memory_dir / "MEMORY.md").write_text("# Index", encoding="utf-8")

    snap = _snapshot_memory_dir(memory_dir)
    assert set(snap.keys()) == {"a.md", "MEMORY.md"}

    # 修改文件后快照变化
    import time

    time.sleep(0.01)
    (memory_dir / "a.md").write_text("a2", encoding="utf-8")
    snap2 = _snapshot_memory_dir(memory_dir)
    assert snap2 != snap


def test_memory_extract_prompt(tmp_path: Path):
    prompt = _memory_extract_prompt(5, tmp_path / "mem")
    assert "memory extraction subagent" in prompt
    assert "Nothing to save" in prompt
    # I1: prompt 应注入记忆目录路径与 frontmatter 格式
    assert str(tmp_path / "mem") in prompt
    assert "name: short-slug" in prompt
    assert "type: user|feedback|project|reference" in prompt


def test_build_extract_tool_registry(tmp_path: Path):
    """受限工具注册表：只读工具 + 记忆目录内写工具，无其他工具。"""
    registry = build_extract_tool_registry(tmp_path / "mem")
    names = {t.name for t in registry.list_tools()}
    assert names == {"read_file", "glob", "grep", "write_file", "edit_file"}


@pytest.mark.asyncio
async def test_scoped_write_rejects_outside_path(tmp_path: Path):
    """记忆目录外的写入应被拒绝。"""
    from illusion.memory.extract import _MemoryScopedTool
    from illusion.tools.base import ToolExecutionContext
    from illusion.tools.file_write_tool import FileWriteTool, FileWriteToolInput

    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    outside = tmp_path / "outside.md"

    tool = _MemoryScopedTool(FileWriteTool(), memory_dir, path_fields=("file_path",))

    result = await tool.execute(
        FileWriteToolInput(file_path=str(outside), content="x"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error
    assert "Permission denied" in result.output
    assert not outside.exists()


@pytest.mark.asyncio
async def test_scoped_write_allows_inside_path(tmp_path: Path):
    """记忆目录内的写入应被放行。"""
    from illusion.memory.extract import _MemoryScopedTool
    from illusion.tools.base import ToolExecutionContext
    from illusion.tools.file_write_tool import FileWriteTool, FileWriteToolInput

    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    inside = memory_dir / "user_role.md"

    tool = _MemoryScopedTool(FileWriteTool(), memory_dir, path_fields=("file_path",))

    result = await tool.execute(
        FileWriteToolInput(file_path=str(inside), content="role: engineer"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert not result.is_error
    assert inside.exists()


@pytest.mark.asyncio
async def test_scoped_read_rejects_outside_path(tmp_path: Path):
    """记忆目录外的读取应被拒绝。"""
    from illusion.memory.extract import _MemoryScopedTool
    from illusion.tools.base import ToolExecutionContext
    from illusion.tools.file_read_tool import FileReadTool, FileReadToolInput

    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")

    tool = _MemoryScopedTool(FileReadTool(), memory_dir, path_fields=("file_path",))

    result = await tool.execute(
        FileReadToolInput(file_path=str(outside)),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error
    assert "Permission denied" in result.output


@pytest.mark.asyncio
async def test_scoped_grep_forces_root(tmp_path: Path):
    """grep 的 path 为空时应强制为记忆目录（而不是默认搜索项目）。"""
    from illusion.memory.extract import _MemoryScopedTool
    from illusion.tools.base import ToolExecutionContext
    from illusion.tools.grep_tool import GrepTool, GrepToolInput

    memory_dir = tmp_path / "mem"
    memory_dir.mkdir()
    (memory_dir / "user_role.md").write_text("role: engineer", encoding="utf-8")

    tool = _MemoryScopedTool(GrepTool(), memory_dir, path_fields=("path",))

    result = await tool.execute(
        GrepToolInput(pattern="engineer"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert not result.is_error
    assert "user_role.md" in result.output


# --- 状态机测试（C1 守卫 / I3 互斥 / I4 压缩复位） ---


class FakeEngine2(FakeEngine):
    """带 _is_memory_subagent 标记的引擎桩。"""

    def __init__(self, cwd, messages=None, *, is_subagent=False) -> None:
        super().__init__(cwd, messages)
        self._is_memory_subagent = is_subagent


def test_subagent_guard_skips_schedule(tmp_path: Path, monkeypatch):
    """C1: 子代理引擎不得触发后台提取（防级联）。"""
    from illusion.memory.extract import maybe_schedule_extract
    from illusion.memory.manager import is_memory_enabled

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    assert is_memory_enabled(tmp_path) is True

    engine = FakeEngine2(tmp_path, [object()] * 3, is_subagent=True)
    maybe_schedule_extract(engine)
    # 守卫直接返回：不创建状态、不调度任务
    assert not hasattr(engine, "_memory_extract_state")


@pytest.mark.asyncio
async def test_extract_cursor_resets_after_compact(tmp_path: Path, monkeypatch):
    """I4: 自动压缩后消息数骤减，游标应复位重新分析。"""
    from illusion.memory.extract import MemoryExtractState, maybe_schedule_extract

    _enable_auto_extract(monkeypatch, tmp_path)

    engine = FakeEngine2(tmp_path, [object()] * 5)
    state = MemoryExtractState(engine.cwd)
    state.last_extracted_index = 10  # 模拟游标超过消息数（压缩后）
    engine._memory_extract_state = state

    maybe_schedule_extract(engine)
    # 游标被复位到 0 且调度了提取任务（state.running = True）
    assert state.last_extracted_index == 0
    assert state.running is True


@pytest.mark.asyncio
async def test_extract_mutual_exclusion_advances_cursor(tmp_path: Path, monkeypatch):
    """I3: 主代理已写记忆（快照变化）→ 跳过并推进游标，不重复分析。"""
    from illusion.memory.extract import (
        MemoryExtractState,
        _snapshot_memory_dir,
        maybe_schedule_extract,
    )

    _enable_auto_extract(monkeypatch, tmp_path)

    engine = FakeEngine2(tmp_path, [object()] * 5)
    state = MemoryExtractState(engine.cwd)
    state.last_extracted_index = 2
    # 初始快照：空记忆目录（模拟上次提取后的状态）
    state.snapshot = _snapshot_memory_dir(_memory_dir_for(engine.cwd))
    engine._memory_extract_state = state

    # 主代理已写入记忆 → 快照变化，触发互斥
    await asyncio.sleep(0.01)
    (_memory_dir_for(engine.cwd) / "user_role.md").write_text("role: engineer", encoding="utf-8")

    maybe_schedule_extract(engine)
    # 互斥命中：游标推进到消息末尾，不调度提取
    assert state.last_extracted_index == 5
    assert state.running is False


@pytest.mark.asyncio
async def test_auto_extract_disabled_skips_schedule(tmp_path: Path, monkeypatch):
    """auto_extract=false（手动模式）时不应调度后台提取。"""
    import json

    from illusion.memory.extract import MemoryExtractState, maybe_schedule_extract

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))

    # 写入 settings.json：memory.enabled=true 但 auto_extract=false
    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"memory": {"enabled": True, "auto_extract": False}}),
        encoding="utf-8",
    )

    engine = FakeEngine2(tmp_path, [object()] * 5)
    state = MemoryExtractState(engine.cwd)
    engine._memory_extract_state = state

    maybe_schedule_extract(engine)
    # 开关关闭：不创建状态、不调度任务
    assert state.running is False
    assert state.turns_since_extract == 0


def _memory_dir_for(cwd: str) -> Path:
    """获取引擎对应记忆目录（避免重复 import 样板）。"""
    from illusion.memory.paths import get_memory_dir_for_cwd

    return get_memory_dir_for_cwd(cwd)
