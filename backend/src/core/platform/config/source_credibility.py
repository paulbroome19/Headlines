"""
Curated source credibility / prominence for playback attribution AND ranking authority.

GNews returns a per-article `source` display name but no credibility signal, so
this is a HAND-CURATED model. Two consumers:

  • Playback attribution — `rank_sources()` / `best_tier()` surface a story's most
    recognisable outlets under the now-playing card.
  • Ranking authority — `resolve_authority()` returns a per-story (outlet × category)
    authority bonus and a `lead_eligible` flag that the ranker uses so household
    Western names lead, specialists get extra weight in their home category, trusted
    regional names only count when their region is explicitly selected, and unknown /
    PR-wire / non-Western tail outlets can appear but never lead.

Matching is forgiving of GNews' naming variants: case-insensitive, strips a
leading "The", strips domain suffixes (.co.uk/.com/.eu/…), folds a canonical-merge
map (Yahoo* → Yahoo, thesun.* → The Sun, …), and falls back to the part before a
" - " / " | " description tail. Unknown outlets are kept (shown as-is) at the
lowest tier — a story is never left without attribution.

⚑ Editorial: promote/demote by moving outlets between the structures below.
The AUTHORITY STRENGTH multiplier (how much these bonuses move rank) lives in
`ranking/config.py` (SOURCE_AUTHORITY_STRENGTH / UNKNOWN_AUTHORITY_FACTOR); the
class weights below describe only the *relative* credibility of each class.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Global trusted tiers — the Western core, trusted in EVERY category ──────────
# Lower number = more prominent. Each entry: (canonical_display, aliases).
_TIER_1 = [  # household / wire / flagship — lead the bulletin
    ("BBC", ["bbc", "bbc news"]),
    ("Sky News", ["sky news"]),
    ("Sky Sports", ["sky sports"]),
    ("The Guardian", ["the guardian", "guardian"]),
    ("AP News", ["ap news", "associated press", "ap"]),
    ("The New York Times", ["the new york times", "new york times", "nytimes", "nyt"]),
    ("CNN", ["cnn"]),
    ("CBS News", ["cbs news", "cbs"]),
    ("NBC News", ["nbc news"]),
    ("Fox News", ["fox news"]),
    ("The Independent", ["the independent", "independent"]),
    ("The Telegraph", ["the telegraph", "telegraph"]),
    # Future-proof: absent from the current GNews feed but harmless to list, so
    # they connect immediately if the sourcing improves.
    ("Reuters", ["reuters"]),
    ("Financial Times", ["financial times", "ft"]),
    ("Bloomberg", ["bloomberg"]),
    ("The Wall Street Journal", ["the wall street journal", "wall street journal", "wsj"]),
    ("The Times", ["the times", "times of london"]),
    ("The Economist", ["the economist", "economist"]),
    ("The Washington Post", ["the washington post", "washington post"]),
]
_TIER_2 = [  # major national / well-known — can lead, smaller boost
    ("NPR", ["npr"]),
    ("ABC News", ["abc news"]),
    ("ITV News", ["itv news", "itv", "itvx"]),
    ("Channel 4 News", ["channel 4 news", "channel 4"]),
    ("Business Insider", ["business insider", "insider"]),
    ("The Observer", ["the observer", "observer"]),
    ("Daily Mail", ["daily mail", "mail online", "mailonline"]),
    ("The Mirror", ["the mirror", "mirror", "daily mirror"]),
    ("Daily Express", ["daily express", "express"]),
    ("Metro", ["metro"]),
    ("Evening Standard", ["evening standard", "london evening standard", "standard"]),
    ("The Sun", ["the sun", "sun", "thesun"]),
]
_TIER_3 = [  # recognised generalist / regional flagship — modest boost
    ("iNews", ["inews", "i news"]),
    ("PA Media", ["pa media", "press association"]),
    ("Liverpool Echo", ["liverpool echo"]),
    ("Manchester Evening News", ["manchester evening news"]),
    ("Autosport", ["autosport"]),
    ("Opta Analyst", ["opta analyst", "opta"]),
    ("Digital Spy", ["digital spy"]),
    ("Nintendo Life", ["nintendo life"]),
    ("Push Square", ["push square"]),
    ("PetaPixel", ["petapixel"]),
]

# ── Category specialists — extra authority in their HOME category (top-level),
#    reduced (but not zero) elsewhere. NOT locked to the category. ──────────────
# canonical_display -> (home_categories, aliases)
_SPECIALISTS: list[tuple[str, set[str], list[str]]] = [
    ("Forbes", {"technology", "business"}, ["forbes"]),
    ("TechCrunch", {"technology"}, ["techcrunch"]),
    ("CNET", {"technology"}, ["cnet"]),
    ("The Verge", {"technology"}, ["the verge", "verge"]),
    ("Wired", {"technology"}, ["wired"]),
    ("Ars Technica", {"technology"}, ["ars technica"]),
    ("Variety", {"culture"}, ["variety"]),
    ("The Hollywood Reporter", {"culture"}, ["the hollywood reporter", "hollywood reporter"]),
    ("Deadline", {"culture"}, ["deadline"]),
    ("Rolling Stone", {"culture"}, ["rolling stone"]),
    ("Nature", {"science"}, ["nature"]),
    ("Phys.org", {"science"}, ["phys org", "phys", "physorg"]),
    ("ScienceAlert", {"science"}, ["sciencealert", "science alert"]),
    ("ESPN", {"sport"}, ["espn", "espn india"]),
    ("BBC Sport", {"sport"}, ["bbc sport"]),
    ("CNBC", {"business"}, ["cnbc"]),
    ("Fox Business", {"business"}, ["fox business"]),
]

# ── Regional whitelist — trusted names that ONLY confer authority / lead-eligibility
#    when their region is explicitly selected. Keyed on OUTLET IDENTITY (the geo
#    tags are too noisy). canonical_display -> (region, aliases). ────────────────
_REGIONAL: list[tuple[str, str, list[str]]] = [
    ("Daily Maverick", "africa", ["daily maverick"]),
    ("News24", "africa", ["news24", "news 24"]),
    ("The Straits Times", "asia", ["the straits times", "straits times"]),
    ("Channel NewsAsia", "asia", ["channel newsasia", "channel news asia", "cna"]),
    ("The Hindu", "asia", ["the hindu"]),
    ("Euronews", "europe", ["euronews"]),
    ("Politico Europe", "europe", ["politico europe", "politico eu", "politico"]),
    ("Al Jazeera", "middle-east", ["al jazeera"]),
    ("Haaretz", "middle-east", ["haaretz"]),
]

# ── Excluded — never confer authority, never lead, never regional. ─────────────
_EXCLUDED_NAMES = [
    ("Times of Israel", ["times of israel"]),
    ("Jerusalem Post", ["jerusalem post"]),
    # Non-Western / regional outlets that shouldn't surface in a Western briefing's
    # attribution (they leaked through as default-tier "unknown" sources). Aliases match
    # the _norm() form of the raw source strings seen in prod.
    ("Hindustan Times", ["hindustan times"]),
    ("Times of India", ["times of india"]),          # _norm drops a leading "the"
    ("Moneycontrol", ["moneycontrol"]),              # _norm drops the .com suffix
    ("NDTV", ["ndtv"]),
    ("The Indian Express", ["indian express"]),
    ("Firstpost", ["firstpost"]),
    ("Livemint", ["livemint", "mint"]),
    ("Guardian Nigeria", ["guardian nigeria news", "guardian nigeria"]),
    ("Punch Newspapers", ["punch newspapers"]),
    ("The Star (Kenya)", ["star co ke"]),            # the-star.co.ke → _norm → "star co ke"
    ("Globes (Israel)", ["globes", "globes israel business news"]),
]

# Canonical-merge map for multi-variant names that don't reduce via _norm alone,
# so authority + display dedup key on one canonical form. _norm(variant) -> _norm(canonical).
_CANONICAL_MERGES_RAW = {
    "irish sun": "the sun", "scottish sun": "the sun",
    "yahoo sports": "yahoo", "yahoo finance": "yahoo", "yahoo news": "yahoo",
    "ign india": "ign", "eurogamer net": "eurogamer",
}

_DEFAULT_TIER = 9  # unknown / everything else — kept, shown as-is at the bottom

# Class weights: how credible each class is (0..1). The overall strength multiplier
# lives in ranking/config.py. Global tiers by number; specialists home vs away.
BONUS_GLOBAL: dict[int, float] = {1: 1.0, 2: 0.5, 3: 0.33}
SPECIALIST_HOME_BONUS = 0.67
SPECIALIST_AWAY_BONUS = 0.33
REGIONAL_BONUS = 0.50


def _norm(name: str) -> str:
    """Normalise a source name for matching: lowercase, drop domain suffixes and a
    leading 'the', collapse to single-spaced alphanumerics."""
    s = (name or "").strip().lower()
    for suf in (".co.uk", ".com", ".org", ".net", ".eu", ".io", ".news"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s


def _head(name: str) -> str:
    """The part before a ' - ' / ' | ' / ' — ' description tail (GNews appends these)."""
    for sep in (" - ", " | ", " — ", " – "):
        if sep in name:
            return name.split(sep, 1)[0]
    return name


_CANONICAL_MERGES = {_norm(k): _norm(v) for k, v in _CANONICAL_MERGES_RAW.items()}

# Build normalised alias → (tier, canonical_display) for the global trusted tiers.
_LOOKUP: dict[str, tuple[int, str]] = {}
for _tier, _entries in ((1, _TIER_1), (2, _TIER_2), (3, _TIER_3)):
    for _display, _aliases in _entries:
        for _a in [_display, *_aliases]:
            _LOOKUP.setdefault(_norm(_a), (_tier, _display))

# alias → (canonical_display, home_categories)
_SPEC_LOOKUP: dict[str, tuple[str, frozenset[str]]] = {}
for _display, _cats, _aliases in _SPECIALISTS:
    for _a in [_display, *_aliases]:
        _SPEC_LOOKUP.setdefault(_norm(_a), (_display, frozenset(_cats)))

# alias → (canonical_display, region)
_REGIONAL_LOOKUP: dict[str, tuple[str, str]] = {}
for _display, _region, _aliases in _REGIONAL:
    for _a in [_display, *_aliases]:
        _REGIONAL_LOOKUP.setdefault(_norm(_a), (_display, _region))

# excluded normalised keys
_EXCLUDED_KEYS: set[str] = set()
for _display, _aliases in _EXCLUDED_NAMES:
    for _a in [_display, *_aliases]:
        _EXCLUDED_KEYS.add(_norm(_a))


def _keys(name: str) -> tuple[str, ...]:
    """Candidate lookup keys for a raw source name: normalised form, head form, and
    any canonical-merge target — de-duplicated, order preserved."""
    out: list[str] = []
    for k in (_norm(name), _norm(_head(name))):
        for kk in (_CANONICAL_MERGES.get(k, k), k):
            if kk and kk not in out:
                out.append(kk)
    return tuple(out)


def _clean_display(name: str) -> str:
    """A tidy display name for an unknown outlet: drop the description tail and any
    domain suffix, keep the outlet's own casing."""
    s = _head(name).strip()
    for suf in (".co.uk", ".com", ".org", ".net", ".eu", ".io"):
        if s.lower().endswith(suf):
            s = s[: -len(suf)]
            break
    return s.strip() or name.strip()


def _classify(name: str) -> tuple[int, str]:
    for key in _keys(name):
        if key in _LOOKUP:
            return _LOOKUP[key]
    return (_DEFAULT_TIER, _clean_display(name))


def best_tier(names: list[str]) -> int:
    """The best (lowest-numbered = most credible) curated GLOBAL tier among a story's
    source names, or _DEFAULT_TIER if none are recognised. Kept for now-playing
    attribution and back-compat (ranking authority now uses resolve_authority)."""
    best = _DEFAULT_TIER
    for name in names or []:
        if not name or not name.strip():
            continue
        tier, _ = _classify(name)
        if tier < best:
            best = tier
    return best


def _outlet_authority(
    name: str, top_category: str, selected_regions: frozenset[str] | None
) -> tuple[float, bool]:
    """Authority (bonus, lead_eligible) for a single outlet in a given top-level
    category. Precedence: excluded → regional (region-gated) → global(+specialist) →
    specialist-only → unknown."""
    keys = _keys(name)
    for k in keys:
        if k in _EXCLUDED_KEYS:
            return (0.0, False)
    for k in keys:
        if k in _REGIONAL_LOOKUP:
            _, region = _REGIONAL_LOOKUP[k]
            if selected_regions and region in selected_regions:
                return (REGIONAL_BONUS, True)
            return (0.0, False)  # regional name, region not selected → not trusted here
    for k in keys:
        if k in _LOOKUP:
            tier, _ = _LOOKUP[k]
            bonus = BONUS_GLOBAL.get(tier, 0.0)
            for kk in keys:  # a global outlet that is ALSO a specialist gets the max
                if kk in _SPEC_LOOKUP:
                    _, cats = _SPEC_LOOKUP[kk]
                    spec = SPECIALIST_HOME_BONUS if top_category in cats else SPECIALIST_AWAY_BONUS
                    bonus = max(bonus, spec)
            return (bonus, True)
    for k in keys:
        if k in _SPEC_LOOKUP:
            _, cats = _SPEC_LOOKUP[k]
            spec = SPECIALIST_HOME_BONUS if top_category in cats else SPECIALIST_AWAY_BONUS
            return (spec, True)
    return (0.0, False)  # unknown / untiered


def resolve_authority(
    names: list[str] | tuple[str, ...],
    category: str | None,
    selected_regions: frozenset[str] | set[str] | None = None,
) -> tuple[float, bool]:
    """
    Authority for a whole story: the MAX outlet bonus and whether ANY outlet makes
    it lead-eligible, given the story's primary `category` and the set of
    explicitly-selected top-stories regions.

    Returns (bonus in [0,1], lead_eligible). bonus > 0 iff the story has at least one
    trusted/active outlet. Unknown-only or excluded-only stories return (0.0, False):
    the caller applies the unknown penalty and bars them from lead slots.
    """
    top = (category or "").split(".", 1)[0]
    regions = frozenset(selected_regions) if selected_regions else None
    best = 0.0
    lead = False
    for name in names or []:
        if not name or not name.strip():
            continue
        bonus, ok = _outlet_authority(name, top, regions)
        if bonus > best:
            best = bonus
        lead = lead or ok
    return best, lead


def _is_known(name: str) -> bool:
    """True if the outlet is curated anywhere (global / specialist / regional / excluded)."""
    for k in _keys(name):
        if k in _LOOKUP or k in _SPEC_LOOKUP or k in _REGIONAL_LOOKUP or k in _EXCLUDED_KEYS:
            return True
    return False


_UNSEEN_LOGGED: set[str] = set()


def record_unseen_source(name: str | None) -> None:
    """Log (once per process, per distinct outlet) any source not curated in any tier,
    so unknown outlets can be reviewed / LLM-tiered later. Records only — no ranking
    effect. Cheap: in-memory dedup set, single INFO line per new name."""
    if not name or not name.strip():
        return
    key = _norm(name)
    if key in _UNSEEN_LOGGED:
        return
    _UNSEEN_LOGGED.add(key)
    if _is_known(name):
        return
    logger.info("source_credibility: unseen source %r (untiered) — candidate for LLM tiering", name.strip())


def all_sources_excluded(names: list[str] | tuple[str, ...] | None) -> bool:
    """True iff a story has at least one source and EVERY source is on the exclusion denylist
    — a story with no kept outlet at all. Such a story is dropped from candidate selection
    (it would otherwise surface with blank attribution, since rank_sources drops the excluded
    names). A story with even ONE non-excluded source is kept. Sourceless (empty/blank) → False
    (no sources to be 'all excluded')."""
    real = [n for n in (names or []) if n and n.strip()]
    if not real:
        return False
    return all(any(k in _EXCLUDED_KEYS for k in _keys(n)) for n in real)


def rank_sources(names: list[str], *, limit: int = 3) -> list[str]:
    """
    Rank a story's outlet names by curated credibility tier and return the top
    `limit` canonical display names.

    - Dedupes by canonical outlet (so "The Sun" and "thesun.co.uk" count once).
    - Ties within a tier keep first-seen order (pass names most-covering-first if
      you want frequency to break ties).
    - Unknown outlets are kept (shown as-is, cleaned) below the curated ones — a
      story is never left without attribution.
    """
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    order = 0
    for name in names:
        if not name or not name.strip():
            continue
        # Drop EXCLUDED outlets entirely — they must never appear in attribution. (_classify
        # only knows the global tiers, so without this an excluded name leaked through as a
        # default-tier "unknown" source — the bug that surfaced Hindustan Times etc.)
        if any(k in _EXCLUDED_KEYS for k in _keys(name)):
            continue
        tier, display = _classify(name)
        dedupe_key = _norm(display)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ranked.append((tier, order, display))
        order += 1

    ranked.sort(key=lambda x: (x[0], x[1]))  # tier asc, then first-seen
    return [display for _, _, display in ranked[:limit]]
