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
