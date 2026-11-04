"""Regression tests for the task board command line front end.

These tests protect the behaviour that exists before the label filter change.
They must pass both before and after that change.
"""

from __future__ import annotations

import json

from taskboard.cli import main
from taskboard.model import Task, TaskStatus
from taskboard.store import TaskStore


def _board(tmp_path) -> str:
    store = TaskStore(
        [
            Task(id="T-1", title="Write the spec", status=TaskStatus.TODO, assignee="ada"),
            Task(id="T-2", title="Implement the filter", status=TaskStatus.IN_PROGRESS, assignee="bo"),
            Task(id="T-3", title="Release", status=TaskStatus.DONE, assignee="ada"),
        ]
    )
    target = tmp_path / "board.json"
    store.save(target)
    return str(target)


def test_list_prints_every_task(tmp_path, capsys) -> None:
    assert main(["list", "--board", _board(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "T-1" in printed
    assert "T-3" in printed


def test_list_filters_by_status(tmp_path, capsys) -> None:
    assert main(["list", "--board", _board(tmp_path), "--status", "done"]) == 0
    printed = capsys.readouterr().out
    assert "T-3" in printed
    assert "T-1" not in printed


def test_list_filters_by_assignee(tmp_path, capsys) -> None:
    assert main(["list", "--board", _board(tmp_path), "--assignee", "ada"]) == 0
    printed = capsys.readouterr().out
    assert "T-1" in printed
    assert "T-2" not in printed


def test_list_writes_json(tmp_path, capsys) -> None:
    assert main(["list", "--board", _board(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["id"] for entry in payload] == ["T-1", "T-2", "T-3"]


def test_summary_counts_every_status(tmp_path, capsys) -> None:
    assert main(["summary", "--board", _board(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["todo"] == 1
    assert payload["in_progress"] == 1
    assert payload["done"] == 1


def test_a_missing_board_file_is_reported(tmp_path, capsys) -> None:
    assert main(["list", "--board", str(tmp_path / "missing.json")]) == 1
    assert "not found" in capsys.readouterr().out
