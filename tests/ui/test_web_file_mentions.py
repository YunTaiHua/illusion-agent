"""@ 提及补全（web_request_file_mentions）单元测试

验证纯函数 _normalize_mention_query / _file_mention_candidates 的
规范化、过滤、排序与上限行为，以及 handler 的事件载荷与 request_id 回显。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from illusion.ui.protocol import BackendEvent, FrontendRequest
from illusion.ui.web.ws_web_api import (
    _MENTION_MAX_CANDIDATES,
    WebApiDispatcher,
    _file_mention_candidates,
    _normalize_mention_query,
)


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
