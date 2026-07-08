"""One display title everywhere: prefer the clean summariser headline, fall back to the raw
representative_title. The DB fan-out (display_titles) is a thin query over this pure rule."""
from core.pipeline.data.bulletin.selection import pick_display_title


def test_prefers_summariser_headline_when_present():
    assert pick_display_title(
        "Argentina beat Egypt in World Cup thriller",
        "VIDEO: Happy tears! Lionel Messi follows Neymar & Cristiano Ronaldo in allowing emotion "
        "to spill out after seeing Argentina prevail in five-goal World Cup classic with Egypt",
    ) == "Argentina beat Egypt in World Cup thriller"


def test_falls_back_to_representative_when_no_headline():
    assert pick_display_title(None, "Egypt Coach Alleges Injustice After World Cup Loss") \
        == "Egypt Coach Alleges Injustice After World Cup Loss"
    assert pick_display_title("", "Egypt Coach Alleges Injustice") == "Egypt Coach Alleges Injustice"
    assert pick_display_title("   ", "Rep title") == "Rep title"


def test_trims_and_handles_all_empty():
    assert pick_display_title("  Clean headline  ", "rep") == "Clean headline"
    assert pick_display_title(None, None) == ""
    assert pick_display_title("", "") == ""
