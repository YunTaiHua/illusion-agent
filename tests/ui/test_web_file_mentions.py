"""@ 提及补全（web_request_file_mentions）单元测试

验证纯函数 _normalize_mention_query / _file_mention_candidates 的
规范化、过滤、排序与上限行为，以及 handler 的事件载荷与 request_id 回显。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from illusion.ui.file_mentions import (
    _MENTION_MAX_CANDIDATES,
    _MENTION_MAX_SKILLS,
    _skill_registry_cache,
)
from illusion.ui.protocol import BackendEvent, FrontendRequest
from illusion.ui.web.ws_web_api import (
    WebApiDispatcher,
    _file_mention_candidates,
    _normalize_mention_query,
    _skill_mention_candidates,
)


def _make_skill(workspace: Path, name: str, description: str) -> None:
    """在工作区 .illusion/skills 下写入受控技能夹具（SKILL.md 目录格式）。"""
    skill_dir = workspace / ".illusion" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def skill_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的技能测试工作区。

    - ILLUSION_CONFIG_DIR 指向临时目录：隔离用户级技能与插件/设置，
      保证 CI 与本地环境一致（仅 bundled 技能 + 项目级夹具）。
    - 清空模块级注册表缓存，避免跨用例串扰。
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    _skill_registry_cache.clear()
    return workspace


class TestNormalizeMentionQuery:
    """查询串规范化测试"""

    def test_none_becomes_empty(self):
        assert _normalize_mention_query(None) == ""

    def test_strip_whitespace(self):
        assert _normalize_mention_query("  src/a.py  ") == "src/a.py"

    def test_backslash_normalized(self):
        assert _normalize_mention_query("src\\a.py") == "src/a.py"

    def test_leading_dot_slash_stripped(self):
        assert _normalize_mention_query("./src/a.py") == "src/a.py"

    def test_leading_slash_stripped(self):
        assert _normalize_mention_query("/src/a.py") == "src/a.py"

    def test_trailing_slash_kept_for_dir_prefix(self):
        assert _normalize_mention_query("src/") == "src/"

    def test_empty_string(self):
        assert _normalize_mention_query("") == ""


class TestFileMentionCandidates:
    """候选收集测试"""

    @staticmethod
    def _make_tree(tmp_path: Path) -> Path:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x")
        (tmp_path / "src" / "util.py").write_text("x")
        (tmp_path / "README.md").write_text("x")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("x")
        (tmp_path / ".env").write_text("secret")
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "ci.yml").write_text("x")
        return tmp_path

    def test_empty_query_shallow_first_dirs_before_files(self, tmp_path: Path):
        self._make_tree(tmp_path)
        candidates, truncated = _file_mention_candidates(str(tmp_path), "")
        paths = [c["path"] for c in candidates]
        # 根层目录优先于根层文件，且都在子目录条目之前（BFS 浅层优先）
        assert paths.index(".github") < paths.index("README.md")
        assert all(not p.startswith("node_modules") for p in paths)
        assert truncated is False

    def test_substring_match_case_insensitive(self, tmp_path: Path):
        self._make_tree(tmp_path)
        candidates, _ = _file_mention_candidates(str(tmp_path), "README")
        assert [c["path"] for c in candidates] == ["README.md"]
        assert candidates[0]["kind"] == "file"

    def test_query_matches_directory_prefix(self, tmp_path: Path):
        self._make_tree(tmp_path)
        candidates, _ = _file_mention_candidates(str(tmp_path), "src/")
        paths = [c["path"] for c in candidates]
        assert "src/main.py" in paths and "src/util.py" in paths
        assert all(p.startswith("src/") for p in paths)

    def test_ignored_and_dot_entries_hidden(self, tmp_path: Path):
        self._make_tree(tmp_path)
        candidates, _ = _file_mention_candidates(str(tmp_path), "")
        paths = [c["path"] for c in candidates]
        assert not any(".env" in p or "node_modules" in p for p in paths)
        # 点目录白名单内可见
        assert ".github/ci.yml" in paths

    def test_no_escape_from_root(self, tmp_path: Path):
        self._make_tree(tmp_path)
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("x")
        candidates, _ = _file_mention_candidates(str(tmp_path), "../outside")
        assert candidates == []

    def test_truncation_at_limit(self, tmp_path: Path):
        for i in range(_MENTION_MAX_CANDIDATES + 10):
            (tmp_path / f"f{i:03d}.txt").write_text("x")
        candidates, truncated = _file_mention_candidates(str(tmp_path), "")
        assert len(candidates) == _MENTION_MAX_CANDIDATES
        assert truncated is True

    def test_missing_root_returns_empty(self, tmp_path: Path):
        candidates, truncated = _file_mention_candidates(str(tmp_path / "nope"), "")
        assert candidates == []
        assert truncated is False

    def test_depth_limit_skips_deeper_levels(self, tmp_path: Path):
        deep = tmp_path
        for i in range(20):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        (deep / "leaf.txt").write_text("x")
        candidates, _ = _file_mention_candidates(str(tmp_path), "leaf")
        assert candidates == []


class TestHandleWebRequestFileMentions:
    """handler 事件载荷测试"""

    @pytest.mark.asyncio
    async def test_emits_candidates_with_request_id_echo(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x")
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._active_bundle.return_value = host._bundle
        dispatcher = WebApiDispatcher(host)

        req = FrontendRequest(
            type="web_request_file_mentions", query="main", request_id="m1")
        await dispatcher.handle_web_request_file_mentions(req)

        host._emit.assert_called_once()
        evt: BackendEvent = host._emit.call_args.args[0]
        assert evt.type == "web_file_mentions"
        assert evt.request_id == "m1"
        assert evt.web_file_mentions is not None
        assert evt.web_file_mentions["query"] == "main"
        assert {"path": "src/main.py", "kind": "file"} in evt.web_file_mentions["candidates"]
        assert evt.web_file_mentions["truncated"] is False

    @pytest.mark.asyncio
    async def test_query_normalized_before_matching(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x")
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._active_bundle.return_value = host._bundle
        dispatcher = WebApiDispatcher(host)

        req = FrontendRequest(
            type="web_request_file_mentions", query=" ./src/ ", request_id="m2")
        await dispatcher.handle_web_request_file_mentions(req)

        evt: BackendEvent = host._emit.call_args.args[0]
        assert evt.web_file_mentions is not None
        assert evt.web_file_mentions["query"] == "src/"
        paths = [c["path"] for c in evt.web_file_mentions["candidates"]]
        assert "src/main.py" in paths


class TestSkillMentionCandidates:
    """技能提及候选测试（受控夹具：隔离配置目录 + 项目级技能）"""

    def test_query_filters_by_name(self, skill_workspace: Path):
        _make_skill(skill_workspace, "docx", "Word document toolkit")
        _make_skill(skill_workspace, "data-export", "导出数据为表格")
        skills = _skill_mention_candidates(str(skill_workspace), "docx")
        names = [s["name"] for s in skills]
        assert "docx" in names
        # 名称与描述都不含 docx 的不应入选
        assert all("docx" in n.lower() or "docx" in s["description"].lower()
                   for s, n in zip(skills, names))

    def test_empty_query_returns_bounded_list(self, skill_workspace: Path):
        for i in range(_MENTION_MAX_SKILLS + 4):
            _make_skill(skill_workspace, f"alpha-{i:02d}", f"description number {i}")
        skills = _skill_mention_candidates(str(skill_workspace), "")
        assert len(skills) <= _MENTION_MAX_SKILLS
        assert all(set(s) == {"name", "description"} for s in skills)

    def test_sorted_by_name(self, skill_workspace: Path):
        _make_skill(skill_workspace, "zeta-tool", "最后注册的技能")
        _make_skill(skill_workspace, "mid-tool", "中间的技能")
        names = [s["name"].lower() for s in _skill_mention_candidates(str(skill_workspace), "")]
        assert names == sorted(names)

    def test_no_match_returns_empty(self, skill_workspace: Path):
        assert _skill_mention_candidates(str(skill_workspace), "zzz-no-such-skill") == []


class TestSkillMentionRanking:
    """技能候选相关度排序测试（前缀命中优先于仅描述命中）"""

    def test_prefix_match_not_crowded_out_by_description_matches(self, skill_workspace: Path):
        # 回归：'r' 查询下描述含 r 的长尾技能曾把 requesting-code-review
        # 挤出上限窗口；名称不含 r 的填充项只靠描述命中（低层），
        # 前缀分层后前缀命中必须入选
        total = _MENTION_MAX_SKILLS * 2 + 2
        for i in range(total):
            _make_skill(skill_workspace, f"aa-pad-{i:02d}", "regular resource review notes")
        _make_skill(skill_workspace, "requesting-code-review", "ask for review before merge")
        skills = _skill_mention_candidates(str(skill_workspace), "r")
        names = [s["name"] for s in skills]
        assert "requesting-code-review" in names

    def test_name_prefix_tiers_before_description_only(self, skill_workspace: Path):
        _make_skill(skill_workspace, "aaa-desc-match", "requires careful planning")
        _make_skill(skill_workspace, "bbb-desc-match", "prerequisites checklist")
        _make_skill(skill_workspace, "requesting-code-review", "ask for review")
        skills = _skill_mention_candidates(str(skill_workspace), "requ")
        names = [s["name"] for s in skills]
        assert names[0] == "requesting-code-review"
