"""The sentence rule of CONTRACTS 13 and source ID handling.

Remove parenthesised source IDs such as (EV01) or (EV01, C25), collapse white space, and split after `.`, `!` or
`?` followed by white space and an uppercase letter or a quotation mark.
"""

from __future__ import annotations

import re

_ID = r"(?:EV|RT|RS|SK|S|C|N|L)\d{2}"  # common.schema.json source_id pattern
_GROUP = re.compile(r"\s*\(\s*" + _ID + r"(?:\s*,\s*" + _ID + r")*\s*\)")
_FIND = re.compile(_ID)
_SPACE = re.compile(r"\s+")
_ENDINGS = ".!?"
_QUOTES = '"“'  # ASCII and typographic opening quotation marks

NOTHING_TO_DO = "Nothing to do"  # CONTRACTS 12 living_here: such sentences are skipped


def source_ids_in(text: str) -> tuple[str, ...]:
    """Source IDs named inside parentheses, in order of appearance, without repeats."""
    seen: list[str] = []
    for group in _GROUP.findall(text):
        for found in _FIND.findall(group):
            if found not in seen:
                seen.append(found)
    return tuple(seen)


def strip_source_ids(text: str) -> str:
    return _SPACE.sub(" ", _GROUP.sub("", text)).strip()


def split_sentences(text: str) -> tuple[str, ...]:
    """Split after . ! or ? followed by white space and an uppercase letter or quotation mark."""
    cleaned = strip_source_ids(text)
    if not cleaned:
        return ()
    sentences: list[str] = []
    start = 0
    for index in range(len(cleaned) - 2):
        if cleaned[index] in _ENDINGS and cleaned[index + 1] == " ":
            following = cleaned[index + 2]
            if following.isupper() or following in _QUOTES:
                sentences.append(cleaned[start : index + 1])
                start = index + 2
    sentences.append(cleaned[start:])
    return tuple(sentences)


def sentences_skipping(text: str, prefix: str = NOTHING_TO_DO) -> tuple[str, ...]:
    return tuple(s for s in split_sentences(text) if not s.startswith(prefix))
