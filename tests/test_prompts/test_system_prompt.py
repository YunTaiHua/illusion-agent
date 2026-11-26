"""Tests for illusion.prompts.system_prompt."""

from __future__ import annotations

import os
from pathlib import Path

from illusion.prompts.environment import EnvironmentInfo
from illusion.prompts.system_prompt import build_system_prompt


def _make_env(**overrides) -> EnvironmentInfo:
    defaults = {
        "os_name": "Linux",
        "os_version": "5.15.0",
        "platform_machine": "x86_64",
        "shell": "bash",
        "cwd": "/home/user/project",
        "home_dir": "/home/user",
        "date": "2026-04-01",
        "python_version": "3.10.17",
        "is_git_repo": True,
        "git_branch": "main",
        "hostname": "testhost",
    }
    defaults.update(overrides)
    return EnvironmentInfo(**defaults)


def test_build_system_prompt_contains_environment():
    env = _make_env()
    prompt = build_system_prompt(env=env)
    assert "Linux 5.15.0" in prompt
    assert "x86_64" in prompt
    assert "bash" in prompt
    assert "/home/user/project" in prompt
    assert "2026-04-01" in prompt
    assert "3.10.17" in prompt
    assert "branch: main" in prompt


def test_prompt_tracks_os_home_and_optional_user_config_directory(tmp_path: Path, monkeypatch):
    profile = tmp_path / "user profile"
    monkeypatch.setenv("USERPROFILE" if os.name == "nt" else "HOME", str(profile))
    monkeypatch.delenv("ILLUSION_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "project"
    workspace.mkdir()

    home_line = f" - Home directory: {profile}"
    lines = build_system_prompt(cwd=str(workspace)).splitlines()
    assert home_line in lines
    assert not any("Illusion user config directory:" in line for line in lines)

    monkeypatch.setenv("ILLUSION_CONFIG_DIR", "custom config")
    lines = build_system_prompt(cwd=str(workspace)).splitlines()
    config_root = tmp_path / "custom config"
    assert home_line in lines
    assert f" - Illusion user config directory: {config_root}" in lines
    assert not config_root.exists()


def test_build_system_prompt_no_git():
    env = _make_env(is_git_repo=False, git_branch=None)
    prompt = build_system_prompt(env=env)
    assert "Git:" not in prompt


def test_build_system_prompt_git_no_branch():
    env = _make_env(is_git_repo=True, git_branch=None)
    prompt = build_system_prompt(env=env)
    assert "Git: yes" in prompt
    assert "branch:" not in prompt


def test_build_system_prompt_default_includes_base():
    env = _make_env()
    prompt = build_system_prompt(env=env)
    assert "IllusionAgent" in prompt
