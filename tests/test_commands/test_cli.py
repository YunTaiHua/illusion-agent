"""CLI smoke tests."""

from typer.testing import CliRunner

from illusion.cli import app


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "IllusionAgent" in result.output


def test_continue_flag_passed_to_backend_command(monkeypatch):
    """-c 标志应传递给子进程的 --backend-only 命令。"""
    from illusion.ui.react_launcher import build_backend_command

    cmd = build_backend_command(cwd="/tmp", continue_session=True)
    assert "--continue" in cmd
    assert "--backend-only" in cmd


def test_resume_flag_passed_to_backend_command():
    """--resume 标志应传递给子进程。"""
    from illusion.ui.react_launcher import build_backend_command

    cmd = build_backend_command(cwd="/tmp", resume="abc123")
    assert "--resume" in cmd
    assert "abc123" in cmd


def test_settings_file_passed_to_load_settings(tmp_path, monkeypatch):
    """--settings 指定的文件应被 load_settings 加载。"""
    import json
    settings_file = tmp_path / "custom_settings.json"
    settings_file.write_text(json.dumps({"model": "env_1.model_custom"}))

    from illusion.config.settings import load_settings
    # 验证 load_settings 接受 config_path 参数
    settings = load_settings(config_path=settings_file)
    assert settings.model == "env_1.model_custom"


def test_continue_without_print_mode_errors():
    """-c 不带 -p 应报错退出。"""
    runner = CliRunner()
    result = runner.invoke(app, ["-c"])
    assert result.exit_code == 1
    assert "--continue/--resume 需要配合 -p" in result.output or "需要配合" in result.output


def test_resume_without_print_mode_errors():
    """-r 不带 -p 应报错退出。"""
    runner = CliRunner()
    result = runner.invoke(app, ["-r", "some-session-id"])
    assert result.exit_code == 1


def test_effort_short_flag_persists(tmp_path, monkeypatch):
    """-e 简写应持久化 effort 到 settings。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)

    from illusion.config import load_settings

    runner = CliRunner()
    # 使用 -p 模式避免启动 TUI；-p 执行可能因无 API key 失败，但持久化应先执行
    runner.invoke(app, ["-e", "high", "-p", "test"])
    # effort 应已持久化（即使 -p 执行失败，持久化应先执行）
    settings = load_settings()
    assert settings.effort == "high"


def test_max_turns_short_flag_persists(tmp_path, monkeypatch):
    """-t 简写应持久化 max_turns 到 settings。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)

    from illusion.config import load_settings

    runner = CliRunner()
    runner.invoke(app, ["-t", "5", "-p", "test"])
    settings = load_settings()
    assert settings.max_turns == 5


def test_model_flag_persists(tmp_path, monkeypatch):
    """-m 应持久化 model 到 settings。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)

    from illusion.config import load_settings

    runner = CliRunner()
    runner.invoke(app, ["-m", "env_2.model_3", "-p", "test"])
    settings = load_settings()
    assert settings.model == "env_2.model_3"


def test_permission_mode_flag_persists(tmp_path, monkeypatch):
    """--permission-mode 应持久化到 settings.permission.mode。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)

    from illusion.config import load_settings

    runner = CliRunner()
    runner.invoke(app, ["--permission-mode", "plan", "-p", "test"])
    settings = load_settings()
    assert settings.permission.mode == "plan"
