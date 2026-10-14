"""Application-side decision policy for MailMate AI.

This module decides which action is permitted.
It does not create tasks, send notifications, or create calendar events.
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
    """Return a permitted action without executing it.

    Routing table:
      P0/P1 + YES + CLEAR     -> CREATE_TASK_AND_NOTIFY
      P0/P1 + YES + NONE      -> SUMMARIZE_ONLY   (no deadline supplied; no task auto-created)
      P0/P1 + YES + AMBIGUOUS -> CLARIFY_DEADLINE
      P2    + YES + CLEAR     -> ASK_CONFIRMATION
      P2    + YES + NONE      -> ASK_CONFIRMATION  (deadline not required for P2)
      P2    + YES + AMBIGUOUS -> CLARIFY_DEADLINE
      ANY   + NO              -> SUMMARIZE_ONLY
      ANY   + REVIEW          -> REVIEW_CLASSIFICATION
    """

    try:
        priority = Priority(priority.upper())
        actionability = Actionability(actionability.upper())
        deadline_state = DeadlineState(deadline_state.upper())
    except (ValueError, AttributeError):
        return PolicyDecision(
            action=PermittedAction.REVIEW_CLASSIFICATION,
            reason="Unknown or invalid classification.",
        )

    # Do not take action on uncertain classifications.
    if actionability == Actionability.REVIEW:
        return PolicyDecision(
            action=PermittedAction.REVIEW_CLASSIFICATION,
            reason="Actionability requires human review.",
        )

    if actionability == Actionability.NO:
        return PolicyDecision(
            action=PermittedAction.SUMMARIZE_ONLY,
            reason="No action is required by the email.",
        )

    # An actionable email must have a supported priority.
    if priority == Priority.OTHER:
        return PolicyDecision(
            action=PermittedAction.REVIEW_CLASSIFICATION,
            reason="Actionable email has no valid task priority.",
        )

    if priority in (Priority.P0, Priority.P1):
        if deadline_state == DeadlineState.CLEAR:
            return PolicyDecision(
                action=PermittedAction.CREATE_TASK_AND_NOTIFY,
                reason="High-priority task with a clear deadline.",
                # Task creation does not automatically authorize a
                # calendar event. Calendar mapping is a separate step.
                calendar_allowed=False,
            )
        if deadline_state == DeadlineState.AMBIGUOUS:
            return PolicyDecision(
                action=PermittedAction.CLARIFY_DEADLINE,
                reason="Clarify the ambiguous deadline before scheduling.",
            )
        # NONE: high-priority but no deadline supplied — summarize only.
        # Do not auto-create tasks without a deadline.
        return PolicyDecision(
            action=PermittedAction.SUMMARIZE_ONLY,
            reason="High-priority email has no deadline; summary only until deadline is known.",
        )

    if priority == Priority.P2:
        if deadline_state == DeadlineState.AMBIGUOUS:
            return PolicyDecision(
                action=PermittedAction.CLARIFY_DEADLINE,
                reason="Clarify the ambiguous deadline before creating a task.",
            )
        # CLEAR or NONE: P2 tasks do not require a deadline for confirmation.
        return PolicyDecision(
            action=PermittedAction.ASK_CONFIRMATION,
            reason="Routine task requires user confirmation.",
        )

    return PolicyDecision(
        action=PermittedAction.REVIEW_CLASSIFICATION,
        reason="No policy rule matched.",
    )
