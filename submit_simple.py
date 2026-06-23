import requests, json, time, sys

base = "http://127.0.0.1:8000"

# Login with retries
for attempt in range(10):
    try:
        r = requests.post(
            f"{base}/api/auth/login",
            json={"username": "admin", "password": "THUIE2026"},
            timeout=120,
        )
        token = r.json()["access_token"]
        print("Login OK")
        break
    except Exception as e:
        print(f"Login attempt {attempt+1} failed: {type(e).__name__}")
        time.sleep(10)
else:
    print("Login failed after 10 attempts")
    sys.exit(1)

# Submit batch
payload_file = sys.argv[1] if len(sys.argv) > 1 else "../TestData4StemTutor/eval_output/combined_batch_payload_wf.json"
output_file = sys.argv[2] if len(sys.argv) > 2 else "../TestData4StemTutor/eval_output/combined_batch_id_wf.txt"

payload = json.loads(open(payload_file, encoding="utf-8").read())
num_items = len(payload["items"])
mode = payload["settings"]["mode"]
print(f"Submitting {mode} batch ({num_items} items)...")

for attempt in range(10):
    try:
        r = requests.post(
            f"{base}/batch/create",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        batch_id = r.json()["batch_id"]
        print(f"Batch ID: {batch_id}")
        open(output_file, "w", encoding="utf-8").write(batch_id)
        print(f"Saved to {output_file}")
        break
    except Exception as e:
        print(f"Submit attempt {attempt+1} failed: {type(e).__name__}")
        time.sleep(10)
else:
    print("Submit failed after 10 attempts")
    sys.exit(1)
