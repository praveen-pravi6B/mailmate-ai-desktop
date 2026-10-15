"""Notification service for MailMate AI.

Builds Telegram message text and dispatches it via a NotificationTransport.

Message flow (per poll):
  1. send_poll_summary()  — one batch message grouping all emails by outcome
  2. notify()             — individual message per task created / P2 confirmation

Safety rules
------------
- notify() only sends when result.notify is True (task successfully committed).
- P2 confirmation requests include ✅ Yes / ❌ No inline buttons.
- Credentials never referenced here — they live in the transport.
"""

import logging
from typing import Optional

from app.integrations.telegram import InlineButton, NotificationTransport
from app.models.task import TaskCandidate
from app.services.orchestrator import (
    AWAITING_CONFIRMATION,
    NO_ACTION,
    OrchestrationResult,
    TASK_CREATED,
    NEEDS_REVIEW,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Callback data protocol
# ---------------------------------------------------------------------------

CONFIRM_PREFIX = "confirm:"
DECLINE_PREFIX = "decline:"


def confirmation_callback_data(task_id: str) -> tuple[str, str]:
    return f"{CONFIRM_PREFIX}{task_id}", f"{DECLINE_PREFIX}{task_id}"


# ---------------------------------------------------------------------------
# Priority helpers
# ---------------------------------------------------------------------------

_PRIORITY_EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "OTHER": "⚪", "REVIEW": "⚠️"}

def _pri(priority: str) -> str:
    return f"{_PRIORITY_EMOJI.get(priority, '⚪')} {priority}"


# ---------------------------------------------------------------------------
# Batch poll summary  (PI-repo style — one message per poll run)
# ---------------------------------------------------------------------------

def send_poll_summary(
    results: list[OrchestrationResult],
    transport: NotificationTransport,
) -> bool:
    """Send one grouped summary message covering all emails in the poll.

    Groups:
      🔴 Important  — P0/P1 task created
      ✅ Action Required — P2 awaiting confirmation
      📦 Updates    — summarize-only / no action
      ⚠️ Review     — needs human review
    """
    if not results:
        return False

    important, updates, reviews = [], [], []

    for r in results:
        subj = (r.candidate.title[:55] + "…"
                if r.candidate and len(r.candidate.title) > 55
                else (r.candidate.title if r.candidate else r.email_id))
        pri  = _pri(r.candidate.priority) if r.candidate else ""
        dl   = r.candidate.deadline if r.candidate and r.candidate.deadline else ""

        line = f"• *{subj}*"
        if pri:
            line += f"\n  {pri}"
        if dl:
            line += f"  |  📅 {dl}"

        if r.status == TASK_CREATED:
            important.append(line + "\n  _Task created ✅_")
        elif r.status == AWAITING_CONFIRMATION:
            important.append(line + "\n  _Awaiting your confirmation ❓_")
        elif r.status == NEEDS_REVIEW:
            reviews.append(line)
        else:
            # All NO_ACTION emails go to Updates regardless of priority
            updates.append(line)

    msg = "📬 *MailMate AI — Email Summary*\n\n"

    if important:
        msg += "🔴 *Important / Tasks*\n"
        msg += "\n".join(important) + "\n\n"

    if updates:
        msg += "📦 *Updates*\n"
        msg += "\n".join(updates) + "\n\n"

    if reviews:
        msg += "⚠️ *Needs Review*\n"
        msg += "\n".join(reviews) + "\n\n"

    total = len(results)
    created  = sum(1 for r in results if r.status == TASK_CREATED)
    awaiting = sum(1 for r in results if r.status == AWAITING_CONFIRMATION)

    footer_parts = [f"Processed {total} email(s)"]
    if created:
        footer_parts.append(f"{created} task(s) created")
    if awaiting:
        footer_parts.append(f"{awaiting} awaiting confirmation")
    if not created and not awaiting:
        footer_parts.append("no tasks")

    msg += f"_{' — '.join(footer_parts)}_"

    ok = transport.send_message(msg)
    log.info("notifier: poll_summary sent=%s total=%d", ok, total)
    return ok


# ---------------------------------------------------------------------------
# Individual task messages
# ---------------------------------------------------------------------------

def build_task_created_message(candidate: TaskCandidate) -> str:
    """Detailed message for P0/P1 task created."""
    lines = [
        f"✅ *Task Created* — {_pri(candidate.priority)}",
        f"",
        f"📌 *{candidate.title}*",
        f"",
    ]
    if candidate.description:
        # Full description, split into readable lines, max 800 chars
        desc = candidate.description[:800]
        lines.append(desc)
        lines.append("")
    if candidate.deadline:
        lines.append(f"📅 *Deadline:* {candidate.deadline}")
    else:
        lines.append(f"📅 *Deadline:* Not specified ({candidate.deadline_state})")
    if candidate.evidence and candidate.evidence != "No date or time reference found.":
        lines.append(f"🔍 *Evidence:* _{candidate.evidence}_")
    return "\n".join(lines).strip()


def build_confirmation_request_message(candidate: TaskCandidate) -> str:
    """P2 confirmation message with full context."""
    lines = [
        f"❓ *Task Confirmation Required* — {_pri(candidate.priority)}",
        f"",
        f"📌 *{candidate.title}*",
        f"",
    ]
    if candidate.description:
        lines.append(candidate.description[:800])
        lines.append("")
    lines.append("*Do you want to create this task?*")
    return "\n".join(lines).strip()


def build_confirmation_buttons(task_id: str) -> list[list[InlineButton]]:
    confirm_data, decline_data = confirmation_callback_data(task_id)
    return [[
        InlineButton(text="✅ Yes, create task", callback_data=confirm_data),
        InlineButton(text="❌ No, skip",         callback_data=decline_data),
    ]]


# ---------------------------------------------------------------------------
# Dispatch — individual per-task notification
# ---------------------------------------------------------------------------

def notify(
    result: OrchestrationResult,
    transport: NotificationTransport,
) -> bool:
    """Send an individual task notification for a committed result.

    Only fires when result.notify is True (task successfully written to DB).
    """
    if not result.notify:
        return False

    candidate = result.candidate
    if candidate is None:
        log.warning("notifier: notify=True but no candidate email_id=%s",
                    result.email_id)
        return False

    if result.status == TASK_CREATED:
        text = build_task_created_message(candidate)
        ok = transport.send_message(text)
        log.info("notifier: task_created sent=%s email_id=%s", ok, result.email_id)
        return ok

    if result.status == AWAITING_CONFIRMATION:
        if not result.task_id:
            log.warning("notifier: AWAITING_CONFIRMATION no task_id email_id=%s",
                        result.email_id)
            return False
        text = build_confirmation_request_message(candidate)
        buttons = build_confirmation_buttons(result.task_id)
        ok = transport.send_message(text, buttons=buttons)
        log.info("notifier: confirmation_request sent=%s email_id=%s task_id=%s",
                 ok, result.email_id, result.task_id)
        return ok

    log.warning("notifier: unexpected notify=True status=%s email_id=%s",
                result.status, result.email_id)
    return False
