"""cron_routes FastAPI 路由测试。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from illusion.ui.web.cron_routes import register_cron_routes


@pytest.fixture
def app(tmp_path, monkeypatch):
    """创建带 cron 路由的 FastAPI 测试 app，并隔离配置目录。"""
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(tmp_path / "config"))
    app = FastAPI()
    register_cron_routes(app, host_config=None)
    return app


@pytest.fixture
def client(app):
    # 浏览器信任栅栏要求 Host 为回环地址（TestClient 默认 host 是 testserver）
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture(autouse=True)
def _no_daemon_spawn(monkeypatch):
    """测试中屏蔽真实守护进程拉起（cron_routes 内部延迟导入源模块，需 patch 源路径）。"""
    monkeypatch.setattr(
        "illusion.services.cron_spawn.maybe_spawn_cron_daemon",
        lambda *a, **k: (None, None),
    )


def _create_job(client, **overrides):
    """创建任务的辅助函数，返回响应对象。"""
    payload = {
        "name": "daily-report",
        "schedule": "0 9 * * *",
        "prompt": "生成日报",
        **overrides,
    }
    return client.post("/api/cron/jobs", json=payload)


def test_get_status_returns_empty_state(client):
    """无任务时 GET /api/cron/status 返回空状态。"""
    resp = client.get("/api/cron/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_jobs"] == 0
    assert data["enabled_jobs"] == 0
    assert "running" in data
    assert "pid" in data


def test_get_jobs_returns_empty_list(client):
    """无任务时 GET /api/cron/jobs 返回空列表。"""
    resp = client.get("/api/cron/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["jobs"] == []
    assert data["running_jobs"] == []


def test_run_job_marks_running_and_clears(client, monkeypatch):
    """run 执行期间 running_jobs 标记，完成后清除（异常路径也清除）。"""
    async def fake_execute_job(job, timeout=300):
        # 执行期间：另一个请求应能看到该任务在运行
        mid = client.get("/api/cron/jobs").json()
        assert job["id"] in mid["running_jobs"]
        return {
            "id": job["id"], "name": job["name"], "prompt": job["prompt"],
            "started_at": "2026-01-01T09:00:00", "ended_at": "2026-01-01T09:00:05",
            "returncode": 0, "status": "success", "stdout": "ok", "stderr": "",
        }

    monkeypatch.setattr("illusion.services.cron_scheduler.execute_job", fake_execute_job)
    job_id = _create_job(client).json()["id"]
    resp = client.post(f"/api/cron/jobs/{job_id}/run")
    assert resp.status_code == 200
    # 完成后 running_jobs 已清除
    assert client.get("/api/cron/jobs").json()["running_jobs"] == []


def test_run_job_clears_running_on_error(client, monkeypatch):
    """run 执行异常时 running_jobs 也清除（finally 语义）。"""

    async def boom(job, timeout=300):
        raise RuntimeError("boom")

    monkeypatch.setattr("illusion.services.cron_scheduler.execute_job", boom)
    job_id = _create_job(client).json()["id"]
    with pytest.raises(RuntimeError):
        client.post(f"/api/cron/jobs/{job_id}/run")
    assert client.get("/api/cron/jobs").json()["running_jobs"] == []


def test_get_sessions_returns_list(client):
    """GET /api/cron/sessions 返回项目会话列表。"""
    resp = client.get("/api/cron/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)


def test_get_channel_sessions_no_channels(client):
    """无启用渠道时 GET /api/cron/channel_sessions 返回空 channels 字典。"""
    resp = client.get("/api/cron/channel_sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "channels" in data
    assert data["channels"] == {}


def test_create_job_success(client, monkeypatch):
    """POST /api/cron/jobs 创建任务成功并返回 id。"""
    spawn_called = []

    def fake_spawn(*args, **kwargs):
        spawn_called.append(True)
        return None, None

    monkeypatch.setattr("illusion.services.cron_spawn.maybe_spawn_cron_daemon", fake_spawn)

    resp = _create_job(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"]
    assert data["job"]["name"] == "daily-report"
    assert data["job"]["schedule"] == "0 9 * * *"
    assert data["job"]["next_run"]  # next_run 已计算
    assert data["job"]["enabled"] is True
    assert data["job"]["recurring"] is True
    assert data["job"]["delete_after_run"] is False
    assert data["job"]["cwd"]
    assert spawn_called, "创建任务后应触发守护进程拉起"


def test_get_jobs_after_create(client):
    """创建任务后 GET /api/cron/jobs 返回该任务。"""
    _create_job(client)
    resp = client.get("/api/cron/jobs")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["name"] == "daily-report"


def test_create_job_invalid_schedule_rejected(client):
    """无效 cron 表达式返回 400。"""
    resp = _create_job(client, schedule="not-a-cron")
    assert resp.status_code == 400


def test_create_job_missing_prompt_rejected(client):
    """prompt 为空返回 400。"""
    resp = _create_job(client, prompt="   ")
    assert resp.status_code == 400


def test_create_job_without_name_generates_id(client):
    """name 缺省时自动生成 id 作为名称。"""
    resp = client.post("/api/cron/jobs", json={"schedule": "*/5 * * * *", "prompt": "检查状态"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"]
    assert data["job"]["name"] == data["id"]


def test_create_job_with_deliver_to(client):
    """创建任务支持 deliver_to 投递目标。"""
    resp = _create_job(client, deliver_to=["weixin:abc", "feishu:ou_123"])
    assert resp.status_code == 200
    assert resp.json()["job"]["deliver_to"] == ["weixin:abc", "feishu:ou_123"]


def test_create_job_with_session_id(client):
    """创建任务支持指定会话执行（session_id 透传）。"""
    resp = _create_job(client, session_id="sess_abc123")
    assert resp.status_code == 200
    assert resp.json()["job"]["session_id"] == "sess_abc123"


def test_create_job_without_session_id(client):
    """创建任务缺省 session_id 时不写入该字段。"""
    resp = _create_job(client)
    assert resp.status_code == 200
    assert "session_id" not in resp.json()["job"]


def test_update_job_session_id(client):
    """PATCH 更新 session_id。"""
    job_id = _create_job(client).json()["id"]
    resp = client.patch(f"/api/cron/jobs/{job_id}", json={"session_id": "sess_xyz"})
    assert resp.status_code == 200
    assert resp.json()["job"]["session_id"] == "sess_xyz"


def test_update_job_session_id_cleared(client):
    """PATCH 传 session_id=null 显式清除。"""
    job_id = _create_job(client, session_id="sess_abc").json()["id"]
    resp = client.patch(f"/api/cron/jobs/{job_id}", json={"session_id": None})
    assert resp.status_code == 200
    assert "session_id" not in resp.json()["job"]


def test_update_job_without_session_id_keeps_existing(client):
    """PATCH 不提供 session_id 时保留原值（model_fields_set 语义）。"""
    job_id = _create_job(client, session_id="sess_abc").json()["id"]
    resp = client.patch(f"/api/cron/jobs/{job_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["job"]["session_id"] == "sess_abc"


def test_update_job_fields(client):
    """PATCH 更新 enabled / schedule / prompt。"""
    job_id = _create_job(client).json()["id"]

    resp = client.patch(f"/api/cron/jobs/{job_id}", json={
        "enabled": False,
        "schedule": "30 8 * * 1-5",
        "prompt": "新的提示词",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    job = data["job"]
    assert job["enabled"] is False
    assert job["schedule"] == "30 8 * * 1-5"
    assert job["prompt"] == "新的提示词"
    # next_run 已按新表达式重算
    assert job["next_run"]


def test_update_job_rename_duplicate_rejected(client):
    """重命名与其他任务重名时返回 400。"""
    job_id_1 = _create_job(client, name="job-a").json()["id"]
    _create_job(client, name="job-b")

    resp = client.patch(f"/api/cron/jobs/{job_id_1}", json={"name": "job-b"})
    assert resp.status_code == 400


def test_update_job_invalid_schedule_rejected(client):
    """更新为无效 cron 表达式返回 400。"""
    job_id = _create_job(client).json()["id"]
    resp = client.patch(f"/api/cron/jobs/{job_id}", json={"schedule": "bad"})
    assert resp.status_code == 400


def test_update_job_not_found(client):
    """更新不存在的任务返回 404。"""
    resp = client.patch("/api/cron/jobs/nonexistent", json={"enabled": False})
    assert resp.status_code == 404


def test_delete_job(client):
    """DELETE 删除任务成功。"""
    job_id = _create_job(client).json()["id"]
    resp = client.delete(f"/api/cron/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # 删除后列表为空
    assert client.get("/api/cron/jobs").json()["jobs"] == []


def test_delete_job_not_found(client):
    """删除不存在的任务返回 404。"""
    resp = client.delete("/api/cron/jobs/nonexistent")
    assert resp.status_code == 404


def test_run_job(client, monkeypatch):
    """POST run 手动触发执行并返回结果摘要。"""
    job_id = _create_job(client).json()["id"]

    async def fake_execute_job(job, timeout=300):
        return {
            "id": job["id"],
            "name": job["name"],
            "prompt": job["prompt"],
            "started_at": "2026-01-01T09:00:00",
            "ended_at": "2026-01-01T09:00:05",
            "returncode": 0,
            "status": "success",
            "stdout": "report ok",
            "stderr": "",
        }

    monkeypatch.setattr("illusion.services.cron_scheduler.execute_job", fake_execute_job)

    resp = client.post(f"/api/cron/jobs/{job_id}/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["returncode"] == 0
    assert data["stdout"] == "report ok"


def test_run_job_not_found(client):
    """运行不存在的任务返回 404。"""
    resp = client.post("/api/cron/jobs/nonexistent/run")
    assert resp.status_code == 404


def test_run_job_without_prompt_rejected(client):
    """任务无 prompt 时运行返回 400。"""
    # 直接构造一个无 prompt 的任务写入注册表
    from illusion.services.cron import upsert_cron_job

    job_id = upsert_cron_job({"name": "no-prompt-job", "schedule": "0 0 * * *", "prompt": ""})
    resp = client.post(f"/api/cron/jobs/{job_id}/run")
    assert resp.status_code == 400
