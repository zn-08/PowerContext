"""The domain model of one task board."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum


class TaskStatus(StrEnum):
    """The lifecycle states a task can be in."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Task:
    """One unit of work on a board."""

    id: str
    title: str
    status: TaskStatus = TaskStatus.TODO
    assignee: str = ""
    labels: tuple[str, ...] = ()
    estimate: int = 0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("task id must not be blank")
        if not self.title.strip():
            raise ValueError("task title must not be blank")
        if self.estimate < 0:
            raise ValueError("task estimate must not be negative")
        normalized = tuple(label.strip().lower() for label in self.labels if label.strip())
        object.__setattr__(self, "labels", normalized)

    def has_label(self, label: str) -> bool:
        """Return whether this task carries ``label``."""

        return label.strip().lower() in self.labels

    def with_changes(self, **changes: object) -> "Task":
        """Return a copy of this task with the given fields replaced."""

        if "status" in changes and isinstance(changes["status"], str):
            changes["status"] = TaskStatus(changes["status"])
        if "labels" in changes and isinstance(changes["labels"], list):
            changes["labels"] = tuple(changes["labels"])
        return replace(self, **changes)


@dataclass(slots=True)
class BoardSnapshot:
    """The serialisable state of a whole board."""

    tasks: list[Task] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "status": str(task.status),
                    "assignee": task.assignee,
                    "labels": list(task.labels),
                    "estimate": task.estimate,
                }
                for task in self.tasks
            ]
        }


__all__ = ["BoardSnapshot", "Task", "TaskStatus"]
