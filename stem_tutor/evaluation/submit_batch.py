"""Submit batch to web server and wait for completion."""

import argparse
import json
import time
from pathlib import Path

import requests


def login(base_url: str, username: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    if resp.status_code == 403:
        detail = resp.json().get("detail", "")
        raise RuntimeError(
            f"Login refused (may need password change): {detail}."
        )
    resp.raise_for_status()
    return resp.json()["access_token"]


def submit_batch(base_url: str, token: str, payload: dict) -> str:
    resp = requests.post(
        f"{base_url}/batch/create",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["batch_id"]


def poll_batch_status(
    base_url: str,
    token: str,
    batch_id: str,
    poll_interval: float = 10.0,
    max_wait: float = 7200.0,
) -> dict:
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(
            f"{base_url}/batch/{batch_id}/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        resp.raise_for_status()
        status = resp.json()
        done = status["completed_count"] + status["failed_count"]
        total = status["total_count"]
        elapsed = int(time.time() - start)
        current = status.get("current_item_seq")
        print(
            f"\r[{elapsed}s] {done}/{total} ({status['progress_percent']}%) "
            f"ok={status['completed_count']} fail={status['failed_count']}"
            f"{f' running_item={current}' if current is not None else ''}",
            end="",
            flush=True,
        )
        if status["status"] == "completed":
            print()
            return status
        time.sleep(poll_interval)
    print()
    raise TimeoutError(f"Batch {batch_id} did not complete within {max_wait}s")


def main():
    parser = argparse.ArgumentParser(description="Submit batch to web server")
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=None)
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--max-wait", type=float, default=7200.0)
    args = parser.parse_args()

    password = args.password
    if not password:
        pw_file = Path(__file__).resolve().parent.parent.parent / "data" / "admin_password.txt"
        if pw_file.exists():
            password = pw_file.read_text(encoding="utf-8").strip()
            print(f"Read password from {pw_file}")
        else:
            password = input("Enter password: ")

    print(f"Logging in to {args.base_url} as {args.username} ...")
    token = login(args.base_url, args.username, password)
    print("Login OK")

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    print(f"Submitting batch ({len(payload['items'])} items) ...")
    batch_id = submit_batch(args.base_url, token, payload)
    print(f"Batch ID: {batch_id}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(batch_id, encoding="utf-8")
    print(f"Saved batch_id to {args.output}")

    print("Waiting for batch completion ...")
    final_status = poll_batch_status(
        args.base_url, token, batch_id,
        poll_interval=args.poll_interval,
        max_wait=args.max_wait,
    )
    print(f"\nDone: {final_status['completed_count']} ok, "
          f"{final_status['failed_count']} failed")

    if final_status["failed_count"] > 0:
        print("\nFailed items:")
        for item in final_status["items"]:
            if item["status"] == "failed":
                print(f"  seq={item['seq']}: {item['error_message']}")


if __name__ == "__main__":
    main()
