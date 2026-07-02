from __future__ import annotations

CATEGORY_WEIGHTS: dict[str, float] = {
    # Business — a real audience lean, kept high.
    "business.markets": 1.70,
    "business.economy": 1.55,
    "business.companies": 1.35,
    "business.personal-finance": 0.60,  # deliberate: not core for this audience

    # Front-page axis.
    "top-stories": 1.20,

    # World / geopolitics / conflict — a general front page LEADS with these, so
    # they sit ABOVE tech-product news. NOTE: the taxonomy + categoriser use
    # `politics.world` (a real picker leaf: politics → uk/us/europe/world), NOT
    # `politics.global` — the old key here matched nothing produced, so geopolitics
    # (~50 stories/day) silently fell to the 0.75 default. `world` is kept for stray
    # top-level world.* tags.
    "world": 1.35,
    "politics.world": 1.35,   # geopolitics / G7 / UN / war / diplomacy (the produced slug)
    "politics.global": 1.35,  # legacy alias — kept in case anything still emits it
    "politics.uk": 1.25,
    "politics.us": 1.20,
    "politics.europe": 1.20,

    # Technology — AI stays newsy; company/product news moderated below world/
    # politics; gaming is soft product news, not front-page hard news.
    "technology.ai": 1.15,
    "technology.companies": 0.95,  # product/earnings — shouldn't beat world/politics
    "technology": 1.00,            # prefix fallback for unspecified tech subcategories
    "technology.gaming": 0.40,     # PlayStation/Xbox/Warhammer product news

    # Science / health / environment — legitimate news (were falling to the 0.75 default).
    "science": 1.00,
    "health": 1.10,
    "environment": 1.10,

    # Soft news.
    "culture": 0.40,          # celebrity / tv-film / music / soap
    "sport": 0.55,
    "entertainment": 0.30,
}

DEFAULT_CATEGORY_WEIGHT = 0.75
DEFAULT_ENTITY_WEIGHT = 1.00

# ══════════════════════════════════════════════════════════════════════════════
# NEWSPAPER TIER MODEL (front page composition; see docs/ranking-depth-design.md)
# ══════════════════════════════════════════════════════════════════════════════
# The bulletin must read like a newspaper: hard news LEADS the front page, soft
# news (sport/tech/culture) sits at the back — UNLESS a soft story is so heavily
# covered the whole press led with it (a World Cup final), which earns it a
# break-through onto the front. CATEGORY (the tier) is the PRIMARY, decisive driver
# of front-page order; coverage is secondary (orders within a tier + earns the rare
# break-through). This replaces the old model where category was only a ~±14%
# tiebreak that soft-news coverage routinely overwhelmed.
#
# Resolution mirrors CATEGORY_WEIGHTS: exact slug → prefix fallback → DEFAULT_TIER.
CATEGORY_TIERS: dict[str, int] = {
    # ── Tier 1 — the front page. A finance-commuter paper leads with these.
    "top-stories": 1,   # the front-page axis + every top-stories.<region> bucket
    "politics": 1,      # incl. politics.uk/us/europe/world (geopolitics/war/diplomacy)
    "business": 1,      # incl. markets/economy/companies/personal-finance

    # ── Tier 2 — the middle. Legitimate news, below the front page.
    "world": 2,         # stray top-level world.* tags (geopolitics lives in politics.*)
    "health": 2,
    "science": 2,
    "environment": 2,

    # ── Tier 3 — the back. Soft/product/leisure news; never leads unless exceptional.
    "technology": 3,    # incl. technology.gaming — PlayStation/Xbox product news
    "sport": 3,
    "culture": 3,
    "entertainment": 3,
}
# Unknown / uncategorised slugs sit at the back — they must never lead the paper.
DEFAULT_TIER = 3

# Break-through: a Tier 2/3 story is promoted to the front page (Tier 1) ONLY if its
# distinct-source coverage clears this HIGH bar — i.e. it is genuinely dominating the
# news cycle (a World Cup final: the whole press led with it), not merely a well-clustered
# ordinary sport/tech story. Calibrated against live data: ordinary football/tech
# clustering tops out around 8–11 distinct sources, so this sits clearly above it —
# nothing soft breaks through on a normal day, only a true everyone's-leading-with-it
# event. ⚑ tune as coverage richens (raise if soft product stories start leaking in).
BREAKTHROUGH_SOURCES = 28

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
# Category as a LIGHT tiebreak: rescales the CATEGORY_WEIGHTS range to ≈ ±14%.
# Coverage still dominates ACROSS coverage levels, but among EQUAL-coverage stories
# (the single-source tail that dominates thin/early data) this is the main
# differentiator — so it must be enough to sort a war story above a soap-award or a
# gaming blog post. Nudged 0.15→0.20 for that separation. ⚑ tune.
CATEGORY_TIEBREAK_STRENGTH = 0.20

# Source authority: a LIGHT lift by the best (most credible) outlet covering the
# story, reusing the curated source_credibility tiers. This breaks ties in the
# single-source tail — a lone Reuters/BBC/AP story outranks a lone gaming-blog one.
# Bonus by tier (1 = wire/flagship … default = unknown); scaled by the strength.
# Coverage still dominates; this only sorts near-ties. ⚑ tune.
SOURCE_AUTHORITY_STRENGTH = 0.10
TIER_AUTHORITY_BONUS: dict[int, float] = {1: 1.0, 2: 0.6, 3: 0.3}  # else 0.0

# 0–10 normalisation: score10 = 10 · importance / (importance + IMPORTANCE_HALF).
# IMPORTANCE_HALF is the importance value that maps to 5/10. Calibrated against the
# live importance distribution (max raw importance ~6): 0.9 maps the biggest stories
# to ~8.7 and the bulk to ~6.5, so the front/top-stories bar is actually reachable
# (was 2.2 → max score 7.34, BELOW the 7.5 bar, so via_front never fired). ⚑ recalibrate
# as coverage richens (the raw ceiling rises on multi-source days).
IMPORTANCE_HALF = 0.9

# ── Threshold-per-source selection (personalisation lives HERE, not in ranking) ──
# A story qualifies if its 0–10 score clears the bar of ANY source it belongs to
# (Top Stories = high universal bar; each ticked topic category = its own lower bar).
# The bar is now REACHABLE (see IMPORTANCE_HALF) so via_front actually fires. LENGTH is
# not driven by the bar (thin data makes bar-counts unstable) — it's PRESET_TARGET_STORIES.
THRESHOLDS: dict[str, dict[str, float]] = {
    #            Top Stories (front page, all topics)   ticked topic category
    "short":    {"top_stories": 8.0,                    "category": 7.0},
    "medium":   {"top_stories": 7.5,                    "category": 6.5},
    "detailed": {"top_stories": 7.0,                    "category": 6.0},
}
DEFAULT_PRESET = "medium"

# ── Fresh followed-topic bar relief (thresholds.py select_by_thresholds) ─────────
# Following a topic is an explicit "I want this live" signal. A genuinely-breaking
# story arrives single-source, so coverage (the dominant importance term) can't yet
# rate it highly — it sits just under its category bar until other outlets pick it
# up. This lets such a story clear its OWN (already-lower) category bar a little
# sooner, decaying fast so it only offsets the fresh single-source gap.
#   effective_category_bar = category_bar − FRESH_CATEGORY_RELIEF·exp(−hours_ago/τ)
# Applied to the via_category branch ONLY. via_front / via_region (the front page)
# are NEVER touched, so front-page composition and ordering are unchanged by
# construction, and the final sort stays normalized_score-first (coverage-dominant)
# — a fresh single-source story fills a TAIL slot in a follower's bulletin, it never
# leapfrogs a bigger story. Keep RELIEF ≤ 0.5: above that it offsets the coverage
# deficit itself (not just the fresh gap) and weak single-source noise leaks in.
FRESH_CATEGORY_RELIEF = 0.4     # max category-bar reduction for a brand-new story
BREAKING_TAU_HOURS = 0.75       # decay e-fold ≈ 45 min (~0.51·max at 30 min, ~0.26 at 1 h)

# Per-preset TARGET story count — the real "length" control (time-anchored: Quick≈5m /
# Standard≈10m / Deep≈15m). Selection returns the top-N by score (qualifiers first,
# backfilled to the target), so the three presets are DISTINCT lengths and never empty
# while stories exist. Flexes honestly: a thin day still fills to the target from the
# next-best; a rich day caps at it.
PRESET_TARGET_STORIES: dict[str, int] = {"short": 6, "medium": 10, "detailed": 16}
DEFAULT_TARGET_STORIES = 10

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
#
# CALIBRATED FOR DURATION (measured ~156 wpm on ElevenLabs): a Medium briefing is ~10
# stories (1 lead + 2 major + 4 standard + 3 brief). The OLD targets summed to ~555 story
# words ≈ ~4 min of speech, so a full 10-story Medium landed at only ~5:39 — the summaries
# hit their targets, the targets were just half of what ~10 min needs. These land the
# story content near ~1250 words + ~270 words of intro/transitions/outro ≈ ~1520 words ≈
# ~10 min. Keep the prose word counts in llm_summariser._DEPTH_SPEC in sync — THAT is the
# instruction the model actually reads. ⚑ retune against the live wpm if the voice changes.
DEPTH_TIERS: list[tuple[str, int]] = [   # (tier name, target words) — ordered by rank band
    ("lead", 260),      # rank 1
    ("major", 180),     # ranks 2–3
    ("standard", 110),  # ranks 4–7
    ("brief", 55),      # ranks 8+
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
