"""Tests for skill loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from illusion.skills import get_user_skills_dir, load_skill_registry
from illusion.skills.loader import (
    get_project_skills_dir,
    load_project_skills,
    load_user_skills,
)


def test_load_skill_registry_includes_bundled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    registry = load_skill_registry()

    names = [skill.name for skill in registry.list_skills()]
    assert "update-config" in names
    assert "illusion-print-mode" in names
    assert "remember" in names
    assert "skillify" in names


def test_load_skill_registry_includes_user_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "deploy.md").write_text("# Deploy\nDeployment workflow guidance\n", encoding="utf-8")

    registry = load_skill_registry()
    deploy = registry.get("Deploy")

    assert deploy is not None
    assert deploy.source == "user"
    assert "Deployment workflow guidance" in deploy.content


def test_load_project_skills_from_subdirectory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # 创建项目级 skill 子目录结构: .illusion/skills/deploy/deploy.md
    skills_dir = get_project_skills_dir(project_dir)
    skill_subdir = skills_dir / "deploy"
    skill_subdir.mkdir(parents=True)
    (skill_subdir / "deploy.md").write_text(
        "---\nname: deploy\ndescription: Project deploy skill\n---\n\n# Deploy\n\nDeploy steps.\n",
        encoding="utf-8",
    )

    skills = load_project_skills(project_dir)
    assert len(skills) == 1
    assert skills[0].name == "deploy"
    assert skills[0].source == "project"


def test_load_skill_registry_includes_project_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    skills_dir = get_project_skills_dir(project_dir)
    skill_subdir = skills_dir / "my-skill"
    skill_subdir.mkdir(parents=True)
    (skill_subdir / "my-skill.md").write_text(
        "---\nname: my-skill\ndescription: A project skill\n---\n\n# My Skill\n\nContent.\n",
        encoding="utf-8",
    )

    registry = load_skill_registry(project_dir)
    skill = registry.get("my-skill")

    assert skill is not None
    assert skill.source == "project"


def test_project_skill_overrides_global_skill(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # 创建全局 skill
    user_skills_dir = get_user_skills_dir()
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    (user_skills_dir / "deploy.md").write_text(
        "# Deploy\nGlobal deploy skill.\n",
        encoding="utf-8",
    )

    # 创建同名项目级 skill（子目录结构）
    skills_dir = get_project_skills_dir(project_dir)
    skill_subdir = skills_dir / "deploy"
    skill_subdir.mkdir(parents=True)
    (skill_subdir / "deploy.md").write_text(
        "---\nname: deploy\ndescription: Project deploy skill\n---\n\n# Deploy\n\nProject deploy.\n",
        encoding="utf-8",
    )

    registry = load_skill_registry(project_dir)
    skill = registry.get("deploy")

    assert skill is not None
    assert skill.source == "project"
    assert "Project deploy" in skill.content


def test_load_user_skills_from_skill_md_directory(tmp_path: Path, monkeypatch):
    """测试用户级 SKILL.md 目录格式加载"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_subdir = skills_dir / "my-skill"
    skill_subdir.mkdir(parents=True)
    (skill_subdir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A user skill\n---\n\n# My Skill\n\nContent.\n",
        encoding="utf-8",
    )

    registry = load_skill_registry()
    skill = registry.get("my-skill")

    assert skill is not None
    assert skill.source == "user"
    assert skill.skill_root == str(skill_subdir)


def _create_symlink(link: Path, target: Path, target_is_directory: bool) -> bool:
    """创建符号链接；Windows 无符号链接权限时返回 False（调用方跳过测试）。"""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
        return True
    except (OSError, NotImplementedError):
        return False


def test_load_user_skills_skips_broken_symlink(tmp_path: Path, monkeypatch):
    """失效符号链接不中断技能加载（WinError 5 修复回归）"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    (skills_dir / "good").mkdir(parents=True)
    (skills_dir / "good" / "SKILL.md").write_text(
        "---\nname: good\ndescription: Good skill\n---\n",
        encoding="utf-8",
    )
    if not _create_symlink(skills_dir / "broken", skills_dir / "not-exists", True):
        pytest.skip("无符号链接创建权限")

    skills = load_user_skills()
    names = [s.name for s in skills]
    assert names == ["good"]


def test_load_user_skills_dereferenced_repo_copy(tmp_path: Path, monkeypatch):
    """整仓拷贝（软链解引用为真实目录）时同名技能只加载一次

    对应 academic-research-skills 场景：仓库根级技能目录与 skills/ 目录
    双份声明同一技能，且仓库根散落大量 README 文档。
    """
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    repo = skills_dir / "some-repo"
    # 根级技能包 + skills/ 下的同名副本（模拟解引用后的软链）
    (repo / "alpha").mkdir(parents=True)
    (repo / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n", encoding="utf-8"
    )
    (repo / "skills" / "alpha").mkdir(parents=True)
    (repo / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha duplicate\n---\n", encoding="utf-8"
    )
    # 仓库根散落文档：不应被加载为技能
    (repo / "README.md").write_text("# Some Repo\nDocs docs.\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n1.0.0\n", encoding="utf-8")

    skills = load_user_skills()
    names = [s.name for s in skills]
    assert names == ["alpha"]
    assert skills[0].description == "Alpha skill"  # 先加载者（根级）生效


def test_load_user_skills_nested_skill_md_directory(tmp_path: Path, monkeypatch):
    """无 SKILL.md 的目录递归查找嵌套技能包（整仓拷贝布局）"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    repo = skills_dir / "repo"
    (repo / "skills" / "beta").mkdir(parents=True)
    (repo / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Nested skill\n---\n", encoding="utf-8"
    )
    # 同仓库内散落文档不加载（存在嵌套技能包时）
    (repo / "NOTICE.md").write_text("Notice text\n", encoding="utf-8")

    skills = load_user_skills()
    assert [s.name for s in skills] == ["beta"]


def test_load_user_skills_loose_md_fallback_preserved(tmp_path: Path, monkeypatch):
    """兼容旧格式：目录内无嵌套技能包时，散落 .md 仍作为技能加载"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    subdir = skills_dir / "commands-dir"
    subdir.mkdir(parents=True)
    (subdir / "deploy.md").write_text(
        "---\nname: deploy\ndescription: Loose command\n---\n", encoding="utf-8"
    )

    skills = load_user_skills()
    assert [s.name for s in skills] == ["deploy"]


def test_load_user_skills_symlinked_skill_directory(tmp_path: Path, monkeypatch):
    """目录型符号链接指向技能包时可正常加载"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    real = skills_dir / "real-skill"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text(
        "---\nname: linked\ndescription: Linked skill\n---\n", encoding="utf-8"
    )
    if not _create_symlink(skills_dir / "alias-skill", real, True):
        pytest.skip("无符号链接创建权限")

    skills = load_user_skills()
    assert [s.name for s in skills] == ["linked"]
