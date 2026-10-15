"""Telegram integration for MailMate AI.

Provides a NotificationTransport abstraction so all application code
is tested against the mock and real HTTP calls are isolated here.

Credentials
-----------
``TelegramTransport`` reads ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID`` from environment variables.  These must never be
written into source files, committed to Git, or emitted in log messages.
"""

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InlineButton:
    """A single button in a Telegram inline keyboard row."""
    text: str
    callback_data: str


@dataclass
class SentMessage:
    """Record of a message sent via the transport."""
    text: str
    buttons: list[list[InlineButton]] = field(default_factory=list)
    success: bool = True


# ---------------------------------------------------------------------------
# Transport abstraction
# ---------------------------------------------------------------------------

class NotificationTransport(ABC):
    """Abstract Telegram notification transport.

    All application code targets this interface so that tests use the
    mock and production uses TelegramTransport without any code changes.
    """

    @abstractmethod
    def send_message(
        self,
        text: str,
        buttons: Optional[list[list[InlineButton]]] = None,
    ) -> bool:
        """Send a message.  Returns True on success, False on failure."""

    @abstractmethod
    def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
        """Acknowledge a Telegram callback query."""


# ---------------------------------------------------------------------------
# Mock transport (used in tests and dry-run)
# ---------------------------------------------------------------------------

class MockTransport(NotificationTransport):
    """In-memory transport that records all calls without making HTTP requests."""

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.answered_callbacks: list[tuple[str, str]] = []
        self.fail_next: bool = False  # set True to simulate a send failure

    def send_message(
        self,
        text: str,
        buttons: Optional[list[list[InlineButton]]] = None,
    ) -> bool:
        if self.fail_next:
            self.fail_next = False
            log.warning("mock_transport: simulated send failure")
            return False
        self.sent.append(SentMessage(text=text, buttons=buttons or []))
        return True

    def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
        self.answered_callbacks.append((callback_query_id, text))
        return True

    @property
    def last_message(self) -> Optional[SentMessage]:
        return self.sent[-1] if self.sent else None

    @property
    def message_count(self) -> int:
        return len(self.sent)


# ---------------------------------------------------------------------------
# Real Telegram transport
# ---------------------------------------------------------------------------

class TelegramTransport(NotificationTransport):
    """Calls the Telegram Bot API over HTTPS.

    Reads credentials from environment variables:
        TELEGRAM_BOT_TOKEN  — the bot token issued by @BotFather
        TELEGRAM_CHAT_ID    — target chat / group chat ID

    Never use this transport until policy, storage, and orchestration
    tests pass.  Prefer MockTransport in all automated tests.
    """

    _API_BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self._token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

        if not self._token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is not set. "
                "Export it as an environment variable — never hardcode it."
            )
        if not self._chat_id:
            raise ValueError(
                "TELEGRAM_CHAT_ID is not set. "
                "Export it as an environment variable."
            )

        # Log a safe prefix only — never the full token.
        log.info(
            "telegram: transport initialised token_prefix=%s chat_id=%s",
            self._token[:8] + "…",
            self._chat_id,
        )

    def _url(self, method: str) -> str:
        return self._API_BASE.format(token=self._token, method=method)

    def _build_reply_markup(
        self, buttons: list[list[InlineButton]]
    ) -> Optional[dict]:
        if not buttons:
            return None
        return {
            "inline_keyboard": [
                [{"text": btn.text, "callback_data": btn.callback_data}
                 for btn in row]
                for row in buttons
            ]
        }

    def send_message(
        self,
        text: str,
        buttons: Optional[list[list[InlineButton]]] = None,
    ) -> bool:
        try:
            import requests  # imported lazily to keep tests fast
        except ImportError:
            log.error("telegram: 'requests' package not installed")
            return False

        payload: dict = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        markup = self._build_reply_markup(buttons or [])
        if markup:
            payload["reply_markup"] = markup

        try:
            resp = requests.post(self._url("sendMessage"), json=payload, timeout=10)
            if not resp.ok:
                log.warning(
                    "telegram: sendMessage failed status=%s", resp.status_code
                )
                return False
            return True
        except Exception as exc:
            log.error("telegram: sendMessage error type=%s", type(exc).__name__)
            return False

    def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
        try:
            import requests
        except ImportError:
            return False

        try:
            resp = requests.post(
                self._url("answerCallbackQuery"),
                json={"callback_query_id": callback_query_id, "text": text},
                timeout=10,
            )
            return resp.ok
        except Exception as exc:
            log.error("telegram: answerCallbackQuery error type=%s",
                      type(exc).__name__)
            return False
