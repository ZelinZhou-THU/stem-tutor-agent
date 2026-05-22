"""Migrate logs/runs/*.json, logs/chats/*.json, logs/reports/*.json to SQLite."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from web.database import save_run, save_chat, save_report, create_user, get_user_by_username, _ensure_db
from web.auth import hash_password


async def migrate():
    await _ensure_db()
    base = Path(__file__).resolve().parent / "logs"

    user = await get_user_by_username("legacy_user")
    if not user:
        uid = await create_user("legacy_user", hash_password("legacy"))
    else:
        uid = user["id"]

    count = 0
    runs_dir = base / "runs"
    if runs_dir.exists():
        for p in runs_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                meta = data.get("run_meta", {})
                await save_run(meta.get("run_id", p.stem), uid, data, data.get("status", "unknown"), meta.get("subject_id"))
                count += 1
            except Exception as e:
                print(f"  SKIP run {p.name}: {e}")
    print(f"Migrated {count} runs")

    count = 0
    chats_dir = base / "chats"
    if chats_dir.exists():
        for p in chats_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                await save_chat(data["run_id"], uid, data.get("messages", []))
                count += 1
            except Exception as e:
                print(f"  SKIP chat {p.name}: {e}")
    print(f"Migrated {count} chats")

    count = 0
    reports_dir = base / "reports"
    if reports_dir.exists():
        for p in reports_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                await save_report(data["report_id"], uid, data)
                count += 1
            except Exception as e:
                print(f"  SKIP report {p.name}: {e}")
    print(f"Migrated {count} reports")

    print("Done. Legacy user: legacy_user / legacy")


if __name__ == "__main__":
    asyncio.run(migrate())
