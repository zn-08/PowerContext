"""Selection helpers for a task board."""

from __future__ import annotations

from typing import Iterable, Sequence

from taskboard.model import Task, TaskStatus


def filter_tasks(
    tasks: Iterable[Task],
    *,
    status: TaskStatus | None = None,
    assignee: str | None = None,
    labels: Sequence[str] | None = None,
) -> list[Task]:
    """Return the tasks that match every given condition.

    A condition that is not given does not narrow the result.  Several labels
    are an "or": a task matches when it carries any of them.  Labels are
    compared the way :meth:`Task.has_label` compares them, so the case and the
    padding of a label do not matter.  A label that is blank carries no
    condition and is ignored.
    """

    selected = list(tasks)
    if status is not None:
        selected = [task for task in selected if task.status == status]
    if assignee is not None:
        selected = [task for task in selected if task.assignee == assignee]
    if labels:
        wanted = {label.strip().lower() for label in labels if label.strip()}
        if wanted:
            selected = [task for task in selected if wanted.intersection(task.labels)]
    return selected


def group_by_status(tasks: Iterable[Task]) -> dict[str, list[Task]]:
    """Group the tasks by their status, keeping every status that has tasks."""

    grouped: dict[str, list[Task]] = {}
    for task in tasks:
        grouped.setdefault(str(task.status), []).append(task)
    return grouped


def summarize(tasks: Sequence[Task]) -> dict[str, int]:
    """Count the tasks per status."""

    summary = {str(status): 0 for status in TaskStatus}
    for task in tasks:
        summary[str(task.status)] += 1
    return summary


__all__ = ["filter_tasks", "group_by_status", "summarize"]
