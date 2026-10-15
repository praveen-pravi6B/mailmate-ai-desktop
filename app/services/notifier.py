"""Notification service for MailMate AI.

Builds Telegram message text and dispatches it via a NotificationTransport.
Never sends notifications directly — always delegates to the transport so
tests can use MockTransport without making real HTTP calls.

Safety rules
------------
- Notifications are only sent when the caller confirms that a task was
  successfully committed (i.e. result.notify is True).
- P2 confirmation requests include Confirm/Decline inline buttons whose
  callback_data embeds the task_id.
- Credentials are never referenced here — they live in the transport.
- No email body or sender address is included in notification text unless
  it is part of the task title/description already extracted.
"""

import logging
from typing import Optional

from app.integrations.telegram import InlineButton, NotificationTransport
from app.models.task import TaskCandidate
from app.services.orchestrator import (
    AWAITING_CONFIRMATION,
    NEEDS_REVIEW,
    NO_ACTION,
    OrchestrationResult,
    TASK_CREATED,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Callback data protocol
# ---------------------------------------------------------------------------

CONFIRM_PREFIX = "confirm:"
DECLINE_PREFIX = "decline:"


def confirmation_callback_data(task_id: str) -> tuple[str, str]:
    """Return (confirm_data, decline_data) strings for inline buttons."""
    return f"{CONFIRM_PREFIX}{task_id}", f"{DECLINE_PREFIX}{task_id}"


# ---------------------------------------------------------------------------
# Priority emoji helpers
# ---------------------------------------------------------------------------

_PRIORITY_EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡"}


def _priority_label(priority: str) -> str:
    emoji = _PRIORITY_EMOJI.get(priority, "⚪")
    return f"{emoji} {priority}"


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

def build_task_created_message(candidate: TaskCandidate) -> str:
    """Template for P0/P1 task-created notifications."""
    deadline_line = (
        f"📅 Deadline: {candidate.deadline}"
        if candidate.deadline
        else "📅 Deadline: not specified"
    )
    return (
        f"✅ *Task Created* — {_priority_label(candidate.priority)}\n"
        f"*{candidate.title}*\n"
        f"{deadline_line}\n\n"
        f"{candidate.description[:300]}"
    ).strip()


def build_confirmation_request_message(candidate: TaskCandidate) -> str:
    """Template for P2 confirmation-request messages."""
    return (
        f"📋 *Task Confirmation Required* — {_priority_label(candidate.priority)}\n"
        f"*{candidate.title}*\n\n"
        f"{candidate.description[:300]}\n\n"
        f"Do you want to create this task?"
    ).strip()


def build_confirmation_buttons(task_id: str) -> list[list[InlineButton]]:
    """Single row with Confirm and Decline buttons."""
    confirm_data, decline_data = confirmation_callback_data(task_id)
    return [[
        InlineButton(text="✅ Confirm", callback_data=confirm_data),
        InlineButton(text="❌ Decline", callback_data=decline_data),
    ]]


def build_summary_message(candidate: TaskCandidate) -> str:
    """Template for summary-only (non-actionable) emails."""
    return (
        f"ℹ️ *Summary* — {candidate.title}\n\n"
        f"{candidate.description[:300]}"
    ).strip()


def build_review_message(candidate: TaskCandidate) -> str:
    """Template when classification needs human review."""
    return (
        f"⚠️ *Needs Review* — {candidate.title}\n\n"
        f"Classification was uncertain. Please review manually."
    ).strip()


def build_clarify_deadline_message(candidate: TaskCandidate) -> str:
    """Template when the deadline must be clarified before a task is created."""
    return (
        f"🗓️ *Deadline Clarification Needed* — {candidate.title}\n\n"
        f"{candidate.evidence}\n\n"
        f"Please clarify the deadline before a task can be created."
    ).strip()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def notify(
    result: OrchestrationResult,
    transport: NotificationTransport,
) -> bool:
    """Send the appropriate notification for an OrchestrationResult.

    Parameters
    ----------
    result:
        The result of ``process_email()``.
    transport:
        A NotificationTransport (MockTransport in tests, TelegramTransport
        in production).

    Returns
    -------
    bool
        True if the message was sent successfully, False otherwise.

    Notes
    -----
    - If ``result.notify`` is False, no message is sent and False is
      returned.  Callers must not pre-filter — this function is the gate.
    - P2 confirmation requests include inline buttons with the task_id.
    - Notifications are never sent for non-committed results.
    """
    if not result.notify:
        log.debug("notifier: notify=False for email_id=%s, skipping",
                  result.email_id)
        return False

    candidate = result.candidate
    if candidate is None:
        log.warning("notifier: notify=True but no candidate for email_id=%s",
                    result.email_id)
        return False

    if result.status == TASK_CREATED:
        text = build_task_created_message(candidate)
        ok = transport.send_message(text)
        log.info("notifier: task_created notification sent=%s email_id=%s",
                 ok, result.email_id)
        return ok

    if result.status == AWAITING_CONFIRMATION:
        if not result.task_id:
            log.warning("notifier: AWAITING_CONFIRMATION but no task_id "
                        "email_id=%s", result.email_id)
            return False
        text = build_confirmation_request_message(candidate)
        buttons = build_confirmation_buttons(result.task_id)
        ok = transport.send_message(text, buttons=buttons)
        log.info("notifier: confirmation_request sent=%s email_id=%s task_id=%s",
                 ok, result.email_id, result.task_id)
        return ok

    # result.notify should not be True for other statuses, but guard anyway.
    log.warning("notifier: unexpected notify=True for status=%s email_id=%s",
                result.status, result.email_id)
    return False
