"""
Pool taxonomy helpers — the free-by-construction mappings the pool architecture
relies on (see docs/ingestion-pool-design.md Parts 2/3/5/6):

  - country code  → geo_region         (geographic "Top Stories" axis)
  - country code  → country_weight     (importance, for the Part-4 roll-up)
  - GNews pool category → our coarse top-level slug (verification layer)
  - geo_region    → politics slug       (politics derivation geo split)

Resolution is RESILIENT to the taxonomy version: coarse mapping prefers the final
slugs (culture / top-stories / politics.world) but falls back to the legacy ones
(entertainment / world / politics.global) when only those exist, so this works
whether or not the final-taxonomy PR is merged.
"""
from __future__ import annotations

from functools import lru_cache


# ── Regions (docs Part 3a; straddler first-calls applied) ────────────────────
# geo_region carries 7 values as a RAW signal; the taxonomy's top-stories axis
# has 6 leaves (no latin-america) — the roll-up consumer maps it.
_REGION_COUNTRIES: dict[str, tuple[str, ...]] = {
    "uk":            ("gb", "ie"),
    "us":            ("us", "ca"),
    "europe":        ("de", "fr", "it", "es", "nl", "ch", "se", "no", "fi", "be",
                      "at", "pt", "gr", "pl", "cz", "hu", "ro", "bg", "sk", "si",
                      "ee", "lv", "lt", "ua", "ru"),
    "middle-east":   ("il", "sa", "ae", "lb", "tr", "eg"),
    "asia":          ("cn", "jp", "in", "kr", "hk", "sg", "tw", "id", "my", "th",
                      "ph", "vn", "pk", "bd", "au", "nz"),
    "africa":        ("za", "ng", "ke", "gh", "ma", "et", "tz", "ug", "sn", "bw",
                      "na", "zw"),
    "latin-america": ("br", "mx", "ar", "cl", "co", "pe", "ve", "cu"),
}
_COUNTRY_REGION: dict[str, str] = {
    c: region for region, countries in _REGION_COUNTRIES.items() for c in countries
}

# top-stories.* leaves that actually exist as categories (latin-america does not).
_GEO_LEAVES = {"uk", "us", "europe", "middle-east", "africa", "asia"}

# ── Country importance weight (docs Part 3b) — for the Part-4 roll-up ─────────
# gb sits in its own top band (1.5) for a stronger UK lean: for a UK audience,
# UK-edition stories (BBC/Sky/Guardian) surface ahead of equal-scored US/world.
# Paired with COUNTRY_TIEBREAK_STRENGTH=0.25 in ranking/config. ⚑ tune.
_TOP = {"gb"}
_HIGH = {"us", "de", "fr", "ua", "il", "cn", "in", "jp", "za", "ng", "br"}
_MED = {"ie", "ca", "it", "es", "nl", "ch", "ru", "sa", "ae", "kr", "hk", "sg",
        "au", "ke", "ma", "mx", "ar"}


def geo_region(country: str | None) -> str | None:
    """Region for a 2-letter country code (the pool's edition), or None."""
    if not country:
        return None
    return _COUNTRY_REGION.get(country.strip().lower())


def country_weight(country: str | None) -> float:
    """Importance weight (1.5 / 1.0 / 0.6 / 0.3) for cross-country roll-up ranking.
    gb=1.5 gives the UK lean; other big markets stay 1.0."""
    if not country:
        return 0.3
    c = country.strip().lower()
    if c in _TOP:
        return 1.5
    if c in _HIGH:
        return 1.0
    if c in _MED:
        return 0.6
    return 0.3


# ── GNews pool category → our coarse top-level ───────────────────────────────
# Ordered candidates: prefer the final-taxonomy slug, fall back to legacy.
_GNEWS_COARSE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "business":      ("business",),
    "technology":    ("technology",),
    "sports":        ("sport",),
    "science":       ("science",),
    "health":        ("health",),
    "entertainment": ("culture", "entertainment"),
    "world":         ("top-stories", "world"),
    "nation":        ("top-stories", "world"),
    "general":       ("top-stories", "world"),
}

# Pools whose coarse label is geographic (resolved per-region), not topical.
GEO_POOL_CATEGORIES = {"world", "nation", "general"}


@lru_cache(maxsize=1)
def _valid_top_levels() -> frozenset[str]:
    from core.pipeline.data.normalise.categorise.category_loader import load_category_groups
    return frozenset(g["slug"] for g in load_category_groups())


def resolve_top_level(pool_category: str | None) -> str | None:
    """Our coarse top-level slug for a GNews pool category (taxonomy-version aware)."""
    if not pool_category:
        return None
    cands = _GNEWS_COARSE_CANDIDATES.get(pool_category.strip().lower())
    if not cands:
        return None
    valid = _valid_top_levels()
    for c in cands:
        if c in valid:
            return c
    return cands[0]


def coarse_category_slug(pool_category: str | None, region: str | None) -> str | None:
    """
    The coarse category an article should carry from its pool. For topical pools
    this is the top-level (business, sport…); for geographic pools it's the
    region leaf (top-stories.uk) when that leaf exists, else the bare top-level.
    """
    top = resolve_top_level(pool_category)
    if top is None:
        return None
    if (pool_category or "").strip().lower() in GEO_POOL_CATEGORIES:
        if region and region in _GEO_LEAVES:
            return f"{top}.{region}"
        return top  # latin-america / unknown region → bare parent
    return top


# ── Politics geo split (docs Part 6) ─────────────────────────────────────────
def politics_slug_for_region(region: str | None) -> str:
    """politics.<geo> from the pool's region (world → the legacy 'global' fallback)."""
    if region == "uk":
        return "politics.uk"
    if region == "us":
        return "politics.us"
    if region == "europe":
        return "politics.europe"
    # politics.world (final taxonomy) vs politics.global (legacy)
    from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs
    leaves = load_valid_category_slugs()
    return "politics.world" if "politics.world" in leaves else "politics.global"
