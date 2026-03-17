"""ScheduledTaskManager — time-triggered task execution for Mimir."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    id: str
    user_id: str
    description: str           # e.g. "每天早上查BTC价格"
    intent: str                # passed to action_engine
    interval_seconds: int      # execution interval
    last_run: float            # timestamp of last execution
    enabled: bool = True


class ScheduledTaskManager:
    """Manages periodic/scheduled tasks for users."""

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}

    def add_task(self, task: ScheduledTask) -> str:
        """Add a scheduled task. Returns the task id."""
        if not task.id:
            task.id = f"sched_{uuid.uuid4().hex[:8]}"
        self._tasks[task.id] = task
        log.info("Scheduled task added: %s (%s) every %ds",
                 task.id, task.description[:40], task.interval_seconds)
        return task.id

    def remove_task(self, task_id: str) -> None:
        """Remove a scheduled task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            log.info("Scheduled task removed: %s", task_id)

    def get_due_tasks(self) -> list[ScheduledTask]:
        """Return tasks whose interval has elapsed since last_run."""
        now = time.time()
        due: list[ScheduledTask] = []
        for task in self._tasks.values():
            if not task.enabled:
                continue
            if now - task.last_run >= task.interval_seconds:
                due.append(task)
        return due

    def mark_executed(self, task_id: str) -> None:
        """Update last_run timestamp to now."""
        if task_id in self._tasks:
            self._tasks[task_id].last_run = time.time()

    def list_tasks(self, user_id: str | None = None) -> list[ScheduledTask]:
        """List all tasks, optionally filtered by user_id."""
        if user_id is not None:
            return [t for t in self._tasks.values() if t.user_id == user_id]
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> ScheduledTask | None:
        return self._tasks.get(task_id)

    def to_dict(self) -> dict:
        """Serialize all tasks to a dict."""
        return {
            "tasks": {
                tid: {
                    "id": t.id,
                    "user_id": t.user_id,
                    "description": t.description,
                    "intent": t.intent,
                    "interval_seconds": t.interval_seconds,
                    "last_run": t.last_run,
                    "enabled": t.enabled,
                }
                for tid, t in self._tasks.items()
            }
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledTaskManager":
        """Deserialize from a dict."""
        mgr = cls()
        for tid, tdata in data.get("tasks", {}).items():
            task = ScheduledTask(
                id=tdata["id"],
                user_id=tdata["user_id"],
                description=tdata["description"],
                intent=tdata["intent"],
                interval_seconds=tdata["interval_seconds"],
                last_run=tdata["last_run"],
                enabled=tdata.get("enabled", True),
            )
            mgr._tasks[tid] = task
        return mgr
