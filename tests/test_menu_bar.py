"""Tests for app/ui/menu_bar.py.

Tests drive MailMateApp methods directly without starting the macOS
event loop.  No real Gmail, Telegram, or Calendar calls are made.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.integrations.gmail import MockGmailTransport
from app.integrations.telegram import MockTransport
from app.storage.task_store import ConfirmationStatus, TaskStatus, TaskStore
from app.ui.menu_bar import MailMateApp, _priority_label


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store() -> TaskStore:
    return TaskStore(":memory:")


def insert_task(
    store: TaskStore,
    email_id: str = "email-001",
    priority: str = "P1",
    deadline: str = "2026-10-20",
    deadline_state: str = "CLEAR",
    confirmation_status: ConfirmationStatus = ConfirmationStatus.NOT_REQUIRED,
) -> str:
    task = store.create_task(
        source_email_id=email_id,
        title="Fix login bug",
        description="Login fails.",
        priority=priority,
        deadline_state=deadline_state,
        confirmation_status=confirmation_status,
        deadline=deadline,
    )
    return task.id


def make_app(store=None, live_mode=False) -> MailMateApp:
    """Create a MailMateApp without starting the event loop or timer."""
    if store is None:
        store = make_store()
    with patch("rumps.Timer") as mock_timer:
        mock_timer.return_value = MagicMock()
        app = MailMateApp(
            store=store,
            agent=None,
            gmail_transport=None,
            notification_transport=None,
            live_mode=live_mode,
        )
    return app


# ---------------------------------------------------------------------------
# Priority label helper
# ---------------------------------------------------------------------------

class TestPriorityLabel(unittest.TestCase):

    def test_p0_has_red_emoji(self):
        self.assertIn("🔴", _priority_label("P0"))

    def test_p1_has_orange_emoji(self):
        self.assertIn("🟠", _priority_label("P1"))

    def test_p2_has_yellow_emoji(self):
        self.assertIn("🟡", _priority_label("P2"))

    def test_label_contains_priority_text(self):
        self.assertIn("P1", _priority_label("P1"))

    def test_unknown_priority_has_fallback_emoji(self):
        label = _priority_label("UNKNOWN")
        self.assertIn("⚪", label)


# ---------------------------------------------------------------------------
# App initialisation
# ---------------------------------------------------------------------------

class TestAppInit(unittest.TestCase):

    def test_creates_without_error(self):
        app = make_app()
        self.assertIsNotNone(app)

    def test_default_title_is_idle_icon(self):
        app = make_app()
        self.assertIn("✉", app.title)

    def test_live_mode_false_by_default(self):
        app = make_app()
        self.assertFalse(app._live_mode)

    def test_live_mode_can_be_enabled(self):
        app = make_app(live_mode=True)
        self.assertTrue(app._live_mode)


# ---------------------------------------------------------------------------
# Task menu refresh
# ---------------------------------------------------------------------------

class TestTaskMenuRefresh(unittest.TestCase):

    def test_no_tasks_shows_placeholder(self):
        app = make_app()
        app._refresh_task_menu()
        self.assertIn("No pending tasks", app._no_tasks_item.title)

    def test_pending_task_updates_count(self):
        store = make_store()
        insert_task(store, email_id="e1")
        app = make_app(store=store)
        app._refresh_task_menu()
        self.assertIn("1", app._no_tasks_item.title)

    def test_multiple_tasks_shown_in_count(self):
        store = make_store()
        insert_task(store, email_id="e1")
        insert_task(store, email_id="e2")
        app = make_app(store=store)
        app._refresh_task_menu()
        self.assertIn("2", app._no_tasks_item.title)

    def test_completed_tasks_not_in_pending(self):
        store = make_store()
        tid = insert_task(store)
        store.update_status(tid, TaskStatus.COMPLETED)
        app = make_app(store=store)
        app._refresh_task_menu()
        self.assertIn("No pending tasks", app._no_tasks_item.title)


# ---------------------------------------------------------------------------
# Confirm / Decline / Complete actions
# ---------------------------------------------------------------------------

class TestTaskActions(unittest.TestCase):

    def test_confirm_p2_task(self):
        store = make_store()
        tid = insert_task(
            store, email_id="p2-email",
            priority="P2",
            confirmation_status=ConfirmationStatus.PENDING,
            deadline_state="NONE",
            deadline=None,
        )
        app = make_app(store=store)

        with patch("rumps.notification"):
            app._confirm_task(tid)

        task = store.get_by_id(tid)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.CONFIRMED)

    def test_decline_p2_task(self):
        store = make_store()
        tid = insert_task(
            store, email_id="p2-decline",
            priority="P2",
            confirmation_status=ConfirmationStatus.PENDING,
            deadline_state="NONE",
            deadline=None,
        )
        app = make_app(store=store)

        with patch("rumps.notification"):
            app._decline_task(tid)

        task = store.get_by_id(tid)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.DECLINED)
        self.assertEqual(task.status, TaskStatus.DECLINED)

    def test_declined_task_does_not_become_confirmed(self):
        store = make_store()
        tid = insert_task(
            store, email_id="p2-already-declined",
            priority="P2",
            confirmation_status=ConfirmationStatus.PENDING,
            deadline_state="NONE",
            deadline=None,
        )
        app = make_app(store=store)

        with patch("rumps.notification"):
            app._decline_task(tid)
            app._confirm_task(tid)  # attempt after decline

        task = store.get_by_id(tid)
        self.assertEqual(task.status, TaskStatus.DECLINED)

    def test_complete_task(self):
        store = make_store()
        tid = insert_task(store)
        app = make_app(store=store)

        with patch("rumps.notification"):
            app._complete_task(tid)

        task = store.get_by_id(tid)
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    def test_complete_nonexistent_task_does_not_crash(self):
        app = make_app()
        with patch("rumps.notification"):
            app._complete_task("nonexistent-uuid")  # should not raise


# ---------------------------------------------------------------------------
# Poll in dry-run (no Gmail configured)
# ---------------------------------------------------------------------------

class TestPollDryRun(unittest.TestCase):

    def test_poll_without_gmail_sets_status(self):
        app = make_app()
        app._do_poll()
        self.assertIn("dry run", app._last_status.lower())

    def test_poll_does_not_write_tasks_in_dry_run(self):
        store = make_store()
        app = make_app(store=store)
        app._do_poll()
        self.assertEqual(store.list_tasks(), [])


# ---------------------------------------------------------------------------
# Poll with MockGmailTransport
# ---------------------------------------------------------------------------

class TestPollWithMockGmail(unittest.TestCase):

    def _make_mock_agent(self, actionability="YES", priority="P1"):
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

    def test_dry_run_poll_does_not_create_task(self):
        store = make_store()
        gmail = MockGmailTransport()
        gmail.add_message("m1", subject="Fix bug",
                          body="Please fix the login bug by 2026-10-20.")
        agent = self._make_mock_agent()
        notif = MockTransport()

        with patch("rumps.Timer", return_value=MagicMock()):
            app = MailMateApp(
                store=store, agent=agent,
                gmail_transport=gmail,
                notification_transport=notif,
                live_mode=False,
            )

        app._do_poll()
        self.assertEqual(store.list_tasks(), [])
        self.assertEqual(notif.message_count, 0)

    def test_live_poll_creates_task(self):
        store = make_store()
        gmail = MockGmailTransport()
        gmail.add_message("m1", subject="Fix bug",
                          body="Please fix the login bug by 2026-10-20.")
        agent = self._make_mock_agent("YES", "P1")
        notif = MockTransport()

        with patch("rumps.Timer", return_value=MagicMock()):
            app = MailMateApp(
                store=store, agent=agent,
                gmail_transport=gmail,
                notification_transport=notif,
                live_mode=True,
            )

        app._do_poll()
        tasks = store.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].priority, "P1")

    def test_live_poll_sends_notification_after_task_created(self):
        store = make_store()
        gmail = MockGmailTransport()
        gmail.add_message("m1", body="Fix by 2026-10-20.")
        agent = self._make_mock_agent("YES", "P1")
        notif = MockTransport()

        with patch("rumps.Timer", return_value=MagicMock()):
            app = MailMateApp(
                store=store, agent=agent,
                gmail_transport=gmail,
                notification_transport=notif,
                live_mode=True,
            )

        app._do_poll()
        self.assertEqual(notif.message_count, 1)

    def test_already_processed_email_skipped(self):
        store = make_store()
        store.create_task(
            source_email_id="m1", title="Old", description="Old.",
            priority="P1", deadline_state="CLEAR",
            confirmation_status=ConfirmationStatus.NOT_REQUIRED,
            deadline="2026-10-20",
        )
        gmail = MockGmailTransport()
        gmail.add_message("m1", body="Fix by 2026-10-20.")
        agent = self._make_mock_agent()
        notif = MockTransport()

        with patch("rumps.Timer", return_value=MagicMock()):
            app = MailMateApp(
                store=store, agent=agent,
                gmail_transport=gmail,
                notification_transport=notif,
                live_mode=True,
            )

        app._do_poll()
        # Still only one task
        self.assertEqual(len(store.list_tasks()), 1)


if __name__ == "__main__":
    unittest.main()
