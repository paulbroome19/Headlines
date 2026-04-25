"""
Validates _extract_title_hint() and the hint-based clustering logic.

Run: cd backend && .venv/bin/python src/core/pipeline/devtools/cluster_hint_test.py
"""
from __future__ import annotations

from core.pipeline.data.cluster.handlers.requested import _extract_title_hint


def _run_extraction_tests() -> int:
    cases: list[tuple[str, str | None]] = [
        # Should extract meaningful subject word
        ("Jeffrey Epstein documents released by court", "jeffrey"),
        ("Epstein files reveal new names", "epstein"),
        ("Lewis Hamilton signs Ferrari contract", "lewis"),
        ("Taylor Swift announces new album tour", "taylor"),
        ("Drake faces fresh legal challenge", "drake"),
        ("Novak Djokovic wins Wimbledon title", "novak"),
        ("Coronation Street star Tracy Shaw hospitalised", "coronation"),
        # Specific location name IS a valid hint (not generic)
        ("Man arrested in connection with Wrexham murder investigation", "wrexham"),
        # "flat" is stopword, "linked"/"county" follow — "county" is the hint
        ("Police raid flat linked to county lines operation", "county"),
        # All words are stopwords → None
        ("Man arrested on suspicion of murder in connection with investigation", None),
        # "news" is stopword, "live"/"update" are stopwords → None
        ("Breaking news live update", None),
        # Both words are stopwords/short → None
        ("Man dies", None),
        # Short word below 4-char threshold ignored, next word taken
        ("UFC bout heavyweight title fight", "bout"),
    ]

    failures = 0
    for title, expected in cases:
        got = _extract_title_hint(title)
        status = "PASS" if got == expected else "FAIL"
        if status == "FAIL":
            failures += 1
        print(f"  [{status}] {title!r:65s}  expected={expected!r:20s} got={got!r}")

    return failures


def _run_clustering_logic_tests() -> int:
    """
    Simulate the clustering decision without hitting the DB.
    Two conditions must BOTH be true for hint-based merge:
      - same hint word
      - same category
    """
    from core.pipeline.data.cluster.handlers.requested import _extract_title_hint

    failures = 0

    def check(label: str, title_a: str, cat_a: str | None, title_b: str, cat_b: str | None, should_merge: bool) -> None:
        nonlocal failures
        hint_a = _extract_title_hint(title_a) if not None else None
        hint_b = _extract_title_hint(title_b) if not None else None
        # Re-extract properly
        hint_a = _extract_title_hint(title_a)
        hint_b = _extract_title_hint(title_b)

        key_a = f"hint:{hint_a}:{cat_a}" if (hint_a and cat_a) else None
        key_b = f"hint:{hint_b}:{cat_b}" if (hint_b and cat_b) else None

        would_merge = bool(key_a and key_b and key_a == key_b)
        status = "PASS" if would_merge == should_merge else "FAIL"
        if status == "FAIL":
            failures += 1
        print(f"  [{status}] {label}")
        if status == "FAIL":
            print(f"         key_a={key_a!r}  key_b={key_b!r}  should_merge={should_merge}")

    # Related stories — same hint, same category → should merge
    check(
        "Epstein articles both starting 'Jeffrey...' (same world.uk) → merge",
        "Jeffrey Epstein documents released by court", "world.uk",
        "Jeffrey Epstein files published online", "world.uk",
        should_merge=True,
    )
    check(
        "Epstein articles with different leading names → no merge (conservative by design)",
        "Jeffrey Epstein documents released", "world.uk",
        "Epstein files reveal royal connections", "world.uk",
        should_merge=False,
    )
    check(
        "Coronation Street articles (same entertainment.tv-film) → merge",
        "Coronation Street star quits after row", "entertainment.tv-film",
        "Coronation Street actor leaves ITV soap", "entertainment.tv-film",
        should_merge=True,
    )

    # Collision safety — same hint word, different category → must NOT merge
    check(
        "Hamilton in sport.formula1 vs finance news → no merge",
        "Hamilton wins British Grand Prix", "sport.formula1",
        "Hamilton Bank reports record losses", "business.markets",
        should_merge=False,
    )
    check(
        "Swift in entertainment vs Swift programming language → no merge",
        "Swift releases surprise album", "entertainment.music",
        "Swift language gains popularity in enterprise", "technology.companies",
        should_merge=False,
    )
    check(
        "Drake music news vs Drake legal case (same category would merge, but different) → no merge",
        "Drake drops new track tonight", "entertainment.music",
        "Drake faces lawsuit over copyright", "business.companies",
        should_merge=False,
    )

    # No category on either side → no hint key → no merge
    check(
        "No category on article → no merge",
        "Jeffrey Epstein files released", None,
        "Epstein documents published", None,
        should_merge=False,
    )

    return failures


def main() -> None:
    print("=== _extract_title_hint() extraction tests ===")
    extract_failures = _run_extraction_tests()
    print()

    print("=== Clustering logic (hint + category two-condition guard) ===")
    logic_failures = _run_clustering_logic_tests()
    print()

    total = extract_failures + logic_failures
    if total == 0:
        print("All tests passed.")
    else:
        print(f"{total} test(s) FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
