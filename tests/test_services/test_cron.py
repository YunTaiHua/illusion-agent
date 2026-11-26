"""Cron 注册表单元测试。

测试 cron 数据模型后的注册表功能：
- 验证、CRUD、切换、标记执行、损坏数据处理
- 新增：ID 自动生成、连续错误跟踪、一次性任务删除
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from illusion.services.cron import (
    delete_cron_job,
    get_cron_job,
    get_cron_job_by_name,
    load_cron_jobs,
    mark_job_run,
    next_run_time,
    remove_expired_jobs,
    set_job_enabled,
    upsert_cron_job,
    validate_cron_expression,
)


@pytest.fixture(autouse=True)
def _tmp_cron_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """将 cron 注册表重定向到临时目录。"""
    data_dir = tmp_path / "data"
    cron_dir = data_dir / "cron"
    data_dir.mkdir()
    cron_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "illusion.services.cron.get_cron_registry_path",
        lambda: cron_dir / "jobs.json",
    )


class TestValidation:
    """cron 表达式验证测试。"""

    def test_valid_expressions(self) -> None:
        assert validate_cron_expression("* * * * *")
        assert validate_cron_expression("*/5 * * * *")
        assert validate_cron_expression("0 9 * * 1-5")
        assert validate_cron_expression("0 0 1 1 *")

    def test_invalid_expressions(self) -> None:
        assert not validate_cron_expression("")
        assert not validate_cron_expression("every 5 minutes")
        assert not validate_cron_expression("60 * * * *")
        assert not validate_cron_expression("* * * *")  # 只有 4 个字段

    def test_next_run_time(self) -> None:
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        nxt = next_run_time("0 * * * *", base)
        assert nxt == datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)


class TestCRUD:
    """任务增删改查测试。"""

    def test_empty_load(self) -> None:
        assert load_cron_jobs() == []

    def test_upsert_and_load(self) -> None:
        job_id = upsert_cron_job({
            "name": "test-job",
            "schedule": "*/5 * * * *",
            "prompt": "echo hi",
        })
        assert job_id  # 应返回非空 ID
        jobs = load_cron_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "test-job"
        assert jobs[0]["enabled"] is True
        assert "next_run" in jobs[0]
        assert "created_at" in jobs[0]
        assert "id" in jobs[0]

    def test_upsert_replaces(self) -> None:
        upsert_cron_job({"name": "j1", "schedule": "* * * * *", "prompt": "echo 1"})
        upsert_cron_job({"name": "j1", "schedule": "0 * * * *", "prompt": "echo 2"})
        jobs = load_cron_jobs()
        assert len(jobs) == 1
        assert jobs[0]["prompt"] == "echo 2"

    def test_delete_by_name(self) -> None:
        upsert_cron_job({"name": "j1", "schedule": "* * * * *", "prompt": "echo 1"})
        assert delete_cron_job("j1") is True
        assert load_cron_jobs() == []

    def test_delete_by_id(self) -> None:
        job_id = upsert_cron_job({"name": "j1", "schedule": "* * * * *", "prompt": "echo 1"})
        assert delete_cron_job(job_id) is True
        assert load_cron_jobs() == []

    def test_delete_missing(self) -> None:
        assert delete_cron_job("nope") is False

    def test_get_job_by_id(self) -> None:
        job_id = upsert_cron_job({"name": "j1", "schedule": "* * * * *", "prompt": "echo 1"})
        job = get_cron_job(job_id)
        assert job is not None
        assert job["name"] == "j1"

    def test_get_job_by_name(self) -> None:
        upsert_cron_job({"name": "j1", "schedule": "* * * * *", "prompt": "echo 1"})
        job = get_cron_job_by_name("j1")
        assert job is not None
        assert job["name"] == "j1"

    def test_get_missing(self) -> None:
        assert get_cron_job("nope") is None

    def test_sorted_output(self) -> None:
        upsert_cron_job({"name": "z-job", "schedule": "* * * * *", "prompt": "z"})
        upsert_cron_job({"name": "a-job", "schedule": "* * * * *", "prompt": "a"})
        jobs = load_cron_jobs()
        assert [j["name"] for j in jobs] == ["a-job", "z-job"]

    def test_auto_id_generation(self) -> None:
        """未提供 id 时应自动生成。"""
        upsert_cron_job({"schedule": "* * * * *", "prompt": "test"})
        jobs = load_cron_jobs()
        assert len(jobs) == 1
        assert jobs[0]["id"]  # 自动生成的 ID 不为空


class TestToggle:
    """启用/禁用切换测试。"""

    def test_enable_disable(self) -> None:
        upsert_cron_job({"name": "j1", "schedule": "* * * * *", "prompt": "echo 1"})
        assert set_job_enabled("j1", False) is True
        job = get_cron_job("j1")
        assert job is not None
        assert job["enabled"] is False

        assert set_job_enabled("j1", True) is True
        job = get_cron_job("j1")
        assert job is not None
        assert job["enabled"] is True

    def test_toggle_by_id(self) -> None:
        """按 ID 切换启用状态。"""
        job_id = upsert_cron_job({"name": "j1", "schedule": "* * * * *", "prompt": "echo 1"})
        assert set_job_enabled(job_id, False) is True
        job = get_cron_job(job_id)
        assert job is not None
        assert job["enabled"] is False

    def test_toggle_missing(self) -> None:
        assert set_job_enabled("nope", True) is False


class TestMarkRun:
    """执行结果标记测试。"""

    def test_mark_success(self) -> None:
        upsert_cron_job({"name": "j1", "schedule": "*/5 * * * *", "prompt": "echo ok"})
        mark_job_run("j1", success=True)
        job = get_cron_job("j1")
        assert job is not None
        assert job["last_status"] == "success"
        assert "last_run" in job
        assert job["consecutive_errors"] == 0

    def test_mark_failure(self) -> None:
        upsert_cron_job({"name": "j1", "schedule": "*/5 * * * *", "prompt": "false"})
        mark_job_run("j1", success=False)
        job = get_cron_job("j1")
        assert job is not None
        assert job["last_status"] == "failed"
        assert job["consecutive_errors"] == 1

    def test_consecutive_errors_increment(self) -> None:
        """连续失败应递增错误计数。"""
        upsert_cron_job({"name": "j1", "schedule": "*/5 * * * *", "prompt": "fail"})
        mark_job_run("j1", success=False)
        mark_job_run("j1", success=False)
        mark_job_run("j1", success=False)
        job = get_cron_job("j1")
        assert job is not None
        assert job["consecutive_errors"] == 3

    def test_success_resets_consecutive_errors(self) -> None:
        """成功执行应重置连续错误计数。"""
        upsert_cron_job({"name": "j1", "schedule": "*/5 * * * *", "prompt": "test"})
        mark_job_run("j1", success=False)
        mark_job_run("j1", success=False)
        mark_job_run("j1", success=True)
        job = get_cron_job("j1")
        assert job is not None
        assert job["consecutive_errors"] == 0

    def test_one_shot_disabled_after_run(self) -> None:
        """一次性任务执行后应自动禁用。"""
        upsert_cron_job({
            "name": "reminder",
            "schedule": "0 9 * * *",
            "prompt": "提醒",
            "recurring": False,
        })
        mark_job_run("reminder", success=True)
        job = get_cron_job("reminder")
        assert job is not None
        assert job["enabled"] is False

    def test_mark_missing_is_noop(self) -> None:
        # 不应抛出异常
        mark_job_run("nope", success=True)


class TestDeleteAfterRun:
    """delete_after_run 功能测试。"""

    def test_pending_delete_flag(self) -> None:
        """一次性任务执行后应标记为待删除。"""
        upsert_cron_job({
            "name": "one-shot",
            "schedule": "0 9 * * *",
            "prompt": "test",
            "recurring": False,
            "delete_after_run": True,
        })
        mark_job_run("one-shot", success=True)
        job = get_cron_job("one-shot")
        assert job is not None
        assert job.get("_pending_delete") is True

    def test_one_shot_auto_delete_after_run(self) -> None:
        """一次性任务即使未显式设置 delete_after_run 也应自动删除。"""
        # 模拟 CronTool 的行为：显式传 delete_after_run=False
        upsert_cron_job({
            "name": "auto-delete",
            "schedule": "0 9 * * *",
            "prompt": "test",
            "recurring": False,
            "delete_after_run": False,
        })
        job = get_cron_job("auto-delete")
        assert job is not None
        # upsert 应自动将 delete_after_run 设为 True
        assert job.get("delete_after_run") is True

        mark_job_run("auto-delete", success=True)
        job = get_cron_job("auto-delete")
        assert job is not None
        assert job.get("_pending_delete") is True

        removed = remove_expired_jobs()
        assert len(removed) == 1
        assert get_cron_job_by_name("auto-delete") is None

    def test_remove_expired_jobs(self) -> None:
        """remove_expired_jobs 应清理标记为待删除的任务。"""
        upsert_cron_job({
            "name": "keep",
            "schedule": "* * * * *",
            "prompt": "keep",
        })
        upsert_cron_job({
            "name": "remove-me",
            "schedule": "0 9 * * *",
            "prompt": "bye",
            "recurring": False,
            "delete_after_run": True,
        })
        mark_job_run("remove-me", success=True)

        removed = remove_expired_jobs()
        assert "remove-me" not in [j.get("name") for j in load_cron_jobs()]
        assert len(removed) == 1

        # "keep" 应该还在
        assert get_cron_job_by_name("keep") is not None


class TestCorruptData:
    """损坏数据处理测试。"""

    def test_corrupt_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bad_file = tmp_path / "data" / "cron_jobs.json"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(
            "illusion.services.cron.get_cron_registry_path",
            lambda: bad_file,
        )
        assert load_cron_jobs() == []

    def test_non_list_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """非列表且非 dict 格式应返回空列表。"""
        bad_file = tmp_path / "data" / "cron_jobs.json"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text('"just a string"', encoding="utf-8")
        monkeypatch.setattr(
            "illusion.services.cron.get_cron_registry_path",
            lambda: bad_file,
        )
        assert load_cron_jobs() == []

    def test_new_store_format(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """应正确加载新格式（带 version 的 dict）。"""
        store_file = tmp_path / "data" / "cron_jobs.json"
        store_file.parent.mkdir(parents=True, exist_ok=True)
        store = {
            "version": 1,
            "jobs": [
                {"id": "abc123", "name": "j1", "schedule": "* * * * *", "prompt": "test", "enabled": True},
            ],
        }
        store_file.write_text(json.dumps(store), encoding="utf-8")
        monkeypatch.setattr(
            "illusion.services.cron.get_cron_registry_path",
            lambda: store_file,
        )
        jobs = load_cron_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "j1"

    def test_legacy_format_migration(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """旧格式（command 字段）应自动迁移到 prompt。"""
        store_file = tmp_path / "data" / "cron_jobs.json"
        store_file.parent.mkdir(parents=True, exist_ok=True)
        legacy = [
            {"name": "old-job", "schedule": "* * * * *", "command": "echo old", "enabled": True},
        ]
        store_file.write_text(json.dumps(legacy), encoding="utf-8")
        monkeypatch.setattr(
            "illusion.services.cron.get_cron_registry_path",
            lambda: store_file,
        )
        jobs = load_cron_jobs()
        assert len(jobs) == 1
        assert jobs[0]["prompt"] == "echo old"
        assert "command" not in jobs[0]
