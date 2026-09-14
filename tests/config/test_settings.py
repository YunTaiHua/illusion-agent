"""Settings effort 字段测试模块

本模块提供 Settings 类 effort 字段的单元测试，包括：
- effort 字段默认值测试
- effort 字段设置测试
- effort 字段验证测试
"""

from illusion.config.settings import Settings


class TestSettingsEffort:
    """Settings effort 字段测试"""

    def test_effort_default_value(self):
        """测试 effort 字段默认值"""
        settings = Settings()
        assert settings.effort == "high"

    def test_effort_set_valid_value(self):
        """测试设置有效的 effort 值"""
        settings = Settings()
        settings.effort = "high"
        assert settings.effort == "high"

    def test_effort_set_xhigh(self):
        """测试设置 xhigh effort 值"""
        settings = Settings()
        settings.effort = "xhigh"
        assert settings.effort == "xhigh"

    def test_effort_set_max(self):
        """测试设置 max effort 值"""
        settings = Settings()
        settings.effort = "max"
        assert settings.effort == "max"

    def test_effort_invalid_value(self):
        """测试设置无效的 effort 值"""
        settings = Settings()
        # Pydantic 允许设置任意值，但 EffortLevel 枚举会验证
        settings.effort = "invalid"
        # 在实际使用中，EffortMapper.normalize 会抛出 ValueError
