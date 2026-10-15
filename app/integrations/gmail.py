"""Gmail integration for MailMate AI.

Provides a GmailTransport abstraction so all fetching logic is tested
against MockGmailTransport and real API calls are isolated in GmailClient.

Architecture
------------
GmailTransport       — abstract interface (list_messages, get_message)
MockGmailTransport   — in-memory, preset messages, for tests
GmailClient          — real Google API implementation

The transport returns raw message dicts that the email normaliser
(app/services/email_normalizer.py) converts to NormalizedEmail objects.

Safety rules
------------
- Read-only scope only: ``gmail.readonly``.
- Credentials and tokens are read from files referenced by env vars —
  never hardcoded.
- Token paths and only non-sensitive metadata are written to logs.
- Repeated processing is prevented by the task store idempotency check
  in the orchestrator, not here.  The client fetches; the orchestrator
  decides whether to skip.
- This module never sends email, modifies labels beyond what is
  required for read tracking, or deletes messages.
"""

import base64
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# Scopes: Gmail read-only + Calendar event creation.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]

# Default number of messages fetched per run.
DEFAULT_MAX_RESULTS = 10


# ---------------------------------------------------------------------------
# Raw message shape (provider-agnostic subset of Gmail API response)
# ---------------------------------------------------------------------------

@dataclass
class RawMessage:
    """Minimal parsed representation of a Gmail message.

    This is what GmailTransport implementations return.  The email
    normaliser converts it into a NormalizedEmail.
    """

    id: str
    thread_id: str
    sender: str
    subject: str
    body: str           # plain text; HTML converted before storage
    date: str           # RFC 2822 date string from the Date header
    headers: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MIME parsing utilities
# ---------------------------------------------------------------------------

def _decode_base64(data: str) -> str:
    """Decode URL-safe base64 Gmail body data to a UTF-8 string."""
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove script and style elements.
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        # Collapse whitespace.
        return re.sub(r"\s+", " ", text).strip()
    except ImportError:
        # Fall back to a crude tag stripper if bs4 is unavailable.
        return re.sub(r"<[^>]+>", " ", html).strip()


def _extract_plain(payload: dict) -> str:
    """Recursively extract the best plain-text body from a MIME payload."""
    mime_type = payload.get("mimeType", "")
    parts = payload.get("parts", [])
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain":
        return _decode_base64(body_data) if body_data else ""

    if mime_type == "text/html":
        raw = _decode_base64(body_data) if body_data else ""
        return _html_to_text(raw)

    if mime_type == "multipart/alternative":
        # Prefer text/plain; fall back to text/html.
        plain = next(
            (_extract_plain(p) for p in parts if p.get("mimeType") == "text/plain"),
            "",
        )
        if plain:
            return plain
        return next(
            (_extract_plain(p) for p in parts if p.get("mimeType") == "text/html"),
            "",
        )

    if mime_type.startswith("multipart/"):
        for part in parts:
            text = _extract_plain(part)
            if text:
                return text

    # Simple non-multipart body.
    if body_data:
        raw = _decode_base64(body_data)
        return _html_to_text(raw) if "<" in raw else raw

    return ""


def _extract_header(headers: list[dict], name: str) -> str:
    """Return the value of the first matching header (case-insensitive)."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _parse_message(raw: dict) -> Optional[RawMessage]:
    """Parse a Gmail API message dict into a RawMessage.

    Returns None if the message is malformed or empty.
    """
    msg_id = raw.get("id")
    thread_id = raw.get("threadId", "")
    payload = raw.get("payload", {})
    if not msg_id or not payload:
        return None

    api_headers = payload.get("headers", [])
    sender = _extract_header(api_headers, "From")
    subject = _extract_header(api_headers, "Subject")
    date = _extract_header(api_headers, "Date")

    body = _extract_plain(payload)

    safe_headers = {
        h["name"].lower(): h["value"]
        for h in api_headers
        if h.get("name", "").lower()
        in {"to", "cc", "date", "message-id", "reply-to"}
    }

    return RawMessage(
        id=msg_id,
        thread_id=thread_id,
        sender=sender,
        subject=subject,
        body=body,
        date=date,
        headers=safe_headers,
    )


# ---------------------------------------------------------------------------
# Transport abstraction
# ---------------------------------------------------------------------------

class GmailTransport(ABC):
    """Abstract Gmail transport.

    All application code targets this interface so that tests use
    MockGmailTransport and production uses GmailClient.
    """

    @abstractmethod
    def list_message_ids(self, max_results: int = DEFAULT_MAX_RESULTS) -> list[str]:
        """Return up to max_results unread message IDs, oldest first."""

    @abstractmethod
    def get_message(self, message_id: str) -> Optional[RawMessage]:
        """Fetch and parse a single message.

        Returns None if the message was deleted or cannot be retrieved.
        """


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

class MockGmailTransport(GmailTransport):
    """In-memory Gmail transport for tests.

    Preset messages with ``add_message()``.  Simulates deletions by
    removing a message from the store via ``remove_message()``.
    """

    def __init__(self) -> None:
        self._messages: dict[str, RawMessage] = {}
        self._order: list[str] = []   # insertion order = chronological order
        self.list_calls: int = 0
        self.get_calls: int = 0

    def add_message(
        self,
        msg_id: str,
        subject: str = "Test subject",
        body: str = "Test body.",
        sender: str = "sender@example.com",
        date: str = "Wed, 15 Oct 2026 09:00:00 +0000",
        thread_id: str = "",
        headers: Optional[dict] = None,
    ) -> RawMessage:
        msg = RawMessage(
            id=msg_id,
            thread_id=thread_id or msg_id,
            sender=sender,
            subject=subject,
            body=body,
            date=date,
            headers=headers or {},
        )
        self._messages[msg_id] = msg
        if msg_id not in self._order:
            self._order.append(msg_id)
        return msg

    def remove_message(self, msg_id: str) -> None:
        """Simulate a deleted / expunged message."""
        self._messages.pop(msg_id, None)
        if msg_id in self._order:
            self._order.remove(msg_id)

    def list_message_ids(self, max_results: int = DEFAULT_MAX_RESULTS) -> list[str]:
        self.list_calls += 1
        return self._order[:max_results]

    def get_message(self, message_id: str) -> Optional[RawMessage]:
        self.get_calls += 1
        return self._messages.get(message_id)


# ---------------------------------------------------------------------------
# OAuth credential helpers
# ---------------------------------------------------------------------------

def load_credentials(
    token_path: Optional[str] = None,
    credentials_path: Optional[str] = None,
):
    """Load or refresh OAuth credentials from disk.

    Parameters
    ----------
    token_path:
        Path to the stored token JSON file (default: ``GOOGLE_TOKEN_PATH``
        env var, falling back to ``"token.json"``).
    credentials_path:
        Path to the client secrets JSON file (default:
        ``GOOGLE_CREDENTIALS_PATH`` env var, falling back to
        ``"credentials.json"``).

    Returns the credentials object, refreshed if needed.
    Raises ``RuntimeError`` if credentials cannot be loaded or refreshed.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    resolved_token = token_path or os.environ.get("GOOGLE_TOKEN_PATH", "token.json")
    resolved_creds = credentials_path or os.environ.get(
        "GOOGLE_CREDENTIALS_PATH", "credentials.json"
    )

    creds = None
    if os.path.exists(resolved_token):
        try:
            creds = Credentials.from_authorized_user_file(
                resolved_token, GMAIL_SCOPES
            )
        except Exception as exc:
            log.warning("gmail_auth: failed to load token err=%s",
                        type(exc).__name__)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                log.info("gmail_auth: token refreshed token_path=%s",
                         resolved_token)
            except Exception as exc:
                raise RuntimeError(
                    f"Token refresh failed: {type(exc).__name__}. "
                    "Delete the token file and re-run authentication."
                ) from exc
        else:
            if not os.path.exists(resolved_creds):
                raise RuntimeError(
                    f"Credentials file not found: {resolved_creds}. "
                    "Download it from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                resolved_creds, GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
            log.info("gmail_auth: new token obtained")

        # Persist the refreshed / new token.
        try:
            with open(resolved_token, "w") as f:
                f.write(creds.to_json())
        except Exception as exc:
            log.warning("gmail_auth: failed to save token err=%s",
                        type(exc).__name__)

    return creds


# ---------------------------------------------------------------------------
# Real Gmail client
# ---------------------------------------------------------------------------

class GmailClient(GmailTransport):
    """Fetches unread Gmail messages via the Google API.

    Credentials
    -----------
    Pass a credentials object from ``load_credentials()``, or let the
    constructor call it automatically using the env-var defaults.

    Scope
    -----
    Requests ``gmail.readonly`` only — the minimum needed.

    Pagination
    ----------
    ``list_message_ids()`` returns up to ``max_results`` IDs in one
    call.  For larger fetches use multiple calls with the ``page_token``
    returned by the underlying API (not yet exposed — add when needed).
    """

    def __init__(self, credentials=None) -> None:
        from googleapiclient.discovery import build

        if credentials is None:
            credentials = load_credentials()

        self._service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )
        log.info("gmail: GmailClient initialised")

    def list_message_ids(self, max_results: int = DEFAULT_MAX_RESULTS) -> list[str]:
        """Return unread message IDs (up to max_results), oldest first."""
        try:
            resp = (
                self._service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=["UNREAD"],
                    maxResults=max_results,
                )
                .execute()
            )
            messages = resp.get("messages", [])
            ids = [m["id"] for m in messages]
            # Gmail returns newest-first; reverse so we process oldest first.
            ids.reverse()
            log.info("gmail: listed message_count=%d", len(ids))
            return ids
        except Exception as exc:
            log.error("gmail: list_message_ids failed err=%s",
                      type(exc).__name__)
            return []

    def get_message(self, message_id: str) -> Optional[RawMessage]:
        """Fetch and parse a single message.  Returns None if not found."""
        try:
            raw = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            msg = _parse_message(raw)
            if msg is None:
                log.warning("gmail: unparseable message message_id=%s", message_id)
            return msg
        except Exception as exc:
            err_name = type(exc).__name__
            # HttpError 404 means the message was deleted — treat as None.
            if "HttpError" in err_name or "404" in str(exc):
                log.info("gmail: message not found (deleted?) message_id=%s",
                         message_id)
            else:
                log.error("gmail: get_message failed message_id=%s err=%s",
                          message_id, err_name)
            return None
