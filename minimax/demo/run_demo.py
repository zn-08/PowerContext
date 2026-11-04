#!/usr/bin/env python3
"""Run the verification sample of the MiniMax SDD context module.

The sample walks the whole flow of the task:

1. check that seekdb and PowerContext answer,
2. import the engineering material into seekdb,
3. build the task-scoped context pack out of it,
4. save the task state through PowerContext,
5. read that state back from a *new* module process,
6. run the target test and the regression suite,
7. write the verification results back into the task state.

Usage:
    python demo/run_demo.py [--task "<task description>"] [--repo-root <path>]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_ROOT = HERE.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

DEFAULT_TASK = "为任务看板增加按标签筛选任务的能力"
EVIDENCE_DIR = MODULE_ROOT / "docs" / "evidence"


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = f"{MODULE_ROOT}{os.pathsep}{existing}" if existing else str(MODULE_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "minimax_ctx", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        cwd=str(MODULE_ROOT),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--repo-root", default=str(MODULE_ROOT.parent))
    parser.add_argument("--skip-tests", action="store_true", help="Do not run the test suites.")
    arguments = parser.parse_args()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"task": arguments.task, "steps": []}

    print("== 1. health ==")
    health = _run(["health"])
    print(health.stdout.strip() or health.stderr.strip())
    report["health"] = health.stdout.strip()
    if health.returncode != 0:
        print("seekdb is not reachable; see the run instructions in minimax/README.md", file=sys.stderr)
        return 1

    print("\n== 2. import the engineering material into seekdb ==")
    imported = _run(["import", "--repo-root", arguments.repo_root])
    print(imported.stdout.strip() or imported.stderr.strip())
    report["import"] = imported.stdout.strip()
    if imported.returncode != 0:
        return 1

    print("\n== 3. build the context pack ==")
    context_md = EVIDENCE_DIR / "context-pack.md"
    context_json = EVIDENCE_DIR / "context-pack.json"
    built = _run(
        [
            "context",
            "--task",
            arguments.task,
            "--markdown",
            str(context_md),
            "--json",
            str(context_json),
        ]
    )
    print(built.stdout.strip() or built.stderr.strip())
    if built.returncode != 0:
        return 1
    pack = json.loads(context_json.read_text(encoding="utf-8"))
    report["context_sources"] = pack["sources"]
    report["context_digest"] = pack["context_digest"]

    print("\n== 4. save the task state through PowerContext ==")
    saved = _run(
        [
            "save-state",
            "--task",
            arguments.task,
            "--phase",
            "context-ready",
            "--done",
            f"从 seekdb 检索到 {len(pack['specifications'])} 条规格、{len(pack['code'])} 处代码、{len(pack['tests'])} 处测试",
            "--todo",
            "按规格实现 taskboard.filters.filter_tasks 的 labels 条件",
            "--context-digest",
            pack["context_digest"],
        ]
    )
    print(saved.stdout.strip() or saved.stderr.strip())
    report["save_state"] = saved.returncode == 0

    print("\n== 5. read the state back from a new module process ==")
    loaded = _run(["load-state", "--task", arguments.task])
    print(loaded.stdout.strip() or loaded.stderr.strip())
    if loaded.returncode != 0:
        print("the saved state could not be read back", file=sys.stderr)
        return 1
    state = json.loads(loaded.stdout)
    report["reloaded_state"] = state
    if not state.get("done"):
        print("the reloaded state carries no progress", file=sys.stderr)
        return 1

    if not arguments.skip_tests:
        print("\n== 6. run the target test and the regression suite ==")
        target = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_filters.py", "-q"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(MODULE_ROOT),
        )
        regression = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_store.py", "tests/test_cli.py", "-q"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(MODULE_ROOT),
        )
        print(target.stdout.strip())
        print(regression.stdout.strip())
        report["target_test"] = {"exit_code": target.returncode, "output": target.stdout.strip()}
        report["regression_test"] = {"exit_code": regression.returncode, "output": regression.stdout.strip()}
        (EVIDENCE_DIR / "target-test.txt").write_text(target.stdout, encoding="utf-8")
        (EVIDENCE_DIR / "regression-test.txt").write_text(regression.stdout, encoding="utf-8")

        print("\n== 7. write the verification results back into the task state ==")
        verification = [
            json.dumps(
                {
                    "name": "target test",
                    "command": "python -m pytest tests/test_filters.py -q",
                    "status": "passed" if target.returncode == 0 else "failed",
                    "exit_code": target.returncode,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "name": "regression suite",
                    "command": "python -m pytest tests/test_store.py tests/test_cli.py -q",
                    "status": "passed" if regression.returncode == 0 else "failed",
                    "exit_code": regression.returncode,
                },
                ensure_ascii=False,
            ),
        ]
        finalised = _run(
            [
                "save-state",
                "--task",
                arguments.task,
                "--phase",
                "verified" if target.returncode == 0 and regression.returncode == 0 else "failing",
                "--verification",
                verification[0],
                "--verification",
                verification[1],
            ]
        )
        print(finalised.stdout.strip() or finalised.stderr.strip())

    (EVIDENCE_DIR / "demo-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"\nevidence written to {EVIDENCE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
