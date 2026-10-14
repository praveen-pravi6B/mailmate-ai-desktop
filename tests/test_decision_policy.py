import unittest

from app.policy.decision_policy import (
    Actionability,
    DeadlineState,
    PermittedAction,
    Priority,
    decide,
)


class DecisionPolicyTests(unittest.TestCase):

    # --- P0 / P1 with CLEAR deadline ---

    def test_p0_with_clear_deadline(self):
        result = decide("P0", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.CREATE_TASK_AND_NOTIFY)
        self.assertFalse(result.calendar_allowed)

    def test_p1_with_clear_deadline(self):
        result = decide("P1", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.CREATE_TASK_AND_NOTIFY)

    # --- P0 / P1 with no deadline (NONE) ---
    # High-priority emails without any deadline are summary-only under current
    # policy. A task must not be auto-created when no deadline is known.

    def test_p0_without_deadline(self):
        result = decide("P0", "YES", "NONE")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    def test_p1_without_deadline(self):
        result = decide("P1", "YES", "NONE")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    # --- P0 / P1 with AMBIGUOUS deadline ---

    def test_p0_with_ambiguous_deadline(self):
        result = decide("P0", "YES", "AMBIGUOUS")
        self.assertEqual(result.action, PermittedAction.CLARIFY_DEADLINE)

    def test_p1_with_ambiguous_deadline(self):
        result = decide("P1", "YES", "AMBIGUOUS")
        self.assertEqual(result.action, PermittedAction.CLARIFY_DEADLINE)

    # --- P2 ---
    # P2 tasks do not require a deadline to proceed to confirmation.

    def test_p2_with_clear_deadline_requires_confirmation(self):
        result = decide("P2", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.ASK_CONFIRMATION)

    def test_p2_without_deadline_requires_confirmation(self):
        result = decide("P2", "YES", "NONE")
        self.assertEqual(result.action, PermittedAction.ASK_CONFIRMATION)

    def test_p2_with_ambiguous_deadline_requires_clarification(self):
        result = decide("P2", "YES", "AMBIGUOUS")
        self.assertEqual(result.action, PermittedAction.CLARIFY_DEADLINE)

    # --- Non-actionable / uncertain ---

    def test_non_actionable_email_is_summary_only(self):
        result = decide("OTHER", "NO", "NONE")
        self.assertEqual(result.action, PermittedAction.SUMMARIZE_ONLY)

    def test_uncertain_actionability_requires_review(self):
        result = decide("P1", "REVIEW", "CLEAR")
        self.assertEqual(result.action, PermittedAction.REVIEW_CLASSIFICATION)

    def test_invalid_priority_requires_review(self):
        result = decide("P3", "YES", "CLEAR")
        self.assertEqual(result.action, PermittedAction.REVIEW_CLASSIFICATION)

    def test_invalid_deadline_state_requires_review(self):
        result = decide("P1", "YES", "UNKNOWN")
        self.assertEqual(result.action, PermittedAction.REVIEW_CLASSIFICATION)

    # --- Enum instances are also accepted ---

    def test_accepts_enum_instances(self):
        result = decide(Priority.P0, Actionability.YES, DeadlineState.CLEAR)
        self.assertEqual(result.action, PermittedAction.CREATE_TASK_AND_NOTIFY)


if __name__ == "__main__":
    unittest.main()
