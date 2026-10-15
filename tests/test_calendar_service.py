"""Tests for app/services/calendar_service.py and app/integrations/calendar.py.

Uses MockCalendarTransport and an in-memory TaskStore throughout.
No real Google Calendar API calls are made.
"""

import unittest

from app.integrations.calendar import (
    CalendarEventRequest,
    MockCalendarTransport,
    GoogleCalendarTransport,
)
from app.services.calendar_service import (
    CREATED,
    DUPLICATE,
    ERROR,
    INELIGIBLE,
    MISSING_TIME,
    MISSING_TIMEZONE,
    CalendarResult,
    check_eligibility,
    schedule_event,
)
from app.storage.task_store import (
    ConfirmationStatus,
    Task,
    TaskStatus,
    TaskStore,
)


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
) -> Task:
    return store.create_task(
        source_email_id=email_id,
        title="Fix login bug",
        description="Login fails for SSO users.",
        priority=priority,
        deadline_state=deadline_state,
        confirmation_status=confirmation_status,
        deadline=deadline,
    )


def make_transport() -> MockCalendarTransport:
    return MockCalendarTransport()


_TIME = "14:00"
_TZ = "Asia/Kolkata"


# ---------------------------------------------------------------------------
# MockCalendarTransport
# ---------------------------------------------------------------------------

class TestMockTransport(unittest.TestCase):

    def test_creates_event_and_returns_id(self):
        t = make_transport()
        req = CalendarEventRequest(
            task_id="t1", title="Meeting", description="Desc",
            date="2026-10-20", time="10:00", timezone="Asia/Kolkata",
        )
        event_id = t.create_event(req)
        self.assertTrue(event_id.startswith("mock-event-"))
        self.assertEqual(t.event_count, 1)

    def test_increments_event_id(self):
        t = make_transport()
        req = CalendarEventRequest(
            task_id="t1", title="A", description="B",
            date="2026-10-20", time="10:00", timezone="UTC",
        )
        id1 = t.create_event(req)
        id2 = t.create_event(req)
        self.assertNotEqual(id1, id2)

    def test_fail_next_raises(self):
        t = make_transport()
        t.fail_next = True
        req = CalendarEventRequest(
            task_id="t1", title="A", description="B",
            date="2026-10-20", time="10:00", timezone="UTC",
        )
        with self.assertRaises(RuntimeError):
            t.create_event(req)

    def test_fail_next_resets_after_one_failure(self):
        t = make_transport()
        t.fail_next = True
        req = CalendarEventRequest(
            task_id="t1", title="A", description="B",
            date="2026-10-20", time="10:00", timezone="UTC",
        )
        try:
            t.create_event(req)
        except RuntimeError:
            pass
        # Should succeed on second call
        event_id = t.create_event(req)
        self.assertIsNotNone(event_id)


# ---------------------------------------------------------------------------
# GoogleCalendarTransport safety gate
# ---------------------------------------------------------------------------

class TestGoogleTransportSafetyGate(unittest.TestCase):

    def test_raises_without_live_writes(self):
        with self.assertRaises(ValueError):
            GoogleCalendarTransport(live_writes=False)

    def test_requires_explicit_live_writes_true(self):
        # We do NOT actually instantiate with live_writes=True (would
        # need credentials), just confirm the guard is the blocker.
        try:
            GoogleCalendarTransport(live_writes=True,
                                    token_path="/nonexistent",
                                    credentials_path="/nonexistent")
        except ValueError:
            self.fail("Should not raise ValueError when live_writes=True")
        except Exception:
            pass  # other errors (file not found, etc.) are expected


# ---------------------------------------------------------------------------
# Eligibility checks
# ---------------------------------------------------------------------------

class TestEligibility(unittest.TestCase):

    def test_clear_deadline_with_time_and_tz_is_eligible(self):
        store = make_store()
        task = insert_task(store, deadline_state="CLEAR", deadline="2026-10-20")
        result = check_eligibility(task, _TIME, _TZ)
        self.assertIsNone(result)

    def test_ambiguous_deadline_is_ineligible(self):
        store = make_store()
        task = insert_task(store, deadline_state="AMBIGUOUS", deadline=None)
        result = check_eligibility(task, _TIME, _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, INELIGIBLE)

    def test_none_deadline_state_is_ineligible(self):
        store = make_store()
        task = insert_task(store, deadline_state="NONE", deadline=None)
        result = check_eligibility(task, _TIME, _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, INELIGIBLE)

    def test_missing_deadline_value_is_ineligible(self):
        store = make_store()
        task = insert_task(store, deadline_state="CLEAR", deadline=None)
        result = check_eligibility(task, _TIME, _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, INELIGIBLE)

    def test_declined_task_is_ineligible(self):
        store = make_store()
        task = insert_task(store, confirmation_status=ConfirmationStatus.PENDING)
        store.update_status(task.id, TaskStatus.DECLINED)
        task = store.get_by_id(task.id)
        result = check_eligibility(task, _TIME, _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, INELIGIBLE)

    def test_cancelled_task_is_ineligible(self):
        store = make_store()
        task = insert_task(store)
        store.update_status(task.id, TaskStatus.CANCELLED)
        task = store.get_by_id(task.id)
        result = check_eligibility(task, _TIME, _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, INELIGIBLE)

    def test_missing_time_returns_missing_time(self):
        store = make_store()
        task = insert_task(store)
        result = check_eligibility(task, "", _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, MISSING_TIME)

    def test_whitespace_time_returns_missing_time(self):
        store = make_store()
        task = insert_task(store)
        result = check_eligibility(task, "   ", _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, MISSING_TIME)

    def test_missing_timezone_returns_missing_timezone(self):
        store = make_store()
        task = insert_task(store)
        result = check_eligibility(task, _TIME, "")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, MISSING_TIMEZONE)

    def test_existing_calendar_event_id_is_duplicate(self):
        store = make_store()
        task = insert_task(store)
        store.set_calendar_event_id(task.id, "existing-event-id")
        task = store.get_by_id(task.id)
        result = check_eligibility(task, _TIME, _TZ)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, DUPLICATE)


# ---------------------------------------------------------------------------
# schedule_event — happy path
# ---------------------------------------------------------------------------

class TestScheduleEvent(unittest.TestCase):

    def test_creates_event_and_returns_created_status(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(result.status, CREATED)
        self.assertIsNotNone(result.event_id)

    def test_event_id_persisted_in_store(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        result = schedule_event(task, store, transport, _TIME, _TZ)
        persisted = store.get_by_id(task.id)
        self.assertEqual(persisted.calendar_event_id, result.event_id)

    def test_request_has_correct_date(self):
        store = make_store()
        task = insert_task(store, deadline="2026-11-01")
        transport = make_transport()
        schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(transport.last_event.request.date, "2026-11-01")

    def test_request_has_correct_time(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        schedule_event(task, store, transport, "09:30", _TZ)
        self.assertEqual(transport.last_event.request.time, "09:30")

    def test_request_has_correct_timezone(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        schedule_event(task, store, transport, _TIME, "Europe/London")
        self.assertEqual(transport.last_event.request.timezone, "Europe/London")

    def test_request_has_task_title(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(transport.last_event.request.title, task.title)


# ---------------------------------------------------------------------------
# schedule_event — ineligible paths
# ---------------------------------------------------------------------------

class TestScheduleEventIneligible(unittest.TestCase):

    def test_ambiguous_deadline_not_scheduled(self):
        store = make_store()
        task = insert_task(store, deadline_state="AMBIGUOUS", deadline=None)
        transport = make_transport()
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(result.status, INELIGIBLE)
        self.assertEqual(transport.event_count, 0)

    def test_none_deadline_not_scheduled(self):
        store = make_store()
        task = insert_task(store, deadline_state="NONE", deadline=None)
        transport = make_transport()
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(result.status, INELIGIBLE)
        self.assertEqual(transport.event_count, 0)

    def test_missing_time_not_scheduled(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        result = schedule_event(task, store, transport, "", _TZ)
        self.assertEqual(result.status, MISSING_TIME)
        self.assertEqual(transport.event_count, 0)

    def test_missing_timezone_not_scheduled(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        result = schedule_event(task, store, transport, _TIME, "")
        self.assertEqual(result.status, MISSING_TIMEZONE)
        self.assertEqual(transport.event_count, 0)

    def test_declined_task_not_scheduled(self):
        store = make_store()
        task = insert_task(store, confirmation_status=ConfirmationStatus.PENDING)
        store.update_status(task.id, TaskStatus.DECLINED)
        task = store.get_by_id(task.id)
        transport = make_transport()
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(result.status, INELIGIBLE)
        self.assertEqual(transport.event_count, 0)


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------

class TestDuplicatePrevention(unittest.TestCase):

    def test_second_schedule_returns_duplicate(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        schedule_event(task, store, transport, _TIME, _TZ)
        task = store.get_by_id(task.id)  # reload with event_id set
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(result.status, DUPLICATE)

    def test_duplicate_does_not_create_second_event(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        schedule_event(task, store, transport, _TIME, _TZ)
        task = store.get_by_id(task.id)
        schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(transport.event_count, 1)

    def test_existing_event_id_blocks_creation(self):
        store = make_store()
        task = insert_task(store)
        store.set_calendar_event_id(task.id, "already-exists")
        task = store.get_by_id(task.id)
        transport = make_transport()
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(result.status, DUPLICATE)
        self.assertEqual(transport.event_count, 0)


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling(unittest.TestCase):

    def test_transport_failure_returns_error(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        transport.fail_next = True
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertEqual(result.status, ERROR)

    def test_transport_failure_does_not_persist_event_id(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        transport.fail_next = True
        schedule_event(task, store, transport, _TIME, _TZ)
        task = store.get_by_id(task.id)
        self.assertIsNone(task.calendar_event_id)

    def test_error_result_has_reason(self):
        store = make_store()
        task = insert_task(store)
        transport = make_transport()
        transport.fail_next = True
        result = schedule_event(task, store, transport, _TIME, _TZ)
        self.assertTrue(len(result.reason) > 0)


if __name__ == "__main__":
    unittest.main()
