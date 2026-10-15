"""Tests for app/services/notifier.py.

Uses MockTransport throughout — no real HTTP calls are made.
"""

import unittest

from app.integrations.telegram import MockTransport
from app.models.task import TaskCandidate
from app.services.notifier import (
    CONFIRM_PREFIX,
    DECLINE_PREFIX,
    build_confirmation_buttons,
    build_confirmation_request_message,
    build_task_created_message,
    confirmation_callback_data,
    notify,
)
from app.services.orchestrator import (
    AWAITING_CONFIRMATION,
    DRY_RUN,
    NEEDS_REVIEW,
    NO_ACTION,
    OrchestrationResult,
    SKIPPED,
    TASK_CREATED,
)
from app.policy.decision_policy import PermittedAction, PolicyDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_candidate(
    priority: str = "P1",
    title: str = "Fix login bug",
    description: str = "Login fails for SSO users.",
    deadline: str = "2026-10-20",
    deadline_state: str = "CLEAR",
) -> TaskCandidate:
    return TaskCandidate(
        email_id="email-001",
        title=title,
        description=description,
        priority=priority,
        deadline=deadline,
        deadline_state=deadline_state,
        evidence='Explicit date: "2026-10-20"',
    )


def make_result(
    status: str = TASK_CREATED,
    notify_flag: bool = True,
    task_id: str = "task-uuid-001",
    candidate: TaskCandidate = None,
) -> OrchestrationResult:
    decision = PolicyDecision(
        action=PermittedAction.CREATE_TASK_AND_NOTIFY,
        reason="High-priority task with a clear deadline.",
    )
    return OrchestrationResult(
        status=status,
        email_id="email-001",
        decision=decision,
        candidate=candidate or make_candidate(),
        task_id=task_id,
        notify=notify_flag,
    )


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

class TestTaskCreatedMessage(unittest.TestCase):

    def test_contains_priority(self):
        msg = build_task_created_message(make_candidate(priority="P0"))
        self.assertIn("P0", msg)

    def test_contains_title(self):
        msg = build_task_created_message(make_candidate(title="Deploy hotfix"))
        self.assertIn("Deploy hotfix", msg)

    def test_contains_deadline_when_set(self):
        msg = build_task_created_message(make_candidate(deadline="2026-10-20"))
        self.assertIn("2026-10-20", msg)

    def test_no_deadline_shows_fallback(self):
        msg = build_task_created_message(make_candidate(deadline=None))
        self.assertIn("not specified", msg)

    def test_description_included(self):
        msg = build_task_created_message(make_candidate(description="Fix urgently."))
        self.assertIn("Fix urgently.", msg)

    def test_description_truncated_at_300(self):
        long_desc = "A" * 400
        msg = build_task_created_message(make_candidate(description=long_desc))
        self.assertLessEqual(len(msg), 700)


class TestConfirmationRequestMessage(unittest.TestCase):

    def test_contains_title(self):
        msg = build_confirmation_request_message(make_candidate(title="Review doc"))
        self.assertIn("Review doc", msg)

    def test_contains_priority(self):
        msg = build_confirmation_request_message(make_candidate(priority="P2"))
        self.assertIn("P2", msg)

    def test_contains_confirmation_prompt(self):
        msg = build_confirmation_request_message(make_candidate())
        self.assertIn("create this task", msg.lower())


class TestConfirmationButtons(unittest.TestCase):

    def test_two_buttons_in_one_row(self):
        buttons = build_confirmation_buttons("task-123")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(len(buttons[0]), 2)

    def test_confirm_button_callback_data(self):
        buttons = build_confirmation_buttons("task-abc")
        confirm_btn = buttons[0][0]
        self.assertTrue(confirm_btn.callback_data.startswith(CONFIRM_PREFIX))
        self.assertIn("task-abc", confirm_btn.callback_data)

    def test_decline_button_callback_data(self):
        buttons = build_confirmation_buttons("task-abc")
        decline_btn = buttons[0][1]
        self.assertTrue(decline_btn.callback_data.startswith(DECLINE_PREFIX))
        self.assertIn("task-abc", decline_btn.callback_data)

    def test_callback_data_round_trip(self):
        task_id = "uuid-xyz-789"
        confirm_data, decline_data = confirmation_callback_data(task_id)
        self.assertEqual(confirm_data, f"{CONFIRM_PREFIX}{task_id}")
        self.assertEqual(decline_data, f"{DECLINE_PREFIX}{task_id}")


# ---------------------------------------------------------------------------
# notify() dispatch
# ---------------------------------------------------------------------------

class TestNotifyDispatch(unittest.TestCase):

    def test_task_created_sends_message(self):
        transport = MockTransport()
        result = make_result(status=TASK_CREATED, notify_flag=True)
        ok = notify(result, transport)
        self.assertTrue(ok)
        self.assertEqual(transport.message_count, 1)

    def test_task_created_message_contains_priority(self):
        transport = MockTransport()
        result = make_result(status=TASK_CREATED,
                             candidate=make_candidate(priority="P0"))
        notify(result, transport)
        self.assertIn("P0", transport.last_message.text)

    def test_awaiting_confirmation_sends_message_with_buttons(self):
        transport = MockTransport()
        result = make_result(status=AWAITING_CONFIRMATION, notify_flag=True,
                             task_id="task-p2")
        ok = notify(result, transport)
        self.assertTrue(ok)
        self.assertEqual(transport.message_count, 1)
        self.assertTrue(len(transport.last_message.buttons) > 0)

    def test_awaiting_confirmation_buttons_contain_task_id(self):
        transport = MockTransport()
        result = make_result(status=AWAITING_CONFIRMATION, notify_flag=True,
                             task_id="task-p2-xyz")
        notify(result, transport)
        all_cb = [
            btn.callback_data
            for row in transport.last_message.buttons
            for btn in row
        ]
        self.assertTrue(any("task-p2-xyz" in cb for cb in all_cb))

    def test_notify_false_sends_nothing(self):
        transport = MockTransport()
        result = make_result(notify_flag=False)
        ok = notify(result, transport)
        self.assertFalse(ok)
        self.assertEqual(transport.message_count, 0)

    def test_no_action_sends_nothing(self):
        transport = MockTransport()
        result = OrchestrationResult(
            status=NO_ACTION, email_id="e1",
            candidate=make_candidate(), notify=False,
        )
        ok = notify(result, transport)
        self.assertFalse(ok)
        self.assertEqual(transport.message_count, 0)

    def test_dry_run_sends_nothing(self):
        transport = MockTransport()
        result = OrchestrationResult(
            status=DRY_RUN, email_id="e1",
            candidate=make_candidate(), notify=False,
        )
        ok = notify(result, transport)
        self.assertFalse(ok)
        self.assertEqual(transport.message_count, 0)

    def test_skipped_sends_nothing(self):
        transport = MockTransport()
        result = OrchestrationResult(
            status=SKIPPED, email_id="e1", notify=False,
        )
        ok = notify(result, transport)
        self.assertFalse(ok)

    def test_transport_failure_returns_false(self):
        transport = MockTransport()
        transport.fail_next = True
        result = make_result(status=TASK_CREATED, notify_flag=True)
        ok = notify(result, transport)
        self.assertFalse(ok)
        self.assertEqual(transport.message_count, 0)

    def test_awaiting_confirmation_missing_task_id_returns_false(self):
        transport = MockTransport()
        result = OrchestrationResult(
            status=AWAITING_CONFIRMATION, email_id="e1",
            candidate=make_candidate(), task_id=None, notify=True,
        )
        ok = notify(result, transport)
        self.assertFalse(ok)
        self.assertEqual(transport.message_count, 0)

    def test_notify_true_missing_candidate_returns_false(self):
        transport = MockTransport()
        result = OrchestrationResult(
            status=TASK_CREATED, email_id="e1",
            candidate=None, task_id="t1", notify=True,
        )
        ok = notify(result, transport)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
