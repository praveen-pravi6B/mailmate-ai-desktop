"""Create Google Calendar events for all tasks with CLEAR deadlines."""

import os, sys, requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

env_path = ROOT / ".env"
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from app.integrations.calendar import GoogleCalendarTransport
from app.services.calendar_service import schedule_event, CREATED
from app.storage.task_store import TaskStore

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT  = os.environ["TELEGRAM_CHAT_ID"]
BASE  = f"https://api.telegram.org/bot{TOKEN}"

EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡"}

def send(text):
    requests.post(f"{BASE}/sendMessage",
                  json={"chat_id": CHAT, "text": text, "parse_mode": "Markdown"})

store     = TaskStore("mailmate.db")
transport = GoogleCalendarTransport(live_writes=True)

eligible = [
    t for t in store.list_tasks()
    if t.deadline_state == "CLEAR"
    and t.deadline
    and not t.calendar_event_id
]

print(f"Tasks eligible for calendar: {len(eligible)}")

for task in eligible:
    print(f"\n→ [{task.priority}] {task.title[:60]} | {task.deadline}")
    result = schedule_event(task, store, transport, time="09:00", timezone="Asia/Kolkata")
    print(f"  Status: {result.status}  event_id: {result.event_id}")

    emoji = EMOJI.get(task.priority, "⚪")
    if result.status == CREATED:
        send(
            f"🗓️ *Calendar Event Created!*\n\n"
            f"{emoji} *{task.title}*\n"
            f"📅 {task.deadline} at 09:00 IST\n"
            f"✅ Added to your Google Calendar"
        )
    else:
        print(f"  Reason: {result.reason}")

print("\n─── Final Task Store ───")
for t in store.list_tasks():
    cal = f"  🗓 {t.calendar_event_id[:25]}" if t.calendar_event_id else ""
    print(f"[{t.priority}] {t.deadline_state:<10} {t.confirmation_status.value:<14} "
          f"{t.title[:45]}{cal}")
