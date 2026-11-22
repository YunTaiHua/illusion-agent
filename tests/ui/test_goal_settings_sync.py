"""apply_goal_settings_sync 热切换单元测试。

回归：session bundle 是工作区 bundle 的浅拷贝（build_session_engine
对 goal_manager / tool_metadata 做创建时快照拷贝），goal 开关热切换
必须按 engine 身份去重后覆盖全部已物化会话引擎——只更新工作区原
引擎会让正在对话的会话永远拿不到（或摘不掉）GoalManager。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from illusion.goal.manager import GoalManager
from illusion.goal.types import GoalSettings
from illusion.tools.base import ToolRegistry
from illusion.tools.goal_tools import CreateGoalTool, GetGoalTool, UpdateGoalTool
from illusion.ui.web.ws_host import WebBackendHost

GOAL_TOOL_NAMES = {"get_goal", "create_goal", "update_goal"}


def _make_host(goal_enabled: bool) -> tuple[WebBackendHost, MagicMock]:
    """构造跳过 __init__ 的 host 桩（只打桩 apply_goal_settings_sync 用到的成员）。"""
    host = WebBackendHost.__new__(WebBackendHost)
    bundle = MagicMock()
    bundle.tool_registry = ToolRegistry()
    bundle.engine._tool_metadata = {}
    bundle.engine._goal_manager = None

    from illusion.config.settings import Settings

    settings = Settings()
    settings.goal.enabled = goal_enabled
    bundle.current_settings = MagicMock(return_value=settings)

    host._bundle = bundle
    host._active_bundle = MagicMock(return_value=bundle)
    host._workspace_bundles = MagicMock(return_value=[bundle])
    host._sessions = {}
    host._create_background_task = MagicMock()
    host._emit = AsyncMock()
    host._status_snapshot = MagicMock(return_value={})
    return host, bundle


def _register_goal_tools(registry: ToolRegistry) -> None:
    for tool in (GetGoalTool(), CreateGoalTool(), UpdateGoalTool()):
        registry.register(tool)


def test_goal_hot_toggle_on_reaches_materialized_session():
    host, bundle = _make_host(goal_enabled=True)
    session = MagicMock()
    session.engine._tool_metadata = {}
    session.engine._goal_manager = None
    session.bundle = MagicMock()
    session.bundle.tool_registry = ToolRegistry()
    host._sessions = {"s1": session}

    host.apply_goal_settings_sync()

    # 工作区原引擎与已物化会话引擎都必须拿到 GoalManager，且是各自独立的
    # 实例——GoalManager 是可变有状态机（revision CAS / 轮次计数 /
    # checkpoint 回写），共享实例会让并发会话的 goal 状态互相污染
    assert isinstance(bundle.engine._goal_manager, GoalManager)
    assert isinstance(session.engine._goal_manager, GoalManager)
    assert session.engine._goal_manager is not bundle.engine._goal_manager
    # 各引擎 metadata 持有自己的管理器实例
    assert session.engine._tool_metadata["goal_manager"] is session.engine._goal_manager
    assert bundle.engine._tool_metadata["goal_manager"] is bundle.engine._goal_manager
    # goal 工具注册进两个 registry
    for reg in (bundle.tool_registry, session.bundle.tool_registry):
        assert GOAL_TOOL_NAMES <= {t.name for t in reg.list_tools()}


def test_goal_hot_toggle_off_clears_materialized_session():
    host, bundle = _make_host(goal_enabled=False)
    stale_manager = GoalManager(GoalSettings())
    _register_goal_tools(bundle.tool_registry)

    session = MagicMock()
    session.engine._tool_metadata = {"goal_manager": stale_manager}
    session.engine._goal_manager = stale_manager
    session.bundle = MagicMock()
    session.bundle.tool_registry = ToolRegistry()
    _register_goal_tools(session.bundle.tool_registry)
    host._sessions = {"s1": session}

    host.apply_goal_settings_sync()

    # 工作区原引擎与已物化会话引擎的 manager 都被摘除
    assert bundle.engine._goal_manager is None
    assert session.engine._goal_manager is None
    assert "goal_manager" not in bundle.engine._tool_metadata
    assert "goal_manager" not in session.engine._tool_metadata
    # goal 工具从两个 registry 注销
    for reg in (bundle.tool_registry, session.bundle.tool_registry):
        assert not (GOAL_TOOL_NAMES & {t.name for t in reg.list_tools()})


def test_goal_hot_toggle_dedupes_shared_engine():
    """工作区 bundle 与会话共享同一引擎时只应用一次（按 engine 身份去重）。

    现实对应：session bundle 是工作区 bundle 的浅拷贝——引擎与
    registry 都是同一对象引用；去重防止对同一引擎重复应用。
    """
    host, bundle = _make_host(goal_enabled=True)
    session = MagicMock()
    session.engine = bundle.engine  # 同一引擎对象
    session.bundle = MagicMock()
    session.bundle.tool_registry = bundle.tool_registry  # 浅拷贝共享 registry
    host._sessions = {"s1": session}

    host.apply_goal_settings_sync()

    assert isinstance(session.engine._goal_manager, GoalManager)
    # 共享 registry 在 workspace 路径已应用，会话侧可见同一结果
    assert GOAL_TOOL_NAMES <= {t.name for t in session.bundle.tool_registry.list_tools()}
