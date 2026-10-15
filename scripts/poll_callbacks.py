"""Poll Telegram for pending callback queries and process them.

Handles:
  - P2 task confirmation (Yes / No buttons)
  - After Yes: if task has a CLEAR deadline, creates a Google Calendar event
  - After Yes: if no deadline, asks user to reply with time

Run once after clicking buttons, or keep running for real-time response.

Usage:
    python scripts/poll_callbacks.py
"""

import os, sys, requests
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

env_path = ROOT / ".env"
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT  = os.environ["TELEGRAM_CHAT_ID"]
BASE  = f"https://api.telegram.org/bot{TOKEN}"

from app.integrations.calendar import GoogleCalendarTransport
from app.services.calendar_service import schedule_event, CREATED
from app.services.confirmation_handler import handle_callback
from app.storage.task_store import TaskStore

store = TaskStore(os.environ.get("DB_PATH", "mailmate.db"))


def send(text: str):
    requests.post(f"{BASE}/sendMessage",
                  json={"chat_id": CHAT, "text": text, "parse_mode": "Markdown"})


def answer(cb_query_id: str, text: str):
    requests.post(f"{BASE}/answerCallbackQuery",
                  json={"callback_query_id": cb_query_id, "text": text})


def try_create_calendar_event(task):
    """If task has a CLEAR deadline, create a calendar event at 09:00 IST."""
    if task.deadline_state != "CLEAR" or not task.deadline:
        send(
            f"✅ *Task confirmed:* _{task.title}_\n\n"
            f"No deadline set — task is in your queue but no calendar event was created.\n"
            f"Add a deadline to schedule it in Google Calendar."
        )
        return

    try:
        transport = GoogleCalendarTransport(live_writes=True)
        result = schedule_event(
            task=task,
            store=store,
            transport=transport,
            time="09:00",
            timezone="Asia/Kolkata",
        )
        if result.status == CREATED:
            send(
                f"✅ *Task confirmed & Calendar event created!*\n\n"
                f"📌 *{task.title}*\n"
                f"📅 {task.deadline} at 09:00 IST\n"
                f"🗓️ Event ID: `{result.event_id}`"
            )
        else:
            send(
                f"✅ *Task confirmed:* _{task.title}_\n\n"
                f"⚠️ Calendar event not created: {result.reason}"
            )
    except Exception as exc:
        send(
            f"✅ *Task confirmed:* _{task.title}_\n\n"
            f"⚠️ Calendar error: {type(exc).__name__} — {exc}"
        )


# Fetch pending updates
resp = requests.get(f"{BASE}/getUpdates", params={"timeout": 10}).json()
updates = resp.get("result", [])
print(f"Pending updates: {len(updates)}")

last_update_id = None
processed = 0

for update in updates:
    last_update_id = update["update_id"]
    cb = update.get("callback_query")
    if not cb:
        continue

    data        = cb.get("data", "")
    cb_query_id = cb["id"]
    from_user   = cb.get("from", {}).get("first_name", "User")

    print(f"\nCallback: {data} from {from_user}")

    result = handle_callback(data, store)
    print(f"  → accepted={result.accepted} declined={result.declined} "
          f"already_resolved={result.already_resolved} expired={result.expired}")

    # Acknowledge to remove spinner
    answer(cb_query_id, result.reply_text)

    if result.accepted and result.task_id:
        task = store.get_by_id(result.task_id)
        try_create_calendar_event(task)
        processed += 1

    elif result.declined:
        send(f"❌ *Task declined.* No task or calendar event was created.")
        processed += 1

    elif result.already_resolved:
        send(f"ℹ️ Already resolved — {result.reply_text}")

    elif result.expired:
        send(f"⏰ Confirmation window expired.")

    elif result.error:
        send(f"⚠️ Error processing callback: {result.reply_text}")

# Clear processed updates
if last_update_id is not None:
    requests.get(f"{BASE}/getUpdates",
                 params={"offset": last_update_id + 1, "timeout": 1})

print(f"\nProcessed {processed} callback(s).")

# Show DB state
print("\n─── Task Store ───")
for t in store.list_tasks():
    cal = f"  cal={t.calendar_event_id[:20]}" if t.calendar_event_id else ""
    print(f"[{t.priority}] {t.confirmation_status.value:<12} {t.status.value:<12} "
          f"{t.title[:50]}{cal}")
