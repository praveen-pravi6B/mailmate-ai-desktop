"""Typed Laya classification wrapper for MailMate AI.

Calls the Laya typed-decision engine and returns a validated
ClassificationResult.  All unknown or invalid labels are normalised to
REVIEW so that the downstream policy module always receives a known value.

This module only classifies.  It never creates tasks, writes to the
database, sends notifications, or creates calendar events.
"""

from dataclasses import dataclass, field

from app.policy.decision_policy import (
    Actionability,
    DeadlineState,
    Priority,
)

# ---------------------------------------------------------------------------
# Questions passed to the Laya agent (reused from evaluate.py prompts)
# ---------------------------------------------------------------------------

ACTION_QUESTIONS = {
    "action_required": {
        "type": "choice",
        "instructions": (
            "Decide if the recipient must do something. "
            "Answer YES only if the email asks the recipient to "
            "complete a task, reply, make a decision, fix something, "
            "or pay. Answer NO if the email only shares information, "
            "news, updates, advertisements, or meeting changes. "
            "A date, urgent wording, or meeting time alone does not "
            "mean a task is required. Do not invent tasks."
        ),
        "criteria": {
            "YES": (
                "The email asks the recipient to complete a task, "
                "reply, make a decision, fix something, or pay."
            ),
            "NO": (
                "The email only shares information, news, updates, "
                "advertisements, or meeting changes without requesting "
                "an action."
            ),
        },
    }
}

PRIORITY_QUESTIONS = {
    "priority": {
        "type": "choice",
        "instructions": (
            "Classify the priority of an email that requires action. "
            "P0 is ONLY a critical incident or immediate serious risk "
            "requiring intervention now, such as a confirmed production "
            "outage affecting customers or an active security breach. "
            "P1 is important work with a meaningful deadline or business "
            "impact, without an established P0-level crisis. P2 is routine "
            "work that can reasonably wait. An overdue payment, failed "
            "backup, bug report, expiring access, or deadline is not "
            "automatically P0. Do not infer urgency or criticality without "
            "supporting context. Choose the best-supported category."
        ),
        "criteria": {
            "P0": (
                "Critical incident or immediate serious risk requiring "
                "intervention now."
            ),
            "P1": (
                "Important actionable work with a meaningful deadline "
                "or business impact, but no established critical incident."
            ),
            "P2": "Routine actionable work that can reasonably wait.",
        },
    }
}

_VALID_ACTIONABILITY = {Actionability.YES.value, Actionability.NO.value}
_VALID_PRIORITIES = {Priority.P0.value, Priority.P1.value, Priority.P2.value}

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationResult:
    """Typed output from the Laya classifier.

    actionability   — YES / NO / REVIEW
    priority        — P0 / P1 / P2 / OTHER / REVIEW
    deadline_state  — NONE / CLEAR / AMBIGUOUS  (supplied by caller; not
                      derived from Laya — deadline extraction is a
                      separate step handled in Milestone 4)
    normalized      — True when any label was corrected to REVIEW because
                      the model returned an unrecognised value
    """

    actionability: str
    priority: str
    deadline_state: str
    action_probabilities: dict = field(default_factory=dict)
    priority_probabilities: dict = field(default_factory=dict)
    normalized: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_choice(result: dict, answer_key: str) -> tuple[str, dict]:
    """Pull the choice string and probability dict from a Laya answer block."""
    try:
        answer = result["answers"][answer_key]
        choice = str(answer["choice"]).strip().upper()
        raw_probs = answer.get("probabilities", {})
    except (KeyError, TypeError, AttributeError):
        return "REVIEW", {}

    probs: dict = {}
    if isinstance(raw_probs, dict):
        for label, prob in raw_probs.items():
            try:
                probs[str(label).upper()] = float(prob)
            except (TypeError, ValueError):
                continue

    return choice, probs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_email(
    agent,
    email: dict,
    deadline_state: str = DeadlineState.AMBIGUOUS,
) -> ClassificationResult:
    """Classify a single email and return a validated ClassificationResult.

    Parameters
    ----------
    agent:
        A loaded Laya agent exposing ``system_one(state, questions)``.
    email:
        Dict with at least ``subject`` and ``body`` keys.
    deadline_state:
        Deadline state determined by the email normaliser (Milestone 4).
        Defaults to AMBIGUOUS — the safest fallback — so that callers
        which have not yet extracted deadline information never silently
        auto-schedule anything.

    Notes
    -----
    - The checkpoint may produce uncalibrated probabilities; treat them
      as model outputs, not statistical confidence.
    - Unknown labels from the model are normalised to REVIEW so the
      policy module always receives a value it can handle.
    - This function never writes to storage or triggers notifications.
    """

    # Validate deadline_state; fall back to AMBIGUOUS if unrecognised.
    try:
        deadline_state = DeadlineState(str(deadline_state).upper()).value
    except ValueError:
        deadline_state = DeadlineState.AMBIGUOUS.value

    # Accept either a plain dict or a NormalizedEmail dataclass.
    if isinstance(email, dict):
        state = {"subject": email.get("subject", ""), "body": email.get("body", "")}
    else:
        state = {"subject": getattr(email, "subject", ""), "body": getattr(email, "body", "")}

    normalized = False

    # Stage 1 — actionability
    action_result = agent.system_one(state, ACTION_QUESTIONS)
    actionability, action_probs = _extract_choice(action_result, "action_required")

    if actionability not in _VALID_ACTIONABILITY:
        actionability = Actionability.REVIEW.value
        normalized = True

    # Stage 2 — priority (only when actionable)
    priority = Priority.OTHER.value
    priority_probs: dict = {}

    if actionability == Actionability.YES.value:
        priority_result = agent.system_one(state, PRIORITY_QUESTIONS)
        priority, priority_probs = _extract_choice(priority_result, "priority")

        if priority not in _VALID_PRIORITIES:
            priority = "REVIEW"
            normalized = True

    elif actionability == Actionability.REVIEW.value:
        # Propagate REVIEW to priority too.
        priority = "REVIEW"

    return ClassificationResult(
        actionability=actionability,
        priority=priority,
        deadline_state=deadline_state,
        action_probabilities=action_probs,
        priority_probabilities=priority_probs,
        normalized=normalized,
    )
