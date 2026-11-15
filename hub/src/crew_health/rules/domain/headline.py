"""The Today headline (E24 3.5, C14 "Headline").

Computed from the active warnings visible to the person, sorted by `headline.rank` then `raised_at`:
a first warning whose headline is `solo` shows alone; otherwise up to `max_fragments` fragments of
non-solo warnings are joined by the separator with the first letter capitalised; with nothing active the
configured `none_text` shows.
"""

from __future__ import annotations

from collections.abc import Iterable

from crew_health.rules.domain.definitions import HeadlineRule
from crew_health.rules.domain.ordering import ordered
from crew_health.rules.domain.views import HeadlineView
from crew_health.rules.domain.vocabulary import ACTIVE
from crew_health.rules.domain.warning import Warning


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:]


def headline(warnings: Iterable[Warning], rule: HeadlineRule) -> HeadlineView:
    active = ordered(w for w in warnings if w.state in ACTIVE)
    if not active:
        return HeadlineView(rule.none_text, ())
    first = active[0]
    if first.headline.solo:
        return HeadlineView(first.headline.fragment, (first.warning_id,))
    chosen = [w for w in active if not w.headline.solo][: rule.max_fragments]
    text = _capitalise(rule.separator.join(w.headline.fragment for w in chosen))
    return HeadlineView(text, tuple(w.warning_id for w in chosen))
