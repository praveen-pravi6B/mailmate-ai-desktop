"""Task extractor for MailMate AI.

Extracts a TaskCandidate from a NormalizedEmail and a ClassificationResult.

Deadline detection rules
------------------------
Only explicit, unambiguous calendar dates in the email body result in
``deadline_state = CLEAR``.  Everything else is AMBIGUOUS or NONE:

  CLEAR     — exactly one explicit date found (ISO, or common written forms
               such as "October 20" / "20 Oct 2026").  A time component is
               not required.
  AMBIGUOUS — a relative term ("tomorrow", "next Friday", "end of week",
               "soon", "ASAP") OR an urgency-only word ("now",
               "immediately") OR multiple differing explicit dates.
  NONE      — no date or time reference of any kind.

Dates are never invented.  Relative dates are never resolved to a
calendar date.  When deadline_state is AMBIGUOUS, ``deadline`` is None.

This module does not call the Laya model, apply policy, write to storage,
or send notifications.
"""

import re
from typing import Optional

from app.laya_engine.classifier import ClassificationResult
from app.models.email import NormalizedEmail
from app.models.task import TaskCandidate
from app.policy.decision_policy import DeadlineState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TITLE_MAX = 120     # characters
_DESC_MAX = 500      # characters

# ---------------------------------------------------------------------------
# Date detection patterns
# ---------------------------------------------------------------------------

# ISO date: 2026-10-20  (time component optional and ignored for deadline)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Written month-day forms:  "October 20", "Oct 20", "20 October", "20 Oct"
# Also accepts a year: "October 20, 2026" / "20 Oct 2026"
_MONTH_NAMES = (
    "january|february|march|april|may|june|july|august|september|"
    "october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
)
_WRITTEN_DATE_RE = re.compile(
    rf"(?:"
    rf"\b(?:{_MONTH_NAMES})\b\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s*,?\s*\d{{4}})?"
    rf"|"
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_NAMES})\b\.?(?:\s+\d{{4}})?"
    rf")",
    re.IGNORECASE,
)

# Slash/dot dates: 10/20/2026, 20/10/2026, 10.20.2026 — only with full year
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}[/\.]\d{1,2}[/\.]\d{4}\b")

# Relative / vague terms that signal AMBIGUOUS.
# "this week's" (possessive) is excluded via a negative lookahead so that
# phrases like "this week's newsletter" don't trigger AMBIGUOUS.
_RELATIVE_RE = re.compile(
    r"\b("
    r"tomorrow|tonight|today|this\s+(?:afternoon|morning|evening)|"
    r"next\s+(?:week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"this\s+(?:week|month|friday|monday|tuesday|wednesday|thursday|saturday|sunday)(?!['\u2019]s)|"
    r"end\s+of\s+(?:week|month|day|quarter)|"
    r"by\s+(?:eod|cob|end\s+of\s+(?:day|week|month))|"
    r"(?:by\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"asap|a\.s\.a\.p\.|soon|shortly|urgently|immediately|right\s+away|"
    r"at\s+your\s+earliest|as\s+soon\s+as\s+possible|"
    r"(?:in|within)\s+\d+\s+(?:hour|day|week|month)s?"
    r")\b",
    re.IGNORECASE,
)


def _find_explicit_dates(text: str) -> list[str]:
    """Return all explicit date strings found in text."""
    matches: list[str] = []
    matches += _ISO_DATE_RE.findall(text)
    matches += [m.group() for m in _WRITTEN_DATE_RE.finditer(text)]
    matches += _SLASH_DATE_RE.findall(text)
    return matches


def _has_relative_terms(text: str) -> bool:
    return bool(_RELATIVE_RE.search(text))


def _detect_deadline(body: str) -> tuple[DeadlineState, Optional[str], str]:
    """Detect deadline state from the email body.

    Returns
    -------
    (deadline_state, deadline_value, evidence)
    deadline_value is an ISO date string when state is CLEAR, else None.
    evidence is the matched text or a description of why state was chosen.
    """
    explicit = _find_explicit_dates(body)
    has_relative = _has_relative_terms(body)

    if not explicit and not has_relative:
        return DeadlineState.NONE, None, "No date or time reference found."

    if has_relative and not explicit:
        match = _RELATIVE_RE.search(body)
        evidence = match.group() if match else "Relative time reference found."
        return DeadlineState.AMBIGUOUS, None, f'Relative date: "{evidence}"'

    if explicit:
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique = [d for d in explicit if not (d in seen or seen.add(d))]  # type: ignore[func-returns-value]

        if len(unique) > 1:
            return (
                DeadlineState.AMBIGUOUS,
                None,
                f"Multiple conflicting dates found: {', '.join(repr(d) for d in unique[:3])}",
            )

        # Single explicit date — CLEAR.
        raw_date = unique[0]

        # Normalise to ISO YYYY-MM-DD when the date is already in that form.
        iso_match = _ISO_DATE_RE.fullmatch(raw_date.strip())
        deadline_value = raw_date.strip() if iso_match else None

        # If both relative AND explicit are present, that's ambiguous.
        if has_relative:
            return (
                DeadlineState.AMBIGUOUS,
                None,
                f'Explicit date "{raw_date}" alongside relative reference.',
            )

        return DeadlineState.CLEAR, deadline_value, f'Explicit date: "{raw_date}"'

    # Should not be reachable.
    return DeadlineState.AMBIGUOUS, None, "Unable to determine deadline clearly."


def detect_deadline_state(body: str) -> str:
    """Return the DeadlineState value string for a given email body.

    Public wrapper around ``_detect_deadline`` for use by the orchestrator
    so it can pass the correct deadline_state to the classifier before
    building the full TaskCandidate.
    """
    state, _, _ = _detect_deadline(body)
    return state.value


# ---------------------------------------------------------------------------
# Title / description helpers
# ---------------------------------------------------------------------------

def _derive_title(subject: str, body: str) -> str:
    """Derive a task title from the subject line."""
    # Strip common reply/forward prefixes.
    cleaned = re.sub(r"^(re|fw|fwd)\s*:\s*", "", subject.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned:
        return cleaned[:_TITLE_MAX]
    # Fall back to the first non-empty body sentence.
    first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return first_line[:_TITLE_MAX] or "Untitled task"


def _derive_description(body: str) -> str:
    """Derive a task description from the email body (structural, not summarized)."""
    stripped = body.strip()
    if not stripped:
        return ""
    if len(stripped) <= _DESC_MAX:
        return stripped
    # Truncate at the last sentence boundary within the limit.
    truncated = stripped[:_DESC_MAX]
    last_stop = max(
        truncated.rfind("."),
        truncated.rfind("!"),
        truncated.rfind("?"),
    )
    if last_stop > _DESC_MAX // 2:
        return truncated[: last_stop + 1].strip()
    return truncated.rstrip() + "…"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_task(
    email: NormalizedEmail,
    classification: ClassificationResult,
) -> TaskCandidate:
    """Derive a TaskCandidate from a normalized email and classification result.

    Parameters
    ----------
    email:
        Normalized email produced by the email normaliser.
    classification:
        Output from ``classify_email()``.  Priority is taken from here;
        deadline_state on the ClassificationResult is IGNORED — the
        extractor re-derives it from the email body to ensure it is
        grounded in the actual text.

    Returns
    -------
    TaskCandidate:
        The proposed task.  ``deadline_state`` reflects what was found in
        the body.  ``deadline`` is only set when state is CLEAR.

    Notes
    -----
    - Dates are never invented or inferred beyond what is written.
    - Relative dates ("tomorrow", "next Friday") are classified AMBIGUOUS
      and never resolved to a calendar value.
    - If multiple different explicit dates appear, state is AMBIGUOUS.
    - This function never writes to storage or sends notifications.
    """
    deadline_state, deadline_value, evidence = _detect_deadline(email.body)

    return TaskCandidate(
        email_id=email.message_id,
        title=_derive_title(email.subject, email.body),
        description=_derive_description(email.body),
        priority=classification.priority,
        deadline=deadline_value,
        deadline_state=deadline_state.value,
        evidence=evidence,
    )
