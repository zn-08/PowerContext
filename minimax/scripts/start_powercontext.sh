#!/usr/bin/env bash
# Start a local PowerContext Server for the SDD context module.
#
# The module keeps the task state in PowerContext Memory, so a PowerContext
# Server has to be running before the demo starts.  The Server stores its own
# state in SQLite, which keeps the sample free of extra services.
#
# Usage:
#   ./scripts/start_powercontext.sh [port] [data_dir]
#
# The PowerContext package has to be importable.  Either install it
# (pip install powercontext) or point PYTHONPATH at a checkout of
# https://github.com/oceanbase/powercontext .

set -euo pipefail

PORT="${1:-8787}"
DATA_DIR="${2:-./powercontext_data}"
HOST="${POWERCONTEXT_HOST:-127.0.0.1}"

if ! python3 -c "import powercontext" >/dev/null 2>&1; then
  echo "the powercontext package is not importable" >&2
  echo "Install it, or export PYTHONPATH=<path to>/powercontext/src" >&2
  exit 1
fi

mkdir -p "${DATA_DIR}"
export POWERCONTEXT_SERVER_HTTP_HOST="${HOST}"
export POWERCONTEXT_SERVER_HTTP_PORT="${PORT}"
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///${DATA_DIR}/powercontext.db"

echo "PowerContext Server is starting on http://${HOST}:${PORT}"
echo "export POWERCONTEXT_URL=http://${HOST}:${PORT}"
exec python3 -c "from powercontext.server.cli import app; app()" run --host "${HOST}" --port "${PORT}" --no-env-file
