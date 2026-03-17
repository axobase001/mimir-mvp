"""Tests for ScheduledTaskManager (Feature #5)."""

import time

from mimir.core.scheduled_tasks import ScheduledTask, ScheduledTaskManager


def test_add_and_list_tasks():
    """Add tasks and list them."""
    mgr = ScheduledTaskManager()

    t1 = ScheduledTask(
        id="t1", user_id="u1",
        description="Check BTC price",
        intent="search for BTC price",
        interval_seconds=60,
        last_run=0.0,
    )
    t2 = ScheduledTask(
        id="t2", user_id="u1",
        description="Check ETH price",
        intent="search for ETH price",
        interval_seconds=3600,
        last_run=0.0,
    )

    mgr.add_task(t1)
    mgr.add_task(t2)

    tasks = mgr.list_tasks()
    assert len(tasks) == 2

    tasks_u1 = mgr.list_tasks(user_id="u1")
    assert len(tasks_u1) == 2

    tasks_u2 = mgr.list_tasks(user_id="u2")
    assert len(tasks_u2) == 0


def test_remove_task():
    mgr = ScheduledTaskManager()
    t = ScheduledTask(
        id="t1", user_id="u1", description="test",
        intent="test", interval_seconds=60, last_run=0.0,
    )
    mgr.add_task(t)
    assert len(mgr.list_tasks()) == 1

    mgr.remove_task("t1")
    assert len(mgr.list_tasks()) == 0


def test_get_due_tasks_returns_overdue():
    """A task with last_run far in the past should be due."""
    mgr = ScheduledTaskManager()
    past = time.time() - 120  # 2 minutes ago
    t = ScheduledTask(
        id="t1", user_id="u1", description="check prices",
        intent="price check", interval_seconds=60,
        last_run=past,
    )
    mgr.add_task(t)

    due = mgr.get_due_tasks()
    assert len(due) == 1
    assert due[0].id == "t1"


def test_get_due_tasks_skips_recent():
    """A task that ran recently should NOT be due."""
    mgr = ScheduledTaskManager()
    recent = time.time()  # just now
    t = ScheduledTask(
        id="t1", user_id="u1", description="check prices",
        intent="price check", interval_seconds=60,
        last_run=recent,
    )
    mgr.add_task(t)

    due = mgr.get_due_tasks()
    assert len(due) == 0


def test_get_due_tasks_skips_disabled():
    """Disabled tasks are never due."""
    mgr = ScheduledTaskManager()
    t = ScheduledTask(
        id="t1", user_id="u1", description="disabled",
        intent="nope", interval_seconds=60,
        last_run=0.0, enabled=False,
    )
    mgr.add_task(t)

    due = mgr.get_due_tasks()
    assert len(due) == 0


def test_mark_executed_updates_last_run():
    mgr = ScheduledTaskManager()
    t = ScheduledTask(
        id="t1", user_id="u1", description="test",
        intent="test", interval_seconds=60, last_run=0.0,
    )
    mgr.add_task(t)

    before = time.time()
    mgr.mark_executed("t1")
    after = time.time()

    task = mgr.get_task("t1")
    assert task is not None
    assert before <= task.last_run <= after


def test_serialization_roundtrip():
    """to_dict -> from_dict should preserve all task data."""
    mgr = ScheduledTaskManager()
    t = ScheduledTask(
        id="t1", user_id="u1",
        description="Check BTC",
        intent="search BTC price",
        interval_seconds=300,
        last_run=1000.0,
        enabled=True,
    )
    mgr.add_task(t)

    data = mgr.to_dict()
    mgr2 = ScheduledTaskManager.from_dict(data)

    tasks = mgr2.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].id == "t1"
    assert tasks[0].description == "Check BTC"
    assert tasks[0].interval_seconds == 300
    assert tasks[0].last_run == 1000.0


def test_auto_generated_id():
    """Task with empty id should get an auto-generated id."""
    mgr = ScheduledTaskManager()
    t = ScheduledTask(
        id="", user_id="u1", description="test",
        intent="test", interval_seconds=60, last_run=0.0,
    )
    task_id = mgr.add_task(t)
    assert task_id.startswith("sched_")
    assert len(task_id) > 6


def test_get_due_tasks_60_second_interval():
    """Create a 60-second task, verify it's due after the interval passes."""
    mgr = ScheduledTaskManager()
    # Simulate task that ran 61 seconds ago
    t = ScheduledTask(
        id="t1", user_id="u1", description="periodic check",
        intent="check something", interval_seconds=60,
        last_run=time.time() - 61,
    )
    mgr.add_task(t)

    due = mgr.get_due_tasks()
    assert len(due) == 1
    assert due[0].id == "t1"

    # Mark executed and verify no longer due
    mgr.mark_executed("t1")
    due = mgr.get_due_tasks()
    assert len(due) == 0
