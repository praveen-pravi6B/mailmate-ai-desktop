"""Orchestration pipeline for MailMate AI.

Coordinates email normalization, Laya classification, policy gating,
and SQLite task persistence in a single, idempotent processing step.

Safety rules enforced here
--------------------------
- Repeated processing of the same email is always safe (idempotent).
- A task is persisted ONLY after the policy permits it.
- The ``notify`` flag on the result is True ONLY after a successful DB
  commit — callers must check it before sending any notification.
- dry_run=True (the default) runs the full pipeline but skips all DB
  writes and sets notify=False.  Enable live mode explicitly.
- No email body, sender, or subject is written to log messages.
- This module never sends Telegram messages or creates Calendar events.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.laya_engine.classifier import ClassificationResult, classify_email
from app.models.email import NormalizedEmail
from app.models.task import TaskCandidate
from app.policy.decision_policy import PolicyDecision, PermittedAction
from app.services.classification_pipeline import apply_policy
from app.services.summarizer import summarize_email
from app.services.task_extractor import detect_deadline_state, extract_task
from app.storage.task_store import (
    ConfirmationStatus,
    DuplicateEmailError,
    TaskStore,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

# Human-readable status codes returned by process_email().
SKIPPED = "SKIPPED"                    # already processed; idempotent skip
DRY_RUN = "DRY_RUN"                    # pipeline ran but no DB writes made
TASK_CREATED = "TASK_CREATED"          # task persisted; notify=True
AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # P2 task pending user reply
NO_ACTION = "NO_ACTION"                # policy says summarize-only or clarify
NEEDS_REVIEW = "NEEDS_REVIEW"          # classification uncertain; human needed
ERROR = "ERROR"                        # unexpected failure; no side effect


@dataclass(frozen=True)
class OrchestrationResult:
    """Result of a single email processing attempt.

    Fields
    ------
    status:
        One of the status constants above.
    email_id:
        Source email message_id.
    decision:
        PolicyDecision, or None when processing was skipped or failed.
    candidate:
        Extracted TaskCandidate, or None when not applicable.
    task_id:
        UUID of the persisted task, or None when no task was written.
    notify:
        True only when a task was successfully committed and the caller
        should send a notification.  Always False in dry-run mode.
    reason:
        Human-readable explanation (safe for logs — no email content).
    """

    status: str
    email_id: str
    decision: Optional[PolicyDecision] = None
    candidate: Optional[TaskCandidate] = None
    task_id: Optional[str] = None
    notify: bool = False
    reason: str = ""
    summary: str = ""  # AI-generated email summary (1-2 sentences)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_email(
    email: NormalizedEmail,
    agent,
    store: TaskStore,
    dry_run: bool = True,
) -> OrchestrationResult:
    """Process a single normalised email end-to-end.

    Parameters
    ----------
    email:
        A ``NormalizedEmail`` produced by the email normaliser.
    agent:
        A loaded Laya agent (``laya.load(...)``).
    store:
        An open ``TaskStore`` instance.
    dry_run:
        When True (the default), the full pipeline runs but no database
        writes are made and ``notify`` is always False.  Set to False
        only after policy, storage, and orchestration tests pass.

    Returns
    -------
    OrchestrationResult
        Always returned — never raises.  Check ``status`` and ``notify``
        before taking any follow-up action.
    """

    eid = email.message_id

    # ------------------------------------------------------------------
    # 1. Idempotency check — skip if already processed
    # ------------------------------------------------------------------
    try:
        already = store.email_has_task(eid)
    except Exception as exc:
        log.error("orchestrator: store check failed for email_id=%s err=%s",
                  eid, type(exc).__name__)
        return OrchestrationResult(
            status=ERROR, email_id=eid,
            reason=f"Store check failed: {type(exc).__name__}",
        )

    if already:
        log.info("orchestrator: skipping already-processed email_id=%s", eid)
        return OrchestrationResult(
            status=SKIPPED, email_id=eid,
            reason="Email already has a task; skipped.",
        )

    # ------------------------------------------------------------------
    # 2. Generate summary (LLM — no policy side effects)
    # ------------------------------------------------------------------
    summary = summarize_email(email.subject, email.body)
    log.info("orchestrator: summary generated email_id=%s chars=%d", eid, len(summary))

    # ------------------------------------------------------------------
    # 3. Detect deadline state from body (structural, no model call)
    # ------------------------------------------------------------------
    deadline_state = detect_deadline_state(email.body)

    # ------------------------------------------------------------------
    # 4. Classify with Laya
    # ------------------------------------------------------------------
    try:
        classification: ClassificationResult = classify_email(
            agent, email, deadline_state=deadline_state
        )
    except Exception as exc:
        log.error("orchestrator: classification failed email_id=%s err=%s",
                  eid, type(exc).__name__)
        return OrchestrationResult(
            status=ERROR, email_id=eid,
            reason=f"Classification failed: {type(exc).__name__}",
        )

    log.info(
        "orchestrator: classified email_id=%s actionability=%s priority=%s "
        "deadline_state=%s normalized=%s",
        eid,
        classification.actionability,
        classification.priority,
        classification.deadline_state,
        classification.normalized,
    )

    # ------------------------------------------------------------------
    # 5. Apply policy (firewall — no side effects)
    # ------------------------------------------------------------------
    decision: PolicyDecision = apply_policy(classification)

    log.info("orchestrator: policy decision email_id=%s action=%s",
             eid, decision.action.value)

    # ------------------------------------------------------------------
    # 6. Extract task candidate (structural)
    # ------------------------------------------------------------------
    candidate: TaskCandidate = extract_task(email, classification)

    # ------------------------------------------------------------------
    # 7. Dry-run short-circuit
    # ------------------------------------------------------------------
    if dry_run:
        return OrchestrationResult(
            status=DRY_RUN,
            email_id=eid,
            decision=decision,
            candidate=candidate,
            notify=False,
            reason="Dry-run mode: no database writes performed.",
            summary=summary,
        )

    # ------------------------------------------------------------------
    # 8. Act on policy decision
    # ------------------------------------------------------------------
    action = decision.action

    if action == PermittedAction.CREATE_TASK_AND_NOTIFY:
        try:
            task = store.create_task(
                source_email_id=eid,
                title=candidate.title,
                description=candidate.description,
                priority=candidate.priority,
                deadline_state=candidate.deadline_state,
                confirmation_status=ConfirmationStatus.NOT_REQUIRED,
                deadline=candidate.deadline,
            )
        except DuplicateEmailError:
            # Race condition: another process inserted between the check
            # and the insert.  Treat as a safe idempotent skip.
            log.warning("orchestrator: duplicate task race for email_id=%s", eid)
            return OrchestrationResult(
                status=SKIPPED, email_id=eid, decision=decision, candidate=candidate,
                reason="Duplicate detected during insert; treated as already processed.",
            )
        except Exception as exc:
            log.error("orchestrator: task creation failed email_id=%s err=%s",
                      eid, type(exc).__name__)
            return OrchestrationResult(
                status=ERROR, email_id=eid, decision=decision, candidate=candidate,
                reason=f"Task creation failed: {type(exc).__name__}",
            )

        log.info("orchestrator: task created email_id=%s task_id=%s", eid, task.id)
        return OrchestrationResult(
            status=TASK_CREATED,
            email_id=eid,
            decision=decision,
            candidate=candidate,
            task_id=task.id,
            notify=True,
            reason=f"Task created: {candidate.priority} — {candidate.title[:60]}",
            summary=summary,
        )

    if action == PermittedAction.ASK_CONFIRMATION:
        try:
            task = store.create_task(
                source_email_id=eid,
                title=candidate.title,
                description=candidate.description,
                priority=candidate.priority,
                deadline_state=candidate.deadline_state,
                confirmation_status=ConfirmationStatus.PENDING,
                deadline=candidate.deadline,
            )
        except DuplicateEmailError:
            log.warning("orchestrator: duplicate P2 task race for email_id=%s", eid)
            return OrchestrationResult(
                status=SKIPPED, email_id=eid, decision=decision, candidate=candidate,
                reason="Duplicate detected during insert; treated as already processed.",
                summary=summary,
            )
        except Exception as exc:
            log.error("orchestrator: P2 task creation failed email_id=%s err=%s",
                      eid, type(exc).__name__)
            return OrchestrationResult(
                status=ERROR, email_id=eid, decision=decision, candidate=candidate,
                reason=f"P2 task creation failed: {type(exc).__name__}",
                summary=summary,
            )

        log.info("orchestrator: task awaiting confirmation email_id=%s task_id=%s",
                 eid, task.id)
        return OrchestrationResult(
            status=AWAITING_CONFIRMATION,
            email_id=eid,
            decision=decision,
            candidate=candidate,
            task_id=task.id,
            notify=True,
            reason=f"Task pending confirmation: {candidate.title[:60]}",
            summary=summary,
        )

    if action == PermittedAction.CLARIFY_DEADLINE:
        return OrchestrationResult(
            status=NO_ACTION,
            email_id=eid,
            decision=decision,
            candidate=candidate,
            reason=f"Deadline clarification needed: {candidate.evidence}",
            summary=summary,
        )

    if action == PermittedAction.SUMMARIZE_ONLY:
        return OrchestrationResult(
            status=NO_ACTION,
            email_id=eid,
            decision=decision,
            candidate=candidate,
            reason="Summary only; no task required.",
            summary=summary,
        )

    # REVIEW_CLASSIFICATION or unmatched
    return OrchestrationResult(
        status=NEEDS_REVIEW,
        email_id=eid,
        decision=decision,
        candidate=candidate,
        reason="Classification requires human review; no automated action taken.",
        summary=summary,
    )
