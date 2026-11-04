"""Target tests for the label filter of the task board.

Specification: ``minimax/specs/SPEC-TASKBOARD-LABEL-FILTER.md``.

Before the change these tests fail because ``filter_tasks`` does not accept a
``labels`` condition at all.  After the change every test in this file passes.
The regression suite (``test_store.py``, ``test_cli.py``) passes in both states.
"""

from __future__ import annotations

import json

from taskboard.cli import main
from taskboard.filters import filter_tasks
from taskboard.model import Task, TaskStatus


def _tasks() -> list[Task]:
    return [
        Task(id="T-1", title="Write the spec", status=TaskStatus.TODO, labels=("docs",)),
        Task(id="T-2", title="Implement the filter", status=TaskStatus.IN_PROGRESS, labels=("backend", "api")),
        Task(id="T-3", title="Release", status=TaskStatus.DONE, labels=("release",)),
    ]


def test_filter_tasks_selects_the_tasks_carrying_one_label() -> None:
    selected = filter_tasks(_tasks(), labels=["backend"])
    assert [task.id for task in selected] == ["T-2"]


def test_filter_tasks_matches_any_of_the_given_labels() -> None:
    selected = filter_tasks(_tasks(), labels=["backend", "release"])
    assert [task.id for task in selected] == ["T-2", "T-3"]


def test_filter_tasks_matches_a_task_carrying_several_labels() -> None:
    selected = filter_tasks(_tasks(), labels=["api"])
    assert [task.id for task in selected] == ["T-2"]


def test_filter_tasks_without_a_label_condition_keeps_every_task() -> None:
    assert [task.id for task in filter_tasks(_tasks())] == ["T-1", "T-2", "T-3"]


def test_filter_tasks_combines_the_label_and_the_status_condition() -> None:
    selected = filter_tasks(_tasks(), labels=["backend"], status=TaskStatus.IN_PROGRESS)
    assert [task.id for task in selected] == ["T-2"]


def test_filter_tasks_with_a_label_no_task_carries_returns_nothing() -> None:
    assert filter_tasks(_tasks(), labels=["frontend"]) == []


def test_filter_tasks_ignores_the_case_and_the_padding_of_a_label() -> None:
    selected = filter_tasks(_tasks(), labels=[" BACKEND "])
    assert [task.id for task in selected] == ["T-2"]


def test_the_command_line_lists_the_tasks_carrying_one_label(tmp_path, capsys) -> None:
    from taskboard.store import TaskStore

    store = TaskStore(_tasks())
    board = tmp_path / "board.json"
    store.save(board)

    assert main(["list", "--board", str(board), "--label", "backend"]) == 0
    printed = capsys.readouterr().out
    assert "T-2" in printed
    assert "T-1" not in printed


def test_the_command_line_accepts_several_labels(tmp_path, capsys) -> None:
    from taskboard.store import TaskStore

    store = TaskStore(_tasks())
    board = tmp_path / "board.json"
    store.save(board)

    assert main(["list", "--board", str(board), "--label", "backend", "--label", "release", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["id"] for entry in payload] == ["T-2", "T-3"]
