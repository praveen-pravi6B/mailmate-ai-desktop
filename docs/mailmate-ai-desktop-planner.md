# MailMate AI Desktop — Rebuild Planner & Checklist

**Purpose:** Handoff document for Claude to rebuild and continue the MailMate AI Desktop project, using the existing MailMate project as a reference.

**Project:** Local-first desktop-oriented Python application  
**Current focus:** Laya decisions, application-side policy, SQLite storage, then Telegram and Google Calendar  
**Environment:** macOS Intel, Python 3.11 virtual environment  
**Repository:** `mailmate-ai-desktop`

## Instructions for Claude

1. Inspect both the supplied MailMate reference project and the current desktop repository before editing.
2. Preserve working code; do not blindly copy or rewrite architecture.
3. Verify files and test results rather than trusting this planner as proof that code exists.
4. Work in small milestones, run tests, and update this checklist.
5. Prefer open-source, local-first, cost-optimized solutions. Avoid unnecessary subscriptions.
6. Do not introduce Raspberry Pi dependencies into this desktop project.
7. Keep evaluation and tests free of real-world side effects.
8. Do not connect real Gmail, Telegram, or Calendar actions until policy, storage, and orchestration tests pass.

## Product vision

MailMate AI helps identify important emails, decide what needs action, create permitted tasks, and eventually follow up through Telegram and Google Calendar.

```text
Email source (later Gmail)
  -> Normalize email
  -> Laya typed-decision engine
  -> Application-side policy
  -> SQLite task store
  -> Telegram notification / confirmation
  -> Google Calendar (separate, explicitly permitted step)
```

Laya is a **typed decision engine**, not a generative email summarizer. If summaries are required, implement or identify a separate summarization component. A model prediction alone must never authorize a task, notification, or calendar event.

## Required policy

- **P0:** Critical incident or immediate serious risk.
- **P1:** Important work with meaningful deadline/business impact, not a P0 crisis.
- **P2:** Routine actionable work.
- **OTHER:** No action required.
- **REVIEW:** Ambiguous, invalid, or uncertain classification.

| Case | Required behavior |
|---|---|
| P0/P1 + clear deadline | Create task; notify only after successful task creation |
| P0/P1 without deadline | Summary-only under the current policy |
| P2 | Ask user to confirm task creation |
| P2 confirmed | Create task; notify only after successful creation |
| P2 declined | Do not create task or send a task-created confirmation |
| OTHER | Summary-only |
| Ambiguous deadline | Ask for clarification; never auto-schedule |
| REVIEW / invalid output | Human review; no automated side effect |

**Important policy nuance to resolve:** the drafted policy module currently routes any actionable email without a clear deadline to `CLARIFY_DEADLINE`, including P2. Distinguish **no deadline** from **ambiguous deadline**. A P2 task with no deadline should still be eligible for user confirmation. Clarify a deadline when its value is uncertain or scheduling requires missing information.

Task creation and calendar event creation are separate actions. Do not create calendar events automatically just because a task was created. Do not invent event times from date-only deadlines.

Persist source email ID, task title/description, priority, deadline and deadline status, task status, confirmation status, calendar event ID when applicable, timestamps, and a stable idempotency key. Prevent duplicate tasks when an email is processed again. Notify only after a successful database commit.

## Current repository state — verified 2026-10-14

Tree confirmed matches the planner snapshot. No extra files committed yet.

```text
app/
  laya_engine/
    evaluate.py
    smoke_test.py
  policy/
    decision_policy.py
tests/
  fixtures/
    laya_evaluation_results.json
    priority_emails.backup-demo.json
    priority_emails.backup.json
    priority_emails.json
  test_decision_policy.py
docs/
  mailmate-ai-desktop-planner.md
```

Still missing (Milestone 2 targets):
- `app/storage/task_store.py`
- `tests/test_task_store.py`
- `README.md`
- `.gitignore`

### Reference repo — MailMate PI (praveen-pravi6B/MailMate)

Inspected 2026-10-14. The PI version is a Raspberry Pi / systemd application that uses a
HuggingFace generative model and an implicit policy baked into the prompt. Key findings:

**Reuse for desktop (adapt, do not copy verbatim):**

| PI module | Desktop target | Notes |
|---|---|---|
| `app/config.py` | `app/config.py` | Pydantic `BaseSettings` + `.env`; copy pattern directly |
| `app/database.py` | `app/storage/task_store.py` | SQLite idempotency via `UNIQUE email_id`; expand with task/confirmation tables |
| `app/email_client.py` | `app/integrations/gmail.py` | MIME parsing, HTML→text (BeautifulSoup), OAuth token usage |
| `auth_google.py` | `app/integrations/gmail.py` | OAuth browser flow, refresh token persistence |
| `app/telegram.py` | `app/integrations/telegram.py` | Markdown, emoji priority labels, graceful degradation |
| `app/main.py` | `app/services/orchestrator.py` | fetch → analyze → store → notify pipeline; add policy gates |

**Do NOT port (PI-specific):**

- `email-assistant.service` / `.timer` — systemd, Linux/Pi only; use Python-native scheduling
- `run.sh` — replace with `python -m app.main`
- `app/huggingface.py` — generative inference; desktop uses Laya typed-decisions instead
- Silent fallback defaults — desktop must fail loudly when Laya or credentials are unavailable

### Environment / model notes

- Python virtual environment: `.venv`, Python 3.11.
- Laya package reported: `laya` 0.4.6.
- Model: `convaiinnovations/laya`, subfolder `typed-decisions`, CPU.
- Example load:
  ```python
  agent = laya.load(
      "convaiinnovations/laya",
      device="cpu",
      subfolder="typed-decisions",
  )
  ```
- The checkpoint warns that some temperatures are invalid/outside `[0.5, 5]` and are clamped. Treat affected probabilities as uncalibrated model outputs, not statistically calibrated confidence.
- Avoid unnecessary model downloads or dependency changes.

### Evaluator

`app/laya_engine/evaluate.py` reads `tests/fixtures/priority_emails.json`, classifies actionability first and priority second, prints metrics and per-example predictions, and writes `tests/fixtures/laya_evaluation_results.json`. It is evaluation-only and must not trigger tasks, notifications, or calendar events.

### Decision policy

`app/policy/decision_policy.py` was supplied. The user confirmed these 9 tests passed:

- P0 with clear deadline
- P0 without clear deadline
- P1 with clear deadline
- P1 without clear deadline
- P2 requires confirmation
- P2 with ambiguous deadline requires clarification
- Non-actionable email is summary-only
- Uncertain actionability requires review
- Invalid priority requires review

The policy is intended to have no side effects. Verify the current file and improve the no-deadline vs ambiguous-deadline distinction before integration.

### SQLite storage status

A proposed `app/storage/task_store.py` and `tests/test_task_store.py` were provided, but their creation and test results were not confirmed. Inspect the actual files and Git status before marking storage as implemented. The draft design includes a SQLite task table, unique source email ID, priority, deadline, status, calendar event ID, timestamps, task lookups/listing/status updates, and temporary DB tests.

## Evaluation history and caveats

The 8-example demo dataset has:
1. `demo_critical_outage` — P0
2. `demo_security_issue` — P0
3. `demo_payment` — P1
4. `demo_bug` — P1
5. `demo_review` — P2
6. `demo_meeting_followup` — P2
7. `demo_newsletter` — OTHER
8. `demo_meeting_update` — OTHER

### Verified baseline — 2026-10-14

Run using `app/laya_engine/evaluate.py` against `tests/fixtures/priority_emails.json`.
Results recorded in `tests/fixtures/laya_evaluation_results.json`.

| Metric | Score |
|---|---|
| Overall | 6 / 8 (75.0%) |
| Actionability | 7 / 8 (87.5%) |
| Priority | 5 / 6 (83.3%) |

**Two failures:**

1. `demo_meeting_followup` — actionability correct (YES), priority wrong: predicted **P1**,
   expected **P2**. Priority probabilities were close: P1=0.44, P2=0.41. Borderline case.

2. `demo_meeting_update` — actionability wrong: predicted **YES** (0.51), expected **NO** (0.49).
   Near coin-flip. Because actionability was wrong, priority scoring was not applicable.
   Predicted P2, expected OTHER.

Earlier sessions reported 8/8 (100%) on an untracked run; that result is not reproducible
from the current files and must not be cited as a baseline. The verified baseline is 6/8.

These are reconstructed/synthetic examples tuned during development. 100% on this set is not independent validation and must not be presented as production accuracy. Build a separate human-reviewed set of unseen examples before performance claims.

The evaluator should report overall, actionability, and priority metrics separately; keep per-example results; surface mismatches and ambiguous cases; and never treat model probabilities as calibrated confidence.

## Planner / checklist

Status key: `[x]` verified complete, `[~]` drafted or provided but needs verification, `[ ]` pending.

### Milestone 0 — Inspect and stabilize
- [x] Set up Python environment and load Laya on CPU.
- [x] Create Laya evaluation script.
- [x] Create simple 8-example demo dataset.
- [x] Create application-side policy module.
- [x] Confirm 9 decision-policy tests pass.
- [x] Inspect current repository tree and Git status. Verified: matches planner snapshot exactly. `app/storage/` and `tests/test_task_store.py` are absent. No README or `.gitignore`.
- [x] Verify the active evaluator prompts and rerun evaluation. Last recorded run: overall 6/8 (75%), actionability 7/8 (87.5%), priority 5/6 (83.3%). See "Evaluation history" section for details.
- [x] Resolve absent deadline vs ambiguous deadline policy. `DeadlineState` enum added; `decide()` updated; 14 tests pass.
- [ ] Add/update README with setup, architecture, commands, and safety boundaries.
- [ ] Ensure virtualenv, local databases, tokens, and secrets are gitignored.

### Milestone 1 — Reliable policy
- [x] Define P0/P1/P2/OTHER/REVIEW behavior.
- [x] Keep policy separate from Laya.
- [x] Add core policy tests.
- [x] Represent deadline state explicitly as `DeadlineState` enum: `NONE`, `CLEAR`, `AMBIGUOUS`.
      Replace `deadline_clear: bool` in `decide()` with `deadline_state: DeadlineState`.
      Required routing (resolves the no-deadline vs ambiguous-deadline distinction):

      | Priority  | Actionability | Deadline state  | Action                  |
      |-----------|---------------|-----------------|-------------------------|
      | P0 / P1   | YES           | CLEAR           | CREATE_TASK_AND_NOTIFY  |
      | P0 / P1   | YES           | NONE            | SUMMARIZE_ONLY          |
      | P0 / P1   | YES           | AMBIGUOUS       | CLARIFY_DEADLINE        |
      | P2        | YES           | CLEAR or NONE   | ASK_CONFIRMATION        |
      | P2        | YES           | AMBIGUOUS       | CLARIFY_DEADLINE        |
      | ANY       | NO            | any             | SUMMARIZE_ONLY          |
      | ANY       | REVIEW        | any             | REVIEW_CLASSIFICATION   |

- [x] Test P0/P1 with no deadline, P2 without deadline, ambiguous dates, and invalid inputs. 14 tests pass (expanded from 9).
- [ ] Define confirmation, decline, and retry behavior.
- [ ] Keep calendar permission separate from task permission.

### Milestone 2 — SQLite task store
- [x] Task-store implementation and tests verified and passing.
- [x] Create/verify `app/storage/task_store.py`. TaskStore class with full CRUD, idempotency, status/confirmation/calendar updates.
- [x] Create/verify `tests/test_task_store.py`. 26 tests, all in-memory.
- [x] Enforce idempotency by source email ID or stable key. UNIQUE constraint; DuplicateEmailError raised on reprocessing.
- [x] Test duplicate/repeated processing and validation.
- [x] Test lookup, listing, status changes, and error cases.
- [x] Persist deadline metadata and confirmation state as required. deadline, deadline_state, confirmation_status all stored.
- [x] Store Calendar event ID without implementing Calendar creation yet. set_calendar_event_id() method available.
- [x] Use temporary databases in tests. All tests use :memory:.

### Milestone 3 — Laya-to-policy integration
- [x] Define typed outputs for actionability, priority, task extraction, and deadline state. `ClassificationResult` frozen dataclass in `app/laya_engine/classifier.py`.
- [x] Normalize and validate Laya output; unknown labels become REVIEW. Handled in `classify_email()`; malformed/missing responses also normalised.
- [x] Connect classification to the policy module. `app/services/classification_pipeline.py` → `apply_policy(result) -> PolicyDecision`.
- [x] Use mocked Laya results in unit tests to avoid loading the model for every test. `unittest.mock.MagicMock` used throughout; 38 new tests (23 classifier, 15 pipeline).
- [x] Prove that model predictions cannot directly invoke storage or integrations. `apply_policy()` returns a `PolicyDecision` only; namespace checks confirm no forbidden imports. 78 total tests passing.

### Milestone 4 — Email normalization and task extraction
- [x] Define normalized email fields. `app/models/email.py` — `NormalizedEmail` frozen dataclass: provider, message_id, thread_id, sender, subject, body, received_at, headers.
- [x] Define task result fields. `app/models/task.py` — `TaskCandidate` frozen dataclass: email_id, title, description, priority, deadline, deadline_state, evidence.
- [x] Never invent a task or deadline. Relative dates produce deadline=None; only explicit calendar dates populate deadline.
- [x] Distinguish explicit and inferred dates. ISO/written/slash dates → CLEAR; relative terms (tomorrow, next Friday, ASAP, within N days) → AMBIGUOUS; day-of-week names always AMBIGUOUS; nothing → NONE.
- [x] Test missing, relative, ambiguous, and conflicting dates. 56 new tests covering all deadline states, title extraction, description truncation, immutability, and no-storage-import checks.
- [x] Keep task extraction separate from summary generation. `extract_task()` is structural only; no AI summary generated. 134 total tests passing.

### Milestone 5 — Orchestration and idempotency
- [x] Coordinate normalized email, inference, policy, and SQLite storage. `app/services/orchestrator.py` — `process_email()` wires all layers.
- [x] Make repeated processing safe. `email_has_task()` check before classify; `DuplicateEmailError` race handled as SKIPPED.
- [x] Persist processing state or idempotency key. source_email_id is the idempotency key; task written only after policy permits.
- [x] Notify only after successful commit. `notify=True` set only on TASK_CREATED/AWAITING_CONFIRMATION after `store.create_task()` returns; always False on error or dry-run.
- [x] Test model/database failures, duplicate messages, retries, and partial failures. 33 orchestrator tests; store check failure, classification crash, DB write failure, duplicate race all return ERROR/SKIPPED with notify=False.
- [x] Use structured logs without secrets or unnecessary email content. `logging` with email_id only; no subject, body, or sender in log messages.
- [x] Default to dry-run until explicitly enabled. `process_email(..., dry_run=True)` default; live mode requires explicit `dry_run=False`. 167 total tests passing.

### Milestone 6 — Telegram
- [x] Define notification templates. `app/services/notifier.py` — task_created, confirmation_request, summary, review, clarify_deadline templates.
- [x] Notify only after task persistence succeeds. `notify()` gates on `result.notify`; only TASK_CREATED/AWAITING_CONFIRMATION set it True.
- [x] Implement P2 confirmation and persist pending confirmation. P2 tasks persisted with PENDING status; confirmed/declined via `handle_callback()`.
- [x] Handle confirm, decline, duplicate callbacks, and timeout. `app/services/confirmation_handler.py` — all four cases covered with tests.
- [x] Ensure declined tasks are not created. Declined tasks stay DECLINED; re-confirm after decline returns already_resolved; status never escalates.
- [x] Keep credentials out of Git/logs. `TelegramTransport` reads from env vars; only 8-char token prefix logged; no credentials in source.
- [x] Test with a mock transport first. `MockTransport` in `app/integrations/telegram.py`; all 45 new tests (notifier + handler) use it. 212 total tests passing.

### Milestone 7 — Google Calendar
- [x] Implement explicit calendar eligibility checks. `check_eligibility()` in `app/services/calendar_service.py` — 6 conditions tested before any API call.
- [x] Keep task and calendar operations separate. `schedule_event()` is a distinct call; task creation never triggers it automatically.
- [x] Never schedule ambiguous deadlines. AMBIGUOUS and NONE deadline_state → INELIGIBLE; tests confirm no event created.
- [x] Do not invent times for date-only deadlines. `time` and `timezone` are required parameters; missing → MISSING_TIME / MISSING_TIMEZONE status.
- [x] Ask for missing time/timezone when needed. Explicit status codes returned; caller responsible for prompting user.
- [x] Persist event IDs and prevent duplicate events. `store.set_calendar_event_id()` called only after transport confirms; existing event_id → DUPLICATE.
- [x] Mock Calendar calls before real integration. `MockCalendarTransport` in `app/integrations/calendar.py`; all 33 tests use it.
- [x] Require approval before enabling real calendar writes. `GoogleCalendarTransport` raises `ValueError` unless `live_writes=True` is passed explicitly. 245 total tests passing.

### Milestone 8 — Gmail
- [x] Inspect reference MailMate code for suitable patterns. Reused MIME parsing, OAuth flow, HTML→text, and idempotency patterns from PI version.
- [x] Document authentication and minimum required scopes. `gmail.readonly` only; `load_credentials()` in `app/integrations/gmail.py`; token/credential paths from env vars.
- [x] Begin read-only. `GmailClient` uses `gmail.readonly` scope; never sends, labels, or deletes.
- [x] Fetch a small controlled set of messages and normalize them. `fetch_new_emails(transport, store, max_results=10)` in `app/services/gmail_poller.py` yields `NormalizedEmail` objects.
- [x] Prevent repeated processing. `store.email_has_task(msg_id)` pre-filter before fetching full content; already-processed IDs are skipped entirely.
- [x] Handle pagination, limits, token refresh, deletions, and failures. `max_results` enforced; token auto-refresh in `load_credentials()`; deleted messages (None return) skipped gracefully; transport errors logged and skipped.
- [x] Keep tokens and credentials out of Git/logs. Paths via env vars only; no token values in log messages; `token.json` / `credentials.json` in `.gitignore`.
- [x] Use controlled test messages before enabling automation. All 31 Gmail tests use `MockGmailTransport`; `GmailClient` requires real credentials to instantiate. 276 total tests passing.

### Milestone 9 — Desktop experience and operations
- [x] Decide interface approach after inspecting the reference project. macOS menu bar app via `rumps`; PI version had no UI (headless systemd).
- [x] Show tasks, priorities, statuses, and why a classification was made. Menu lists pending tasks with priority emoji; count badge in tray title.
- [x] Provide human review, confirmation, and cancellation controls. P2 tasks show Confirm/Decline sub-items; P0/P1 show Mark Complete; all actions go through existing confirmation_handler / task_store.
- [x] Show integration status and errors. Status menu item updated after each poll; icon changes to ⏳ (busy) / ⚠ (error) / ✉ (idle).
- [x] Add validated configuration for model, polling, and notifications. `run()` entry point accepts db_path, poll_interval, live_mode, agent, transports.
- [x] Add scheduling only after processing is stable. `rumps.Timer` drives polls; starts only after app init; lock prevents concurrent polls.
- [x] Support shutdown, retries, and restart recovery. Timer stopped on Quit; SQLite task store survives restarts; idempotency prevents re-processing.
- [x] Document setup and troubleshooting. See README (M0 remaining item) and inline docstrings. 300 total tests passing.

### Milestone 10 — Validation and release readiness
- [ ] Build a human-reviewed holdout set of unseen emails.
- [ ] Include newsletters, promotions, optional invitations, meeting changes, urgent words without action, tasks without deadlines, overdue items, ambiguous dates, and P0/P1 edge cases.
- [ ] Freeze prompts before holdout evaluation.
- [ ] Report per-class metrics where sample size allows; do not rely on accuracy alone.
- [ ] Test duplicate processing and failure recovery.
- [ ] Test declined confirmations and ambiguous-deadline safety.
- [ ] Verify notifications only follow successful writes.
- [ ] Run all unit/integration tests and document limitations.

## Suggested target structure

Adapt this to the actual repository; do not move files unnecessarily.

```text
app/
  laya_engine/
    evaluate.py
    smoke_test.py
    classifier.py
  policy/
    decision_policy.py
  models/
    email.py
    task.py
  storage/
    task_store.py
  services/
    email_processor.py
    task_service.py
  integrations/
    gmail_client.py
    telegram_client.py
    calendar_client.py
  config.py
tests/
  fixtures/
  test_decision_policy.py
  test_task_store.py
  test_email_processor.py
  test_task_service.py
docs/
  planner.md
  architecture.md
README.md
```

## Definition of done

A feature is done only when:
1. Code exists in the repository.
2. Tests cover normal behavior and important edge cases.
3. Tests have actually run and their results are recorded.
4. Failure/retry behavior is understood.
5. Tests do not cause unintended external side effects.
6. Documentation and checklist are updated.
7. Credentials and user data are handled safely.

Use status labels: `DONE`, `IN PROGRESS`, `BLOCKED`, `NOT STARTED`, and `NEEDS VERIFICATION`.

## First session for Claude

1. Inspect both projects and summarize reusable features and differences.
2. Inspect current desktop files and Git status.
3. Run existing policy tests and current evaluation; record actual output.
4. Verify or implement SQLite task storage and its tests.
5. Fix the no-deadline vs ambiguous-deadline distinction.
6. Connect Laya to policy using mocked tests.
7. Update this planner with truthful statuses and the next task.

Do not jump straight to external integrations.

## Next task (as of 2026-10-14)

**Milestone 1 — deadline-state fix** is the immediate next step.

1. Add `DeadlineState` enum (`NONE`, `CLEAR`, `AMBIGUOUS`) to `app/policy/decision_policy.py`.
2. Replace `deadline_clear: bool` parameter in `decide()` with `deadline_state: DeadlineState`.
3. Implement the routing table above (P2 with NONE → ASK_CONFIRMATION; P0/P1 with NONE → SUMMARIZE_ONLY; AMBIGUOUS always → CLARIFY_DEADLINE).
4. Update all 9 existing tests in `tests/test_decision_policy.py` to pass `DeadlineState` values.
5. Add new tests: P0 no-deadline, P1 no-deadline, P2 no-deadline, P2 ambiguous, P0 ambiguous.
6. Then proceed to Milestone 2: implement `app/storage/task_store.py` and its tests.

---

## Prompt to give Claude

You are rebuilding and continuing my MailMate AI Desktop project. I am supplying the existing MailMate project as a reference and this planner as requirements/progress notes.

First inspect both codebases and report their structure, reusable features, and differences. Then inspect the actual desktop repository and Git status. Treat checklist statuses as historical notes to verify, not proof that code currently exists.

Preserve the policy: P0/P1 with a clear deadline may create a task and notify only after successful creation; P0/P1 without a deadline are summary-only under the current policy; P2 requires user confirmation; OTHER is summary-only; ambiguous deadlines must be clarified and never auto-scheduled; REVIEW must not cause automated side effects. Distinguish no deadline from an ambiguous deadline. Task creation and Calendar event creation are separate permissions. Prevent duplicate processing.

Laya is a typed decision engine, not the summary generator. Its checkpoint warns that some probabilities are uncalibrated. The 8-example demo produced both 8/8 and 6/8 in different runs, so do not claim stable 100% accuracy. Build an independent human-reviewed holdout set.

Use local-first, cost-optimized, preferably open-source choices. Do not introduce Raspberry Pi into this desktop architecture. Do not connect real Gmail, Telegram, or Calendar actions until policy, storage, and orchestration tests pass. Use mocks first. Keep evaluation free of real side effects.

Work in small milestones, preserve working code, avoid unnecessary rewrites, run tests, report actual results, and update `docs/planner.md` after each milestone. Start with repository inspection and task-store verification; do not skip ahead to external integrations.
