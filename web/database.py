from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

USE_PG = bool(os.environ.get("DATABASE_URL"))
PG_URL = os.environ.get("DATABASE_URL", "")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "stem_tutor.db"

BEIJING_TZ = timezone(timedelta(hours=8))


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
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

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS batch_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    problem_text TEXT NOT NULL DEFAULT '',
    student_solution TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'text',
    run_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_batch_items_batch ON batch_items(batch_id, seq);
CREATE INDEX IF NOT EXISTS idx_batches_user ON batches(user_id, created_at DESC);
"""

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',
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

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS batch_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    problem_text TEXT NOT NULL DEFAULT '',
    student_solution TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'text',
    run_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_batch_items_batch ON batch_items(batch_id, seq);
CREATE INDEX IF NOT EXISTS idx_batches_user ON batches(user_id, created_at DESC);
"""

_initialized = False
_pg_pool = None


def _pg_convert_replace(sql: str) -> str:
    m = re.match(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
        sql, re.IGNORECASE,
    )
    if not m:
        return sql
    cols = [c.strip() for c in m.group(2).split(",")]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    return f"INSERT INTO {m.group(1)} ({m.group(2)}) VALUES ({m.group(3)}) ON CONFLICT ({cols[0]}) DO UPDATE SET {set_clause}"


def _pg_convert(sql: str) -> str:
    i = [0]

    def _repl(m):
        i[0] += 1
        return f"${i[0]}"

    return re.sub(r"\?", _repl, sql)


def _stmt_type(sql: str) -> str:
    s = sql.strip().split(None, 1)
    return s[0].upper() if s else ""


async def _ensure_db() -> None:
    global _initialized
    if _initialized:
        return
    if USE_PG and PG_URL:
        import asyncpg
        conn = await asyncpg.connect(PG_URL)
        try:
            await conn.execute(_PG_SCHEMA)
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='users'"
            )
            col_names = [r["column_name"] for r in cols]
            if "status" not in col_names:
                await conn.execute(
                    "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
                )
        finally:
            await conn.close()
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.executescript(_SQLITE_SCHEMA)
            cols = await db.execute("PRAGMA table_info(users)")
            col_names = [r[1] for r in await cols.fetchall()]
            if "status" not in col_names:
                await db.execute(
                    "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
                )
                await db.commit()
    _initialized = True


class _DB:
    """统一包装 aiosqlite 和 asyncpg 的数据库连接。"""

    def __init__(self, raw_conn):
        self._raw = raw_conn
        self._is_pg = type(raw_conn).__module__.startswith("asyncpg")
        self._pg_stmt = None
        self._pg_sql = None
        self._pg_params = None
        self._sqlite_cur = None
        self._rowcount = 0
        self._lastrowid = None

    async def execute(self, sql: str, params=None) -> _DB:
        if self._is_pg:
            pg_sql = _pg_convert_replace(sql)
            pg_sql = _pg_convert(pg_sql)
            stmt = _stmt_type(sql)
            self._pg_stmt = stmt
            if stmt in ("SELECT", "WITH", "PRAGMA"):
                self._pg_sql = pg_sql
                self._pg_params = params
            else:
                self._pg_sql = None
                self._pg_params = None
                args = params if isinstance(params, (list, tuple)) else (params,) if params is not None else ()
                result = await self._raw.execute(pg_sql, *args)
                parts = result.split()
                self._rowcount = int(parts[-1]) if len(parts) >= 2 and parts[-1].isdigit() else 0
                if stmt == "INSERT":
                    try:
                        self._lastrowid = await self._raw.fetchval("SELECT LASTVAL()")
                    except Exception:
                        self._lastrowid = None
        else:
            self._sqlite_cur = await self._raw.execute(sql, params or ())
        return self

    async def fetchone(self, sql=None, params=None):
        if sql is not None:
            await self.execute(sql, params)
        if self._is_pg and self._pg_sql:
            args = self._pg_params if isinstance(self._pg_params, (list, tuple)) else (self._pg_params,) if self._pg_params is not None else ()
            row = await self._raw.fetchrow(self._pg_sql, *args)
            self._rowcount = 1 if row else 0
            self._pg_sql = None
            return dict(row) if row else None
        if self._sqlite_cur:
            row = await self._sqlite_cur.fetchone()
            return dict(row) if row else None
        return None

    async def fetchall(self, sql=None, params=None):
        if sql is not None:
            await self.execute(sql, params)
        if self._is_pg and self._pg_sql:
            args = self._pg_params if isinstance(self._pg_params, (list, tuple)) else (self._pg_params,) if self._pg_params is not None else ()
            rows = await self._raw.fetch(self._pg_sql, *args)
            self._rowcount = len(rows)
            self._pg_sql = None
            return [dict(r) for r in rows]
        if self._sqlite_cur:
            rows = await self._sqlite_cur.fetchall()
            return [dict(r) for r in rows]
        return []

    @property
    def rowcount(self) -> int:
        if self._is_pg:
            return self._rowcount
        return self._sqlite_cur.rowcount or 0 if self._sqlite_cur else 0

    @property
    def lastrowid(self):
        if self._is_pg:
            return self._lastrowid
        return self._sqlite_cur.lastrowid if self._sqlite_cur else None

    async def executescript(self, sql: str):
        if self._is_pg:
            for statement in sql.split(";"):
                s = statement.strip()
                if s:
                    await self._raw.execute(s)
        else:
            await self._raw.executescript(sql)

    async def commit(self):
        if not self._is_pg:
            await self._raw.commit()

    async def close(self):
        await self._raw.close()


async def get_db() -> _DB:
    """获取数据库连接——自动选择 SQLite（本地）或 PostgreSQL（Vercel）。"""
    await _ensure_db()
    if USE_PG and PG_URL:
        import asyncpg
        conn = await asyncpg.connect(PG_URL)
    else:
        conn = await aiosqlite.connect(str(DB_PATH))
        conn.row_factory = aiosqlite.Row
    return _DB(conn)


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


# ── User CRUD ──────────────────────────────────────────────────────────

async def create_user(username: str, password_hash: str, is_admin: bool = False, status: str = "active") -> int:
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO users (username, password_hash, is_admin, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, bool(is_admin) if USE_PG else int(is_admin), status, _now_iso()),
        )
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await db.fetchone("SELECT * FROM users WHERE username=?", (username,))
        return row
    finally:
        await db.close()


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))
        return row
    finally:
        await db.close()


async def list_pending_users() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await db.fetchall(
            "SELECT id, username, created_at FROM users WHERE status='pending' ORDER BY created_at ASC"
        )
        return rows
    finally:
        await db.close()


async def approve_user(user_id: int) -> bool:
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE users SET status='active' WHERE id=? AND status='pending'", (user_id,)
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def reject_user(user_id: int) -> bool:
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM users WHERE id=? AND status='pending'", (user_id,)
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def update_password(user_id: int, new_password_hash: str) -> bool:
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE users SET password_hash=? WHERE id=?", (new_password_hash, user_id)
        )
        await db.commit()
        return cur.rowcount > 0
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
        row = await db.fetchone("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user_id))
        if not row:
            return None
        row["data"] = json.loads(row["data"])
        return row
    finally:
        await db.close()


async def load_run_admin(run_id: str) -> dict | None:
    db = await get_db()
    try:
        row = await db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        if not row:
            return None
        row["data"] = json.loads(row["data"])
        return row
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

        row = await db.fetchone(f"SELECT COUNT(*) FROM runs r WHERE {where}", params)
        total = row[list(row.keys())[0]] if row else 0

        offset = (page - 1) * per_page
        rows = await db.fetchall(
            f"SELECT r.* FROM runs r WHERE {where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        )
        runs = []
        for d in rows:
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
        rows = await db.fetchall("SELECT * FROM runs WHERE user_id=? ORDER BY created_at", (user_id,))
        result = []
        for d in rows:
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
        row = await db.fetchone("SELECT messages FROM chats WHERE run_id=? AND user_id=?", (run_id, user_id))
        if not row:
            return []
        return json.loads(row["messages"])
    finally:
        await db.close()


async def list_chats_by_user(user_id: int) -> list[dict]:
    db = await get_db()
    try:
        rows = await db.fetchall(
            "SELECT run_id, user_id, messages, updated_at FROM chats WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
        )
        result = []
        for d in rows:
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
        row = await db.fetchone("SELECT * FROM reports WHERE id=? AND user_id=?", (report_id, user_id))
        if not row:
            return None
        row["data"] = json.loads(row["data"])
        return row
    finally:
        await db.close()


async def list_reports_db(user_id: int) -> list[dict]:
    db = await get_db()
    try:
        rows = await db.fetchall("SELECT * FROM reports WHERE user_id=? ORDER BY created_at DESC", (user_id,))
        result = []
        for d in rows:
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
        row = await db.fetchone("SELECT settings FROM user_settings WHERE user_id=?", (user_id,))
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
        row = await db.fetchone("SELECT data FROM user_mastery WHERE user_id=?", (user_id,))
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


# ── Batch CRUD ──────────────────────────────────────────────────────────

async def create_batch(user_id: int, settings: dict, total_count: int) -> str:
    batch_id = str(uuid4())
    now = _now_iso()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO batches (id, user_id, status, total_count, completed_count, failed_count, settings, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (batch_id, user_id, "pending", total_count, 0, 0, json.dumps(settings, ensure_ascii=False), now, now),
        )
        await db.commit()
        return batch_id
    finally:
        await db.close()


async def load_batch(batch_id: str, user_id: int) -> dict | None:
    db = await get_db()
    try:
        row = await db.fetchone("SELECT * FROM batches WHERE id=? AND user_id=?", (batch_id, user_id))
        return row
    finally:
        await db.close()


async def update_batch_status(batch_id: str, status: str) -> None:
    now = _now_iso()
    db = await get_db()
    try:
        await db.execute("UPDATE batches SET status=?, updated_at=? WHERE id=?", (status, now, batch_id))
        await db.commit()
    finally:
        await db.close()


async def add_batch_items(batch_id: str, items: list[dict]) -> None:
    db = await get_db()
    now = _now_iso()
    try:
        for i, item in enumerate(items):
            item_id = str(uuid4())
            await db.execute(
                "INSERT INTO batch_items (id, batch_id, seq, status, problem_text, student_solution, source_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (item_id, batch_id, i, "pending", item["problem_text"], item.get("student_solution", ""), item.get("source_type", "text"), now, now),
            )
        await db.commit()
    finally:
        await db.close()


async def list_batch_items(batch_id: str) -> list[dict]:
    db = await get_db()
    try:
        rows = await db.fetchall("SELECT * FROM batch_items WHERE batch_id=? ORDER BY seq", (batch_id,))
        return rows
    finally:
        await db.close()


async def update_batch_item(batch_id: str, seq: int, status: str, run_id: str | None = None, error_message: str | None = None) -> None:
    now = _now_iso()
    db = await get_db()
    try:
        await db.execute(
            "UPDATE batch_items SET status=?, run_id=?, error_message=?, updated_at=? WHERE batch_id=? AND seq=?",
            (status, run_id, error_message, now, batch_id, seq),
        )
        if status == "completed":
            await db.execute("UPDATE batches SET completed_count = completed_count + 1, updated_at=? WHERE id=?", (now, batch_id))
        elif status == "failed":
            await db.execute("UPDATE batches SET failed_count = failed_count + 1, updated_at=? WHERE id=?", (now, batch_id))
        done_row = await db.fetchone("SELECT completed_count + failed_count AS done FROM batches WHERE id=?", (batch_id,))
        total_row = await db.fetchone("SELECT total_count FROM batches WHERE id=?", (batch_id,))
        if done_row and total_row and done_row["done"] >= total_row["total_count"]:
            await db.execute("UPDATE batches SET status='completed', updated_at=? WHERE id=?", (now, batch_id))
        await db.commit()
    finally:
        await db.close()


async def claim_next_pending_item(batch_id: str) -> dict | None:
    db = await get_db()
    try:
        row = await db.fetchone(
            "SELECT * FROM batch_items WHERE batch_id=? AND status='pending' ORDER BY seq LIMIT 1",
            (batch_id,),
        )
        if not row:
            return None
        now = _now_iso()
        await db.execute(
            "UPDATE batch_items SET status='running', updated_at=? WHERE batch_id=? AND seq=?",
            (now, batch_id, row["seq"]),
        )
        await db.commit()
        return row
    finally:
        await db.close()


async def list_batches(user_id: int, status: str | None = None, page: int = 1, per_page: int = 20) -> dict:
    db = await get_db()
    try:
        where = "WHERE user_id=?"
        params: list[Any] = [user_id]
        if status:
            where += " AND status=?"
            params.append(status)
        row = await db.fetchone(f"SELECT COUNT(*) AS cnt FROM batches {where}", params)
        total = row["cnt"] if row else 0
        params.extend([per_page, (page - 1) * per_page])
        rows = await db.fetchall(
            f"SELECT * FROM batches {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        return {"batches": rows, "total": total, "page": page, "per_page": per_page}
    finally:
        await db.close()


async def delete_batch(batch_id: str, user_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM batch_items WHERE batch_id=?", (batch_id,))
        await db.execute("DELETE FROM batches WHERE id=? AND user_id=?", (batch_id, user_id))
        await db.commit()
    finally:
        await db.close()


async def recover_stale_running_items() -> int:
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE batch_items SET status='pending', updated_at=? WHERE status='running'",
            (_now_iso(),),
        )
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


async def get_running_batches() -> list[dict]:
    db = await get_db()
    try:
        rows = await db.fetchall(
            "SELECT * FROM batches WHERE status='running' AND completed_count + failed_count < total_count ORDER BY created_at"
        )
        return rows
    finally:
        await db.close()
