"""The sensor card and Habitat content (CONTRACTS 12, 13; E23 3.7 card channel, 3.9)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from crew_health.sensors.domain.channel import (
    ChannelFacts,
    Freshness,
    Level,
    Origin,
    SensorTypeFacts,
    level_rank,
)
from crew_health.sensors.domain.errors import ConfigInconsistent
from crew_health.sensors.domain.evaluation import (
    Evaluation,
    LimitFacts,
    Status,
    limits_for,
    no_reading_evaluation,
)
from crew_health.sensors.domain.reading import arithmetic, decimal_scope, round_wire
from crew_health.sensors.domain.sentences import (
    sentences_skipping,
    source_ids_in,
    split_sentences,
    strip_source_ids,
)

# Card channel order, DE23-10: beyond_limit by level descending, then these.
STATUS_ORDER: tuple[Status, ...] = (
    "beyond_limit",
    "stale",
    "insufficient_data",
    "no_reading",
    "within_limit",
    "threshold_pending",
    "no_limit_on_file",
)
# ui-copy keys for a null shown_level (CONTRACTS 13 card table); keys are vocabulary, words stay in ui-copy.
SHOWN_STATE_KEY: dict[Status, str] = {
    "within_limit": "within_limit_word",
    "threshold_pending": "designed_states.threshold_pending",
    "no_limit_on_file": "designed_states.no_limit_on_file",
    "insufficient_data": "designed_states.insufficient_data",
    "stale": "designed_states.sensor_quiet",
    "no_reading": "designed_states.no_reading",
    "beyond_limit": "",
}
TRACE_SEPARATOR = "\n"
ReadingState = Literal["current", "stale", "no_reading"]


@dataclass(frozen=True, slots=True)
class HazardEffect:
    text: str
    body_region: str | None
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HazardFacts:
    id: str
    name: str
    kind: str
    effects: tuple[HazardEffect, ...]
    by_environment: tuple[tuple[str, str], ...]
    what_to_do: tuple[tuple[str, str], ...]
    source_ids: tuple[str, ...]

    def words_for(self, environment_id: str) -> str | None:
        return next((text for key, text in self.by_environment if key == environment_id), None)

    def to_do_at(self, level: str) -> str | None:
        return next((text for key, text in self.what_to_do if key == level), None)


@dataclass(frozen=True, slots=True)
class EffectFilter:
    hazard_id: str
    effect_index: int
    effect_starts_with: str
    only_in: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChannelView:
    channel: ChannelFacts
    evaluation: Evaluation
    freshness: Freshness
    stale_since: datetime | None
    provenance_trace: str | None


@dataclass(frozen=True, slots=True)
class LinkedWarning:
    warning_id: str
    level: Level


@dataclass(frozen=True, slots=True)
class DisplayValue:
    value: Decimal | None
    unit: str
    text: str


@dataclass(frozen=True, slots=True)
class LimitMark:
    limit_id: str
    value: Decimal
    unit: str
    comparator: str
    role: str
    label: str


@dataclass(frozen=True, slots=True)
class EffectLine:
    text: str
    body_region: str | None


@dataclass(frozen=True, slots=True)
class Citation:
    text: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CardInput:
    sensor_type: SensorTypeFacts
    environment_id: str
    channels: tuple[ChannelView, ...]
    limits: tuple[LimitFacts, ...]
    hazard: HazardFacts
    filters: tuple[EffectFilter, ...]
    linked_warnings: tuple[LinkedWarning, ...]
    unsupported_text: str
    idle_origin: Origin
    idle_source_label: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class CardFacts:
    sensor_type_id: str
    hazard_id: str
    environment_id: str
    name: str
    long_name: str
    body_region: str | None
    channel_id: str | None
    reading_state: ReadingState
    display_value: DisplayValue
    context_label: str
    source_label: str
    origin_mode: Origin
    evaluation: Evaluation
    shown_level: Level | None
    shown_state_key: str | None
    linked_warning_ids: tuple[str, ...]
    display_range: tuple[Decimal, Decimal]
    limit_marks: tuple[LimitMark, ...]
    stale_since: datetime | None
    trace: str | None
    effects: tuple[EffectLine, ...]
    todo: tuple[str, ...]
    here: str
    citation: Citation


def format_value(value: Decimal, decimals: int, trim: bool) -> str:
    """Round half even to the catalog decimals; optionally trim trailing zeros. No negative zero."""
    with decimal_scope(arithmetic()):
        rounded = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)
    if rounded == 0:
        rounded = abs(rounded)
    text = format(rounded, "f")
    if trim and "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def rank_key(view: ChannelView) -> tuple[int, int, str]:
    """Lower sorts first. Beyond limit by level descending, then the status order, ties by channel ID."""
    status = view.evaluation.status
    level = view.evaluation.level
    boost = -level_rank(level) if status == "beyond_limit" and level is not None else 0
    return (STATUS_ORDER.index(status), boost, view.channel.channel_id)


def select_card_channel(views: Sequence[ChannelView]) -> ChannelView | None:
    return min(views, key=rank_key, default=None)


def shown_level(evaluated: Level | None, linked: Sequence[LinkedWarning]) -> Level | None:
    levels = [w.level for w in linked]
    if evaluated is not None:
        levels.append(evaluated)
    return max(levels, key=level_rank, default=None)


def _display_text(sensor_type: SensorTypeFacts, evaluation: Evaluation) -> DisplayValue:
    value = evaluation.aggregated_value
    if value is None:
        return DisplayValue(None, sensor_type.unit, sensor_type.no_reading_label)
    return DisplayValue(
        round_wire(value),
        sensor_type.unit,
        format_value(value, sensor_type.decimals, sensor_type.trim_trailing_zeros),
    )


def _line(sensor_type: SensorTypeFacts, view: ChannelView) -> str:
    where = [view.channel.source_label]
    if view.channel.location_label:
        where.append(view.channel.location_label)
    value = _display_text(sensor_type, view.evaluation)
    return (", ".join(where) + ": " + value.text + " " + sensor_type.unit_display).rstrip()


def _trace(
    sensor_type: SensorTypeFacts, chosen: ChannelView | None, views: Sequence[ChannelView]
) -> str | None:
    lines: list[str] = []
    if chosen is not None and chosen.provenance_trace:
        lines.append(chosen.provenance_trace)
    lines.extend(_line(sensor_type, v) for v in sorted(views, key=rank_key) if v is not chosen)
    return TRACE_SEPARATOR.join(lines) if lines else None


def shown_effects(
    hazard: HazardFacts, environment_id: str, filters: Sequence[EffectFilter]
) -> tuple[HazardEffect, ...]:
    """Effects after effect_filters: an effect at effect_index shows only in the environments in only_in."""
    shown: list[HazardEffect] = []
    for index, effect in enumerate(hazard.effects):
        rule = next((f for f in filters if f.hazard_id == hazard.id and f.effect_index == index), None)
        if rule is not None:
            if not effect.text.startswith(rule.effect_starts_with):
                raise ConfigInconsistent("effect filter does not match " + hazard.id)
            if environment_id not in rule.only_in:
                continue
        shown.append(effect)
    return tuple(shown)


def effect_lines(effects: Sequence[HazardEffect], unsupported_text: str) -> tuple[EffectLine, ...]:
    """An effect with no source reads the configured unsupported state and lights no body region."""
    return tuple(
        EffectLine(strip_source_ids(e.text), e.body_region)
        if e.source_ids
        else EffectLine(unsupported_text, None)
        for e in effects
    )


def _citation(
    sensor_type: SensorTypeFacts, marks: Sequence[LimitFacts], effects: Sequence[HazardEffect]
) -> Citation:
    limit_ids = sorted({i for m in marks for i in m.source_ids})
    effect_ids = sorted({i for e in effects for i in e.source_ids})
    parts: list[str] = []
    if limit_ids:
        parts.append("Limit: " + ", ".join(limit_ids) + ".")
    if effect_ids:
        parts.append("What it can do: " + ", ".join(effect_ids) + ".")
    text = " ".join(parts) if parts else sensor_type.citation_text
    return Citation(text, tuple(sorted(set(limit_ids) | set(effect_ids))))


def _limit_marks(limits: Sequence[LimitFacts]) -> tuple[LimitMark, ...]:
    return tuple(
        LimitMark(m.limit_id, m.value, m.unit, m.comparator, m.role, m.label)
        for m in limits
        if m.card_mark and m.value is not None
    )


def _context_label(sensor_type: SensorTypeFacts, chosen: ChannelView | None) -> str:
    """The channel's display_note, else the aggregation label and the location label joined by ", "."""
    if chosen is not None and chosen.channel.display_note:
        return chosen.channel.display_note
    spec = sensor_type.aggregation(sensor_type.display_aggregation_id)
    parts = [spec.label if spec is not None else ""]
    if chosen is not None and chosen.channel.location_label:
        parts.append(chosen.channel.location_label)
    return ", ".join(p for p in parts if p)


def _reading_state(evaluation: Evaluation) -> ReadingState:
    if evaluation.status == "no_reading":
        return "no_reading"
    return "stale" if evaluation.status == "stale" else "current"


def _guidance(inp: CardInput, level: Level | None) -> tuple[tuple[str, ...], str]:
    if not inp.hazard.source_ids:
        return (inp.unsupported_text,), inp.unsupported_text  # missing source: unsupported, never guidance
    todo_text = inp.hazard.to_do_at(level or "info")
    here = inp.hazard.words_for(inp.environment_id)
    return (split_sentences(todo_text) if todo_text else ()), (strip_source_ids(here) if here else "")


def compose_card(inp: CardInput) -> CardFacts:
    """One card per sensor type: the channel that ranks first, every other channel in the trace."""
    chosen = select_card_channel(inp.channels)
    evaluation = (
        chosen.evaluation
        if chosen is not None
        else no_reading_evaluation(inp.limits, inp.sensor_type.id, inp.environment_id, inp.evaluated_at)
    )
    linked = inp.linked_warnings
    level = shown_level(evaluation.level, linked)
    marks = [m for m in limits_for(inp.limits, inp.sensor_type.id, inp.environment_id) if m.card_mark]
    effects = shown_effects(inp.hazard, inp.environment_id, inp.filters)
    todo, here = _guidance(inp, level)
    return CardFacts(
        sensor_type_id=inp.sensor_type.id,
        hazard_id=inp.sensor_type.hazard_id,
        environment_id=inp.environment_id,
        name=inp.sensor_type.name,
        long_name=inp.sensor_type.long_name,
        body_region=inp.sensor_type.body_region,
        channel_id=None if chosen is None else chosen.channel.channel_id,
        reading_state=_reading_state(evaluation),
        display_value=_display_text(inp.sensor_type, evaluation),
        context_label=_context_label(inp.sensor_type, chosen),
        source_label=inp.idle_source_label if chosen is None else chosen.channel.source_label,
        origin_mode=inp.idle_origin if chosen is None else chosen.channel.origin_mode,
        evaluation=evaluation,
        shown_level=level,
        shown_state_key=None if level is not None else SHOWN_STATE_KEY[evaluation.status],
        linked_warning_ids=tuple(w.warning_id for w in linked),
        display_range=inp.sensor_type.display.range_for(inp.environment_id),
        limit_marks=_limit_marks(marks),
        stale_since=None if chosen is None or evaluation.status != "stale" else chosen.stale_since,
        trace=_trace(inp.sensor_type, chosen, inp.channels),
        effects=effect_lines(effects, inp.unsupported_text),
        todo=todo,
        here=here,
        citation=_citation(inp.sensor_type, marks, effects),
    )


@dataclass(frozen=True, slots=True)
class LivingHere:
    hazard_id: str
    name: str
    body_regions: tuple[str, ...]
    effects: tuple[EffectLine, ...]
    todo: tuple[str, ...]
    here: str


def living_here(
    hazards: Sequence[HazardFacts],
    environment_hazard_ids: Sequence[str],
    environment_id: str,
    filters: Sequence[EffectFilter],
    unsupported_text: str,
) -> tuple[LivingHere, ...]:
    """CONTRACTS 12 clauses (a) to (f) for the condition hazards of the environment, in its order."""
    by_id = {h.id: h for h in hazards}
    result: list[LivingHere] = []
    for hazard_id in environment_hazard_ids:
        hazard = by_id.get(hazard_id)
        if hazard is None or hazard.kind != "condition":
            continue
        effects = shown_effects(hazard, environment_id, filters)
        regions = tuple(dict.fromkeys(e.body_region for e in effects if e.body_region and e.source_ids))
        todo: list[str] = []
        for level in ("info", "watch"):
            text = hazard.to_do_at(level)
            todo.extend(sentences_skipping(text) if text else ())
        words = hazard.words_for(environment_id)
        result.append(
            LivingHere(
                hazard.id,
                strip_source_ids(hazard.name),
                regions,
                effect_lines(effects, unsupported_text),
                tuple(todo),
                strip_source_ids(words) if words else "",
            )
        )
    return tuple(result)


def ground_delay_text(earth_delay: str) -> tuple[str, tuple[str, ...]]:
    """Habitat ground delay words: the sourced earth_delay with its source IDs moved out (CONTRACTS 12)."""
    return strip_source_ids(earth_delay), source_ids_in(earth_delay)


@dataclass(frozen=True, slots=True)
class ReturnOptionView:
    trip_text: str
    source_ids: tuple[str, ...]
    to_be_set_by: str | None


def serve_return_option(
    trip_text: str | None, source_ids: Sequence[str], to_be_set_by: str | None
) -> ReturnOptionView | None:
    """The return option exactly as configured, or None where it is null (T-E23-046). Nothing is edited."""
    if trip_text is None:
        return None
    return ReturnOptionView(trip_text, tuple(source_ids), to_be_set_by)
