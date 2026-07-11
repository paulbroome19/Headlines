"""L-B: the selection identity (run:hash) threaded through the load artifacts, and the LOUD guard
that a served bulletin must belong to the user's PINNED edition selection — including the warmer /
cached path (the rider: never a silent grandfather clause).

Pure functions; no DB.
"""
import pytest
from core.pipeline.data.bulletin.selector import selection_token
from core.api.routes.data import assert_same_selection


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
