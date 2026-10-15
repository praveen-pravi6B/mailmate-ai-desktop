"""MailMate AI — persistent Telegram bot runner.

Continuously polls Telegram for commands and callback queries and responds
in real time.  Run this in a terminal alongside live_run.py.

Usage:
    python scripts/bot_runner.py

Stop with Ctrl+C.
"""

import os, sys, time, requests, signal
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

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT  = os.environ["TELEGRAM_CHAT_ID"]
BASE  = f"https://api.telegram.org/bot{TOKEN}"
POLL_TIMEOUT = 30  # long-poll seconds

from app.integrations.calendar import GoogleCalendarTransport
from app.services.calendar_service import schedule_event, CREATED
from app.services.confirmation_handler import handle_callback
from app.storage.task_store import TaskStore, TaskStatus

EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "OTHER": "⚪"}

running = True
def _stop(sig, frame):
    global running
    print("\nShutting down…")
    running = False
signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def get_store():
    return TaskStore(os.environ.get("DB_PATH", "mailmate.db"))


def send(text: str, parse_mode: str = "Markdown"):
    requests.post(f"{BASE}/sendMessage",
                  json={"chat_id": CHAT, "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True},
                  timeout=10)


def answer_cb(cb_query_id: str, text: str):
    requests.post(f"{BASE}/answerCallbackQuery",
                  json={"callback_query_id": cb_query_id, "text": text},
                  timeout=10)


WELCOME = (
    "👋 *Welcome to MailMate AI!*\n\n"
    "I watch your Gmail inbox, classify emails by priority using the "
    "*Laya* typed-decision engine, and create tasks for actionable emails.\n\n"
    "🔴 *P0* — Critical incident → task created automatically\n"
    "🟠 *P1* — Important with deadline → task created automatically\n"
    "🟡 *P2* — Routine → asks for your confirmation\n"
    "⚪ *OTHER* — Informational → summary only\n\n"
    "📋 *Commands:*\n"
    "/tasks — view pending tasks\n"
    "/status — pipeline status\n"
    "/poll — trigger a Gmail poll\n"
    "/help — show this message"
)


def handle_command(text: str):
    cmd = text.strip().split()[0].lower().split("@")[0]
    store = get_store()

    if cmd in ("/start", "/help"):
        send(WELCOME)

    elif cmd == "/tasks":
        tasks = store.list_tasks(status=TaskStatus.PENDING)
        if not tasks:
            send("✅ No pending tasks right now.")
            return
        msg = f"📋 *Pending Tasks ({len(tasks)})*\n\n"
        for t in tasks:
            e = EMOJI.get(t.priority, "⚪")
            dl = f" | 📅 {t.deadline}" if t.deadline else ""
            conf = " | ⏳ _awaiting confirmation_" if t.confirmation_status.value == "PENDING" else ""
            msg += f"{e} *{t.title[:55]}*{dl}{conf}\n\n"
        send(msg)

    elif cmd == "/status":
        tasks = store.list_tasks()
        pending   = sum(1 for t in tasks if t.status.value == "PENDING")
        confirmed = sum(1 for t in tasks if t.status.value == "CONFIRMED")
        with_cal  = sum(1 for t in tasks if t.calendar_event_id)
        send(
            f"📊 *MailMate AI Status*\n\n"
            f"Tasks in DB: {len(tasks)}\n"
            f"⏳ Pending: {pending}\n"
            f"✔️ Confirmed: {confirmed}\n"
            f"🗓️ With calendar event: {with_cal}\n\n"
            f"_Pipeline: Gmail → Laya → Policy → SQLite → Telegram → Calendar_"
        )

    elif cmd == "/poll":
        send("🔄 Polling Gmail… run `python scripts/live_run.py` to process inbox.")

    else:
        send(f"Unknown command: `{cmd}`. Try /help")


def handle_cb(cb: dict):
    data        = cb.get("data", "")
    cb_query_id = cb["id"]
    store       = get_store()

    result = handle_callback(data, store)
    answer_cb(cb_query_id, result.reply_text)

    if result.accepted and result.task_id:
        task = store.get_by_id(result.task_id)
        e = EMOJI.get(task.priority, "⚪")
        send(f"✅ *Task confirmed!*\n\n{e} *{task.title}*")

        # Auto-create calendar event if task has a clear deadline
        if task.deadline_state == "CLEAR" and task.deadline and not task.calendar_event_id:
            try:
                transport = GoogleCalendarTransport(live_writes=True)
                cal = schedule_event(task, store, transport,
                                     time="09:00", timezone="Asia/Kolkata")
                if cal.status == CREATED:
                    send(f"🗓️ *Calendar event created!*\n"
                         f"📅 {task.deadline} at 09:00 IST")
            except Exception as exc:
                send(f"⚠️ Calendar error: {type(exc).__name__}")

    elif result.declined:
        send("❌ *Task declined.* No task was created.")

    elif result.already_resolved:
        send(f"ℹ️ {result.reply_text}")

    elif result.expired:
        send("⏰ Confirmation window expired.")

    elif result.error:
        send(f"⚠️ {result.reply_text}")


def poll(offset: int) -> int:
    try:
        resp = requests.get(
            f"{BASE}/getUpdates",
            params={"offset": offset, "timeout": POLL_TIMEOUT},
            timeout=POLL_TIMEOUT + 5,
        ).json()
    except Exception as exc:
        print(f"  poll error: {exc}")
        return offset

    for update in resp.get("result", []):
        uid = update["update_id"]

        msg = update.get("message") or update.get("channel_post")
        if msg:
            text = msg.get("text", "")
            if text.startswith("/"):
                print(f"  cmd: {text.split()[0]}")
                handle_command(text)

        cb = update.get("callback_query")
        if cb:
            print(f"  callback: {cb.get('data','')}")
            handle_cb(cb)

        offset = uid + 1

    return offset


print("🤖 MailMate AI bot runner started. Listening for commands…")
print("   Send /start, /tasks, /status or click buttons in Telegram.")
print("   Press Ctrl+C to stop.\n")

offset = 0
while running:
    offset = poll(offset)
