"""SQLite-backed task store for MailMate AI.

Persists tasks created from actionable emails. Enforces idempotency via
a unique source_email_id so reprocessing the same email never creates a
duplicate task.

This module only reads and writes the database. It does not call Laya,
apply policy, send notifications, or create calendar events.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Generator, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "PENDING"          # Created; awaiting any further action
    CONFIRMED = "CONFIRMED"      # P2 task confirmed by user
    DECLINED = "DECLINED"        # P2 task declined by user
    COMPLETED = "COMPLETED"      # Task marked done
    CANCELLED = "CANCELLED"      # Withdrawn before completion


class ConfirmationStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"  # P0/P1 — no confirmation needed
    PENDING = "PENDING"            # P2 — awaiting user response
    CONFIRMED = "CONFIRMED"        # P2 — user confirmed
    DECLINED = "DECLINED"          # P2 — user declined


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str                          # Stable UUID
    source_email_id: str             # Unique ID of the originating email
    title: str
    description: str
    priority: str                    # P0 / P1 / P2
    deadline: Optional[str]          # ISO-8601 date/datetime string, or None
    deadline_state: str              # NONE / CLEAR / AMBIGUOUS
    status: TaskStatus
    confirmation_status: ConfirmationStatus
    calendar_event_id: Optional[str] # Set if a Calendar event is later created
    created_at: str                  # ISO-8601 UTC
    updated_at: str                  # ISO-8601 UTC


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    source_email_id     TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    priority            TEXT NOT NULL,
    deadline            TEXT,
    deadline_state      TEXT NOT NULL,
    status              TEXT NOT NULL,
    confirmation_status TEXT NOT NULL,
    calendar_event_id   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""

_COLUMN_ORDER = (
    "id",
    "source_email_id",
    "title",
    "description",
    "priority",
    "deadline",
    "deadline_state",
    "status",
    "confirmation_status",
    "calendar_event_id",
    "created_at",
    "updated_at",
)


# ---------------------------------------------------------------------------
# TaskStore
# ---------------------------------------------------------------------------

class DuplicateEmailError(Exception):
    """Raised when a task for the given source_email_id already exists."""


class TaskNotFoundError(Exception):
    """Raised when no task matches the requested identifier."""


class TaskStore:
    """SQLite task store.

    Pass ``db_path=":memory:"`` for in-process tests.  In-memory stores
    keep a single persistent connection so the schema survives across
    calls; file-backed stores open a fresh connection per operation.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # Keep one connection alive for :memory: databases — each new
        # connection would create a separate empty database.
        self._mem_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._mem_conn = self._make_conn()
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        if self._mem_conn is not None:
            try:
                yield self._mem_conn
                self._mem_conn.commit()
            except Exception:
                self._mem_conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            source_email_id=row["source_email_id"],
            title=row["title"],
            description=row["description"],
            priority=row["priority"],
            deadline=row["deadline"],
            deadline_state=row["deadline_state"],
            status=TaskStatus(row["status"]),
            confirmation_status=ConfirmationStatus(row["confirmation_status"]),
            calendar_event_id=row["calendar_event_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_task(
        self,
        source_email_id: str,
        title: str,
        description: str,
        priority: str,
        deadline_state: str,
        confirmation_status: ConfirmationStatus,
        deadline: Optional[str] = None,
    ) -> Task:
        """Insert a new task and return it.

        Raises DuplicateEmailError if a task for source_email_id exists.
        """
        now = self._now()
        task = Task(
            id=str(uuid.uuid4()),
            source_email_id=source_email_id,
            title=title,
            description=description,
            priority=priority,
            deadline=deadline,
            deadline_state=deadline_state,
            status=TaskStatus.PENDING,
            confirmation_status=confirmation_status,
            calendar_event_id=None,
            created_at=now,
            updated_at=now,
        )

        sql = f"""
            INSERT INTO tasks ({', '.join(_COLUMN_ORDER)})
            VALUES ({', '.join('?' * len(_COLUMN_ORDER))})
        """
        values = (
            task.id,
            task.source_email_id,
            task.title,
            task.description,
            task.priority,
            task.deadline,
            task.deadline_state,
            task.status.value,
            task.confirmation_status.value,
            task.calendar_event_id,
            task.created_at,
            task.updated_at,
        )

        try:
            with self._connect() as conn:
                conn.execute(sql, values)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise DuplicateEmailError(
                    f"Task for email '{source_email_id}' already exists."
                ) from exc
            raise

        return task

    def get_by_id(self, task_id: str) -> Task:
        """Return a task by its UUID. Raises TaskNotFoundError if absent."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"No task with id '{task_id}'.")
        return self._row_to_task(row)

    def get_by_email_id(self, source_email_id: str) -> Task:
        """Return the task for a given email. Raises TaskNotFoundError if absent."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE source_email_id = ?",
                (source_email_id,),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(
                f"No task for email '{source_email_id}'."
            )
        return self._row_to_task(row)

    def email_has_task(self, source_email_id: str) -> bool:
        """Return True if a task already exists for this email."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM tasks WHERE source_email_id = ? LIMIT 1",
                (source_email_id,),
            ).fetchone()
        return row is not None

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[Task]:
        """Return all tasks, optionally filtered by status, newest first."""
        with self._connect() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                    (status.value,),
                ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update_status(self, task_id: str, status: TaskStatus) -> Task:
        """Update task status. Raises TaskNotFoundError if absent."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, task_id),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"No task with id '{task_id}'.")
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row)

    def update_confirmation(
        self, task_id: str, confirmation_status: ConfirmationStatus
    ) -> Task:
        """Update confirmation status. Raises TaskNotFoundError if absent."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET confirmation_status = ?, updated_at = ? WHERE id = ?",
                (confirmation_status.value, now, task_id),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"No task with id '{task_id}'.")
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row)

    def set_calendar_event_id(self, task_id: str, calendar_event_id: str) -> Task:
        """Persist a Calendar event ID after a calendar event is created separately."""
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET calendar_event_id = ?, updated_at = ? WHERE id = ?",
                (calendar_event_id, now, task_id),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"No task with id '{task_id}'.")
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row)
