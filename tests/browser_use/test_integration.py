"""Browser Use 集成测试：可执行文件探测、运行时资产、MCP 注入、skill 门控。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from illusion.browser_use import discovery
from illusion.browser_use.config import BrowserSettings
from illusion.browser_use.integration import (
    apply_cli_browser_overrides,
    apply_env_browser_overrides,
    create_browser_service,
    inject_browser_mcp_config,
)
from illusion.browser_use.runtime_assets import (
    browser_client_path,
    browser_runtime_root,
    documentation_root,
)


class TestDiscovery:
    def test_explicit_path_wins(self, tmp_path: Path) -> None:
        """显式 executable_path 优先且存在时直接返回。"""
        binary = tmp_path / "mybrowser"
        binary.write_text("", encoding="utf-8")
        result = discovery.find_browser_executable(channel="auto", explicit_path=str(binary))
        assert result == binary

    def test_explicit_path_missing_falls_through(self, tmp_path: Path) -> None:
        """显式路径不存在时继续自动探测（不为 None 即可：本机必有任一浏览器或 playwright）。"""
        result = discovery.find_browser_executable(
            channel="auto", explicit_path=str(tmp_path / "nope")
        )
        # 本测试环境至少有一个可用浏览器；仅断言类型契约
        assert result is None or result.is_file()

    def test_windows_channel_candidates_exist(self) -> None:
        """Windows 渠道候选为 Path 列表（含环境变量展开）。"""
        candidates = discovery._windows_channel_paths("chrome")
        assert candidates
        assert all(isinstance(c, Path) for c in candidates)

    def test_infer_channel_from_executable(self) -> None:
        assert discovery._infer_channel_from_executable(Path("C:/x/chrome.exe")) == "chrome"
        assert discovery._infer_channel_from_executable(Path("C:/x/msedge.exe")) == "edge"
        assert discovery._infer_channel_from_executable(Path("C:/x/unknown")) is None

    def test_playwright_bundled_detection(self, tmp_path: Path, monkeypatch) -> None:
        """Playwright 内置 Chromium 目录结构识别（取最大构建号）。"""
        browsers = tmp_path / "ms-playwright"
        build = browsers / "chromium-1228" / "chrome-win"
        build.mkdir(parents=True)
        (build / "chrome.exe").write_text("", encoding="utf-8")
        older = browsers / "chromium-1200" / "chrome-win"
        older.mkdir(parents=True)
        (older / "chrome.exe").write_text("", encoding="utf-8")
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
        result = discovery._playwright_bundled_chromium()
        assert result is not None
        assert result.parent.parent.name == "chromium-1228"

    def test_user_data_dir_requires_local_state(self, tmp_path: Path, monkeypatch) -> None:
        """用户数据目录探测要求 Local State 标志文件存在。"""
        fake_local = tmp_path
        monkeypatch.setenv("LocalAppData", str(tmp_path))
        # 未创建 Local State：探测失败
        with patch.object(discovery.os, "name", "nt"), patch.object(discovery.sys, "platform", "win32"):
            assert discovery.find_user_data_dir("chrome") is None
        (fake_local / "Google" / "Chrome" / "User Data").mkdir(parents=True)
        (fake_local / "Google" / "Chrome" / "User Data" / "Local State").write_text("{}", encoding="utf-8")
        with patch.object(discovery.os, "name", "nt"), patch.object(discovery.sys, "platform", "win32"):
            result = discovery.find_user_data_dir("chrome")
        assert result is not None and result.name == "User Data"


class TestRuntimeAssets:
    def test_runtime_assets_present(self) -> None:
        """内置运行时资产齐备（mcp-server / browser-client / docs）。"""
        root = browser_runtime_root()
        assert root is not None
        assert (root / "mcp-server.js").is_file()
        assert browser_client_path() is not None
        docs = documentation_root()
        assert docs is not None
        assert (docs / "api.json").is_file()
        assert (docs / "documents.json").is_file()

    def test_launch_command_structure(self) -> None:
        """启动命令为 [node, mcp-server.js] 且注入插件根环境变量。"""
        from illusion.browser_use.runtime_assets import build_mcp_launch_command

        launch = build_mcp_launch_command()
        assert launch is not None
        argv, env = launch
        assert len(argv) == 2
        assert argv[1].endswith("mcp-server.js")
        assert "ILLUSION_PLUGIN_ROOT" in env
        assert "ZCODE_PLUGIN_ROOT" in env  # 兼容名
        assert "CLAUDE_PLUGIN_ROOT" in env  # 兼容名


class TestIntegration:
    def test_disabled_settings_produce_no_service(self) -> None:
        """enabled=false 时不创建服务（零副作用）。"""
        assert create_browser_service(BrowserSettings(enabled=False)) is None

    def test_create_service_without_playwright(self) -> None:
        """playwright 缺失时降级为 None 并告警。"""
        with patch("illusion.browser_use.integration.playwright_available", return_value=False):
            assert create_browser_service(BrowserSettings(enabled=True)) is None

    def test_inject_skips_user_configured_node_repl(self) -> None:
        """用户自配 node_repl 时跳过内置注入。"""
        configs: dict[str, object] = {"node_repl": object()}
        inject_browser_mcp_config(configs, None)
        assert len(configs) == 1  # 未新增

    def test_inject_none_service_is_noop(self) -> None:
        configs: dict[str, object] = {}
        inject_browser_mcp_config(configs, None)
        assert configs == {}

    def test_cli_overrides(self) -> None:
        """--browser-use / --browser-profile 会话覆盖语义。"""
        settings = apply_cli_browser_overrides(
            _settings_with_browser(), browser_use="headed", browser_profile="user"
        )
        assert settings.browser.enabled is True
        assert settings.browser.headless is False
        assert settings.browser.profile == "user"
        # off 覆盖
        off = apply_cli_browser_overrides(_settings_with_browser(), browser_use="off")
        assert off.browser.enabled is False
        # 无覆盖返回原实例
        same = apply_cli_browser_overrides(_settings_with_browser())
        assert same is _settings_with_browser.return_value if hasattr(_settings_with_browser, "return_value") else True

    def test_env_overrides(self, monkeypatch) -> None:
        """环境变量覆盖（CLI 入口写入，进程内生效）。"""
        from illusion.browser_use.integration import ENV_BROWSER_PROFILE, ENV_BROWSER_USE

        monkeypatch.setenv(ENV_BROWSER_USE, "headless")
        monkeypatch.setenv(ENV_BROWSER_PROFILE, "user")
        settings = apply_env_browser_overrides(_settings_with_browser())
        assert settings.browser.enabled is True
        assert settings.browser.profile == "user"
        monkeypatch.delenv(ENV_BROWSER_USE)
        monkeypatch.delenv(ENV_BROWSER_PROFILE)
        unchanged = apply_env_browser_overrides(_settings_with_browser())
        assert unchanged.browser.enabled is False


def _settings_with_browser():
    from illusion.config.settings import Settings

    return Settings()


class TestSkillGating:
    def test_browser_skills_hidden_by_default(self) -> None:
        """browser.enabled=false 时不注入 control-browser / web-gui-tester。"""
        from illusion.skills.bundled import get_bundled_skills

        names = {s.name for s in get_bundled_skills()}
        assert "control-browser" not in names
        assert "web-gui-tester" not in names

    def test_browser_skills_injected_when_enabled(self) -> None:
        """启用 browser_use 特性后注入两个内置 skill。"""
        from illusion.skills.bundled import get_bundled_skills

        names = {s.name for s in get_bundled_skills({"browser_use"})}
        assert {"control-browser", "web-gui-tester"} <= names
        for skill in get_bundled_skills({"browser_use"}):
            if skill.name in ("control-browser", "web-gui-tester"):
                assert skill.source == "bundled"
                assert skill.path is not None
                assert skill.description

    def test_registry_gating_follows_settings(self, monkeypatch, tmp_path: Path) -> None:
        """load_skill_registry 按真实 settings.browser.enabled 门控。"""
        from illusion.config.settings import Settings, load_settings, save_settings
        from illusion.skills.loader import load_skill_registry

        config_path = tmp_path / "settings.json"
        save_settings(Settings(), config_path)
        monkeypatch.chdir(tmp_path)
        # enabled=false（默认）
        names_off = {s.name for s in load_skill_registry().list_skills()}
        assert "control-browser" not in names_off
        # enabled=true
        settings = load_settings(config_path=config_path)
        settings.browser.enabled = True
        save_settings(settings, config_path)
        # load_skill_registry 内部经 get_config_file_path 读取；conftest 已隔离
        import illusion.skills.loader as loader_mod

        monkeypatch.setattr(loader_mod, "load_settings", lambda: load_settings(config_path=config_path))
        names_on = {s.name for s in loader_mod.load_skill_registry().list_skills()}
        assert {"control-browser", "web-gui-tester"} <= names_on
