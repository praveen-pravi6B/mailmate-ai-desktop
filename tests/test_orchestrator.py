"""Tests for app/services/orchestrator.py.

The Laya agent and TaskStore are mocked throughout.  No model is loaded
and no real database is written.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.models.email import NormalizedEmail
from app.services.orchestrator import (
    AWAITING_CONFIRMATION,
    DRY_RUN,
    ERROR,
    NEEDS_REVIEW,
    NO_ACTION,
    SKIPPED,
    TASK_CREATED,
    process_email,
)
from app.storage.task_store import (
    ConfirmationStatus,
    DuplicateEmailError,
    Task,
    TaskStatus,
    TaskStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_email(
    message_id: str = "email-001",
    subject: str = "Fix login bug",
    body: str = "Please fix the login bug by 2026-10-20.",
) -> NormalizedEmail:
    return NormalizedEmail(
        provider="test",
        message_id=message_id,
        thread_id=None,
        sender="alice@example.com",
        subject=subject,
        body=body,
        received_at="2026-10-14T09:00:00+00:00",
    )


def make_laya_agent(actionability: str = "YES", priority: str = "P1") -> MagicMock:
    """Mock Laya agent returning fixed choices."""

    def system_one(state, questions):
        key = next(iter(questions))
        if key == "action_required":
            return {"answers": {"action_required": {
                "choice": actionability,
                "probabilities": {"YES": 0.8, "NO": 0.2},
            }}}
        return {"answers": {"priority": {
            "choice": priority,
            "probabilities": {"P0": 0.1, "P1": 0.6, "P2": 0.3},
        }}}

    agent = MagicMock()
    agent.system_one.side_effect = system_one
    return agent


def make_task(task_id: str = "task-uuid-001",
              source_email_id: str = "email-001") -> Task:
    return Task(
        id=task_id,
        source_email_id=source_email_id,
        title="Fix login bug",
        description="Fix the login bug.",
        priority="P1",
        deadline="2026-10-20",
        deadline_state="CLEAR",
        status=TaskStatus.PENDING,
        confirmation_status=ConfirmationStatus.NOT_REQUIRED,
        calendar_event_id=None,
        created_at="2026-10-14T09:00:00+00:00",
        updated_at="2026-10-14T09:00:00+00:00",
    )


def make_store(
    already_has_task: bool = False,
    create_raises=None,
) -> MagicMock:
    store = MagicMock(spec=TaskStore)
    store.email_has_task.return_value = already_has_task
    if create_raises:
        store.create_task.side_effect = create_raises
    else:
        store.create_task.return_value = make_task()
    return store


# ---------------------------------------------------------------------------
# Dry-run mode (default)
# ---------------------------------------------------------------------------

class TestDryRun(unittest.TestCase):

    def test_dry_run_returns_dry_run_status(self):
        result = process_email(make_email(), make_laya_agent(), make_store())
        self.assertEqual(result.status, DRY_RUN)

    def test_dry_run_does_not_write_to_store(self):
        store = make_store()
        process_email(make_email(), make_laya_agent(), make_store())
        store.create_task.assert_not_called()

    def test_dry_run_notify_is_false(self):
        result = process_email(make_email(), make_laya_agent(), make_store())
        self.assertFalse(result.notify)

    def test_dry_run_populates_decision_and_candidate(self):
        result = process_email(make_email(), make_laya_agent(), make_store())
        self.assertIsNotNone(result.decision)
        self.assertIsNotNone(result.candidate)

    def test_dry_run_skips_already_processed(self):
        store = make_store(already_has_task=True)
        result = process_email(make_email(), make_laya_agent(), store)
        self.assertEqual(result.status, SKIPPED)
        store.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency(unittest.TestCase):

    def test_already_processed_returns_skipped(self):
        store = make_store(already_has_task=True)
        result = process_email(make_email(), make_laya_agent(), store,
                               dry_run=False)
        self.assertEqual(result.status, SKIPPED)

    def test_already_processed_does_not_classify(self):
        store = make_store(already_has_task=True)
        agent = make_laya_agent()
        process_email(make_email(), agent, store, dry_run=False)
        agent.system_one.assert_not_called()

    def test_already_processed_does_not_create_task(self):
        store = make_store(already_has_task=True)
        process_email(make_email(), make_laya_agent(), store, dry_run=False)
        store.create_task.assert_not_called()

    def test_duplicate_race_during_insert_returns_skipped(self):
        store = make_store(create_raises=DuplicateEmailError("dup"))
        result = process_email(make_email(), make_laya_agent("YES", "P1"), store,
                               dry_run=False)
        self.assertEqual(result.status, SKIPPED)
        self.assertFalse(result.notify)


# ---------------------------------------------------------------------------
# Live mode — P0 / P1 (CREATE_TASK_AND_NOTIFY)
# ---------------------------------------------------------------------------

class TestTaskCreation(unittest.TestCase):

    def _run(self, actionability="YES", priority="P1",
             body="Fix by 2026-10-20.", **kwargs):
        email = make_email(body=body, **kwargs)
        agent = make_laya_agent(actionability, priority)
        store = make_store()
        return process_email(email, agent, store, dry_run=False), store

    def test_p1_clear_deadline_asks_confirmation(self):
        result, _ = self._run(priority="P1")
        self.assertEqual(result.status, AWAITING_CONFIRMATION)

    def test_p0_clear_deadline_creates_task(self):
        result, _ = self._run(priority="P0")
        self.assertEqual(result.status, TASK_CREATED)

    def test_p0_no_deadline_still_creates_task(self):
        result, _ = self._run(priority="P0", body="Server is down.")
        self.assertEqual(result.status, TASK_CREATED)

    def test_task_created_notify_is_true(self):
        result, _ = self._run(priority="P0")
        self.assertTrue(result.notify)

    def test_task_created_task_id_set(self):
        result, _ = self._run(priority="P0")
        self.assertIsNotNone(result.task_id)

    def test_store_create_task_called_with_correct_priority(self):
        _, store = self._run(priority="P0")
        call_kwargs = store.create_task.call_args.kwargs
        self.assertEqual(call_kwargs["priority"], "P0")

    def test_store_create_task_called_with_correct_email_id(self):
        email = make_email(message_id="email-xyz", body="Due 2026-10-20.")
        agent = make_laya_agent("YES", "P1")
        store = make_store()
        process_email(email, agent, store, dry_run=False)
        call_kwargs = store.create_task.call_args.kwargs
        self.assertEqual(call_kwargs["source_email_id"], "email-xyz")

    def test_p1_no_deadline_is_no_action(self):
        result, store = self._run(priority="P1", body="Please fix the login bug.")
        self.assertEqual(result.status, NO_ACTION)
        store.create_task.assert_not_called()

    def test_p0_ambiguous_deadline_still_creates_task(self):
        result, store = self._run(priority="P0", body="Fix this ASAP.")
        self.assertEqual(result.status, TASK_CREATED)


# ---------------------------------------------------------------------------
# Live mode — P2 (ASK_CONFIRMATION)
# ---------------------------------------------------------------------------

class TestP2Confirmation(unittest.TestCase):

    def test_p2_clear_deadline_returns_awaiting_confirmation(self):
        email = make_email(body="Review this document by 2026-10-25.")
        agent = make_laya_agent("YES", "P2")
        store = make_store()
        result = process_email(email, agent, store, dry_run=False)
        self.assertEqual(result.status, AWAITING_CONFIRMATION)

    def test_p2_clear_deadline_notify_is_true(self):
        email = make_email(body="Review this by 2026-10-25.")
        agent = make_laya_agent("YES", "P2")
        store = make_store()
        result = process_email(email, agent, store, dry_run=False)
        self.assertTrue(result.notify)

    def test_p2_task_created_with_pending_confirmation(self):
        email = make_email(body="Review this document by 2026-10-25.")
        agent = make_laya_agent("YES", "P2")
        store = make_store()
        process_email(email, agent, store, dry_run=False)
        call_kwargs = store.create_task.call_args.kwargs
        self.assertEqual(call_kwargs["confirmation_status"],
                         ConfirmationStatus.PENDING)

    def test_p2_no_deadline_is_no_action(self):
        email = make_email(body="Review this ASAP.")
        agent = make_laya_agent("YES", "P2")
        store = make_store()
        result = process_email(email, agent, store, dry_run=False)
        self.assertEqual(result.status, NO_ACTION)
        store.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Non-actionable and REVIEW
# ---------------------------------------------------------------------------

class TestNonActionable(unittest.TestCase):

    def test_non_actionable_is_no_action(self):
        email = make_email(body="Here is the weekly newsletter.")
        agent = make_laya_agent("NO", "OTHER")
        store = make_store()
        result = process_email(email, agent, store, dry_run=False)
        self.assertEqual(result.status, NO_ACTION)
        store.create_task.assert_not_called()
        self.assertFalse(result.notify)

    def test_review_actionability_needs_review(self):
        email = make_email(body="Something unclear.")
        agent = make_laya_agent("MAYBE", "UNKNOWN")  # normalised to REVIEW
        store = make_store()
        result = process_email(email, agent, store, dry_run=False)
        self.assertEqual(result.status, NEEDS_REVIEW)
        store.create_task.assert_not_called()
        self.assertFalse(result.notify)


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling(unittest.TestCase):

    def test_store_check_failure_returns_error(self):
        store = MagicMock(spec=TaskStore)
        store.email_has_task.side_effect = RuntimeError("db gone")
        result = process_email(make_email(), make_laya_agent(), store,
                               dry_run=False)
        self.assertEqual(result.status, ERROR)
        self.assertFalse(result.notify)

    def test_classification_failure_returns_error(self):
        agent = MagicMock()
        agent.system_one.side_effect = RuntimeError("model crash")
        store = make_store()
        result = process_email(make_email(), agent, store, dry_run=False)
        self.assertEqual(result.status, ERROR)
        self.assertFalse(result.notify)
        store.create_task.assert_not_called()

    def test_store_create_failure_returns_error(self):
        store = make_store(create_raises=RuntimeError("disk full"))
        result = process_email(
            make_email(), make_laya_agent("YES", "P1"), store, dry_run=False
        )
        self.assertEqual(result.status, ERROR)
        self.assertFalse(result.notify)

    def test_notify_never_true_on_error(self):
        store = MagicMock(spec=TaskStore)
        store.email_has_task.side_effect = Exception("oops")
        result = process_email(make_email(), make_laya_agent(), store)
        self.assertFalse(result.notify)


# ---------------------------------------------------------------------------
# Notification guard
# ---------------------------------------------------------------------------

class TestNotifyGuard(unittest.TestCase):
    """notify=True must only be set after a confirmed DB write."""

    def test_notify_false_on_no_action(self):
        email = make_email(body="Newsletter content.")
        result = process_email(email, make_laya_agent("NO"), make_store(),
                               dry_run=False)
        self.assertFalse(result.notify)

    def test_notify_false_on_needs_review(self):
        result = process_email(
            make_email(), make_laya_agent("GARBAGE", "GARBAGE"), make_store(),
            dry_run=False,
        )
        self.assertFalse(result.notify)

    def test_notify_false_in_dry_run_even_for_p0(self):
        email = make_email(body="Outage now fix 2026-10-20.")
        result = process_email(email, make_laya_agent("YES", "P0"), make_store(),
                               dry_run=True)
        self.assertFalse(result.notify)


# ---------------------------------------------------------------------------
# Result fields
# ---------------------------------------------------------------------------

class TestResultFields(unittest.TestCase):

    def test_email_id_always_set(self):
        result = process_email(
            make_email(message_id="my-id"), make_laya_agent(), make_store()
        )
        self.assertEqual(result.email_id, "my-id")

    def test_task_id_none_when_no_task_created(self):
        email = make_email(body="Newsletter.")
        result = process_email(email, make_laya_agent("NO"), make_store(),
                               dry_run=False)
        self.assertIsNone(result.task_id)

    def test_reason_always_non_empty(self):
        for dry in (True, False):
            with self.subTest(dry_run=dry):
                result = process_email(
                    make_email(), make_laya_agent(), make_store(), dry_run=dry
                )
                self.assertTrue(len(result.reason) > 0)


if __name__ == "__main__":
    unittest.main()
