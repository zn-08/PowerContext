#!/usr/bin/env bash
# Reproduce the "before the change" result of the target test.
#
# The target test of the label filter did not exist before the change, so the
# script checks the baseline commit out into a separate worktree, copies the
# target test into it, and runs the test there.  The test then fails because
# the feature is missing -- not because of a broken environment.
#
# Usage:
#   ./scripts/reproduce_before.sh [baseline_commit] [target_commit]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASELINE_COMMIT="${1:-}"
TARGET_COMMIT="${2:-HEAD}"

if [[ -z "${BASELINE_COMMIT}" ]]; then
  BASELINE_COMMIT="$(git -C "${REPO_ROOT}" log --format=%H --reverse -- minimax/taskboard/filters.py | head -1)"
fi
if [[ -z "${BASELINE_COMMIT}" ]]; then
  echo "cannot determine the baseline commit; pass it as the first argument" >&2
  exit 2
fi

WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/minimax-before.XXXXXX")"
echo "baseline commit : ${BASELINE_COMMIT}"
echo "target commit   : ${TARGET_COMMIT}"
echo "worktree        : ${WORKTREE}"

git -C "${REPO_ROOT}" worktree add --detach "${WORKTREE}" "${BASELINE_COMMIT}" >/dev/null
trap 'git -C "${REPO_ROOT}" worktree remove --force "${WORKTREE}" >/dev/null 2>&1 || true' EXIT

git -C "${REPO_ROOT}" show "${TARGET_COMMIT}:minimax/tests/test_filters.py" > "${WORKTREE}/minimax/tests/test_filters.py"

cd "${WORKTREE}/minimax"
set +e
python3 -m pytest tests/test_filters.py -q
STATUS=$?
set -e

echo
echo "exit code: ${STATUS} (a non-zero code is the expected 'before' result)"
exit "${STATUS}"
