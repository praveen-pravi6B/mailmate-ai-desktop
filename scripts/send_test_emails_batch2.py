"""Send 2nd batch of test emails — triggered by /poll from Telegram."""

import base64, os, sys
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

from email.mime.text import MIMEText
from googleapiclient.discovery import build
from app.integrations.gmail import load_credentials

creds   = load_credentials()
service = build("gmail", "v1", credentials=creds, cache_discovery=False)
profile = service.users().getProfile(userId="me").execute()
me      = profile["emailAddress"]
print(f"Sending to: {me}\n")

BATCH2 = [
    {
        "subject": "[MailMate B2] P0 — Security breach: admin credentials leaked",
        "body": (
            "URGENT: Our security team detected that admin credentials were exposed "
            "in a public GitHub commit at 2026-10-15T12:00:00Z. "
            "Affected systems include the admin panel and CI/CD pipeline. "
            "Please revoke all admin tokens and rotate credentials before 2026-10-15. "
            "Incident ticket: SEC-4821."
        ),
    },
    {
        "subject": "[MailMate B2] P1 — Design review deadline 2026-10-28",
        "body": (
            "Hi, the design review for the new onboarding flow must be completed by 2026-10-28 "
            "so the engineering team can begin implementation in the next sprint. "
            "Please review the Figma prototype and leave your comments in Notion. "
            "Tag me when done."
        ),
    },
    {
        "subject": "[MailMate B2] P2 — Send the meeting notes from today",
        "body": (
            "Could you send over the meeting notes from this morning's standup? "
            "The team needs them for the wiki."
        ),
    },
    {
        "subject": "[MailMate B2] OTHER — Office closed on 2026-10-24",
        "body": (
            "Just a reminder that the office will be closed on Friday 2026-10-24 "
            "for the company offsite. Remote work is available as usual. "
            "No action required — have a great long weekend!"
        ),
    },
]

for email in BATCH2:
    msg = MIMEText(email["body"])
    msg["to"]      = me
    msg["from"]    = me
    msg["subject"] = email["subject"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"✅  {email['subject']}")

print(f"\nBatch 2 sent to {me}. Now send /poll from Telegram.")
