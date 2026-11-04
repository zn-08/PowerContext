"""Regression tests for the task board store.

These tests protect the behaviour that exists before the label filter change.
They must pass both before and after that change.
"""

from __future__ import annotations

import json

import pytest

from taskboard.model import Task, TaskStatus
from taskboard.store import TaskBoardError, TaskStore


def _task(task_id: str = "T-1", **changes: object) -> Task:
    return Task(id=task_id, title="Ship the release", **changes)


def test_add_and_get_one_task() -> None:
    store = TaskStore()
    store.add(_task())
    assert store.get("T-1").title == "Ship the release"


def test_list_orders_tasks_by_identity() -> None:
    store = TaskStore([_task("T-2"), _task("T-1"), _task("T-3")])
    assert [task.id for task in store.list()] == ["T-1", "T-2", "T-3"]


def test_add_rejects_a_repeated_identity() -> None:
    store = TaskStore([_task("T-1")])
    with pytest.raises(TaskBoardError, match="already exists"):
        store.add(_task("T-1"))


def test_get_rejects_an_unknown_identity() -> None:
    with pytest.raises(TaskBoardError, match="unknown task"):
        TaskStore().get("T-9")


def test_update_replaces_the_given_fields_only() -> None:
    store = TaskStore([_task("T-1", assignee="ada")])
    updated = store.update("T-1", status=TaskStatus.DONE)
    assert updated.status is TaskStatus.DONE
    assert updated.assignee == "ada"
    assert updated.title == "Ship the release"


def test_remove_deletes_the_task() -> None:
    store = TaskStore([_task("T-1")])
    store.remove("T-1")
    assert store.list() == []


def test_remove_rejects_an_unknown_identity() -> None:
    with pytest.raises(TaskBoardError, match="unknown task"):
        TaskStore().remove("T-9")


def test_labels_are_normalised() -> None:
    task = _task(labels=(" Backend ", "RELEASE", ""))
    assert task.labels == ("backend", "release")
    assert task.has_label("BACKEND")
    assert not task.has_label("frontend")


def test_a_blank_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="task id"):
        Task(id=" ", title="x")


def test_a_blank_title_is_rejected() -> None:
    with pytest.raises(ValueError, match="task title"):
        Task(id="T-1", title=" ")


def test_a_negative_estimate_is_rejected() -> None:
    with pytest.raises(ValueError, match="estimate"):
        Task(id="T-1", title="x", estimate=-1)


def test_save_and_load_round_trip(tmp_path) -> None:
    store = TaskStore([_task("T-1", labels=("backend",), status=TaskStatus.IN_PROGRESS)])
    target = tmp_path / "board.json"
    store.save(target)
    assert json.loads(target.read_text(encoding="utf-8"))["tasks"][0]["labels"] == ["backend"]
    loaded = TaskStore.load(target)
    assert loaded.get("T-1").labels == ("backend",)
    assert loaded.get("T-1").status is TaskStatus.IN_PROGRESS


def test_load_rejects_a_missing_file(tmp_path) -> None:
    with pytest.raises(TaskBoardError, match="not found"):
        TaskStore.load(tmp_path / "missing.json")
