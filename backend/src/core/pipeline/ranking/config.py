from __future__ import annotations

CATEGORY_WEIGHTS: dict[str, float] = {
    # Business — highest priority for business-professional audience
    "business.markets": 1.70,
    "business.economy": 1.55,
    "business.companies": 1.35,
    "business.personal-finance": 0.60,  # deliberate: not core for this audience

    # Politics
    "politics.uk": 1.20,
    "politics.us": 1.15,
    "politics.europe": 1.10,  # ECB, EU trade policy, European elections are market-moving
    "politics.global": 1.10,  # G7/G20, UN, major diplomatic events

    # Technology
    "technology.ai": 1.15,
    "technology.companies": 1.10,  # Apple/Google/Microsoft earnings and antitrust
    "technology": 1.05,            # prefix fallback for unspecified tech subcategories

    # World (geopolitical events) — neutral baseline
    "world": 1.00,

    # Lower priority for business-professional audience
    "sport": 0.55,
    "entertainment": 0.30,
}

DEFAULT_CATEGORY_WEIGHT = 0.75
DEFAULT_ENTITY_WEIGHT = 1.00

ENTITY_TYPE_WEIGHTS: dict[str, float] = {
    "company": 1.25,
    "government": 1.25,
    "country": 1.20,
    "person": 1.15,
    "organisation": 1.15,
    "organization": 1.15,  # both spellings accepted
    "team": 1.05,
    "competition": 1.00,
    "league": 1.00,
}

RECENCY_DECAY_LAMBDA = 0.06

SOURCE_WEIGHT_MULTIPLIER = 0.15
SOURCE_WEIGHT_CAP = 0.30

CLUSTER_WEIGHT_MULTIPLIER = 0.10
CLUSTER_WEIGHT_CAP = 0.20

# ══════════════════════════════════════════════════════════════════════════════
# DAY-LEVEL IMPORTANCE MODEL (coverage-driven; see docs/ranking-depth-design.md)
# ══════════════════════════════════════════════════════════════════════════════
# importance = coverage · prominence · freshness · category_tiebreak
# Coverage (how many outlets run the story) is the dominant, UNCAPPED driver —
# "how big is this story". Category is only a LIGHT tiebreak; it must never
# promote a smaller story over a genuinely bigger one.

# Coverage: 1 + COVERAGE_K · log1p(source_count). Uncapped. ⚑ tune.
COVERAGE_K = 0.80
# Prominence (secondary bigness): 1 + PROMINENCE_K · log1p(article_count) + entity heft.
PROMINENCE_K = 0.25
# Freshness: bounded floor — FRESHNESS_MIN + (1-FRESHNESS_MIN)·exp(-λ·hours_ago).
# A big morning story still scores FRESHNESS_MIN by evening, so recency is a
# factor, not the driver. ⚑ tune (window is the candidate loader's, ~24-48h).
FRESHNESS_MIN = 0.60
FRESHNESS_LAMBDA = 0.03
# Category as a LIGHT tiebreak only: rescales the CATEGORY_WEIGHTS (0.30–1.70)
# range down to ≈ ±10%. e.g. business.markets 1.70 → ×1.105; entertainment 0.30
# → ×0.895. Coverage dominates; category breaks near-ties. ⚑ tune.
CATEGORY_TIEBREAK_STRENGTH = 0.15

# 0–10 normalisation: score10 = 10 · importance / (importance + IMPORTANCE_HALF).
# IMPORTANCE_HALF is the importance value that maps to 5/10. ⚑ CALIBRATE on the
# live source-count distribution so typical big stories land ~8–10, minor ~3–5.
IMPORTANCE_HALF = 2.2

# ── Threshold-per-source selection (personalisation lives HERE, not in ranking) ──
# Each preset slides ALL bars together. A story qualifies if its 0–10 score clears
# the bar of ANY source it belongs to (Top Stories = high universal bar across all
# topics; each ticked topic category = its own lower bar). Union, dedup, rank.
# ⚑ TUNE these against the normalised-score distribution.
THRESHOLDS: dict[str, dict[str, float]] = {
    #            Top Stories (front page, all topics)   ticked topic category
    "short":    {"top_stories": 8.5,                    "category": 7.5},
    "medium":   {"top_stories": 7.5,                    "category": 6.0},
    "detailed": {"top_stories": 6.0,                    "category": 4.5},
}
DEFAULT_PRESET = "medium"

# ── Top-Stories-by-region roll-up (Part 4) ───────────────────────────────────
# Regions with a dedicated top-stories.<region> bucket (the taxonomy geo axis).
# geo_region also carries 'latin-america', which has no leaf → those stories have
# no dedicated regional bucket but still appear on the bare 'top-stories' front
# page and via topic filters. ⚑ add 'latin-america' here + a taxonomy leaf if you
# want a dedicated LatAm bucket.
TOP_STORIES_REGIONS: set[str] = {"uk", "us", "europe", "middle-east", "africa", "asia"}

# country_weight (from docs/ingestion-pool-design.md, via pool_taxonomy) applied
# as a LIGHT tiebreak — same principle as CATEGORY_TIEBREAK_STRENGTH. Bigger
# markets break ties within a region; it NEVER overrides raw coverage. ⚑ tune.
COUNTRY_TIEBREAK_STRENGTH = 0.15

# ── Depth by rank (four coarse tiers; word target feeds the summariser prompt) ──
# Assigned by a story's position in the final ordered bulletin. Lead deep, tail
# brisk. Coarse tiers keep the summary cache to ≤4 rows/story. ⚑ tune word targets.
DEPTH_TIERS: list[tuple[str, int]] = [   # (tier name, target words) — ordered by rank band
    ("lead", 120),      # rank 1
    ("major", 80),      # ranks 2–3
    ("standard", 50),   # ranks 4–7
    ("brief", 25),      # ranks 8+
]
# Rank (0-based) → tier index boundaries. rank 0 → lead; 1–2 → major; 3–6 → standard; 7+ → brief.
_DEPTH_RANK_BANDS = (1, 3, 7)  # exclusive upper bounds for lead/major/standard

MIN_ADJUSTED_SCORE = 0.20

BUCKET_MAX_STORIES: dict[str, int] = {
    "business": 3,
    "politics": 2,
    "world": 2,
    "technology": 2,
    "sport": 1,
    "entertainment": 1,  # was 0 (hard exclude); weight (0.30) already deprioritises strongly
}

DEFAULT_BUCKET_MAX_STORIES = 1

BUCKET_REPEAT_PENALTY = 0.85
REPEATED_PRIMARY_ENTITY_PENALTY = 0.65
REPEATED_TOPIC_PENALTY = 0.60
