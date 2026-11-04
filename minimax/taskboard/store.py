"""In-memory storage for one task board."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from taskboard.model import BoardSnapshot, Task, TaskStatus


class TaskBoardError(Exception):
    """Raised when a board operation cannot be satisfied."""


class TaskStore:
    """Hold the tasks of one board and keep their identities unique."""

    def __init__(self, tasks: Iterable[Task] | None = None) -> None:
        self._tasks: dict[str, Task] = {}
        for task in tasks or ():
            self.add(task)

    def add(self, task: Task) -> Task:
        """Add one task; a repeated identity is rejected."""

        if task.id in self._tasks:
            raise TaskBoardError(f"task {task.id!r} already exists on this board")
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task:
        """Return one task by identity."""

        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise TaskBoardError(f"unknown task {task_id!r}") from error

    def list(self) -> list[Task]:
        """Return every task, ordered by identity."""

        return [self._tasks[key] for key in sorted(self._tasks)]

    def update(self, task_id: str, **changes: object) -> Task:
        """Replace the given fields of one task and return the new value."""

        updated = self.get(task_id).with_changes(**changes)
        self._tasks[task_id] = updated
        return updated

    def remove(self, task_id: str) -> None:
        """Remove one task from the board."""

        self.get(task_id)
        del self._tasks[task_id]

    def snapshot(self) -> BoardSnapshot:
        """Return the board as a serialisable snapshot."""

        return BoardSnapshot(tasks=self.list())

    def save(self, path: str | Path) -> None:
        """Write the board to a JSON file."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.snapshot().as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "TaskStore":
        """Read a board back from a JSON file."""

        source = Path(path)
        if not source.exists():
            raise TaskBoardError(f"board file not found: {source}")
        payload = json.loads(source.read_text(encoding="utf-8"))
        tasks = [
            Task(
                id=entry["id"],
                title=entry["title"],
                status=TaskStatus(entry.get("status", TaskStatus.TODO)),
                assignee=entry.get("assignee", ""),
                labels=tuple(entry.get("labels", ())),
                estimate=int(entry.get("estimate", 0)),
            )
            for entry in payload.get("tasks", [])
        ]
        return cls(tasks)


__all__ = ["TaskBoardError", "TaskStore"]
