"""Gmail polling service for MailMate AI.

Fetches unread messages via the Gmail transport, normalises them, and
yields NormalizedEmail objects for the orchestrator to process.

This module coordinates fetching, normalization, and idempotency
pre-filtering.  It does not classify emails, apply policy, or write
tasks.

Safety rules
------------
- Fetches at most ``max_results`` messages per run (default 10).
- Messages already in the task store are skipped before normalization
  so the classifier is never called for already-processed emails.
- Deleted messages (transport returns None) are logged and skipped.
- No email content (subject, body, sender) is written to logs.
- This module never sends notifications, creates tasks, or modifies
  Gmail labels.
"""

import logging
from typing import Generator

from app.integrations.gmail import GmailTransport, RawMessage, DEFAULT_MAX_RESULTS
from app.models.email import NormalizedEmail
from app.services.email_normalizer import normalize
from app.storage.task_store import TaskStore

log = logging.getLogger(__name__)


def _raw_to_dict(raw: RawMessage) -> dict:
    """Convert a RawMessage to the dict shape the normaliser expects."""
    return {
        "id": raw.id,
        "thread_id": raw.thread_id,
        "from": raw.sender,
        "subject": raw.subject,
        "body": raw.body,
        "received_at": raw.date,
        "headers": raw.headers,
    }


def fetch_new_emails(
    transport: GmailTransport,
    store: TaskStore,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> Generator[NormalizedEmail, None, None]:
    """Yield NormalizedEmail objects for unread, not-yet-processed messages.

    Parameters
    ----------
    transport:
        A GmailTransport (MockGmailTransport in tests, GmailClient in
        production).
    store:
        An open TaskStore used for idempotency pre-filtering.
    max_results:
        Maximum number of messages to fetch per call.

    Yields
    ------
    NormalizedEmail
        One per unread message not already in the task store.

    Notes
    -----
    - Deleted messages (transport.get_message returns None) are skipped.
    - Already-processed emails are skipped before normalisation.
    - Errors from the transport are logged; the generator continues.
    """
    message_ids = transport.list_message_ids(max_results=max_results)
    log.info("gmail_poller: fetched id_count=%d", len(message_ids))

    skipped_already_processed = 0
    skipped_deleted = 0
    yielded = 0

    for msg_id in message_ids:
        # Idempotency pre-filter: skip before fetching full content.
        try:
            if store.email_has_task(msg_id):
                skipped_already_processed += 1
                log.debug("gmail_poller: skip already-processed message_id=%s",
                          msg_id)
                continue
        except Exception as exc:
            log.error("gmail_poller: store check failed message_id=%s err=%s",
                      msg_id, type(exc).__name__)
            continue

        # Fetch full message.
        raw: RawMessage | None = transport.get_message(msg_id)
        if raw is None:
            skipped_deleted += 1
            log.info("gmail_poller: skip deleted/missing message_id=%s", msg_id)
            continue

        email = normalize(_raw_to_dict(raw), provider="gmail")
        yielded += 1
        yield email

    log.info(
        "gmail_poller: done yielded=%d skipped_processed=%d skipped_deleted=%d",
        yielded,
        skipped_already_processed,
        skipped_deleted,
    )
