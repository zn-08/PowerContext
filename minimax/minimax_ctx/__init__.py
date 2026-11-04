"""SDD context module for the MiniMax x OceanBase challenge.

The module keeps engineering material (specifications, code and tests) in seekdb,
builds a task-scoped context pack out of it, and persists task progress through
PowerContext so a later run can resume from the saved state.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
