"""
Verification layer + politics derivation (docs/ingestion-pool-design.md Parts 5–6).

Reconciles our fine categoriser output with the pool's coarse label:

  TOPIC pools (business/technology/sports/science/health/entertainment)
    - Trust the pool for the coarse top-level.
    - RESCUE: if our matcher found nothing, floor to the pool coarse (kills most
      of the ~22% unclassified — every article at least lands in its top-level).
    - DISAGREEMENT: if our confident primary's top-level contradicts the pool,
      prefer the pool coarse as primary, keep our detection as a secondary label
      (multi-label), and LOG a categorisation_mismatch (a free eval set).

  GEO pools (general/nation/world)
    - The pool gives GEOGRAPHY, not topic — so we do NOT override the topical
      primary. We ADD the geo label (top-stories.<region>) as a secondary label,
      RESCUE unclassified articles to it, and DERIVE politics from political
      content + the pool country's region (Part 6).

Returns (category_slugs, primary_category, method). method gains "pool-rescue" /
"pool-corrected" / "pool-geo" so downstream can see how a row was decided.
"""
from __future__ import annotations

import logging
import re

from core.pipeline.data.ingest.pool_taxonomy import (
    GEO_POOL_CATEGORIES,
    coarse_category_slug,
    resolve_top_level,
    politics_slug_for_region,
)

logger = logging.getLogger(__name__)

# Generic political-content cues so a nation/world-pool article that misses the
# specific politics.* keyword rules is still caught for politics derivation.
_POLITICAL_CUES = (
    "government", "minister", "parliament", "election", "president",
    "prime minister", "senate", "congress", "cabinet", "downing street",
    "white house", "diplomat", "sanctions", "referendum", "policy",
)
_POL_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in _POLITICAL_CUES) + r")\b", re.I)


def _is_political(slugs: list[str], text: str) -> bool:
    if any(s.startswith("politics.") for s in slugs):
        return True
    return bool(_POL_RE.search(text or ""))


def reconcile_with_pool(
    *,
    category_slugs: list[str],
    primary_category: str | None,
    method: str,
    pool_category: str | None,
    region: str | None,
    text: str,
) -> tuple[list[str], str | None, str]:
    slugs = list(category_slugs)
    primary = primary_category

    if not pool_category:
        return slugs, primary, method  # manual/legacy ingest — no pool context

    pc = pool_category.strip().lower()

    # ── GEO pools: add geography + politics; never override the topical primary ──
    if pc in GEO_POOL_CATEGORIES:
        geo_slug = coarse_category_slug(pc, region)  # top-stories.<region> (or bare)
        pol_slug = politics_slug_for_region(region) if _is_political(slugs, text) else None

        if geo_slug and geo_slug not in slugs:
            slugs.append(geo_slug)
        if pol_slug and pol_slug not in slugs:
            slugs.append(pol_slug)

        if primary is None:
            # Unclassified domestic/world article — floor to politics (if political)
            # else to the geographic bucket. Never leaves it unclassified.
            primary = pol_slug or geo_slug
            method = "pool-rescue"
        return slugs, primary, method

    # ── TOPIC pools: trust the pool for the coarse top-level ─────────────────────
    coarse_top = resolve_top_level(pc)
    if coarse_top is None:
        return slugs, primary, method

    if primary is None:
        # RESCUE — floor to the pool's coarse top-level.
        return [coarse_top], coarse_top, "pool-rescue"

    our_top = primary.split(".")[0]
    if our_top != coarse_top:
        # DISAGREEMENT — prefer the pool for coarse, keep our detection secondary.
        logger.warning(
            "categorisation_mismatch  pool_coarse=%s  our_primary=%s  text=%r",
            coarse_top, primary, (text or "")[:80],
        )
        new_slugs = [coarse_top] + [s for s in slugs if s != coarse_top]
        return new_slugs, coarse_top, "pool-corrected"

    # AGREEMENT — our fine sub-cat is under the right coarse; ensure coarse present.
    if coarse_top not in slugs:
        slugs.append(coarse_top)
    return slugs, primary, method
