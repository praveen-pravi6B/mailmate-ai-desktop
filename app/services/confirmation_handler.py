"""P2 confirmation callback handler for MailMate AI.

Parses Telegram inline-keyboard callback data and updates the task store
accordingly.

Callback data format
--------------------
``"confirm:<task_id>"``  — user pressed Confirm
``"decline:<task_id>"``  — user pressed Decline

Safety rules
------------
- Only tasks in PENDING confirmation state may be confirmed or declined.
  Duplicate callbacks (already CONFIRMED or DECLINED) are handled
  gracefully without modifying the task again.
- Declined tasks remain in the store with status DECLINED and
  confirmation_status DECLINED.  They are never auto-escalated.
- A timed-out confirmation request is treated as implicitly declined
  (no task action, no notification).
- This module never sends Telegram messages directly — it returns a
  result and the caller decides whether to answer the callback query.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.services.notifier import CONFIRM_PREFIX, DECLINE_PREFIX
from app.storage.task_store import (
    ConfirmationStatus,
    Task,
    TaskNotFoundError,
    TaskStatus,
    TaskStore,
)

log = logging.getLogger(__name__)

# Default window in which a P2 confirmation must be received.
DEFAULT_TIMEOUT_HOURS = 48


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfirmationResult:
    """Outcome of handling a confirmation callback.

    Fields
    ------
    accepted:
        True when the task was successfully confirmed.
    declined:
        True when the task was explicitly declined.
    already_resolved:
        True when the callback arrived but the task was already
        CONFIRMED or DECLINED (duplicate callback).
    expired:
        True when the confirmation window has passed.
    error:
        True when an unexpected failure occurred.
    task_id:
        The task UUID from the callback, if parseable.
    reply_text:
        Short acknowledgement text to send back to Telegram via
        ``answer_callback_query``.
    """

    accepted: bool = False
    declined: bool = False
    already_resolved: bool = False
    expired: bool = False
    error: bool = False
    task_id: Optional[str] = None
    reply_text: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_callback(callback_data: str) -> tuple[Optional[str], Optional[str]]:
    """Return (action, task_id) from callback_data, or (None, None) on failure.

    action is ``"confirm"`` or ``"decline"``.
    """
    if callback_data.startswith(CONFIRM_PREFIX):
        return "confirm", callback_data[len(CONFIRM_PREFIX):]
    if callback_data.startswith(DECLINE_PREFIX):
        return "decline", callback_data[len(DECLINE_PREFIX):]
    return None, None


def _is_expired(task: Task, timeout_hours: int) -> bool:
    """Return True if the task was created more than timeout_hours ago."""
    try:
        created = datetime.fromisoformat(task.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created
        return age > timedelta(hours=timeout_hours)
    except (ValueError, TypeError):
        return False  # if we can't parse the timestamp, don't block the user


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def handle_callback(
    callback_data: str,
    store: TaskStore,
    timeout_hours: int = DEFAULT_TIMEOUT_HOURS,
) -> ConfirmationResult:
    """Process a Telegram inline-keyboard callback.

    Parameters
    ----------
    callback_data:
        The ``callback_data`` string from the Telegram callback query.
    store:
        An open TaskStore.
    timeout_hours:
        Confirmation window.  Callbacks arriving after this period are
        treated as expired.

    Returns
    -------
    ConfirmationResult
        Always returned — never raises.  Check the boolean flags and
        send ``reply_text`` back to Telegram via ``answer_callback_query``.
    """
    action, task_id = _parse_callback(callback_data)

    if action is None or not task_id:
        log.warning("confirmation_handler: unrecognised callback_data=%r",
                    callback_data)
        return ConfirmationResult(
            error=True,
            reply_text="Unknown request.",
        )

    # Fetch the task.
    try:
        task = store.get_by_id(task_id)
    except TaskNotFoundError:
        log.warning("confirmation_handler: task not found task_id=%s", task_id)
        return ConfirmationResult(
            error=True,
            task_id=task_id,
            reply_text="Task not found.",
        )
    except Exception as exc:
        log.error("confirmation_handler: store error task_id=%s err=%s",
                  task_id, type(exc).__name__)
        return ConfirmationResult(error=True, task_id=task_id,
                                  reply_text="An error occurred.")

    # Guard: only PENDING tasks may be updated.
    if task.confirmation_status == ConfirmationStatus.CONFIRMED:
        log.info("confirmation_handler: duplicate confirm task_id=%s", task_id)
        return ConfirmationResult(
            already_resolved=True,
            task_id=task_id,
            reply_text="Already confirmed.",
        )

    if task.confirmation_status == ConfirmationStatus.DECLINED:
        log.info("confirmation_handler: duplicate decline task_id=%s", task_id)
        return ConfirmationResult(
            already_resolved=True,
            task_id=task_id,
            reply_text="Already declined.",
        )

    # Guard: timeout check.
    if _is_expired(task, timeout_hours):
        log.info("confirmation_handler: confirmation expired task_id=%s", task_id)
        return ConfirmationResult(
            expired=True,
            task_id=task_id,
            reply_text="Confirmation window has expired.",
        )

    # Apply the action.
    try:
        if action == "confirm":
            store.update_confirmation(task_id, ConfirmationStatus.CONFIRMED)
            store.update_status(task_id, TaskStatus.CONFIRMED)
            log.info("confirmation_handler: confirmed task_id=%s", task_id)
            return ConfirmationResult(
                accepted=True,
                task_id=task_id,
                reply_text="Task confirmed ✅",
            )

        # decline
        store.update_confirmation(task_id, ConfirmationStatus.DECLINED)
        store.update_status(task_id, TaskStatus.DECLINED)
        log.info("confirmation_handler: declined task_id=%s", task_id)
        return ConfirmationResult(
            declined=True,
            task_id=task_id,
            reply_text="Task declined ❌",
        )

    except Exception as exc:
        log.error("confirmation_handler: update failed task_id=%s err=%s",
                  task_id, type(exc).__name__)
        return ConfirmationResult(
            error=True,
            task_id=task_id,
            reply_text="An error occurred while updating the task.",
        )
