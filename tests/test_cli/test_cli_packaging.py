"""cli 包入口完整性测试"""
from typer.testing import CliRunner

from illusion.cli import app


def test_cli_import_app():
    """from illusion.cli import app 应可用"""
    assert app is not None


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "IllusionAgent" in result.output


def test_set_command_registered():
    """illusion set 命令应已注册"""
    runner = CliRunner()
    result = runner.invoke(app, ["set", "--help"])
    assert result.exit_code == 0


def test_subcommands_registered():
    """各子命令应已注册"""
    runner = CliRunner()
    for cmd in ["mcp", "plugin", "auth", "cron", "web", "add", "channel", "update"]:
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} 子命令未注册或出错: {result.output}"
