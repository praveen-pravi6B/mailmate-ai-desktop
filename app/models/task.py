"""Task candidate model for MailMate AI.

A TaskCandidate is the proposed task extracted from an actionable email,
before it is persisted to the task store or presented to the user for
confirmation.  It is produced by the task extractor and consumed by the
orchestration layer.

This module defines the data shape only.  It does not write to the
database or trigger any side effect.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TaskCandidate:
    """Proposed task derived from a classified email.

    Fields
    ------
    email_id:
        ``message_id`` of the source ``NormalizedEmail``.  Used as the
        idempotency key when the task is persisted.
    title:
        Short task title derived from the subject line.
    description:
        Task description derived from the email body.  This is a
        structural extraction, not an AI-generated summary.
    priority:
        P0 / P1 / P2 — propagated from the ``ClassificationResult``.
    deadline:
        ISO-8601 date string (``YYYY-MM-DD``) if an explicit date was
        found in the email body, otherwise ``None``.  Relative dates
        (e.g. "tomorrow", "next Friday") are never resolved here; they
        result in ``deadline_state = AMBIGUOUS`` with ``deadline = None``.
    deadline_state:
        NONE / CLEAR / AMBIGUOUS — determined by the task extractor.
        NONE   — no date was mentioned in the email.
        CLEAR  — a single, explicit, unambiguous calendar date was found.
        AMBIGUOUS — date is relative, vague, or multiple conflicting
                    dates were present.
    evidence:
        A short quote or reason from the email body that supports the
        extracted deadline or the absence of one.  Never invented.
    """

    email_id: str
    title: str
    description: str
    priority: str
    deadline: Optional[str]
    deadline_state: str
    evidence: str
