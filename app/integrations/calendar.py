"""Google Calendar integration for MailMate AI.

Provides a CalendarTransport abstraction so all calendar logic is tested
against MockCalendarTransport and real API calls are isolated in
GoogleCalendarTransport.

Safety rules
------------
- Calendar event creation is a SEPARATE step from task creation.
  A task being created does NOT automatically warrant a calendar event.
- Real writes require explicit approval (``require_approval=True`` guard
  on GoogleCalendarTransport).
- Credentials are read from environment variables — never hardcoded.
- No event is ever created for an AMBIGUOUS or NONE deadline.
- Time and timezone must be supplied by the caller — they are never
  invented from a date-only deadline.
"""

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event request / response shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalendarEventRequest:
    """All fields required to create a calendar event.

    ``date``     — ISO-8601 date string (YYYY-MM-DD) from the task record.
    ``time``     — HH:MM in 24-hour format, supplied by the caller.
                   Never inferred from a date-only deadline.
    ``timezone`` — IANA timezone string (e.g. ``"Asia/Kolkata"``),
                   supplied by the caller.  Never assumed.
    """

    task_id: str
    title: str
    description: str
    date: str       # YYYY-MM-DD
    time: str       # HH:MM  — required; never invented
    timezone: str   # IANA   — required; never assumed


@dataclass
class CreatedEvent:
    """Record of a successfully created calendar event."""
    event_id: str
    request: CalendarEventRequest


# ---------------------------------------------------------------------------
# Transport abstraction
# ---------------------------------------------------------------------------

class CalendarTransport(ABC):
    """Abstract calendar transport.

    All application code targets this interface so that tests use the
    mock and production uses GoogleCalendarTransport without code changes.
    """

    @abstractmethod
    def create_event(self, request: CalendarEventRequest) -> str:
        """Create a calendar event and return the provider event ID.

        Raises ``RuntimeError`` on failure.
        """


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

class MockCalendarTransport(CalendarTransport):
    """In-memory transport that records all calls without any API requests."""

    def __init__(self) -> None:
        self.created: list[CreatedEvent] = []
        self._next_event_id: int = 1
        self.fail_next: bool = False  # set True to simulate an API failure

    def create_event(self, request: CalendarEventRequest) -> str:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("Simulated calendar API failure")
        event_id = f"mock-event-{self._next_event_id}"
        self._next_event_id += 1
        self.created.append(CreatedEvent(event_id=event_id, request=request))
        log.debug("mock_calendar: created event_id=%s task_id=%s",
                  event_id, request.task_id)
        return event_id

    @property
    def event_count(self) -> int:
        return len(self.created)

    @property
    def last_event(self) -> Optional[CreatedEvent]:
        return self.created[-1] if self.created else None


# ---------------------------------------------------------------------------
# Real Google Calendar transport
# ---------------------------------------------------------------------------

class GoogleCalendarTransport(CalendarTransport):
    """Creates events via the Google Calendar API.

    Credentials
    -----------
    Reads OAuth tokens from the path in ``GOOGLE_TOKEN_PATH`` (default
    ``token.json``) and the client secrets from ``GOOGLE_CREDENTIALS_PATH``
    (default ``credentials.json``).  These files must never be committed
    to Git.

    Safety gate
    -----------
    This class refuses to create real events unless ``live_writes=True``
    is passed explicitly.  Do not enable live writes until policy,
    storage, orchestration, and notification tests pass end-to-end.
    """

    _SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
    _DEFAULT_CALENDAR_ID = "primary"

    def __init__(
        self,
        live_writes: bool = False,
        token_path: Optional[str] = None,
        credentials_path: Optional[str] = None,
        calendar_id: Optional[str] = None,
    ) -> None:
        if not live_writes:
            raise ValueError(
                "GoogleCalendarTransport requires live_writes=True. "
                "Use MockCalendarTransport in tests and dry-run mode. "
                "Enable live_writes only after end-to-end tests pass."
            )

        self._token_path = token_path or os.environ.get(
            "GOOGLE_TOKEN_PATH", "token.json"
        )
        self._credentials_path = credentials_path or os.environ.get(
            "GOOGLE_CREDENTIALS_PATH", "credentials.json"
        )
        self._calendar_id = (
            calendar_id
            or os.environ.get("GOOGLE_CALENDAR_ID", self._DEFAULT_CALENDAR_ID)
        )

        log.info(
            "calendar: GoogleCalendarTransport initialised "
            "calendar_id=%s token_path=%s",
            self._calendar_id,
            self._token_path,
        )

    def _build_service(self):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Google API client libraries not installed. "
                "Run: pip install google-api-python-client google-auth-oauthlib"
            ) from exc

        creds = None
        if os.path.exists(self._token_path):
            creds = Credentials.from_authorized_user_file(
                self._token_path, self._SCOPES
            )
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise RuntimeError(
                    f"No valid credentials found at {self._token_path}. "
                    "Run auth_google.py to obtain a refresh token."
                )
        return build("calendar", "v3", credentials=creds)

    def create_event(self, request: CalendarEventRequest) -> str:
        service = self._build_service()

        start_dt = f"{request.date}T{request.time}:00"
        body = {
            "summary": request.title,
            "description": request.description,
            "start": {"dateTime": start_dt, "timeZone": request.timezone},
            "end": {"dateTime": start_dt, "timeZone": request.timezone},
        }

        try:
            event = (
                service.events()
                .insert(calendarId=self._calendar_id, body=body)
                .execute()
            )
            event_id: str = event.get("id", "")
            log.info("calendar: event created event_id=%s task_id=%s",
                     event_id, request.task_id)
            return event_id
        except Exception as exc:
            log.error("calendar: create_event failed task_id=%s err=%s",
                      request.task_id, type(exc).__name__)
            raise RuntimeError(
                f"Calendar API error: {type(exc).__name__}"
            ) from exc
