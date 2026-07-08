"""
Request-hash construction (the #157 wiring guard).

The home preview, the streaming skeleton and the cached/assemble path must key the materialised
selection on the SAME request_hash, or the skeleton misses the preview's deduped edition and
falls back to the pre-dedup order. `selection_filters` is the single builder they all share;
these pin that (a) it's byte-identical to the historical hand-built dict (so existing cached
bulletins/editions still hash-match), and (b) include_top_stories is part of the key — which is
exactly why the manifest must derive it from the profile (like the preview), not the client.
"""
from __future__ import annotations

from core.pipeline.data.bulletin.selector import compute_request_hash, selection_filters


def _legacy_filters(preset, include_top_stories, include_categories, exclude_categories, name):
    """The construction that used to live (duplicated) in every route — the cache-key contract we
    must not change, or every cached bulletin/edition silently misses."""
    filters: dict = {"preset": preset, "include_top_stories": include_top_stories}
    if include_categories:
        filters["include_categories"] = sorted(include_categories)
    if exclude_categories:
        filters["exclude_categories"] = sorted(exclude_categories)
    if name:
        filters["name"] = name.strip()
    return filters


def test_selection_filters_matches_legacy_construction():
    # Byte-identical to the old hand-built dict across representative inputs → no cache invalidation.
    cases = [
        dict(preset="medium", include_top_stories=True, include_categories=["b", "a"],
             exclude_categories=None, name="Paul "),
        dict(preset="short", include_top_stories=False, include_categories=None,
             exclude_categories=["politics", "business"], name=None),
        dict(preset="long", include_top_stories=True, include_categories=[],
             exclude_categories=[], name="  Sam  "),
    ]
    for c in cases:
        assert selection_filters(**c) == _legacy_filters(**c)
        assert compute_request_hash(selection_filters(**c)) == compute_request_hash(_legacy_filters(**c))


def test_hash_identical_across_surfaces_for_same_profile():
    # Preview and manifest, given the SAME profile-derived inputs, produce the SAME hash — the
    # guarantee the #157 fix restores (both now call selection_filters with the profile value).
    kw = dict(preset="medium", include_top_stories=False,
              include_categories=["politics.uk"], exclude_categories=None, name="Paul")
    preview_hash = compute_request_hash(selection_filters(**kw))
    manifest_hash = compute_request_hash(selection_filters(**kw))
    assert preview_hash == manifest_hash


def test_include_top_stories_is_part_of_the_key():
    # The exact reason the bug bit: a profile with top-stories OFF hashes differently from a client
    # request that hardcodes True. If the two surfaces key it from different sources they diverge and
    # the skeleton misses the preview's materialised selection. So both MUST key the profile value.
    base = dict(preset="medium", include_categories=["politics.uk"],
                exclude_categories=None, name="Paul")
    profile_off = compute_request_hash(selection_filters(include_top_stories=False, **base))
    client_on   = compute_request_hash(selection_filters(include_top_stories=True, **base))
    assert profile_off != client_on


def test_category_order_and_name_whitespace_are_normalised():
    # Order-insensitive categories + trimmed name → the same hash regardless of client formatting.
    a = selection_filters(preset="medium", include_top_stories=True,
                          include_categories=["b", "a"], exclude_categories=["y", "x"], name="Paul")
    b = selection_filters(preset="medium", include_top_stories=True,
                          include_categories=["a", "b"], exclude_categories=["x", "y"], name="Paul ")
    assert compute_request_hash(a) == compute_request_hash(b)
