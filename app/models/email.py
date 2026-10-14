"""Normalized email model for MailMate AI.

A NormalizedEmail is a provider-agnostic representation of an incoming
email.  It is produced by an email normaliser (e.g. from a Gmail API
response or a test fixture dict) and passed downstream to the classifier
and task extractor.

This module defines the data shape only.  It does not fetch, send, or
modify any email.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class NormalizedEmail:
    """Provider-agnostic email record.

    Fields
    ------
    provider:
        Source identifier, e.g. ``"gmail"`` or ``"test"``.
    message_id:
        Unique identifier assigned by the provider.  Used as the
        idempotency key in the task store.
    thread_id:
        Provider thread identifier, if available.
    sender:
        Sender address string, e.g. ``"Alice <alice@example.com>"``.
    subject:
        Email subject line (raw, not cleaned).
    body:
        Plain-text body.  HTML should be converted to plain text before
        normalisation.
    received_at:
        ISO-8601 UTC timestamp when the message was received by the
        provider, e.g. ``"2026-10-14T09:00:00+00:00"``.
    headers:
        Mapping of relevant headers (``to``, ``cc``, ``date``, etc.).
        Keys are lower-case.
    """

    provider: str
    message_id: str
    thread_id: Optional[str]
    sender: str
    subject: str
    body: str
    received_at: str
    headers: dict = field(default_factory=dict)
