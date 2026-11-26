"""MCP CLI 持久化与发现测试。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from illusion.cli import app

runner = CliRunner()


def test_mcp_list_reads_user_and_project_single_files(tmp_path: Path, monkeypatch) -> None:
    config_root = tmp_path / "missing" / "user-config"
    project = tmp_path / "project"
    other_cwd = tmp_path / "other"
    project.mkdir()
    other_cwd.mkdir()
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
    monkeypatch.chdir(other_cwd)

    user_mcp = config_root / "mcp.json"
    user_mcp.parent.mkdir(parents=True)
    user_mcp.write_text(
        json.dumps({"mcpServers": {"user-server": {"command": "user-cmd"}}}),
        encoding="utf-8",
    )
    project_mcp = project / ".illusion" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {"project-server": {"command": "project-cmd"}}}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--cwd", str(project), "mcp", "list"])

    assert result.exit_code == 0, result.output
    assert "user-server: stdio (user-cmd)" in result.output
    assert "project-server: stdio (project-cmd)" in result.output


def test_mcp_add_accepts_implicit_stdio_and_creates_settings_parent(
    tmp_path: Path, monkeypatch
) -> None:
    config_root = tmp_path / "missing" / "user-config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["mcp", "add", "local-server", '{"command":"local-cmd"}'],
    )

    assert result.exit_code == 0, result.output
    settings = json.loads((config_root / "settings.json").read_text(encoding="utf-8"))
    assert settings["mcp_servers"]["local-server"] == {
        "type": "stdio",
        "command": "local-cmd",
        "args": [],
        "env": None,
        "cwd": None,
        "log_file": None,
        "enabled": True,
    }


def test_mcp_remove_only_mutates_settings_source(tmp_path: Path, monkeypatch) -> None:
    config_root = tmp_path / "config"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_root))
    monkeypatch.chdir(project)

    project_mcp = project / ".illusion" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_content = json.dumps({"mcpServers": {"project-server": {"command": "project-cmd"}}})
    project_mcp.write_text(project_content, encoding="utf-8")

    added = runner.invoke(
        app,
        ["mcp", "add", "settings-server", '{"command":"settings-cmd"}'],
    )
    assert added.exit_code == 0, added.output

    removed = runner.invoke(app, ["mcp", "remove", "settings-server"])

    assert removed.exit_code == 0, removed.output
    assert project_mcp.read_text(encoding="utf-8") == project_content
    listed = runner.invoke(app, ["mcp", "list"])
    assert listed.exit_code == 0, listed.output
    assert "project-server: stdio (project-cmd)" in listed.output
    assert "settings-server" not in listed.output
