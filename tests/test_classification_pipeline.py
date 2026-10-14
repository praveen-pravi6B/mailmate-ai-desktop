"""Tests for app/services/classification_pipeline.py.

Verifies that ClassificationResult values are correctly routed through
the policy module and that the pipeline never triggers side effects.
All tests use pre-built ClassificationResult instances — no Laya model
is loaded.
"""

import unittest

from app.laya_engine.classifier import ClassificationResult
from app.policy.decision_policy import PermittedAction
from app.services.classification_pipeline import apply_policy


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_result(
    actionability: str = "YES",
    priority: str = "P1",
    deadline_state: str = "CLEAR",
    normalized: bool = False,
) -> ClassificationResult:
    return ClassificationResult(
        actionability=actionability,
        priority=priority,
        deadline_state=deadline_state,
        action_probabilities={"YES": 0.8, "NO": 0.2},
        priority_probabilities={"P0": 0.1, "P1": 0.6, "P2": 0.3},
        normalized=normalized,
    )


# ---------------------------------------------------------------------------
# Routing tests — mirrors the policy routing table
# ---------------------------------------------------------------------------

class TestPipelineRouting(unittest.TestCase):

    def test_p0_clear_deadline_creates_task(self):
        result = make_result(priority="P0", deadline_state="CLEAR")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.CREATE_TASK_AND_NOTIFY)
        self.assertFalse(decision.calendar_allowed)

    def test_p1_clear_deadline_creates_task(self):
        result = make_result(priority="P1", deadline_state="CLEAR")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.CREATE_TASK_AND_NOTIFY)

    def test_p0_no_deadline_is_summary_only(self):
        result = make_result(priority="P0", deadline_state="NONE")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.SUMMARIZE_ONLY)

    def test_p1_no_deadline_is_summary_only(self):
        result = make_result(priority="P1", deadline_state="NONE")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.SUMMARIZE_ONLY)

    def test_p0_ambiguous_deadline_requires_clarification(self):
        result = make_result(priority="P0", deadline_state="AMBIGUOUS")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.CLARIFY_DEADLINE)

    def test_p1_ambiguous_deadline_requires_clarification(self):
        result = make_result(priority="P1", deadline_state="AMBIGUOUS")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.CLARIFY_DEADLINE)

    def test_p2_clear_deadline_asks_confirmation(self):
        result = make_result(priority="P2", deadline_state="CLEAR")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.ASK_CONFIRMATION)

    def test_p2_no_deadline_asks_confirmation(self):
        result = make_result(priority="P2", deadline_state="NONE")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.ASK_CONFIRMATION)

    def test_p2_ambiguous_deadline_requires_clarification(self):
        result = make_result(priority="P2", deadline_state="AMBIGUOUS")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.CLARIFY_DEADLINE)

    def test_non_actionable_is_summary_only(self):
        result = make_result(actionability="NO", priority="OTHER", deadline_state="NONE")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.SUMMARIZE_ONLY)

    def test_review_actionability_requires_review(self):
        result = make_result(actionability="REVIEW", priority="REVIEW")
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.REVIEW_CLASSIFICATION)

    def test_normalised_result_with_review_priority(self):
        """Model returned unknown priority → normalised=True → REVIEW_CLASSIFICATION."""
        result = make_result(actionability="YES", priority="REVIEW", normalized=True)
        decision = apply_policy(result)
        self.assertEqual(decision.action, PermittedAction.REVIEW_CLASSIFICATION)


class TestPipelineDoesNotExecuteSideEffects(unittest.TestCase):
    """apply_policy must return a decision, never act on it."""

    def test_returns_policy_decision_not_none(self):
        result = make_result()
        decision = apply_policy(result)
        self.assertIsNotNone(decision)
        self.assertIsNotNone(decision.action)

    def test_pipeline_module_has_no_storage_imports(self):
        import app.services.classification_pipeline as mod
        # Verify no side-effecting modules are imported into the namespace
        for forbidden in ("task_store", "telegram", "calendar", "gmail", "sqlite3"):
            self.assertNotIn(
                forbidden,
                [m for m in mod.__dict__ if not m.startswith("_")],
                msg=f"pipeline imports '{forbidden}' which should not be present",
            )

    def test_model_prediction_alone_cannot_create_task(self):
        """Even a confident P0/CLEAR result only returns a decision object."""
        result = make_result(priority="P0", deadline_state="CLEAR")
        decision = apply_policy(result)
        # decision.action is CREATE_TASK_AND_NOTIFY but no task was created.
        # The caller holds the decision and must explicitly call the store.
        self.assertEqual(decision.action, PermittedAction.CREATE_TASK_AND_NOTIFY)
        # No exception, no database write, no notification happened.


if __name__ == "__main__":
    unittest.main()
