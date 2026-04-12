from __future__ import annotations


def get_category_bucket(category_slug: str | None) -> str | None:
    """
    Map a category slug to a broader editorial bucket.

    Examples:
    - business.markets -> business
    - business.economy -> business
    - sport.football.premier-league -> sport
    - politics.uk -> politics
    """
    if not category_slug:
        return None

    return category_slug.split(".")[0]