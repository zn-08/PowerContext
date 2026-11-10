#!/usr/bin/env bash
# Start the memory service of the "npc-memory-chatflow" Dify application.
#
# It installs the single dependency, seeds the demo database and then serves
# the two endpoints the workflow calls: POST /context and POST /memory.
#
# Every setting can be overridden with an environment variable; the defaults
# match a seekdb that listens on 127.0.0.1:2881 with the root account and an
# empty password.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

export SEEKDB_HOST="${SEEKDB_HOST:-127.0.0.1}"
export SEEKDB_PORT="${SEEKDB_PORT:-2881}"
export SEEKDB_USER="${SEEKDB_USER:-root}"
export SEEKDB_PASSWORD="${SEEKDB_PASSWORD:-}"
export SEEKDB_DATABASE="${SEEKDB_DATABASE:-npc_memory_demo}"
export SERVICE_HOST="${SERVICE_HOST:-0.0.0.0}"
export SERVICE_PORT="${SERVICE_PORT:-8808}"

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

"$PYTHON" -m pip install --quiet -r requirements.txt

echo "[npc] seeding ${SEEKDB_DATABASE} on ${SEEKDB_HOST}:${SEEKDB_PORT} ..."
"$PYTHON" seed.py

echo "[npc] serving on http://${SERVICE_HOST}:${SERVICE_PORT} (this is the address baked into the DSL)"
exec "$PYTHON" app.py
