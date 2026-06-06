#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Kill anything currently holding port 8000 so we never get stale-code
# or "address already in use" surprises.
if lsof -ti :8000 >/dev/null 2>&1; then
    echo "Killing existing process on port 8000..."
    lsof -ti :8000 | xargs kill -9
    sleep 0.5
fi

echo "Starting Headlines backend on http://0.0.0.0:8000 (--reload active)"
.venv/bin/python3.11 -m uvicorn core.api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --log-level info
