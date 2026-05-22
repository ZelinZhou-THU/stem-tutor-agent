from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import web.database as db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db._initialized = False
    asyncio.get_event_loop().run_until_complete(db._ensure_db())
    yield
    db._initialized = False


@pytest.mark.asyncio
async def test_create_and_get_user():
    uid = await db.create_user("alice", "hashed_pw")
    assert isinstance(uid, int)

    by_name = await db.get_user_by_username("alice")
    assert by_name is not None
    assert by_name["username"] == "alice"
    assert by_name["id"] == uid

    by_id = await db.get_user_by_id(uid)
    assert by_id is not None
    assert by_id["username"] == "alice"


@pytest.mark.asyncio
async def test_get_nonexistent_user():
    assert await db.get_user_by_username("nobody") is None
    assert await db.get_user_by_id(99999) is None


@pytest.mark.asyncio
async def test_save_and_load_run():
    uid = await db.create_user("bob", "hashed")
    data = {"status": "success", "run_meta": {"run_id": "run-1"}}
    await db.save_run("run-1", uid, data, status="success", subject="calculus", problem_text="x^2")

    loaded = await db.load_run("run-1", uid)
    assert loaded is not None
    assert loaded["data"]["status"] == "success"
    assert loaded["status"] == "success"
    assert loaded["subject"] == "calculus"


@pytest.mark.asyncio
async def test_update_run():
    uid = await db.create_user("charlie", "hashed")
    await db.save_run("run-2", uid, {"step": 1}, status="running")
    await db.update_run("run-2", {"step": 2}, status="success")

    loaded = await db.load_run("run-2", uid)
    assert loaded["data"]["step"] == 2
    assert loaded["status"] == "success"


@pytest.mark.asyncio
async def test_user_isolation():
    uid_a = await db.create_user("user_a", "h")
    uid_b = await db.create_user("user_b", "h")

    await db.save_run("iso-run", uid_a, {"owner": "a"}, status="success")
    assert await db.load_run("iso-run", uid_a) is not None
    assert await db.load_run("iso-run", uid_b) is None


@pytest.mark.asyncio
async def test_list_runs_filtering():
    uid = await db.create_user("filter_user", "h")
    await db.save_run("r1", uid, {"s": 1}, status="success", subject="calculus")
    await db.save_run("r2", uid, {"s": 2}, status="running", subject="linear_algebra")
    await db.save_run("r3", uid, {"s": 3}, status="success", subject="calculus")

    all_runs = await db.list_runs_db(uid)
    assert all_runs["total"] == 3

    calc_runs = await db.list_runs_db(uid, subject="calculus")
    assert calc_runs["total"] == 2

    success_runs = await db.list_runs_db(uid, status="success")
    assert success_runs["total"] == 2


@pytest.mark.asyncio
async def test_delete_runs():
    uid = await db.create_user("del_user", "h")
    await db.save_run("d1", uid, {}, status="success")
    await db.save_run("d2", uid, {}, status="success")

    deleted = await db.delete_runs_db(uid, ["d1"])
    assert deleted == 1
    assert await db.load_run("d1", uid) is None
    assert await db.load_run("d2", uid) is not None


@pytest.mark.asyncio
async def test_save_and_load_chat():
    uid = await db.create_user("chat_user", "h")
    msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    await db.save_chat("run-chat", uid, msgs)

    loaded = await db.load_chat("run-chat", uid)
    assert len(loaded) == 2
    assert loaded[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_save_and_load_report():
    uid = await db.create_user("report_user", "h")
    data = {"report_id": "rep-1", "sections": [{"title": "test"}]}
    await db.save_report("rep-1", uid, data)

    loaded = await db.load_report("rep-1", uid)
    assert loaded is not None
    assert loaded["data"]["report_id"] == "rep-1"


@pytest.mark.asyncio
async def test_settings_crud():
    uid = await db.create_user("settings_user", "h")

    empty = await db.get_settings(uid)
    assert empty == {}

    await db.save_settings(uid, {"theme": "dark", "defaultDepth": "thorough"})
    loaded = await db.get_settings(uid)
    assert loaded["theme"] == "dark"


@pytest.mark.asyncio
async def test_mastery_crud():
    uid = await db.create_user("mastery_user", "h")

    default = await db.get_mastery(uid)
    assert default == {"errors": {}, "practice_history": []}

    data = {"errors": {"SIGN_ERROR": {"total": 3, "mastered": False}}, "practice_history": []}
    await db.save_mastery(uid, data)

    loaded = await db.get_mastery(uid)
    assert loaded["errors"]["SIGN_ERROR"]["total"] == 3
