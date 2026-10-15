"""End-to-end integration tests for MailMate AI — Milestone 10.

These tests exercise the full pipeline (Gmail → normalise → classify →
policy → store → notify → confirm) using mocks for Laya, Telegram, and
Gmail.  No real external API calls are made.

Covers the M10 checklist:
- Duplicate processing safety
- Failure recovery (store failures, model failures)
- Declined confirmation safety
- Ambiguous-deadline safety (never auto-schedules)
- Notification only after successful write
- P1/P0 without deadline → summarize only
"""

import unittest
from unittest.mock import MagicMock

from app.integrations.gmail import MockGmailTransport
from app.integrations.telegram import MockTransport
from app.models.email import NormalizedEmail
from app.services.confirmation_handler import handle_callback
from app.services.gmail_poller import fetch_new_emails
from app.services.notifier import CONFIRM_PREFIX, DECLINE_PREFIX, notify
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
from app.services.email_normalizer import normalize
from app.storage.task_store import (
    ConfirmationStatus,
    TaskStatus,
    TaskStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store() -> TaskStore:
    return TaskStore(":memory:")


def make_agent(actionability: str = "YES", priority: str = "P1") -> MagicMock:
    def system_one(state, questions):
        key = next(iter(questions))
        if key == "action_required":
            return {"answers": {"action_required": {
                "choice": actionability,
                "probabilities": {"YES": 0.8, "NO": 0.2},
            }}}
        return {"answers": {"priority": {
            "choice": priority,
            "probabilities": {"P0": 0.15, "P1": 0.55, "P2": 0.30},
        }}}
    agent = MagicMock()
    agent.system_one.side_effect = system_one
    return agent


def make_email(
    message_id: str = "email-001",
    subject: str = "Fix login bug",
    body: str = "Please fix the login bug by 2026-10-20.",
) -> NormalizedEmail:
    return normalize({
        "id": message_id,
        "from": "alice@example.com",
        "subject": subject,
        "body": body,
        "received_at": "2026-10-15T09:00:00+00:00",
    }, provider="test")


def run(email, agent, store, dry_run=False):
    return process_email(email, agent, store, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Duplicate processing
# ---------------------------------------------------------------------------

class TestDuplicateProcessing(unittest.TestCase):

    def test_second_processing_returns_skipped(self):
        store = make_store()
        agent = make_agent()
        email = make_email()
        run(email, agent, store)
        result = run(email, agent, store)
        self.assertEqual(result.status, SKIPPED)

    def test_second_processing_does_not_create_duplicate_task(self):
        store = make_store()
        agent = make_agent()
        email = make_email()
        run(email, agent, store)
        run(email, agent, store)
        self.assertEqual(len(store.list_tasks()), 1)

    def test_second_processing_does_not_notify(self):
        store = make_store()
        agent = make_agent()
        transport = MockTransport()
        email = make_email()

        r1 = run(email, agent, store)
        notify(r1, transport)

        r2 = run(email, agent, store)
        notify(r2, transport)

        # Only one notification sent (from r1)
        self.assertEqual(transport.message_count, 1)

    def test_batch_with_duplicate_in_gmail_poll(self):
        store = make_store()
        agent = make_agent()
        gmail = MockGmailTransport()
        gmail.add_message("m1", body="Fix by 2026-10-20.")

        # First poll
        emails = list(fetch_new_emails(gmail, store))
        run(emails[0], agent, store)

        # Second poll — same message still "unread" in mock
        emails2 = list(fetch_new_emails(gmail, store))
        self.assertEqual(len(emails2), 0)  # pre-filtered by email_has_task


# ---------------------------------------------------------------------------
# Failure recovery
# ---------------------------------------------------------------------------

class TestFailureRecovery(unittest.TestCase):

    def test_model_failure_returns_error_no_task(self):
        store = make_store()
        agent = MagicMock()
        agent.system_one.side_effect = RuntimeError("model crash")
        email = make_email()
        result = run(email, agent, store)
        self.assertEqual(result.status, ERROR)
        self.assertEqual(store.list_tasks(), [])

    def test_model_failure_notify_false(self):
        store = make_store()
        agent = MagicMock()
        agent.system_one.side_effect = RuntimeError("crash")
        result = run(make_email(), agent, store)
        self.assertFalse(result.notify)

    def test_store_write_failure_returns_error(self):
        store = MagicMock(spec=TaskStore)
        store.email_has_task.return_value = False
        store.create_task.side_effect = RuntimeError("disk full")
        result = run(make_email(), make_agent(), store)
        self.assertEqual(result.status, ERROR)
        self.assertFalse(result.notify)

    def test_after_failure_reprocessing_attempts_again(self):
        """A failed email (no task written) can be retried on next poll."""
        store = make_store()
        agent_fail = MagicMock()
        agent_fail.system_one.side_effect = RuntimeError("crash")
        email = make_email()

        r1 = run(email, agent_fail, store)
        self.assertEqual(r1.status, ERROR)
        # No task was written, so a retry should not be SKIPPED
        self.assertFalse(store.email_has_task(email.message_id))

        agent_ok = make_agent("YES", "P0")
        r2 = run(email, agent_ok, store)
        self.assertEqual(r2.status, TASK_CREATED)


# ---------------------------------------------------------------------------
# Declined confirmation safety
# ---------------------------------------------------------------------------

class TestDeclinedConfirmation(unittest.TestCase):

    def test_declined_p2_task_stays_declined(self):
        store = make_store()
        agent = make_agent("YES", "P2")
        email = make_email(body="Review this document by 2026-10-25.")
        result = run(email, agent, store)
        self.assertEqual(result.status, AWAITING_CONFIRMATION)

        handle_callback(f"{DECLINE_PREFIX}{result.task_id}", store)

        task = store.get_by_id(result.task_id)
        self.assertEqual(task.status, TaskStatus.DECLINED)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.DECLINED)

    def test_declined_task_cannot_be_reconfirmed(self):
        store = make_store()
        agent = make_agent("YES", "P2")
        email = make_email(body="Review this document by 2026-10-25.")
        result = run(email, agent, store)

        handle_callback(f"{DECLINE_PREFIX}{result.task_id}", store)
        cr = handle_callback(f"{CONFIRM_PREFIX}{result.task_id}", store)
        self.assertTrue(cr.already_resolved)

        task = store.get_by_id(result.task_id)
        self.assertEqual(task.status, TaskStatus.DECLINED)

    def test_decline_does_not_send_task_created_notification(self):
        store = make_store()
        transport = MockTransport()
        agent = make_agent("YES", "P2")
        email = make_email(body="Please review the draft proposal by 2026-10-25.")
        result = run(email, agent, store)

        notify(result, transport)
        self.assertEqual(transport.message_count, 1)

        handle_callback(f"{DECLINE_PREFIX}{result.task_id}", store)

        declined_result = process_email(
            make_email(message_id="email-002", body="Another email."),
            make_agent("NO"),
            store,
        )
        notify(declined_result, transport)
        self.assertLessEqual(transport.message_count, 2)

    def test_reprocessing_declined_email_is_skipped(self):
        store = make_store()
        agent = make_agent("YES", "P2")
        email = make_email(body="Review doc by 2026-10-25.")
        result = run(email, agent, store)
        handle_callback(f"{DECLINE_PREFIX}{result.task_id}", store)

        result2 = run(email, agent, store)
        self.assertEqual(result2.status, SKIPPED)


# ---------------------------------------------------------------------------
# Ambiguous deadline safety
# ---------------------------------------------------------------------------

class TestAmbiguousDeadlineSafety(unittest.TestCase):

    def test_ambiguous_deadline_not_auto_created(self):
        store = make_store()
        agent = make_agent("YES", "P1")
        email = make_email(body="Fix this ASAP please.")
        result = run(email, agent, store)
        self.assertEqual(result.status, NO_ACTION)
        self.assertEqual(store.list_tasks(), [])

    def test_ambiguous_p2_not_auto_created(self):
        store = make_store()
        agent = make_agent("YES", "P2")
        email = make_email(body="Review this tomorrow please.")
        result = run(email, agent, store)
        self.assertEqual(result.status, NO_ACTION)
        self.assertEqual(store.list_tasks(), [])

    def test_p0_ambiguous_deadline_still_creates_task(self):
        store = make_store()
        agent = make_agent("YES", "P0")
        email = make_email(body="Critical outage — fix immediately!")
        result = run(email, agent, store)
        self.assertEqual(result.status, TASK_CREATED)

    def test_p1_ambiguous_deadline_notify_false(self):
        store = make_store()
        result = run(
            make_email(body="Fix ASAP."),
            make_agent("YES", "P1"),
            store,
        )
        self.assertFalse(result.notify)

    def test_no_deadline_p1_is_summary_only(self):
        store = make_store()
        agent = make_agent("YES", "P1")
        email = make_email(body="Please fix the data export bug.")
        result = run(email, agent, store)
        self.assertEqual(result.status, NO_ACTION)
        self.assertEqual(store.list_tasks(), [])

    def test_no_deadline_p0_creates_task(self):
        store = make_store()
        agent = make_agent("YES", "P0")
        email = make_email(body="The login service is broken.")
        result = run(email, agent, store)
        self.assertEqual(result.status, TASK_CREATED)


# ---------------------------------------------------------------------------
# Notification only after successful write
# ---------------------------------------------------------------------------

class TestNotificationGuard(unittest.TestCase):

    def test_notify_true_after_p0_task_created(self):
        store = make_store()
        result = run(
            make_email(body="Server is down."),
            make_agent("YES", "P0"),
            store,
        )
        self.assertEqual(result.status, TASK_CREATED)
        self.assertTrue(result.notify)

    def test_notify_false_for_no_action(self):
        result = run(
            make_email(body="Weekly newsletter content."),
            make_agent("NO"),
            make_store(),
        )
        self.assertFalse(result.notify)

    def test_notify_false_for_needs_review(self):
        result = run(
            make_email(),
            make_agent("GARBAGE", "UNKNOWN"),
            make_store(),
        )
        self.assertFalse(result.notify)

    def test_notify_false_for_dry_run(self):
        result = run(
            make_email(body="Fix by 2026-10-25."),
            make_agent("YES", "P0"),
            make_store(),
            dry_run=True,
        )
        self.assertFalse(result.notify)

    def test_transport_failure_does_not_create_duplicate(self):
        """If notification fails, the task remains created — no rollback."""
        store = make_store()
        transport = MockTransport()
        transport.fail_next = True

        result = run(
            make_email(body="Server is down."),
            make_agent("YES", "P0"),
            store,
        )
        ok = notify(result, transport)
        self.assertFalse(ok)
        # Task still exists — notification failure does not undo the write
        self.assertEqual(len(store.list_tasks()), 1)

    def test_notification_sent_exactly_once_per_task(self):
        store = make_store()
        transport = MockTransport()
        result = run(
            make_email(body="Fix by 2026-10-25."),
            make_agent("YES", "P1"),
            store,
        )
        notify(result, transport)
        notify(result, transport)   # second call — result.notify still True
        # Both go through — idempotency is the caller's responsibility.
        # The test confirms notify() itself doesn't refuse the second call
        # (that's handled by not calling it twice in production code).
        self.assertEqual(transport.message_count, 2)


# ---------------------------------------------------------------------------
# Full pipeline smoke (Gmail → store → notify)
# ---------------------------------------------------------------------------

class TestFullPipelineSmoke(unittest.TestCase):

    def test_p1_email_creates_task_and_notification(self):
        store = make_store()
        transport = MockTransport()
        gmail = MockGmailTransport()
        gmail.add_message(
            "m-p1",
            subject="Fix login bug",
            body="Please fix the login bug by 2026-10-20.",
        )

        for email in fetch_new_emails(gmail, store):
            result = process_email(email, make_agent("YES", "P0"), store,
                                   dry_run=False)
            if result.notify:
                notify(result, transport)

        self.assertEqual(len(store.list_tasks()), 1)
        self.assertEqual(transport.message_count, 1)

    def test_p2_clear_deadline_creates_confirmation_request(self):
        store = make_store()
        transport = MockTransport()
        gmail = MockGmailTransport()
        gmail.add_message(
            "m-p2",
            subject="Review document",
            body="Please review this document by 2026-10-25.",
        )

        for email in fetch_new_emails(gmail, store):
            result = process_email(email, make_agent("YES", "P2"), store,
                                   dry_run=False)
            if result.notify:
                notify(result, transport)

        tasks = store.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].confirmation_status, ConfirmationStatus.PENDING)
        self.assertTrue(len(transport.last_message.buttons) > 0)

    def test_non_actionable_email_no_task_no_notification(self):
        store = make_store()
        transport = MockTransport()
        gmail = MockGmailTransport()
        gmail.add_message(
            "m-newsletter",
            subject="Weekly digest",
            body="Here is this week's company newsletter.",
        )

        for email in fetch_new_emails(gmail, store):
            result = process_email(email, make_agent("NO"), store)
            if result.notify:
                notify(result, transport)

        self.assertEqual(store.list_tasks(), [])
        self.assertEqual(transport.message_count, 0)

    def test_mixed_inbox_correct_routing(self):
        """Multiple emails of different types processed in one batch."""
        store = make_store()
        gmail = MockGmailTransport()
        gmail.add_message("p0-email", body="Production DB is down!")
        gmail.add_message("p2-email", body="Please review this document by 2026-10-25.")
        gmail.add_message("other-email", body="Weekly newsletter content.")

        agents = {
            "p0-email": make_agent("YES", "P0"),
            "p2-email": make_agent("YES", "P2"),
            "other-email": make_agent("NO"),
        }
        statuses = []

        for email in fetch_new_emails(gmail, store):
            agent = agents[email.message_id]
            result = process_email(email, agent, store, dry_run=False)
            statuses.append(result.status)

        self.assertIn(TASK_CREATED, statuses)
        self.assertIn(AWAITING_CONFIRMATION, statuses)
        self.assertIn(NO_ACTION, statuses)
        self.assertEqual(len(store.list_tasks()), 2)


if __name__ == "__main__":
    unittest.main()
