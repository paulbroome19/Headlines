"""
Curated source credibility / prominence tiers for playback attribution.

GNews returns a per-article `source` display name but no credibility signal, so
this is a HAND-CURATED tiered list: recognisable household names lead, lesser-known
outlets rank lower. Used to rank a story's outlets and surface the top few under
the now-playing card.

Matching is forgiving of GNews' naming variants: case-insensitive, strips a
leading "The", strips domain suffixes (.co.uk/.com/.eu/…), and falls back to the
part before a " - " / " | " description tail. Unknown outlets are kept (shown
as-is, cleaned) at the lowest tier — a story is never left without attribution.

⚑ Editorial: promote/demote outlets by moving them between the tier lists below.
"""
from __future__ import annotations

import re

# Lower number = more prominent (leads). Each entry: (canonical_display, aliases).
# Aliases are matched after normalisation; include GNews' domain/variant forms.
_TIER_1 = [  # household / wire / flagship
    ("BBC", ["bbc", "bbc news"]),
    ("Sky News", ["sky news"]),
    ("Reuters", ["reuters"]),
    ("Financial Times", ["financial times", "ft"]),
    ("The Guardian", ["the guardian", "guardian"]),
    ("The Times", ["the times", "times of london"]),
    ("The Telegraph", ["the telegraph", "telegraph"]),
    ("The Economist", ["the economist", "economist"]),
    ("Bloomberg", ["bloomberg"]),
    ("Associated Press", ["associated press", "ap", "ap news"]),
    ("The Wall Street Journal", ["the wall street journal", "wall street journal", "wsj"]),
    ("The New York Times", ["the new york times", "new york times", "nytimes", "nyt"]),
    ("The Washington Post", ["the washington post", "washington post"]),
    ("ITV News", ["itv news", "itv", "itvx"]),
    ("Channel 4 News", ["channel 4 news", "channel 4"]),
]
_TIER_2 = [  # major national / well-known
    ("The Independent", ["the independent", "independent"]),
    ("CNN", ["cnn"]),
    ("CNBC", ["cnbc"]),
    ("BBC Sport", ["bbc sport"]),
    ("Sky Sports", ["sky sports"]),
    ("Al Jazeera", ["al jazeera"]),
    ("Politico", ["politico"]),
    ("The Observer", ["the observer", "observer"]),
    ("Daily Mail", ["daily mail", "mail online", "mailonline"]),
    ("The Mirror", ["the mirror", "mirror", "daily mirror"]),
    ("Daily Express", ["daily express", "express"]),
    ("Metro", ["metro"]),
    ("Evening Standard", ["evening standard", "london evening standard", "standard"]),
    ("The Sun", ["the sun", "sun", "thesun"]),
    ("NPR", ["npr"]),
    ("CBS News", ["cbs news", "cbs"]),
    ("ABC News", ["abc news", "abc"]),
    ("Forbes", ["forbes"]),
    ("Business Insider", ["business insider", "insider"]),
    ("Euronews", ["euronews"]),
    ("Yahoo News", ["yahoo news", "yahoo"]),
]
_TIER_3 = [  # recognised specialist / trade / regional flagship
    ("iNews", ["inews", "i news"]),
    ("Variety", ["variety"]),
    ("The Athletic", ["the athletic", "athletic"]),
    ("ESPN", ["espn"]),
    ("Autosport", ["autosport"]),
    ("Opta Analyst", ["opta analyst", "opta"]),
    ("Eurogamer", ["eurogamer"]),
    ("Nintendo Life", ["nintendo life"]),
    ("Push Square", ["push square"]),
    ("Digital Spy", ["digital spy"]),
    ("ScienceAlert", ["sciencealert", "science alert"]),
    ("PetaPixel", ["petapixel"]),
    ("TechCrunch", ["techcrunch"]),
    ("The Verge", ["the verge", "verge"]),
    ("Wired", ["wired"]),
    ("PA Media", ["pa media", "press association"]),
    ("Liverpool Echo", ["liverpool echo"]),
    ("Manchester Evening News", ["manchester evening news"]),
]

_DEFAULT_TIER = 9  # unknown / everything else — kept, shown as-is at the bottom


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


# Build normalised alias → (tier, canonical_display).
_LOOKUP: dict[str, tuple[int, str]] = {}
for _tier, _entries in ((1, _TIER_1), (2, _TIER_2), (3, _TIER_3)):
    for _display, _aliases in _entries:
        for _a in [_display, *_aliases]:
            _LOOKUP.setdefault(_norm(_a), (_tier, _display))


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
    for key in (_norm(name), _norm(_head(name))):
        if key in _LOOKUP:
            return _LOOKUP[key]
    return (_DEFAULT_TIER, _clean_display(name))


def best_tier(names: list[str]) -> int:
    """The best (lowest-numbered = most credible) curated tier among a story's
    source names, or _DEFAULT_TIER if none are recognised. Used as a light
    source-authority signal in ranking (a lone wire/flagship story outranks a lone
    unknown-blog one when coverage is equal)."""
    best = _DEFAULT_TIER
    for name in names or []:
        if not name or not name.strip():
            continue
        tier, _ = _classify(name)
        if tier < best:
            best = tier
    return best


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
        tier, display = _classify(name)
        dedupe_key = _norm(display)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ranked.append((tier, order, display))
        order += 1

    ranked.sort(key=lambda x: (x[0], x[1]))  # tier asc, then first-seen
    return [display for _, _, display in ranked[:limit]]
