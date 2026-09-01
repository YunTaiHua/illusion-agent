"""i18n 翻译测试模块

本模块提供 i18n 翻译的单元测试，包括：
- effort 相关翻译测试
- 降级提示翻译测试
"""

import re

import pytest

from illusion.config.i18n import translate_command_message


class TestI18nEffort:
    """i18n effort 翻译测试"""

    def test_effort_command_translation(self):
        """测试 /effort 命令翻译"""
        # 这个测试需要完整的命令注册表，暂时跳过

    def test_effort_usage_translation(self):
        """测试 /effort 用法翻译"""
        result = translate_command_message("Usage: /effort [show|low|medium|high|xhigh|max]", locale="zh-CN")
        assert result == "用法：/effort [show|low|medium|high|xhigh|max]"

    def test_effort_show_translation(self):
        """测试 effort 显示翻译"""
        result = translate_command_message("Reasoning effort: high", locale="zh-CN")
        assert result == "推理强度：high"

    def test_effort_set_translation(self):
        """测试 effort 设置翻译"""
        result = translate_command_message("Reasoning effort set to high.", locale="zh-CN")
        assert result == "推理强度已设置为 high。"


class TestI18nIndentedUsage:
    """i18n 缩进行用法翻译测试"""

    def test_indented_usage_line_translated(self):
        """测试带 14 个空格缩进的 /effort usage 行被翻译且保留缩进"""
        result = translate_command_message(
            "              Usage: /effort [show|low|medium|high|xhigh|max]",
            locale="zh-CN",
        )
        assert result == "              用法：/effort [show|low|medium|high|xhigh|max]"
        # en locale 原样返回
        en_result = translate_command_message(
            "              Usage: /effort [show|low|medium|high|xhigh|max]",
            locale="en",
        )
        assert en_result == "              Usage: /effort [show|low|medium|high|xhigh|max]"

    def test_indented_usage_context_translated(self):
        """测试带 14 个空格缩进的 /context usage 行被翻译且保留缩进"""
        result = translate_command_message(
            "              Usage: /context [usage|show|window|set N]",
            locale="zh-CN",
        )
        assert result == "              用法：/context [usage|show|window|set N]"
        en_result = translate_command_message(
            "              Usage: /context [usage|show|window|set N]",
            locale="en",
        )
        assert en_result == "              Usage: /context [usage|show|window|set N]"

    def test_non_indented_usage_still_works(self):
        """测试非缩进的 usage 行仍能正确翻译"""
        result = translate_command_message(
            "Usage: /effort [show|low|medium|high|xhigh|max]",
            locale="zh-CN",
        )
        assert result == "用法：/effort [show|low|medium|high|xhigh|max]"
        en_result = translate_command_message(
            "Usage: /effort [show|low|medium|high|xhigh|max]",
            locale="en",
        )
        assert en_result == "Usage: /effort [show|low|medium|high|xhigh|max]"

    def test_indented_non_usage_line_preserved(self):
        """测试无可翻译内容的缩进行原样返回（保留缩进）"""
        result = translate_command_message(
            "              some random text",
            locale="zh-CN",
        )
        assert result == "              some random text"
        en_result = translate_command_message(
            "              some random text",
            locale="en",
        )
        assert en_result == "              some random text"

    def test_tab_indented_line_translated(self):
        """测试 Tab 缩进的 usage 行被翻译且保留 Tab 缩进"""
        result = translate_command_message(
            "\t\tUsage: /effort [show|low|medium|high|xhigh|max]",
            locale="zh-CN",
        )
        assert result == "\t\t用法：/effort [show|low|medium|high|xhigh|max]"
        en_result = translate_command_message(
            "\t\tUsage: /effort [show|low|medium|high|xhigh|max]",
            locale="en",
        )
        assert en_result == "\t\tUsage: /effort [show|low|medium|high|xhigh|max]"


class TestUsageCoverageComplete:
    """覆盖历史上遗漏的 Usage 行（terminal 翻译 + web 过滤依赖同一表）"""

    @pytest.mark.parametrize(
        "line,expect_prefix",
        [
            (
                "Usage: /mcp auth SERVER TOKEN | /mcp auth SERVER [bearer|env] VALUE | /mcp auth SERVER header KEY VALUE",
                "用法：/mcp auth 服务器",
            ),
            ("Usage: /rules <name|number>  — view a specific rule", "用法：/rules <名称|序号>"),
            (
                "Usage: /memory add [user|feedback|project|reference] TITLE :: CONTENT",
                "用法：/memory add [user|feedback|project|reference] 标题 :: 内容",
            ),
            (
                "Usage: /goal [<objective>|clear|edit <objective>|pause|resume]",
                "用法：/goal [<目标>|clear",
            ),
            ("Usage: /sandbox exclude <command pattern>", "用法：/sandbox exclude <命令模式>"),
            ("Example: /sandbox exclude npm test", "示例：/sandbox exclude npm test"),
            ("Usage: /sandbox remove <command pattern>", "用法：/sandbox remove <命令模式>"),
            ("Usage:", "用法："),
        ],
    )
    def test_zh_translation(self, line: str, expect_prefix: str):
        assert translate_command_message(line, locale="zh-CN").startswith(expect_prefix)

    @pytest.mark.parametrize(
        "line,expect_contains",
        [
            ("  /sandbox              — Show sandbox status", "— 查看沙箱状态"),
            ("  /sandbox exclude <pattern> — Add excluded command", "— 添加排除命令"),
            ("  /login API_KEY          (standard x-api-key auth)", "（标准 x-api-key 认证）"),
            ("  /login auth_token TOKEN (Bearer Token auth)", "（Bearer Token 认证）"),
        ],
    )
    def test_indented_help_lines_keep_leading_ws(self, line: str, expect_contains: str):
        """lstrip 后查表、回填原缩进：译文应包含且保留行首空白"""
        result = translate_command_message(line, locale="zh-CN")
        assert expect_contains in result
        assert result.startswith(line[: len(line) - len(line.lstrip())])

    def test_en_locale_passthrough(self):
        line = "Usage: /goal [<objective>|clear]"
        assert translate_command_message(line, locale="en-US") == line


    def test_processor_variant_lines_translated(self):
        """处理器内自带（非 registry 追加）的 Usage 变体行也必须入表"""
        variants = [
            "Usage: /agent [list|create|model <name> <env_N.model_M|inherit>|<task_id>]",
            "Usage: /agent model <name> <env_N.model_M|inherit>",
            "Usage: /rename [name|#N name|session_id name|--clear]",
            "Usage: /resume #1 or /resume <session_id>",
            (
                "Usage: /delete #1 or /delete <session_id>  — delete a specific session",
                "用法：/delete #1 或 /delete <会话ID>",
            ),
        ]
        for item in variants:
            line, expect_prefix = (item if isinstance(item, tuple) else (item, "用法："))
            assert translate_command_message(line, locale="zh-CN").startswith(expect_prefix)

    def test_every_registered_command_usage_translated(self):
        """全量扫描默认注册表：任何带 usage 的命令，其追加 Usage 行都必须被翻译。

        这是本表唯一能防止"新增命令忘记补翻译条目"的强制网——
        terminal 翻译与 web 过滤共用同一张表。
        """
        from illusion.commands.registry import create_default_command_registry

        registry = create_default_command_registry()
        untranslated = []
        for command in registry._commands.values():  # 测试访问内部注册字典
            if not command.usage:
                continue
            line = f"Usage: {command.usage}"
            translated = translate_command_message(line, locale="zh-CN")
            if re.match(r"^Usage:", translated):
                untranslated.append(command.name)
        assert not untranslated, f"以下命令的 Usage 行未配置中文翻译: {untranslated}"
