"""Command line front end of the task board."""

from __future__ import annotations

import argparse
import json

from taskboard.filters import filter_tasks, summarize
from taskboard.model import TaskStatus
from taskboard.store import TaskBoardError, TaskStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskboard", description="Manage one task board.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List the tasks of the board.")
    list_parser.add_argument("--board", default=None, help="Path of the board JSON file.")
    list_parser.add_argument("--status", choices=[str(status) for status in TaskStatus], default=None)
    list_parser.add_argument("--assignee", default=None)
    list_parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="Only list the tasks carrying this label; repeat for several labels.",
    )
    list_parser.add_argument("--json", action="store_true", help="Write the tasks as JSON.")

    summary_parser = subparsers.add_parser("summary", help="Count the tasks per status.")
    summary_parser.add_argument("--board", default=None, help="Path of the board JSON file.")
    summary_parser.add_argument("--json", action="store_true", help="Write the summary as JSON.")

    return parser


def _load_board(path: str | None) -> TaskStore:
    if path is None:
        return TaskStore()
    return TaskStore.load(path)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        store = _load_board(args.board)
        if args.command == "list":
            tasks = filter_tasks(
                store.list(),
                status=TaskStatus(args.status) if args.status else None,
                assignee=args.assignee,
                labels=args.label or None,
            )
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                "id": task.id,
                                "title": task.title,
                                "status": str(task.status),
                                "assignee": task.assignee,
                                "labels": list(task.labels),
                                "estimate": task.estimate,
                            }
                            for task in tasks
                        ],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                for task in tasks:
                    print(f"{task.id}\t{task.status}\t{task.title}")
            return 0
        if args.command == "summary":
            summary = summarize(store.list())
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                for status, count in sorted(summary.items()):
                    print(f"{status}\t{count}")
            return 0
    except TaskBoardError as error:
        print(f"error: {error}")
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
