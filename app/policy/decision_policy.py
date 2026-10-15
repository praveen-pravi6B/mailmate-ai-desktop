"""Application-side decision policy for MailMate AI.

This module decides which action is permitted.
It does not create tasks, send notifications, or create calendar events.

Routing table:
  P0  + YES + any              -> CREATE_TASK_AND_NOTIFY  (always, no deadline needed)
  P1  + YES + CLEAR            -> ASK_CONFIRMATION
  P1  + YES + NONE / AMBIGUOUS -> SUMMARIZE_ONLY
  P2  + YES + CLEAR            -> ASK_CONFIRMATION
  P2  + YES + NONE / AMBIGUOUS -> SUMMARIZE_ONLY
  OTHER + any                  -> SUMMARIZE_ONLY
  ANY + NO                     -> SUMMARIZE_ONLY
  ANY + REVIEW                 -> REVIEW_CLASSIFICATION
"""

from dataclasses import dataclass
from enum import Enum


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    OTHER = "OTHER"


class Actionability(str, Enum):
    YES = "YES"
    NO = "NO"
    REVIEW = "REVIEW"


class DeadlineState(str, Enum):
    NONE = "NONE"            # No date was mentioned in the email
    CLEAR = "CLEAR"          # Explicit, unambiguous date/time
    AMBIGUOUS = "AMBIGUOUS"  # Date exists but is vague, relative, or conflicting


class PermittedAction(str, Enum):
    CREATE_TASK_AND_NOTIFY = "create_task_and_notify"
    ASK_CONFIRMATION = "ask_confirmation"
    SUMMARIZE_ONLY = "summarize_only"
    CLARIFY_DEADLINE = "clarify_deadline"
    REVIEW_CLASSIFICATION = "review_classification"


@dataclass(frozen=True)
class PolicyDecision:
    action: PermittedAction
    reason: str
    calendar_allowed: bool = False


def decide(
    priority: str,
    actionability: str,
    deadline_state: str,
) -> PolicyDecision:
    """Return a permitted action without executing it."""

    try:
        priority = Priority(priority.upper())
        actionability = Actionability(actionability.upper())
        deadline_state = DeadlineState(deadline_state.upper())
    except (ValueError, AttributeError):
        return PolicyDecision(
            action=PermittedAction.REVIEW_CLASSIFICATION,
            reason="Unknown or invalid classification.",
        )

    # Uncertain classification — always requires human review.
    if actionability == Actionability.REVIEW:
        return PolicyDecision(
            action=PermittedAction.REVIEW_CLASSIFICATION,
            reason="Actionability requires human review.",
        )

    # Non-actionable or OTHER — summarize only.
    if actionability == Actionability.NO or priority == Priority.OTHER:
        return PolicyDecision(
            action=PermittedAction.SUMMARIZE_ONLY,
            reason="No action required — summary only.",
        )

    # P0 — always create task immediately, no deadline required.
    if priority == Priority.P0:
        return PolicyDecision(
            action=PermittedAction.CREATE_TASK_AND_NOTIFY,
            reason="Critical priority — task created automatically.",
            calendar_allowed=False,
        )

    # P1 / P2 — ask confirmation only when deadline is clear.
    if priority in (Priority.P1, Priority.P2):
        if deadline_state == DeadlineState.CLEAR:
            return PolicyDecision(
                action=PermittedAction.ASK_CONFIRMATION,
                reason=f"{priority.value} task with a clear deadline — awaiting your confirmation.",
            )
        # No or ambiguous deadline — summarize only.
        return PolicyDecision(
            action=PermittedAction.SUMMARIZE_ONLY,
            reason=(
                f"{priority.value} task but deadline is {deadline_state.value.lower()} — "
                "summary only until a clear deadline is provided."
            ),
        )

    return PolicyDecision(
        action=PermittedAction.REVIEW_CLASSIFICATION,
        reason="No policy rule matched.",
    )
