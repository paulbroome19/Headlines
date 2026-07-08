"""Contract test: the /summary batch payload iOS actually sends MUST validate against the endpoint
schema. The build-45 regression was exactly this drift — the batch reused BulletinEventRequest
(profile_id REQUIRED per event) while iOS sends profile_id only at the top level, so every flush
422'd and silently recorded nothing. A test like this would have caught it on day one.

If iOS changes its flush payload (ios/.../BulletinPlayer.swift SummaryRequest / EventItem), update
the fixtures below in lockstep — that is the point of the contract.
"""
import pytest
from pydantic import ValidationError

from core.api.routes.data import BulletinSummaryRequest

# The EXACT JSON the iOS BulletinPlayer flush encodes today (build 45: no per-event profile_id).
IOS_FLUSH_BUILD45 = {
    "profile_id": 22,
    "events": [
        {"story_hash": "560756d8909610bb", "action": "skipped", "position_pct": 0.0},
        {"story_hash": "8ae3c2e3b688fe13", "action": "completed", "position_pct": 1.0},
    ],
}

# Build 46 (#185) adds story_id per event.
IOS_FLUSH_BUILD46 = {
    "profile_id": 22,
    "events": [
        {"story_hash": "560756d8909610bb", "story_id": "240779", "action": "skipped", "position_pct": 0.0},
    ],
}


def test_endpoint_accepts_ios_build45_payload():
    req = BulletinSummaryRequest.model_validate(IOS_FLUSH_BUILD45)
    assert req.profile_id == 22
    assert [e.action for e in req.events] == ["skipped", "completed"]
    assert req.events[0].story_id is None          # build 45 doesn't send it — must still validate


def test_endpoint_accepts_ios_build46_payload_with_story_id():
    req = BulletinSummaryRequest.model_validate(IOS_FLUSH_BUILD46)
    assert req.events[0].story_id == "240779"


def test_batch_events_do_not_require_per_event_profile_id():
    # THE regression guard: requiring profile_id on each event is what 422'd every iOS flush.
    req = BulletinSummaryRequest.model_validate(
        {"profile_id": 22, "events": [{"story_hash": "h", "action": "skipped"}]}
    )
    assert req.events[0].profile_id is None


def test_optional_per_event_profile_id_is_accepted_and_ignored():
    # Belt-and-braces: a future client that DOES send per-event profile_id still validates.
    req = BulletinSummaryRequest.model_validate(
        {"profile_id": 22, "events": [{"story_hash": "h", "action": "skipped", "profile_id": 22}]}
    )
    assert len(req.events) == 1


def test_top_level_profile_id_still_required():
    with pytest.raises(ValidationError):
        BulletinSummaryRequest.model_validate({"events": [{"story_hash": "h", "action": "skipped"}]})
