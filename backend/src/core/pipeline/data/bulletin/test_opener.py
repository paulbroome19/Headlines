"""Unit tests for the lead-opener split."""
from __future__ import annotations

from core.pipeline.data.bulletin.opener import (
    split_lead_opener, apply_lead_opener, OPENER_TARGET_WORDS,
)

LEAD = (
    "A hundred and twenty thousand PlayStation owners have signed a petition against Sony's "
    "plan to kill the disc. Here's what's happening: Sony announced it's moving away from "
    "physical game discs on its next consoles. The company said it would stop supporting "
    "disc-based games after twenty twenty-seven. It's not the full reversal gamers hoped for. "
    "The petition's core message is clear. Industry experts see this as being about control. "
    "There is also a practical issue for players with slow connections."
)


def test_split_on_sentence_boundary_reaches_target():
    opener, remainder = split_lead_opener(LEAD)
    assert opener and remainder
    # opener ends on a sentence boundary (no mid-sentence cut)
    assert opener.rstrip()[-1] in ".!?"
    # opener reaches ~target words (or the sentence that crosses it), remainder keeps the rest
    assert len(opener.split()) >= OPENER_TARGET_WORDS - 25
    # concatenation preserves all words (nothing dropped)
    assert opener.split() + remainder.split() == LEAD.split()


def test_at_least_one_sentence_each_side():
    opener, remainder = split_lead_opener(LEAD)
    assert opener.count(".") >= 1
    assert remainder.strip()


def test_single_word_is_unsplittable():
    assert split_lead_opener("Breaking") == ("Breaking", "")


def test_short_two_sentence_lead_still_splits():
    txt = "First sentence here. Second sentence follows."
    opener, remainder = split_lead_opener(txt)
    assert opener == "First sentence here."
    assert remainder == "Second sentence follows."


def test_apply_lead_opener_splits_first_story_only():
    segs = [
        {"type": "intro", "text": "Evening."},
        {"type": "story", "story_id": "1", "text": LEAD, "title": "Lead"},
        {"type": "transition", "text": "Next."},
        {"type": "story", "story_id": "2", "text": "Second story body here.", "title": "Two"},
        {"type": "outro", "text": "Bye."},
    ]
    out = apply_lead_opener(segs)
    # one extra segment: the lead is now two story segments (opener + remainder)
    assert len(out) == len(segs) + 1
    opener, remainder = out[1], out[2]
    assert opener["lead_part"] == "opener" and remainder["lead_part"] == "remainder"
    assert opener["story_id"] == "1" and remainder["story_id"] == "1"
    assert opener["title"] == "Lead" and remainder["title"] == "Lead"
    # story 2 untouched, single segment
    assert out[4]["story_id"] == "2" and "lead_part" not in out[4]


def test_apply_lead_opener_idempotent():
    segs = [{"type": "story", "story_id": "1", "text": LEAD}]
    once = apply_lead_opener(segs)
    twice = apply_lead_opener(once)
    assert twice == once   # already tagged → no re-split
