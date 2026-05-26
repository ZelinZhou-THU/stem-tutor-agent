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
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72

_bearer = HTTPBearer()


def hash_password(password: str) -> str:
    return hashpw(password.encode("utf-8"), gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: int, username: str, is_admin: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "admin": is_admin, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", 0))
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号未激活")
    return user


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user
