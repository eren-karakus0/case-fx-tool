#!/usr/bin/env bash
# Runs the tests. Nothing here opens a socket: every upstream response the tests
# need is served by an in-process httpx.MockTransport, so this passes with
# $FX_UPSTREAM_BASE pointing at a closed port, or with no network at all.
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

exec "$PY" -m pytest -q
