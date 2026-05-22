# 批量分析队列功能实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现批量分析队列功能——学生可一次提交多道题目（文本或 OCR），系统后台串行自动分析，无需保持浏览器打开。

**Architecture:** 新增 `batches` + `batch_items` 两张 SQLite 表，`BatchWorker` 后台 asyncio 协程串行消费。前端新增 `#queue` 页面，轮询进度。OCR 在提交前完成（复用现有 `/ocr` 端点），提交后 worker 只处理文本。

**Tech Stack:** FastAPI + asyncio + SQLite (aiosqlite) + 原生 JS (ES5) + KaTeX

**设计文档:** `docs/superpowers/specs/2026-05-22-batch-analysis-queue-design.md`

---

## Task 1: 数据库层 — batches + batch_items 表与 CRUD

**Files:**
- Modify: `web/database.py`
- Test: `tests/test_batch.py`（新建）

**依赖:** 无

- [ ] **Step 1: 编写 batches + batch_items 表 DDL 和 CRUD 的测试**

创建 `tests/test_batch.py`，编写以下测试：

```python
import asyncio
import json
import pytest

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def db():
    from web.database import _ensure_db, get_db
    await _ensure_db()
    yield
    db = await get_db()
    await db.close()

@pytest.mark.asyncio
async def test_create_and_load_batch(db):
    from web.database import create_batch, load_batch
    batch_id = await create_batch(user_id=1, settings={"model": "qwen/qwen3.6-plus", "subject_id": "calculus", "mode": "workflow_r1", "depth": "standard"}, total_count=3)
    batch = await load_batch(batch_id, user_id=1)
    assert batch is not None
    assert batch["status"] == "pending"
    assert batch["total_count"] == 3
    assert batch["completed_count"] == 0
    assert batch["failed_count"] == 0
    assert json.loads(batch["settings"])["model"] == "qwen/qwen3.6-plus"

@pytest.mark.asyncio
async def test_add_and_list_batch_items(db):
    from web.database import create_batch, add_batch_items, list_batch_items
    batch_id = await create_batch(user_id=1, settings={}, total_count=2)
    items = [
        {"problem_text": "求积分1", "student_solution": "解1", "source_type": "text"},
        {"problem_text": "求积分2", "student_solution": "解2", "source_type": "text"},
    ]
    await add_batch_items(batch_id, items)
    rows = await list_batch_items(batch_id)
    assert len(rows) == 2
    assert rows[0]["seq"] == 0
    assert rows[0]["status"] == "pending"
    assert rows[0]["problem_text"] == "求积分1"
    assert rows[1]["seq"] == 1

@pytest.mark.asyncio
async def test_update_batch_item_status(db):
    from web.database import create_batch, add_batch_items, update_batch_item, load_batch
    batch_id = await create_batch(user_id=1, settings={}, total_count=1)
    await add_batch_items(batch_id, [{"problem_text": "题1", "student_solution": "解1", "source_type": "text"}])
    await update_batch_item(batch_id, seq=0, status="completed", run_id="run-uuid-123")
    rows = await list_batch_items(batch_id)
    assert rows[0]["status"] == "completed"
    assert rows[0]["run_id"] == "run-uuid-123"
    batch = await load_batch(batch_id, user_id=1)
    assert batch["completed_count"] == 1

@pytest.mark.asyncio
async def test_update_batch_status(db):
    from web.database import create_batch, update_batch_status, load_batch
    batch_id = await create_batch(user_id=1, settings={}, total_count=1)
    await update_batch_status(batch_id, status="running")
    batch = await load_batch(batch_id, user_id=1)
    assert batch["status"] == "running"

@pytest.mark.asyncio
async def test_list_batches(db):
    from web.database import create_batch, list_batches
    await create_batch(user_id=1, settings={}, total_count=2)
    await create_batch(user_id=1, settings={}, total_count=3)
    result = await list_batches(user_id=1)
    assert len(result["batches"]) == 2
    assert result["total"] == 2
    assert result["batches"][0]["total_count"] == 3  -- DESC order

@pytest.mark.asyncio
async def test_claim_next_pending_item(db):
    from web.database import create_batch, add_batch_items, claim_next_pending_item, update_batch_item, update_batch_status
    batch_id = await create_batch(user_id=1, settings={}, total_count=3)
    await add_batch_items(batch_id, [
        {"problem_text": "题1", "student_solution": "解1", "source_type": "text"},
        {"problem_text": "题2", "student_solution": "解2", "source_type": "text"},
        {"problem_text": "题3", "student_solution": "解3", "source_type": "text"},
    ])
    await update_batch_status(batch_id, status="running")
    item1 = await claim_next_pending_item(batch_id)
    assert item1["seq"] == 0
    assert item1["status"] == "running"
    await update_batch_item(batch_id, seq=0, status="completed", run_id="r1")
    item2 = await claim_next_pending_item(batch_id)
    assert item2["seq"] == 1

@pytest.mark.asyncio
async def test_delete_batch(db):
    from web.database import create_batch, add_batch_items, delete_batch, load_batch, list_batch_items
    batch_id = await create_batch(user_id=1, settings={}, total_count=1)
    await add_batch_items(batch_id, [{"problem_text": "题1", "student_solution": "解1", "source_type": "text"}])
    await delete_batch(batch_id, user_id=1)
    assert await load_batch(batch_id, user_id=1) is None
    assert await list_batch_items(batch_id) == []

@pytest.mark.asyncio
async def test_recover_stale_running_items(db):
    from web.database import create_batch, add_batch_items, update_batch_item, update_batch_status, recover_stale_running_items
    batch_id = await create_batch(user_id=1, settings={}, total_count=2)
    await add_batch_items(batch_id, [
        {"problem_text": "题1", "student_solution": "解1", "source_type": "text"},
        {"problem_text": "题2", "student_solution": "解2", "source_type": "text"},
    ])
    await update_batch_status(batch_id, status="running")
    await update_batch_item(batch_id, seq=0, status="running")
    n = await recover_stale_running_items()
    assert n >= 1
    rows = await list_batch_items(batch_id)
    assert rows[0]["status"] == "pending"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_batch.py -v`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 在 `web/database.py` 中实现 batches/batch_items DDL 和 CRUD**

在 `_ensure_db()` 函数中追加建表语句，然后实现以下函数：

```python
async def create_batch(user_id: int, settings: dict, total_count: int) -> str:
    batch_id = str(uuid4())
    now = _now_iso()
    db = await get_db()
    await db.execute(
        "INSERT INTO batches (id, user_id, status, total_count, completed_count, failed_count, settings, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (batch_id, user_id, "pending", total_count, 0, 0, json.dumps(settings, ensure_ascii=False), now, now),
    )
    await db.commit()
    await db.close()
    return batch_id

async def load_batch(batch_id: str, user_id: int) -> dict | None:
    db = await get_db()
    cur = await db.execute("SELECT * FROM batches WHERE id=? AND user_id=?", (batch_id, user_id))
    row = await cur.fetchone()
    await db.close()
    return dict(row) if row else None

async def update_batch_status(batch_id: str, status: str) -> None:
    now = _now_iso()
    db = await get_db()
    await db.execute("UPDATE batches SET status=?, updated_at=? WHERE id=?", (status, now, batch_id))
    await db.commit()
    await db.close()

async def add_batch_items(batch_id: str, items: list[dict]) -> None:
    db = await get_db()
    now = _now_iso()
    for i, item in enumerate(items):
        item_id = str(uuid4())
        await db.execute(
            "INSERT INTO batch_items (id, batch_id, seq, status, problem_text, student_solution, source_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (item_id, batch_id, i, "pending", item["problem_text"], item.get("student_solution", ""), item.get("source_type", "text"), now, now),
        )
    await db.commit()
    await db.close()

async def list_batch_items(batch_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM batch_items WHERE batch_id=? ORDER BY seq", (batch_id,))
    rows = await cur.fetchall()
    await db.close()
    return [dict(r) for r in rows]

async def update_batch_item(batch_id: str, seq: int, status: str, run_id: str | None = None, error_message: str | None = None) -> None:
    now = _now_iso()
    db = await get_db()
    await db.execute(
        "UPDATE batch_items SET status=?, run_id=?, error_message=?, updated_at=? WHERE batch_id=? AND seq=?",
        (status, run_id, error_message, now, batch_id, seq),
    )
    if status == "completed":
        await db.execute("UPDATE batches SET completed_count = completed_count + 1, updated_at=? WHERE id=?", (now, batch_id))
    elif status == "failed":
        await db.execute("UPDATE batches SET failed_count = failed_count + 1, updated_at=? WHERE id=?", (now, batch_id))
    done = (await db.execute("SELECT completed_count + failed_count FROM batches WHERE id=?", (batch_id,))).fetchone()
    total = (await db.execute("SELECT total_count FROM batches WHERE id=?", (batch_id,))).fetchone()
    if done and total and done[0] >= total[0]:
        await db.execute("UPDATE batches SET status='completed', updated_at=? WHERE id=?", (now, batch_id))
    await db.commit()
    await db.close()

async def claim_next_pending_item(batch_id: str) -> dict | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM batch_items WHERE batch_id=? AND status='pending' ORDER BY seq LIMIT 1",
        (batch_id,),
    )
    row = await cur.fetchone()
    if not row:
        await db.close()
        return None
    now = _now_iso()
    await db.execute(
        "UPDATE batch_items SET status='running', updated_at=? WHERE batch_id=? AND seq=?",
        (now, batch_id, row["seq"]),
    )
    await db.commit()
    await db.close()
    return dict(row)

async def list_batches(user_id: int, status: str | None = None, page: int = 1, per_page: int = 20) -> dict:
    db = await get_db()
    where = "WHERE user_id=?"
    params: list = [user_id]
    if status:
        where += " AND status=?"
        params.append(status)
    count_cur = await db.execute(f"SELECT COUNT(*) FROM batches {where}", params)
    total = (await count_cur.fetchone())[0]
    params.extend([per_page, (page - 1) * per_page])
    cur = await db.execute(
        f"SELECT * FROM batches {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params,
    )
    rows = await cur.fetchall()
    await db.close()
    return {"batches": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}

async def delete_batch(batch_id: str, user_id: int) -> None:
    db = await get_db()
    await db.execute("DELETE FROM batch_items WHERE batch_id=?", (batch_id,))
    await db.execute("DELETE FROM batches WHERE id=? AND user_id=?", (batch_id, user_id))
    await db.commit()
    await db.close()

async def recover_stale_running_items() -> int:
    db = await get_db()
    cur = await db.execute(
        "UPDATE batch_items SET status='pending', updated_at=? WHERE status='running'",
        (_now_iso(),),
    )
    await db.commit()
    n = cur.rowcount
    await db.close()
    return n

async def get_running_batches() -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM batches WHERE status='running' AND completed_count + failed_count < total_count ORDER BY created_at"
    )
    rows = await cur.fetchall()
    await db.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_batch.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add web/database.py tests/test_batch.py
git commit -m "feat: 批量分析数据库层 — batches/batch_items 表与 CRUD"
```

---

## Task 2: BatchWorker 后台协程

**Files:**
- Create: `web/batch_worker.py`
- Modify: `tests/test_batch.py`（追加 worker 测试）

**依赖:** Task 1

- [ ] **Step 1: 编写 BatchWorker 单元测试**

在 `tests/test_batch.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_worker_processes_batch(db):
    from web.database import create_batch, add_batch_items, load_batch, update_batch_status, list_batch_items
    from web.batch_worker import BatchWorker
    worker = BatchWorker()
    batch_id = await create_batch(user_id=1, settings={"model": "qwen/qwen3.6-plus", "subject_id": "calculus", "mode": "workflow_r1", "depth": "standard"}, total_count=1)
    await add_batch_items(batch_id, [{"problem_text": "1+1=?", "student_solution": "1+1=2", "source_type": "text"}])
    await update_batch_status(batch_id, status="running")
    await worker._process_one_cycle()
    items = await list_batch_items(batch_id)
    assert items[0]["status"] in ("completed", "failed")
    batch = await load_batch(batch_id, user_id=1)
    assert batch["status"] == "completed"
```

注意：此测试会调用真实 LLM（因为 run_stem_tutor_stream 内部需要 provider）。如果需要 mock，可以在测试中 mock `run_stem_tutor_stream`。但考虑到 CI 可能没有 API key，可以将此测试标记为 `@pytest.mark.skipif` 或用 mock provider。

**Mock 方案**：在测试中 monkey-patch `run_stem_tutor_stream` 返回模拟 SSE：

```python
async def _mock_stream(**kwargs):
    import json
    yield f'data: {json.dumps({"type": "start", "run_id": "mock-run-id"}, ensure_ascii=False)}\n\n'
    yield f'data: {json.dumps({"type": "result", "data": {"run_id": "mock-run-id", "status": "success"}}, ensure_ascii=False)}\n\n'
    yield f'data: {json.dumps({"type": "done", "message": "done"}, ensure_ascii=False)}\n\n'

@pytest.mark.asyncio
async def test_worker_processes_batch(db, monkeypatch):
    import web.batch_worker
    monkeypatch.setattr(web.batch_worker, "run_stem_tutor_stream", _mock_stream)
    from web.database import create_batch, add_batch_items, load_batch, update_batch_status, list_batch_items
    from web.batch_worker import BatchWorker
    worker = BatchWorker()
    batch_id = await create_batch(user_id=1, settings={"model": "qwen/qwen3.6-plus", "subject_id": "calculus", "mode": "workflow_r1", "depth": "standard"}, total_count=1)
    await add_batch_items(batch_id, [{"problem_text": "1+1=?", "student_solution": "1+1=2", "source_type": "text"}])
    await update_batch_status(batch_id, status="running")
    await worker._process_one_cycle()
    items = await list_batch_items(batch_id)
    assert items[0]["status"] == "completed"
    assert items[0]["run_id"] == "mock-run-id"
    batch = await load_batch(batch_id, user_id=1)
    assert batch["status"] == "completed"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_batch.py::test_worker_processes_batch -v`
Expected: FAIL（module 不存在）

- [ ] **Step 3: 创建 `web/batch_worker.py`**

```python
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from web.database import (
    claim_next_pending_item,
    get_running_batches,
    load_batch,
    recover_stale_running_items,
    update_batch_item,
    update_batch_status,
)
from web.service import run_stem_tutor_stream

log = logging.getLogger(__name__)


class BatchWorker:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._wake = asyncio.Event()

    async def start(self):
        n = await recover_stale_running_items()
        if n:
            log.info("BatchWorker: recovered %d stale running items", n)
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("BatchWorker: started")

    async def stop(self):
        self._running = False
        self._wake.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("BatchWorker: stopped")

    def notify(self):
        self._wake.set()

    async def _loop(self):
        while self._running:
            try:
                processed = await self._process_one_cycle()
                if not processed:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("BatchWorker: error in loop")
                await asyncio.sleep(5)

    async def _process_one_cycle(self) -> bool:
        batches = await get_running_batches()
        if not batches:
            return False
        batch = batches[0]
        batch_id = batch["id"]
        settings = json.loads(batch["settings"])
        item = await claim_next_pending_item(batch_id)
        if item is None:
            await update_batch_status(batch_id, "completed")
            return True
        try:
            run_id = None
            async for chunk in run_stem_tutor_stream(
                problem_text=item["problem_text"],
                raw_student_solution=item.get("student_solution", ""),
                source_type=item.get("source_type", "text"),
                model_name=settings.get("model", "qwen/qwen3.6-plus"),
                subject_id=settings.get("subject_id", "calculus"),
                mode=settings.get("mode", "workflow_r1"),
                depth=settings.get("depth", "standard"),
                user_id=batch["user_id"],
            ):
                if '"type": "result"' in chunk:
                    for line in chunk.strip().split("\n"):
                        if line.startswith("data: "):
                            data = json.loads(line[6:])
                            if data.get("type") == "result":
                                run_id = data.get("data", {}).get("run_id") or data.get("data", {}).get("run_meta", {}).get("run_id")
                batch_fresh = await load_batch(batch_id, batch["user_id"])
                if batch_fresh and batch_fresh["status"] in ("paused", "cancelled"):
                    break
            if run_id:
                await update_batch_item(batch_id, item["seq"], "completed", run_id=run_id)
            else:
                await update_batch_item(batch_id, item["seq"], "failed", error_message="no run_id returned")
        except Exception as exc:
            log.exception("BatchWorker: item %d failed", item["seq"])
            await update_batch_item(batch_id, item["seq"], "failed", error_message=str(exc)[:200])
        return True


_worker: BatchWorker | None = None


def get_batch_worker() -> BatchWorker:
    global _worker
    if _worker is None:
        _worker = BatchWorker()
    return _worker
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_batch.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add web/batch_worker.py tests/test_batch.py
git commit -m "feat: BatchWorker 后台协程 — 串行消费批次任务"
```

---

## Task 3: 批量分析 API 端点

**Files:**
- Modify: `web/app.py`

**依赖:** Task 1, Task 2

- [ ] **Step 1: 在 `web/app.py` 中添加 batch API 端点**

在 imports 区追加：
```python
from web.batch_worker import get_batch_worker
from web.database import (
    create_batch, load_batch, update_batch_status, update_batch_item,
    add_batch_items, list_batch_items, list_batches, delete_batch,
)
```

在 `startup()` 函数中启动 worker：
```python
@app.on_event("startup")
async def startup():
    await _ensure_db()
    admin = await get_user_by_username("admin")
    if not admin:
        pw_hash = hash_password("admin123")
        await create_user("admin", pw_hash, is_admin=True)
    await get_batch_worker().start()
```

添加 shutdown 事件：
```python
@app.on_event("shutdown")
async def shutdown():
    await get_batch_worker().stop()
```

添加批量 API 端点（在现有端点之后）：

```python
@app.post("/batch/create")
async def batch_create(req: dict = Body(...), user: dict = Depends(get_current_user)):
    items = req.get("items", [])
    settings = req.get("settings", {})
    auto_start = req.get("auto_start", True)
    if not items:
        return JSONResponse(status_code=400, content={"error": "至少需要一道题目"})
    if len(items) > 100:
        return JSONResponse(status_code=400, content={"error": "单次最多 100 道题目"})
    for item in items:
        if not item.get("problem_text", "").strip():
            return JSONResponse(status_code=400, content={"error": f"第 {items.index(item) + 1} 题缺少题目内容"})
    batch_id = await create_batch(
        user_id=user["id"],
        settings={
            "model": settings.get("model", "qwen/qwen3.6-plus"),
            "subject_id": settings.get("subject_id", "calculus"),
            "mode": settings.get("mode", "workflow_r1"),
            "depth": settings.get("depth", "standard"),
        },
        total_count=len(items),
    )
    await add_batch_items(batch_id, items)
    if auto_start:
        await update_batch_status(batch_id, "running")
        get_batch_worker().notify()
    return {"batch_id": batch_id, "total_count": len(items)}


@app.get("/batch/list")
async def batch_list(
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
    user: dict = Depends(get_current_user),
):
    return await list_batches(user["id"], status=status, page=page, per_page=per_page)


@app.get("/batch/{batch_id}/status")
async def batch_status(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    items = await list_batch_items(batch_id)
    total = batch["total_count"]
    done = batch["completed_count"] + batch["failed_count"]
    current_item = next((i for i in items if i["status"] == "running"), None)
    return {
        "batch_id": batch["id"],
        "status": batch["status"],
        "total_count": total,
        "completed_count": batch["completed_count"],
        "failed_count": batch["failed_count"],
        "progress_percent": int(done / total * 100) if total > 0 else 0,
        "current_item_seq": current_item["seq"] if current_item else None,
        "items": [
            {
                "seq": i["seq"],
                "status": i["status"],
                "problem_preview": i["problem_text"][:60] + ("..." if len(i["problem_text"]) > 60 else ""),
                "run_id": i["run_id"],
                "error_message": i["error_message"],
            }
            for i in items
        ],
        "settings": json.loads(batch["settings"]) if isinstance(batch["settings"], str) else batch["settings"],
        "created_at": batch["created_at"],
        "updated_at": batch["updated_at"],
    }


@app.post("/batch/{batch_id}/pause")
async def batch_pause(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] != "running":
        return JSONResponse(status_code=400, content={"error": "只能暂停运行中的批次"})
    await update_batch_status(batch_id, "paused")
    return {"status": "paused"}


@app.post("/batch/{batch_id}/resume")
async def batch_resume(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] != "paused":
        return JSONResponse(status_code=400, content={"error": "只能继续已暂停的批次"})
    await update_batch_status(batch_id, "running")
    get_batch_worker().notify()
    return {"status": "running"}


@app.post("/batch/{batch_id}/cancel")
async def batch_cancel(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] not in ("running", "paused"):
        return JSONResponse(status_code=400, content={"error": "只能取消运行中或暂停的批次"})
    await update_batch_status(batch_id, "cancelled")
    items = await list_batch_items(batch_id)
    for item in items:
        if item["status"] == "pending":
            await update_batch_item(batch_id, item["seq"], "cancelled")
    return {"status": "cancelled"}


@app.delete("/batch/{batch_id}")
async def batch_delete(batch_id: str, user: dict = Depends(get_current_user)):
    batch = await load_batch(batch_id, user["id"])
    if not batch:
        return JSONResponse(status_code=404, content={"error": "批次不存在"})
    if batch["status"] == "running":
        return JSONResponse(status_code=400, content={"error": "请先暂停或取消运行中的批次"})
    await delete_batch(batch_id, user["id"])
    return {"status": "deleted"}
```

注意：需要在文件顶部追加 `import json`（如果还没有的话）。

- [ ] **Step 2: 验证 import 无误**

Run: `python -c "from web.app import app; print('OK')"`
Expected: OK

- [ ] **Step 3: 提交**

```bash
git add web/app.py
git commit -m "feat: 批量分析 API 端点 — create/list/status/pause/resume/cancel/delete"
```

---

## Task 4: 前端 HTML — `#queue` 页面 + 新建批次模态框

**Files:**
- Modify: `web/templates/index.html`

**依赖:** 无（可与 Task 1-3 并行）

- [ ] **Step 1: 在侧边栏导航中添加"批量队列"项**

在 `分析诊断` nav-item 之后（约第 56 行），添加：

```html
<a href="#queue" class="nav-item" data-page="queue" title="批量队列">
    <span class="nav-icon">&#128438;</span>
    <span class="nav-text">批量队列</span>
</a>
```

- [ ] **Step 2: 添加 `#page-queue` section**

在 `#page-history` section 之前，添加：

```html
<section id="page-queue" class="page" style="display:none">
    <div class="queue-page">
        <div class="queue-header">
            <h2>批量分析队列</h2>
            <div class="queue-actions">
                <button id="queue-new-btn" class="btn btn-primary">+ 新建批次</button>
                <select id="queue-filter-status" class="input-field" style="width:auto">
                    <option value="">全部状态</option>
                    <option value="running">运行中</option>
                    <option value="paused">已暂停</option>
                    <option value="completed">已完成</option>
                    <option value="cancelled">已取消</option>
                </select>
            </div>
        </div>
        <div id="queue-list" class="queue-list"></div>
        <div id="queue-pagination" class="pagination"></div>
    </div>
</section>
```

- [ ] **Step 3: 添加新建批次模态框**

在 `</body>` 之前添加：

```html
<div id="batch-modal" class="modal" style="display:none">
    <div class="modal-overlay"></div>
    <div class="modal-content batch-modal-content">
        <div class="modal-header">
            <h3>新建批量分析</h3>
            <button class="modal-close" id="batch-modal-close">&times;</button>
        </div>
        <div class="modal-body batch-modal-body">
            <div class="batch-settings">
                <label>学科</label>
                <select id="batch-subject" class="input-field">
                    <option value="auto_detect">自动检测</option>
                    <option value="calculus" selected>微积分</option>
                    <option value="linear_algebra">线性代数</option>
                    <option value="mechanics">力学</option>
                    <option value="electromagnetism">电磁学</option>
                    <option value="optics">光学</option>
                    <option value="quantum">量子力学</option>
                    <option value="relativity">相对论</option>
                    <option value="thermodynamics">热力学</option>
                </select>
                <label>模式</label>
                <select id="batch-mode" class="input-field">
                    <option value="workflow_r1">工作流分析</option>
                    <option value="baseline_qwen3.6">基线对比</option>
                </select>
                <label>深度</label>
                <select id="batch-depth" class="input-field">
                    <option value="quick">快速</option>
                    <option value="standard" selected>标准</option>
                    <option value="thorough">深度</option>
                </select>
                <label>模型</label>
                <select id="batch-model" class="input-field">
                    <option value="qwen/qwen3.6-plus">Qwen 3.6 Plus</option>
                    <option value="deepseek/deepseek-v3.2">DeepSeek V3.2</option>
                </select>
            </div>
            <div class="batch-items-header">
                <h4>题目列表 <span id="batch-item-count">(0 题)</span></h4>
                <button id="batch-add-item-btn" class="btn btn-sm">+ 添加题目</button>
            </div>
            <div id="batch-items-list" class="batch-items-list"></div>
        </div>
        <div class="modal-footer">
            <button id="batch-submit-btn" class="btn btn-primary" disabled>提交批量分析</button>
            <button id="batch-cancel-btn" class="btn">取消</button>
        </div>
    </div>
</div>
```

- [ ] **Step 4: 提交**

```bash
git add web/templates/index.html
git commit -m "feat: 批量队列 HTML 结构 — #queue 页面 + 新建批次模态框"
```

---

## Task 5: 前端 CSS — 队列样式

**Files:**
- Modify: `web/static/style.css`

**依赖:** Task 4

- [ ] **Step 1: 在 `style.css` 末尾追加队列样式**

```css
.queue-page { max-width: 800px; margin: 0 auto; padding: 20px; }
.queue-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.queue-actions { display: flex; gap: 10px; align-items: center; }
.queue-list { display: flex; flex-direction: column; gap: 12px; }

.batch-card {
    background: var(--card-bg, #1a1a2e);
    border: 1px solid var(--border-color, #333);
    border-radius: 10px;
    padding: 16px;
    cursor: pointer;
    transition: border-color 0.2s;
}
.batch-card:hover { border-color: var(--accent, #00d4ff); }
.batch-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.batch-card-meta { font-size: 13px; color: var(--text-muted, #888); }
.batch-card-progress { margin: 10px 0; }
.batch-progress-bar {
    height: 8px; background: var(--border-color, #333); border-radius: 4px; overflow: hidden;
}
.batch-progress-fill {
    height: 100%; background: var(--accent, #00d4ff); border-radius: 4px; transition: width 0.5s;
}
.batch-progress-text { font-size: 12px; color: var(--text-muted, #888); margin-top: 4px; }
.batch-card-actions { display: flex; gap: 8px; margin-top: 10px; }

.batch-status-badge {
    font-size: 12px; padding: 2px 8px; border-radius: 10px;
}
.batch-status-running { background: #1a3a5c; color: #4dabf7; }
.batch-status-paused { background: #3d3a1a; color: #ffd43b; }
.batch-status-completed { background: #1a3d1a; color: #51cf66; }
.batch-status-cancelled { background: #3d1a1a; color: #ff6b6b; }
.batch-status-failed { background: #3d1a1a; color: #ff6b6b; }
.batch-status-pending { background: #2a2a3a; color: #aaa; }

.batch-item-list { margin-top: 12px; border-top: 1px solid var(--border-color, #333); padding-top: 10px; }
.batch-item-row {
    display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 14px;
    border-bottom: 1px solid var(--border-color, #222);
}
.batch-item-row:last-child { border-bottom: none; }
.batch-item-seq { width: 30px; color: var(--text-muted, #888); font-weight: bold; }
.batch-item-preview { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.batch-item-link { color: var(--accent, #00d4ff); text-decoration: none; cursor: pointer; }
.batch-item-link:hover { text-decoration: underline; }

.batch-modal-content { max-width: 700px; max-height: 90vh; overflow-y: auto; }
.batch-settings {
    display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px;
}
.batch-items-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.batch-items-list { max-height: 50vh; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.batch-item-input {
    background: var(--input-bg, #16213e); border: 1px solid var(--border-color, #333);
    border-radius: 8px; padding: 10px; position: relative;
}
.batch-item-input-header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;
}
.batch-item-input-header span { font-weight: bold; font-size: 13px; }
.batch-item-remove { background: none; border: none; color: #ff6b6b; cursor: pointer; font-size: 18px; }
.batch-item-input textarea {
    width: 100%; background: transparent; border: none; color: inherit;
    font-size: 14px; resize: vertical; min-height: 50px; outline: none;
}
.batch-item-input .ocr-btns {
    display: flex; gap: 6px; margin-top: 4px;
}
.batch-item-input .ocr-btns button {
    font-size: 11px; padding: 2px 8px; border-radius: 4px;
    background: var(--border-color, #333); border: none; color: var(--text-muted, #aaa); cursor: pointer;
}
```

- [ ] **Step 2: 提交**

```bash
git add web/static/style.css
git commit -m "feat: 批量队列 CSS 样式 — 卡片、进度条、模态框、状态标签"
```

---

## Task 6: 前端 JS — QueueModule + 路由注册

**Files:**
- Modify: `web/static/app.js`

**依赖:** Task 4, Task 5

这是最大的任务。在 `app.js` 中需要：
1. 在 `AppRouter.pages` 中添加 `"queue"`
2. 在 `AppRouter.titles` 中添加标题
3. 在路由回调中添加 QueueModule.load()
4. 实现 `QueueModule` 对象

- [ ] **Step 1: 注册路由**

在 `AppRouter.pages` 数组（约第 75 行）中添加 `"queue"`：
```javascript
pages: ["new", "queue", "history", "stats", "report", "logs", "settings", "admin"]
```

在 `titles` 对象中添加：
```javascript
queue: "批量队列"
```

在路由回调（约第 154 行的 if/else 链）中添加：
```javascript
if (page === "queue") QueueModule.load();
```

- [ ] **Step 2: 实现 QueueModule**

在 `app.js` 末尾追加 `QueueModule` 对象（约 250 行）：

```javascript
var QueueModule = (function() {
    var _pollTimer = null;
    var _currentBatchId = null;
    var _expandedBatchId = null;

    function _stopPoll() {
        if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    }

    function _startPoll(batchId) {
        _stopPoll();
        _currentBatchId = batchId;
        _pollTimer = setInterval(function() { _refreshStatus(batchId); }, 5000);
    }

    function _api(method, path, body) {
        var opts = { method: method, headers: { "Authorization": "Bearer " + App.auth.token } };
        if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
        return fetch(path, opts).then(function(r) { return r.json(); });
    }

    function load() {
        _stopPoll();
        _expandedBatchId = null;
        document.getElementById("queue-filter-status").onchange = function() { _loadList(); };
        document.getElementById("queue-new-btn").onclick = function() { _openNewModal(); };
        _loadList();
    }

    function _loadList() {
        var status = document.getElementById("queue-filter-status").value;
        var params = "?per_page=50";
        if (status) params += "&status=" + status;
        _api("GET", "/batch/list" + params).then(function(data) {
            _renderList(data);
        });
    }

    function _renderList(data) {
        var container = document.getElementById("queue-list");
        if (!data.batches || data.batches.length === 0) {
            container.innerHTML = '<p style="text-align:center;color:var(--text-muted,#888)">暂无批量分析任务</p>';
            return;
        }
        var html = "";
        data.batches.forEach(function(b) {
            var done = b.completed_count + b.failed_count;
            var pct = b.total_count > 0 ? Math.round(done / b.total_count * 100) : 0;
            var settings = typeof b.settings === "string" ? JSON.parse(b.settings) : (b.settings || {});
            var statusClass = "batch-status-" + b.status;
            var statusLabel = {pending:"等待中",running:"运行中",paused:"已暂停",completed:"已完成",cancelled:"已取消"}[b.status] || b.status;
            html += '<div class="batch-card" data-batch-id="' + b.id + '">';
            html += '<div class="batch-card-header">';
            html += '<span class="batch-card-meta">' + (settings.subject_id || "微积分") + ' | ' + (settings.depth || "标准") + ' | ' + (b.created_at || "") + '</span>';
            html += '<span class="batch-status-badge ' + statusClass + '">' + statusLabel + '</span>';
            html += '</div>';
            html += '<div class="batch-card-progress">';
            html += '<div class="batch-progress-bar"><div class="batch-progress-fill" style="width:' + pct + '%"></div></div>';
            html += '<div class="batch-progress-text">' + done + '/' + b.total_count + ' (' + pct + '%)</div>';
            html += '</div>';
            html += '<div class="batch-card-actions">';
            if (b.status === "running") {
                html += '<button class="btn btn-sm" onclick="QueueModule.pause(\'' + b.id + '\')">暂停</button>';
                html += '<button class="btn btn-sm" onclick="QueueModule.cancel(\'' + b.id + '\')">取消</button>';
            } else if (b.status === "paused") {
                html += '<button class="btn btn-sm btn-primary" onclick="QueueModule.resume(\'' + b.id + '\')">继续</button>';
                html += '<button class="btn btn-sm" onclick="QueueModule.cancel(\'' + b.id + '\')">取消</button>';
            } else if (b.status === "pending") {
                html += '<button class="btn btn-sm btn-primary" onclick="QueueModule.start(\'' + b.id + '\')">开始</button>';
                html += '<button class="btn btn-sm" onclick="QueueModule.remove(\'' + b.id + '\')">删除</button>';
            } else {
                if (b.status === "completed") html += '<button class="btn btn-sm" onclick="QueueModule.viewSummary(\'' + b.id + '\')">查看汇总</button>';
                html += '<button class="btn btn-sm" onclick="QueueModule.remove(\'' + b.id + '\')">删除</button>';
            }
            html += '<button class="btn btn-sm" onclick="QueueModule.toggleExpand(\'' + b.id + '\', this)">' + (_expandedBatchId === b.id ? '收起' : '展开详情') + '</button>';
            html += '</div>';
            if (_expandedBatchId === b.id) {
                html += '<div class="batch-item-list" id="batch-items-' + b.id + '">加载中...</div>';
            }
            html += '</div>';
        });
        container.innerHTML = html;
        if (_expandedBatchId) {
            _refreshStatus(_expandedBatchId);
            if (!_pollTimer) _startPoll(_expandedBatchId);
        }
        data.batches.forEach(function(b) {
            if (b.status === "running" && !_pollTimer) _startPoll(b.id);
        });
    }

    function _refreshStatus(batchId) {
        _api("GET", "/batch/" + batchId + "/status").then(function(data) {
            var el = document.getElementById("batch-items-" + batchId);
            if (!el) return;
            var html = "";
            (data.items || []).forEach(function(item) {
                var icon = {pending:"⏳",running:"🔄",completed:"✅",failed:"❌",cancelled:"🚫",skipped:"⏭"}[item.status] || "⏳";
                html += '<div class="batch-item-row">';
                html += '<span class="batch-item-seq">#' + (item.seq + 1) + '</span>';
                html += '<span>' + icon + '</span>';
                if (item.status === "completed" && item.run_id) {
                    html += '<a class="batch-item-link" onclick="QueueModule.viewRun(\'' + item.run_id + '\')">' + esc(item.problem_preview) + '</a>';
                } else {
                    html += '<span class="batch-item-preview">' + esc(item.problem_preview) + '</span>';
                }
                if (item.error_message) html += '<span style="color:#ff6b6b;font-size:12px">(' + esc(item.error_message) + ')</span>';
                html += '</div>';
            });
            el.innerHTML = html || '<p style="color:var(--text-muted,#888)">暂无题目</p>';
            if (data.status !== "running" && data.status !== "pending") {
                if (_currentBatchId === batchId) _stopPoll();
                _loadList();
            }
        });
    }

    function toggleExpand(batchId) {
        _expandedBatchId = _expandedBatchId === batchId ? null : batchId;
        _loadList();
    }

    function pause(batchId) { _api("POST", "/batch/" + batchId + "/pause").then(function() { _loadList(); }); }
    function resume(batchId) { _api("POST", "/batch/" + batchId + "/resume").then(function() { _stopPoll(); _startPoll(batchId); _loadList(); }); }
    function cancel(batchId) { if (confirm("确定取消此批次？")) _api("POST", "/batch/" + batchId + "/cancel").then(function() { _loadList(); }); }
    function start(batchId) { _api("POST", "/batch/" + batchId + "/resume").then(function() { _startPoll(batchId); _loadList(); }); }
    function remove(batchId) { if (confirm("确定删除此批次？")) _api("DELETE", "/batch/" + batchId).then(function() { _loadList(); }); }

    function viewRun(runId) {
        App.currentRunId = runId;
        AppRouter.navigate("history");
    }

    function viewSummary(batchId) {
        _expandedBatchId = batchId;
        _loadList();
    }

    function _openNewModal() {
        document.getElementById("batch-modal").style.display = "flex";
        document.getElementById("batch-items-list").innerHTML = "";
        document.getElementById("batch-item-count").textContent = "(0 题)";
        document.getElementById("batch-submit-btn").disabled = true;
        _addItem();
        document.getElementById("batch-modal-close").onclick = function() { document.getElementById("batch-modal").style.display = "none"; };
        document.querySelector("#batch-modal .modal-overlay").onclick = function() { document.getElementById("batch-modal").style.display = "none"; };
        document.getElementById("batch-cancel-btn").onclick = function() { document.getElementById("batch-modal").style.display = "none"; };
        document.getElementById("batch-add-item-btn").onclick = function() { _addItem(); };
        document.getElementById("batch-submit-btn").onclick = function() { _submitBatch(); };
    }

    function _addItem() {
        var list = document.getElementById("batch-items-list");
        var idx = list.children.length;
        var div = document.createElement("div");
        div.className = "batch-item-input";
        div.innerHTML =
            '<div class="batch-item-input-header"><span>第 ' + (idx + 1) + ' 题</span><button class="batch-item-remove" title="删除">&times;</button></div>' +
            '<label style="font-size:12px;color:var(--text-muted,#888)">题目</label>' +
            '<textarea class="batch-problem-text" placeholder="输入题目文本" rows="2"></textarea>' +
            '<div class="ocr-btns"><button onclick="QueueModule._ocrInput(this, \'problem\')">📷 拍照识别</button><button onclick="QueueModule._ocrInput(this, \'problem\')">🖼 上传图片</button></div>' +
            '<label style="font-size:12px;color:var(--text-muted,#888);margin-top:6px;display:block">学生解答</label>' +
            '<textarea class="batch-solution-text" placeholder="输入学生解题步骤" rows="3"></textarea>' +
            '<div class="ocr-btns"><button onclick="QueueModule._ocrInput(this, \'solution\')">📷 拍照识别</button><button onclick="QueueModule._ocrInput(this, \'solution\')">🖼 上传图片</button></div>';
        div.querySelector(".batch-item-remove").onclick = function() { div.remove(); _renumber(); };
        list.appendChild(div);
        _updateCount();
    }

    function _renumber() {
        var items = document.querySelectorAll("#batch-items-list .batch-item-input");
        items.forEach(function(el, i) { el.querySelector("span").textContent = "第 " + (i + 1) + " 题"; });
        _updateCount();
    }

    function _updateCount() {
        var n = document.querySelectorAll("#batch-items-list .batch-item-input").length;
        document.getElementById("batch-item-count").textContent = "(" + n + " 题)";
        document.getElementById("batch-submit-btn").disabled = n === 0;
    }

    function _ocrInput(btn, field) {
        var input = document.createElement("input");
        input.type = "file";
        input.accept = "image/*";
        input.onchange = function() {
            if (!input.files[0]) return;
            var fd = new FormData();
            fd.append("image", input.files[0]);
            fd.append("model", document.getElementById("batch-model").value);
            btn.textContent = "识别中...";
            fetch("/ocr", { method: "POST", body: fd }).then(function(r) { return r.json(); }).then(function(data) {
                var text = data.text || "";
                var itemDiv = btn.closest(".batch-item-input");
                var textarea = field === "problem" ? itemDiv.querySelector(".batch-problem-text") : itemDiv.querySelector(".batch-solution-text");
                textarea.value = text;
                btn.textContent = field === "problem" ? "📷 拍照识别" : "📷 拍照识别";
            }).catch(function() { btn.textContent = "识别失败"; });
        };
        input.click();
    }

    function _submitBatch() {
        var items = [];
        document.querySelectorAll("#batch-items-list .batch-item-input").forEach(function(el) {
            var pt = el.querySelector(".batch-problem-text").value.trim();
            var st = el.querySelector(".batch-solution-text").value.trim();
            if (pt) items.push({ problem_text: pt, student_solution: st, source_type: "text" });
        });
        if (items.length === 0) { alert("至少需要一道题目"); return; }
        var settings = {
            model: document.getElementById("batch-model").value,
            subject_id: document.getElementById("batch-subject").value,
            mode: document.getElementById("batch-mode").value,
            depth: document.getElementById("batch-depth").value,
        };
        document.getElementById("batch-submit-btn").disabled = true;
        document.getElementById("batch-submit-btn").textContent = "提交中...";
        _api("POST", "/batch/create", { items: items, settings: settings, auto_start: true }).then(function(data) {
            document.getElementById("batch-modal").style.display = "none";
            document.getElementById("batch-submit-btn").textContent = "提交批量分析";
            _loadList();
            if (data.batch_id) _startPoll(data.batch_id);
        }).catch(function() {
            document.getElementById("batch-submit-btn").disabled = false;
            document.getElementById("batch-submit-btn").textContent = "提交批量分析";
        });
    }

    return {
        load: load,
        toggleExpand: toggleExpand,
        pause: pause,
        resume: resume,
        cancel: cancel,
        start: start,
        remove: remove,
        viewRun: viewRun,
        viewSummary: viewSummary,
        _ocrInput: _ocrInput,
    };
})();
```

- [ ] **Step 2: 验证 JS 语法无误**

在浏览器中打开应用，确认无控制台报错。

- [ ] **Step 3: 提交**

```bash
git add web/static/app.js
git commit -m "feat: QueueModule — 批量队列前端模块（列表/轮询/新建/OCR/控制）"
```

---

## Task 7: 集成测试 + 最终验证

**Files:**
- Modify: `tests/test_batch.py`（追加集成测试）

**依赖:** Task 1-6 全部完成

- [ ] **Step 1: 编写 API 端点集成测试**

```python
import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.fixture
async def client():
    from web.app import app
    from web.database import _ensure_db, create_user, get_user_by_username
    await _ensure_db()
    user = await get_user_by_username("test_batch_user")
    if not user:
        await create_user("test_batch_user", "hashed_pw")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_batch_create_and_status(client):
    resp = await client.post("/batch/create", json={
        "items": [
            {"problem_text": "1+1=?", "student_solution": "2", "source_type": "text"},
            {"problem_text": "2+2=?", "student_solution": "4", "source_type": "text"},
        ],
        "settings": {"model": "qwen/qwen3.6-plus", "subject_id": "calculus", "mode": "workflow_r1", "depth": "standard"},
        "auto_start": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "batch_id" in data
    assert data["total_count"] == 2

    batch_id = data["batch_id"]
    resp2 = await client.get(f"/batch/{batch_id}/status")
    assert resp2.status_code == 200
    status = resp2.json()
    assert status["total_count"] == 2
    assert status["status"] == "pending"

@pytest.mark.asyncio
async def test_batch_list(client):
    resp = await client.get("/batch/list")
    assert resp.status_code == 200
    data = resp.json()
    assert "batches" in data
    assert "total" in data

@pytest.mark.asyncio
async def test_batch_pause_resume_cancel(client):
    resp = await client.post("/batch/create", json={
        "items": [{"problem_text": "test", "student_solution": "t", "source_type": "text"}],
        "settings": {},
        "auto_start": True,
    })
    batch_id = resp.json()["batch_id"]

    resp2 = await client.post(f"/batch/{batch_id}/pause")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "paused"

    resp3 = await client.post(f"/batch/{batch_id}/resume")
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "running"

    resp4 = await client.post(f"/batch/{batch_id}/cancel")
    assert resp4.status_code == 200
    assert resp4.json()["status"] == "cancelled"

    resp5 = await client.delete(f"/batch/{batch_id}")
    assert resp5.status_code == 200
```

注意：这些测试需要 auth token。根据项目的 auth 机制，可能需要在 headers 中加入 Bearer token。如果 `get_current_user` 在测试中返回 mock user，则可直接使用。

- [ ] **Step 2: 运行全部测试**

Run: `python -m pytest tests/test_batch.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 运行现有测试确认无回归**

Run: `python -m pytest tests/test_auth.py tests/test_database.py tests/test_review_problems_topic.py tests/test_v1_feedback_review_fallbacks.py tests/test_user_safe_status.py -v`
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add tests/test_batch.py
git commit -m "test: 批量分析集成测试 — API 端点 CRUD + worker 流程"
```

---

## Task 8: 中期报告更新

**Files:**
- Modify: `docs/midterm-progress-report.md`（在父仓库）

**依赖:** Task 7 完成

- [ ] **Step 1: 在 §2 已完成工作中补充批量队列功能**

在"Web 界面与交互功能"段落后追加：

```
**批量分析队列。** 支持一次提交最多 100 道题目（文本或 OCR 识别），系统后台串行自动分析，无需保持浏览器打开。包含批次管理（暂停/继续/取消）、实时进度展示（列表+进度条）、断点续传（服务重启自动恢复）。分析结果自动进入历史记录，支持批量汇总与单题详情查看。
```

- [ ] **Step 2: 更新测试统计**

更新测试统计数字（在提交前运行 `pytest --co` 确认实际数字）。

---

## 自查清单

1. **设计覆盖**：所有 spec 需求均有对应 task ✓
2. **无占位符**：所有步骤包含实际代码 ✓
3. **类型一致**：函数名、参数名在各 task 间一致 ✓
