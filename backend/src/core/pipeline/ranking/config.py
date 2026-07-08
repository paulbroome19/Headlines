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
# NOTE: CATEGORY_TIERS is no longer used to ORDER the bulletin (the threshold +
# filter-order model orders by the user's filter sequence, not newspaper tier). It is
# retained only for the LLM editorial pass (editorial_review.get_category_tier), which
# scopes review depth by tier. The break-through-to-front-page mechanism is removed.

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

# ── Stable ranked list (data.ranked_stories) ─────────────────────────────────
# The ONE allowed reorder: a placed story is re-scored/moved ONLY when its coverage
# GENUINELY grows — source_count must rise by BOTH an absolute AND a relative margin, so
# ordinary noise (a single extra outlet) never churns the list. Never reorders on recency.
# This is what stops a 6am single-source story that becomes the day's biggest by noon from
# being frozen at the bottom.
COVERAGE_GROWTH_MIN_ABS = 2        # at least +2 distinct sources vs the placed count
COVERAGE_GROWTH_MIN_FACTOR = 1.5   # AND at least ×1.5 the placed source_count
# Rolling window for the list: a story leaves once it ages past this (pruned by
# entered_list_at). The candidate loader's window narrows 36h→24h to match.
RANKED_LIST_WINDOW_HOURS = 24
# Category as a LIGHT tiebreak: rescales the CATEGORY_WEIGHTS range to ≈ ±14%.
# Coverage still dominates ACROSS coverage levels, but among EQUAL-coverage stories
# (the single-source tail that dominates thin/early data) this is the main
# differentiator — so it must be enough to sort a war story above a soap-award or a
# gaming blog post. Nudged 0.15→0.20 for that separation. ⚑ tune.
CATEGORY_TIEBREAK_STRENGTH = 0.20

# Source authority: a CURATED, per-category-modulated lift by the story's outlets
# (see platform/config/source_credibility.resolve_authority). Trusted Western core
# leads everywhere; specialists get extra weight in their home category; trusted
# regional names only count when their region is selected. The class weights
# (global/specialist/regional bonus ∈ [0,1]) live in source_credibility; the knobs
# below control HOW MUCH authority moves rank.
#   BONUS lift (importance axis, compute_importance): 1 + SOURCE_AUTHORITY_STRENGTH·bonus
#     → tier-1 ×1.30, home specialist ≈×1.20. Applied to EVERY story so trusted names
#     rise on both the front page and followed topics.
#   UNKNOWN down-rank (front-page / region axis, thresholds.py only): untrusted /
#     unknown-only stories are ×UNKNOWN_AUTHORITY_FACTOR so they can't clear the high
#     top-stories bar or lead a general bulletin. Deliberately NOT applied to the
#     followed-topic (via_category) path, so niche followed content stays supplementary
#     rather than being evicted (keeps the fresh-category-relief behaviour intact).
SOURCE_AUTHORITY_STRENGTH = 0.30   # trusted lift (was a flat 0.10). ⚑ tune.
UNKNOWN_AUTHORITY_FACTOR = 0.60    # front-page down-rank for untrusted/unknown stories. ⚑ tune.
# 0–10 normalisation: score10 = 10 · importance / (importance + IMPORTANCE_HALF).
# IMPORTANCE_HALF maps an importance value to 5/10. NOTE: compute_importance (with the
# freshness term) is RANK-TIME ONLY — it orders the editorial review set. The REQUEST path
# selects on the persisted, time-invariant merit_score from data.ranked_stories (scorer.
# compute_merit → bulletin.selection), NOT on live importance. Calibrated against the live
# importance distribution (max raw ~6): 0.9 maps the biggest stories to ~8.7. ⚑ recalibrate.
IMPORTANCE_HALF = 0.9

# ── Threshold + filter-order selection (personalisation lives HERE) ──────────────
# A preset is a QUALITY BAR (not a count): a story qualifies for a selected category when its
# 0–10 score clears the preset's bar. On the REQUEST path that score is the persisted
# merit_score (the stable ranked list), applied uniformly to every selected source (front
# page, region bucket, topic). Quick = high bar (only the big stuff), Standard = middle, Deep
# = lower bar (more depth). Length is an OUTPUT of what clears the bar, bounded per-category by
# PRESET_DEPTH and globally by MAX_BULLETIN_STORIES. ⚑ recalibrate as coverage richens.
PRESET_BAR: dict[str, float] = {"short": 7.0, "medium": 6.4, "detailed": 6.0}
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

# ── Representation depth + hard ceiling (the size lever, replacing count targets) ──
# Per-preset MAX stories PER selected top-level category. Selection fills round-robin
# in filter order (each category's best before any category's next), so breadth sets
# the floor and depth sets how far down each category goes. NOT a promise: a category
# with fewer qualifiers contributes fewer; a thin day is honestly shorter. Deep goes
# deeper per category than Quick, giving distinct sizes even when the ceiling binds.
PRESET_DEPTH: dict[str, int] = {"short": 3, "medium": 4, "detailed": 8}
# Global hard ceiling on total bulletin size — a cap, not a promise (a monster news day
# can't produce a 40-minute bulletin). Capping drops the DEEPEST stories of the most
# over-represented categories first (a by-product of the round-robin fill order).
MAX_BULLETIN_STORIES = 20

# ── Top-Stories regional toggles (the FINAL top-stories spec) ─────────────────
# The Top Stories filter has exactly these four regional toggles. A story's region is its
# SUBJECT region, derived from the primary_category prefix (thresholds._subject_region):
# politics.uk→uk, politics.us→us, politics.europe→europe, everything else→world. 'world' is the
# catch-all (politics.world + every non-politics top story). middle-east/africa/asia were removed:
# the pipeline cannot attribute subject region for them today (only politics.* carries it), so
# they were dead switches — their return (via editorial region emission) is filed in
# docs/post-demo.md. Their stories are reachable via WORLD until then.
TOP_STORIES_REGIONS: set[str] = {"uk", "us", "europe", "world"}
# The top block holds at most this many flagged top stories (highest-ranked across the toggled
# regions); any remaining slots backfill from the user's followed topics. ⚑ tune.
TOP_BLOCK_SIZE = 5

# country_weight (from docs/ingestion-pool-design.md, via pool_taxonomy) applied
# as a tiebreak — same principle as CATEGORY_TIEBREAK_STRENGTH. Bigger markets break
# ties within a region. Raised 0.15→0.25 alongside a gb bump (pool_taxonomy: gb→1.3)
# to give a moderate UK lean, so BBC/Sky/Guardian surface first for a UK audience
# without burying a clearly-bigger US/world story. ⚑ tune.
COUNTRY_TIEBREAK_STRENGTH = 0.25

# Top-block region order: the TOP STORIES block is ordered by SUBJECT region group first (UK
# leads a UK app), then by score within a group. Config-driven so it can become a per-user
# preference later. Subject region is derived from the primary_category prefix in thresholds
# (_subject_region) — a deterministic replacement for the old FRONT_PAGE_REGION_LEAN, which
# multiplied score by a lean keyed on geo_region. geo_region is SOURCE region and UK-saturated
# (UK outlets cover everything, so global stories read 'uk' and collected the lean too), so the
# lean discriminated nothing and ordering fell back to merit — US stories led a UK front page.
# A region not listed here sorts last. ⚑ per-user ordering later.
TOP_STORIES_REGION_ORDER: list[str] = ["uk", "us", "europe", "world"]

# ── Depth by rank (four coarse tiers; word target feeds the summariser prompt) ──
# Assigned by a story's position in the final ordered bulletin. Lead deep, tail
# brisk. Coarse tiers keep the summary cache to ≤4 rows/story. ⚑ tune word targets.
#
# CALIBRATED FOR THE PODCAST VOICE (measured ~156 wpm on ElevenLabs). The voice is a warm
# spoken catch-up, NOT an essay (see core.pipeline.data.voice.VOICE_BLOCK: "flowing but not
# bloated… not an essay"), so the tiers are deliberately tight — each story runs as long as
# it naturally needs and no longer. A Medium briefing is ~10 stories (1 lead + 2 major +
# 4 standard + 3 brief): 110 + 2·80 + 4·55 + 3·35 ≈ 595 story words + ~270 words of
# greeting/bridges/outro ≈ ~865 words ≈ ~5.5 min. This is a shorter, tighter briefing than
# the old essay-length targets (which pushed a Medium to ~10 min) — chosen for the podcast
# register. Keep the prose word counts in llm_summariser._DEPTH_SPEC in sync — THAT is the
# instruction the model actually reads. ⚑ retune against the live wpm if the voice changes.
DEPTH_TIERS: list[tuple[str, int]] = [   # (tier name, target words) — ordered by rank band
    ("lead", 110),      # rank 1
    ("major", 80),      # ranks 2–3
    ("standard", 55),   # ranks 4–7
    ("brief", 35),      # ranks 8+
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
