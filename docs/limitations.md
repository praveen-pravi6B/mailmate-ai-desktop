# MailMate AI Desktop — Known Limitations

This document records known limitations discovered during development and
holdout evaluation.  These are not bugs to fix before first use — they
are behaviours to be aware of when interpreting results.

---

## 1. Laya model accuracy

**Baseline (demo set, 8 examples):** 6/8 overall (75%), 7/8 actionability
(87.5%), 5/6 priority (83.3%).

**Important caveats:**

- The demo dataset is small (8 examples) and was used during prompt
  tuning.  These numbers are **not independent validation**.
- An earlier session reported 8/8 (100%) on an untracked run; that result
  is not reproducible from the current files and must not be cited.
- The Laya checkpoint warns that some temperatures are outside [0.5, 5]
  and are clamped.  Treat all probabilities as **uncalibrated model
  outputs**, not statistical confidence.
- The holdout set (24 human-reviewed examples) exists for independent
  evaluation.  Run `app/laya_engine/evaluate.py` with
  `DATASET_PATH = tests/fixtures/holdout_emails.json` to obtain holdout
  metrics.  Do not retune prompts against the holdout set.

**Known misclassifications in the demo set:**

| Email | Error | Probabilities |
|---|---|---|
| `demo_meeting_followup` | P2 predicted as P1 | P1=0.44, P2=0.41 (near tie) |
| `demo_meeting_update` | NO predicted as YES | YES=0.51, NO=0.49 (near coin flip) |

---

## 2. Deadline detection (regex-based)

The task extractor uses structural regex patterns to detect deadline state.
This has known limitations.

### 2a. False positives — AMBIGUOUS when body is informational

Day-of-week names (Monday, Thursday, Friday, etc.) and time words (tonight,
this morning) are matched as relative terms even when they describe a
meeting time rather than a task deadline.

**Examples from the holdout set:**

| Email | Body excerpt | Detected | Expected |
|---|---|---|---|
| `hold_optional_invite` | "lunch-and-learn on Friday at noon" | AMBIGUOUS | NONE |
| `hold_meeting_rescheduled` | "moved to Thursday 3 PM" | AMBIGUOUS | NONE |
| `hold_urgent_maintenance` | "maintenance tonight from 11 PM" | AMBIGUOUS | NONE |
| `hold_newsletter_tech` | (no date — likely a false match elsewhere) | AMBIGUOUS | NONE |

**Impact:** For **non-actionable emails** (NO actionability), deadline
state is irrelevant — the policy fires on actionability first and returns
`SUMMARIZE_ONLY` regardless.  These false positives do not cause incorrect
task creation.

For **actionable emails**, false AMBIGUOUS may route a task to
`CLARIFY_DEADLINE` instead of proceeding directly.  This is the safe
direction (requires human confirmation).

### 2b. False negative — CLEAR missed when explicit date + day name present

When an email body contains a clear ISO date AND an incidental day-of-week
reference, the detector returns AMBIGUOUS because the mixed-signal logic
fires.

**Example:**

| Email | Body excerpt | Detected | Expected |
|---|---|---|---|
| `hold_mixed_content_task_buried` | "signature by 2026-10-29 ... team lunch is Friday" | AMBIGUOUS | CLEAR |

**Impact:** The task would be routed to `CLARIFY_DEADLINE` instead of
`CREATE_TASK_AND_NOTIFY`.  The human is asked to confirm the deadline
rather than the task being auto-created.  This is the safe direction.

### 2c. "Immediate" urgency without a calendar date

P0/P1 incidents using language like "immediately" or "now" produce
AMBIGUOUS or NONE — there is no calendar date to extract.  Under current
policy:

- P0/P1 + AMBIGUOUS → `CLARIFY_DEADLINE`
- P0/P1 + NONE → `SUMMARIZE_ONLY`

This means a real production outage email ("database is down, fix
immediately") does **not** auto-create a task under the current policy.
A human must confirm the timeline.

**Recommended mitigation:** For genuine P0 incidents, use Telegram
directly to create tasks manually until a calendar-date-aware extraction
approach is implemented.

---

## 3. Policy edge cases

### 3a. P1 without a deadline → SUMMARIZE_ONLY

A P1 email with no deadline produces `SUMMARIZE_ONLY` under the current
policy ("P0/P1 without deadline → summary-only").  Some P1 bugs or
overdue items would benefit from task creation even without a deadline.
This is a deliberate conservative choice — not a bug.

### 3b. P2 with an ambiguous deadline → CLARIFY_DEADLINE

A P2 task with a vague deadline ("ASAP", "sometime next week") routes to
`CLARIFY_DEADLINE` rather than `ASK_CONFIRMATION`.  This may surprise
users who expect routine tasks to appear in the confirmation queue
regardless of deadline vagueness.  The current policy prioritises not
scheduling ambiguous dates over convenience.

---

## 4. Gmail integration (not yet live)

The Gmail integration (`app/integrations/gmail.py`) is complete and tested
against `MockGmailTransport` but has not been run against a real Gmail
account.  Before enabling:

1. Obtain OAuth credentials from Google Cloud Console.
2. Run the authentication flow to obtain `token.json`.
3. Set `GOOGLE_TOKEN_PATH` and `GOOGLE_CREDENTIALS_PATH` env vars.
4. Test with a controlled set of 3–5 known emails using `dry_run=True`.
5. Verify normalization, classification, and policy routing on those emails.
6. Only then enable `live_mode=True`.

Token refresh is automated but if the refresh token expires you must
re-authenticate manually.

---

## 5. Telegram integration (not yet live)

`TelegramTransport` is implemented and tested against `MockTransport`.
To enable:

1. Create a bot via @BotFather.
2. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars.
3. Test `send_message` manually with a known text before wiring to the
   pipeline.
4. The P2 confirm/decline callback handler requires a webhook or polling
   loop to receive callback queries from Telegram.  This is not yet
   implemented — add `getUpdates` polling or set a webhook URL.

---

## 6. Google Calendar (not yet live)

`GoogleCalendarTransport` requires `live_writes=True` to instantiate.
Additional steps before enabling:
1. Add Calendar API scope to the OAuth credentials.
2. Re-authenticate to obtain a token with the calendar scope.
3. Test event creation with `dry_run=True` equivalent (check eligibility
   without calling `schedule_event`).
4. Only enable live writes after confirming that task_id → event_id
   persistence works correctly.

---

## 7. Menu bar UI

- The UI is macOS-only (`rumps` does not run on Linux or Windows).
- The P2 confirm/decline inline menu requires the app to be running when
  the user clicks — there is no persistent webhook.
- Task count in the tray badge is updated after each poll; it does not
  update in real time when tasks are confirmed/declined externally.

---

## 8. Holdout evaluation instructions

To run the Laya model against the holdout set:

```bash
# Temporarily point the evaluator at the holdout dataset:
DATASET_PATH=tests/fixtures/holdout_emails.json \
    python -c "
import app.laya_engine.evaluate as ev
ev.DATASET_PATH = __import__('pathlib').Path('tests/fixtures/holdout_emails.json')
ev.REPORT_PATH  = __import__('pathlib').Path('tests/fixtures/holdout_evaluation_results.json')
ev.main()
"
```

Or edit `DATASET_PATH` in `app/laya_engine/evaluate.py` temporarily.
Record the output in `tests/fixtures/holdout_evaluation_results.json`.
**Do not retune prompts based on these results.**
