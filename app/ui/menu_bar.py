"""MailMate AI — macOS menu bar application.

Sits in the menu bar, polls Gmail on a configurable interval, runs the
full processing pipeline, and surfaces task status via the tray icon and
a simple menu.

Menu structure
--------------
MailMate AI  [icon + unread count badge]
├── Status: <last poll time / error>
├── ── Tasks ──
├── [P0] Fix login bug          → mark complete
├── [P1] Pay invoice            → mark complete
├── [P2] Review document        → Confirm / Decline
├── ──────────────────
├── Poll now
├── Open task log
└── Quit

Safety rules
------------
- The menu bar never triggers real Gmail / Telegram / Calendar in a dry
  run.  ``live_mode=True`` must be set explicitly in configuration.
- Confirmed / declined actions go through the existing confirmation
  handler — no new policy logic is introduced here.
- Credentials are never displayed or logged.
- The app gracefully degrades if the Laya model or Gmail credentials are
  unavailable (shows an error status item instead of crashing).
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

import rumps

from app.services.orchestrator import (
    AWAITING_CONFIRMATION,
    NEEDS_REVIEW,
    NO_ACTION,
    SKIPPED,
    TASK_CREATED,
    DRY_RUN,
    process_email,
)
from app.services.confirmation_handler import handle_callback
from app.services.notifier import (
    CONFIRM_PREFIX,
    DECLINE_PREFIX,
    notify,
)
from app.storage.task_store import TaskStatus, TaskStore

log = logging.getLogger(__name__)

# Status line icons
_ICON_IDLE = "✉"
_ICON_BUSY = "⏳"
_ICON_ERROR = "⚠"

_PRIORITY_EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "OTHER": "⚪"}


def _priority_label(priority: str) -> str:
    return f"{_PRIORITY_EMOJI.get(priority, '⚪')} [{priority}]"


class MailMateApp(rumps.App):
    """Menu bar application for MailMate AI.

    Parameters
    ----------
    store:
        An open TaskStore backed by the application SQLite database.
    agent:
        Loaded Laya agent, or None to run in dry-run mode.
    gmail_transport:
        A GmailTransport (MockGmailTransport or GmailClient).
    notification_transport:
        A NotificationTransport (MockTransport or TelegramTransport).
    poll_interval:
        Seconds between automatic polls.  Default 300 (5 minutes).
    live_mode:
        When False (default) the orchestrator runs in dry-run mode —
        no tasks are written and no notifications are sent.
    """

    def __init__(
        self,
        store: TaskStore,
        agent=None,
        gmail_transport=None,
        notification_transport=None,
        poll_interval: int = 300,
        live_mode: bool = False,
    ) -> None:
        super().__init__(
            name="MailMate AI",
            title=_ICON_IDLE,
            quit_button=None,
        )

        self._store = store
        self._agent = agent
        self._gmail = gmail_transport
        self._notif = notification_transport
        self._poll_interval = poll_interval
        self._live_mode = live_mode
        self._poll_lock = threading.Lock()
        self._last_status = "Not polled yet"

        # Build the static menu skeleton; task items are rebuilt on each poll.
        self._status_item = rumps.MenuItem("Status: starting…")
        self._status_item.set_callback(None)

        self._tasks_title = rumps.MenuItem("── Tasks ──")
        self._tasks_title.set_callback(None)

        self._no_tasks_item = rumps.MenuItem("  No pending tasks")
        self._no_tasks_item.set_callback(None)

        self.menu = [
            self._status_item,
            None,  # separator
            self._tasks_title,
            self._no_tasks_item,
            None,
            rumps.MenuItem("Poll now", callback=self._poll_now),
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # Start automatic polling timer.
        self._timer = rumps.Timer(self._auto_poll, self._poll_interval)
        self._timer.start()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _auto_poll(self, _sender) -> None:
        self._run_poll()

    @rumps.clicked("Poll now")
    def _poll_now(self, _sender) -> None:
        threading.Thread(target=self._run_poll, daemon=True).start()

    def _run_poll(self) -> None:
        if not self._poll_lock.acquire(blocking=False):
            log.info("ui: poll already in progress, skipping")
            return

        self.title = _ICON_BUSY
        try:
            self._do_poll()
        except Exception as exc:
            log.error("ui: poll failed err=%s", type(exc).__name__)
            self._set_status(f"Error: {type(exc).__name__}")
            self.title = _ICON_ERROR
        finally:
            self._poll_lock.release()
            self._refresh_task_menu()

    def _do_poll(self) -> None:
        if self._gmail is None or self._agent is None:
            self._set_status("No Gmail or model configured — dry run only")
            self.title = _ICON_IDLE
            return

        from app.services.gmail_poller import fetch_new_emails

        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        processed = 0
        created = 0

        for email in fetch_new_emails(self._gmail, self._store):
            result = process_email(
                email,
                self._agent,
                self._store,
                dry_run=not self._live_mode,
            )
            processed += 1
            if result.status == TASK_CREATED:
                created += 1
            if result.notify and self._notif:
                notify(result, self._notif)

        self._set_status(f"Polled {now} — {processed} emails, {created} tasks")
        self.title = _ICON_IDLE

    def _set_status(self, text: str) -> None:
        self._last_status = text
        self._status_item.title = f"Status: {text}"

    # ------------------------------------------------------------------
    # Task menu
    # ------------------------------------------------------------------

    def _refresh_task_menu(self) -> None:
        """Rebuild the task section of the menu from the store."""
        try:
            pending = self._store.list_tasks(status=TaskStatus.PENDING)
        except Exception as exc:
            log.error("ui: failed to list tasks err=%s", type(exc).__name__)
            return

        # Remove old task items (everything between the tasks title and the
        # separator below it).  rumps menus support dict-style access.
        for key in list(self.menu.keys()):
            if key.startswith("__task__"):
                del self.menu[key]

        if not pending:
            self._no_tasks_item.title = "  No pending tasks"
            return

        self._no_tasks_item.title = f"  {len(pending)} pending task(s)"

        for task in pending[:10]:  # cap display at 10
            label = f"{_priority_label(task.priority)} {task.title[:50]}"
            item_key = f"__task__{task.id}"

            if task.confirmation_status.value == "PENDING":
                # P2 tasks show Confirm / Decline sub-items.
                confirm_item = rumps.MenuItem(
                    "  ✅ Confirm",
                    callback=lambda s, tid=task.id: self._confirm_task(tid),
                )
                decline_item = rumps.MenuItem(
                    "  ❌ Decline",
                    callback=lambda s, tid=task.id: self._decline_task(tid),
                )
                task_item = rumps.MenuItem(label)
                task_item[confirm_item.title] = confirm_item
                task_item[decline_item.title] = decline_item
            else:
                task_item = rumps.MenuItem(
                    label,
                    callback=lambda s, tid=task.id: self._complete_task(tid),
                )

            self.menu.insert_after(self._tasks_title.title, task_item)

    # ------------------------------------------------------------------
    # Task actions
    # ------------------------------------------------------------------

    def _confirm_task(self, task_id: str) -> None:
        result = handle_callback(f"{CONFIRM_PREFIX}{task_id}", self._store)
        status = "Confirmed ✅" if result.accepted else result.reply_text
        rumps.notification("MailMate AI", "Task confirmation", status)
        self._refresh_task_menu()

    def _decline_task(self, task_id: str) -> None:
        result = handle_callback(f"{DECLINE_PREFIX}{task_id}", self._store)
        status = "Declined ❌" if result.declined else result.reply_text
        rumps.notification("MailMate AI", "Task declined", status)
        self._refresh_task_menu()

    def _complete_task(self, task_id: str) -> None:
        try:
            self._store.update_status(task_id, TaskStatus.COMPLETED)
            rumps.notification("MailMate AI", "Task completed", "Marked as done ✓")
        except Exception as exc:
            log.error("ui: complete_task failed task_id=%s err=%s",
                      task_id, type(exc).__name__)
        self._refresh_task_menu()

    # ------------------------------------------------------------------
    # Quit
    # ------------------------------------------------------------------

    @rumps.clicked("Quit")
    def _quit(self, _sender) -> None:
        self._timer.stop()
        rumps.quit_application()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    db_path: str = "mailmate.db",
    poll_interval: int = 300,
    live_mode: bool = False,
    agent=None,
    gmail_transport=None,
    notification_transport=None,
) -> None:
    """Start the menu bar application.

    Parameters
    ----------
    db_path:
        Path to the SQLite task database.
    poll_interval:
        Seconds between automatic Gmail polls.
    live_mode:
        Set True to enable real task creation and notifications.
        Defaults to False (dry-run).
    agent:
        Loaded Laya agent.  Pass None to run without classification
        (dry-run mode only).
    gmail_transport:
        A GmailTransport.  Pass None for offline / dry-run mode.
    notification_transport:
        A NotificationTransport.  Pass None to suppress notifications.
    """
    store = TaskStore(db_path)
    app = MailMateApp(
        store=store,
        agent=agent,
        gmail_transport=gmail_transport,
        notification_transport=notification_transport,
        poll_interval=poll_interval,
        live_mode=live_mode,
    )
    app.run()
