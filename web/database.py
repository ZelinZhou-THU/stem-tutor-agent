from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "stem_tutor.db"

BEIJING_TZ = timezone(timedelta(hours=8))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    subject TEXT,
    problem_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS chats (
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    messages TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    data TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    settings TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_mastery (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{"errors":{},"practice_history":[]}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at DESC);
"""

_initialized = False


async def _ensure_db() -> None:
    global _initialized
    if _initialized:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
    _initialized = True


async def get_db() -> aiosqlite.Connection:
    await _ensure_db()
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    return db


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


# ── User CRUD ──────────────────────────────────────────────────────────

async def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, int(is_admin), _now_iso()),
        )
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM users WHERE username=?", (username,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ── Run CRUD ───────────────────────────────────────────────────────────

async def save_run(
    run_id: str,
    user_id: int,
    data: dict,
    status: str = "running",
    subject: str | None = None,
    problem_text: str | None = None,
) -> None:
    now = _now_iso()
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO runs (id, user_id, data, status, subject, problem_text, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, user_id, json.dumps(data, ensure_ascii=False), status, subject, problem_text, now, now),
        )
        await db.commit()
    finally:
        await db.close()


async def update_run(run_id: str, data: dict, status: str | None = None) -> None:
    now = _now_iso()
    db = await get_db()
    try:
        if status is not None:
            await db.execute(
                "UPDATE runs SET data=?, status=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), status, now, run_id),
            )
        else:
            await db.execute(
                "UPDATE runs SET data=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), now, run_id),
            )
        await db.commit()
    finally:
        await db.close()


async def load_run(run_id: str, user_id: int) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user_id))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d
    finally:
        await db.close()


async def load_run_admin(run_id: str) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM runs WHERE id=?", (run_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d
    finally:
        await db.close()


async def list_runs_db(
    user_id: int,
    subject: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    db = await get_db()
    try:
        where_clauses = ["r.user_id=?"]
        params: list[Any] = [user_id]
        if subject:
            where_clauses.append("r.subject=?")
            params.append(subject)
        if status:
            where_clauses.append("r.status=?")
            params.append(status)
        if search:
            where_clauses.append("r.problem_text LIKE ?")
            params.append(f"%{search}%")
        where = " AND ".join(where_clauses)

        count_cur = await db.execute(f"SELECT COUNT(*) FROM runs r WHERE {where}", params)
        total = (await count_cur.fetchone())[0]

        offset = (page - 1) * per_page
        data_cur = await db.execute(
            f"SELECT r.* FROM runs r WHERE {where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        )
        rows = await data_cur.fetchall()
        runs = []
        for row in rows:
            d = dict(row)
            d["data"] = json.loads(d["data"])
            runs.append(d)
        return {"runs": runs, "total": total, "page": page, "per_page": per_page}
    finally:
        await db.close()


async def delete_runs_db(user_id: int, run_ids: list[str]) -> int:
    db = await get_db()
    try:
        placeholders = ",".join("?" for _ in run_ids)
        cur = await db.execute(
            f"DELETE FROM runs WHERE user_id=? AND id IN ({placeholders})",
            [user_id] + run_ids,
        )
        await db.execute(
            f"DELETE FROM chats WHERE user_id=? AND run_id IN ({placeholders})",
            [user_id] + run_ids,
        )
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


async def cleanup_runs_db(user_id: int, before_iso: str) -> int:
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM runs WHERE user_id=? AND updated_at<?", (user_id, before_iso))
        await db.execute("DELETE FROM chats WHERE user_id=? AND updated_at<?", (user_id, before_iso))
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


async def get_all_runs_for_stats(user_id: int) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM runs WHERE user_id=? ORDER BY created_at", (user_id,))
        rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["data"] = json.loads(d["data"])
            result.append(d)
        return result
    finally:
        await db.close()


# ── Chat CRUD ──────────────────────────────────────────────────────────

async def save_chat(run_id: str, user_id: int, messages: list) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO chats (run_id, user_id, messages, updated_at) VALUES (?,?,?,?)",
            (run_id, user_id, json.dumps(messages, ensure_ascii=False), _now_iso()),
        )
        await db.commit()
    finally:
        await db.close()


async def load_chat(run_id: str, user_id: int) -> list:
    db = await get_db()
    try:
        cur = await db.execute("SELECT messages FROM chats WHERE run_id=? AND user_id=?", (run_id, user_id))
        row = await cur.fetchone()
        if not row:
            return []
        return json.loads(row["messages"])
    finally:
        await db.close()


async def list_chats_by_user(user_id: int) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT run_id, user_id, messages, updated_at FROM chats WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["messages"] = json.loads(d["messages"])
            result.append(d)
        return result
    finally:
        await db.close()


# ── Report CRUD ────────────────────────────────────────────────────────

async def save_report(report_id: str, user_id: int, data: dict) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO reports (id, user_id, data, title, created_at) VALUES (?,?,?,?,?)",
            (report_id, user_id, json.dumps(data, ensure_ascii=False), data.get("title", ""), _now_iso()),
        )
        await db.commit()
    finally:
        await db.close()


async def load_report(report_id: str, user_id: int) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM reports WHERE id=? AND user_id=?", (report_id, user_id))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d
    finally:
        await db.close()


async def list_reports_db(user_id: int) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM reports WHERE user_id=? ORDER BY created_at DESC", (user_id,))
        rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["data"] = json.loads(d["data"])
            result.append(d)
        return result
    finally:
        await db.close()


async def delete_report_db(report_id: str, user_id: int) -> bool:
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM reports WHERE id=? AND user_id=?", (report_id, user_id))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


# ── Settings CRUD ──────────────────────────────────────────────────────

async def get_settings(user_id: int) -> dict:
    db = await get_db()
    try:
        cur = await db.execute("SELECT settings FROM user_settings WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return {}
        return json.loads(row["settings"])
    finally:
        await db.close()


async def save_settings(user_id: int, settings: dict) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO user_settings (user_id, settings) VALUES (?,?)",
            (user_id, json.dumps(settings, ensure_ascii=False)),
        )
        await db.commit()
    finally:
        await db.close()


# ── Mastery CRUD ───────────────────────────────────────────────────────

async def get_mastery(user_id: int) -> dict:
    db = await get_db()
    try:
        cur = await db.execute("SELECT data FROM user_mastery WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return {"errors": {}, "practice_history": []}
        return json.loads(row["data"])
    finally:
        await db.close()


async def save_mastery(user_id: int, data: dict) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO user_mastery (user_id, data, updated_at) VALUES (?,?,?)",
            (user_id, json.dumps(data, ensure_ascii=False), _now_iso()),
        )
        await db.commit()
    finally:
        await db.close()
