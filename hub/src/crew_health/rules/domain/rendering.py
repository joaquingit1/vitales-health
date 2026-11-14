"""Rendering of a warning's words at raise (E24 5.2, C11, C14).

Rendered once at raise and stored, never re-rendered. Words come from the definition through the
injected `TemplateRenderer`; sensor values come pre-formatted from E23 per limit, so this module does no
sensor arithmetic or formatting. The steps follow the C13 sentence rule or are listed or supplied by the
assistant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from crew_health.kernel.config import TemplateRenderer
from crew_health.rules.domain.decisions import RenderValues
from crew_health.rules.domain.definitions import (
    AnswerCondition,
    GroundSilenceCondition,
    SupplyCoverLowCondition,
    WarningDefinition,
)
from crew_health.rules.domain.errors import InvalidInput
from crew_health.rules.domain.vocabulary import Basis, RaisedBy, StepsFrom
from crew_health.rules.domain.warning import Citation, DefinitionPin

FIELD_TITLE: Final = "warning.title"
FIELD_WHAT: Final = "warning.what"
FIELD_WHY: Final = "warning.why"
FIELD_FRAGMENT: Final = "warning.headline.fragment"
FIELD_CITATION: Final = "warning.citation"

_SOURCE_PAREN: Final = re.compile(r"\s*\((?:[A-Z]{1,2}\d{2})(?:,\s*[A-Z]{1,2}\d{2})*\)")
_SPACE: Final = re.compile(r"\s+")
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])")


@dataclass(frozen=True, slots=True)
class RenderedContent:
    title: str
    what: str
    why: str
    steps: tuple[str, ...]
    headline_fragment: str
    citation: Citation
    body_regions: tuple[str, ...]


def sentences(text: str) -> tuple[str, ...]:
    """The C13 sentence rule: drop source IDs in parentheses, collapse white space, split after `. ! ?`."""
    cleaned = _SPACE.sub(" ", _SOURCE_PAREN.sub("", text)).strip()
    return tuple(part.strip() for part in _SENTENCE_BREAK.split(cleaned) if part.strip())


def steps_for(
    d: WarningDefinition, what_to_do: Mapping[str, str] | None, values: RenderValues
) -> tuple[str, ...]:
    spec = d.content.steps
    if spec.source is StepsFrom.LISTED:
        return spec.items
    if spec.source is StepsFrom.ASSISTANT:
        steps = values.assistant_steps
    else:
        text = None if what_to_do is None else what_to_do.get(d.level.value)
        steps = sentences(text) if text else ()
    if not steps:
        raise InvalidInput(f"{d.id}: no steps at level {d.level.value}")
    return steps


def weakest_basis(pin: DefinitionPin) -> Basis | None:
    """The weakest basis among condition, clear, steps and, for sensor limits, the limit and its clear."""
    d = pin.definition
    found: list[Basis] = [d.clear.basis]
    cond = d.condition
    if isinstance(cond, AnswerCondition) and cond.within_days_after_event is not None:
        found.append(cond.within_days_after_event.basis)
    if isinstance(cond, GroundSilenceCondition | SupplyCoverLowCondition):
        found.append(cond.basis)
    if d.content.steps.basis is not None:
        found.append(d.content.steps.basis)
    if pin.limit is not None:
        found.extend(b for b in (pin.limit.basis, pin.limit.clear_basis) if b is not None)
    if not found:
        return None
    return Basis.TEAM_DEFAULT if Basis.TEAM_DEFAULT in found else Basis.SOURCED


def render_content(
    pin: DefinitionPin,
    values: RenderValues,
    renderer: TemplateRenderer,
    what_to_do: Mapping[str, str] | None,
) -> RenderedContent:
    d = pin.definition
    c = d.content
    ph = values.placeholders
    regions = (
        (values.body_region,) if d.raised_by is RaisedBy.ASSISTANT and values.body_region else c.body_regions
    )
    return RenderedContent(
        title=renderer.render(FIELD_TITLE, c.title, ph),
        what=renderer.render(FIELD_WHAT, c.what, ph),
        why=renderer.render(FIELD_WHY, c.why, ph),
        steps=steps_for(d, what_to_do, values),
        headline_fragment=renderer.render(FIELD_FRAGMENT, c.headline.fragment, ph),
        citation=Citation(
            renderer.render(FIELD_CITATION, c.citation_template, ph), c.source_ids, weakest_basis(pin)
        ),
        body_regions=regions,
    )
