"""Email normalizer for MailMate AI.

Converts a raw email dict (from a test fixture, Gmail API response, or
other provider) into a NormalizedEmail.

This module only transforms data.  It does not fetch emails, call the
classifier, apply policy, or write to storage.
"""

import re
from datetime import datetime, timezone
from typing import Optional

from app.models.email import NormalizedEmail

# Headers worth preserving from raw provider responses.
_HEADERS_OF_INTEREST = {"to", "cc", "bcc", "date", "reply-to", "message-id"}

# Matches a valid ISO-8601 datetime string (subset sufficient for our use).
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"          # date part
    r"(?:[T ]\d{2}:\d{2}"          # optional time
    r"(?::\d{2}(?:\.\d+)?)?"       # optional seconds/frac
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"  # optional timezone
)


def _is_iso(value: str) -> bool:
    return bool(_ISO_RE.match(value.strip()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_subject(subject: str) -> str:
    """Strip common reply/forward prefixes and collapse whitespace."""
    cleaned = re.sub(r"^(re|fw|fwd)\s*:\s*", "", subject.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip() or subject.strip()


def _extract_headers(raw: dict) -> dict:
    """Pull a safe subset of headers from the raw dict."""
    raw_headers = raw.get("headers", {})
    if not isinstance(raw_headers, dict):
        return {}
    return {
        k.lower(): v
        for k, v in raw_headers.items()
        if k.lower() in _HEADERS_OF_INTEREST
    }


def normalize(raw: dict, provider: str = "test") -> NormalizedEmail:
    """Normalize a raw email dict into a NormalizedEmail.

    Expected raw keys (all optional except ``id`` or ``message_id``):
        id / message_id   — provider message ID
        thread_id         — provider thread ID
        from / sender     — sender address
        subject           — subject line
        body              — plain-text body (HTML should be pre-converted)
        received_at / date — ISO-8601 received timestamp
        headers           — dict of raw headers

    Unknown keys are ignored.  Missing values are replaced with safe
    defaults so the normaliser never raises on malformed input.
    """

    message_id: str = (
        str(raw.get("message_id") or raw.get("id") or "").strip()
        or _utc_now()  # last-resort stable-ish fallback for test fixtures
    )

    thread_id: Optional[str] = (
        str(raw.get("thread_id")).strip() if raw.get("thread_id") else None
    )

    sender: str = str(raw.get("from") or raw.get("sender") or "").strip()
    subject: str = str(raw.get("subject") or "").strip()
    body: str = str(raw.get("body") or "").strip()

    # Received timestamp — accept ISO string or fall back to now.
    raw_ts = raw.get("received_at") or raw.get("date") or ""
    received_at: str = (
        raw_ts.strip()
        if isinstance(raw_ts, str) and _is_iso(raw_ts)
        else _utc_now()
    )

    headers = _extract_headers(raw)

    return NormalizedEmail(
        provider=provider,
        message_id=message_id,
        thread_id=thread_id,
        sender=sender,
        subject=subject,
        body=body,
        received_at=received_at,
        headers=headers,
    )
