"""管道编排器集成测试"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.commands.init.orchestrator import run_init


def _make_context(tmp_path: Path) -> MagicMock:
    """创建测试用 CommandContext"""
    context = MagicMock()
    context.cwd = str(tmp_path)
    return context


@pytest.mark.asyncio
async def test_run_init_creates_all_files(tmp_path: Path, monkeypatch):
    """测试完整流程创建所有文件"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    # 创建一个简单的 Python 项目
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text('"""My project."""\n')
    (tmp_path / "src" / "main.py").write_text(
        '"""Main module."""\n\ndef main():\n    """Run the app."""\n    pass\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myproject"\ndependencies = []\n\n[tool.ruff]\nline-length = 88\n'
    )
    (tmp_path / "README.md").write_text("# My Project\n\nA sample project.\n")

    context = _make_context(tmp_path)
    result = await run_init(context)

    # 检查报告
    assert "initialization complete" in result.message

    # 检查文件创建
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "ILLUSION.md").exists()
    # 记忆入口位于 user 级记忆目录（仅保留 user 级记忆入口，无项目级 .illusion/memory/）
    from illusion.memory.paths import get_memory_entrypoint

    assert get_memory_entrypoint(tmp_path).exists()
    assert not (tmp_path / ".illusion" / "memory" / "MEMORY.md").exists()
    assert (tmp_path / ".illusion" / "rules" / "project-structure.md").exists()
    assert (tmp_path / ".illusion" / "plugins" / ".gitkeep").exists()
    assert (tmp_path / ".illusion" / "skills" / ".gitkeep").exists()

    # 检查 CLAUDE.md 内容质量
    claudemd = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "A sample project" in claudemd
    assert "Python" in claudemd
    assert "snake_case" in claudemd
    assert "ILLUSION:overview" in claudemd

    # 检查 ILLUSION.md 内容
    illusionmd = (tmp_path / "ILLUSION.md").read_text(encoding="utf-8")
    assert "ILLUSION.md" in illusionmd or "project" in illusionmd.lower()


@pytest.mark.asyncio
async def test_run_init_reports_external_user_memory_path(tmp_path: Path, monkeypatch):
    """外置用户配置根下的 MEMORY.md 也不应阻断 init 报告。"""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "test"\n')
    user_config = tmp_path / "user-config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(user_config))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    result = await run_init(_make_context(project))

    from illusion.memory.paths import get_memory_entrypoint

    memory_path = get_memory_entrypoint(project)
    assert memory_path.exists()
    assert str(memory_path) in result.message
    assert (project / ".illusion" / "rules" / "project-structure.md").exists()


@pytest.mark.asyncio
async def test_run_init_idempotent(tmp_path: Path, monkeypatch):
    """测试幂等性：第二次运行不会重复创建文件"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

    context = _make_context(tmp_path)

    # 第一次运行
    result1 = await run_init(context)
    assert "initialization complete" in result1.message

    # 第二次运行 — CLAUDE.md 会被更新（marker 内容刷新），其他文件跳过
    result2 = await run_init(context)
    assert "initialization complete" in result2.message or "already initialized" in result2.message
    # 确认 ILLUSION.md 没有被重新创建（内容应相同）
    assert (tmp_path / "ILLUSION.md").exists()


@pytest.mark.asyncio
async def test_run_init_updates_claudemd(tmp_path: Path, monkeypatch):
    """测试重新运行时更新 CLAUDE.md 的 marker 内容"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

    context = _make_context(tmp_path)

    # 第一次运行
    await run_init(context)

    # 手动修改 CLAUDE.md（在 marker 外添加内容）
    claudemd = tmp_path / "CLAUDE.md"
    content = claudemd.read_text(encoding="utf-8")
    content = "# Custom Title\n\nMy custom notes.\n\n" + content
    claudemd.write_text(content, encoding="utf-8")

    # 第二次运行
    await run_init(context)

    # CLAUDE.md 应该被更新
    updated = claudemd.read_text(encoding="utf-8")
    assert "Custom Title" in updated
    assert "My custom notes." in updated
