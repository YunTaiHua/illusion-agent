"""启动不得物化项目资源目录。"""
from __future__ import annotations

from pathlib import Path

import pytest


class NoNetworkApiClient:
    """客户端桩：启动若尝试模型请求则直接失败。"""

    async def stream_message(self, request):
        del request
        raise AssertionError("startup must not call the model")


@pytest.mark.asyncio
async def test_real_runtime_startup_keeps_empty_workspace_read_only(tmp_path: Path, monkeypatch):
    """构建并启动运行时不得创建工作区 .illusion 资源。"""
    workspace = tmp_path / "empty-workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setenv("ILLUSION_LOGS_DIR", str(tmp_path / "user-logs"))
    monkeypatch.setenv("ILLUSION_BROWSER_PREWARM", "0")

    from illusion.coordinator.agent_definitions import get_all_agent_definitions
    from illusion.hooks.loader import load_hook_registry
    from illusion.mcp.config import load_mcp_server_configs
    from illusion.plugins.loader import discover_plugin_paths
    from illusion.skills.loader import load_skill_registry
    from illusion.config.settings import Settings

    from illusion.services.session_storage import list_session_snapshots
    from illusion.ui.runtime import build_runtime, close_runtime, start_runtime, sync_app_state
    from illusion.ui.web.ws_web_api import _collect_resources

    bundle = await build_runtime(
        api_client=NoNetworkApiClient(),
        cwd=str(workspace),
    )
    try:
        await start_runtime(bundle)
        sync_app_state(bundle)

        # 覆盖启动/资源面板使用的五个资源根目录。
        load_skill_registry(workspace)
        load_hook_registry(Settings(), cwd=workspace)
        load_mcp_server_configs(Settings(), [], cwd=workspace)
        get_all_agent_definitions(cwd=str(workspace))
        discovered = discover_plugin_paths(workspace)
        assert all(workspace / ".illusion" not in path.parents for path in discovered)
        resources = _collect_resources(bundle)
        assert set(resources) >= {"skills", "agents", "plugins", "rules", "mcp_servers"}
        assert list_session_snapshots(workspace) == []

        assert not (workspace / ".illusion").exists()
    finally:
        await close_runtime(bundle)

    assert not (workspace / ".illusion").exists()
    assert not list(workspace.iterdir())
