"""L-A load invariant: the synthesised opener MUST be the final lead — a loud raise, never a silent
serve. This pins the guard that replaced the old "LEAD SHIFTED … should be impossible" warning
(the board≠audio incident: start-pack opener 240779 served against final lead 415760).

Pure function; no DB / no TTS.
"""
import pytest
from core.api.routes.data import assert_opener_is_final_lead


def test_matching_opener_and_lead_passes():
    order = [{"story_id": "415760"}, {"story_id": "240779"}, {"story_id": "157456"}]
    # opener == final[0] → no raise
    assert_opener_is_final_lead(opener_story_id="415760", final_order=order, bulletin_id=241, ranking_run_id=7418) is None


def test_mismatch_raises_loudly():
    # The incident: opener synthesised for 240779, final order leads with 415760.
    order = [{"story_id": "415760"}, {"story_id": "240779"}]
    with pytest.raises(RuntimeError, match="opener lead 240779 != final lead 415760"):
        assert_opener_is_final_lead(opener_story_id="240779", final_order=order, bulletin_id=241, ranking_run_id=7418)


def test_str_int_coercion():
    # story_ids may arrive as int on one side, str on the other — compare as strings.
    order = [{"story_id": 415760}]
    assert_opener_is_final_lead(opener_story_id="415760", final_order=order, bulletin_id=1, ranking_run_id=1) is None
    with pytest.raises(RuntimeError):
        assert_opener_is_final_lead(opener_story_id="240779", final_order=order, bulletin_id=1, ranking_run_id=1)


def test_empty_sides_are_noops():
    assert_opener_is_final_lead(opener_story_id=None, final_order=[{"story_id": "X"}], bulletin_id=1, ranking_run_id=1) is None
    assert_opener_is_final_lead(opener_story_id="X", final_order=[], bulletin_id=1, ranking_run_id=1) is None
