from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    import web.database as db_mod
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(db_mod, "_initialized", False)
    yield db_file


@pytest.mark.asyncio
async def test_list_chats_by_user(tmp_db):
    from web.database import _ensure_db, create_user, save_run, save_chat, list_chats_by_user

    await _ensure_db()
    uid = await create_user("u1", "h1")

    await save_run("r1", uid, {"status": "success"}, status="success")
    await save_chat("r1", uid, [{"role": "user", "content": "hi"}])

    await save_run("r2", uid, {"status": "success"}, status="success")
    await save_chat("r2", uid, [{"role": "user", "content": "hello"}])

    chats = await list_chats_by_user(uid)
    assert len(chats) == 2
    run_ids = {c["run_id"] for c in chats}
    assert run_ids == {"r1", "r2"}
    assert chats[0]["messages"] is not None


@pytest.mark.asyncio
async def test_list_chats_by_user_empty(tmp_db):
    from web.database import _ensure_db, create_user, list_chats_by_user

    await _ensure_db()
    uid = await create_user("u2", "h2")
    chats = await list_chats_by_user(uid)
    assert chats == []


@pytest.mark.asyncio
async def test_list_chats_by_user_isolation(tmp_db):
    from web.database import _ensure_db, create_user, save_run, save_chat, list_chats_by_user

    await _ensure_db()
    uid_a = await create_user("a", "ha")
    uid_b = await create_user("b", "hb")

    await save_run("r1", uid_a, {}, status="success")
    await save_chat("r1", uid_a, [{"role": "user", "content": "a msg"}])

    await save_run("r2", uid_b, {}, status="success")
    await save_chat("r2", uid_b, [{"role": "user", "content": "b msg"}])

    chats_a = await list_chats_by_user(uid_a)
    assert len(chats_a) == 1
    assert chats_a[0]["run_id"] == "r1"
