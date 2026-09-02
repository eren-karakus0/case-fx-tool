#!/usr/bin/env bash
# Starts the service. It listens on $PORT (default 8080). The upstream base URL
# is read from $FX_UPSTREAM_BASE inside the application, so nothing here needs
# to know it, and nothing here hardcodes it.
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

exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
