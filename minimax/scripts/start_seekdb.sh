#!/usr/bin/env bash
# Start a seekdb instance for the SDD context module.
#
# The module stores the engineering material in seekdb, so a seekdb server has
# to be running before the demo starts.  Any seekdb build works; the version
# used while developing this material was seekdb v1.3.0.0.
#
# Usage:
#   ./scripts/start_seekdb.sh [base_dir] [port]

set -euo pipefail

BASE_DIR="${1:-./seekdb_run}"
PORT="${2:-2881}"
SEEKDB_BIN="${SEEKDB_BIN:-seekdb}"

if ! command -v "${SEEKDB_BIN}" >/dev/null 2>&1; then
  echo "seekdb binary not found: ${SEEKDB_BIN}" >&2
  echo "Set SEEKDB_BIN to the seekdb executable, for example:" >&2
  echo "  SEEKDB_BIN=/opt/seekdb/bin/seekdb ./scripts/start_seekdb.sh" >&2
  exit 1
fi

mkdir -p "${BASE_DIR}"
"${SEEKDB_BIN}" --base-dir="${BASE_DIR}" --port="${PORT}" --nodaemon &
echo "seekdb is starting on port ${PORT} (data directory ${BASE_DIR})"
echo "export SEEKDB_PORT=${PORT}"
