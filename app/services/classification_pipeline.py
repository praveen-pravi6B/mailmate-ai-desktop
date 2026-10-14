"""Classification pipeline — connects Laya output to the policy module.

This is the firewall between model predictions and permitted actions.
A ClassificationResult from the classifier is passed in; a PolicyDecision
is returned.  No storage writes, notifications, or calendar events are
triggered here.

The only way a task or notification can be authorised is via the
PolicyDecision returned by this module.  Callers must check
``decision.action`` before taking any side-effecting step.
"""

from app.laya_engine.classifier import ClassificationResult
from app.policy.decision_policy import PolicyDecision, decide


def apply_policy(result: ClassificationResult) -> PolicyDecision:
    """Map a ClassificationResult to a PolicyDecision.

    Parameters
    ----------
    result:
        Output from ``classify_email()``.

    Returns
    -------
    PolicyDecision:
        The permitted action.  Callers must not act until they have
        inspected ``decision.action``; model predictions alone do not
        authorise any side effect.
    """
    return decide(
        priority=result.priority,
        actionability=result.actionability,
        deadline_state=result.deadline_state,
    )
