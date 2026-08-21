"""web_request_file_stats 单元测试模块

验证单轮变更条的批量增删行数统计：
- _file_numstats 纯函数（Git 内 tracked/untracked/deleted/二进制、非 Git、工作区外）
- handle_web_request_file_stats 白名单过滤与会话缺失降级
- _collect_session_files display 统一绝对路径
"""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from illusion.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from illusion.ui.protocol import FrontendRequest
from illusion.ui.web.ws_web_api import (
    WebApiDispatcher,
    _collect_session_files,
    _file_numstats,
    _read_session_file_payload,
)


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


# ---- _file_numstats 纯函数 ----


class TestFileNumstats:
    """_file_numstats 批量行数统计测试"""

    @pytest.fixture
    def git_repo(self, tmp_path):
        """初始化一个带已提交文件的 Git 仓库"""
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "t@t")
        _git(tmp_path, "config", "user.name", "t")
        tracked = tmp_path / "tracked.py"
        tracked.write_text("a\nb\nc\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-qm", "init")
        return tmp_path

    def test_modified_tracked_file(self, git_repo):
        """已跟踪文件修改后返回 modified 与正确的增删行数"""
        (git_repo / "tracked.py").write_text("a\nX\nc\nd\n", encoding="utf-8")
        stats = _file_numstats(str(git_repo), [str(git_repo / "tracked.py")])
        assert len(stats) == 1
        entry = stats[0]
        assert entry["status"] == "modified"
        assert entry["insertions"] == 2
        assert entry["deletions"] == 1

    def test_untracked_new_file_counts_all_lines(self, git_repo):
        """未跟踪新文件返回 added 且 insertions 为总行数、deletions 为 0"""
        new_file = git_repo / "new.py"
        new_file.write_text("l1\nl2\nl3\n", encoding="utf-8")
        stats = _file_numstats(str(git_repo), [str(new_file)])
        assert stats[0]["status"] == "added"
        assert stats[0]["insertions"] == 3
        assert stats[0]["deletions"] == 0
        assert stats[0]["path"] == str(new_file)

    def test_untracked_binary_file_degrades_to_none(self, git_repo):
        """未跟踪的二进制文件 insertions 退化为 None"""
        (git_repo / "blob.bin").write_bytes(b"\x00\x01\x02")
        stats = _file_numstats(str(git_repo), [str(git_repo / "blob.bin")])
        assert stats[0]["status"] == "added"
        assert stats[0]["insertions"] is None
        assert stats[0]["deletions"] == 0

    def test_deleted_file_marked(self, git_repo):
        """文件不存在（被用户手动删除或会话中被删除）标记 deleted"""
        victim = git_repo / "victim.py"  # 从未创建：等价于已被删除
        stats = _file_numstats(str(git_repo), [str(victim)])
        assert stats[0]["status"] == "deleted"
        # 已删除的文件不覆盖为 added/modified
        assert stats[0]["insertions"] is None
        assert stats[0]["deletions"] is None

    def test_non_git_directory_all_none(self, tmp_path):
        """非 Git 目录下全部条目数值为 None"""
        f = tmp_path / "x.txt"
        f.write_text("hello\n", encoding="utf-8")
        stats = _file_numstats(str(tmp_path), [str(f)])
        entry = stats[0]
        assert entry["path"] == str(f)
        assert entry["status"] is None
        assert entry["insertions"] is None
        assert entry["deletions"] is None

    def test_outside_workspace_returns_none_stats(self, git_repo, tmp_path_factory):
        """Git 仓库内请求工作区外路径：不崩溃且无数值"""
        outside = tmp_path_factory.mktemp("outside") / "o.txt"
        outside.write_text("data\n", encoding="utf-8")
        stats = _file_numstats(str(git_repo), [str(outside)])
        assert stats[0]["status"] is None
        assert stats[0]["insertions"] is None

    def test_read_session_file_payload_deleted(self, tmp_path):
        """不存在的文件返回结构化 file_deleted 错误码而非裸 OSError"""
        payload = _read_session_file_payload(str(tmp_path / "gone.txt"))
        assert payload == {"path": str(tmp_path / "gone.txt"), "error": "file_deleted"}

    def test_read_session_file_payload_existing(self, tmp_path):
        """存在的文件走正常读取载荷（无 error 字段）"""
        f = tmp_path / "ok.txt"
        f.write_text("data", encoding="utf-8")
        payload = _read_session_file_payload(str(f))
        assert "error" not in payload
        assert payload.get("content") == "data"


# ---- _collect_session_files 绝对路径展示 ----


def _session_with_edit(tmp_path, file_rel: str, *, error: bool = False) -> MagicMock:
    """构造带一次 edit_file 调用转录的 mock 会话"""
    session = MagicMock()
    session.bundle.cwd = str(tmp_path)
    use = ToolUseBlock(name="edit_file", input={"file_path": file_rel})
    result = ToolResultBlock(tool_use_id=use.id, content="ok", is_error=error)
    session.engine.messages = [
        ConversationMessage(role="assistant", content=[use]),
        ConversationMessage(role="user", content=[result, TextBlock(text="done")]),
    ]
    return session


class TestCollectSessionFilesDisplay:
    """display 统一绝对路径测试"""

    def test_display_is_absolute_for_relative_input(self, tmp_path):
        """相对路径输入的 display 也为绝对路径（与单轮变更条样式统一）"""
        inside = tmp_path / "in.py"
        inside.write_text("a\n", encoding="utf-8")
        session = _session_with_edit(tmp_path, "in.py")
        files = _collect_session_files(session.engine.messages, str(tmp_path))
        assert len(files) == 1
        assert files[0]["path"] == str(inside)
        # display 统一 posix 风格绝对路径
        assert files[0]["display"] == Path(inside).as_posix()


# ---- handler 白名单过滤 ----


class TestWebRequestFileStats:
    """handle_web_request_file_stats 测试"""

    @pytest.fixture
    def dispatcher(self, tmp_path):
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._active_session_id = None
        host._sessions = {}
        return WebApiDispatcher(host)

    @pytest.fixture
    def git_dispatcher(self, tmp_path):
        """Git 仓库工作区 + 白名单内含未提交修改的 dispatcher。

        必须用 Git 仓库：非 Git 目录下 _file_numstats 天然产出全 None，
        会掩盖白名单脱敏失效（回归：数值曾泄漏给白名单外路径）。
        """
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "t@t")
        _git(tmp_path, "config", "user.name", "t")
        inside = tmp_path / "in.py"
        inside.write_text("a\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-qm", "init")
        inside.write_text("b\n", encoding="utf-8")  # 未提交修改 → numstat 有值
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._active_session_id = None
        host._sessions = {}
        return WebApiDispatcher(host), tmp_path

    @pytest.mark.asyncio
    async def test_filters_paths_not_in_session(self, git_dispatcher):
        """白名单外路径仅回显占位：无数值、不回显解析路径、不做存在性探测"""
        dispatcher, tmp_path = git_dispatcher
        inside = tmp_path / "in.py"
        secret = tmp_path / "secret.txt"
        secret.write_text("s\n", encoding="utf-8")  # 存在但不在会话白名单
        session = _session_with_edit(tmp_path, "in.py")
        dispatcher._host._sessions = {"s1": session}
        req = FrontendRequest(
            type="web_request_file_stats", session_id="s1",
            paths=[str(inside), str(secret)])
        await dispatcher.handle(req)
        event = dispatcher._host._emit.call_args.args[0]
        assert event.type == "web_file_stats"
        by_input = {e["input"]: e for e in event.web_file_stats}
        # 白名单内：真实统计数值
        assert by_input[str(inside)]["status"] == "modified"
        assert by_input[str(inside)]["insertions"] == 1
        assert by_input[str(inside)]["display"] == Path(inside).as_posix()
        # 白名单外：纯占位——无数值、path 置空（不泄漏解析结果）
        outside = by_input[str(secret)]
        assert outside["status"] is None
        assert outside["insertions"] is None
        assert outside["deletions"] is None
        assert outside["path"] == ""
        assert outside["display"] == str(secret)

    @pytest.mark.asyncio
    async def test_diff_absolute_path_echoes_requested_path(self, tmp_path):
        """web_file_diff 绝对路径请求：响应 path 回显请求原串（含分隔符形式）。

        前端以「kind|请求原串」精确关联响应，回显相对/posix 形式会被
        判为不匹配而丢弃，预览面板永久加载中（回归）。
        """
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "t@t")
        _git(tmp_path, "config", "user.name", "t")
        f = tmp_path / "a.py"
        f.write_text("a\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-qm", "init")
        f.write_text("b\n", encoding="utf-8")

        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._active_session_id = None
        host._sessions = {}
        disp = WebApiDispatcher(host)
        bundle = MagicMock()
        bundle.cwd = str(tmp_path)
        disp._resolve_resource_bundle = lambda req: bundle

        requested = str(f)  # Windows 反斜杠绝对路径原串
        await disp.handle(FrontendRequest(type="web_file_diff", path=requested))
        event = host._emit.call_args.args[0]
        assert event.type == "web_file_content"
        payload = event.web_file_content
        assert payload["path"] == requested  # 回显原串，key 关联命中
        assert payload.get("kind") == "diff"
        assert "diff --git" in (payload.get("content") or "")

    @pytest.mark.asyncio
    async def test_read_file_absolute_path_echoes_requested_path(self, tmp_path):
        """web_read_file 绝对路径请求：响应 path 回显请求原串（key 关联命中）"""
        f = tmp_path / "a.py"
        f.write_text("hello\n", encoding="utf-8")
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        host._active_session_id = None
        host._sessions = {}
        disp = WebApiDispatcher(host)
        bundle = MagicMock()
        bundle.cwd = str(tmp_path)
        disp._resolve_resource_bundle = lambda req: bundle

        requested = str(f)  # 反斜杠绝对路径原串
        await disp.handle(FrontendRequest(type="web_read_file", path=requested))
        event = host._emit.call_args.args[0]
        assert event.type == "web_file_content"
        payload = event.web_file_content
        assert payload["path"] == requested
        assert payload.get("error") is None
        assert "hello" in (payload.get("content") or "")

    @pytest.mark.asyncio
    async def test_missing_session_emits_placeholders(self, dispatcher):
        """会话不存在时逐条回显占位（非空数组）：前端按 input 清理 in-flight
        标记，空响应会让键永久滞留导致之后不再重试"""
        req = FrontendRequest(type="web_request_file_stats", paths=["a.py", "b.py"])
        await dispatcher.handle(req)
        event = dispatcher._host._emit.call_args.args[0]
        assert event.type == "web_file_stats"
        assert [e["input"] for e in event.web_file_stats] == ["a.py", "b.py"]
        assert all(e["status"] is None and e["path"] == "" for e in event.web_file_stats)

    @pytest.mark.asyncio
    async def test_stats_traversal_path_not_leaked(self, git_dispatcher):
        """.. 穿越路径请求统计：白名单判定拒绝，仅占位回显（防存在性探测）"""
        dispatcher, tmp_path = git_dispatcher
        import tempfile as _tf
        with _tf.TemporaryDirectory() as outer:
            secret = Path(outer) / "secret.txt"
            secret.write_text("top secret", encoding="utf-8")
            session = _session_with_edit(tmp_path, "in.py")
            dispatcher._host._sessions = {"s1": session}
            traversal = str(tmp_path / ".." / "secret.txt")
            await dispatcher.handle(FrontendRequest(
                type="web_request_file_stats", session_id="s1",
                paths=[traversal]))
            event = dispatcher._host._emit.call_args.args[0]
            entries = {e["input"]: e for e in event.web_file_stats}
            entry = entries[traversal]
            # 穿越 resolve 后落在工作区外 → 不在白名单 → 占位且无数值
            assert entry["status"] is None
            assert entry["path"] == ""
            assert entry["insertions"] is None

    @pytest.mark.asyncio
    async def test_diff_outside_path_error_echoes_requested(self, tmp_path):
        """出界绝对路径 diff：错误载荷同样回显请求原串（供前端 key 清理）"""
        host = MagicMock()
        host._emit = AsyncMock()
        host._bundle = MagicMock()
        host._bundle.cwd = str(tmp_path)
        disp = WebApiDispatcher(host)
        bundle = MagicMock()
        bundle.cwd = str(tmp_path)
        disp._resolve_resource_bundle = lambda req: bundle

        outside = tmp_path.parent / "elsewhere.txt"
        outside.write_text("s", encoding="utf-8")
        await disp.handle(FrontendRequest(type="web_file_diff", path=str(outside)))
        event = host._emit.call_args.args[0]
        payload = event.web_file_content
        assert payload["path"] == str(outside)
        assert payload.get("error")

    @pytest.mark.asyncio
    async def test_empty_paths_emits_empty(self, dispatcher, tmp_path):
        """paths 缺失/全空白时直接推送空列表，不做转录扫描"""
        session = _session_with_edit(tmp_path, "in.py")
        dispatcher._host._sessions = {"s1": session}
        req = FrontendRequest(type="web_request_file_stats", session_id="s1", paths=["   "])
        await dispatcher.handle(req)
        event = dispatcher._host._emit.call_args.args[0]
        assert event.web_file_stats == []
