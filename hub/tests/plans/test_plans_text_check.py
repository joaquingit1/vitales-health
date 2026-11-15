"""C11 safety vocabulary has fixed code-owned meaning."""

import pytest

from crew_health.plans.domain.text_check import find_amounts, medicine_words, needs_medicine_phrase


@pytest.mark.trace("R57", "EI6", "T28-18")
def test_amount_units_boundaries_and_spans() -> None:
    for amount in ("2 mg", "1,5 mcg", "2 µg", "2g", "3.5 mL", "4 IU", "1 units", "2 MG"):
        text = "before " + amount + " after"
        assert find_amounts(text) == ((7, 7 + len(amount)),)
    for text in ("10 seconds", "60 seconds", "1 unit", "2 grams", "2 mLextra"):
        assert find_amounts(text) == ()


@pytest.mark.trace("R57", "EI6", "T28-18")
def test_medicine_words_are_ordered_and_phrase_is_required() -> None:
    assert medicine_words("PAIN RELIEF and medicines, inhaler, adrenaline, glucose and glucagon") == (
        "medicine",
        "inhaler",
        "adrenaline",
        "glucose",
        "glucagon",
        "pain relief",
    )
    assert medicine_words("biomedicine and glucosemeter") == ()
    assert needs_medicine_phrase("Use pain relief")
    assert not needs_medicine_phrase("Medicines AS IN THE ONBOARD PROCEDURE")
