"""L-C server gate: safe_to_start must NEVER green-light audio whose opener isn't the edition's
committed lead. Pure decision function; the 240779≠415760 incident is the failing input.
"""
from core.api.routes.data import gate_opener_ok


def test_opener_matches_committed_lead_ok():
    assert gate_opener_ok("415760", "415760") is True


def test_incident_mismatch_blocks():
    # The incident: opener synthesised for 240779, edition committed lead is 415760 → BLOCK.
    assert gate_opener_ok("240779", "415760") is False


def test_no_committed_lead_is_backward_compatible_noop():
    # No edition / no profile_id → nothing to enforce (today's behaviour).
    assert gate_opener_ok("240779", None) is True
    assert gate_opener_ok(None, "415760") is True


def test_int_str_coercion():
    assert gate_opener_ok(415760, "415760") is True
    assert gate_opener_ok("240779", 415760) is False
