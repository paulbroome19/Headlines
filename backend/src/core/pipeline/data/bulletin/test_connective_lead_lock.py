"""
Connective lead lock (PR2): the LLM smooths transitions but cannot float a minor
story above a major one. _apply_lead_lock pins the first LEAD_PIN_COUNT slots to
importance rank and realigns transitions (blank any whose predecessor changed).
"""
from __future__ import annotations

from core.pipeline.data.bulletin.connective import _apply_lead_lock
from core.pipeline.ranking.config import LEAD_PIN_COUNT


def test_leads_pinned_to_rank_order():
    inp = ["A", "B", "C", "D", "E"]          # importance rank (A biggest)
    llm = ["E", "A", "B", "D", "C"]          # LLM floated minor story E to the lead
    order, _ = _apply_lead_lock(inp, llm, {sid: "x" for sid in inp})
    assert order[:LEAD_PIN_COUNT] == inp[:LEAD_PIN_COUNT]   # A,B,C lead, in rank order
    assert set(order) == set(inp)                            # still a permutation
    assert order[LEAD_PIN_COUNT:] == ["E", "D"]              # tail keeps LLM's relative order


def test_transitions_kept_only_where_predecessor_unchanged():
    inp = ["A", "B", "C", "D", "E"]
    llm = ["E", "A", "B", "D", "C"]
    tr = {"E": "", "A": "from E", "B": "from A", "D": "from B", "C": "from D"}
    order, trans = _apply_lead_lock(inp, llm, tr)
    assert trans[order[0]] == ""                 # greeting leads into the first story
    assert trans["B"] == "from A"                # B still follows A → bridge preserved
    for sid in ("C", "E", "D"):                  # predecessor changed → blanked
        assert trans[sid] == ""


def test_short_bulletin_is_full_rank_order():
    inp = ["A", "B"]                             # n <= LEAD_PIN_COUNT → whole thing pinned
    order, _ = _apply_lead_lock(inp, ["B", "A"], {"A": "x", "B": "y"})
    assert order == ["A", "B"]


def test_llm_order_respected_when_it_already_leads_correctly():
    inp = ["A", "B", "C", "D"]
    llm = ["A", "B", "C", "D"]                   # already rank-ordered → unchanged
    order, trans = _apply_lead_lock(inp, llm, {"A": "", "B": "b", "C": "c", "D": "d"})
    assert order == inp
    assert trans["B"] == "b" and trans["C"] == "c" and trans["D"] == "d"
