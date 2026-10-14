"""Tests for app/services/task_extractor.py.

Covers deadline detection (NONE / CLEAR / AMBIGUOUS), title/description
extraction, and the guarantee that no date is ever invented.

No Laya model is loaded.  ClassificationResult instances are built
directly with known values.
"""

import unittest

from app.laya_engine.classifier import ClassificationResult
from app.models.email import NormalizedEmail
from app.services.task_extractor import extract_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_email(body: str, subject: str = "Fix login bug",
               message_id: str = "email-001") -> NormalizedEmail:
    return NormalizedEmail(
        provider="test",
        message_id=message_id,
        thread_id=None,
        sender="alice@example.com",
        subject=subject,
        body=body,
        received_at="2026-10-14T09:00:00+00:00",
    )


def make_classification(priority: str = "P1",
                        actionability: str = "YES",
                        deadline_state: str = "CLEAR") -> ClassificationResult:
    return ClassificationResult(
        actionability=actionability,
        priority=priority,
        deadline_state=deadline_state,
        action_probabilities={"YES": 0.8, "NO": 0.2},
        priority_probabilities={"P0": 0.1, "P1": 0.6, "P2": 0.3},
    )


# ---------------------------------------------------------------------------
# Deadline state: NONE
# ---------------------------------------------------------------------------

class TestDeadlineNone(unittest.TestCase):

    def test_no_date_gives_none_state(self):
        email = make_email("Please review the attached document.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "NONE")
        self.assertIsNone(result.deadline)

    def test_purely_informational_body(self):
        email = make_email("Here is this week's company newsletter.")
        result = extract_task(email, make_classification(priority="P2"))
        self.assertEqual(result.deadline_state, "NONE")

    def test_empty_body_gives_none(self):
        email = make_email("")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "NONE")
        self.assertIsNone(result.deadline)


# ---------------------------------------------------------------------------
# Deadline state: CLEAR
# ---------------------------------------------------------------------------

class TestDeadlineClear(unittest.TestCase):

    def test_iso_date_gives_clear(self):
        email = make_email("Please pay the invoice by 2026-10-20.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "CLEAR")
        self.assertEqual(result.deadline, "2026-10-20")

    def test_written_month_day_gives_clear(self):
        email = make_email("Please submit the report by October 25.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "CLEAR")
        self.assertIsNone(result.deadline)      # non-ISO date; value not parsed
        self.assertIn("October 25", result.evidence)

    def test_written_day_month_gives_clear(self):
        email = make_email("Deadline is 25 October 2026.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "CLEAR")

    def test_slash_date_with_year_gives_clear(self):
        email = make_email("Complete by 10/20/2026.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "CLEAR")

    def test_iso_date_stored_in_deadline_field(self):
        email = make_email("Submit expense report by 2026-11-01.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline, "2026-11-01")

    def test_evidence_quotes_found_date(self):
        email = make_email("Fix the bug by 2026-10-15.")
        result = extract_task(email, make_classification())
        self.assertIn("2026-10-15", result.evidence)


# ---------------------------------------------------------------------------
# Deadline state: AMBIGUOUS
# ---------------------------------------------------------------------------

class TestDeadlineAmbiguous(unittest.TestCase):

    def test_tomorrow_gives_ambiguous(self):
        email = make_email("Please fix the login bug by tomorrow.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")
        self.assertIsNone(result.deadline)

    def test_next_friday_gives_ambiguous(self):
        email = make_email("Send me the report by next Friday.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_asap_gives_ambiguous(self):
        email = make_email("Please respond ASAP.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_end_of_week_gives_ambiguous(self):
        email = make_email("Finish this by end of week.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_eod_gives_ambiguous(self):
        email = make_email("Please submit by EOD.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_soon_gives_ambiguous(self):
        email = make_email("We need this done soon.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_immediately_gives_ambiguous(self):
        email = make_email("Fix this immediately.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_multiple_conflicting_dates_gives_ambiguous(self):
        email = make_email(
            "The first deadline is 2026-10-15 but the revised date is 2026-11-01."
        )
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")
        self.assertIsNone(result.deadline)

    def test_relative_alongside_explicit_date_gives_ambiguous(self):
        # "by Friday 2026-10-17" — explicit date AND relative term together
        email = make_email("Please pay by Friday 2026-10-17.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_in_n_days_gives_ambiguous(self):
        email = make_email("Please complete within 3 days.")
        result = extract_task(email, make_classification())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_ambiguous_deadline_never_has_deadline_value(self):
        for body in [
            "Done by tomorrow.",
            "Finish next week.",
            "ASAP please.",
            "Two conflicting dates: 2026-10-10 and 2026-11-10.",
        ]:
            with self.subTest(body=body):
                result = extract_task(make_email(body), make_classification())
                if result.deadline_state == "AMBIGUOUS":
                    self.assertIsNone(result.deadline)


# ---------------------------------------------------------------------------
# Title and description extraction
# ---------------------------------------------------------------------------

class TestTitleExtraction(unittest.TestCase):

    def test_title_from_subject(self):
        email = make_email("Please fix ASAP.", subject="Fix login bug")
        result = extract_task(email, make_classification())
        self.assertEqual(result.title, "Fix login bug")

    def test_reply_prefix_stripped(self):
        email = make_email("See my earlier note.", subject="Re: Fix login bug")
        result = extract_task(email, make_classification())
        self.assertEqual(result.title, "Fix login bug")

    def test_forward_prefix_stripped(self):
        email = make_email("FYI.", subject="Fwd: Important update")
        result = extract_task(email, make_classification())
        self.assertEqual(result.title, "Important update")

    def test_title_truncated_at_120_chars(self):
        long_subject = "A" * 200
        email = make_email("body", subject=long_subject)
        result = extract_task(email, make_classification())
        self.assertLessEqual(len(result.title), 120)


class TestDescriptionExtraction(unittest.TestCase):

    def test_short_body_used_as_description(self):
        body = "Please review this document when you have time."
        email = make_email(body)
        result = extract_task(email, make_classification())
        self.assertEqual(result.description, body)

    def test_long_body_truncated(self):
        body = "A" * 600
        email = make_email(body)
        result = extract_task(email, make_classification())
        self.assertLessEqual(len(result.description), 510)  # 500 + ellipsis

    def test_empty_body_gives_empty_description(self):
        email = make_email("")
        result = extract_task(email, make_classification())
        self.assertEqual(result.description, "")


# ---------------------------------------------------------------------------
# Core invariants
# ---------------------------------------------------------------------------

class TestCoreInvariants(unittest.TestCase):

    def test_email_id_propagated(self):
        email = make_email("body", message_id="source-email-xyz")
        result = extract_task(email, make_classification())
        self.assertEqual(result.email_id, "source-email-xyz")

    def test_priority_from_classification(self):
        email = make_email("Critical production outage.")
        result = extract_task(email, make_classification(priority="P0"))
        self.assertEqual(result.priority, "P0")

    def test_deadline_state_from_body_not_classification(self):
        """deadline_state on ClassificationResult is ignored; body is authoritative."""
        email = make_email("No date mentioned here.")
        # Classification says CLEAR, but body has no date.
        classification = make_classification(deadline_state="CLEAR")
        result = extract_task(email, classification)
        self.assertEqual(result.deadline_state, "NONE")

    def test_evidence_always_present(self):
        for body in [
            "No date.",
            "Due 2026-10-20.",
            "Fix this tomorrow.",
        ]:
            result = extract_task(make_email(body), make_classification())
            self.assertIsInstance(result.evidence, str)
            self.assertTrue(len(result.evidence) > 0)

    def test_result_is_immutable(self):
        email = make_email("body")
        result = extract_task(email, make_classification())
        with self.assertRaises((TypeError, AttributeError)):
            result.priority = "P0"  # type: ignore[misc]

    def test_extractor_has_no_storage_imports(self):
        import app.services.task_extractor as mod
        for forbidden in ("task_store", "telegram", "gmail", "sqlite3"):
            self.assertNotIn(
                forbidden,
                [m for m in mod.__dict__ if not m.startswith("_")],
            )


if __name__ == "__main__":
    unittest.main()
