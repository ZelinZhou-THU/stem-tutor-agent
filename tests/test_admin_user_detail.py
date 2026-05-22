from __future__ import annotations

import json

import pytest

import asyncio
from httpx import AsyncClient, ASGITransport


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


def _make_admin_token(user_id=1, username="admin"):
    from web.auth import create_access_token
    return create_access_token(user_id, username, is_admin=True)


def _make_user_token(user_id=2, username="normal"):
    from web.auth import create_access_token
    return create_access_token(user_id, username, is_admin=False)


def admin_headers():
    token = _make_admin_token()
    return {"Authorization": "Bearer " + token}


def user_headers():
    token = _make_user_token()
    return {"Authorization": "Bearer " + token}


async def _ensure_admin_user():
    from web.database import _ensure_db, create_user
    await _ensure_db()
    await create_user("admin", "h", is_admin=True)


async def _ensure_normal_user():
    from web.database import _ensure_db, create_user
    await _ensure_db()
    return await create_user("normal", "h", is_admin=False)


@pytest.mark.asyncio
async def test_admin_get_user_info(tmp_db):
    from web.database import _ensure_db, create_user, save_mastery
    from web.app import app

    await _ensure_admin_user()
    uid = await create_user("target_user", "hashed_pw")
    await save_mastery(uid, {"errors": {"calculus": 3}, "practice_history": []})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/admin/users/{uid}", headers=admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["username"] == "target_user"
    assert "settings" in data
    assert "mastery" in data
    assert data["mastery"]["errors"]["calculus"] == 3


@pytest.mark.asyncio
async def test_admin_get_user_info_not_found(tmp_db):
    from web.app import app

    await _ensure_admin_user()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/admin/users/9999", headers=admin_headers())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_get_user_runs(tmp_db):
    from web.database import _ensure_db, create_user, save_run
    from web.app import app

    await _ensure_admin_user()
    uid = await create_user("runuser", "h")
    await save_run("run1", uid, {
        "run_meta": {"run_id": "run1", "subject_id": "calculus"},
        "user_status": "complete",
        "raw_output": {"problem_input": {"problem_text": "求导 x^2"}},
    }, status="success", subject="calculus", problem_text="求导 x^2")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/admin/users/{uid}/runs", headers=admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(r["run_id"] == "run1" for r in data["runs"])


@pytest.mark.asyncio
async def test_admin_get_user_reports(tmp_db):
    from web.database import _ensure_db, create_user, save_report
    from web.app import app

    await _ensure_admin_user()
    uid = await create_user("repuser", "h")
    await save_report("rep1", uid, {"title": "测试报告", "content": "abc"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/admin/users/{uid}/reports", headers=admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["title"] == "测试报告"


@pytest.mark.asyncio
async def test_admin_get_user_chats(tmp_db):
    from web.database import _ensure_db, create_user, save_run, save_chat
    from web.app import app

    await _ensure_admin_user()
    uid = await create_user("chatuser", "h")
    await save_run("cr1", uid, {}, status="success")
    await save_chat("cr1", uid, [{"role": "user", "content": "你好"}])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/admin/users/{uid}/chats", headers=admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["run_id"] == "cr1"


@pytest.mark.asyncio
async def test_admin_get_run_detail(tmp_db):
    from web.database import _ensure_db, create_user, save_run
    from web.app import app

    await _ensure_admin_user()
    uid = await create_user("detailuser", "h")
    await save_run("det1", uid, {
        "run_meta": {"run_id": "det1"},
        "user_status": "complete",
        "raw_output": {"problem_input": {"problem_text": "test"}},
    }, status="success")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/admin/users/{uid}/run/det1", headers=admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_meta"]["run_id"] == "det1"


@pytest.mark.asyncio
async def test_admin_endpoints_require_admin(tmp_db):
    from web.database import _ensure_db, create_user
    from web.app import app

    await _ensure_admin_user()
    uid = await _ensure_normal_user()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for path in [
            f"/api/admin/users/{uid}",
            f"/api/admin/users/{uid}/runs",
            f"/api/admin/users/{uid}/reports",
            f"/api/admin/users/{uid}/chats",
            f"/api/admin/users/{uid}/settings",
            f"/api/admin/users/{uid}/mastery",
        ]:
            resp = await ac.get(path, headers=user_headers())
            assert resp.status_code == 403, f"{path} should return 403 for non-admin"
