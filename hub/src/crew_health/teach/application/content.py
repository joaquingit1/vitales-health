"""Pinned configuration content of a check (E30 3.1, section 8, I30-17): the skills catalog, ui-copy words, the
indicator decimals and responder flow step texts, read by the exact versions a check pinned at creation. A missing
pinned version fails loudly; it is never silently re-pinned to another release."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_skills_catalog_schema import SkillsCatalogJson
from crew_health.kernel.config import ConfigReader, ReleaseHandle, TemplateRenderer
from crew_health.kernel.errors import ProblemError
from crew_health.teach.domain.catalog import SkillCatalog, SkillDefinition, TeachStep
from crew_health.teach.domain.errors import CatalogShapeError

PINNED: tuple[ConfigName, ...] = (
    ConfigName.SKILLS_CATALOG,
    ConfigName.UI_COPY,
    ConfigName.INDICATOR_CATALOG,
    ConfigName.RESPONDER_FLOWS,
)
STEP_FIELD = "skills-catalog.teach_steps.text"
TITLE_FIELD = "skills-catalog.card_title"
PHRASE_FIELD = "skills-catalog.checks.phrases"
RENDER_FIELDS: Mapping[str, frozenset[str]] = {
    STEP_FIELD: frozenset({"subject_name", "helper_name"}),
    TITLE_FIELD: frozenset({"subject_name", "helper_name"}),
    PHRASE_FIELD: frozenset({"count", "camera", "difference", "rate", "reason"}),
}


def config_error() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=False)


def decode(raw: bytes) -> object:
    """JSON with exact numbers: floats become Decimal, NaN and infinity are refused."""

    def refuse(_: str) -> object:
        raise ValueError("non-finite number")

    return json.loads(raw, parse_float=Decimal, parse_constant=refuse)


def parse_catalog(raw: bytes) -> SkillCatalog:
    """Schema check through the generated model, then the domain build with checks C1 to C5 (T30-27)."""
    try:
        SkillsCatalogJson.model_validate_json(raw)
        return SkillCatalog.from_document(decode(raw))
    except (ValidationError, ValueError) as error:
        raise CatalogShapeError("schema") from error


@dataclass(frozen=True, slots=True)
class PinnedContent:
    catalog: SkillCatalog
    ui_copy: Mapping[str, object]
    indicators: Mapping[str, object]
    flows: Mapping[str, object]

    def skill(self, skill_id: str) -> SkillDefinition:
        skill = self.catalog.skill(skill_id)
        if skill is None:
            raise config_error()
        return skill

    def designed_state(self, key: str) -> str:
        states = self.ui_copy.get("designed_states")
        value = states.get(key) if isinstance(states, Mapping) else None
        if not isinstance(value, str) or not value:
            raise config_error()
        return value

    def guided_check_word(self, key: str) -> str | None:
        """An optional ui-copy guided_check_words entry; None when the pinned ui-copy has no such word."""
        words = self.ui_copy.get("guided_check_words")
        value = words.get(key) if isinstance(words, Mapping) else None
        return value if isinstance(value, str) and value else None

    def decimals(self, code: str) -> int:
        items = self.indicators.get("indicators")
        for item in items if isinstance(items, list) else []:
            if isinstance(item, Mapping) and item.get("code") == code:
                value = item.get("decimals")
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
        raise config_error()

    def flow_step_text(self, flow_id: str, index: int) -> str:
        flows = self.flows.get("flows")
        for flow in flows if isinstance(flows, list) else []:
            if isinstance(flow, Mapping) and flow.get("id") == flow_id:
                steps = flow.get("immediate_steps")
                if isinstance(steps, list) and 0 <= index < len(steps) and isinstance(steps[index], str):
                    return cast(str, steps[index])
        raise config_error()


class ContentLoader:
    """Loads pinned documents by version through E01's ConfigReader; a bounded cache of parsed catalogs."""

    def __init__(self, reader: ConfigReader, *, max_cached: int) -> None:
        if not 1 <= max_cached <= 64:
            raise ValueError("cache bound outside 1 to 64")
        self._reader = reader
        self._max = max_cached
        self._cache: dict[tuple[str, ...], PinnedContent] = {}

    @staticmethod
    def pins_of(release: ReleaseHandle) -> dict[str, str]:
        return {name.value: release.version_of(name) for name in PINNED}

    def load(self, pins: Mapping[str, str]) -> PinnedContent:
        key = tuple(pins.get(name.value, "") for name in PINNED)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            content = PinnedContent(
                catalog=parse_catalog(self._raw(ConfigName.SKILLS_CATALOG, pins)),
                ui_copy=self._mapping(ConfigName.UI_COPY, pins),
                indicators=self._mapping(ConfigName.INDICATOR_CATALOG, pins),
                flows=self._mapping(ConfigName.RESPONDER_FLOWS, pins),
            )
        except (CatalogShapeError, ValueError) as error:
            raise config_error() from error
        if len(self._cache) >= self._max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = content
        return content

    def _raw(self, name: ConfigName, pins: Mapping[str, str]) -> bytes:
        version = pins.get(name.value)
        if version is None:
            raise config_error()
        try:
            return self._reader.document(name, version).raw
        except ProblemError as error:
            raise config_error() from error

    def _mapping(self, name: ConfigName, pins: Mapping[str, str]) -> Mapping[str, object]:
        value = decode(self._raw(name, pins))
        if not isinstance(value, Mapping):
            raise config_error()
        return cast(Mapping[str, object], value)


@dataclass(frozen=True, slots=True)
class RenderedStep:
    step_no: int
    kind: str
    actor: str
    text: str
    text_first_time: str | None
    landmark_id: str | None
    body_region: str | None
    count_seconds: int | None
    choices: tuple[tuple[str, str], ...]
    source_ids: tuple[str, ...]


def render_steps(
    content: PinnedContent,
    renderer: TemplateRenderer,
    steps: tuple[TeachStep, ...],
    names: Mapping[str, str],
) -> tuple[RenderedStep, ...]:
    """Every step text rendered by the hub from the pinned catalog or the responder flow it cites (X1 interim)."""
    rendered: list[RenderedStep] = []
    for step in steps:
        if step.from_flow_step is not None:
            text = content.flow_step_text(step.from_flow_step.flow_id, step.from_flow_step.index)
        else:
            text = renderer.render(STEP_FIELD, step.text or "", names)
        first = (
            None if step.text_first_time is None else renderer.render(STEP_FIELD, step.text_first_time, names)
        )
        rendered.append(
            RenderedStep(
                step_no=step.step_no,
                kind=step.kind,
                actor=step.actor,
                text=text,
                text_first_time=first,
                landmark_id=step.landmark_id,
                body_region=step.body_region,
                count_seconds=step.count_seconds,
                choices=tuple((c.answer, c.label) for c in step.choices),
                source_ids=step.source_ids,
            )
        )
    return tuple(rendered)
