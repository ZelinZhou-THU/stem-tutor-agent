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
                                run_id = (
                                    data.get("data", {}).get("run_id")
                                    or data.get("data", {}).get("run_meta", {}).get("run_id")
                                )
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
