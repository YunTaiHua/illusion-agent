"""
内置插件 seed 机制
==================

把随包分发的 browser-use 插件
（plugin.json + skills + docs）按版本 seed 到用户级插件目录
``~/.illusion/plugins/browser-use/``——版本一致则跳过，版本变化则整目录
替换（保留用户的启用/禁用状态，该状态在 settings.enabled_plugins 中）。

seed 幂等且在构建 RuntimeBundle 前调用，插件发现（plugins/loader.py）
随后自然扫描到该目录。
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from illusion.config.paths import get_config_dir

logger = logging.getLogger(__name__)

BUILTIN_PLUGIN_NAME = "browser-use"

# 包内插件源目录（随 wheel 分发的数据文件）
_PLUGIN_DATA_DIR = Path(__file__).parent / "plugin_data" / BUILTIN_PLUGIN_NAME


def seed_builtin_browser_plugin() -> Path | None:
    """把内置 browser-use 插件 seed 到用户插件目录。

    Returns:
        Path | None: seed 后的插件目录；包内数据缺失时返回 None。
    """
    manifest_path = _PLUGIN_DATA_DIR / "plugin.json"
    if not manifest_path.is_file():
        logger.debug("[browser] builtin plugin data missing, skip seeding")
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("[browser] builtin plugin manifest unreadable, skip seeding")
        return None
    version = str(manifest.get("version", "0.0.0"))

    target = get_config_dir() / "plugins" / BUILTIN_PLUGIN_NAME
    marker = target / ".illusion-plugin-seed.json"
    if _already_seeded(target, marker, version):
        return target

    # 原子替换：先写临时目录再 rename，避免半拷贝状态被发现
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{BUILTIN_PLUGIN_NAME}.seed-tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(_PLUGIN_DATA_DIR, tmp)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    marker.write_text(json.dumps({"version": version, "source": "builtin"}, ensure_ascii=False),
                      encoding="utf-8")
    logger.info("[browser] seeded builtin plugin browser-use@%s to %s", version, target)
    return target


def _already_seeded(target: Path, marker: Path, version: str) -> bool:
    if not marker.is_file() or not target.is_dir():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return str(data.get("version")) == version and (target / "plugin.json").is_file()
