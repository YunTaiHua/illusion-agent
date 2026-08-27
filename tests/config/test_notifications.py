"""NotificationSettings（通知开关）测试模块

覆盖 settings.json 中 notifications 小节的行为：
- 默认值：toast 与音效默认全开
- 从 JSON 解析与字段独立保存
- 音效联动规则：toast 总开关关闭时，无论 sound 取值如何音效均不生效
- 嵌套局部更新不影响其余字段
"""

from illusion.config.settings import NotificationSettings, Settings


class TestNotificationDefaults:
    """notifications 默认值测试"""

    def test_default_enabled_and_sound(self):
        """未配置时 toast 与音效默认开启"""
        settings = Settings()
        assert settings.notifications.enabled is True
        assert settings.notifications.sound is True
        assert settings.toast_sound_enabled is True

    def test_notification_settings_defaults(self):
        """NotificationSettings 模型自身默认值"""
        notif = NotificationSettings()
        assert notif.enabled is True
        assert notif.sound is True


class TestNotificationParsing:
    """notifications 字段解析与独立保存测试"""

    def test_parse_from_json(self):
        """从 JSON 字典解析 notifications 配置"""
        settings = Settings.model_validate({"notifications": {"enabled": False, "sound": False}})
        assert settings.notifications.enabled is False
        assert settings.notifications.sound is False

    def test_toggles_saved_independently(self):
        """两个开关独立保存：toast 关闭时 sound 的取值仍按原样保留"""
        settings = Settings.model_validate({"notifications": {"enabled": False, "sound": True}})
        assert settings.notifications.enabled is False
        # 独立保存语义：sound=True 不会被改写为 False
        assert settings.notifications.sound is True
        # 但生效值必须被 toast 总关闭压住
        assert settings.toast_sound_enabled is False

    def test_partial_update_keeps_other_field(self):
        """model_copy 局部更新只影响提供的字段（PATCH /api/settings/notifications 路径）"""
        base = Settings.model_validate({"notifications": {"sound": False}})
        updated = base.model_copy(
            update={"notifications": base.notifications.model_copy(update={"enabled": False})}
        )
        assert updated.notifications.enabled is False
        assert updated.notifications.sound is False


class TestSoundCoupling:
    """音效只在 toast 开关有效时处理（联动规则）"""

    def test_sound_ineffective_when_toast_disabled(self):
        """toast 关闭 → 音效一律不生效"""
        settings = Settings(notifications=NotificationSettings(enabled=False, sound=True))
        assert settings.toast_sound_enabled is False

    def test_sound_effective_only_when_both_on(self):
        """两个开关都开时音效才生效"""
        settings = Settings(notifications=NotificationSettings(enabled=True, sound=True))
        assert settings.toast_sound_enabled is True

    def test_muted_explicitly_when_toast_on(self):
        """toast 开启但用户显式关闭音效"""
        settings = Settings(notifications=NotificationSettings(enabled=True, sound=False))
        assert settings.toast_sound_enabled is False


class TestNotificationBackfill:
    """load_settings 对缺失 notifications 键的一次性落盘（与 sandbox 同策略）"""

    def test_missing_key_backfilled_to_file(self, tmp_path):
        """配置文件缺 notifications 时，首次加载写入显式默认值"""
        import json

        from illusion.config.settings import load_settings

        cfg = tmp_path / "settings.json"
        cfg.write_text(
            json.dumps({"env_1": {"api_format": "openai"}, "sandbox": {}}),
            encoding="utf-8",
        )
        settings = load_settings(cfg)
        raw = json.loads(cfg.read_text(encoding="utf-8"))
        assert raw["notifications"] == {"enabled": True, "sound": True}
        assert settings.notifications.enabled is True

    def test_user_authored_key_not_overwritten(self, tmp_path):
        """用户手写过的 notifications 键不被回填覆盖（可只写部分字段）"""
        import json

        from illusion.config.settings import load_settings

        cfg = tmp_path / "settings.json"
        cfg.write_text(
            json.dumps(
                {"env_1": {"api_format": "openai"}, "notifications": {"enabled": False}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        settings = load_settings(cfg)
        raw = json.loads(cfg.read_text(encoding="utf-8"))
        assert raw["notifications"]["enabled"] is False
        # 部分字段缺失时按模型默认值补齐解析
        assert settings.notifications.sound is True
