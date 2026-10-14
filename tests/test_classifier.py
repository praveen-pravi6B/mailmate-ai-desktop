"""Tests for app/laya_engine/classifier.py.

The Laya model is never loaded.  A lightweight mock agent replaces it so
tests run instantly and produce no external side effects.
"""

import unittest
from unittest.mock import MagicMock

from app.laya_engine.classifier import ClassificationResult, classify_email


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent(actionability: str, priority: str | None = None) -> MagicMock:
    """Build a mock Laya agent that returns fixed choices."""

    def system_one(state, questions):
        question_key = next(iter(questions))
        if question_key == "action_required":
            return {
                "answers": {
                    "action_required": {
                        "choice": actionability,
                        "probabilities": {"YES": 0.7, "NO": 0.3},
                    }
                }
            }
        # priority question
        return {
            "answers": {
                "priority": {
                    "choice": priority or "P1",
                    "probabilities": {"P0": 0.1, "P1": 0.6, "P2": 0.3},
                }
            }
        }

    agent = MagicMock()
    agent.system_one.side_effect = system_one
    return agent


def make_email(subject: str = "Test", body: str = "Body text.") -> dict:
    return {"subject": subject, "body": body}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestClassifyActionability(unittest.TestCase):

    def test_yes_actionability_is_passed_through(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertEqual(result.actionability, "YES")
        self.assertFalse(result.normalized)

    def test_no_actionability_is_passed_through(self):
        agent = make_agent("NO")
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.actionability, "NO")
        self.assertEqual(result.priority, "OTHER")

    def test_unknown_actionability_normalises_to_review(self):
        agent = make_agent("MAYBE")
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.actionability, "REVIEW")
        self.assertEqual(result.priority, "REVIEW")
        self.assertTrue(result.normalized)

    def test_empty_actionability_normalises_to_review(self):
        agent = make_agent("")
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.actionability, "REVIEW")
        self.assertTrue(result.normalized)


class TestClassifyPriority(unittest.TestCase):

    def test_p0_priority(self):
        agent = make_agent("YES", "P0")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertEqual(result.priority, "P0")

    def test_p1_priority(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertEqual(result.priority, "P1")

    def test_p2_priority(self):
        agent = make_agent("YES", "P2")
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.priority, "P2")

    def test_unknown_priority_normalises_to_review(self):
        agent = make_agent("YES", "URGENT")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertEqual(result.priority, "REVIEW")
        self.assertTrue(result.normalized)

    def test_priority_not_called_when_not_actionable(self):
        agent = make_agent("NO")
        result = classify_email(agent, make_email(), deadline_state="NONE")
        # system_one should be called exactly once (actionability only)
        self.assertEqual(agent.system_one.call_count, 1)
        self.assertEqual(result.priority, "OTHER")

    def test_priority_called_when_actionable(self):
        agent = make_agent("YES", "P1")
        classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertEqual(agent.system_one.call_count, 2)


class TestDeadlineState(unittest.TestCase):

    def test_clear_deadline_state_preserved(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertEqual(result.deadline_state, "CLEAR")

    def test_none_deadline_state_preserved(self):
        agent = make_agent("YES", "P2")
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.deadline_state, "NONE")

    def test_ambiguous_deadline_state_preserved(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="AMBIGUOUS")
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_unknown_deadline_state_defaults_to_ambiguous(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="BOGUS")
        self.assertEqual(result.deadline_state, "AMBIGUOUS")

    def test_default_deadline_state_is_ambiguous(self):
        """When no deadline_state is supplied, default to AMBIGUOUS (safe)."""
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email())
        self.assertEqual(result.deadline_state, "AMBIGUOUS")


class TestProbabilities(unittest.TestCase):

    def test_action_probabilities_returned(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertIn("YES", result.action_probabilities)
        self.assertIn("NO", result.action_probabilities)

    def test_priority_probabilities_returned_when_actionable(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        self.assertTrue(len(result.priority_probabilities) > 0)

    def test_priority_probabilities_empty_when_not_actionable(self):
        agent = make_agent("NO")
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.priority_probabilities, {})


class TestMalformedAgentResponses(unittest.TestCase):
    """Classifier must not crash or silently pass bad model output."""

    def _agent_returning(self, response):
        agent = MagicMock()
        agent.system_one.return_value = response
        return agent

    def test_missing_answers_key(self):
        agent = self._agent_returning({})
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.actionability, "REVIEW")
        self.assertTrue(result.normalized)

    def test_none_response(self):
        agent = self._agent_returning(None)
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.actionability, "REVIEW")
        self.assertTrue(result.normalized)

    def test_non_string_choice(self):
        agent = self._agent_returning({
            "answers": {"action_required": {"choice": 42, "probabilities": {}}}
        })
        # 42 → "42" → not in VALID_ACTIONABILITY → REVIEW
        result = classify_email(agent, make_email(), deadline_state="NONE")
        self.assertEqual(result.actionability, "REVIEW")
        self.assertTrue(result.normalized)

    def test_result_is_immutable(self):
        agent = make_agent("YES", "P1")
        result = classify_email(agent, make_email(), deadline_state="CLEAR")
        with self.assertRaises((TypeError, AttributeError)):
            result.actionability = "NO"  # type: ignore[misc]


class TestNoSideEffects(unittest.TestCase):
    """Classifier must never touch storage, notifications, or calendar."""

    def test_classify_does_not_import_storage(self):
        import app.laya_engine.classifier as mod
        import importlib
        # Verify no side-effecting modules are imported
        for forbidden in ("task_store", "telegram", "calendar", "gmail"):
            self.assertNotIn(
                forbidden,
                [m for m in mod.__dict__ if not m.startswith("_")],
                msg=f"classifier imports '{forbidden}' which should not be present",
            )


if __name__ == "__main__":
    unittest.main()
