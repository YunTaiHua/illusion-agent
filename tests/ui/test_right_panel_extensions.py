"""右栏扩展功能单元测试模块

覆盖 ws_web_api 中新增的纯函数辅助：
- 文件树过滤/路径边界校验/目录列举（_tree_entry_visible/_resolve_within_root/_list_dir_entries）
- Git porcelain/numstat 解析与状态快照（_parse_git_porcelain/_parse_git_numstat/_git_status_snapshot）
- 单文件 diff（_git_file_diff）
- 文件预览载荷（_read_file_payload）
- 资源快照中的 agents 收集（_collect_resources）
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from illusion.ui.web.ws_web_api import (
    _collect_agent_tasks,
    _collect_resources,
    _git_file_diff,
    _git_status_snapshot,
    _list_dir_entries,
    _parse_git_numstat,
    _parse_git_porcelain,
    _read_file_payload,
    _resolve_within_root,
    _tree_entry_visible,
)

# ---------------------------------------------------------------------------
# 文件树
# ---------------------------------------------------------------------------


class TestTreeEntryVisible:
    """_tree_entry_visible 过滤规则测试"""

    def test_ignored_names_hidden(self):
        """忽略名单（node_modules/.git 等）一律隐藏"""
        assert _tree_entry_visible("node_modules", True) is False
        assert _tree_entry_visible(".git", True) is False
        assert _tree_entry_visible("__pycache__", True) is False
        assert _tree_entry_visible("dist", False) is False

    def test_dotfiles_hidden_except_allowlist_dirs(self):
        """点文件默认隐藏；白名单目录（.github 等）可见"""
        assert _tree_entry_visible(".env", False) is False
        assert _tree_entry_visible(".github", True) is True
        assert _tree_entry_visible(".vscode", True) is True
        assert _tree_entry_visible(".hiddendir", True) is False

    def test_normal_entries_visible(self):
        """普通文件/目录可见"""
        assert _tree_entry_visible("src", True) is True
        assert _tree_entry_visible("main.py", False) is True


class TestResolveWithinRoot:
    """_resolve_within_root 路径边界校验测试"""

    def test_root_and_subpath(self, tmp_path: Path):
        """空 rel 返回根；子路径正常解析"""
        assert _resolve_within_root(str(tmp_path), "") == tmp_path.resolve()
        target = _resolve_within_root(str(tmp_path), "src/app/main.py")
        assert target == (tmp_path / "src" / "app" / "main.py").resolve()

    def test_traversal_rejected(self, tmp_path: Path):
        """../ 逃逸工作区返回 None"""
        assert _resolve_within_root(str(tmp_path), "../outside.txt") is None
        assert _resolve_within_root(str(tmp_path), "src/../../..") is None

    def test_absolute_path_rejected(self, tmp_path: Path):
        """绝对路径不落在 root 下时返回 None"""
        outside = tmp_path.parent / "outside-root"
        assert _resolve_within_root(str(tmp_path), str(outside)) is None

    def test_backslash_normalized(self, tmp_path: Path):
        """反斜杠分隔的相对路径在 Windows 上按分隔符归一化（POSIX 中 \\ 是合法文件名字符）"""
        target = _resolve_within_root(str(tmp_path), "src\\app")
        if os.name == "nt":
            assert target == (tmp_path / "src" / "app").resolve()
        else:
            assert target == (tmp_path / "src\\app").resolve()


class TestListDirEntries:
    """_list_dir_entries 目录列举测试"""

    def test_filter_and_sort(self, tmp_path: Path):
        """过滤隐藏项；目录优先、名称不区分大小写排序；path 为相对路径"""
        (tmp_path / "zebra.py").write_text("z")
        (tmp_path / "Apple.tsx").write_text("a")
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / ".env").write_text("x")
        entries, truncated = _list_dir_entries(tmp_path, str(tmp_path))
        names = [e["name"] for e in entries]
        assert names == ["src", "Apple.tsx", "zebra.py"]
        assert truncated is False
        assert entries[0]["kind"] == "dir"
        assert entries[1]["kind"] == "file"
        assert entries[1]["size"] == 1

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        """目录不存在时返回空列表而非抛异常"""
        entries, truncated = _list_dir_entries(tmp_path / "nope", str(tmp_path))
        assert entries == []
        assert truncated is False


# ---------------------------------------------------------------------------
# Git 解析
# ---------------------------------------------------------------------------


class TestParseGitPorcelain:
    """_parse_git_porcelain 解析测试"""

    def test_plain_modified(self):
        """普通修改条目：XY + 空格 + 路径"""
        raw = " M src/app.py\0"
        files = _parse_git_porcelain(raw)
        assert len(files) == 1
        assert files[0]["path"] == "src/app.py"
        assert files[0]["status"] == "modified"
        assert files[0]["staged"] is False

    def test_staged_added(self):
        """暂存新增：X=A 视为 staged"""
        files = _parse_git_porcelain("A  new_file.py\0")
        assert files[0]["status"] == "added"
        assert files[0]["staged"] is True

    def test_untracked(self):
        """未跟踪条目：?? 前缀"""
        files = _parse_git_porcelain("?? untracked dir/file.txt\0")
        assert files[0]["status"] == "untracked"
        assert files[0]["staged"] is False

    def test_rename_followed_by_orig(self):
        """重命名条目后跟原始路径记录（-z 格式）"""
        raw = "R  renamed.py\0original.py\0"
        files = _parse_git_porcelain(raw)
        assert len(files) == 1
        assert files[0]["path"] == "renamed.py"
        assert files[0]["orig_path"] == "original.py"
        assert files[0]["status"] == "renamed"

    def test_path_with_spaces(self):
        """-z 模式路径含空格不转义，原样解析"""
        files = _parse_git_porcelain("M  my file with spaces.py\0")
        assert files[0]["path"] == "my file with spaces.py"


class TestParseGitNumstat:
    """_parse_git_numstat 解析测试"""

    def test_normal_and_binary(self):
        """普通条目解析为整数；二进制（-）解析为 None"""
        raw = "12\t3\tsrc/app.py\0-\t-\tlogo.png\0"
        stats = _parse_git_numstat(raw)
        assert stats["src/app.py"] == (12, 3)
        assert stats["logo.png"] == (None, None)

    def test_garbage_skipped(self):
        """无制表符结构的字段（如重命名附带的原始路径）跳过"""
        stats = _parse_git_numstat("1\t1\ta.py\0original.py\0")
        assert stats["a.py"] == (1, 1)
        assert "original.py" not in stats


class TestGitStatusSnapshot:
    """_git_status_snapshot 集成测试（真实临时仓库）"""

    def test_non_repo(self, tmp_path: Path):
        """非 Git 目录返回 is_repo=False"""
        assert _git_status_snapshot(str(tmp_path)) == {"is_repo": False}

    def test_real_repo(self, tmp_path: Path):
        """真实仓库：分支/变更/统计（git 不可用时跳过）"""
        try:
            subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            import pytest

            pytest.skip("git 不可用")
        (tmp_path / "base.txt").write_text("line\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
            cwd=tmp_path, check=True, capture_output=True, timeout=10,
        )
        # 工作区修改 + 新增未跟踪文件
        (tmp_path / "base.txt").write_text("line\nline2\nline3\n")
        (tmp_path / "new.txt").write_text("x")

        snap = _git_status_snapshot(str(tmp_path))
        assert snap["is_repo"] is True
        assert snap["branch"] in ("master", "main")
        by_path = {f["path"]: f for f in snap["files"]}
        assert by_path["base.txt"]["status"] == "modified"
        assert by_path["base.txt"]["insertions"] == 2
        assert by_path["base.txt"]["deletions"] == 0
        assert by_path["new.txt"]["status"] == "untracked"


# ---------------------------------------------------------------------------
# 单文件 diff
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """构造带一次提交的临时 Git 仓库（git 不可用时跳过）"""
    try:
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True, capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git 不可用")
    (tmp_path / "base.txt").write_text("line1\nline2\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, timeout=10)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, timeout=10)
    return tmp_path


class TestGitFileDiff:
    """_git_file_diff 单文件 diff 测试"""

    def test_modified_tracked(self, git_repo: Path):
        """跟踪文件修改：diff 含删除旧行与新增新行"""
        (git_repo / "base.txt").write_text("line1\nline2-changed\n", encoding="utf-8", newline="\n")
        payload = _git_file_diff(str(git_repo), "base.txt")
        assert payload["kind"] == "diff"
        assert "-line2" in payload["content"]
        assert "+line2-changed" in payload["content"]

    def test_untracked_synthesized(self, git_repo: Path):
        """未跟踪文件：--no-index 合成全新增 diff，头部规整为 a/dev/null"""
        (git_repo / "new.txt").write_text("hello\n", encoding="utf-8", newline="\n")
        payload = _git_file_diff(str(git_repo), "new.txt")
        assert payload["kind"] == "diff"
        assert "+hello" in payload["content"]
        assert f"a/{_devnull_name()}" not in payload["content"]

    def test_deleted_file_diff(self, git_repo: Path):
        """已删除文件：diff 仍可获取（文件不存在不阻塞）"""
        (git_repo / "base.txt").unlink()
        payload = _git_file_diff(str(git_repo), "base.txt")
        assert payload["kind"] == "diff"
        assert "-line1" in payload["content"]

    def test_clean_file_empty_diff(self, git_repo: Path):
        """无变更的提交文件：成功但 diff 为空（前端显示暂无变更）"""
        payload = _git_file_diff(str(git_repo), "base.txt")
        assert payload["kind"] == "diff"
        assert payload["content"] == ""

    def test_non_repo_error(self, tmp_path: Path):
        """非 Git 仓库：返回 error 载荷而非抛异常"""
        payload = _git_file_diff(str(tmp_path), "x.txt")
        assert payload["kind"] == "diff"
        assert "error" in payload


def _devnull_name() -> str:
    import os

    return os.devnull


# ---------------------------------------------------------------------------
# 智能体与任务（复用 /agent 双数据源）
# ---------------------------------------------------------------------------


class TestCollectAgentTasks:
    """_collect_agent_tasks 测试（前台 agent + 后台 task-notification）"""

    @staticmethod
    def _messages(blocks_by_role: list[tuple[str, list]]) -> list:
        """构造 ConversationMessage 列表：[(role, [blocks]), ...]"""
        from illusion.engine.messages import ConversationMessage

        return [ConversationMessage(role=role, content=blocks) for role, blocks in blocks_by_role]

    def test_front_agent_result(self):
        """前台 agent：ToolUse + 对应 tool_result → type=agent，取 description 标题"""
        from illusion.engine.messages import ToolResultBlock, ToolUseBlock

        messages = self._messages([
            ("assistant", [ToolUseBlock(id="toolu_1", name="agent", input={"description": "调研配置加载"})]),
            ("user", [ToolResultBlock(tool_use_id="toolu_1", content="调研完成：共 3 处加载点")]),
        ])
        items = _collect_agent_tasks(messages)
        assert len(items) == 1
        assert items[0]["id"] == "toolu_1"
        assert items[0]["type"] == "agent"
        assert items[0]["title"] == "调研配置加载"
        assert items[0]["status"] == "completed"
        assert "调研完成" in items[0]["summary"]

    def test_front_agent_launch_notice_skipped(self):
        """前台 agent 的后台启动通知（非摘要）被跳过（与 /agent 过滤一致）"""
        from illusion.engine.messages import ToolResultBlock, ToolUseBlock

        messages = self._messages([
            ("assistant", [ToolUseBlock(id="toolu_2", name="agent", input={"description": "后台任务"})]),
            ("user", [ToolResultBlock(tool_use_id="toolu_2", content="Agent launched in background as task agent-1")]),
        ])
        assert _collect_agent_tasks(messages) == []

    def test_task_notification_types(self):
        """后台通知：agent 前缀 → 智能体；bash → 任务；最近的在最前"""
        from illusion.engine.messages import TextBlock

        def notification(task_id: str, name: str, status: str = "completed") -> str:
            return (
                f"<task-notification>\n<task-id>{task_id}</task-id>\n"
                f"<status>{status}</status>\n<summary>做点事 {task_id}</summary>\n"
                f"<task-name>{name}</task-name>\n<result>done</result>\n</task-notification>"
            )

        messages = self._messages([
            ("user", [TextBlock(text=notification("agent-111", "agent", "completed"))]),
            ("user", [TextBlock(text=notification("bash-222", "bash", "failed"))]),
        ])
        items = _collect_agent_tasks(messages)
        assert len(items) == 2
        # 倒排：最近（bash-222）在最前
        assert items[0]["id"] == "bash-222"
        assert items[0]["type"] == "task"
        assert items[0]["status"] == "failed"
        assert items[1]["id"] == "agent-111"
        assert items[1]["type"] == "agent"

    def test_empty(self):
        """空会话返回空列表"""
        assert _collect_agent_tasks([]) == []


# ---------------------------------------------------------------------------
# 文件预览
# ---------------------------------------------------------------------------


class TestReadFilePayload:
    """_read_file_payload 预览载荷测试"""

    def test_text_file(self, tmp_path: Path):
        """文本文件返回内容与大小"""
        target = tmp_path / "a.txt"
        target.write_text("hello\nworld", encoding="utf-8", newline="\n")
        payload = _read_file_payload(target, "a.txt")
        assert payload["binary"] is False
        assert payload["content"] == "hello\nworld"
        assert payload["size"] == 11
        assert payload["truncated"] is False

    def test_binary_file(self, tmp_path: Path):
        """前 8KB 含 NUL 判定二进制，不返回内容"""
        target = tmp_path / "b.bin"
        target.write_bytes(b"ab\x00cd")
        payload = _read_file_payload(target, "b.bin")
        assert payload["binary"] is True
        assert payload["content"] == ""

    def test_truncation_by_lines(self, tmp_path: Path):
        """超过行数上限截断并标记"""
        target = tmp_path / "big.txt"
        target.write_text("\n".join(str(i) for i in range(5000)), encoding="utf-8")
        payload = _read_file_payload(target, "big.txt")
        assert payload["truncated"] is True
        assert payload["content"].count("\n") == 3999

    def test_missing_file_error(self, tmp_path: Path):
        """文件消失时返回 error 载荷而非抛异常"""
        payload = _read_file_payload(tmp_path / "gone.txt", "gone.txt")
        assert "error" in payload


# ---------------------------------------------------------------------------
# 资源快照（agents）
# ---------------------------------------------------------------------------


class TestCollectResourcesAgents:
    """_collect_resources 的 agents 收集测试"""

    def test_agents_included(self, tmp_path: Path):
        """快照包含 agents 键且内置代理可见（builtin 来源）"""
        bundle = MagicMock()
        bundle.cwd = str(tmp_path)
        bundle.current_plugins = MagicMock(return_value=[])
        bundle.mcp_manager.list_statuses = MagicMock(return_value=[])
        resources = _collect_resources(bundle)
        assert "agents" in resources
        names = {a["name"] for a in resources["agents"]}
        assert "general-purpose" in names
        assert "explore" in names
        for agent in resources["agents"]:
            assert agent["source"] in ("builtin", "user", "project", "plugin")
            assert isinstance(agent["description"], str)
