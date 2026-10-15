"""Send test emails to the authenticated Gmail account covering all priority cases."""

import base64
import os
import sys
from email.mime.text import MIMEText
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

from googleapiclient.discovery import build
from app.integrations.gmail import load_credentials

creds = load_credentials()
service = build("gmail", "v1", credentials=creds, cache_discovery=False)

# Get the authenticated user's email address
profile = service.users().getProfile(userId="me").execute()
my_email = profile["emailAddress"]
print(f"Sending to: {my_email}\n")

TEST_EMAILS = [
    {
        "subject": "[MailMate Test v2] P0 — Production database is down 2026-10-15",
        "body": (
            "CRITICAL: The primary production database cluster went offline at 09:00 UTC on 2026-10-15. "
            "All customer-facing services are returning 500 errors and no bookings can be made. "
            "The incident was declared at 09:05 UTC. "
            "Please join the incident call and restore the database cluster before 10:00 UTC. "
            "Revenue loss is estimated at $5,000 per minute."
        ),
    },
    {
        "subject": "[MailMate Test v2] P1 — Invoice #INV-9822 due 2026-10-30",
        "body": (
            "Please process payment for invoice INV-9822 totalling $3,500 by 2026-10-30. "
            "Failure to pay by the due date will incur a 2% late fee. "
            "Bank transfer details: IBAN GB29 NWBK 6016 1331 9268 19, Reference INV-9822. "
            "Please confirm once payment has been made."
        ),
    },
    {
        "subject": "[MailMate Test v2] P2 — Review the Q3 performance report",
        "body": (
            "Hi, could you please review the Q3 performance report and share your feedback? "
            "No hard deadline — just whenever you can fit it in. "
            "The document covers revenue, churn, and NPS for Q3 2026. "
            "Reply with your comments when ready."
        ),
    },
    {
        "subject": "[MailMate Test v2] OTHER — Monthly product newsletter",
        "body": (
            "Welcome to the October 2026 product newsletter! "
            "This month we shipped dark mode, improved search performance by 40%, "
            "and launched our new mobile app. "
            "No action is needed — this is a summary of what we shipped. "
            "Thanks for being part of our community!"
        ),
    },
]

for email in TEST_EMAILS:
    msg = MIMEText(email["body"])
    msg["to"] = my_email
    msg["from"] = my_email
    msg["subject"] = email["subject"]

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    print(f"✅ Sent: {email['subject']}")

print(f"\nAll 4 test emails sent to {my_email}")
print("Wait a few seconds then run:  python scripts/live_run.py")
