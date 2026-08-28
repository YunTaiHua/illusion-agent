"""Computer Use 功能测试。

覆盖：settings 默认值与落盘、plugin 注入（开关控制）、skill 注入、
MCP 服务器配置合并、工具结果提示、斜杠命令、二进制下载信息与版本比较。
"""

from __future__ import annotations

import json

import pytest

from illusion.config.paths import get_config_file_path
from illusion.config.settings import Settings, load_settings
from illusion.mcp.config import load_mcp_server_configs
from illusion.plugins.loader import load_plugins
from illusion.skills.loader import load_skill_registry


def test_settings_default_disabled() -> None:
    """computer_use 默认关闭。"""
    settings = Settings()
    assert settings.computer_use.enabled is False


def test_default_config_written_on_load() -> None:
    """settings.json 缺失 computer_use 时，加载会写入默认配置。"""
    cfg_path = get_config_file_path()
    cfg_path.write_text(json.dumps({"model": "env_1.model_1"}), encoding="utf-8")
    settings = load_settings()
    assert settings.computer_use.enabled is False
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw.get("computer_use") == {"enabled": False}


def test_plugin_not_injected_when_disabled() -> None:
    """关闭时不以 plugin 方式注入 computer 插件。"""
    settings = Settings()
    plugins = load_plugins(settings, ".")
    assert not any(plugin.name == "computer" for plugin in plugins)


def test_plugin_injected_when_enabled() -> None:
    """开启时注入 computer 插件（MCP 服务器 + skill）。"""
    settings = Settings()
    settings.computer_use.enabled = True
    plugins = load_plugins(settings, ".")
    computer = next((p for p in plugins if p.name == "computer"), None)
    assert computer is not None
    assert computer.enabled is True
    assert [skill.name for skill in computer.skills] == ["computer:computer-use"]
    assert "cua" in computer.mcp_servers
    server = computer.mcp_servers["cua"]
    assert server.args == ["mcp"]


def test_skill_registry_contains_computer_skill() -> None:
    """开启时 computer skill 进入 skill 注册表。"""
    from illusion.config.settings import save_settings

    settings = Settings()
    settings.computer_use.enabled = True
    save_settings(settings)
    load_plugins(settings, ".")
    registry = load_skill_registry(".")
    skill = registry.get("computer:computer-use")
    assert skill is not None
    assert "get_window_state" in skill.content
    # skill 内容与实际暴露的工具前缀一致
    assert "mcp__computer_cua__" in skill.content
    # skill 教导正确的元素寻址协议（snapshot_id / element_token）
    assert "snapshot_id" in skill.content
    assert "element_token" in skill.content


def test_mcp_config_merges_computer_server() -> None:
    """开启时 computer MCP 服务器以 "computer:cua" 键合并。"""
    settings = Settings()
    settings.computer_use.enabled = True
    plugins = load_plugins(settings, ".")
    configs = load_mcp_server_configs(settings, plugins, ".")
    assert "computer:cua" in configs


def test_hint_text() -> None:
    """工具结果提示只在 computer 服务器上追加。"""
    from illusion.computer.hint import computer_use_hint

    hint = computer_use_hint("computer:cua")
    assert "computer:computer-use" in hint
    assert computer_use_hint("other-server") == ""


def test_tool_allowlist_filters_extra_tools() -> None:
    """computer 服务器按白名单过滤，其他服务器全部暴露。"""
    from illusion.computer.constants import COMPUTER_TOOL_ALLOWLIST
    from illusion.computer.hint import is_computer_tool_exposed

    # 白名单核心工具应暴露
    assert is_computer_tool_exposed("computer:cua", "get_window_state")
    assert is_computer_tool_exposed("computer:cua", "click")
    assert is_computer_tool_exposed("computer:cua", "type_text")
    # cua-driver 的浏览器/录制/生命周期等工具应被过滤
    assert not is_computer_tool_exposed("computer:cua", "browser_navigate")
    assert not is_computer_tool_exposed("computer:cua", "start_recording")
    assert not is_computer_tool_exposed("computer:cua", "health_report")
    assert not is_computer_tool_exposed("computer:cua", "check_permissions")
    # 白名单规模远小于 cua-driver 实际暴露的工具数（50+）
    assert len(COMPUTER_TOOL_ALLOWLIST) <= 20
    # 非 computer 服务器全部暴露
    assert is_computer_tool_exposed("other-server", "anything")


def test_tool_registry_filters_computer_tools() -> None:
    """create_default_tool_registry 只注册白名单内的 computer 工具。"""
    from illusion.mcp.types import McpToolInfo
    from illusion.tools import create_default_tool_registry

    class FakeManager:
        """模拟只暴露 computer 服务器工具的 MCP 管理器。"""

        def __init__(self) -> None:
            names = ["click", "type_text", "browser_navigate", "health_report"]
            self._tools = [
                McpToolInfo(
                    server_name="computer:cua",
                    name=name,
                    description=name,
                    input_schema={"type": "object", "properties": {}},
                )
                for name in names
            ]

        def list_tools(self) -> list[McpToolInfo]:
            return self._tools

    registry = create_default_tool_registry(FakeManager())  # type: ignore[arg-type]
    tool_names = [tool.name for tool in registry.list_tools()]
    # 白名单内的工具以 mcp__computer_cua__ 前缀注册
    assert "mcp__computer_cua__click" in tool_names
    assert "mcp__computer_cua__type_text" in tool_names
    # 白名单外的工具被过滤
    assert "mcp__computer_cua__browser_navigate" not in tool_names
    assert "mcp__computer_cua__health_report" not in tool_names


def test_compare_versions() -> None:
    """版本号语义化比较。"""
    from illusion.computer.binary import _compare_versions

    assert _compare_versions("0.22.1", "0.22.0") > 0
    assert _compare_versions("0.22.0", "0.22.1") < 0
    assert _compare_versions("0.22.1", "0.22.1") == 0


def test_download_info() -> None:
    """下载 URL 与归档内路径按平台生成。"""
    from illusion.computer.binary import _build_download_info, get_platform_key

    url, archive_format, inner_path = _build_download_info("0.22.1")
    assert "cua-driver-rs-v0.22.1" in url
    assert archive_format in ("zip", "tar.gz")
    assert inner_path.endswith(("cua-driver.exe", "cua-driver"))
    if get_platform_key().endswith("-win32"):
        assert "windows" in url
        assert archive_format == "zip"
        assert inner_path.endswith("cua-driver.exe")


def test_download_fallback_when_api_unavailable(monkeypatch, tmp_path) -> None:
    """GitHub API 不可用时，下载回退到固定版本而非失败。"""
    from illusion.computer import binary as binary_mod

    used: dict[str, object] = {}
    monkeypatch.setattr(binary_mod, "get_latest_version", lambda *a, **k: None)

    def fake_build(version: str) -> tuple[str, str, str]:
        used["version"] = version
        return "https://example.invalid/cua.zip", "zip", "inner/cua-driver.exe"

    monkeypatch.setattr(binary_mod, "_build_download_info", fake_build)
    monkeypatch.setattr(binary_mod, "extract_archive", lambda *a, **k: None)
    monkeypatch.setattr(binary_mod, "get_bin_dir", lambda: str(tmp_path))
    monkeypatch.setattr(binary_mod.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(
        binary_mod.urllib.request, "urlretrieve", lambda *a, **k: None
    )

    path = binary_mod.download_cua_binary()
    assert used["version"] == binary_mod.DEFAULT_CUA_VERSION
    assert path


def test_check_update_graceful() -> None:
    """版本检查在二进制缺失/网络失败时优雅返回。"""
    from illusion.computer.binary import check_update

    result = check_update()
    assert "local_version" in result
    assert "latest_version" in result
    assert "update_available" in result


def test_mime_to_ext() -> None:
    """MIME 类型到扩展名映射。"""
    from illusion.mcp.client import _mime_to_ext

    assert _mime_to_ext("image/png") == ".png"
    assert _mime_to_ext("image/jpeg") == ".jpg"
    assert _mime_to_ext("image/webp; charset=x") == ".webp"
    assert _mime_to_ext("unknown/type") == ".png"


def test_save_mcp_image_to_cache() -> None:
    """MCP 图片内容落盘到 cache/computer 并返回路径（不传 base64 进上下文）。"""
    import base64
    from pathlib import Path

    from illusion.config.paths import get_config_dir
    from illusion.mcp.client import _save_mcp_image

    raw = b"\x89PNG\r\n\x1a\n" + b"image-bytes"
    b64 = base64.b64encode(raw).decode()

    class FakeImage:
        type = "image"
        data = b64
        mime_type = "image/png"

    result = _save_mcp_image(FakeImage(), "computer:cua")
    assert result.startswith("[Image saved to ")
    path = Path(result[len("[Image saved to "):-1])
    # 保存在 ~/.illusion/cache/computer/ 下（computer:cua -> computer 子目录）
    assert path.parent == get_config_dir() / "cache" / "computer"
    assert path.read_bytes() == raw


def test_save_mcp_image_invalid_base64_fallback() -> None:
    """base64 解码失败/数据为空时降级描述而非抛异常。"""
    from illusion.mcp.client import _save_mcp_image

    class BadImage:
        type = "image"
        data = "not-valid-base64!!!"
        mime_type = "image/png"

    result = _save_mcp_image(BadImage(), "computer:cua")
    assert "unable to save" in result

    class EmptyImage:
        type = "image"
        data = ""
        mime_type = "image/png"

    result = _save_mcp_image(EmptyImage(), "computer:cua")
    assert "unable to save" in result


@pytest.mark.asyncio
async def test_call_tool_saves_image_content() -> None:
    """call_tool 对图片内容落盘并返回路径，图片 base64 不进入工具结果。"""
    import base64

    from mcp.types import CallToolResult, ImageContent, TextContent

    from illusion.mcp.client import McpClientManager
    from illusion.mcp.types import McpStdioServerConfig

    raw = b"\x89PNG\r\n\x1a\n" + b"dispatch-bytes"
    b64 = base64.b64encode(raw).decode()

    class FakeSession:
        async def call_tool(self, tool_name: str, arguments: dict) -> CallToolResult:
            return CallToolResult(
                content=[
                    TextContent(text="window_id=100 pid=200 elements=1"),
                    ImageContent(data=b64, mime_type="image/png"),
                ],
                structured_content={
                    "snapshot_id": "s00000001",
                    "screenshot_png_b64": b64,  # 超大字段不应进入结果
                },
            )

    manager = McpClientManager({"computer:cua": McpStdioServerConfig(command="echo")})
    manager._require_session = lambda _name: FakeSession()  # type: ignore[method-assign]
    out = await manager.call_tool("computer:cua", "get_window_state", {})
    assert "window_id=100" in out
    assert "Image saved to" in out
    # 图片 base64 不再进入工具结果（避免撑爆 LLM 上下文）
    assert b64 not in out
    # structured_content 的关键字段（snapshot_id）提供给 LLM，
    # 但超大字段（screenshot base64）不进入
    assert "snapshot_id=s00000001" in out


def test_summarize_structured_content() -> None:
    """structured_content 只提取紧凑关键字段，排除超大字段。"""
    from illusion.mcp.client import _summarize_structured_content

    summary = _summarize_structured_content(
        {"snapshot_id": "s00000003", "screenshot_png_b64": "big-base64-data"}
    )
    assert summary == "snapshot_id=s00000003"
    # 无可提取字段时返回 None
    assert _summarize_structured_content({"foo": "bar"}) is None
    assert _summarize_structured_content(None) is None
    assert _summarize_structured_content("plain string") is None


@pytest.mark.asyncio
async def test_computer_command_toggle() -> None:
    """/computer on 开启开关并落盘。"""
    from illusion.commands.registry import create_default_command_registry
    from illusion.commands.types import CommandContext

    registry = create_default_command_registry()
    command, _ = registry.lookup("/computer")
    assert command is not None
    ctx = CommandContext(cwd=".", engine=None)
    res = await command.handler("on", ctx)
    assert "enabled" in res.message.lower()
    assert load_settings().computer_use.enabled is True
    # 再次关闭
    res = await command.handler("off", ctx)
    assert "disabled" in res.message.lower()
    assert load_settings().computer_use.enabled is False


@pytest.mark.asyncio
async def test_computer_command_update_missing_binary() -> None:
    """二进制未安装时 /computer update 不抛异常。"""
    from illusion.commands.registry import create_default_command_registry
    from illusion.commands.types import CommandContext

    registry = create_default_command_registry()
    command, _ = registry.lookup("/computer")
    ctx = CommandContext(cwd=".", engine=None)
    # 未安装 + 网络查询失败时应返回提示而非崩溃
    res = await command.handler("update", ctx)
    assert res.message
