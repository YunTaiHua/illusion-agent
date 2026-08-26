"""
@ 提及补全共享模块
==================

提供 terminal 与 web 两个前端共用的 @ 提及（@file / @skill）补全候选收集：

    - normalize_mention_query: 查询串规范化
    - file_mention_candidates: 工作区内文件/目录路径候选（BFS，不读内容）
    - skill_mention_candidates: 技能名候选（名称 + 描述）

安全边界：文件候选以工作区根为界 BFS 遍历，过滤规则与文件树一致
（tree_entry_visible），不会越出根目录；技能候选仅返回名称与描述。
选中后的提及文本保持普通 prompt 文本，内容由模型自行调用 read 工具获取。

主要组件：
    - normalize_mention_query: 规范化 @ 提及查询串
    - file_mention_candidates: 收集文件/目录路径候选
    - skill_mention_candidates: 收集技能提及候选

使用示例：
    >>> from illusion.ui.file_mentions import normalize_mention_query
    >>> normalize_mention_query(".\\src\\ma")
    'src/ma'
"""

from __future__ import annotations

import logging
import os
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 文件树可见性过滤（与右栏 Files 文件树共用同一规则）
# ---------------------------------------------------------------------------

# 文件树忽略的目录名（生成产物/依赖/缓存，体积大且无导航价值）
_TREE_IGNORED_NAMES = frozenset({
    "node_modules", "__pycache__", ".git", ".hg", ".svn",
    ".venv", "venv", "env", "dist", "build", "out", "target",
    ".next", ".nuxt", ".cache", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".idea", "coverage", ".turbo", ".parcel-cache",
})
# 点文件目录白名单（项目配置相关，导航有意义才展示）
_TREE_DOTDIR_ALLOW = frozenset({
    ".github", ".illusion", ".claude", ".cursor", ".devcontainer", ".vscode",
})


def tree_entry_visible(name: str, is_dir: bool) -> bool:
    """判断目录条目是否在文件树中可见。

    规则：忽略名单一律隐藏；点文件仅展示白名单内的目录，其余
    点文件/点目录（.env、.DS_Store 等）视为噪音隐藏。

    Args:
        name: 条目名（不含路径）
        is_dir: 是否目录

    Returns:
        bool: 可见返回 True
    """
    if name in _TREE_IGNORED_NAMES:
        return False
    if name.startswith("."):
        return is_dir and name in _TREE_DOTDIR_ALLOW
    return True


# ---------------------------------------------------------------------------
# @ 提及补全候选收集
# ---------------------------------------------------------------------------

# @ 提及补全候选上限（下拉菜单容量）
_MENTION_MAX_CANDIDATES = 20
# @ 提及补全扫描条目上限（防止巨型仓库全量遍历；超出标记截断）
_MENTION_MAX_SCANNED = 5000
# @ 提及补全目录深度上限
_MENTION_MAX_DEPTH = 12


def normalize_mention_query(query: str | None) -> str:
    """规范化 @ 提及查询串：统一 / 分隔、去首尾空白与多余前缀。

    Args:
        query: 前端输入的原始查询（@ 之后、光标之前的路径片段）

    Returns:
        str: 规范化后的查询串（可能为空串）
    """
    q = (query or "").strip().replace("\\", "/")
    while q.startswith("./"):
        q = q[2:]
    if q.startswith("/"):
        q = q.lstrip("/")
    return q.strip()


def file_mention_candidates(root: str, query: str) -> tuple[list[dict[str, Any]], bool]:
    """在工作区内收集 @ 提及补全候选（仅路径，不读内容）。

    以 root 为界 BFS 遍历，过滤规则与文件树一致（tree_entry_visible）。
    每层条目按「目录优先 + 名称不区分大小写」排序，BFS 天然浅层优先，
    因此凑满上限即可提前返回。匹配为大小写不敏感的子串包含
    （query 为空串时全部可见条目命中，等价于从根浏览）。

    Args:
        root: 工作区根目录（绝对路径）
        query: 规范化后的查询串

    Returns:
        tuple[list[dict[str, Any]], bool]: (候选列表, 是否因扫描/深度上限截断)；
        候选为 {path: 根相对路径(/ 分隔), kind: dir|file}
    """
    root_path = Path(root)
    lowered = query.lower()
    candidates: list[dict[str, Any]] = []
    truncated = False
    scanned = 0
    # FIFO 队列：元素为 (相对目录, 深度)；根目录 rel="" depth=0。
    # 多收集 1 个候选用于判定截断：恰好凑满上限时无法区分"正好这么多"
    # 与"还有更多"，找到上限+1 个才可靠地标记 truncated。
    queue: deque[tuple[str, int]] = deque([("", 0)])
    while queue and len(candidates) <= _MENTION_MAX_CANDIDATES and scanned < _MENTION_MAX_SCANNED:
        dir_rel, depth = queue.popleft()
        try:
            with os.scandir(root_path / dir_rel if dir_rel else root_path) as it:
                def _dir_key(e: os.DirEntry[str]) -> tuple[bool, str]:
                    try:
                        return (not e.is_dir(follow_symlinks=False), e.name.lower())
                    except OSError:
                        return (True, e.name.lower())
                children = sorted(it, key=_dir_key)
        except OSError:
            continue
        for child in children:
            scanned += 1
            if scanned > _MENTION_MAX_SCANNED:
                truncated = True
                break
            try:
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not tree_entry_visible(child.name, is_dir):
                continue
            child_rel = f"{dir_rel}/{child.name}" if dir_rel else child.name
            if lowered in child_rel.lower():
                candidates.append({"path": child_rel, "kind": "dir" if is_dir else "file"})
                if len(candidates) > _MENTION_MAX_CANDIDATES:
                    break
            if is_dir and depth < _MENTION_MAX_DEPTH:
                queue.append((child_rel, depth + 1))
        else:
            continue
        # 内层 break（凑满上限+1 或超扫描上限）：外层同步退出
        if len(candidates) > _MENTION_MAX_CANDIDATES:
            break
    if len(candidates) > _MENTION_MAX_CANDIDATES:
        truncated = True
    return candidates[:_MENTION_MAX_CANDIDATES], truncated


# @ 技能提及候选上限
_MENTION_MAX_SKILLS = 8


# 技能注册表短 TTL 缓存（补全按击键触发请求，避免每次重读/解析全部 SKILL.md）
_SKILL_REGISTRY_TTL = 5.0
_skill_registry_cache: dict[str, tuple[float, Any]] = {}


def skill_mention_candidates(cwd: str, query: str) -> list[dict[str, str]]:
    """收集 @ 技能提及候选（名称 + 描述，按查询串过滤并按相关度排序）。

    与文件候选同走一个补全菜单；匹配为大小写不敏感的子串包含。
    排序按相关度分层：名称前缀命中 > 名称包含 > 仅描述命中，
    层内按名称字母序——避免描述含常见字母的长尾技能把精确
    前缀候选挤出上限窗口。注册表按 cwd 缓存 5 秒（TTL），
    加载失败返回空列表，补全菜单静默降级。

    Args:
        cwd: 工作区根目录（技能注册表按此解析用户级/项目级技能）
        query: 规范化后的查询串

    Returns:
        list[dict[str, str]]: 候选列表，元素为 {name, description}
    """
    import time

    now = time.monotonic()
    cached = _skill_registry_cache.get(cwd)
    if cached is not None and now - cached[0] <= _SKILL_REGISTRY_TTL:
        registry = cached[1]
    else:
        try:
            from illusion.skills.loader import load_skill_registry
            registry = load_skill_registry(cwd)
        except Exception:
            log.exception("收集技能提及候选失败")
            return []
        _skill_registry_cache[cwd] = (now, registry)
    lowered = query.lower()

    def _rank(s: dict[str, str]) -> tuple[int, str]:
        name = s["name"].lower()
        if not lowered or name.startswith(lowered):
            return (0, name)
        if lowered in name:
            return (1, name)
        return (2, name)

    skills = [
        {"name": s.name, "description": s.description or ""}
        for s in registry.list_skills()
        if lowered in s.name.lower() or lowered in (s.description or "").lower()
    ]
    skills.sort(key=_rank)
    return skills[:_MENTION_MAX_SKILLS]
