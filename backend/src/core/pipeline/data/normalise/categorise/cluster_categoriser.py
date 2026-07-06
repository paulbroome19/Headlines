"""
Cluster-level LLM-primary categorisation — the orchestration used by both the pipeline
handler (categorise stage) and the legacy backfill devtool.

For ONE story: gather its representative title + a few article snippets + the pool hints
(majority pool topic/country across its articles), classify once with Haiku (cache by
content hash), and return a guaranteed-valid categorisation. Returns None only when the
LLM is unavailable / unusable — the caller then keeps its existing fallback.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.platform.config.settings import settings
from .category_loader import load_valid_category_slugs
from .categorisation_repo import CategorisationRepo, content_hash
from .llm_categoriser import (
    StoryCategorisation,
    classify_stories_batch,
    classify_story,
    _model,
)

# Stories classified per Anthropic call. The ~1.6k-token taxonomy system prompt is sent
# once per call regardless of size, so batching amortises it across this many stories
# (the dominant, uncached input cost). Calibrated on prod (temp=0): size 5 keeps ~96%
# agreement with the single-story classification while cutting ~62% of cost; agreement
# falls off past ~8 (cross-story interference), so 5 is the fidelity/cost sweet spot.
_BATCH_SIZE = 5


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _load_story_inputs(db: Session, story_id: int) -> dict | None:
    """Representative title + article snippets + majority pool hints for a story."""
    head = db.execute(
        text("SELECT representative_title FROM data.stories WHERE id = :id"),
        {"id": story_id},
    ).first()
    if head is None or not head[0]:
        return None
    arts = db.execute(
        text("""
            SELECT title, content_snippet, pool_category, pool_country
            FROM data.normalisation_articles
            WHERE story_id = :id
            ORDER BY id
        """),
        {"id": story_id},
    ).all()
    snippets: list[str] = []
    for a in arts[:4]:  # a few members are plenty for the topic
        if a[0]:
            snippets.append(a[0])
        if a[1]:
            snippets.append(a[1])
    pools = Counter(a[2] for a in arts if a[2])
    countries = Counter(a[3] for a in arts if a[3])
    return {
        "title": head[0],
        "snippets": snippets,
        "pool_topic": pools.most_common(1)[0][0] if pools else None,
        "pool_country": countries.most_common(1)[0][0] if countries else None,
    }


def categorise_story(db: Session, story_id: int) -> StoryCategorisation | None:
    """Classify one story LLM-primary, cached by content hash. Guaranteed-valid result
    or None (API off/failed) so the caller can fall back."""
    if not settings.anthropic_api_key:
        return None
    inputs = _load_story_inputs(db, story_id)
    if inputs is None:
        return None

    from .category_loader import load_category_registry
    leaves = sorted(load_valid_category_slugs())
    taxonomy_version = load_category_registry().version
    model = _model()

    h = content_hash(
        title=inputs["title"], snippets=inputs["snippets"],
        pool_topic=inputs["pool_topic"], pool_country=inputs["pool_country"],
        taxonomy_version=taxonomy_version,
    )
    repo = CategorisationRepo(db)
    repo.ensure_table()
    cached = repo.get(h, model)
    if cached is not None:
        return cached  # never re-classify identical content

    result = classify_story(
        title=inputs["title"], snippets=inputs["snippets"],
        pool_topic=inputs["pool_topic"], pool_country=inputs["pool_country"],
        valid_leaves=leaves,
    )
    if result is None:
        return None
    repo.put(story_id=story_id, content_hash=h, model=model,
             result=result, taxonomy_version=taxonomy_version)
    db.commit()
    return result


def categorise_stories(db: Session, story_ids: list[int]) -> dict[int, StoryCategorisation | None]:
    """Batch LLM-primary categorisation for many stories, cached by content hash.

    Mirrors categorise_story but amortises the taxonomy system prompt by classifying
    ~_BATCH_SIZE cache-MISS stories per Anthropic call. Returns {story_id: result-or-None}
    where None means the LLM was off / that story couldn't be read back (caller falls back).
    A story with cached content is never re-classified (content-hash cache), so replays and
    overlapping ranking windows never re-pay.

    Behaviour per story is identical to categorise_story — same guard, DROP, and cache — so
    classifications match the single-story path; only the transport (one call for many) and
    thus the input-token cost differ."""
    results: dict[int, StoryCategorisation | None] = {}
    if not settings.anthropic_api_key:
        return {sid: None for sid in story_ids}

    from .category_loader import load_category_registry
    leaves = sorted(load_valid_category_slugs())
    taxonomy_version = load_category_registry().version
    model = _model()
    repo = CategorisationRepo(db)
    repo.ensure_table()

    # 1. Resolve inputs + serve cache hits; collect the misses to classify.
    misses: list[tuple[int, dict, str]] = []
    for sid in story_ids:
        inputs = _load_story_inputs(db, sid)
        if inputs is None:
            results[sid] = None
            continue
        h = content_hash(
            title=inputs["title"], snippets=inputs["snippets"],
            pool_topic=inputs["pool_topic"], pool_country=inputs["pool_country"],
            taxonomy_version=taxonomy_version,
        )
        cached = repo.get(h, model)
        if cached is not None:
            results[sid] = cached  # never re-classify identical content
        else:
            misses.append((sid, inputs, h))

    # 2. Classify the misses in batches; persist each result to the cache.
    for chunk in _chunks(misses, _BATCH_SIZE):
        payload = [
            {"title": inp["title"], "snippets": inp["snippets"],
             "pool_topic": inp["pool_topic"], "pool_country": inp["pool_country"]}
            for _, inp, _ in chunk
        ]
        batch = classify_stories_batch(payload, leaves)
        for (sid, _inp, h), res in zip(chunk, batch):
            results[sid] = res
            if res is not None:
                repo.put(story_id=sid, content_hash=h, model=model,
                         result=res, taxonomy_version=taxonomy_version)
        db.commit()

    return results
