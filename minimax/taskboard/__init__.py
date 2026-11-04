"""Task board: a small business project used to demonstrate the SDD context module.

The package is deliberately small but complete: a domain model, a store, a
filter layer and a command line front end, all covered by tests.  It is the
project whose engineering material the context module stores in seekdb and
retrieves for a development task.
"""

from __future__ import annotations

from taskboard.model import Task, TaskStatus
from taskboard.store import TaskBoardError, TaskStore

__all__ = ["Task", "TaskBoardError", "TaskStatus", "TaskStore"]
