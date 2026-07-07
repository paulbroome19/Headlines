"""
Cluster merge identity (audit risk #2 — entity-slug over-merge).

`cluster_bucket` is the unit-tested spec for the handler's match + key derivation: two articles
within the age window join the same cluster IFF they share a bucket. Pins the fix:
  - broad institutions (anchor_eligible=false: nato, white-house) are NOT anchors → fall to the
    hint path, so their distinct events split into distinct clusters;
  - anchors are gated on top-level category → one entity can't merge across topics;
  - Wimbledon-style hint keys are unchanged.
Pure — no DB, in the style of test_threshold_filter_order / test_ranked_list.
"""
from __future__ import annotations

from .requested import cluster_bucket, is_clustering_anchor, _extract_title_hint


# --- demotion: broad institutions are not anchors -------------------------------------------

def test_broad_institution_is_not_an_anchor():
    assert is_clustering_anchor("nato", "organisation", anchor_eligible=False) is False
    assert is_clustering_anchor("white-house", "organisation", anchor_eligible=False) is False


def test_specific_entity_and_country_unchanged():
    assert is_clustering_anchor("manchester-united", "team", anchor_eligible=True) is True
    assert is_clustering_anchor("federal-reserve", "organisation", anchor_eligible=True) is True
    assert is_clustering_anchor("united-states", "country", anchor_eligible=True) is False  # country
    assert is_clustering_anchor(None, None, anchor_eligible=True) is False                  # entity-less


# --- one entity, two unrelated events → two clusters ----------------------------------------

def test_demoted_entity_two_unrelated_events_split():
    # white-house (anchor_eligible=false) — a Smithsonian story and a FIFA story must NOT merge.
    b1 = cluster_bucket(entity="white-house", entity_type="organisation", anchor_eligible=False,
                        category="culture.arts", title="Smithsonian report sparks White House row")
    b2 = cluster_bucket(entity="white-house", entity_type="organisation", anchor_eligible=False,
                        category="sport.football", title="FIFA overturns Balogun ban after White House pressure")
    assert b1 != b2                       # two distinct clusters
    assert b1.startswith("hint:") and b2.startswith("hint:")   # both took the hint path, not the bare slug


def test_anchor_entity_splits_across_top_level_category():
    # A genuine anchor entity appearing in two different TOP-LEVEL categories must not merge
    # (the category gate) — e.g. a club in a sport story vs a business story.
    b_sport = cluster_bucket(entity="manchester-united", entity_type="team", anchor_eligible=True,
                             category="sport.football.premier-league", title="United beat rivals")
    b_biz = cluster_bucket(entity="manchester-united", entity_type="team", anchor_eligible=True,
                           category="business.companies", title="United reports record revenue")
    assert b_sport != b_biz
    # same entity + same top-level category → still one cluster (sub-category noise tolerated)
    b_a = cluster_bucket(entity="manchester-united", entity_type="team", anchor_eligible=True,
                         category="sport.football.premier-league", title="United beat rivals")
    b_b = cluster_bucket(entity="manchester-united", entity_type="team", anchor_eligible=True,
                         category="sport.football", title="United's win analysed")
    assert b_a == b_b


# --- hint path unchanged (Wimbledon) --------------------------------------------------------

def test_wimbledon_hint_key_unchanged():
    # No anchor-eligible entity (tennis players aren't entities) → hint path, exactly as today.
    b = cluster_bucket(entity=None, entity_type=None, anchor_eligible=True,
                       category="sport.tennis", title="Wimbledon quarter-final: Fery faces Dimitrov")
    assert b == "hint:wimbledon:sport.tennis"
    # two Wimbledon articles, same leading term + category → one cluster (unchanged behaviour)
    b2 = cluster_bucket(entity=None, entity_type=None, anchor_eligible=True,
                        category="sport.tennis", title="Wimbledon: Osaka through to the next round")
    assert b == b2
    assert _extract_title_hint("Wimbledon quarter-final") == "wimbledon"
