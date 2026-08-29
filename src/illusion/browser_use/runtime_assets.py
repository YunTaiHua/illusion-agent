"""node_repl 运行时资产定位与 MCP 启动命令
==========================================

定位随包分发的 node_repl MCP 服务器资产并构建启动命令：

- 打包形态：``illusion/_browser_runtime/``（hatch force-include 注入 wheel）
- 源码形态：仓库 ``browser_runtime/dist/``（npm run build 产物）

资产布局::

    _browser_runtime/
      mcp-server.js        esbuild 产物（stdio MCP 服务器 + vm 内核 + broker 客户端）
      browser-client.mjs   内核内加载的 agent.browsers 客户端（自 browser-use 插件移植）
      docs/                api.json / documents.json / markdown 文档（documentationRoot）
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 打包形态的资产目录（illusion/_browser_runtime）
_PACKAGED_DIR = Path(__file__).resolve().parent.parent / "_browser_runtime"
# 源码形态：runtime_assets.py 位于 src/illusion/browser_use/，仓库根为其上三级
_REPO_ROOT = Path(__file__).resolve().parents[3]


def browser_runtime_root() -> Path | None:
    """返回包含 mcp-server.js 的运行时资产根目录；不可用时 None。"""
    if (_PACKAGED_DIR / "mcp-server.js").is_file():
        return _PACKAGED_DIR
    dev_dir = _REPO_ROOT / "browser_runtime" / "dist"
    if (dev_dir / "mcp-server.js").is_file():
        return dev_dir
    return None


def documentation_root() -> Path | None:
    """返回 documentationRoot（api.json 所在目录）。"""
    root = browser_runtime_root()
    if root is None:
        return None
    docs = root / "docs"
    return docs if (docs / "api.json").is_file() else root


def browser_client_path() -> Path | None:
    """返回 browser-client.mjs 路径（skill 引导代码经插件根环境变量解析）。"""
    root = browser_runtime_root()
    if root is None:
        return None
    client = root / "browser-client.mjs"
    return client if client.is_file() else None


def build_mcp_launch_command() -> tuple[list[str], dict[str, str]] | None:
    """构建 node_repl MCP 服务器启动命令与基础环境变量。

    Returns:
        (命令参数列表, 注入环境变量)；运行时资产缺失时 None。

    环境变量说明：
        ILLUSION_PLUGIN_ROOT: 插件运行时根（skill 引导代码按此解析
            browser-client.mjs；兼容读取 ZCODE_PLUGIN_ROOT/CLAUDE_PLUGIN_ROOT）
        ILLUSION_CONFIG_DIR: 会话配置目录（截图等 artifact 落盘根）
    """
    root = browser_runtime_root()
    if root is None:
        logger.warning("browser_use 运行时资产缺失，无法注入 node_repl MCP 服务器")
        return None
    from illusion.ui.react_launcher import _resolve_node

    env = {
        # 与官方插件 env 名对齐，保证移植的 skill 引导代码无需改动即可工作
        "ILLUSION_PLUGIN_ROOT": str(root),
        "ZCODE_PLUGIN_ROOT": str(root),
        "CLAUDE_PLUGIN_ROOT": str(root),
    }
    return [_resolve_node(), str(root / "mcp-server.js")], env
