"""Poll Telegram for commands (/start, /help, /tasks, /status) and callbacks."""

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

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT  = os.environ["TELEGRAM_CHAT_ID"]
BASE  = f"https://api.telegram.org/bot{TOKEN}"

from app.services.confirmation_handler import handle_callback
from app.storage.task_store import TaskStore, TaskStatus

store = TaskStore(os.environ.get("DB_PATH", "mailmate.db"))


def send(text: str):
    requests.post(f"{BASE}/sendMessage",
                  json={"chat_id": CHAT, "text": text,
                        "parse_mode": "Markdown", "disable_web_page_preview": True})


def answer_cb(cb_query_id: str, text: str):
    requests.post(f"{BASE}/answerCallbackQuery",
                  json={"callback_query_id": cb_query_id, "text": text})


WELCOME = (
    "👋 *Welcome to MailMate AI!*\n\n"
    "I watch your Gmail inbox, classify emails by priority using the "
    "*Laya* typed\\-decision engine, and create tasks for actionable emails\\.\n\n"
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
    cmd = text.split()[0].lower().split("@")[0]

    if cmd == "/start" or cmd == "/help":
        send(WELCOME)

    elif cmd == "/tasks":
        tasks = store.list_tasks(status=TaskStatus.PENDING)
        if not tasks:
            send("✅ No pending tasks right now\\.")
            return
        EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡"}
        msg = f"📋 *Pending Tasks \\({len(tasks)}\\)*\n\n"
        for t in tasks:
            e = EMOJI.get(t.priority, "⚪")
            dl = f" | 📅 {t.deadline}" if t.deadline else ""
            conf = " \\| ⏳ _awaiting confirmation_" if t.confirmation_status.value == "PENDING" else ""
            msg += f"{e} *{t.title[:55]}*{dl}{conf}\n\n"
        send(msg)

    elif cmd == "/status":
        tasks = store.list_tasks()
        pending  = sum(1 for t in tasks if t.status.value == "PENDING")
        confirmed = sum(1 for t in tasks if t.status.value == "CONFIRMED")
        with_cal  = sum(1 for t in tasks if t.calendar_event_id)
        send(
            f"📊 *MailMate AI Status*\n\n"
            f"✅ Tasks in DB: {len(tasks)}\n"
            f"⏳ Pending: {pending}\n"
            f"✔️ Confirmed: {confirmed}\n"
            f"🗓️ With calendar event: {with_cal}\n\n"
            f"_Pipeline: Gmail → Laya → Policy → SQLite → Telegram → Calendar_"
        )

    elif cmd == "/poll":
        send("🔄 Poll triggered\\. Run `python scripts/live_run.py` to process inbox\\.")

    else:
        send(f"Unknown command: `{cmd}`\\. Try /help")


# Fetch and process updates
resp = requests.get(f"{BASE}/getUpdates", params={"timeout": 10}).json()
updates = resp.get("result", [])
print(f"Updates: {len(updates)}")

last_id = None
for update in updates:
    last_id = update["update_id"]

    # Handle commands
    msg = update.get("message") or update.get("channel_post")
    if msg and msg.get("text", "").startswith("/"):
        cmd_text = msg["text"]
        print(f"  Command: {cmd_text}")
        handle_command(cmd_text)

    # Handle inline button callbacks
    cb = update.get("callback_query")
    if cb:
        data = cb.get("data", "")
        cb_id = cb["id"]
        print(f"  Callback: {data}")
        result = handle_callback(data, store)
        answer_cb(cb_id, result.reply_text)
        if result.accepted:
            task = store.get_by_id(result.task_id)
            send(f"✅ *Task confirmed:* _{task.title}_")
        elif result.declined:
            send("❌ *Task declined\\.*")

# Acknowledge updates
if last_id is not None:
    requests.get(f"{BASE}/getUpdates", params={"offset": last_id + 1, "timeout": 1})

print("Done.")
