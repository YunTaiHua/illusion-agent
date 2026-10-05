"""内置浏览器（browser-use 复刻）单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from illusion.browser.base import (
    BrowserCommandError,
    TabInfo,
    clamp_viewport,
    validate_url,
)
from illusion.browser.manager import BrowserConfig, BrowserManager
from illusion.browser.seeding import seed_builtin_browser_plugin
from illusion.tools.browser_tools import create_browser_tools

# ---------------------------------------------------------------------------
# URL 白名单（对标 ZCode page-command.ts）
# ---------------------------------------------------------------------------

class TestValidateUrl:
    def test_http_https_allowed(self) -> None:
        assert validate_url("https://example.com") == "https://example.com"
        assert validate_url("http://localhost:8000/x") == "http://localhost:8000/x"

    def test_about_blank_allowed(self) -> None:
        assert validate_url("about:blank") == "about:blank"

    def test_dangerous_schemes_rejected(self) -> None:
        for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x",
                    "about:config", "ftp://x"):
            with pytest.raises(BrowserCommandError):
                validate_url(url)


class TestClampViewport:
    def test_clamps_to_bounds(self) -> None:
        assert clamp_viewport(100, 100) == (320, 320)
        assert clamp_viewport(9999, 9999) == (3840, 2160)
        assert clamp_viewport(1280, 720) == (1280, 720)


# ---------------------------------------------------------------------------
# 内置插件 seed（对标 ZCode seedBundledOfficialPlugins）
# ---------------------------------------------------------------------------

class TestSeeding:
    def test_seed_creates_plugin_with_skills(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser import seeding as seeding_mod

        monkeypatch.setattr(seeding_mod, "get_config_dir", lambda: tmp_path)
        target = seed_builtin_browser_plugin()
        assert target is not None
        manifest = json.loads((target / "plugin.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "browser-use"
        assert manifest["enabled_by_default"] is True
        assert (target / "skills" / "control-browser" / "SKILL.md").is_file()
        assert (target / "skills" / "web-gui-tester" / "SKILL.md").is_file()
        # 文档资产存在且不含 recording（范围外）
        assert (target / "docs" / "overview.md").is_file()
        assert not (target / "docs" / "recording.md").exists()

    def test_seed_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser import seeding as seeding_mod

        monkeypatch.setattr(seeding_mod, "get_config_dir", lambda: tmp_path)
        first = seed_builtin_browser_plugin()
        assert first is not None
        marker_mtime = (first / ".illusion-plugin-seed.json").stat().st_mtime_ns
        second = seed_builtin_browser_plugin()
        assert second == first
        assert (first / ".illusion-plugin-seed.json").stat().st_mtime_ns == marker_mtime


# ---------------------------------------------------------------------------
# 插件清单能被 illusion 插件加载器识别（seed 后端到端可发现）
# ---------------------------------------------------------------------------

class TestPluginDiscoverable:
    def test_loader_discovers_seeded_plugin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import illusion.plugins.loader as plugins_loader
        from illusion.browser import seeding as seeding_mod
        from illusion.config.settings import Settings
        from illusion.plugins.loader import load_plugins

        # seed 与插件发现各自持有 get_config_dir 引用，两处一并指向 tmp_path
        monkeypatch.setattr(seeding_mod, "get_config_dir", lambda: tmp_path)
        monkeypatch.setattr(plugins_loader, "get_config_dir", lambda: tmp_path)
        assert seed_builtin_browser_plugin() is not None
        settings = Settings()
        plugins = load_plugins(settings, tmp_path)
        names = [p.manifest.name for p in plugins]
        assert "browser-use" in names
        browser_plugin = next(p for p in plugins if p.manifest.name == "browser-use")
        # 默认启用（对标 ZCode DEFAULT_ENABLED_OFFICIAL_PLUGIN_IDS）
        assert browser_plugin.enabled is True
        # 技能被插件装载（带插件命名空间，与 ZCode 的 browser-use:control-browser 一致）
        skill_names = [s.name for s in browser_plugin.skills]
        assert "browser-use:control-browser" in skill_names
        assert "browser-use:web-gui-tester" in skill_names


# ---------------------------------------------------------------------------
# 工具集与注册
# ---------------------------------------------------------------------------

class TestBrowserTools:
    def test_twelve_tools_with_browser_prefix(self) -> None:
        tools = create_browser_tools()
        assert len(tools) == 12
        assert all(t.name.startswith("browser_") for t in tools)
        names = {t.name for t in tools}
        assert {
            "browser_navigate", "browser_snapshot", "browser_screenshot", "browser_click",
            "browser_type", "browser_press_key", "browser_scroll", "browser_evaluate",
            "browser_tabs", "browser_resize", "browser_wait", "browser_close",
        } == names

    def test_api_schema_shape(self) -> None:
        tool = create_browser_tools()[0]
        schema = tool.to_api_schema()
        assert schema["name"] == "browser_navigate"
        assert "input_schema" in schema

    def test_permission_surface(self) -> None:
        tools = {t.name: t for t in create_browser_tools()}
        from pydantic import BaseModel

        class _Empty(BaseModel):
            pass

        # evaluate 为非只读（任意 JS 执行，权限门控）；其余与 ZCode 体验一致不加审批
        assert tools["browser_evaluate"].is_read_only(_Empty()) is False
        assert tools["browser_click"].is_read_only(_Empty()) is True
        assert tools["browser_navigate"].is_read_only(_Empty()) is True


# ---------------------------------------------------------------------------
# BrowserManager：未启用插件时工具报错；桌面桥接 env 探测
# ---------------------------------------------------------------------------

class TestManager:
    def test_backend_selection_desktop_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser.desktop_bridge import BRIDGE_URL_ENV, DesktopBridgeBackend

        monkeypatch.setenv(BRIDGE_URL_ENV, "http://127.0.0.1:45678")
        manager = BrowserManager(BrowserConfig())
        assert manager.mode == "desktop"
        backend = manager._create_backend()
        assert isinstance(backend, DesktopBridgeBackend)

    def test_backend_selection_managed_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser.desktop_bridge import BRIDGE_URL_ENV

        monkeypatch.delenv(BRIDGE_URL_ENV, raising=False)
        manager = BrowserManager(BrowserConfig())
        assert manager.mode == "managed"

    def test_panel_close_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser.desktop_bridge import BRIDGE_URL_ENV

        monkeypatch.delenv(BRIDGE_URL_ENV, raising=False)
        manager = BrowserManager(BrowserConfig())
        assert manager.is_open is False
        # 未启动时 panel_close 不抛错
        import asyncio

        asyncio.get_event_loop_policy()
        result = asyncio.run(manager.panel_close())
        assert result == {"open": False}


# ---------------------------------------------------------------------------
# TabInfo 序列化（协议契约）
# ---------------------------------------------------------------------------

class TestTabInfo:
    def test_to_dict(self) -> None:
        info = TabInfo(id="t1", url="https://example.com", title="Example", active=True)
        assert info.to_dict() == {
            "id": "t1", "url": "https://example.com", "title": "Example", "active": True,
            "can_go_back": False, "can_go_forward": False,
        }


class TestDesktopBridgeTabParsing:
    """桌面桥 tab 解析必须透传 navigationHistory 边界（否则工具栏永不置灰）。"""

    def _bridge(self) -> object:
        from illusion.browser.desktop_bridge import DesktopBridgeBackend
        return DesktopBridgeBackend.__new__(DesktopBridgeBackend)

    def test_carries_nav_bounds(self) -> None:
        info = self._bridge()._tab({  # type: ignore[attr-defined]
            "id": "t2", "url": "https://example.com/b", "title": "B",
            "active": True, "can_go_back": True, "can_go_forward": False,
        })
        assert info.can_go_back is True
        assert info.can_go_forward is False

    def test_defaults_when_absent(self) -> None:
        info = self._bridge()._tab({"id": "t1", "url": "about:blank"})  # type: ignore[attr-defined]
        assert info.can_go_back is False
        assert info.can_go_forward is False


class TestPickPayloadUnwrapping:
    """拾取链路：picker 脚本返回 {status, element}，回传需解包 element。

    App 的 toElementPayload 口径与后端 pick_script 返回值的接缝：
    漏解包时前端拿到 {status:'selected', element:{...}} 而读不到
    tagName/pageUrl 等字段， payload 校验直接落空（拾取不回传根因）。
    """

    ELEMENT: ClassVar[dict[str, object]] = {
        "pageUrl": "https://example.com",
        "pageTitle": "Example",
        "tagName": "button",
        "role": "button",
        "accessibleName": "提交",
        "selector": "#submit",
        "ref": "e7",
        "capturedAt": 1_700_000_000_000,
    }

    def _unwrap(self, raw: dict) -> dict:
        inner = (raw.get("element") if isinstance(raw.get("element"), dict) else raw)
        return {**inner, "workspacePath": "default"}

    def test_selected_unwraps_element(self) -> None:
        payload = self._unwrap({"status": "selected", "element": self.ELEMENT})
        assert payload["tagName"] == "button"
        assert payload["pageUrl"] == "https://example.com"
        assert payload["ref"] == "e7"
        assert payload["capturedAt"] == 1_700_000_000_000

    def test_cancelled_has_no_element_fields(self) -> None:
        payload = self._unwrap({"status": "cancelled"})
        assert "tagName" not in payload  # 合法性校验拦截，不产生附件


# ---------------------------------------------------------------------------
# 代理解析（无头 Chromium 不继承 Windows 系统代理，需显式接入）
# ---------------------------------------------------------------------------

class TestResolveProxy:
    def test_explicit_url(self) -> None:
        from illusion.browser.executable import resolve_proxy
        assert resolve_proxy("http://127.0.0.1:7890") == {"server": "http://127.0.0.1:7890"}

    def test_off_and_direct_aliases(self) -> None:
        from illusion.browser.executable import resolve_proxy
        for value in ("off", "none", "direct", ""):
            assert resolve_proxy(value) is None

    def test_auto_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser.executable import resolve_proxy
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
        assert resolve_proxy("auto") == {"server": "http://proxy.example:8080"}

    def test_auto_windows_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import illusion.browser.executable as exe_mod

        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.setattr(exe_mod.sys, "platform", "win32")

        class _FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeWinreg:
            HKEY_CURRENT_USER = 0

            @staticmethod
            def OpenKey(root, path):
                assert "Internet Settings" in path
                return _FakeKey()

            @staticmethod
            def QueryValueEx(key, name):
                if name == "ProxyEnable":
                    return (1, None)
                if name == "ProxyServer":
                    return ("127.0.0.1:7890", None)
                raise OSError(name)

            @staticmethod
            def CloseKey(key):
                pass

        monkeypatch.setitem(exe_mod.__dict__, "winreg", _FakeWinreg)
        # resolve_proxy 内部 `import winreg` 需注入 sys.modules
        import sys
        monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg)
        assert exe_mod.resolve_proxy("auto") == {"server": "http://127.0.0.1:7890"}

    def test_auto_registry_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        import illusion.browser.executable as exe_mod

        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(exe_mod.sys, "platform", "win32")

        class _FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeWinreg:
            HKEY_CURRENT_USER = 0

            @staticmethod
            def OpenKey(root, path):
                return _FakeKey()

            @staticmethod
            def QueryValueEx(key, name):
                if name == "ProxyEnable":
                    return (0, None)
                raise OSError(name)

            @staticmethod
            def CloseKey(key):
                pass

        monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg)
        assert exe_mod.resolve_proxy("auto") is None


# ---------------------------------------------------------------------------
# 报告修复回归：ref 防撞号 / type 元素校验 / select-all / 水平滚动预检
# （P0-P2 缺陷的固化测试；真实浏览器行为由 e2e 复测脚本覆盖）
# ---------------------------------------------------------------------------

class TestSnapshotRefAllocation:
    """快照脚本的 ref 分配不变量（JS 行为由 e2e 复测覆盖，此处锁关键源码）。

    P0 根因：data-illusion-ref 持久化在 DOM 上而计数器每轮从 0 起步 →
    新元素撞旧 ref。修复三要素必须在脚本中同时存在：
      1. 预扫描现存 ref（refCounts / usedRefs）
      2. 计数器从最大编号起步
      3. 仅保留"全文档唯一"的现存 ref（撞号双方重分配）
    """

    def _script(self) -> str:
        from illusion.browser.snapshot import SNAPSHOT_SCRIPT
        return SNAPSHOT_SCRIPT

    def test_precollect_existing_refs(self) -> None:
        script = self._script()
        assert "querySelectorAll('[data-illusion-ref]')" in script
        assert "refCounts" in script
        assert "usedRefs" in script

    def test_counter_starts_at_max(self) -> None:
        script = self._script()
        assert "let refCounter = maxRefNum;" in script
        assert "if (n > maxRefNum) maxRefNum = n;" in script

    def test_linear_probe_avoids_collision(self) -> None:
        script = self._script()
        assert "while (usedRefs.has('e' + n)) n += 1;" in script

    def test_only_unique_existing_ref_kept(self) -> None:
        script = self._script()
        assert "refCounts[existing] === 1" in script

    def test_max_nodes_cap_unchanged(self) -> None:
        # 防止修复时误改体积上限语义
        script = self._script()
        assert "MAX_NODES = 1200" in script

class TestBrowserTypeValidation:
    """browser_type 元素类型校验（_assert_typeable 的 JS 探测逻辑）。"""

    def test_error_message_shape(self) -> None:
        # 校验拒绝信息包含可操作指引（供 LLM 自纠错）
        # 消息契约由 e2e 复测覆盖；此处防误删常量
        import inspect

        from illusion.browser.playwright_backend import PlaywrightBackend
        src = inspect.getsource(PlaywrightBackend)
        assert "non-typeable" in src
        assert "browser_click for links and buttons" in src


class TestPressKeySelectAllGuard:
    def test_guard_message_present(self) -> None:
        import inspect

        from illusion.browser.playwright_backend import PlaywrightBackend
        src = inspect.getsource(PlaywrightBackend.press_key)
        assert "select-all (Control+a) requires a focused editable element" in src


class TestScrollGuard:
    def test_no_effect_error_present(self) -> None:
        import inspect

        from illusion.browser.playwright_backend import PlaywrightBackend
        src = inspect.getsource(PlaywrightBackend.scroll)
        # P1-3 修复契约：wheel→验证→scrollBy 兜底→无效报错
        assert "Scroll had no effect" in src
        assert "window.scrollBy" in src
        assert "mouse.wheel" in src


class TestEvaluateUndefined:
    def test_wrap_expression_forms(self) -> None:
        from illusion.browser.playwright_backend import _wrap_expression
        assert _wrap_expression("1 + 1") == "(() => (1 + 1))()"
        assert _wrap_expression("() => document.title") == "(() => document.title)()"
        assert _wrap_expression("async () => 42") == "(async () => 42)()"
        assert _wrap_expression("const a = 2; return a * 3;") == "(() => { const a = 2; return a * 3; })()"

    def test_sentinel_in_source(self) -> None:
        import inspect

        from illusion.browser.playwright_backend import PlaywrightBackend
        src = inspect.getsource(PlaywrightBackend.evaluate)
        assert "__illusion_undef_7f3a__" in src
        assert "Promise.resolve(" in src


class TestSessionRecoveryHint:
    def test_error_hint_pattern(self) -> None:
        from illusion.browser.base import BrowserCommandError
        from illusion.tools.browser_tools import _error
        result = _error(BrowserCommandError("Target page, tab or webview closed"))
        assert result.is_error
        assert "Recovery:" in result.output
        assert "browser_close" in result.output

    def test_plain_error_untouched(self) -> None:
        from illusion.browser.base import BrowserCommandError
        from illusion.tools.browser_tools import _error
        result = _error(BrowserCommandError("Click failed: timeout"))
        assert "Recovery:" not in result.output


class TestTypeFocusSemantics:
    """P1-1 第三轮：ref 路径 focus() 而非 click()（点击折叠 select-all 选区）。"""

    def test_ref_path_uses_focus_not_click(self) -> None:
        import inspect

        from illusion.browser.playwright_backend import PlaywrightBackend
        src = inspect.getsource(PlaywrightBackend.type_text)
        # 关键契约：查聚焦状态跳过、focus() 聚焦；type_text 内不得出现 locator.click
        assert "focus_state" in src
        assert "locator.focus(" in src
        assert "locator.click(" not in src

    def test_append_hint_suggests_select_all(self) -> None:
        import inspect

        from illusion.browser.playwright_backend import PlaywrightBackend
        src = inspect.getsource(PlaywrightBackend.type_text)
        # P2-1 文案必须引导真实可行的替换路径
        assert "appended" in src
        assert "Control+a" in src
        # 旧文案（误导：传 ref 并不替换）不得再出现
        assert "pass a ref to replace" not in src


class TestEvalNestedUndefined:
    """P1-4：哨兵序列化契约（嵌套 undefined 在文本输出中保留）。"""

    def test_sentinel_render(self) -> None:
        from illusion.browser.playwright_backend import _render_eval_result
        # 顶层 undefined
        assert _render_eval_result({"__illusion_undef_7f3a__": 1}) == "undefined"
        # 嵌套
        assert _render_eval_result({"a": {"__illusion_undef_7f3a__": 1}}) == '{"a": undefined}'
        assert _render_eval_result([1, {"__illusion_undef_7f3a__": 1}]) == '[1, undefined]'
        # 混合 null
        assert _render_eval_result(
            {"a": {"__illusion_undef_7f3a__": 1}, "b": None},
        ) == '{"a": undefined, "b": null}'
        # 字符串原样
        assert _render_eval_result("hi") == "hi"

    def test_sentinel_in_evaluate_source(self) -> None:
        import inspect

        from illusion.browser.playwright_backend import PlaywrightBackend
        src = inspect.getsource(PlaywrightBackend.evaluate)
        assert "__illusion_undef_7f3a__" in src
        assert "toJSON" in src  # Date 等对象兼容


class TestDesktopBridgePickOps:
    """桌面桥 pick 操作与 Electron 控制服务器 op 表对齐（审查修复回归）。"""

    def test_pick_script_posts_pickscript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser import desktop_bridge as mod

        captured: dict[str, object] = {}

        class FakeBridge(mod.DesktopBridgeBackend):
            async def _post(self, op: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
                captured["op"] = op
                captured["payload"] = payload or {}
                return {"element": {"tagName": "button"}}

        bridge = FakeBridge()
        result = asyncio_run(bridge.pick_script("t1", "(() => 1)()"))
        assert captured["op"] == "pickScript"
        assert captured["payload"]["script"] == "(() => 1)()"
        assert result == {"tagName": "button"}

    def test_pick_cancel_posts_pickcancel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from illusion.browser import desktop_bridge as mod

        captured: dict[str, object] = {}

        class FakeBridge(mod.DesktopBridgeBackend):
            async def _post(self, op: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
                captured["op"] = op
                return {}

        asyncio_run(FakeBridge().pick_cancel("t1"))
        assert captured["op"] == "pickCancel"

    def test_coordinate_pick_not_supported(self) -> None:
        """坐标拾取在桌面桥不覆盖 → 继承基类 not supported（诚实报错而非 Unknown op）。"""
        from illusion.browser.base import BrowserCommandError
        from illusion.browser.desktop_bridge import DesktopBridgeBackend

        bridge = DesktopBridgeBackend.__new__(DesktopBridgeBackend)
        with pytest.raises(BrowserCommandError, match="not supported"):
            asyncio_run(bridge.pick("t1", 1.0, 2.0))


class TestPickScriptTemplatePlaceholder:
    """坐标拾取模板占位符贯通（此前 __PICK_XY__ 与模板失配 → 静默 (0,0)）。"""

    def test_placeholder_substituted(self) -> None:
        import json

        from illusion.browser.snapshot import PICK_SCRIPT_TEMPLATE

        args = json.dumps({"x": 15.0, "y": 25.0})
        script = PICK_SCRIPT_TEMPLATE.replace("__PICK_XY_ARGS__", args)
        assert "__PICK_XY_ARGS__" not in script
        assert '"x": 15.0' in script and '"y": 25.0' in script


def asyncio_run(coro):  # 局部辅助：避免顶部再导 asyncio（文件已有用例独立成类）
    import asyncio

    return asyncio.run(coro)


class TestPlanModeBlocksBrowserInteractions:
    """plan 模式下浏览器交互类工具必须被拦（不得凭 is_read_only 放行）。"""

    def _checker(self) -> object:
        from illusion.config.settings import PermissionSettings
        from illusion.permissions.checker import PermissionChecker
        from illusion.permissions.modes import PermissionMode

        settings = PermissionSettings(mode=PermissionMode.PLAN)
        return PermissionChecker(settings)

    def test_interactive_blocked(self) -> None:
        from illusion.permissions.checker import PLAN_BLOCKED_BROWSER_TOOLS

        checker = self._checker()
        for name in sorted(PLAN_BLOCKED_BROWSER_TOOLS):
            decision = checker.evaluate(name, is_read_only=True)
            assert not decision.allowed, f"{name} should be blocked in plan mode"
            assert decision.auto_blocked

    def test_blocking_set_covers_mutating_tools(self) -> None:
        """阻断集必须覆盖全部可改变页面状态的 browser_* 工具——
        新增交互类工具时若漏登记，此用例先行失败。"""
        from illusion.permissions.checker import PLAN_BLOCKED_BROWSER_TOOLS

        assert PLAN_BLOCKED_BROWSER_TOOLS == {
            "browser_navigate", "browser_click", "browser_type",
            "browser_press_key", "browser_scroll", "browser_tabs",
            "browser_close", "browser_resize", "browser_evaluate",
        }

    def test_observation_allowed(self) -> None:
        checker = self._checker()
        for name in ("browser_snapshot", "browser_screenshot", "browser_wait"):
            decision = checker.evaluate(name, is_read_only=True)
            assert decision.allowed, f"{name} should remain allowed in plan mode"
