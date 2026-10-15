import unittest

from app.policy.decision_policy import (
    Actionability,
    DeadlineState,
    PermittedAction,
    Priority,
    decide,
)


class DecisionPolicyTests(unittest.TestCase):

    # ── P0: always create task, regardless of deadline ────────────────────

    def test_p0_clear_deadline_creates_task(self):
        result = decide("P0", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.CREATE_TASK_AND_NOTIFY)

    def test_p0_no_deadline_still_creates_task(self):
        result = decide("P0", "YES", "NONE")
        self.assertEqual(result.action, PermittedAction.CREATE_TASK_AND_NOTIFY)

    def test_p0_ambiguous_deadline_still_creates_task(self):
        result = decide("P0", "YES", "AMBIGUOUS")
        self.assertEqual(result.action, PermittedAction.CREATE_TASK_AND_NOTIFY)

    def test_p0_calendar_not_auto_allowed(self):
        result = decide("P0", "YES", "CLEAR")
        self.assertFalse(result.calendar_allowed)

    # ── P1: ask confirmation only when deadline is CLEAR ─────────────────

    def test_p1_clear_deadline_asks_confirmation(self):
        result = decide("P1", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.ASK_CONFIRMATION)

    def test_p1_no_deadline_is_summary_only(self):
        result = decide("P1", "YES", "NONE")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    def test_p1_ambiguous_deadline_is_summary_only(self):
        result = decide("P1", "YES", "AMBIGUOUS")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    # ── P2: ask confirmation only when deadline is CLEAR ─────────────────

    def test_p2_clear_deadline_asks_confirmation(self):
        result = decide("P2", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.ASK_CONFIRMATION)

    def test_p2_no_deadline_is_summary_only(self):
        result = decide("P2", "YES", "NONE")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    def test_p2_ambiguous_deadline_is_summary_only(self):
        result = decide("P2", "YES", "AMBIGUOUS")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    # ── OTHER and non-actionable ──────────────────────────────────────────

    def test_other_is_summary_only(self):
        result = decide("OTHER", "YES", "NONE")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    def test_other_with_deadline_is_still_summary_only(self):
        result = decide("OTHER", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    def test_non_actionable_is_summary_only(self):
        result = decide("P1", "NO", "CLEAR")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    # ── Invalid / REVIEW ─────────────────────────────────────────────────

    def test_review_actionability_requires_review(self):
        result = decide("P1", "REVIEW", "CLEAR")
        self.assertEqual(result.action, PermittedAction.REVIEW_CLASSIFICATION)

    def test_invalid_priority_requires_review(self):
        result = decide("P3", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.REVIEW_CLASSIFICATION)

    def test_invalid_deadline_state_requires_review(self):
        result = decide("P1", "YES", "UNKNOWN")
        self.assertEqual(result.action, PermittedAction.REVIEW_CLASSIFICATION)

    def test_accepts_enum_instances(self):
        result = decide(Priority.P0, Actionability.YES, DeadlineState.NONE)
        self.assertEqual(result.action, PermittedAction.CREATE_TASK_AND_NOTIFY)


if __name__ == "__main__":
    unittest.main()
