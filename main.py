"""MailMate AI Desktop — main entry point.

One script that does everything:
  • Fetches unread Gmail emails
  • Classifies with Laya, applies policy, persists tasks to SQLite
  • Sends Telegram batch summary + individual task notifications
  • Creates Google Calendar events for tasks with clear deadlines
  • Listens for Telegram commands (/start, /tasks, /status) and
    inline-button callbacks (confirm / decline) in a background thread

Usage:
    python main.py            # run once (cron-friendly)
    python main.py --loop     # poll Gmail every POLL_INTERVAL seconds
    python main.py --max 20   # fetch up to 20 emails per run
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
import requests
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mailmate")

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
BASE  = f"https://api.telegram.org/bot{TOKEN}"

from app.integrations.calendar import GoogleCalendarTransport
from app.integrations.gmail import GmailClient, load_credentials
from app.integrations.telegram import TelegramTransport
from app.services.calendar_service import schedule_event, CREATED
from app.services.confirmation_handler import handle_callback
from app.services.gmail_poller import fetch_new_emails
from app.services.notifier import notify, send_poll_summary
from app.services.orchestrator import process_email, TASK_CREATED, AWAITING_CONFIRMATION
from app.storage.task_store import TaskStore, TaskStatus

EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "OTHER": "⚪"}

# ── Telegram helpers ──────────────────────────────────────────────────────────

def tg_send(text: str):
    try:
        requests.post(f"{BASE}/sendMessage",
                      json={"chat_id": CHAT, "text": text,
                            "parse_mode": "Markdown",
                            "disable_web_page_preview": True},
                      timeout=10)
    except Exception as exc:
        log.warning("tg_send failed: %s", exc)


def tg_answer(cb_query_id: str, text: str):
    try:
        requests.post(f"{BASE}/answerCallbackQuery",
                      json={"callback_query_id": cb_query_id, "text": text},
                      timeout=10)
    except Exception as exc:
        log.warning("tg_answer failed: %s", exc)


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


# ── Command handler ───────────────────────────────────────────────────────────

def handle_command(text: str, store: TaskStore):
    cmd = text.strip().split()[0].lower().split("@")[0]
    log.info("command: %s", cmd)

    if cmd in ("/start", "/help"):
        tg_send(WELCOME)

    elif cmd == "/tasks":
        all_tasks = store.list_tasks()
        # Show all active tasks — exclude declined and cancelled
        active = [
            t for t in all_tasks
            if t.status.value not in ("DECLINED", "CANCELLED")
        ]
        if not active:
            tg_send("✅ No active tasks right now.")
            return
        msg = f"📋 *Active Tasks ({len(active)})*\n\n"
        for t in active:
            e = EMOJI.get(t.priority, "⚪")
            dl = f" | 📅 {t.deadline}" if t.deadline else ""
            if t.confirmation_status.value == "PENDING":
                badge = " | ⏳ _awaiting confirmation_"
            elif t.confirmation_status.value == "NOT_REQUIRED":
                badge = " | ✅ _auto-created_"
            else:
                badge = " | ✔️ _confirmed_"
            msg += f"{e} *{t.title[:55]}*{dl}{badge}\n\n"
        tg_send(msg)

    elif cmd == "/status":
        tasks = store.list_tasks()
        active = [t for t in tasks if t.status.value not in ("DECLINED", "CANCELLED")]
        awaiting  = sum(1 for t in active if t.confirmation_status.value == "PENDING")
        confirmed = sum(1 for t in active if t.confirmation_status.value == "CONFIRMED")
        auto      = sum(1 for t in active if t.confirmation_status.value == "NOT_REQUIRED")
        with_cal  = sum(1 for t in active if t.calendar_event_id)
        tg_send(
            f"📊 *MailMate AI Status*\n\n"
            f"Total active tasks: {len(active)}\n"
            f"✅ Auto-created: {auto}\n"
            f"✔️ Confirmed by you: {confirmed}\n"
            f"⏳ Awaiting confirmation: {awaiting}\n"
            f"🗓️ With calendar event: {with_cal}\n\n"
            f"_Pipeline: Gmail → Laya → Policy → SQLite → Telegram → Calendar_"
        )

    elif cmd == "/poll":
        tg_send("🔄 Poll running... check back in a moment.")

    else:
        tg_send(f"Unknown command: `{cmd}`. Try /help")


# ── Callback handler ──────────────────────────────────────────────────────────

def handle_cb(cb: dict, store: TaskStore):
    data        = cb.get("data", "")
    cb_query_id = cb["id"]

    result = handle_callback(data, store)
    tg_answer(cb_query_id, result.reply_text)
    log.info("callback %s → accepted=%s declined=%s", data[:30],
             result.accepted, result.declined)

    if result.accepted and result.task_id:
        task = store.get_by_id(result.task_id)
        e = EMOJI.get(task.priority, "⚪")
        tg_send(f"✅ *Task confirmed!*\n\n{e} *{task.title}*")

        # Auto-create calendar event for tasks with a clear deadline
        if task.deadline_state == "CLEAR" and task.deadline and not task.calendar_event_id:
            try:
                transport = GoogleCalendarTransport(live_writes=True)
                cal = schedule_event(task, store, transport,
                                     time="09:00", timezone="Asia/Kolkata")
                if cal.status == CREATED:
                    tg_send(f"🗓️ *Calendar event created!*\n📅 {task.deadline} at 09:00 IST")
                else:
                    tg_send(f"⚠️ Calendar: {cal.reason}")
            except Exception as exc:
                log.error("calendar error: %s", exc)
                tg_send(f"⚠️ Calendar error: {type(exc).__name__}")

    elif result.declined:
        tg_send("❌ *Task declined.* No task was created.")
    elif result.already_resolved:
        tg_send(f"ℹ️ {result.reply_text}")
    elif result.expired:
        tg_send("⏰ Confirmation window expired.")
    elif result.error:
        tg_send(f"⚠️ {result.reply_text}")


# ── Telegram long-poll thread ─────────────────────────────────────────────────

def tg_listener(store: TaskStore, stop_event: threading.Event,
                 pipeline_fn=None):
    """Background thread: long-polls Telegram and handles commands + callbacks.

    pipeline_fn: callable that runs the email pipeline — invoked on /poll.
    """
    offset = 0
    _poll_running = threading.Event()
    log.info("Telegram listener started")

    while not stop_event.is_set():
        try:
            resp = requests.get(
                f"{BASE}/getUpdates",
                params={"offset": offset, "timeout": 20},
                timeout=25,
            ).json()
        except Exception as exc:
            log.warning("getUpdates error: %s", exc)
            time.sleep(3)
            continue

        for update in resp.get("result", []):
            offset = update["update_id"] + 1

            msg = update.get("message") or update.get("channel_post")
            if msg:
                text = msg.get("text", "")
                if text.startswith("/poll"):
                    if pipeline_fn and not _poll_running.is_set():
                        _poll_running.set()
                        tg_send("🔄 *Polling Gmail now…*")
                        def _run():
                            try:
                                pipeline_fn()
                            finally:
                                _poll_running.clear()
                        threading.Thread(target=_run, daemon=True).start()
                    elif _poll_running.is_set():
                        tg_send("⏳ Poll already running, please wait.")
                    else:
                        tg_send("⚠️ Poll not available in this mode.")
                elif text.startswith("/"):
                    handle_command(text, store)

            cb = update.get("callback_query")
            if cb:
                handle_cb(cb, store)

    log.info("Telegram listener stopped")


# ── Email pipeline ────────────────────────────────────────────────────────────

def run_pipeline(agent, gmail, notif_transport, store, max_emails, live_mode):
    log.info("Starting email pipeline run (live_mode=%s)", live_mode)

    emails = list(fetch_new_emails(gmail, store, max_results=max_emails))
    log.info("Fetched %d new email(s)", len(emails))

    if not emails:
        log.info("No new emails to process.")
        return

    results = []
    for email in emails:
        result = process_email(email, agent, store, dry_run=not live_mode)
        log.info("[%s] %s → %s", result.status, email.message_id[:12],
                 result.candidate.priority if result.candidate else "—")

        if result.notify and notif_transport:
            notify(result, notif_transport)

            # Auto-create calendar events for P0/P1 tasks with clear deadlines
            if (result.status == TASK_CREATED
                    and result.task_id and live_mode):
                task = store.get_by_id(result.task_id)
                if (task.deadline_state == "CLEAR"
                        and task.deadline
                        and not task.calendar_event_id):
                    try:
                        transport = GoogleCalendarTransport(live_writes=True)
                        cal = schedule_event(task, store, transport,
                                             time="09:00", timezone="Asia/Kolkata")
                        if cal.status == CREATED:
                            log.info("Calendar event created: %s", cal.event_id)
                            tg_send(f"🗓️ *Calendar event created!*\n"
                                    f"📅 {task.deadline} at 09:00 IST\n"
                                    f"_{task.title}_")
                    except Exception as exc:
                        log.error("calendar error: %s", exc)

        results.append(result)

    if notif_transport and results:
        send_poll_summary(results, notif_transport)

    created = sum(1 for r in results if r.status == TASK_CREATED)
    pending = sum(1 for r in results if r.status == AWAITING_CONFIRMATION)
    log.info("Run complete — %d processed, %d tasks created, %d awaiting confirmation",
             len(results), created, pending)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MailMate AI Desktop")
    parser.add_argument("--max",  type=int, default=10, help="Emails per poll")
    parser.add_argument("--loop", action="store_true",
                        help="Keep polling Gmail on POLL_INTERVAL schedule")
    args = parser.parse_args()

    live_mode    = os.environ.get("LIVE_MODE", "false").lower() == "true"
    poll_interval = int(os.environ.get("POLL_INTERVAL", "300"))
    db_path      = os.environ.get("DB_PATH", "mailmate.db")

    log.info("=== MailMate AI — %s MODE ===", "LIVE" if live_mode else "DRY-RUN")

    # Load Laya model
    log.info("Loading Laya model…")
    import laya
    agent = laya.load("convaiinnovations/laya", device="cpu", subfolder="typed-decisions")
    log.info("Model loaded.")

    # Connect Gmail
    log.info("Connecting to Gmail…")
    gmail = GmailClient(load_credentials())
    log.info("Gmail connected.")

    # Telegram transport (for outbound notifications)
    notif_transport = TelegramTransport() if live_mode and TOKEN else None

    # SQLite store
    store = TaskStore(db_path if live_mode else ":memory:")

    # Convenience wrapper so /poll can trigger the pipeline
    def _pipeline():
        run_pipeline(agent, gmail, notif_transport, store, args.max, live_mode)

    # Start Telegram listener thread
    stop_event = threading.Event()
    listener = threading.Thread(
        target=tg_listener, args=(store, stop_event, _pipeline), daemon=True
    )
    listener.start()

    def _shutdown(sig, frame):
        log.info("Shutting down…")
        stop_event.set()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Run pipeline
    run_pipeline(agent, gmail, notif_transport, store, args.max, live_mode)

    if args.loop:
        log.info("Loop mode ON — polling every %ds + listening for /poll command. Ctrl+C to stop.", poll_interval)
        while not stop_event.is_set():
            time.sleep(poll_interval)
            if not stop_event.is_set():
                run_pipeline(agent, gmail, notif_transport, store, args.max, live_mode)
    else:
        # Single run — keep listener alive for callbacks and /poll
        log.info("Listening for Telegram commands and button clicks (Ctrl+C to stop)…")
        stop_event.wait()


if __name__ == "__main__":
    main()
