"""Cron 调度器单元测试。

测试 cron 调度器功能：
- 历史记录、到期任务判断、任务执行、调度器循环
- 新增：错误退避、独立会话执行、CronScheduler 类
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from illusion.services.cron_scheduler import (
    CronScheduler,
    _get_backoff_seconds,
    _jobs_due,
    append_history,
    execute_job,
    load_history,
)


@pytest.fixture(autouse=True)
def _tmp_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """将数据和日志目录重定向到临时目录。"""
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    cron_dir = tmp_path / "data" / "cron"
    data_dir.mkdir()
    logs_dir.mkdir()
    cron_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("illusion.services.cron_scheduler.get_cron_dir", lambda: cron_dir)
    monkeypatch.setattr("illusion.services.cron_scheduler.get_logs_dir", lambda: logs_dir)
    # 同时重定向 cron 注册表
    monkeypatch.setattr(
        "illusion.services.cron.get_cron_registry_path",
        lambda: cron_dir / "jobs.json",
    )
    # 重置委托队列服务标志（跨测试文件隔离；需要委托的测试显式 set_served）
    from illusion.services import cron_delegation

    monkeypatch.setattr(cron_delegation, "_served", False)
    cron_delegation._pending.clear()
    yield
    cron_delegation._pending.clear()


class TestHistory:
    """历史记录测试。"""

    def test_empty_history(self) -> None:
        assert load_history() == []

    def test_append_and_load(self) -> None:
        append_history({"name": "j1", "status": "success"})
        append_history({"name": "j2", "status": "failed"})
        entries = load_history()
        assert len(entries) == 2
        assert entries[0]["name"] == "j1"

    def test_filter_by_name(self) -> None:
        append_history({"name": "j1", "status": "success"})
        append_history({"name": "j2", "status": "success"})
        entries = load_history(job_name="j1")
        assert len(entries) == 1
        assert entries[0]["name"] == "j1"

    def test_filter_by_id(self) -> None:
        append_history({"id": "abc", "name": "j1", "status": "success"})
        append_history({"id": "def", "name": "j2", "status": "success"})
        entries = load_history(job_id="abc")
        assert len(entries) == 1
        assert entries[0]["id"] == "abc"

    def test_limit(self) -> None:
        for i in range(10):
            append_history({"name": f"j{i}", "status": "success"})
        entries = load_history(limit=3)
        assert len(entries) == 3
        # 应该是最后 3 条
        assert entries[0]["name"] == "j7"


class TestBackoff:
    """错误退避策略测试。"""

    def test_no_backoff_on_zero_errors(self) -> None:
        assert _get_backoff_seconds(0) == 0

    def test_backoff_increases(self) -> None:
        assert _get_backoff_seconds(1) == 30
        assert _get_backoff_seconds(2) == 60
        assert _get_backoff_seconds(3) == 300
        assert _get_backoff_seconds(4) == 900
        assert _get_backoff_seconds(5) == 3600

    def test_backoff_caps_at_max(self) -> None:
        """超出退避序列时应使用最大值。"""
        assert _get_backoff_seconds(100) == 3600


class TestJobsDue:
    """到期任务判断测试。"""

    def test_due_job(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        past = (now - timedelta(minutes=5)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": True, "next_run": past},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 1

    def test_future_job_not_due(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future = (now + timedelta(hours=1)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": True, "next_run": future},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_disabled_job_not_due(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        past = (now - timedelta(minutes=5)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": False, "next_run": past},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_invalid_schedule_skipped(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        past = (now - timedelta(minutes=5)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "not valid", "enabled": True, "next_run": past},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_missing_next_run_skipped(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": True},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_job_in_backoff_period_skipped(self) -> None:
        """连续错误的任务在退避期内应被跳过。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        past = (now - timedelta(minutes=5)).isoformat()
        recent = (now - timedelta(seconds=10)).isoformat()
        jobs = [
            {
                "name": "j1",
                "schedule": "* * * * *",
                "enabled": True,
                "next_run": past,
                "consecutive_errors": 3,
                "last_run": recent,
            },
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_job_after_backoff_period_is_due(self) -> None:
        """退避期结束后任务应恢复执行。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        past = (now - timedelta(minutes=5)).isoformat()
        old = (now - timedelta(hours=1)).isoformat()
        jobs = [
            {
                "name": "j1",
                "schedule": "* * * * *",
                "enabled": True,
                "next_run": past,
                "consecutive_errors": 1,
                "last_run": old,
            },
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 1


class TestResolveCronPermissionMode:
    """_resolve_cron_permission_mode 权限模式解析测试。"""

    def test_no_targets_yolo(self) -> None:
        """无投递目标且无指定会话 → yolo。"""
        from illusion.services.cron_scheduler import _resolve_cron_permission_mode

        assert _resolve_cron_permission_mode({"id": "x"}) == "yolo"
        assert _resolve_cron_permission_mode({"id": "x", "deliver_to": [], "session_id": ""}) == "yolo"

    def test_deliver_to_uses_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """有投递目标（无会话）→ 继承 settings 权限模式。"""
        from types import SimpleNamespace

        from illusion.permissions.modes import PermissionMode
        from illusion.services.cron_scheduler import _resolve_cron_permission_mode

        fake = SimpleNamespace(permission=SimpleNamespace(mode=PermissionMode.FULL_AUTO))
        monkeypatch.setattr("illusion.config.settings.load_settings", lambda: fake)
        assert _resolve_cron_permission_mode({"deliver_to": ["weixin:x"]}) == "full_auto"

    def test_session_uses_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """有指定会话 → 继承 settings 权限模式。"""
        from types import SimpleNamespace

        from illusion.permissions.modes import PermissionMode
        from illusion.services.cron_scheduler import _resolve_cron_permission_mode

        fake = SimpleNamespace(permission=SimpleNamespace(mode=PermissionMode.PLAN))
        monkeypatch.setattr("illusion.config.settings.load_settings", lambda: fake)
        assert _resolve_cron_permission_mode({"session_id": "s1"}) == "plan"

    def test_session_priority_over_deliver_to(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """有指定会话优先于投递目标：session 优先。"""
        from types import SimpleNamespace

        from illusion.permissions.modes import PermissionMode
        from illusion.services.cron_scheduler import _resolve_cron_permission_mode

        fake = SimpleNamespace(permission=SimpleNamespace(mode=PermissionMode.DEFAULT))
        monkeypatch.setattr("illusion.config.settings.load_settings", lambda: fake)
        assert _resolve_cron_permission_mode(
            {"session_id": "s1", "deliver_to": ["weixin:x"]}
        ) == "default"


class TestExecuteJob:
    """任务执行测试。"""

    @pytest.mark.asyncio
    async def test_missing_prompt(self, tmp_path: Path) -> None:
        """缺少 prompt 的任务应返回错误。"""
        job = {"name": "no-prompt", "id": "test1", "cwd": str(tmp_path)}
        entry = await execute_job(job)
        assert entry["status"] == "error"
        assert "prompt" in entry["stderr"]

    @pytest.mark.asyncio
    async def test_execute_calls_subprocess(self, tmp_path: Path) -> None:
        """应通过子进程执行 prompt。"""
        mock_result = {
            "returncode": 0,
            "status": "success",
            "stdout": "OK",
            "stderr": "",
        }
        with (
            patch("illusion.services.cron_scheduler._resolve_cron_permission_mode", return_value="yolo"),
            patch(
                "illusion.services.cron_scheduler._execute_prompt_in_subprocess",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_exec,
        ):
            job = {"name": "test", "id": "test1", "prompt": "echo hello", "cwd": str(tmp_path)}
            entry = await execute_job(job)
            assert entry["status"] == "success"
            # 无 deliver_to 无 session：yolo 模式通过环境变量传递，不持久化
            mock_exec.assert_called_once_with(
                "echo hello", tmp_path, timeout=300,
                extra_env={"ILLUSION_PERMISSION_MODE": "yolo"},
            )

    @pytest.mark.asyncio
    async def test_execute_with_deliver_to_adds_cron_prefix(self, tmp_path: Path) -> None:
        """有 deliver_to 时应拼接 cron 上下文前缀并设置环境变量标记。"""
        mock_result = {
            "returncode": 0,
            "status": "success",
            "stdout": "OK",
            "stderr": "",
        }
        with (
            patch("illusion.services.cron_scheduler.validate_job_targets", return_value=[]) as _validate_mock,
            patch("illusion.services.cron_scheduler._resolve_cron_permission_mode", return_value="default"),
            patch(
                "illusion.services.cron_scheduler._execute_prompt_in_subprocess",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_exec,
        ):
            job = {
                "name": "test",
                "id": "test1",
                "prompt": "发送早安问候",
                "cwd": str(tmp_path),
                "deliver_to": ["weixin:wxid@im.wechat"],
            }
            entry = await execute_job(job)
            assert entry["status"] == "success"
            # 验证调用参数
            call_args = mock_exec.call_args
            actual_prompt = call_args.args[0]
            assert "[CRON TASK CONTEXT]" in actual_prompt
            assert "发送早安问候" in actual_prompt
            # 有投递无 session：渠道端语义——权限自动批准 + 保留沙箱
            assert call_args.kwargs["extra_env"] == {
                "ILLUSION_PERMISSION_MODE": "default",
                "ILLUSION_CRON_TASK": "1",
                "ILLUSION_CRON_AUTO_APPROVE": "1",
            }

    @pytest.mark.asyncio
    async def test_execute_with_session_id_uses_delegation(self, tmp_path: Path) -> None:
        """指定会话执行（session_id 存在）：应优先走委托，主程序回报后不调子进程。"""
        from illusion.services import cron_delegation

        async def fake_delegate(job, timeout=300):
            # 模拟主程序领取并回报成功
            fut = cron_delegation.register_pending_job(job)
            cron_delegation.report_result(job["id"], {
                "status": "success",
                "returncode": 0,
                "stdout": "delegated ok",
                "stderr": "",
            })
            return await asyncio.wait_for(fut, timeout=1)

        with (
            patch("illusion.services.cron_scheduler.validate_job_targets", return_value=[]),
            patch("illusion.services.cron_scheduler._try_delegate_execution", side_effect=fake_delegate),
            patch(
                "illusion.services.cron_scheduler._execute_prompt_in_subprocess",
                new_callable=AsyncMock,
            ) as mock_exec,
        ):
            job = {
                "name": "test", "id": "test1", "prompt": "hello",
                "cwd": str(tmp_path), "session_id": "sess_1",
            }
            entry = await execute_job(job)
            assert entry["status"] == "success"
            assert entry["stdout"] == "delegated ok"
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_with_session_id_falls_back_to_subprocess(self, tmp_path: Path) -> None:
        """指定会话执行：委托无人接管（返回 None）时应回退子进程并带 -r 参数。"""
        mock_result = {
            "returncode": 0,
            "status": "success",
            "stdout": "OK",
            "stderr": "",
        }
        with (
            patch("illusion.services.cron_scheduler.validate_job_targets", return_value=[]),
            patch(
                "illusion.services.cron_scheduler._try_delegate_execution",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("illusion.services.cron_scheduler._resolve_cron_permission_mode", return_value="default"),
            patch(
                "illusion.services.cron_scheduler._execute_prompt_in_subprocess",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_exec,
        ):
            job = {
                "name": "test", "id": "test1", "prompt": "hello",
                "cwd": str(tmp_path), "session_id": "sess_1",
            }
            entry = await execute_job(job)
            assert entry["status"] == "success"
            call_args = mock_exec.call_args
            assert call_args.kwargs["extra_args"] == ["-r", "sess_1", "--cwd", str(tmp_path)]
            # 指定会话：权限模式通过环境变量继承当前配置（不持久化）
            assert call_args.kwargs["extra_env"] == {"ILLUSION_PERMISSION_MODE": "default"}

    @pytest.mark.asyncio
    async def test_try_delegate_execution_reports_success(self) -> None:
        """_try_delegate_execution：主程序回报结果后返回同构 entry。"""
        from illusion.services import cron_delegation
        from illusion.services.cron_scheduler import _try_delegate_execution

        cron_delegation.set_served()
        job = {"id": "job1", "session_id": "s1"}

        async def _run():
            return await _try_delegate_execution(job)

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.01)
        # 模拟主程序通过 IPC 领取并回报
        cron_delegation.report_result("job1", {
            "status": "failed", "returncode": 2, "stdout": "out", "stderr": "err",
        })
        result = await asyncio.wait_for(task, timeout=1)
        assert result is not None
        assert result["status"] == "failed"
        assert result["stdout"] == "out"

    @pytest.mark.asyncio
    async def test_try_delegate_execution_unclaimed_returns_none(self) -> None:
        """_try_delegate_execution：领取窗口耗尽（unclaimed）返回 None（回退子进程）。"""
        from illusion.services import cron_delegation
        from illusion.services.cron_scheduler import _try_delegate_execution

        cron_delegation.set_served()

        async def _run():
            return await _try_delegate_execution({"id": "job2", "session_id": "s1"})

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.01)
        # 强制标记领取窗口过期后 reap，模拟窗口耗尽
        cron_delegation._pending["job2"].deadline = 1.0
        cron_delegation.reap_expired()
        result = await asyncio.wait_for(task, timeout=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_try_delegate_not_served_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 daemon 进程（is_served=False）：跳过委托直接返回 None，不注册任务。"""
        from illusion.services import cron_delegation
        from illusion.services.cron_scheduler import _try_delegate_execution

        monkeypatch.setattr(cron_delegation, "_served", False)
        result = await _try_delegate_execution({"id": "job9", "session_id": "s1"})
        assert result is None
        assert cron_delegation._pending == {}

    @pytest.mark.asyncio
    async def test_try_delegate_timeout_unclaimed_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """总超时且任务未被领取：回收队列并回退子进程（返回 None）。"""
        from illusion.services import cron_delegation
        from illusion.services.cron_scheduler import _try_delegate_execution

        cron_delegation.set_served()
        monkeypatch.setattr(cron_delegation, "CLAIM_WINDOW_SECONDS", 0)
        result = await _try_delegate_execution({"id": "job3", "session_id": "s1"}, timeout=0)
        assert result is None
        assert "job3" not in cron_delegation._pending

    @pytest.mark.asyncio
    async def test_try_delegate_timeout_claimed_returns_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """总超时且任务已被领取（在途执行）：返回 timeout entry，不回退子进程。"""
        from illusion.services import cron_delegation
        from illusion.services.cron_scheduler import _try_delegate_execution

        cron_delegation.set_served()
        # 总等待 = 领取窗口(0.01s) + 执行超时(0.01s)，给外部领取留出时间窗
        monkeypatch.setattr(cron_delegation, "CLAIM_WINDOW_SECONDS", 0.01)

        async def _run():
            return await _try_delegate_execution({"id": "job4", "session_id": "s1"}, timeout=0.01)

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.005)  # 内部 register 完成
        cron_delegation.claim_pending()  # 模拟主程序已领取（在途执行）
        result = await asyncio.wait_for(task, timeout=1)
        assert result is not None
        assert result["status"] == "timeout"


class TestCronContextPrefix:
    """_build_cron_context_prefix 渠道身份提示测试。"""

    def _prefix(self, deliver_to: list[str], chat_id: str = "") -> str | None:
        from illusion.services.cron_scheduler import _build_cron_context_prefix

        return _build_cron_context_prefix(deliver_to, chat_id)

    def test_no_deliver_to_returns_none(self) -> None:
        """无投递目标 → 无前缀。"""
        assert self._prefix([]) is None

    def test_channel_identity_instead_of_delivery(self) -> None:
        """前缀告知当前所在渠道，而非「输出会被投递到某渠道」。"""
        prefix = self._prefix(["qq:openid_xxx"])
        assert prefix is not None
        assert "currently in the QQ channel" in prefix
        # 不得出现"会被投递到"类表述（旧前缀文案）
        assert "deliver your stdout" not in prefix
        assert "target channel" not in prefix

    def test_multiple_channels(self) -> None:
        """多渠道目标 → 列出全部渠道名。"""
        prefix = self._prefix(["qq:openid_xxx", "weixin:wxid_xxx"])
        assert prefix is not None
        assert "QQ" in prefix
        assert "WeChat" in prefix

    def test_channel_only_target_uses_chat_id(self) -> None:
        """渠道-only 目标（如 ["weixin"]）依赖 chat_id 解析。"""
        prefix = self._prefix(["weixin"], chat_id="wxid_abc")
        assert prefix is not None
        assert "WeChat" in prefix

    def test_includes_anti_delivery_mention_instruction(self) -> None:
        """前缀明确禁止 LLM 在回复中提及投递动作（如"已发送"）。"""
        prefix = self._prefix(["feishu:ou_xxx"])
        assert prefix is not None
        assert "Do NOT mention delivery" in prefix


class TestValidateJobTargets:
    """validate_job_targets 校验测试。"""

    def _write_session_meta(self, cwd: Path, sid: str) -> None:
        """构造一个存在的项目会话（meta.json）。"""
        from illusion.services.session_storage import session_dir_for

        session_dir = session_dir_for(cwd, sid)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "meta.json").write_text(
            f'{{"session_id": "{sid}", "summary": "test", "message_count": 2}}',
            encoding="utf-8",
        )

    def _write_channel_session(self, channel: str, chat_id: str, user_id: str = "") -> None:
        """构造一个渠道活跃会话文件（u_<chat_id>.json）。"""
        from illusion.config.paths import get_channels_data_dir

        sessions_dir = get_channels_data_dir() / channel / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / f"u_{chat_id}.json").write_text(
            '{"user_id": "%s"}' % (user_id or chat_id), encoding="utf-8",
        )

    def _enable_channel(self, channel: str) -> None:
        """启用指定渠道（写 channels.json）。"""
        from illusion.channels.config import load_channels_config, save_channels_config

        cfg = load_channels_config()
        getattr(cfg, channel).enabled = True
        save_channels_config(cfg)

    def test_no_targets_passes(self, tmp_path: Path) -> None:
        """无 session_id 无 deliver_to → 校验通过。"""
        from illusion.services.cron_scheduler import validate_job_targets

        errors = validate_job_targets({"cwd": str(tmp_path)})
        assert errors == []

    def test_session_id_not_found(self, tmp_path: Path) -> None:
        """session_id 指定的会话不存在 → 错误。"""
        from illusion.services.cron_scheduler import validate_job_targets

        errors = validate_job_targets({"cwd": str(tmp_path), "session_id": "ghost_sid"})
        assert len(errors) == 1
        assert "Session not found" in errors[0]

    def test_session_id_found(self, tmp_path: Path) -> None:
        """session_id 指定的会话存在 → 通过。"""
        from illusion.services.cron_scheduler import validate_job_targets

        self._write_session_meta(tmp_path, "sess_exist")
        errors = validate_job_targets({"cwd": str(tmp_path), "session_id": "sess_exist"})
        assert errors == []

    def test_deliver_to_channel_not_enabled(self, tmp_path: Path) -> None:
        """deliver_to 指向未启用渠道 → 错误。"""
        from illusion.services.cron_scheduler import validate_job_targets

        errors = validate_job_targets({
            "cwd": str(tmp_path),
            "deliver_to": ["weixin:abc"],
        })
        assert any("not enabled" in e for e in errors)

    def test_deliver_to_session_not_found(self, tmp_path: Path) -> None:
        """deliver_to 指向已启用渠道但 chat_id 不存在 → 错误。"""
        from illusion.services.cron_scheduler import validate_job_targets

        self._enable_channel("weixin")
        errors = validate_job_targets({
            "cwd": str(tmp_path),
            "deliver_to": ["weixin:nonexistent"],
        })
        assert any("not found" in e for e in errors)

    def test_deliver_to_found(self, tmp_path: Path) -> None:
        """deliver_to 指向已启用渠道且 chat_id 存在 → 通过。"""
        from illusion.services.cron_scheduler import validate_job_targets

        self._enable_channel("weixin")
        self._write_channel_session("weixin", "wxid_test")
        errors = validate_job_targets({
            "cwd": str(tmp_path),
            "deliver_to": ["weixin:wxid_test"],
        })
        assert errors == []


class TestExecuteJobValidation:
    """execute_job 执行前校验集成测试。"""

    @pytest.mark.asyncio
    async def test_execute_rejected_when_session_missing(self, tmp_path: Path) -> None:
        """session_id 不存在时 execute_job 拒绝执行（不调子进程）。"""
        with patch(
            "illusion.services.cron_scheduler._execute_prompt_in_subprocess",
            new_callable=AsyncMock,
        ) as mock_exec:
            job = {
                "id": "t1", "name": "t", "prompt": "hello",
                "cwd": str(tmp_path), "session_id": "ghost_sid",
            }
            entry = await execute_job(job)
            assert entry["status"] == "error"
            assert "Session not found" in entry["stderr"]
            assert "execution rejected" in entry["stderr"]
            mock_exec.assert_not_called()


class TestSchedulerClass:
    """CronScheduler 类测试。"""

    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        """调度器应能正常启动和停止。"""
        scheduler = CronScheduler()
        assert not scheduler.is_running

        await scheduler.start()
        assert scheduler.is_running

        await scheduler.stop()
        assert not scheduler.is_running

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self) -> None:
        """重复启动不应出错。"""
        scheduler = CronScheduler()
        await scheduler.start()
        await scheduler.start()  # 不应抛出异常
        assert scheduler.is_running
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_status(self) -> None:
        """状态查询应返回正确信息。"""
        scheduler = CronScheduler()
        status = scheduler.status()
        assert "running" in status
        assert "total_jobs" in status
        assert "enabled_jobs" in status


def test_start_daemon_deprecated_returns_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """start_daemon 标记废弃后仍返回当前进程 PID"""
    import warnings

    from illusion.services.cron_scheduler import start_daemon

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = start_daemon()
    assert pid == os.getpid()
