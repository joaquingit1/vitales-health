"""C11 check 3 safety vocabulary, deliberately independent of configuration."""

import re

AMOUNT_UNITS: tuple[str, ...] = ("mg", "mcg", "µg", "g", "mL", "IU", "units")
MEDICINE_WORDS: tuple[str, ...] = ("medicine", "inhaler", "adrenaline", "glucose", "glucagon", "pain relief")
MEDICINE_PHRASE = "as in the onboard procedure"


def find_amounts(text: str) -> tuple[tuple[int, int], ...]:
    units = "|".join(re.escape(unit) for unit in AMOUNT_UNITS)
    pattern = re.compile(r"\d+(?:[,.]\d+)?\s*(?:" + units + r")\b", re.IGNORECASE)
    return tuple(match.span() for match in pattern.finditer(text))


def medicine_words(text: str) -> tuple[str, ...]:
    return tuple(
        word
        for word in MEDICINE_WORDS
        if re.search(
            r"\b" + re.escape(word) + (r"s?\b" if word == "medicine" else r"\b"), text, re.IGNORECASE
        )
    )


def needs_medicine_phrase(text: str) -> bool:
    return bool(medicine_words(text)) and MEDICINE_PHRASE not in text.casefold()
