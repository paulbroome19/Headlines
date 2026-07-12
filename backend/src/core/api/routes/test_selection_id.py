"""L-B: the selection identity (run:hash) threaded through the load artifacts, and the LOUD guard
that a served bulletin must belong to the user's PINNED edition selection — including the warmer /
cached path (the rider: never a silent grandfather clause).

Pure functions; no DB.
"""
import pytest
from fastapi import HTTPException
from core.pipeline.data.bulletin.selector import (
    selection_token,
    parse_selection_run,
    parse_selection_token,
    resolve_selection_pin,
)
from core.api.routes.data import assert_same_selection, assert_event_selection


def test_selection_token_is_run_and_hash():
    assert selection_token(7418, "c7a5b5bdb9ee") == "7418:c7a5b5bdb9ee"
    # deterministic + distinguishes run drift for the same filters
    assert selection_token(7418, "abc") != selection_token(7448, "abc")


def test_matching_selection_passes():
    assert_same_selection(served_run=7418, pinned_run=7418, request_hash="h", where="warm/cached serve") is None


def test_no_edition_is_a_noop_not_a_raise():
    # create-time warmer: no edition yet → pinned_run is None → nothing to cross-check.
    assert_same_selection(served_run=7418, pinned_run=None, request_hash="h", where="warm/cached serve") is None


def test_cross_selection_serve_raises_loudly():
    # The rider: a warm bulletin (run 7448) served against an edition pinned to 7418 must fail loudly.
    with pytest.raises(RuntimeError, match=r"selection mismatch \[warm/cached serve\]: served 7448:h != edition pinned 7418:h"):
        assert_same_selection(served_run=7448, pinned_run=7418, request_hash="h", where="warm/cached serve")


# ── L-D: parse the tapped selection's run, and reject stale-selection events ──

def test_parse_selection_run():
    assert parse_selection_run("7418:c7a5b5bdb9ee") == 7418
    assert parse_selection_run(None) is None
    assert parse_selection_run("garbage") is None
    assert parse_selection_run("notanint:hash") is None


def test_parse_selection_token_full_run_and_hash():
    assert parse_selection_token("7418:c7a5b5bdb9ee") == (7418, "c7a5b5bdb9ee")
    assert parse_selection_token(None) is None
    assert parse_selection_token("garbage") is None          # no ":"
    assert parse_selection_token("notanint:hash") is None    # bad run
    assert parse_selection_token("7418:") is None            # empty hash
    # A hash containing ":" (won't happen for a hex hash, but split must not drop it)
    assert parse_selection_token("7418:a:b") == (7418, "a:b")


# ── L-D completed: honor the FULL pin, or explicitly refresh a stale one ──────────
# resolve_selection_pin is the pure decision the manifest makes before any IO. It returns
# (request_hash_to_serve, honor_pin, selection_refreshed).

def test_ld_honors_pin_when_edition_exists():
    # The core fix. Home pinned edition H_A (it materialised it, so it EXISTS). Between render and
    # tap the profile filters changed, so the manifest would recompute current = H_B. We must serve
    # EXACTLY the board's edition H_A — never the recomputed H_B (that was the divergence).
    served, honor, refreshed = resolve_selection_pin(
        (7418, "AAAA"), current_hash="BBBB", pinned_edition_exists=True,
    )
    assert (served, honor, refreshed) == ("AAAA", True, False)


def test_ld_filter_change_mid_tap_serves_the_pinned_board_not_the_recompute():
    # Same as above stated as the requirement: a filter change between viewing home and tapping play
    # never silently swaps the order — the tapped board (H_A) is the contract.
    served, honor, _ = resolve_selection_pin(
        (7418, "board_hash"), current_hash="changed_hash", pinned_edition_exists=True,
    )
    assert honor is True and served == "board_hash"


def test_ld_day_boundary_same_day_honors_same_edition():
    # Home at 22:59 UTC and manifest at 22:59:30 UTC — same UK day, so current == the pinned hash and
    # the edition still exists → honor, no refresh, same order both sides.
    served, honor, refreshed = resolve_selection_pin(
        (7418, "H_DAY"), current_hash="H_DAY", pinned_edition_exists=True,
    )
    assert (served, honor, refreshed) == ("H_DAY", True, False)


def test_ld_day_boundary_roll_refreshes_explicitly_never_silent():
    # Home at 22:59 UTC (UK day D → hash H_D). Manifest at 23:01 UTC (UK day D+1 → current H_D1). The
    # request_hash folds the day in, so H_D != H_D1, and there is NO edition under today's date for
    # H_D → we CANNOT serve the same edition. Refresh to the CURRENT edition and FLAG it (loud) — the
    # served hash is current, never a silent serve of yesterday's order under the client's stale pin.
    served, honor, refreshed = resolve_selection_pin(
        (7418, "H_D"), current_hash="H_D1", pinned_edition_exists=False,
    )
    assert served == "H_D1"          # explicit refresh to today's edition
    assert honor is False
    assert refreshed is True         # client learns → re-syncs to a fresh preview state


def test_ld_degrade_no_selection_id_is_todays_behaviour():
    # No pin (older client / warmer without a preview): recompute from the profile, no refresh flag.
    served, honor, refreshed = resolve_selection_pin(
        None, current_hash="CURRENT", pinned_edition_exists=False,
    )
    assert (served, honor, refreshed) == ("CURRENT", False, False)


def test_ld_degrade_garbage_selection_id_is_todays_behaviour():
    # Garbage parses to None → identical to the no-pin degrade path (graceful, unchanged).
    pin = parse_selection_token("garbage")
    served, honor, refreshed = resolve_selection_pin(
        pin, current_hash="CURRENT", pinned_edition_exists=False,
    )
    assert (served, honor, refreshed) == ("CURRENT", False, False)


def test_ld_cold_play_first_pin_matches_current_no_spurious_refresh():
    # Cold play-first: the client sent a pin whose hash already equals current, but no edition exists
    # yet (play before preview materialised). Must NOT flag a refresh — the hashes agree; the run is
    # still usable for the cache probe (honor_pin False, but selection_refreshed False).
    served, honor, refreshed = resolve_selection_pin(
        (7418, "CURRENT"), current_hash="CURRENT", pinned_edition_exists=False,
    )
    assert (served, honor, refreshed) == ("CURRENT", False, False)


def test_event_selection_match_and_no_id_pass():
    assert_event_selection("7418:h", "7418:h") is None          # match
    assert_event_selection(None, "7418:h") is None              # older client → backward compatible


def test_stale_event_selection_raises_409():
    # Client still on selection 7418 flushing against a bulletin now on 7448 → loud 409.
    with pytest.raises(HTTPException) as ei:
        assert_event_selection("7418:h", "7448:h")
    assert ei.value.status_code == 409
