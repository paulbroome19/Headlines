#!/usr/bin/env bash
set -e

BACKEND="$(cd "$(dirname "$0")/../backend" && pwd)"
cd "$BACKEND"

echo "Running pull_stories pipeline..."
echo ""

.venv/bin/python3.11 src/core/pipeline/devtools/pull_stories.py

echo ""
echo "Done. Press any key to close..."
read -n 1 -s
