"""Command line entry point of the SDD context module.

    python -m minimax_ctx init
    python -m minimax_ctx import --repo-root .
    python -m minimax_ctx context --task "..."
    python -m minimax_ctx save-state --task "..." --done "..." --todo "..."
    python -m minimax_ctx load-state --task "..."
    python -m minimax_ctx health
    python -m minimax_ctx selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from minimax_ctx.config import Settings
from minimax_ctx.context import build_context
from minimax_ctx.importer import MANIFEST_NAME, Manifest, collect_materials, find_repo_root
from minimax_ctx.materials import MaterialStore
from minimax_ctx.powercontext_state import PowerContextState, TaskState


def _repo_root(args: argparse.Namespace) -> Path:
    if getattr(args, "repo_root", None):
        return Path(args.repo_root).resolve()
    return find_repo_root(Path(__file__).resolve().parent)


def _manifest_path(args: argparse.Namespace) -> Path:
    root = _repo_root(args)
    explicit = getattr(args, "manifest", None)
    return Path(explicit).resolve() if explicit else root / "minimax" / MANIFEST_NAME


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def command_init(args: argparse.Namespace) -> int:
    settings = Settings()
    store = MaterialStore(settings)
    store.ensure_schema()
    print(f"seekdb schema ready in database {settings.seekdb_database!r}")
    return 0


def command_import(args: argparse.Namespace) -> int:
    settings = Settings()
    root = _repo_root(args)
    manifest_path = _manifest_path(args)
    manifest = Manifest.load(manifest_path)
    materials = collect_materials(root, manifest)
    store = MaterialStore(settings)
    store.ensure_schema()
    written = store.replace_all(materials)
    print(f"imported {written} material units into {settings.seekdb_database}.material")
    counts: dict[str, int] = {}
    for material in materials:
        counts[material.kind] = counts.get(material.kind, 0) + 1
    for kind, count in sorted(counts.items()):
        print(f"  {kind}: {count}")
    return 0


def command_context(args: argparse.Namespace) -> int:
    settings = Settings()
    store = MaterialStore(settings)
    state = None
    if args.with_state:
        state = PowerContextState(settings).load(args.task)
    pack = build_context(store, task=args.task, query=args.query, state=state)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(pack.to_markdown(), encoding="utf-8")
        print(f"wrote {args.markdown}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(pack.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    if not args.markdown and not args.json:
        print(pack.to_markdown())
    return 0


def _merge(previous: list[str], current: list[str]) -> list[str]:
    """Merge two progress lists, keeping the order and dropping the repeats."""

    merged = list(previous)
    for item in current:
        if item not in merged:
            merged.append(item)
    return merged


def _verification(current: list[str], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge verification records, accepting them as JSON or as plain text."""

    merged = list(previous)
    for raw in current:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            entry = {"name": raw, "status": "recorded"}
        if not isinstance(entry, dict):
            entry = {"name": str(entry), "status": "recorded"}
        if entry not in merged:
            merged.append(entry)
    return merged


def command_save_state(args: argparse.Namespace) -> int:
    settings = Settings()
    previous = PowerContextState(settings).load(args.task)
    state = TaskState(
        task=args.task,
        phase=args.phase or (previous.phase if previous else "planned"),
        done=_merge(previous.done if previous else [], args.done or []),
        todo=_merge(previous.todo if previous else [], args.todo or []),
        verification=_verification(args.verification or [], previous.verification if previous else []),
        context_digest=args.context_digest or (previous.context_digest if previous else ""),
        updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    response = PowerContextState(settings).save(state)
    print(f"saved task state through PowerContext for {args.task!r}")
    print(json.dumps(getattr(response, "model_dump", lambda: {})(), ensure_ascii=False, default=str)[:600])
    return 0


def command_load_state(args: argparse.Namespace) -> int:
    settings = Settings()
    state = PowerContextState(settings).load(args.task)
    if state is None:
        print(f"no saved state for {args.task!r}")
        return 1
    _print_json(
        {
            "task": state.task,
            "phase": state.phase,
            "done": state.done,
            "todo": state.todo,
            "verification": state.verification,
            "context_digest": state.context_digest,
            "updated_at": state.updated_at,
        }
    )
    return 0


def command_list_state(args: argparse.Namespace) -> int:
    settings = Settings()
    states = PowerContextState(settings).list_states()
    _print_json(
        [
            {
                "task": state.task,
                "phase": state.phase,
                "done": state.done,
                "todo": state.todo,
                "updated_at": state.updated_at,
            }
            for state in states
        ]
    )
    return 0


def command_health(args: argparse.Namespace) -> int:
    settings = Settings()
    report: dict[str, Any] = {"seekdb": {}, "powercontext": {}}
    try:
        store = MaterialStore(settings)
        report["seekdb"] = {
            "host": settings.seekdb_host,
            "port": settings.seekdb_port,
            "database": settings.seekdb_database,
            "materials": store.count(),
        }
    except Exception as error:  # noqa: BLE001 - health output reports the failure
        report["seekdb"] = {"error": f"{type(error).__name__}: {error}"}
    report["powercontext"] = {
        "url": settings.powercontext_url,
        "scope": settings.powercontext_scope,
        **PowerContextState(settings).health(),
    }
    _print_json(report)
    return 0 if "error" not in report["seekdb"] else 1


def command_selftest(args: argparse.Namespace) -> int:
    """Check that every component of the flow answers."""

    settings = Settings()
    store = MaterialStore(settings)
    failures: list[str] = []

    try:
        total = store.count()
        if total <= 0:
            failures.append("seekdb holds no material; run `import` first")
    except Exception as error:  # noqa: BLE001
        total = 0
        failures.append(f"seekdb is not reachable: {type(error).__name__}: {error}")

    health = PowerContextState(settings).health()
    if "error" in health:
        failures.append(f"PowerContext is not reachable: {health['error']}")

    payload: dict[str, Any] = {"materials": total, "powercontext": health, "failures": failures}
    _print_json(payload)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minimax-ctx", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create the seekdb material table and its full-text index.")
    init_parser.set_defaults(handler=command_init)

    import_parser = subparsers.add_parser("import", help="Import the declared material into seekdb.")
    import_parser.add_argument("--repo-root", default=None)
    import_parser.add_argument("--manifest", default=None)
    import_parser.set_defaults(handler=command_import)

    context_parser = subparsers.add_parser("context", help="Build the task-scoped context pack.")
    context_parser.add_argument("--task", required=True)
    context_parser.add_argument("--query", default=None)
    context_parser.add_argument("--with-state", action="store_true", help="Attach the saved PowerContext task state.")
    context_parser.add_argument("--markdown", default=None)
    context_parser.add_argument("--json", default=None)
    context_parser.set_defaults(handler=command_context)

    save_parser = subparsers.add_parser("save-state", help="Save task progress through PowerContext.")
    save_parser.add_argument("--task", required=True)
    save_parser.add_argument("--phase", default=None)
    save_parser.add_argument("--done", action="append", default=[])
    save_parser.add_argument("--todo", action="append", default=[])
    save_parser.add_argument("--verification", action="append", default=[])
    save_parser.add_argument("--context-digest", default=None)
    save_parser.set_defaults(handler=command_save_state)

    load_parser = subparsers.add_parser("load-state", help="Read the saved task progress back.")
    load_parser.add_argument("--task", required=True)
    load_parser.set_defaults(handler=command_load_state)

    list_parser = subparsers.add_parser("list-state", help="List every saved task state.")
    list_parser.set_defaults(handler=command_list_state)

    health_parser = subparsers.add_parser("health", help="Report component availability.")
    health_parser.set_defaults(handler=command_health)

    selftest_parser = subparsers.add_parser("selftest", help="Check that the whole flow answers.")
    selftest_parser.set_defaults(handler=command_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as error:  # noqa: BLE001 - the CLI reports the failure
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
