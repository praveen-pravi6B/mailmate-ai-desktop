# MailMate AI Desktop

A local-first macOS email assistant that identifies actionable emails,
applies an explicit decision policy, and surfaces tasks via a menu bar
app and Telegram — without sending data to any cloud backend.

---

## What it does

```
Gmail (read-only)
  → Normalize email
  → HuggingFace Summarizer      ← human-readable summary of each email
  → Laya typed-decision engine  ← actionability + priority classification
  → Application-side policy     ← what is permitted
  → SQLite task store           ← persist if approved
  → Telegram notification       ← summary + notify / ask for confirmation
  → Google Calendar             ← separate explicit step, never automatic
```

**Two models, two jobs:**

| Model | Job | Runs on |
|---|---|---|
| `openai/gpt-oss-120b:fastest` | Summarize each email into 1-2 sentences | HuggingFace Inference API |
| `convaiinnovations/laya` | Classify actionability + priority | Local CPU |

The model classifies; the policy decides; you confirm anything routine.
No model output can bypass that chain.

**Priority levels**

| Priority | Meaning | Policy |
|---|---|---|
| P0 | Critical incident / immediate serious risk | Create task + notify (needs clear deadline) |
| P1 | Important with deadline / business impact | Create task + notify (needs clear deadline) |
| P2 | Routine actionable work | Ask for confirmation first |
| OTHER | No action required | Summarize only |
| REVIEW | Uncertain classification | Human review, no automated action |

**Deadline safety**

- `CLEAR` — explicit calendar date found → policy may proceed
- `AMBIGUOUS` — relative term (tomorrow, ASAP, next Friday) → clarify first
- `NONE` — no date mentioned → P0/P1 summarize only; P2 may still confirm

---

## Requirements

- macOS (Intel or Apple Silicon)
- Python 3.11
- Gmail account (OAuth, read-only scope)
- Telegram bot (optional, for notifications)
- Google Calendar (optional, separate explicit step)

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone git@github-personal:praveen-pravi6B/mailmate-ai-desktop.git
cd mailmate-ai-desktop
python3.11 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install laya \
    beautifulsoup4 \
    google-api-python-client google-auth-oauthlib google-auth-httplib2 \
    rumps
```

### 3. Configure environment variables

Create a `.env` file (never commit this):

```dotenv
# HuggingFace (email summarization)
HF_TOKEN=your_hf_token_here
HF_SUMMARY_MODEL=openai/gpt-oss-120b:fastest

# Google OAuth
GOOGLE_TOKEN_PATH=token.json
GOOGLE_CREDENTIALS_PATH=credentials.json

# Telegram (optional)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Google Calendar (optional)
GOOGLE_CALENDAR_ID=primary
```

### 4. Obtain Google credentials (Gmail + Calendar)

1. Go to [Google Cloud Console](https://console.cloud.google.com).
2. Create a project → enable both **Gmail API** and **Google Calendar API**.
3. Configure OAuth consent screen → External → add your Gmail as a test user.
4. Create OAuth 2.0 Client ID (Desktop app type) → Download JSON → save as `credentials.json` in the project root.
5. Run the one-time auth setup:

```bash
python auth_setup.py
```

This opens a browser for OAuth consent covering both Gmail (read-only)
and Calendar (event creation). `token.json` is saved and auto-refreshed
on subsequent runs.

### 5. Run the tests

```bash
python -m unittest discover -s tests -v
```

All 361 tests should pass. No real Gmail, Telegram, or Calendar calls
are made during tests.

---

## Running the app

### Dry-run mode (safe, default)

Runs the full pipeline without writing tasks or sending notifications.
Use this to verify normalization and classification on real emails before
enabling live mode.

```python
from app.ui.menu_bar import run
run(db_path="mailmate.db", live_mode=False)
```

Or from the command line:

```bash
python -m app.ui.menu_bar
```

### Live mode

Only enable after verifying dry-run output on a controlled set of emails.

```python
from app.integrations.gmail import GmailClient, load_credentials
from app.integrations.telegram import TelegramTransport
import laya

agent = laya.load("convaiinnovations/laya", device="cpu", subfolder="typed-decisions")
gmail = GmailClient(load_credentials())
notif = TelegramTransport()   # reads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from env

from app.ui.menu_bar import run
run(
    db_path="mailmate.db",
    live_mode=True,
    agent=agent,
    gmail_transport=gmail,
    notification_transport=notif,
    poll_interval=300,  # seconds
)
```

### Evaluate the model on the demo set

```bash
python app/laya_engine/evaluate.py
```

Results are saved to `tests/fixtures/laya_evaluation_results.json`.

**Verified baseline (demo set, 8 examples):**
- Overall: 6/8 (75%) — actionability: 7/8 (87.5%) — priority: 5/6 (83.3%)

These numbers are not independent validation. See `docs/limitations.md`.

---

## Architecture

```
app/
  laya_engine/
    classifier.py       — Laya wrapper → ClassificationResult
    evaluate.py         — evaluation script (no side effects)
    smoke_test.py       — single-email classification demo
  policy/
    decision_policy.py  — DeadlineState enum + decide() → PolicyDecision
  models/
    email.py            — NormalizedEmail dataclass
    task.py             — TaskCandidate dataclass
  services/
    email_normalizer.py       — raw dict → NormalizedEmail
    task_extractor.py         — email + classification → TaskCandidate
    classification_pipeline.py — ClassificationResult → PolicyDecision
    orchestrator.py           — full pipeline: fetch → classify → policy → store
    gmail_poller.py           — Gmail transport → NormalizedEmail generator
    notifier.py               — PolicyDecision → Telegram message
    confirmation_handler.py   — callback → confirm/decline task
    calendar_service.py       — eligibility check + schedule event
  integrations/
    gmail.py            — GmailTransport ABC + MockGmailTransport + GmailClient
    telegram.py         — NotificationTransport ABC + MockTransport + TelegramTransport
    calendar.py         — CalendarTransport ABC + MockCalendarTransport + GoogleCalendarTransport
  storage/
    task_store.py       — SQLite task store with idempotency
  ui/
    menu_bar.py         — macOS menu bar app (rumps)
tests/
  fixtures/
    priority_emails.json          — 8-example demo dataset (prompt tuning)
    holdout_emails.json           — 24-example human-reviewed holdout set
    laya_evaluation_results.json  — last recorded evaluation output
  test_decision_policy.py
  test_task_store.py
  test_classifier.py
  test_classification_pipeline.py
  test_email_normalizer.py
  test_task_extractor.py
  test_orchestrator.py
  test_notifier.py
  test_confirmation_handler.py
  test_calendar_service.py
  test_gmail.py
  test_menu_bar.py
  test_end_to_end.py
  test_holdout_evaluation.py
docs/
  mailmate-ai-desktop-planner.md  — requirements, history, checklist
  limitations.md                  — known limitations and caveats
```

---

## Safety boundaries

These rules are enforced in code and tested:

- **Model predictions alone never create tasks.** The policy module
  (`decide()`) is the only authority.
- **P0/P1 without a clear deadline → summarize only.** No task is
  auto-created without a calendar date.
- **Ambiguous deadlines are never scheduled.** Relative terms (tomorrow,
  ASAP, next Friday) always route to `CLARIFY_DEADLINE`.
- **P2 tasks require user confirmation.** `ASK_CONFIRMATION` is returned;
  the task is persisted with `PENDING` status until the user responds.
- **Declined tasks stay declined.** A declined P2 task cannot be
  re-confirmed and is never escalated.
- **Notifications only after a successful DB commit.** `notify=True` is
  set only after `store.create_task()` returns without error.
- **Calendar events are a separate step.** Task creation never
  automatically schedules a calendar event.
- **Idempotency.** Processing the same email twice is always safe — the
  second run returns `SKIPPED`.
- **Dry-run by default.** `process_email(..., dry_run=True)` is the
  default. Live writes require explicit `dry_run=False`.

---

## Telegram P2 confirmation

When a P2 task is created, a Telegram message is sent with **Confirm ✅**
and **Decline ❌** inline buttons. Callback data format:

```
confirm:<task_uuid>
decline:<task_uuid>
```

The `handle_callback()` function in `app/services/confirmation_handler.py`
processes these. A 48-hour confirmation window is enforced — expired
callbacks are rejected without modifying the task.

> **Note:** A `getUpdates` polling loop or webhook is required to receive
> Telegram callback queries. This is not yet implemented — add it before
> enabling live Telegram mode.

---

## Google Calendar

Calendar event creation is **explicitly gated**:

1. Call `check_eligibility(task, time, timezone)` — returns a result if
   the task is not eligible (wrong status, ambiguous deadline, missing
   time/timezone, duplicate event).
2. Call `schedule_event(task, store, transport, time, timezone)` — creates
   the event and persists the event ID only after the API confirms.

`GoogleCalendarTransport` raises `ValueError` unless `live_writes=True` is
passed. Use `MockCalendarTransport` in all tests and dry runs.

---

## Known limitations

See [`docs/limitations.md`](docs/limitations.md) for full details. Key points:

- Laya baseline is 6/8 (75%) on a small demo set — not production accuracy.
- The regex deadline detector produces false positives for day/time words
  in informational emails (no task impact for non-actionable emails).
- P0/P1 urgent incidents without a calendar date do not auto-create tasks
  under the current policy.
- Gmail, Telegram, and Calendar integrations are tested against mocks only
  and have not been run against real accounts.

---

## Credentials and secrets

The following files must **never be committed**:

| File | Purpose |
|---|---|
| `.env` | Environment variables |
| `token.json` | Gmail/Calendar OAuth token |
| `credentials.json` | OAuth client secrets |
| `client_secret*.json` | Alternative credential file names |
| `*.db`, `*.sqlite` | Task database (may contain task content) |

All are covered by `.gitignore`.
