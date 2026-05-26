from __future__ import annotations

import pytest

from web.auth import hash_password, verify_password, create_access_token, SECRET_KEY, ALGORITHM
from jose import jwt, JWTError


def test_hash_and_verify_password():
    hashed = hash_password("mypassword")
    assert isinstance(hashed, str)
    assert verify_password("mypassword", hashed) is True


def test_wrong_password_rejected():
    hashed = hash_password("correct")
    assert verify_password("wrong", hashed) is False


def test_create_and_decode_token():
    token = create_access_token(42, "testuser", is_admin=False)
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "42"
    assert payload["username"] == "testuser"
    assert payload["admin"] is False


def test_expired_token_rejected():
    from datetime import datetime, timedelta, timezone
    expire = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {"sub": "1", "username": "expired", "exp": expire}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(JWTError):
        jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def test_admin_flag_in_token():
    token = create_access_token(1, "admin", is_admin=True)
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["admin"] is True


import asyncio
import uuid
import web.database as _db_mod
from web.database import _ensure_db, create_user, get_user_by_id, get_user_by_username, list_pending_users, approve_user, reject_user


def _run(coro):
    _db_mod._initialized = False
    asyncio.run(coro)


def _uid():
    return "t_" + uuid.uuid4().hex[:12]


def test_create_user_with_pending_status():
    async def _test():
        await _ensure_db()
        uid = await create_user(_uid(), "fakehash", status="pending")
        user = await get_user_by_id(uid)
        assert user is not None
        assert user["status"] == "pending"
    _run(_test())


def test_list_pending_users():
    async def _test():
        await _ensure_db()
        name = _uid()
        uid = await create_user(name, "fakehash", status="pending")
        pending = await list_pending_users()
        assert any(u["id"] == uid for u in pending)
    _run(_test())


def test_approve_user():
    async def _test():
        await _ensure_db()
        uid = await create_user(_uid(), "fakehash", status="pending")
        ok = await approve_user(uid)
        assert ok is True
        user = await get_user_by_id(uid)
        assert user["status"] == "active"
    _run(_test())


def test_reject_user_deletes():
    async def _test():
        await _ensure_db()
        uid = await create_user(_uid(), "fakehash", status="pending")
        ok = await reject_user(uid)
        assert ok is True
        user = await get_user_by_id(uid)
        assert user is None
    _run(_test())


def test_reject_allows_reregister():
    async def _test():
        await _ensure_db()
        name = _uid()
        uid = await create_user(name, "fakehash", status="pending")
        await reject_user(uid)
        uid2 = await create_user(name, "newhash", status="pending")
        assert uid2 is not None
        user = await get_user_by_username(name)
        assert user["password_hash"] == "newhash"
    _run(_test())


def test_update_password():
    async def _test():
        from web.database import update_password
        from web.auth import hash_password, verify_password
        await _ensure_db()
        uid = await create_user(_uid(), hash_password("oldpass"), status="active")
        new_hash = hash_password("newpass")
        ok = await update_password(uid, new_hash)
        assert ok is True
        user = await get_user_by_id(uid)
        assert verify_password("newpass", user["password_hash"]) is True
        assert verify_password("oldpass", user["password_hash"]) is False
    _run(_test())
