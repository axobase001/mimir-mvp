from dataclasses import dataclass, field
from enum import Enum


class NotifyLevel(Enum):
    URGENT = "urgent"
    RESULT = "result"
    PERIODIC = "periodic"


@dataclass
class Notification:
    level: NotifyLevel
    title: str
    body: str
    cycle: int
    related_beliefs: list[str] = field(default_factory=list)
    related_goals: list[str] = field(default_factory=list)


class Notifier:
    def __init__(self) -> None:
        self._queue: list[Notification] = []

    def push(self, notification: Notification) -> None:
        self._queue.append(notification)

    def pull_all(self) -> list[Notification]:
        notifications = self._queue.copy()
        self._queue.clear()
        return notifications

    def has_pending(self) -> bool:
        return len(self._queue) > 0
