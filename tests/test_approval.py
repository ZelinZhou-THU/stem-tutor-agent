from __future__ import annotations

import asyncio
import sys
import os
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from httpx import AsyncClient, ASGITransport

os.environ.setdefault("STEM_TUTOR_JWT_SECRET", "test-secret-key-for-approval-tests")


def _run(coro):
    import web.database as _db_mod
    _db_mod._initialized = False
    asyncio.run(coro)


def _uid():
    return "ap_" + uuid.uuid4().hex[:12]


async def _ensure_admin():
    from web.database import _ensure_db, create_user, get_user_by_username
    from web.auth import hash_password
    await _ensure_db()
    admin = await get_user_by_username("admin")
    if not admin:
        await create_user("admin", hash_password("admin123"), is_admin=True, status="active")
        admin = await get_user_by_username("admin")
    return admin


def test_register_returns_pending():
    async def _test():
        from web.app import app
        admin = await _ensure_admin()
        transport = ASGITransport(app=app)
        name = _uid()
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/auth/register", json={"username": name, "password": "test1234"})
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("status") == "pending"
            assert "access_token" not in data
            assert "\u7b49\u5f85" in data.get("message", "")
    _run(_test())


def test_pending_user_cannot_login():
    async def _test():
        from web.app import app
        admin = await _ensure_admin()
        transport = ASGITransport(app=app)
        name = _uid()
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post("/api/auth/register", json={"username": name, "password": "test1234"})
            resp = await ac.post("/api/auth/login", json={"username": name, "password": "test1234"})
            assert resp.status_code == 403
            assert "\u5ba1\u6279" in resp.json().get("detail", "")
    _run(_test())


def test_admin_approve_flow():
    async def _test():
        from web.app import app
        from web.auth import create_access_token
        admin = await _ensure_admin()
        transport = ASGITransport(app=app)
        name = _uid()
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            reg = await ac.post("/api/auth/register", json={"username": name, "password": "test1234"})
            assert reg.status_code == 200
            headers = {"Authorization": "Bearer " + create_access_token(admin["id"], "admin", is_admin=True)}
            pending_resp = await ac.get("/api/admin/pending-users", headers=headers)
            pending = pending_resp.json()
            target = [u for u in pending if u["username"] == name]
            assert len(target) == 1
            approve_resp = await ac.post("/api/admin/users/" + str(target[0]["id"]) + "/approve", headers=headers)
            assert approve_resp.status_code == 200
            login_resp = await ac.post("/api/auth/login", json={"username": name, "password": "test1234"})
            assert login_resp.status_code == 200
            assert "access_token" in login_resp.json()
    _run(_test())


def test_admin_reject_deletes_user():
    async def _test():
        from web.app import app
        from web.auth import create_access_token
        admin = await _ensure_admin()
        transport = ASGITransport(app=app)
        name = _uid()
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post("/api/auth/register", json={"username": name, "password": "test1234"})
            headers = {"Authorization": "Bearer " + create_access_token(admin["id"], "admin", is_admin=True)}
            pending_resp = await ac.get("/api/admin/pending-users", headers=headers)
            target = [u for u in pending_resp.json() if u["username"] == name]
            assert len(target) == 1
            reject_resp = await ac.post("/api/admin/users/" + str(target[0]["id"]) + "/reject", headers=headers)
            assert reject_resp.status_code == 200
            rereg_resp = await ac.post("/api/auth/register", json={"username": name, "password": "newpass"})
            assert rereg_resp.status_code == 200
    _run(_test())


def test_stats_include_pending_count():
    async def _test():
        from web.app import app
        from web.auth import create_access_token
        admin = await _ensure_admin()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": "Bearer " + create_access_token(admin["id"], "admin", is_admin=True)}
            resp = await ac.get("/api/admin/stats", headers=headers)
            data = resp.json()
            assert "pending_count" in data
            assert isinstance(data["pending_count"], int)
    _run(_test())
