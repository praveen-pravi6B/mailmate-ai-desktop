"""Live run — fetch real Gmail, classify, create tasks, send Telegram notifications.

Set LIVE_MODE=true in .env before running.

Usage:
    python scripts/live_run.py [--max N]
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import laya
from app.integrations.gmail import GmailClient, load_credentials
from app.integrations.telegram import TelegramTransport
from app.services.gmail_poller import fetch_new_emails
from app.services.notifier import notify, send_poll_summary
from app.services.orchestrator import process_email
from app.storage.task_store import TaskStore

STATUS_ICON = {
    "SKIPPED": "⏭",
    "DRY_RUN": "🔍",
    "TASK_CREATED": "✅",
    "AWAITING_CONFIRMATION": "❓",
    "NO_ACTION": "📄",
    "NEEDS_REVIEW": "⚠️",
    "ERROR": "❌",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=10)
    args = parser.parse_args()

    live_mode = os.environ.get("LIVE_MODE", "false").lower() == "true"

    print(f"\n=== MailMate AI — {'LIVE' if live_mode else 'DRY-RUN'} MODE ===\n")

    print("Loading Laya model…")
    agent = laya.load("convaiinnovations/laya", device="cpu", subfolder="typed-decisions")
    print("Model loaded.\n")

    print("Connecting to Gmail…")
    gmail = GmailClient(load_credentials())
    print("Gmail connected.\n")

    notif = TelegramTransport() if live_mode else None
    db_path = os.environ.get("DB_PATH", "mailmate.db")
    store = TaskStore(db_path if live_mode else ":memory:")

    print(f"Database: {'mailmate.db' if live_mode else ':memory: (dry-run)'}\n")

    emails = list(fetch_new_emails(gmail, store, max_results=args.max))
    print(f"Fetched {len(emails)} unread email(s).\n")

    if not emails:
        print("No new emails to process.")
        return

    summary = {"TASK_CREATED": 0, "AWAITING_CONFIRMATION": 0,
               "NO_ACTION": 0, "NEEDS_REVIEW": 0, "ERROR": 0, "SKIPPED": 0}
    all_results = []

    for i, email in enumerate(emails, 1):
        result = process_email(email, agent, store, dry_run=not live_mode)
        icon = STATUS_ICON.get(result.status, "?")
        subject = email.subject[:55] + "…" if len(email.subject) > 55 else email.subject
        priority = result.candidate.priority if result.candidate else "—"
        deadline = (result.candidate.deadline or
                    f"[{result.candidate.deadline_state}]") if result.candidate else "—"

        print(f"{icon} [{i:>2}] {subject}")
        print(f"       Priority: {priority}  |  Deadline: {deadline}  |  Status: {result.status}")
        if result.reason:
            print(f"       Reason: {result.reason}")

        if result.notify and notif:
            ok = notify(result, notif)
            print(f"       Telegram: {'sent ✓' if ok else 'failed ✗'}")

        print()
        summary[result.status] = summary.get(result.status, 0) + 1
        all_results.append(result)

    print("─" * 70)
    print(f"\nSummary: {', '.join(f'{v} {k}' for k, v in summary.items() if v)}\n")

    # Send batch poll summary to Telegram
    if notif and all_results:
        send_poll_summary(all_results, notif)
        print("Telegram poll summary sent.\n")

    if live_mode:
        all_tasks = store.list_tasks()
        print(f"Tasks in DB: {len(all_tasks)}")
        for t in all_tasks:
            print(f"  [{t.priority}] {t.title[:60]}  |  {t.status.value}  |  {t.confirmation_status.value}")


if __name__ == "__main__":
    main()
