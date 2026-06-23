import requests, time, json, sys

base_url = "http://127.0.0.1:8000"
batch_id = sys.argv[1] if len(sys.argv) > 1 else "24bf772b-90c3-4542-a4bb-b0a5eea995dc"
poll_interval = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

resp = requests.post(
    f"{base_url}/api/auth/login",
    json={"username": "admin", "password": "THUIE2026"},
    timeout=30,
)
token = resp.json()["access_token"]

start = time.time()
while time.time() - start < 7200:
    try:
        resp = requests.get(
            f"{base_url}/batch/{batch_id}/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        status = resp.json()
        done = status["completed_count"] + status["failed_count"]
        total = status["total_count"]
        elapsed = int(time.time() - start)
        pct = status["progress_percent"]
        ok = status["completed_count"]
        fail = status["failed_count"]
        st = status["status"]
        print(f"[{elapsed}s] {done}/{total} ({pct}%) ok={ok} fail={fail} status={st}", flush=True)
        if st == "completed":
            print("DONE!")
            break
    except Exception as e:
        elapsed_s = int(time.time() - start)
        print(f"[{elapsed_s}s] poll error: {type(e).__name__}: {e}", flush=True)
    time.sleep(poll_interval)
