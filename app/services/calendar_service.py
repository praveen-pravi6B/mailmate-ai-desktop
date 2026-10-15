"""Calendar service for MailMate AI.

Handles calendar eligibility checks, event creation, duplicate
prevention, and event ID persistence.

This module is the only place that decides whether a task may receive a
calendar event.  It enforces all calendar safety rules so that callers
cannot bypass them.

Safety rules enforced here
--------------------------
- Calendar event creation is separate from task creation.  A task
  existing in the store does not automatically qualify.
- Events are never created for AMBIGUOUS or NONE deadline states.
- Time and timezone must be supplied by the caller.  They are never
  invented, defaulted, or inferred from a date-only deadline.
- Duplicate events are prevented: if a task already has a calendar_event_id
  the service refuses to create another one.
- Declined or cancelled tasks are never eligible.
- Real API writes require an explicit live_writes=True transport.  The
  service itself is transport-agnostic.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.integrations.calendar import CalendarEventRequest, CalendarTransport
from app.storage.task_store import Task, TaskStatus, TaskStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status codes
# ---------------------------------------------------------------------------

CREATED = "CREATED"
DUPLICATE = "DUPLICATE"         # calendar_event_id already set on task
INELIGIBLE = "INELIGIBLE"       # task status or deadline makes it ineligible
MISSING_TIME = "MISSING_TIME"   # caller must supply event time
MISSING_TIMEZONE = "MISSING_TIMEZONE"  # caller must supply timezone
ERROR = "ERROR"                 # unexpected failure


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalendarResult:
    """Outcome of a calendar event creation attempt.

    Fields
    ------
    status:
        One of the status constants above.
    event_id:
        Provider event ID, set only when status is CREATED.
    task_id:
        The task UUID that was processed.
    reason:
        Human-readable explanation safe for logs (no email content).
    """

    status: str
    task_id: str
    reason: str
    event_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Eligibility check
# ---------------------------------------------------------------------------

_INELIGIBLE_STATUSES = {TaskStatus.DECLINED, TaskStatus.CANCELLED}


def check_eligibility(task: Task, time: str, timezone: str) -> Optional[CalendarResult]:
    """Return a CalendarResult if the task is NOT eligible, else None.

    Call this before ``schedule_event``.  A None return means all
    eligibility conditions are met and it is safe to proceed.
    """

    # Duplicate prevention — never create a second event.
    if task.calendar_event_id:
        return CalendarResult(
            status=DUPLICATE,
            task_id=task.id,
            reason=f"Task already has calendar_event_id={task.calendar_event_id!r}.",
        )

    # Declined or cancelled tasks must never be scheduled.
    if task.status in _INELIGIBLE_STATUSES:
        return CalendarResult(
            status=INELIGIBLE,
            task_id=task.id,
            reason=f"Task status is {task.status.value}; not eligible for scheduling.",
        )

    # Deadline must be explicit and unambiguous.
    if task.deadline_state != "CLEAR":
        return CalendarResult(
            status=INELIGIBLE,
            task_id=task.id,
            reason=(
                f"deadline_state is {task.deadline_state!r}. "
                "Only CLEAR deadlines may be scheduled."
            ),
        )

    if not task.deadline:
        return CalendarResult(
            status=INELIGIBLE,
            task_id=task.id,
            reason="Task has no deadline value; cannot schedule.",
        )

    # Time and timezone are required — never invent them.
    if not time or not time.strip():
        return CalendarResult(
            status=MISSING_TIME,
            task_id=task.id,
            reason=(
                "Event time is required but was not supplied. "
                "Please provide a time (HH:MM) — it is never inferred from "
                "a date-only deadline."
            ),
        )

    if not timezone or not timezone.strip():
        return CalendarResult(
            status=MISSING_TIMEZONE,
            task_id=task.id,
            reason=(
                "Timezone is required but was not supplied. "
                "Please provide an IANA timezone (e.g. 'Asia/Kolkata')."
            ),
        )

    return None  # all checks passed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def schedule_event(
    task: Task,
    store: TaskStore,
    transport: CalendarTransport,
    time: str,
    timezone: str,
) -> CalendarResult:
    """Create a calendar event for a task and persist the event ID.

    Parameters
    ----------
    task:
        The Task record from the store.
    store:
        An open TaskStore — used to persist the event ID after creation.
    transport:
        A CalendarTransport (MockCalendarTransport in tests,
        GoogleCalendarTransport with live_writes=True in production).
    time:
        Event start time in HH:MM (24-hour).  Must be supplied by the
        caller — never inferred.
    timezone:
        IANA timezone string.  Must be supplied by the caller.

    Returns
    -------
    CalendarResult
        Always returned — never raises.  Check ``status`` before acting.

    Notes
    -----
    - The event ID is persisted via ``store.set_calendar_event_id`` only
      after the transport confirms creation.
    - If persistence fails after a successful API call, status is ERROR
      and the raw event_id is included in the reason so it can be
      recovered manually.
    - This function never sends Telegram messages or modifies task status.
    """

    ineligible = check_eligibility(task, time, timezone)
    if ineligible is not None:
        log.info(
            "calendar_service: ineligible task_id=%s status=%s reason=%s",
            task.id, ineligible.status, ineligible.reason,
        )
        return ineligible

    request = CalendarEventRequest(
        task_id=task.id,
        title=task.title,
        description=task.description,
        date=task.deadline,  # type: ignore[arg-type]  # checked above
        time=time.strip(),
        timezone=timezone.strip(),
    )

    # Create the event via the transport.
    try:
        event_id = transport.create_event(request)
    except Exception as exc:
        log.error(
            "calendar_service: transport error task_id=%s err=%s",
            task.id, type(exc).__name__,
        )
        return CalendarResult(
            status=ERROR,
            task_id=task.id,
            reason=f"Transport error: {type(exc).__name__}",
        )

    # Persist the event ID — only after a confirmed API success.
    try:
        store.set_calendar_event_id(task.id, event_id)
    except Exception as exc:
        log.error(
            "calendar_service: failed to persist event_id=%s task_id=%s err=%s",
            event_id, task.id, type(exc).__name__,
        )
        return CalendarResult(
            status=ERROR,
            task_id=task.id,
            reason=(
                f"Event created (event_id={event_id!r}) but persistence "
                f"failed: {type(exc).__name__}. Record this event_id manually."
            ),
        )

    log.info(
        "calendar_service: event created and persisted task_id=%s event_id=%s",
        task.id, event_id,
    )
    return CalendarResult(
        status=CREATED,
        task_id=task.id,
        event_id=event_id,
        reason=f"Calendar event created: {event_id}",
    )
