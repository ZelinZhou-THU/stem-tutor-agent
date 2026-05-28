from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

for candidate in [
    Path(__file__).resolve().parent.parent / "key.env",
    Path(__file__).resolve().parent.parent.parent / "key.env",
    Path(__file__).resolve().parent.parent.parent.parent / "key.env",
]:
    if candidate.exists():
        load_dotenv(dotenv_path=candidate)
        break

from bcrypt import checkpw, gensalt, hashpw
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from web.database import get_user_by_id

SECRET_KEY = os.environ.get("STEM_TUTOR_JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError(
        "STEM_TUTOR_JWT_SECRET environment variable is not set. "
        "Set it to a secure random value before starting the server:\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )
if SECRET_KEY in ("change-me-to-a-random-secret", "change-me", "secret", "password", "123456"):
    raise RuntimeError(
        "STEM_TUTOR_JWT_SECRET is set to a weak/default value. "
        "Please use a secure random value:\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )
if len(SECRET_KEY) < 32:
    raise RuntimeError(
        "STEM_TUTOR_JWT_SECRET must be at least 32 characters long. "
        "Please use a secure random value:\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

_bearer = HTTPBearer()


def hash_password(password: str) -> str:
    return hashpw(password.encode("utf-8"), gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: int, username: str, is_admin: bool = False, restricted: bool = False) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "admin": is_admin, "exp": expire, "iat": now, "restricted": restricted}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer), allow_restricted: bool = False) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", 0))
        is_restricted = payload.get("restricted", False)
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if is_restricted and not allow_restricted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要修改密码后才能访问")
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号未激活")
    return user


async def get_current_user_allow_restricted(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    return await get_current_user(credentials=credentials, allow_restricted=True)


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user
