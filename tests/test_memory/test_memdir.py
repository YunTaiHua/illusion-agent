"""Tests for memory helpers."""

from __future__ import annotations

from pathlib import Path

from illusion.memory import (
    find_relevant_memories,
    get_memory_dir_for_cwd,
    get_memory_entrypoint,
    load_memory_prompt,
    resolve_custom_memory_dir,
)
from illusion.memory.memdir import truncate_entrypoint_content
from illusion.memory.scan import _parse_memory_file, scan_memory_files


def test_memory_paths_are_stable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    memory_dir = get_memory_dir_for_cwd(project_dir)
    entrypoint = get_memory_entrypoint(project_dir)

    assert memory_dir.exists()
    assert entrypoint.parent == memory_dir


def test_load_memory_prompt_includes_entrypoint(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    entrypoint = get_memory_entrypoint(project_dir)
    entrypoint.write_text("# Index\n- [Testing](testing.md) — testing hook\n", encoding="utf-8")

    prompt = load_memory_prompt(project_dir)

    assert prompt is not None
    assert "# auto memory" in prompt
    assert "## Types of memory" in prompt
    assert "## What NOT to save in memory" in prompt
    assert "Testing" in prompt


def test_memory_prompt_full_structure(tmp_path: Path, monkeypatch):
    """记忆提示词结构完整性检查。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    prompt = load_memory_prompt(project_dir)

    assert prompt is not None
    # 核心段落齐全
    for section in [
        "# auto memory",
        "## Types of memory",
        "<name>user</name>",
        "<name>feedback</name>",
        "<name>project</name>",
        "<name>reference</name>",
        "## What NOT to save in memory",
        "## How to save memories",
        "## When to access memories",
        "## Before recommending from memory",
        "## Memory and other forms of persistence",
        "## MEMORY.md",
    ]:
        assert section in prompt, f"missing section: {section}"


def test_find_relevant_memories(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    memory_dir = get_memory_dir_for_cwd(project_dir)
    (memory_dir / "pytest_tips.md").write_text("Pytest markers and fixtures\n", encoding="utf-8")
    (memory_dir / "docker_notes.md").write_text("Docker compose caveats\n", encoding="utf-8")

    matches = find_relevant_memories("fix pytest fixtures", project_dir)

    assert matches
    assert matches[0].path.name == "pytest_tips.md"


# --- Frontmatter parsing tests ---


def test_parse_frontmatter_extracts_fields(tmp_path: Path):
    path = tmp_path / "project_auth.md"
    path.write_text(
        "---\n"
        "name: auth-rewrite\n"
        "description: Auth middleware driven by compliance\n"
        "type: project\n"
        "---\n"
        "\n"
        "Session token storage rework for legal team.\n",
        encoding="utf-8",
    )

    header = _parse_memory_file(path, path.read_text(encoding="utf-8"))

    assert header.title == "auth-rewrite"
    assert header.description == "Auth middleware driven by compliance"
    assert header.memory_type == "project"
    assert "Session token storage" in header.body_preview


def test_parse_frontmatter_falls_back_without_frontmatter(tmp_path: Path):
    path = tmp_path / "quick_note.md"
    path.write_text("Redis cache invalidation strategy\n\nDetails here.\n", encoding="utf-8")

    header = _parse_memory_file(path, path.read_text(encoding="utf-8"))

    assert header.title == "quick_note"
    assert header.description == "Redis cache invalidation strategy"
    assert header.memory_type == ""
    # Description line must not be duplicated into body_preview.
    assert header.body_preview == "Details here."


def test_parse_malformed_frontmatter_does_not_return_delimiter(tmp_path: Path):
    """Unclosed frontmatter must not leak '---' into description."""
    path = tmp_path / "broken.md"
    path.write_text("---\nname: oops\nActual content here.\n", encoding="utf-8")

    header = _parse_memory_file(path, path.read_text(encoding="utf-8"))

    # The key invariant: description is never the raw delimiter.
    assert header.description != "---"
    assert header.description  # non-empty


def test_parse_frontmatter_skips_headings_for_description(tmp_path: Path):
    path = tmp_path / "notes.md"
    path.write_text("# My Heading\n\nActual description here.\n", encoding="utf-8")

    header = _parse_memory_file(path, path.read_text(encoding="utf-8"))

    assert header.description == "Actual description here."


def test_parse_frontmatter_handles_quoted_values(tmp_path: Path):
    path = tmp_path / "quoted.md"
    path.write_text(
        "---\nname: \"my-project\"\ndescription: 'A quoted desc'\ntype: feedback\n---\nBody.\n",
        encoding="utf-8",
    )

    header = _parse_memory_file(path, path.read_text(encoding="utf-8"))

    assert header.title == "my-project"
    assert header.description == "A quoted desc"
    assert header.memory_type == "feedback"


def test_scan_memory_files_with_frontmatter(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    memory_dir = get_memory_dir_for_cwd(project_dir)
    (memory_dir / "topic.md").write_text(
        "---\nname: my-topic\ndescription: Important topic\ntype: reference\n---\nContent.\n",
        encoding="utf-8",
    )

    headers = scan_memory_files(project_dir)

    assert len(headers) == 1
    assert headers[0].title == "my-topic"
    assert headers[0].description == "Important topic"
    assert headers[0].memory_type == "reference"


# --- Search relevance tests ---


def test_search_prefers_metadata_over_body(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    memory_dir = get_memory_dir_for_cwd(project_dir)

    # File A: "redis" appears in frontmatter description
    (memory_dir / "a_redis.md").write_text(
        "---\nname: cache-layer\ndescription: Redis caching strategy\n---\nGeneral notes.\n",
        encoding="utf-8",
    )
    # File B: "redis" appears only in body
    (memory_dir / "b_infra.md").write_text(
        "---\nname: infra-notes\ndescription: Infrastructure overview\n---\nWe use redis for sessions.\n",
        encoding="utf-8",
    )

    matches = find_relevant_memories("redis caching", project_dir)

    assert len(matches) == 2
    assert matches[0].title == "cache-layer"


def test_search_finds_body_content(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    memory_dir = get_memory_dir_for_cwd(project_dir)
    (memory_dir / "deploy.md").write_text(
        "---\nname: deploy\ndescription: Deployment notes\n---\nKubernetes rollout strategy details.\n",
        encoding="utf-8",
    )

    matches = find_relevant_memories("kubernetes rollout", project_dir)

    assert matches
    assert matches[0].title == "deploy"


def test_search_handles_cjk_queries(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    memory_dir = get_memory_dir_for_cwd(project_dir)
    (memory_dir / "chinese_note.md").write_text(
        "---\nname: meeting\ndescription: 项目会议纪要\n---\n讨论了部署计划。\n",
        encoding="utf-8",
    )

    matches = find_relevant_memories("会议", project_dir)

    assert matches
    assert matches[0].title == "meeting"


def test_get_project_memory_dir_under_config_dir(monkeypatch, tmp_path):
    """get_project_memory_dir 应位于 ~/.illusion/memory 下，而非 data/memory"""
    config_dir = tmp_path / "config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))

    from illusion.config.paths import get_config_dir
    from illusion.memory.paths import get_project_memory_dir

    mem_dir = get_project_memory_dir(str(tmp_path / "project"))

    expected_parent = get_config_dir() / "memory"
    assert mem_dir.parent == expected_parent, (
        f"memory 目录应位于 {expected_parent}，实际 {mem_dir.parent}"
    )
    assert "data" not in str(mem_dir), "memory 目录不应在 data 下"


def test_get_memory_dir_returns_config_memory(monkeypatch, tmp_path):
    """get_memory_dir 应返回 ~/.illusion/memory"""
    config_dir = tmp_path / "config"
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_dir))

    from illusion.config.paths import get_config_dir
    from illusion.memory.paths import get_memory_dir

    result = get_memory_dir()
    assert result == get_config_dir() / "memory"


# --- Custom memory directory tests ---


def test_resolve_custom_memory_dir_absolute(tmp_path: Path):
    target = tmp_path / "custom_mem"
    resolved = resolve_custom_memory_dir(str(target))
    assert resolved is not None
    assert resolved == target.resolve()


def test_resolve_custom_memory_dir_tilde(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    from pathlib import Path as _Path

    # 强制 Path.home() 使用环境变量（Windows 上 Path.home() 优先 USERPROFILE）
    home = _Path.home()
    resolved = resolve_custom_memory_dir("~/my-memory")
    assert resolved is not None
    assert resolved == home / "my-memory"


def test_resolve_custom_memory_dir_rejects_relative():
    assert resolve_custom_memory_dir("relative/path") is None
    assert resolve_custom_memory_dir(".") is None
    assert resolve_custom_memory_dir("..") is None


def test_resolve_custom_memory_dir_rejects_bare_tilde():
    assert resolve_custom_memory_dir("~") is None
    assert resolve_custom_memory_dir("~/") is None
    assert resolve_custom_memory_dir("~\\") is None


def test_resolve_custom_memory_dir_rejects_home_escape(tmp_path: Path, monkeypatch):
    """I2: ~/foo/..、~/../.. 等展开到 $HOME 或其祖先的路径应被拒绝。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    from pathlib import Path as _Path

    (tmp_path / "home").mkdir(exist_ok=True)

    # ~/foo/.. 展开后回到 $HOME → 拒绝
    assert resolve_custom_memory_dir("~/foo/..") is None
    # ~/../.. 展开后落在 $HOME 之上 → 拒绝
    assert resolve_custom_memory_dir("~/../..") is None
    # 正常子目录 → 允许
    resolved = resolve_custom_memory_dir("~/my-memory")
    assert resolved is not None
    assert resolved == _Path.home() / "my-memory"


def test_memory_dir_for_cwd_uses_custom_setting(tmp_path: Path, monkeypatch):
    """settings.memory.directory 设置后应覆盖默认目录。"""
    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    custom_dir = tmp_path / "custom_mem"
    custom_dir.mkdir(parents=True)

    # 写入 settings.json 配置 memory.directory
    import json

    from illusion.config.paths import get_config_file_path

    settings_path = get_config_file_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"memory": {"directory": str(custom_dir)}}), encoding="utf-8"
    )

    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    memory_dir = get_memory_dir_for_cwd(project_dir)
    entrypoint = get_memory_entrypoint(project_dir)

    assert memory_dir == custom_dir
    assert entrypoint.parent == custom_dir


# --- 类型子目录测试 ---


def test_memory_type_dir_created(tmp_path: Path, monkeypatch):
    """类型子目录应存在于记忆目录下。"""
    from illusion.memory.paths import MEMORY_TYPE_DIRS, get_memory_type_dir

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    memory_dir = get_memory_dir_for_cwd(project_dir)
    for t in MEMORY_TYPE_DIRS:
        type_dir = get_memory_type_dir(project_dir, t)
        assert type_dir == memory_dir / t
        assert type_dir.exists()


def test_scan_includes_type_subdirs(tmp_path: Path, monkeypatch):
    """scan 应同时扫描根目录和类型子目录中的记忆文件。"""
    from illusion.memory.paths import get_memory_type_dir
    from illusion.memory.scan import scan_memory_files

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    # 根目录（旧布局）
    (get_memory_dir_for_cwd(project_dir) / "legacy_note.md").write_text(
        "legacy note\n", encoding="utf-8"
    )
    # 类型子目录（新布局）
    (get_memory_type_dir(project_dir, "user") / "user_role.md").write_text(
        "role: engineer\n", encoding="utf-8"
    )
    (get_memory_type_dir(project_dir, "project") / "roadmap.md").write_text(
        "Q3 roadmap\n", encoding="utf-8"
    )

    headers = scan_memory_files(project_dir)
    names = {h.path.name for h in headers}
    assert names == {"legacy_note.md", "user_role.md", "roadmap.md"}


def test_manager_add_with_type_dir(tmp_path: Path, monkeypatch):
    """add_memory_entry 指定类型时应写入类型子目录且索引含前缀。"""
    from illusion.memory import add_memory_entry, get_memory_entrypoint
    from illusion.memory.paths import get_memory_type_dir

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    path = add_memory_entry(
        project_dir, "User Role", "role: engineer", memory_type="user"
    )
    assert path.parent == get_memory_type_dir(project_dir, "user")
    assert path.name == "user_role.md"

    entrypoint = get_memory_entrypoint(project_dir)
    content = entrypoint.read_text(encoding="utf-8")
    assert "user/user_role.md" in content  # 索引含类型子目录前缀


def test_manager_remove_from_type_dir(tmp_path: Path, monkeypatch):
    """remove_memory_entry 应能删除类型子目录中的文件。"""
    from illusion.memory import add_memory_entry, remove_memory_entry

    monkeypatch.setenv("ILLUSION_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    path = add_memory_entry(
        project_dir, "Feedback Style", "terse responses", memory_type="feedback"
    )
    assert path.exists()

    assert remove_memory_entry(project_dir, "feedback_style") is True
    assert not path.exists()


# --- Entrypoint truncation tests ---


def test_truncate_entrypoint_no_truncation():
    content = "line1\nline2\n"
    assert truncate_entrypoint_content(content) == "line1\nline2"


def test_truncate_entrypoint_line_limit():
    content = "\n".join(f"line-{i}" for i in range(210))
    result = truncate_entrypoint_content(content, max_lines=200)
    assert "WARNING" in result
    assert "lines (limit: 200)" in result
    assert "line-199" in result
    assert "line-200" not in result


def test_truncate_entrypoint_byte_limit():
    # 每条 300 字符 × 3 = 900 > 500
    lines = ["x" * 300 for _ in range(3)]
    content = "\n".join(lines)
    result = truncate_entrypoint_content(content, max_bytes=500)
    assert "WARNING" in result
    assert "bytes (limit: 500)" in result
