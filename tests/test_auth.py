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
