#!/usr/bin/env python3
"""DEV-ONLY — Clear user_story_state for a profile so stories become eligible again.

Use this during development to reset a profile after testing a bulletin, so the
next manifest request can assemble fresh content from the same story pool.

WARNING: Do NOT run this against a production database. It permanently removes
all story consumption history for the given profile, breaking the dedup guarantee.

Usage:
    cd backend && .venv/bin/python3.11 src/core/pipeline/devtools/reset_profile.py
    cd backend && .venv/bin/python3.11 src/core/pipeline/devtools/reset_profile.py --profile-id 2
"""

from __future__ import annotations

import argparse
import os
import sys

_BACKEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../../")
)
os.chdir(_BACKEND_DIR)

from sqlalchemy import text  # noqa: E402

from core.platform.db.session import SessionLocal  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="DEV ONLY: reset user_story_state for a profile")
    parser.add_argument("--profile-id", type=int, default=1, metavar="N")
    args = parser.parse_args()

    profile_id = args.profile_id

    with SessionLocal() as db:
        result = db.execute(
            text("DELETE FROM data.user_story_state WHERE profile_id = :pid"),
            {"pid": profile_id},
        )
        db.commit()
        count = result.rowcount

    print(f"Cleared {count} rows for profile_id {profile_id}.")


if __name__ == "__main__":
    main()
