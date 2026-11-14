"""Warning definitions parsed from `warning-rules.json` (E24 3.2, 3.4).

The loader validates the document against its JSON schema first. This parser repeats the structural
checks by hand, because the domain imports no schema library, and then enforces the raiser matrix
(DE24-2) at construction. Every failure is a `DefinitionInvalid`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from crew_health.rules.domain.errors import DefinitionInvalid
from crew_health.rules.domain.vocabulary import (
    ASSISTANT_LEVELS,
    BODY_REGIONS,
    SENSOR_KINDS,
    Basis,
    ClearBy,
    ConditionKind,
    EnvironmentId,
    Level,
    Presence,
    RaisedBy,
    Scope,
    StepsFrom,
)

CONFIG_ID: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SOURCE_ID: Final = re.compile(r"^(EV|RT|RS|SK|S|C|N|L)\d{2}$")
TEMPLATE: Final = re.compile(r"^[^{}]*(\{[a-z_]+\}[^{}]*)*$")
TO_BE_SET_BY: Final[frozenset[str]] = frozenset({"clinical review", "the flight surgeon", "flight rules"})

type FactValue = bool | str | int | float


@dataclass(frozen=True, slots=True)
class SensorLimitCondition:
    limit_id: str
    kind: ConditionKind = ConditionKind.SENSOR_LIMIT


@dataclass(frozen=True, slots=True)
class SensorQuietCondition:
    sensor_type_ids: tuple[str, ...]
    kind: ConditionKind = ConditionKind.SENSOR_QUIET


@dataclass(frozen=True, slots=True)
class MissionEventCondition:
    event_types: tuple[str, ...]
    clear_event_types: tuple[str, ...]
    kind: ConditionKind = ConditionKind.MISSION_EVENT


@dataclass(frozen=True, slots=True)
class WithinDays:
    event_type: str
    days: int
    basis: Basis


@dataclass(frozen=True, slots=True)
class AnswerCondition:
    question_id: str
    presence: tuple[Presence, ...]
    within_days_after_event: WithinDays | None
    kind: ConditionKind = ConditionKind.ANSWER


@dataclass(frozen=True, slots=True)
class StateFactCondition:
    fact: str
    equals: FactValue
    kind: ConditionKind = ConditionKind.STATE_FACT


@dataclass(frozen=True, slots=True)
class InsightCondition:
    topic_ids: tuple[str, ...]
    kind: ConditionKind = ConditionKind.ASSISTANT_INSIGHT


@dataclass(frozen=True, slots=True)
class GroundSilenceCondition:
    after_next_contact_seconds: int | None
    basis: Basis
    to_be_set_by: str | None
    kind: ConditionKind = ConditionKind.GROUND_SILENCE


@dataclass(frozen=True, slots=True)
class SupplyCoverLowCondition:
    below_uses: int | None
    basis: Basis
    to_be_set_by: str | None
    kind: ConditionKind = ConditionKind.SUPPLY_COVER_LOW


type Condition = (
    SensorLimitCondition
    | SensorQuietCondition
    | MissionEventCondition
    | AnswerCondition
    | StateFactCondition
    | InsightCondition
    | GroundSilenceCondition
    | SupplyCoverLowCondition
)


@dataclass(frozen=True, slots=True)
class ClearRule:
    by_engine: ClearBy
    person_may_resolve: bool
    expires_after_seconds: int | None
    basis: Basis


@dataclass(frozen=True, slots=True)
class StepsSpec:
    source: StepsFrom
    items: tuple[str, ...]
    basis: Basis | None


@dataclass(frozen=True, slots=True)
class HeadlineSpec:
    fragment: str
    rank: int
    solo: bool


@dataclass(frozen=True, slots=True)
class ContentSpec:
    title: str
    what: str
    why: str
    steps: StepsSpec
    body_regions: tuple[str, ...]
    headline: HeadlineSpec
    citation_template: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WarningDefinition:
    id: str
    raised_by: RaisedBy
    level: Level
    hazard_id: str | None
    hazard_level_index: int | None
    sensor_type_id: str | None
    environments: tuple[EnvironmentId, ...]
    scope: Scope
    condition: Condition
    clear: ClearRule
    content: ContentSpec

    def __post_init__(self) -> None:
        _enforce_raiser_matrix(self)
        _enforce_kind_scope(self)


@dataclass(frozen=True, slots=True)
class HeadlineRule:
    max_fragments: int
    separator: str
    none_text: str


@dataclass(frozen=True, slots=True)
class RulesDocument:
    notice: str
    headline: HeadlineRule
    definitions: tuple[WarningDefinition, ...]

    def by_id(self) -> Mapping[str, WarningDefinition]:
        return {d.id: d for d in self.definitions}


def _enforce_raiser_matrix(d: WarningDefinition) -> None:
    """DE24-2: who raises which level, enforced at construction (E24 3.2, 3.4)."""
    kind = d.condition.kind
    if (kind in SENSOR_KINDS) != (d.raised_by is RaisedBy.SENSOR_LIMIT):
        raise DefinitionInvalid(f"{d.id}: sensor condition kinds and raised_by sensor_limit go together")
    if (kind is ConditionKind.ASSISTANT_INSIGHT) != (d.raised_by is RaisedBy.ASSISTANT):
        raise DefinitionInvalid(f"{d.id}: assistant_insight and raised_by assistant go together")
    if d.raised_by is RaisedBy.ASSISTANT and d.level not in ASSISTANT_LEVELS:
        raise DefinitionInvalid(f"{d.id}: assistant definitions are info or watch only")
    if kind not in SENSOR_KINDS and kind is not ConditionKind.ASSISTANT_INSIGHT:
        if d.raised_by is not RaisedBy.RULE:
            raise DefinitionInvalid(f"{d.id}: every other condition kind is raised by rule")


def _enforce_kind_scope(d: WarningDefinition) -> None:
    """Answer and insight warnings are keyed by person (E24 3.5 scope key column)."""
    if d.condition.kind in {ConditionKind.ANSWER, ConditionKind.ASSISTANT_INSIGHT}:
        if d.scope is not Scope.PERSON:
            raise DefinitionInvalid(f"{d.id}: this condition kind needs scope person")
    if d.content.steps.source is StepsFrom.ASSISTANT and d.raised_by is not RaisedBy.ASSISTANT:
        raise DefinitionInvalid(f"{d.id}: steps from the assistant need raised_by assistant")
    if d.content.steps.source is StepsFrom.HAZARD_WHAT_TO_DO and d.hazard_id is None:
        raise DefinitionInvalid(f"{d.id}: hazard_what_to_do steps need a hazard_id")


class _Reader:
    """Typed access to one JSON object, reporting the path of every failure."""

    def __init__(self, raw: object, path: str) -> None:
        if not isinstance(raw, Mapping) or not all(isinstance(k, str) for k in raw):
            raise DefinitionInvalid(f"{path}: object expected")
        self._raw: Mapping[str, object] = raw
        self.path = path

    def closed(self, keys: Sequence[str]) -> None:
        extra = sorted(set(self._raw) - set(keys))
        missing = sorted(set(keys) - set(self._raw))
        if extra or missing:
            raise DefinitionInvalid(f"{self.path}: unexpected {extra} missing {missing}")

    def has(self, key: str) -> bool:
        return key in self._raw

    def value(self, key: str) -> object:
        if key not in self._raw:
            raise DefinitionInvalid(f"{self.path}.{key}: missing")
        return self._raw[key]

    def text(self, key: str, *, pattern: re.Pattern[str] | None = None) -> str:
        v = self.value(key)
        if not isinstance(v, str) or not v or (pattern is not None and not pattern.fullmatch(v)):
            raise DefinitionInvalid(f"{self.path}.{key}: invalid text")
        return v

    def free_text(self, key: str) -> str:
        v = self.value(key)
        if not isinstance(v, str):
            raise DefinitionInvalid(f"{self.path}.{key}: text expected")
        return v

    def opt_text(self, key: str, *, pattern: re.Pattern[str] | None = None) -> str | None:
        return None if self.value(key) is None else self.text(key, pattern=pattern)

    def integer(self, key: str, *, minimum: int) -> int:
        v = self.value(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
            raise DefinitionInvalid(f"{self.path}.{key}: integer >= {minimum} expected")
        return v

    def opt_integer(self, key: str, *, minimum: int) -> int | None:
        return None if self.value(key) is None else self.integer(key, minimum=minimum)

    def boolean(self, key: str) -> bool:
        v = self.value(key)
        if not isinstance(v, bool):
            raise DefinitionInvalid(f"{self.path}.{key}: boolean expected")
        return v

    def obj(self, key: str) -> _Reader:
        return _Reader(self.value(key), f"{self.path}.{key}")

    def texts(self, key: str, *, pattern: re.Pattern[str] | None = None, minimum: int = 0) -> tuple[str, ...]:
        v = self.value(key)
        if not isinstance(v, list) or len(v) < minimum:
            raise DefinitionInvalid(f"{self.path}.{key}: list of {minimum}+ expected")
        out: list[str] = []
        for item in v:
            if not isinstance(item, str) or not item or (pattern is not None and not pattern.fullmatch(item)):
                raise DefinitionInvalid(f"{self.path}.{key}: invalid item")
            out.append(item)
        return tuple(out)

    def enum[E: StrEnum](self, key: str, kind: type[E]) -> E:
        v = self.value(key)
        if not isinstance(v, str):
            raise DefinitionInvalid(f"{self.path}.{key}: not in the fixed vocabulary")
        try:
            return kind(v)
        except ValueError as error:
            raise DefinitionInvalid(f"{self.path}.{key}: not in the fixed vocabulary") from error


def _parse_within(r: _Reader) -> WithinDays:
    r.closed(("event_type", "days", "basis"))
    return WithinDays(
        r.text("event_type", pattern=CONFIG_ID), r.integer("days", minimum=1), r.enum("basis", Basis)
    )


def _parse_fact_value(r: _Reader) -> FactValue:
    v = r.value("equals")
    if isinstance(v, bool | str | int | float):
        return v
    raise DefinitionInvalid(f"{r.path}.equals: boolean, string or number expected")


def _parse_condition(r: _Reader) -> Condition:
    kind = r.enum("kind", ConditionKind)
    match kind:
        case ConditionKind.SENSOR_LIMIT:
            r.closed(("kind", "limit_id"))
            return SensorLimitCondition(r.text("limit_id", pattern=CONFIG_ID))
        case ConditionKind.SENSOR_QUIET:
            r.closed(("kind", "sensor_type_ids"))
            return SensorQuietCondition(r.texts("sensor_type_ids", pattern=CONFIG_ID))
        case ConditionKind.MISSION_EVENT:
            r.closed(("kind", "event_types", "clear_event_types"))
            return MissionEventCondition(
                r.texts("event_types", pattern=CONFIG_ID, minimum=1),
                r.texts("clear_event_types", pattern=CONFIG_ID),
            )
        case ConditionKind.ANSWER:
            return _parse_answer(r)
        case ConditionKind.STATE_FACT:
            r.closed(("kind", "fact", "equals"))
            return StateFactCondition(r.text("fact", pattern=CONFIG_ID), _parse_fact_value(r))
        case ConditionKind.ASSISTANT_INSIGHT:
            r.closed(("kind", "topic_ids"))
            return InsightCondition(r.texts("topic_ids", pattern=CONFIG_ID, minimum=1))
        case ConditionKind.GROUND_SILENCE:
            r.closed(("kind", "after_next_contact_seconds", "basis", "to_be_set_by"))
            return GroundSilenceCondition(
                r.opt_integer("after_next_contact_seconds", minimum=0),
                r.enum("basis", Basis),
                _to_be_set_by(r),
            )
        case ConditionKind.SUPPLY_COVER_LOW:
            r.closed(("kind", "below_uses", "basis", "to_be_set_by"))
            return SupplyCoverLowCondition(
                r.opt_integer("below_uses", minimum=0), r.enum("basis", Basis), _to_be_set_by(r)
            )
    raise DefinitionInvalid(f"{r.path}: unknown condition kind")  # pragma: no cover


def _parse_answer(r: _Reader) -> AnswerCondition:
    keys = ["kind", "question_id", "presence"]
    if r.has("within_days_after_event"):
        keys.append("within_days_after_event")
    r.closed(keys)
    raw_presence = r.texts("presence", minimum=1)
    try:
        presence = tuple(Presence(p) for p in raw_presence)
    except ValueError as error:
        raise DefinitionInvalid(f"{r.path}.presence: not in the fixed vocabulary") from error
    within = _parse_within(r.obj("within_days_after_event")) if r.has("within_days_after_event") else None
    return AnswerCondition(r.text("question_id", pattern=CONFIG_ID), presence, within)


def _to_be_set_by(r: _Reader) -> str | None:
    v = r.opt_text("to_be_set_by")
    if v is not None and v not in TO_BE_SET_BY:
        raise DefinitionInvalid(f"{r.path}.to_be_set_by: not in the fixed vocabulary")
    return v


def _parse_clear(r: _Reader) -> ClearRule:
    r.closed(("by_engine", "person_may_resolve", "expires_after_seconds", "basis"))
    return ClearRule(
        r.enum("by_engine", ClearBy),
        r.boolean("person_may_resolve"),
        r.opt_integer("expires_after_seconds", minimum=1),
        r.enum("basis", Basis),
    )


def _parse_steps(r: _Reader) -> StepsSpec:
    source = r.enum("from", StepsFrom)
    if source is StepsFrom.LISTED:
        r.closed(("from", "items", "basis"))
        return StepsSpec(source, r.texts("items", minimum=1), r.enum("basis", Basis))
    r.closed(("from",))
    return StepsSpec(source, (), None)


def _parse_regions(r: _Reader) -> tuple[str, ...]:
    regions = r.texts("body_regions")
    if len(set(regions)) != len(regions) or not set(regions) <= BODY_REGIONS:
        raise DefinitionInvalid(f"{r.path}.body_regions: unique values of the fixed set expected")
    return regions


def _parse_content(r: _Reader) -> ContentSpec:
    r.closed(("title", "what", "why", "steps", "body_regions", "headline", "citation_template", "source_ids"))
    head = r.obj("headline")
    head.closed(("fragment", "rank", "solo"))
    sources = r.texts("source_ids", pattern=SOURCE_ID)
    if len(set(sources)) != len(sources):
        raise DefinitionInvalid(f"{r.path}.source_ids: unique values expected")
    return ContentSpec(
        r.text("title", pattern=TEMPLATE),
        r.text("what", pattern=TEMPLATE),
        r.text("why"),
        _parse_steps(r.obj("steps")),
        _parse_regions(r),
        HeadlineSpec(
            head.text("fragment", pattern=TEMPLATE), head.integer("rank", minimum=0), head.boolean("solo")
        ),
        r.text("citation_template", pattern=TEMPLATE),
        sources,
    )


def parse_definition(raw: object, path: str = "definition") -> WarningDefinition:
    r = _Reader(raw, path)
    r.closed(
        (
            "id", "raised_by", "level", "hazard_id", "hazard_level_index", "sensor_type_id",
            "environments", "scope", "condition", "clear", "content",
        )
    )  # fmt: skip
    envs_raw = r.texts("environments", minimum=1)
    if len(set(envs_raw)) != len(envs_raw):
        raise DefinitionInvalid(f"{path}.environments: unique values expected")
    try:
        envs = tuple(EnvironmentId(e) for e in envs_raw)
    except ValueError as error:
        raise DefinitionInvalid(f"{path}.environments: not in the fixed vocabulary") from error
    return WarningDefinition(
        id=r.text("id", pattern=CONFIG_ID),
        raised_by=r.enum("raised_by", RaisedBy),
        level=r.enum("level", Level),
        hazard_id=r.opt_text("hazard_id", pattern=CONFIG_ID),
        hazard_level_index=r.opt_integer("hazard_level_index", minimum=0),
        sensor_type_id=r.opt_text("sensor_type_id", pattern=CONFIG_ID),
        environments=envs,
        scope=r.enum("scope", Scope),
        condition=_parse_condition(r.obj("condition")),
        clear=_parse_clear(r.obj("clear")),
        content=_parse_content(r.obj("content")),
    )


def parse_rules_document(raw: object) -> RulesDocument:
    r = _Reader(raw, "warning-rules")
    r.closed(("notice", "headline", "definitions"))
    head = r.obj("headline")
    head.closed(("max_fragments", "separator", "none_text"))
    rule = HeadlineRule(
        head.integer("max_fragments", minimum=1), head.free_text("separator"), head.text("none_text")
    )
    items = r.value("definitions")
    if not isinstance(items, list) or not items:
        raise DefinitionInvalid("warning-rules.definitions: non-empty list expected")
    definitions = tuple(parse_definition(item, f"definitions[{i}]") for i, item in enumerate(items))
    ids = [d.id for d in definitions]
    if len(set(ids)) != len(ids):
        raise DefinitionInvalid("warning-rules.definitions: duplicate id")
    return RulesDocument(r.text("notice"), rule, definitions)


def condition_to_raw(c: Condition) -> dict[str, object]:
    match c:
        case SensorLimitCondition():
            return {"kind": c.kind.value, "limit_id": c.limit_id}
        case SensorQuietCondition():
            return {"kind": c.kind.value, "sensor_type_ids": list(c.sensor_type_ids)}
        case MissionEventCondition():
            return {
                "kind": c.kind.value,
                "event_types": list(c.event_types),
                "clear_event_types": list(c.clear_event_types),
            }
        case AnswerCondition():
            out: dict[str, object] = {
                "kind": c.kind.value,
                "question_id": c.question_id,
                "presence": [p.value for p in c.presence],
            }
            if c.within_days_after_event is not None:
                w = c.within_days_after_event
                out["within_days_after_event"] = {
                    "event_type": w.event_type,
                    "days": w.days,
                    "basis": w.basis.value,
                }
            return out
        case StateFactCondition():
            return {"kind": c.kind.value, "fact": c.fact, "equals": c.equals}
        case InsightCondition():
            return {"kind": c.kind.value, "topic_ids": list(c.topic_ids)}
        case GroundSilenceCondition():
            return {
                "kind": c.kind.value,
                "after_next_contact_seconds": c.after_next_contact_seconds,
                "basis": c.basis.value,
                "to_be_set_by": c.to_be_set_by,
            }
        case SupplyCoverLowCondition():
            return {
                "kind": c.kind.value,
                "below_uses": c.below_uses,
                "basis": c.basis.value,
                "to_be_set_by": c.to_be_set_by,
            }


def content_to_raw(c: ContentSpec) -> dict[str, object]:
    steps: dict[str, object] = {"from": c.steps.source.value}
    if c.steps.source is StepsFrom.LISTED and c.steps.basis is not None:
        steps = {"from": "listed", "items": list(c.steps.items), "basis": c.steps.basis.value}
    return {
        "title": c.title,
        "what": c.what,
        "why": c.why,
        "steps": steps,
        "body_regions": list(c.body_regions),
        "headline": {"fragment": c.headline.fragment, "rank": c.headline.rank, "solo": c.headline.solo},
        "citation_template": c.citation_template,
        "source_ids": list(c.source_ids),
    }


def definition_to_raw(d: WarningDefinition) -> dict[str, object]:
    return {
        "id": d.id,
        "raised_by": d.raised_by.value,
        "level": d.level.value,
        "hazard_id": d.hazard_id,
        "hazard_level_index": d.hazard_level_index,
        "sensor_type_id": d.sensor_type_id,
        "environments": [e.value for e in d.environments],
        "scope": d.scope.value,
        "condition": condition_to_raw(d.condition),
        "clear": {
            "by_engine": d.clear.by_engine.value,
            "person_may_resolve": d.clear.person_may_resolve,
            "expires_after_seconds": d.clear.expires_after_seconds,
            "basis": d.clear.basis.value,
        },
        "content": content_to_raw(d.content),
    }
