"""L-B: the selection identity (run:hash) threaded through the load artifacts, and the LOUD guard
that a served bulletin must belong to the user's PINNED edition selection — including the warmer /
cached path (the rider: never a silent grandfather clause).

Pure functions; no DB.
"""
import pytest
from fastapi import HTTPException
from core.pipeline.data.bulletin.selector import selection_token, parse_selection_run
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


def test_event_selection_match_and_no_id_pass():
    assert_event_selection("7418:h", "7418:h") is None          # match
    assert_event_selection(None, "7418:h") is None              # older client → backward compatible


def test_stale_event_selection_raises_409():
    # Client still on selection 7418 flushing against a bulletin now on 7448 → loud 409.
    with pytest.raises(HTTPException) as ei:
        assert_event_selection("7418:h", "7448:h")
    assert ei.value.status_code == 409
