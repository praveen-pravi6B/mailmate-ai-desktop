"""Tests for app/services/confirmation_handler.py.

Uses an in-memory TaskStore throughout.  No Telegram HTTP calls are made.
"""

import unittest
from datetime import datetime, timezone, timedelta

from app.services.confirmation_handler import (
    DEFAULT_TIMEOUT_HOURS,
    ConfirmationResult,
    handle_callback,
)
from app.services.notifier import CONFIRM_PREFIX, DECLINE_PREFIX
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


def make_p2_task(store: TaskStore, task_id_hint: str = "email-p2") -> str:
    """Insert a P2 task in PENDING confirmation state and return its task_id."""
    task = store.create_task(
        source_email_id=task_id_hint,
        title="Review document",
        description="Please review when you have time.",
        priority="P2",
        deadline_state="NONE",
        confirmation_status=ConfirmationStatus.PENDING,
    )
    return task.id


def confirm_data(task_id: str) -> str:
    return f"{CONFIRM_PREFIX}{task_id}"


def decline_data(task_id: str) -> str:
    return f"{DECLINE_PREFIX}{task_id}"


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

class TestConfirm(unittest.TestCase):

    def test_confirm_returns_accepted(self):
        store = make_store()
        tid = make_p2_task(store)
        result = handle_callback(confirm_data(tid), store)
        self.assertTrue(result.accepted)
        self.assertFalse(result.declined)

    def test_confirm_updates_confirmation_status(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(confirm_data(tid), store)
        task = store.get_by_id(tid)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.CONFIRMED)

    def test_confirm_updates_task_status(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(confirm_data(tid), store)
        task = store.get_by_id(tid)
        self.assertEqual(task.status, TaskStatus.CONFIRMED)

    def test_confirm_reply_text_non_empty(self):
        store = make_store()
        tid = make_p2_task(store)
        result = handle_callback(confirm_data(tid), store)
        self.assertTrue(len(result.reply_text) > 0)

    def test_confirm_task_id_in_result(self):
        store = make_store()
        tid = make_p2_task(store)
        result = handle_callback(confirm_data(tid), store)
        self.assertEqual(result.task_id, tid)


# ---------------------------------------------------------------------------
# Decline
# ---------------------------------------------------------------------------

class TestDecline(unittest.TestCase):

    def test_decline_returns_declined(self):
        store = make_store()
        tid = make_p2_task(store)
        result = handle_callback(decline_data(tid), store)
        self.assertTrue(result.declined)
        self.assertFalse(result.accepted)

    def test_decline_updates_confirmation_status(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(decline_data(tid), store)
        task = store.get_by_id(tid)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.DECLINED)

    def test_decline_updates_task_status(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(decline_data(tid), store)
        task = store.get_by_id(tid)
        self.assertEqual(task.status, TaskStatus.DECLINED)

    def test_declined_task_stays_declined(self):
        """A declined task cannot be confirmed after the fact."""
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(decline_data(tid), store)
        # Attempt to confirm after decline — should hit already_resolved
        result = handle_callback(confirm_data(tid), store)
        self.assertTrue(result.already_resolved)
        # Status must remain DECLINED
        task = store.get_by_id(tid)
        self.assertEqual(task.status, TaskStatus.DECLINED)

    def test_declined_task_never_becomes_active(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(decline_data(tid), store)
        task = store.get_by_id(tid)
        self.assertNotEqual(task.status, TaskStatus.PENDING)
        self.assertNotEqual(task.status, TaskStatus.CONFIRMED)
        self.assertNotEqual(task.status, TaskStatus.COMPLETED)


# ---------------------------------------------------------------------------
# Duplicate callbacks
# ---------------------------------------------------------------------------

class TestDuplicateCallbacks(unittest.TestCase):

    def test_duplicate_confirm_returns_already_resolved(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(confirm_data(tid), store)
        result = handle_callback(confirm_data(tid), store)
        self.assertTrue(result.already_resolved)

    def test_duplicate_decline_returns_already_resolved(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(decline_data(tid), store)
        result = handle_callback(decline_data(tid), store)
        self.assertTrue(result.already_resolved)

    def test_duplicate_does_not_change_status(self):
        store = make_store()
        tid = make_p2_task(store)
        handle_callback(confirm_data(tid), store)
        handle_callback(confirm_data(tid), store)  # duplicate
        task = store.get_by_id(tid)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.CONFIRMED)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

class TestTimeout(unittest.TestCase):

    def _make_old_task(self, store: TaskStore, hours_ago: int) -> str:
        """Insert a P2 task with a backdated created_at."""
        task = store.create_task(
            source_email_id=f"old-email-{hours_ago}h",
            title="Old task",
            description="Very old.",
            priority="P2",
            deadline_state="NONE",
            confirmation_status=ConfirmationStatus.PENDING,
        )
        # Patch created_at directly via a raw SQL update.
        old_ts = (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).isoformat()
        with store._connect() as conn:
            conn.execute(
                "UPDATE tasks SET created_at=?, updated_at=? WHERE id=?",
                (old_ts, old_ts, task.id),
            )
        return task.id

    def test_expired_confirmation_returns_expired(self):
        store = make_store()
        tid = self._make_old_task(store, hours_ago=DEFAULT_TIMEOUT_HOURS + 1)
        result = handle_callback(confirm_data(tid), store,
                                 timeout_hours=DEFAULT_TIMEOUT_HOURS)
        self.assertTrue(result.expired)
        self.assertFalse(result.accepted)

    def test_expired_task_not_modified(self):
        store = make_store()
        tid = self._make_old_task(store, hours_ago=DEFAULT_TIMEOUT_HOURS + 1)
        handle_callback(confirm_data(tid), store,
                        timeout_hours=DEFAULT_TIMEOUT_HOURS)
        task = store.get_by_id(tid)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.PENDING)

    def test_within_window_is_not_expired(self):
        store = make_store()
        tid = self._make_old_task(store, hours_ago=DEFAULT_TIMEOUT_HOURS - 1)
        result = handle_callback(confirm_data(tid), store,
                                 timeout_hours=DEFAULT_TIMEOUT_HOURS)
        self.assertFalse(result.expired)
        self.assertTrue(result.accepted)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):

    def test_unknown_callback_data_returns_error(self):
        store = make_store()
        result = handle_callback("garbage:xyz", store)
        self.assertTrue(result.error)

    def test_empty_callback_data_returns_error(self):
        store = make_store()
        result = handle_callback("", store)
        self.assertTrue(result.error)

    def test_missing_task_id_returns_error(self):
        store = make_store()
        result = handle_callback(CONFIRM_PREFIX, store)
        self.assertTrue(result.error)

    def test_nonexistent_task_returns_error(self):
        store = make_store()
        result = handle_callback(confirm_data("nonexistent-uuid"), store)
        self.assertTrue(result.error)

    def test_error_result_has_reply_text(self):
        store = make_store()
        result = handle_callback("garbage", store)
        self.assertTrue(len(result.reply_text) > 0)


if __name__ == "__main__":
    unittest.main()
