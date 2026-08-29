"""Browser Use 配置测试：Settings 集成、默认落盘、覆盖逻辑。"""

from pathlib import Path

from illusion.browser_use.config import BrowserSettings
from illusion.config.settings import Settings, load_settings, save_settings


def test_settings_default_browser_disabled() -> None:
    """默认 Settings.browser：关闭、空白档案、无头。"""
    settings = Settings()
    assert settings.browser.enabled is False
    assert settings.browser.profile == "blank"
    assert settings.browser.headless is True
    assert settings.browser.viewport.width == 1280


def test_load_settings_materializes_browser_defaults(tmp_path: Path) -> None:
    """缺失 browser 键时 load_settings 一次性落盘默认配置（透明可改）。"""
    config_path = tmp_path / "settings.json"
    config_path.write_text("{}", encoding="utf-8")
    settings = load_settings(config_path=config_path)
    assert settings.browser.enabled is False
    raw = __import__("json").loads(config_path.read_text(encoding="utf-8"))
    assert "browser" in raw
    assert raw["browser"]["profile"] == "blank"
    assert raw["browser"]["enabled"] is False


def test_load_settings_preserves_existing_browser_config(tmp_path: Path) -> None:
    """用户已配置的 browser 键不被默认值覆盖。"""
    config_path = tmp_path / "settings.json"
    config_path.write_text(
        '{"browser": {"enabled": true, "profile": "user", "headless": false}}',
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path)
    assert settings.browser.enabled is True
    assert settings.browser.profile == "user"
    assert settings.browser.headless is False
    raw = __import__("json").loads(config_path.read_text(encoding="utf-8"))
    assert raw["browser"]["viewport"] == {"width": 1280, "height": 720}


def test_browser_settings_normalization() -> None:
    """渠道/档案名大小写兼容，cdp_url 去空白。"""
    settings = BrowserSettings(channel="Chrome", profile="USER", cdp_url="  http://127.0.0.1:9222 ")
    assert settings.channel == "chrome"
    assert settings.profile == "user"
    assert settings.cdp_url == "http://127.0.0.1:9222"


def test_browser_settings_rejects_unknown_fields() -> None:
    """未知字段拒绝（extra=forbid，防止拼写错误静默失效）。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BrowserSettings(profiles="user")


def test_browser_round_trip(tmp_path: Path) -> None:
    """save/load 往返保留 browser 配置。"""
    settings = Settings()
    settings.browser.enabled = True
    settings.browser.profile = "user"
    settings.browser.keep_alive_minutes = 5
    save_settings(settings, tmp_path / "settings.json")
    loaded = load_settings(config_path=tmp_path / "settings.json")
    assert loaded.browser.enabled is True
    assert loaded.browser.profile == "user"
    assert loaded.browser.keep_alive_minutes == 5
