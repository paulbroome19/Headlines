#!/usr/bin/env bash
# DEV ONLY — clears all user_story_state rows for profile 1 (or --profile-id N).
# Use when testing the same bulletin repeatedly. Do NOT run against production.
set -e

BACKEND="$(cd "$(dirname "$0")/../backend" && pwd)"
cd "$BACKEND"

PROFILE_ID="${1:-1}"

echo "DEV: Resetting user_story_state for profile_id ${PROFILE_ID}..."
echo ""
.venv/bin/python3.11 src/core/pipeline/devtools/reset_profile.py --profile-id "$PROFILE_ID"
echo ""
echo "Done. Press any key to close..."
read -n 1 -s
