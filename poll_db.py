import sqlite3, time, sys, json

db_path = sys.argv[1] if len(sys.argv) > 1 else "data/stem_tutor.db"
batch_id = sys.argv[2] if len(sys.argv) > 2 else "24bf772b-90c3-4542-a4bb-b0a5eea995dc"
poll_interval = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

start = time.time()
while time.time() - start < 7200:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            print(f"Batch {batch_id} not found!")
            sys.exit(1)
        items = conn.execute(
            "SELECT seq, status, run_id FROM batch_items WHERE batch_id=? ORDER BY seq",
            (batch_id,),
        ).fetchall()
        total = batch["total_count"]
        done = batch["completed_count"] + batch["failed_count"]
        ok = batch["completed_count"]
        fail = batch["failed_count"]
        status = batch["status"]
        elapsed = int(time.time() - start)
        pct = int(done / total * 100) if total > 0 else 0
        current = next((i["seq"] for i in items if i["status"] == "running"), None)
        print(f"[{elapsed}s] {done}/{total} ({pct}%) ok={ok} fail={fail} running_seq={current} batch_status={status}", flush=True)
        if status == "completed":
            print("Batch completed!")
            break
    finally:
        conn.close()
    time.sleep(poll_interval)
