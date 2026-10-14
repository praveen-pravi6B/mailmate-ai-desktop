"""Tests for app/storage/task_store.py.

All tests use an in-memory SQLite database (:memory:) so they are
isolated, fast, and have no filesystem side effects.
"""

import unittest

from app.storage.task_store import (
    ConfirmationStatus,
    DuplicateEmailError,
    TaskNotFoundError,
    TaskStatus,
    TaskStore,
)


def make_store() -> TaskStore:
    return TaskStore(":memory:")


def make_task(store: TaskStore, email_id: str = "email-001", **kwargs):
    defaults = dict(
        title="Fix login bug",
        description="Login fails for SSO users.",
        priority="P1",
        deadline_state="CLEAR",
        confirmation_status=ConfirmationStatus.NOT_REQUIRED,
        deadline="2026-10-20",
    )
    defaults.update(kwargs)
    return store.create_task(source_email_id=email_id, **defaults)


class TestCreateTask(unittest.TestCase):

    def test_creates_task_with_correct_fields(self):
        store = make_store()
        task = make_task(store)
        self.assertEqual(task.source_email_id, "email-001")
        self.assertEqual(task.title, "Fix login bug")
        self.assertEqual(task.priority, "P1")
        self.assertEqual(task.deadline, "2026-10-20")
        self.assertEqual(task.deadline_state, "CLEAR")
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertEqual(task.confirmation_status, ConfirmationStatus.NOT_REQUIRED)
        self.assertIsNone(task.calendar_event_id)
        self.assertIsNotNone(task.id)
        self.assertIsNotNone(task.created_at)
        self.assertIsNotNone(task.updated_at)

    def test_task_with_no_deadline(self):
        store = make_store()
        task = make_task(store, deadline=None, deadline_state="NONE")
        self.assertIsNone(task.deadline)
        self.assertEqual(task.deadline_state, "NONE")

    def test_p2_task_has_pending_confirmation(self):
        store = make_store()
        task = make_task(
            store,
            priority="P2",
            confirmation_status=ConfirmationStatus.PENDING,
        )
        self.assertEqual(task.confirmation_status, ConfirmationStatus.PENDING)

    def test_duplicate_email_raises(self):
        store = make_store()
        make_task(store, email_id="email-dup")
        with self.assertRaises(DuplicateEmailError):
            make_task(store, email_id="email-dup")

    def test_different_emails_create_separate_tasks(self):
        store = make_store()
        t1 = make_task(store, email_id="email-A")
        t2 = make_task(store, email_id="email-B")
        self.assertNotEqual(t1.id, t2.id)


class TestGetTask(unittest.TestCase):

    def test_get_by_id(self):
        store = make_store()
        created = make_task(store)
        fetched = store.get_by_id(created.id)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.title, created.title)

    def test_get_by_id_not_found(self):
        store = make_store()
        with self.assertRaises(TaskNotFoundError):
            store.get_by_id("nonexistent-id")

    def test_get_by_email_id(self):
        store = make_store()
        created = make_task(store, email_id="email-lookup")
        fetched = store.get_by_email_id("email-lookup")
        self.assertEqual(fetched.id, created.id)

    def test_get_by_email_id_not_found(self):
        store = make_store()
        with self.assertRaises(TaskNotFoundError):
            store.get_by_email_id("missing-email")

    def test_email_has_task_true(self):
        store = make_store()
        make_task(store, email_id="email-exists")
        self.assertTrue(store.email_has_task("email-exists"))

    def test_email_has_task_false(self):
        store = make_store()
        self.assertFalse(store.email_has_task("email-missing"))


class TestListTasks(unittest.TestCase):

    def test_list_all_tasks(self):
        store = make_store()
        make_task(store, email_id="e1")
        make_task(store, email_id="e2")
        tasks = store.list_tasks()
        self.assertEqual(len(tasks), 2)

    def test_list_empty_store(self):
        store = make_store()
        self.assertEqual(store.list_tasks(), [])

    def test_list_filtered_by_status(self):
        store = make_store()
        t1 = make_task(store, email_id="e1")
        make_task(store, email_id="e2")
        store.update_status(t1.id, TaskStatus.COMPLETED)

        pending = store.list_tasks(status=TaskStatus.PENDING)
        completed = store.list_tasks(status=TaskStatus.COMPLETED)
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].id, t1.id)


class TestUpdateStatus(unittest.TestCase):

    def test_update_status(self):
        store = make_store()
        task = make_task(store)
        updated = store.update_status(task.id, TaskStatus.COMPLETED)
        self.assertEqual(updated.status, TaskStatus.COMPLETED)

    def test_update_status_reflects_in_get(self):
        store = make_store()
        task = make_task(store)
        store.update_status(task.id, TaskStatus.CANCELLED)
        fetched = store.get_by_id(task.id)
        self.assertEqual(fetched.status, TaskStatus.CANCELLED)

    def test_update_status_not_found(self):
        store = make_store()
        with self.assertRaises(TaskNotFoundError):
            store.update_status("bad-id", TaskStatus.COMPLETED)

    def test_updated_at_changes(self):
        store = make_store()
        task = make_task(store)
        original_updated_at = task.updated_at
        updated = store.update_status(task.id, TaskStatus.COMPLETED)
        # updated_at must be >= created_at (may be equal in fast tests)
        self.assertGreaterEqual(updated.updated_at, original_updated_at)


class TestUpdateConfirmation(unittest.TestCase):

    def test_confirm_p2_task(self):
        store = make_store()
        task = make_task(
            store,
            priority="P2",
            confirmation_status=ConfirmationStatus.PENDING,
        )
        updated = store.update_confirmation(task.id, ConfirmationStatus.CONFIRMED)
        self.assertEqual(updated.confirmation_status, ConfirmationStatus.CONFIRMED)

    def test_decline_p2_task(self):
        store = make_store()
        task = make_task(
            store,
            priority="P2",
            confirmation_status=ConfirmationStatus.PENDING,
        )
        updated = store.update_confirmation(task.id, ConfirmationStatus.DECLINED)
        self.assertEqual(updated.confirmation_status, ConfirmationStatus.DECLINED)

    def test_update_confirmation_not_found(self):
        store = make_store()
        with self.assertRaises(TaskNotFoundError):
            store.update_confirmation("bad-id", ConfirmationStatus.CONFIRMED)


class TestCalendarEventId(unittest.TestCase):

    def test_set_calendar_event_id(self):
        store = make_store()
        task = make_task(store)
        self.assertIsNone(task.calendar_event_id)
        updated = store.set_calendar_event_id(task.id, "cal-event-xyz")
        self.assertEqual(updated.calendar_event_id, "cal-event-xyz")

    def test_calendar_event_id_persists(self):
        store = make_store()
        task = make_task(store)
        store.set_calendar_event_id(task.id, "cal-event-abc")
        fetched = store.get_by_id(task.id)
        self.assertEqual(fetched.calendar_event_id, "cal-event-abc")

    def test_set_calendar_event_not_found(self):
        store = make_store()
        with self.assertRaises(TaskNotFoundError):
            store.set_calendar_event_id("bad-id", "cal-event-xyz")


class TestIdempotency(unittest.TestCase):
    """Reprocessing the same email must never create a second task."""

    def test_reprocess_same_email_raises(self):
        store = make_store()
        make_task(store, email_id="idempotent-email")
        with self.assertRaises(DuplicateEmailError):
            make_task(store, email_id="idempotent-email")

    def test_email_has_task_can_be_used_to_skip(self):
        store = make_store()
        make_task(store, email_id="skip-email")
        # Caller checks before inserting — no exception needed
        self.assertTrue(store.email_has_task("skip-email"))


if __name__ == "__main__":
    unittest.main()
