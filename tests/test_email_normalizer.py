"""Tests for app/services/email_normalizer.py."""

import unittest

from app.services.email_normalizer import normalize


def raw(**kwargs) -> dict:
    defaults = {
        "id": "msg-001",
        "from": "alice@example.com",
        "subject": "Fix login bug",
        "body": "Please fix the login bug by tomorrow.",
        "received_at": "2026-10-14T09:00:00+00:00",
    }
    defaults.update(kwargs)
    return defaults


class TestBasicNormalization(unittest.TestCase):

    def test_message_id_from_id_key(self):
        email = normalize(raw(id="abc-123"))
        self.assertEqual(email.message_id, "abc-123")

    def test_message_id_from_message_id_key(self):
        email = normalize({"message_id": "msg-xyz", "from": "a@b.com",
                           "subject": "S", "body": "B",
                           "received_at": "2026-10-14T00:00:00+00:00"})
        self.assertEqual(email.message_id, "msg-xyz")

    def test_sender_from_from_key(self):
        email = normalize(raw(**{"from": "bob@example.com"}))
        self.assertEqual(email.sender, "bob@example.com")

    def test_sender_from_sender_key(self):
        email = normalize({"id": "1", "sender": "carol@example.com",
                           "subject": "Hi", "body": "body",
                           "received_at": "2026-10-14T00:00:00+00:00"})
        self.assertEqual(email.sender, "carol@example.com")

    def test_subject_preserved(self):
        email = normalize(raw(subject="Important update"))
        self.assertEqual(email.subject, "Important update")

    def test_body_preserved(self):
        email = normalize(raw(body="Please review the document."))
        self.assertEqual(email.body, "Please review the document.")

    def test_provider_default_is_test(self):
        email = normalize(raw())
        self.assertEqual(email.provider, "test")

    def test_provider_can_be_set(self):
        email = normalize(raw(), provider="gmail")
        self.assertEqual(email.provider, "gmail")

    def test_received_at_valid_iso(self):
        email = normalize(raw(received_at="2026-10-14T09:00:00+00:00"))
        self.assertEqual(email.received_at, "2026-10-14T09:00:00+00:00")

    def test_thread_id_preserved(self):
        email = normalize(raw(thread_id="thread-99"))
        self.assertEqual(email.thread_id, "thread-99")

    def test_thread_id_none_when_absent(self):
        data = raw()
        data.pop("thread_id", None)
        email = normalize(data)
        self.assertIsNone(email.thread_id)

    def test_result_is_immutable(self):
        email = normalize(raw())
        with self.assertRaises((TypeError, AttributeError)):
            email.subject = "changed"  # type: ignore[misc]


class TestMissingAndFallbackFields(unittest.TestCase):

    def test_missing_body_defaults_to_empty(self):
        data = raw()
        del data["body"]
        email = normalize(data)
        self.assertEqual(email.body, "")

    def test_missing_subject_defaults_to_empty(self):
        data = raw()
        del data["subject"]
        email = normalize(data)
        self.assertEqual(email.subject, "")

    def test_missing_sender_defaults_to_empty(self):
        data = raw()
        del data["from"]
        email = normalize(data)
        self.assertEqual(email.sender, "")

    def test_invalid_received_at_falls_back_to_now(self):
        email = normalize(raw(received_at="not-a-date"))
        # Falls back to a generated timestamp — just check it looks like ISO
        self.assertRegex(email.received_at, r"^\d{4}-\d{2}-\d{2}")

    def test_missing_received_at_falls_back_to_now(self):
        data = raw()
        del data["received_at"]
        email = normalize(data)
        self.assertRegex(email.received_at, r"^\d{4}-\d{2}-\d{2}")

    def test_missing_id_generates_fallback(self):
        email = normalize({"from": "a@b.com", "subject": "S",
                           "body": "B", "received_at": "2026-10-14T00:00:00+00:00"})
        self.assertTrue(len(email.message_id) > 0)

    def test_empty_dict_does_not_raise(self):
        email = normalize({})
        self.assertIsNotNone(email)


class TestHeaders(unittest.TestCase):

    def test_known_headers_extracted(self):
        data = raw(headers={"To": "bob@example.com", "CC": "carol@example.com",
                            "Date": "Wed, 14 Oct 2026 09:00:00 +0000"})
        email = normalize(data)
        self.assertIn("to", email.headers)
        self.assertIn("cc", email.headers)
        self.assertIn("date", email.headers)

    def test_unknown_headers_excluded(self):
        data = raw(headers={"X-Custom-Header": "secret", "To": "bob@example.com"})
        email = normalize(data)
        self.assertNotIn("x-custom-header", email.headers)
        self.assertIn("to", email.headers)

    def test_non_dict_headers_ignored(self):
        data = raw(headers="not-a-dict")
        email = normalize(data)
        self.assertEqual(email.headers, {})

    def test_missing_headers_returns_empty_dict(self):
        data = raw()
        data.pop("headers", None)
        email = normalize(data)
        self.assertEqual(email.headers, {})


if __name__ == "__main__":
    unittest.main()
