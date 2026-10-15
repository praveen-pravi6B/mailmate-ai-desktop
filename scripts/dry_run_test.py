"""Live dry-run test — fetch real Gmail, classify, show pipeline decisions.

No tasks are written. No notifications are sent. Safe to run at any time.

Usage:
    python scripts/dry_run_test.py [--max N]
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import laya
from app.integrations.gmail import GmailClient, load_credentials
from app.services.gmail_poller import fetch_new_emails
from app.services.orchestrator import process_email
from app.storage.task_store import TaskStore

PRIORITY_LABEL = {"P0": "🔴 P0", "P1": "🟠 P1", "P2": "🟡 P2", "OTHER": "⚪ OTHER", "REVIEW": "⚠️ REVIEW"}
STATUS_LABEL = {
    "SKIPPED": "⏭  SKIPPED",
    "DRY_RUN": "🔍 DRY RUN",
    "TASK_CREATED": "✅ TASK CREATED",
    "AWAITING_CONFIRMATION": "❓ AWAITING CONFIRM",
    "NO_ACTION": "📄 NO ACTION",
    "NEEDS_REVIEW": "⚠️  NEEDS REVIEW",
    "ERROR": "❌ ERROR",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=10, help="Max emails to fetch")
    args = parser.parse_args()

    print("\n=== MailMate AI — Live Dry-Run Test ===\n")
    print("Loading Laya model (CPU)…")
    agent = laya.load("convaiinnovations/laya", device="cpu", subfolder="typed-decisions")
    print("Model loaded.\n")

    print("Connecting to Gmail…")
    creds = load_credentials()
    gmail = GmailClient(creds)
    print("Gmail connected.\n")

    store = TaskStore(":memory:")  # in-memory — nothing persisted

    emails = list(fetch_new_emails(gmail, store, max_results=args.max))
    print(f"Fetched {len(emails)} unread email(s).\n")

    if not emails:
        print("Inbox is empty or all emails already processed.")
        return

    print(f"{'#':<3}  {'Subject':<45}  {'Action':<22}  {'Priority':<12}  {'Deadline'}")
    print("─" * 105)

    for i, email in enumerate(emails, 1):
        result = process_email(email, agent, store, dry_run=True)

        subject = email.subject[:43] + "…" if len(email.subject) > 45 else email.subject
        action  = STATUS_LABEL.get(result.status, result.status)

        if result.candidate:
            priority = PRIORITY_LABEL.get(result.candidate.priority, result.candidate.priority)
            deadline = result.candidate.deadline or f"[{result.candidate.deadline_state}]"
        else:
            priority = "—"
            deadline = "—"

        print(f"{i:<3}  {subject:<45}  {action:<22}  {priority:<12}  {deadline}")

    print("\n" + "─" * 105)
    print(f"\nDry-run complete. {len(emails)} email(s) processed. Nothing was written.\n")
    print("To enable live mode, set LIVE_MODE=true in .env and re-run the app.")


if __name__ == "__main__":
    main()
