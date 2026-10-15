"""Tests for app/integrations/gmail.py and app/services/gmail_poller.py.

All tests use MockGmailTransport and an in-memory TaskStore.
No real Gmail API calls are made.
"""

import base64
import unittest

from app.integrations.gmail import (
    MockGmailTransport,
    _decode_base64,
    _extract_plain,
    _html_to_text,
    _parse_message,
)
from app.services.gmail_poller import fetch_new_emails
from app.storage.task_store import ConfirmationStatus, TaskStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store() -> TaskStore:
    return TaskStore(":memory:")


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def make_gmail_payload(text: str = "Hello body.", mime: str = "text/plain") -> dict:
    """Build a minimal Gmail API message dict."""
    return {
        "id": "msg-001",
        "threadId": "thread-001",
        "payload": {
            "mimeType": mime,
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "Subject", "value": "Test subject"},
                {"name": "Date", "value": "Wed, 15 Oct 2026 09:00:00 +0000"},
            ],
            "body": {"data": _b64(text), "size": len(text)},
            "parts": [],
        },
    }


def make_multipart_payload(plain: str, html: str) -> dict:
    """Build a multipart/alternative Gmail API message dict."""
    return {
        "id": "msg-multi",
        "threadId": "thread-multi",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "bob@example.com"},
                {"name": "Subject", "value": "Multipart test"},
                {"name": "Date", "value": "Wed, 15 Oct 2026 10:00:00 +0000"},
            ],
            "body": {},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64(plain)},
                    "parts": [],
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64(f"<p>{html}</p>")},
                    "parts": [],
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Base64 decoding
# ---------------------------------------------------------------------------

class TestDecodeBase64(unittest.TestCase):

    def test_decodes_standard_base64(self):
        encoded = _b64("Hello, world!")
        self.assertEqual(_decode_base64(encoded), "Hello, world!")

    def test_handles_padding_variants(self):
        for text in ["a", "ab", "abc", "abcd"]:
            encoded = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
            self.assertEqual(_decode_base64(encoded), text)

    def test_returns_empty_on_invalid(self):
        result = _decode_base64("!!!invalid!!!")
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# HTML to text
# ---------------------------------------------------------------------------

class TestHtmlToText(unittest.TestCase):

    def test_strips_tags(self):
        result = _html_to_text("<p>Hello <b>world</b></p>")
        self.assertIn("Hello", result)
        self.assertIn("world", result)
        self.assertNotIn("<p>", result)

    def test_removes_script(self):
        result = _html_to_text("<script>alert(1)</script>body text")
        self.assertNotIn("alert", result)
        self.assertIn("body text", result)

    def test_removes_style(self):
        result = _html_to_text("<style>.x{color:red}</style>body text")
        self.assertNotIn("color", result)

    def test_empty_html_gives_empty(self):
        self.assertEqual(_html_to_text(""), "")


# ---------------------------------------------------------------------------
# MIME parsing
# ---------------------------------------------------------------------------

class TestExtractPlain(unittest.TestCase):

    def test_text_plain_extracted(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _b64("Plain text body.")},
            "parts": [],
        }
        self.assertEqual(_extract_plain(payload), "Plain text body.")

    def test_text_html_converted(self):
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64("<p>HTML body.</p>")},
            "parts": [],
        }
        result = _extract_plain(payload)
        self.assertIn("HTML body", result)

    def test_multipart_alternative_prefers_plain(self):
        payload = make_multipart_payload("Plain part.", "HTML part.")["payload"]
        result = _extract_plain(payload)
        self.assertIn("Plain part.", result)
        self.assertNotIn("HTML part.", result)

    def test_multipart_alternative_falls_back_to_html(self):
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<p>Only HTML.</p>")},
                    "parts": [],
                }
            ],
        }
        result = _extract_plain(payload)
        self.assertIn("Only HTML.", result)

    def test_empty_body_gives_empty(self):
        payload = {"mimeType": "text/plain", "body": {}, "parts": []}
        self.assertEqual(_extract_plain(payload), "")


class TestParseMessage(unittest.TestCase):

    def test_parses_plain_text_message(self):
        raw = make_gmail_payload("Fix the bug please.")
        msg = _parse_message(raw)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.id, "msg-001")
        self.assertEqual(msg.sender, "alice@example.com")
        self.assertEqual(msg.subject, "Test subject")
        self.assertIn("Fix the bug", msg.body)

    def test_parses_multipart_message(self):
        raw = make_multipart_payload("Plain text.", "HTML text.")
        msg = _parse_message(raw)
        self.assertIsNotNone(msg)
        self.assertIn("Plain text.", msg.body)

    def test_returns_none_for_missing_id(self):
        raw = {"payload": {"mimeType": "text/plain", "headers": [], "body": {}}}
        self.assertIsNone(_parse_message(raw))

    def test_returns_none_for_empty_payload(self):
        raw = {"id": "x"}
        self.assertIsNone(_parse_message(raw))

    def test_extracts_known_headers(self):
        raw = {
            "id": "msg-h",
            "threadId": "t",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "a@b.com"},
                    {"name": "Subject", "value": "Subj"},
                    {"name": "Date", "value": "Wed, 15 Oct 2026"},
                    {"name": "To", "value": "me@b.com"},
                    {"name": "X-Secret", "value": "hidden"},
                ],
                "body": {"data": _b64("body")},
                "parts": [],
            },
        }
        msg = _parse_message(raw)
        self.assertIn("to", msg.headers)
        self.assertNotIn("x-secret", msg.headers)


# ---------------------------------------------------------------------------
# MockGmailTransport
# ---------------------------------------------------------------------------

class TestMockGmailTransport(unittest.TestCase):

    def test_add_and_list(self):
        t = MockGmailTransport()
        t.add_message("msg-1", subject="Subj")
        ids = t.list_message_ids()
        self.assertIn("msg-1", ids)

    def test_get_message_returns_raw_message(self):
        t = MockGmailTransport()
        t.add_message("msg-1", subject="Hello")
        msg = t.get_message("msg-1")
        self.assertIsNotNone(msg)
        self.assertEqual(msg.subject, "Hello")

    def test_get_deleted_message_returns_none(self):
        t = MockGmailTransport()
        t.add_message("msg-1")
        t.remove_message("msg-1")
        self.assertIsNone(t.get_message("msg-1"))

    def test_list_respects_max_results(self):
        t = MockGmailTransport()
        for i in range(5):
            t.add_message(f"msg-{i}")
        ids = t.list_message_ids(max_results=3)
        self.assertEqual(len(ids), 3)

    def test_nonexistent_message_returns_none(self):
        t = MockGmailTransport()
        self.assertIsNone(t.get_message("nonexistent"))

    def test_tracks_call_counts(self):
        t = MockGmailTransport()
        t.add_message("m1")
        t.list_message_ids()
        t.get_message("m1")
        self.assertEqual(t.list_calls, 1)
        self.assertEqual(t.get_calls, 1)


# ---------------------------------------------------------------------------
# fetch_new_emails (gmail_poller)
# ---------------------------------------------------------------------------

class TestFetchNewEmails(unittest.TestCase):

    def test_yields_normalized_emails(self):
        t = MockGmailTransport()
        t.add_message("m1", subject="Fix bug", body="Please fix the login bug by 2026-10-20.")
        store = make_store()
        emails = list(fetch_new_emails(t, store, max_results=10))
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].subject, "Fix bug")
        self.assertEqual(emails[0].provider, "gmail")

    def test_skips_already_processed(self):
        t = MockGmailTransport()
        t.add_message("m1", subject="Old email")
        store = make_store()
        # Mark as already processed by creating a task.
        store.create_task(
            source_email_id="m1",
            title="Old task",
            description="Already done.",
            priority="P1",
            deadline_state="CLEAR",
            confirmation_status=ConfirmationStatus.NOT_REQUIRED,
            deadline="2026-10-20",
        )
        emails = list(fetch_new_emails(t, store))
        self.assertEqual(len(emails), 0)

    def test_skips_deleted_messages(self):
        t = MockGmailTransport()
        t.add_message("m1")
        t.add_message("m2", subject="Valid")
        t.remove_message("m1")
        store = make_store()
        emails = list(fetch_new_emails(t, store))
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].subject, "Valid")

    def test_empty_inbox_yields_nothing(self):
        t = MockGmailTransport()
        store = make_store()
        emails = list(fetch_new_emails(t, store))
        self.assertEqual(emails, [])

    def test_respects_max_results(self):
        t = MockGmailTransport()
        for i in range(5):
            t.add_message(f"m{i}", subject=f"Email {i}")
        store = make_store()
        emails = list(fetch_new_emails(t, store, max_results=3))
        self.assertEqual(len(emails), 3)

    def test_multiple_messages_all_yielded(self):
        t = MockGmailTransport()
        t.add_message("m1", subject="First")
        t.add_message("m2", subject="Second")
        store = make_store()
        emails = list(fetch_new_emails(t, store))
        self.assertEqual(len(emails), 2)

    def test_message_id_used_as_provider_id(self):
        t = MockGmailTransport()
        t.add_message("unique-id-xyz", subject="Test")
        store = make_store()
        emails = list(fetch_new_emails(t, store))
        self.assertEqual(emails[0].message_id, "unique-id-xyz")

    def test_mix_of_new_and_processed(self):
        t = MockGmailTransport()
        t.add_message("old", subject="Old")
        t.add_message("new", subject="New")
        store = make_store()
        store.create_task(
            source_email_id="old",
            title="Old task",
            description="Done.",
            priority="P2",
            deadline_state="NONE",
            confirmation_status=ConfirmationStatus.PENDING,
        )
        emails = list(fetch_new_emails(t, store))
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].subject, "New")


if __name__ == "__main__":
    unittest.main()
