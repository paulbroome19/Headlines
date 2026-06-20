#!/usr/bin/env bash
set -e

BACKEND="$(cd "$(dirname "$0")/../backend" && pwd)"
cd "$BACKEND"
./run.sh
