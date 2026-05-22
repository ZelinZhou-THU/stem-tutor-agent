from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio

import web.database as db
import web.database as db_mod

_test_db = os.path.join(os.path.dirname(__file__), "_test_batch.db")


@pytest.fixture(autouse=True)
def _patch_db_path(tmp_path, monkeypatch):
    db_path = tmp_path / "test_batch.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db._initialized = False
    asyncio.get_event_loop().run_until_complete(db._ensure_db())
    yield
    db._initialized = False


@pytest.mark.asyncio
async def test_create_and_load_batch():
    batch_id = await db.create_batch(
        user_id=1,
        settings={"model": "qwen/qwen3.6-plus", "subject_id": "calculus", "mode": "workflow_r1", "depth": "standard"},
        total_count=3,
    )
    batch = await db.load_batch(batch_id, user_id=1)
    assert batch is not None
    assert batch["status"] == "pending"
    assert batch["total_count"] == 3
    assert batch["completed_count"] == 0
    assert batch["failed_count"] == 0
    settings = json.loads(batch["settings"]) if isinstance(batch["settings"], str) else batch["settings"]
    assert settings["model"] == "qwen/qwen3.6-plus"


@pytest.mark.asyncio
async def test_add_and_list_batch_items():
    batch_id = await db.create_batch(user_id=1, settings={}, total_count=2)
    items = [
        {"problem_text": "求积分1", "student_solution": "解1", "source_type": "text"},
        {"problem_text": "求积分2", "student_solution": "解2", "source_type": "text"},
    ]
    await db.add_batch_items(batch_id, items)
    rows = await db.list_batch_items(batch_id)
    assert len(rows) == 2
    assert rows[0]["seq"] == 0
    assert rows[0]["status"] == "pending"
    assert rows[0]["problem_text"] == "求积分1"
    assert rows[1]["seq"] == 1


@pytest.mark.asyncio
async def test_update_batch_item_status():
    batch_id = await db.create_batch(user_id=1, settings={}, total_count=1)
    await db.add_batch_items(batch_id, [{"problem_text": "题1", "student_solution": "解1", "source_type": "text"}])
    await db.update_batch_item(batch_id, seq=0, status="completed", run_id="run-uuid-123")
    rows = await db.list_batch_items(batch_id)
    assert rows[0]["status"] == "completed"
    assert rows[0]["run_id"] == "run-uuid-123"
    batch = await db.load_batch(batch_id, user_id=1)
    assert batch["completed_count"] == 1


@pytest.mark.asyncio
async def test_update_batch_status():
    batch_id = await db.create_batch(user_id=1, settings={}, total_count=1)
    await db.update_batch_status(batch_id, status="running")
    batch = await db.load_batch(batch_id, user_id=1)
    assert batch["status"] == "running"


@pytest.mark.asyncio
async def test_list_batches():
    await db.create_batch(user_id=1, settings={}, total_count=2)
    await db.create_batch(user_id=1, settings={}, total_count=3)
    result = await db.list_batches(user_id=1)
    assert len(result["batches"]) == 2
    assert result["total"] == 2
    assert result["batches"][0]["total_count"] == 3


@pytest.mark.asyncio
async def test_claim_next_pending_item():
    batch_id = await db.create_batch(user_id=1, settings={}, total_count=3)
    await db.add_batch_items(
        batch_id,
        [
            {"problem_text": "题1", "student_solution": "解1", "source_type": "text"},
            {"problem_text": "题2", "student_solution": "解2", "source_type": "text"},
            {"problem_text": "题3", "student_solution": "解3", "source_type": "text"},
        ],
    )
    await db.update_batch_status(batch_id, status="running")
    item1 = await db.claim_next_pending_item(batch_id)
    assert item1["seq"] == 0
    assert item1["status"] == "pending"
    await db.update_batch_item(batch_id, seq=0, status="completed", run_id="r1")
    item2 = await db.claim_next_pending_item(batch_id)
    assert item2["seq"] == 1


@pytest.mark.asyncio
async def test_delete_batch():
    batch_id = await db.create_batch(user_id=1, settings={}, total_count=1)
    await db.add_batch_items(batch_id, [{"problem_text": "题1", "student_solution": "解1", "source_type": "text"}])
    await db.delete_batch(batch_id, user_id=1)
    assert await db.load_batch(batch_id, user_id=1) is None
    assert await db.list_batch_items(batch_id) == []


@pytest.mark.asyncio
async def test_recover_stale_running_items():
    batch_id = await db.create_batch(user_id=1, settings={}, total_count=2)
    await db.add_batch_items(
        batch_id,
        [
            {"problem_text": "题1", "student_solution": "解1", "source_type": "text"},
            {"problem_text": "题2", "student_solution": "解2", "source_type": "text"},
        ],
    )
    await db.update_batch_status(batch_id, status="running")
    await db.update_batch_item(batch_id, seq=0, status="running")
    n = await db.recover_stale_running_items()
    assert n >= 1
    rows = await db.list_batch_items(batch_id)
    assert rows[0]["status"] == "pending"


async def _mock_stream(**kwargs):
    import json as _json
    yield f'data: {_json.dumps({"type": "start", "run_id": "mock-run-id"}, ensure_ascii=False)}\n\n'
    yield f'data: {_json.dumps({"type": "result", "data": {"run_id": "mock-run-id", "status": "success"}}, ensure_ascii=False)}\n\n'
    yield f'data: {_json.dumps({"type": "done", "message": "done"}, ensure_ascii=False)}\n\n'


@pytest_asyncio.fixture
async def auth_client(tmp_path, monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from web.app import app
    from web.database import _ensure_db, create_user
    from web.auth import create_access_token

    db_path = tmp_path / "test_batch_api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db._initialized = False
    await _ensure_db()

    uid = await create_user("batch_api_user", "hashed_pw")
    token = create_access_token(uid, "batch_api_user", is_admin=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["Authorization"] = f"Bearer {token}"
        yield ac
    db._initialized = False


@pytest.mark.asyncio
async def test_batch_create_and_status_api(auth_client):
    resp = await auth_client.post("/batch/create", json={
        "items": [
            {"problem_text": "1+1=?", "student_solution": "2", "source_type": "text"},
            {"problem_text": "2+2=?", "student_solution": "4", "source_type": "text"},
        ],
        "settings": {"model": "qwen/qwen3.6-plus", "subject_id": "calculus", "mode": "workflow_r1", "depth": "standard"},
        "auto_start": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "batch_id" in data
    assert data["total_count"] == 2

    batch_id = data["batch_id"]
    resp2 = await auth_client.get(f"/batch/{batch_id}/status")
    assert resp2.status_code == 200
    status = resp2.json()
    assert status["total_count"] == 2
    assert status["status"] == "pending"


@pytest.mark.asyncio
async def test_batch_list_api(auth_client):
    resp = await auth_client.get("/batch/list")
    assert resp.status_code == 200
    data = resp.json()
    assert "batches" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_batch_pause_resume_cancel_api(auth_client):
    resp = await auth_client.post("/batch/create", json={
        "items": [{"problem_text": "test", "student_solution": "t", "source_type": "text"}],
        "settings": {},
        "auto_start": True,
    })
    batch_id = resp.json()["batch_id"]

    resp2 = await auth_client.post(f"/batch/{batch_id}/pause")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "paused"

    resp3 = await auth_client.post(f"/batch/{batch_id}/resume")
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "running"

    resp4 = await auth_client.post(f"/batch/{batch_id}/cancel")
    assert resp4.status_code == 200
    assert resp4.json()["status"] == "cancelled"

    resp5 = await auth_client.delete(f"/batch/{batch_id}")
    assert resp5.status_code == 200


@pytest.mark.asyncio
async def test_batch_create_validation(auth_client):
    resp = await auth_client.post("/batch/create", json={
        "items": [],
        "settings": {},
    })
    assert resp.status_code == 400

    resp2 = await auth_client.post("/batch/create", json={
        "items": [{"problem_text": "", "student_solution": "", "source_type": "text"}],
        "settings": {},
    })
    assert resp2.status_code == 400


@pytest.mark.asyncio
async def test_worker_processes_batch(tmp_path, monkeypatch):
    import web.batch_worker
    monkeypatch.setattr(web.batch_worker, "run_stem_tutor_stream", _mock_stream)
    from web.batch_worker import BatchWorker

    db_path = tmp_path / "test_worker.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db._initialized = False
    await db._ensure_db()

    worker = BatchWorker()
    batch_id = await db_mod.create_batch(user_id=1, settings={"model": "qwen/qwen3.6-plus", "subject_id": "calculus", "mode": "workflow_r1", "depth": "standard"}, total_count=1)
    await db_mod.add_batch_items(batch_id, [{"problem_text": "1+1=?", "student_solution": "1+1=2", "source_type": "text"}])
    await db_mod.update_batch_status(batch_id, status="running")
    await worker._process_one_cycle()
    items = await db_mod.list_batch_items(batch_id)
    assert items[0]["status"] == "completed"
    assert items[0]["run_id"] == "mock-run-id"
    batch = await db_mod.load_batch(batch_id, user_id=1)
    assert batch["status"] == "completed"
