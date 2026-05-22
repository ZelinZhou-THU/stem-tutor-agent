from __future__ import annotations

import asyncio
import json
import os

import pytest

import web.database as db

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
