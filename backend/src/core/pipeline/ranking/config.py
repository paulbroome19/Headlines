from __future__ import annotations

CATEGORY_WEIGHTS: dict[str, float] = {
    "business.markets": 1.60,
    "business.economy": 1.45,
    "business.companies": 1.30,
    "politics.uk": 1.20,
    "politics.us": 1.15,
    "technology.ai": 1.15,
    "world": 1.00,
    "sport": 0.65,
    "entertainment": 0.45,
}

DEFAULT_CATEGORY_WEIGHT = 1.00
DEFAULT_ENTITY_WEIGHT = 1.00

RECENCY_DECAY_LAMBDA = 0.08

SOURCE_WEIGHT_MULTIPLIER = 0.18
SOURCE_WEIGHT_CAP = 0.35

CLUSTER_WEIGHT_MULTIPLIER = 0.12
CLUSTER_WEIGHT_CAP = 0.25

MIN_ADJUSTED_SCORE = 0.001

BUCKET_MAX_STORIES: dict[str, int] = {
    "business": 3,
    "politics": 2,
    "world": 2,
    "technology": 2,
    "sport": 1,
    "entertainment": 1,
}

DEFAULT_BUCKET_MAX_STORIES = 1

BUCKET_REPEAT_PENALTY = 0.88
REPEATED_PRIMARY_ENTITY_PENALTY = 0.70
REPEATED_TOPIC_PENALTY = 0.72