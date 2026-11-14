"""Test harness for the pure warnings domain: documents from configuration, input builders and a
minimal in-memory application of engine decisions.

The harness stands in for E24's application layer only far enough to run multi-step scenarios against
the pure engine. It holds no clinical text of its own: every label, threshold, step and word comes from
the configuration documents (the deployment `config/warning-rules.json` and the labeled test example
payloads under `docs/product/api/examples/config/`).
"""

from __future__ import annotations

import json
from functools import lru_cache
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from hypothesis import HealthCheck, settings

from crew_health.kernel.clock import NormalizedTime, TimeInput
from crew_health.kernel.testing import MemoryTemplateRenderer
from crew_health.kernel.units import Quantity, Unit
from crew_health.rules.domain import rendering
from crew_health.rules.domain.decisions import (
    Attach,
    CancelDeadline,
    Clear,
    Decision,
    DeleteDwell,
    PutDwell,
    Raise,
    Refuse,
    ResolveOccurrences,
    ScheduleDeadline,
)
from crew_health.rules.domain.definitions import RulesDocument, WarningDefinition, parse_rules_document
from crew_health.rules.domain.dwell import DwellMarker
from crew_health.rules.domain.engine import evaluate
from crew_health.rules.domain.fingerprint import (
    ReleaseDocuments,
    content_hash,
    limit_fingerprint,
    rule_fingerprint,
)
from crew_health.rules.domain.inputs import (
    ChannelRenderFacts,
    FreshnessInput,
    LimitRenderFacts,
    LimitResult,
    ObservedTime,
    RuleInput,
    SensorEvaluatedInput,
)
from crew_health.rules.domain.loader_checks import CONTENT_PLACEHOLDERS
from crew_health.rules.domain.occurrences import Occurrence
from crew_health.rules.domain.views import EngineView, LimitFacts
from crew_health.rules.domain.vocabulary import (
    FINAL,
    SPENT_CLEAR_REASONS,
    Basis,
    ClearReason,
    EnvironmentId,
    Level,
    OriginMode,
    WarningState,
)
from crew_health.rules.domain.warning import (
    Citation,
    NewWarning,
    TriggerRef,
    Warning,
    clear,
    engine_actor,
    raise_new,
)

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "docs" / "product" / "api" / "examples" / "config"
T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
NODE = "node_a"

# Every property test of this epic runs 1,000 deterministic examples (AGENTS.md 4).
PROP = settings(
    max_examples=1000,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
)


def example_value(name: str) -> dict[str, object]:
    """The `value` payload of a labeled test example document."""
    loaded = json.loads((EXAMPLES / f"{name}.example.json").read_text(encoding="utf-8"))
    value = loaded["value"]
    assert isinstance(value, dict)
    return value


def deployment_rules() -> dict[str, object]:
    loaded = json.loads((ROOT / "config" / "warning-rules.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def hazards_document() -> dict[str, object]:
    loaded = json.loads((ROOT / "docs" / "product" / "environment-hazards.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def what_to_do_by_hazard() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for hazard in hazards_document()["hazards"]:  # type: ignore[attr-defined]
        out[hazard["id"]] = dict(hazard["what_to_do"])
    return out


@lru_cache(maxsize=1)
def release_documents() -> ReleaseDocuments:
    return ReleaseDocuments(
        sensor_limits=example_value("sensor-limits"),
        sensor_catalog=example_value("sensor-catalog"),
        questionnaires=example_value("questionnaires"),
        assistant_policy=example_value("assistant-policy"),
    )


_FACTS: dict[int, dict[str, LimitFacts]] = {}


def limit_facts(docs: ReleaseDocuments) -> dict[str, LimitFacts]:
    cached = _FACTS.get(id(docs))
    if cached is not None:
        return dict(cached)
    built = _limit_facts(docs)
    if docs is release_documents():
        _FACTS[id(docs)] = built
    return dict(built)


def _limit_facts(docs: ReleaseDocuments) -> dict[str, LimitFacts]:
    catalog = {t["id"]: t for t in docs.sensor_catalog["sensor_types"]}  # type: ignore[attr-defined,index]
    facts: dict[str, LimitFacts] = {}
    for entry in docs.sensor_limits["limits"]:  # type: ignore[attr-defined,index]
        value = entry["value"]
        facts[entry["limit_id"]] = LimitFacts(
            limit_id=entry["limit_id"],
            sensor_type_id=entry["sensor_type_id"],
            level=Level(entry["level"]),
            role=entry["role"],
            status=entry["status"],
            value=None if value is None else Decimal(str(value)),
            dwell_seconds=entry["clear"]["dwell_seconds"],
            stale_after_seconds=catalog[entry["sensor_type_id"]]["stale_after_seconds"],
            limit_fingerprint=limit_fingerprint(entry["limit_id"], docs),
            basis=Basis(entry["basis"]),
            clear_basis=Basis(entry["clear"]["basis"]),
        )
    return facts


def observed(
    at: datetime,
    *,
    origin: OriginMode = OriginMode.LIVE,
    uncertainty_ms: int = 0,
    state: Literal["known", "unknown", "future", "replay"] | None = None,
    segment: str = "seg1",
) -> ObservedTime:
    kind = state or ("replay" if origin is OriginMode.REPLAY else "known")
    corrected = at if kind == "known" else None
    return ObservedTime(
        TimeInput(at, "clock_a", segment, 1, "source_raw", origin.value),
        NormalizedTime(at, corrected, None, uncertainty_ms if kind == "known" else None, kind),
    )


def render_facts(text: str = "3.5") -> LimitRenderFacts:
    return LimitRenderFacts("mean_1h", "one-hour average", 3600, text, "mm[Hg]", "mmHg", "3")


def channel_facts(origin: OriginMode = OriginMode.LIVE) -> ChannelRenderFacts:
    words = {"LIVE": "Live sensor", "SIM": "Simulated sensor", "REPLAY": "Replay of recorded data"}
    return ChannelRenderFacts("CO2", "cabin", words[origin.value], "09:00", None)


def result(
    limit_id: str,
    *,
    crossed: bool | None,
    clear_holds: bool | None,
    fingerprint: str | None = None,
    value: str = "3.5",
    render: LimitRenderFacts | None = None,
) -> LimitResult:
    return LimitResult(
        limit_id=limit_id,
        crossed=crossed,
        clear_holds=clear_holds,
        value=Quantity(Decimal(value), Unit("mm[Hg]")),
        coverage=Decimal("1"),
        limit_fingerprint=fingerprint,
        render=render or render_facts(value),
    )


@dataclass
class World:
    """One station's state under the pure engine, applying decisions the way the application would."""

    rules: RulesDocument
    docs: ReleaseDocuments
    mission_mode: OriginMode = OriginMode.LIVE
    environment: EnvironmentId = EnvironmentId.STATION_LEO
    now: datetime = T0
    release_id: str = "rel_a"
    frozen: bool = False
    clock_regressed: bool = False
    warnings: dict[str, Warning] = field(default_factory=dict)
    dwell: dict[str, DwellMarker] = field(default_factory=dict)
    occurrences: dict[str, list[Occurrence]] = field(default_factory=dict)
    spent: set[tuple[str, str, TriggerRef]] = field(default_factory=set)
    latest_clear: dict[tuple[str, str], datetime] = field(default_factory=dict)
    refusals: list[Refuse] = field(default_factory=list)
    deadlines: dict[str, ScheduleDeadline] = field(default_factory=dict)
    answer_events: tuple[object, ...] = ()
    _n: int = 0

    def __post_init__(self) -> None:
        self.limits = limit_facts(self.docs)
        self._fingerprints = {d.id: rule_fingerprint(d, self.docs) for d in self.definitions}
        self._content_hashes = {d.id: content_hash(d) for d in self.definitions}
        self.what_to_do = what_to_do_by_hazard()
        allowed = {
            k: frozenset(CONTENT_PLACEHOLDERS)
            for k in (
                rendering.FIELD_TITLE,
                rendering.FIELD_WHAT,
                rendering.FIELD_WHY,
                rendering.FIELD_FRAGMENT,
                rendering.FIELD_CITATION,
            )
        }
        self.renderer = MemoryTemplateRenderer(unknown="Unknown", allowed=allowed)

    # -- derived release facts -------------------------------------------------------------------
    @property
    def definitions(self) -> tuple[WarningDefinition, ...]:
        return self.rules.definitions

    def fingerprints(self) -> dict[str, str]:
        return dict(self._fingerprints)

    def content_hashes(self) -> dict[str, str]:
        return dict(self._content_hashes)

    def active(self) -> list[Warning]:
        return sorted((w for w in self.warnings.values() if w.active), key=lambda w: w.warning_id)

    def view(self) -> EngineView:
        active = self.active()
        return EngineView(
            release_id=self.release_id,
            environment_id=self.environment,
            mission_mode=self.mission_mode,
            now=self.now,
            node_id=NODE,
            definitions=self.definitions,
            fingerprints=self.fingerprints(),
            content_hashes=self.content_hashes(),
            limits=self.limits,
            active={w.key: w for w in active},
            group_active=tuple(active),
            dwell=dict(self.dwell),
            occurrences={k: tuple(v) for k, v in self.occurrences.items()},
            latest_clear=dict(self.latest_clear),
            spent=frozenset(self.spent),
            clears_frozen=self.frozen,
            answer_events=self.answer_events,  # type: ignore[arg-type]
            clock_regressed=self.clock_regressed,
            origin_words={"LIVE": "Live", "SIM": "Simulated", "REPLAY": "Replay"},
        )

    # -- applying decisions ----------------------------------------------------------------------
    def feed(self, inp: RuleInput) -> Decision:
        decision = evaluate(inp, self.view())
        self.apply(decision)
        return decision

    def apply(self, decision: Decision) -> None:
        for step in decision:
            if isinstance(step, Clear):
                self._clear(step)
        for step in decision:
            if isinstance(step, ResolveOccurrences):
                self._resolve(step)
        for step in decision:
            self._apply_other(step)

    def _apply_other(self, step: object) -> None:
        if isinstance(step, Raise):
            self._raise(step)
        elif isinstance(step, Attach):
            self.occurrences.setdefault(step.warning_id, []).append(step.occurrence)
        elif isinstance(step, PutDwell):
            self.dwell[step.marker.warning_id] = step.marker
        elif isinstance(step, DeleteDwell):
            self.dwell.pop(step.warning_id, None)
        elif isinstance(step, Refuse):
            self.refusals.append(step)
        elif isinstance(step, ScheduleDeadline):
            self.deadlines[step.key] = step
        elif isinstance(step, CancelDeadline):
            self.deadlines.pop(step.key, None)

    def _clear(self, step: Clear) -> None:
        w = self.warnings[step.warning_id]
        cleared = clear(w, engine_actor(NODE, origin_node_id=w.origin_node_id), step.reason, self.now)
        self.warnings[w.warning_id] = cleared
        if step.reason in SPENT_CLEAR_REASONS:
            self.spent.add((w.definition_id, w.scope_key, w.trigger))
        self.dwell.pop(w.warning_id, None)

    def _resolve(self, step: ResolveOccurrences) -> None:
        items = self.occurrences.get(step.warning_id, [])
        for i, o in enumerate(items):
            if o.resolved_by_event_id is None and o.occurred_at < step.before:
                items[i] = Occurrence(
                    o.warning_id, o.definition_id, o.scope_key, o.trigger_ref, o.occurred_at, step.clear_event_id
                )
        w = self.warnings[step.warning_id]
        self.latest_clear[w.key] = max(self.latest_clear.get(w.key, step.before), step.before)

    def _raise(self, r: Raise) -> None:
        self._n += 1
        rendered = rendering.render_content(r.pin, r.render, self.renderer, self.what_to_do.get(r.pin.definition.hazard_id or ""))
        label = f"{r.render.time_label.at:%H:%M}" + (", replay time" if r.render.replay else "")
        wid = f"wrn_{self._n}"
        w = raise_new(
            NewWarning(
                warning_id=wid,
                config_release_id=self.release_id,
                definition=r.pin.definition,
                pin=r.pin,
                limit_id=r.limit_id,
                channel_id=r.channel_id,
                person_id=r.person_id,
                environment_id=self.environment,
                location=r.location,
                origin_mode=r.origin_mode,
                title=rendered.title,
                what=rendered.what,
                why=rendered.why,
                steps=rendered.steps,
                headline_fragment=rendered.headline_fragment,
                evidence=r.evidence,
                evidence_time=r.evidence_time,
                evidence_time_label=label,
                citation=Citation(rendered.citation.text, rendered.citation.source_ids, rendered.citation.basis),
                insight_id=r.insight_id,
                raised_at=self.now,
                previous_warning_id=r.previous_warning_id,
                body_regions=rendered.body_regions,
                scope_key=r.scope_key,
                trigger=r.trigger,
                rule_fingerprint=r.rule_fingerprint,
                content_hash=r.content_hash,
                origin_node_id=NODE,
                source_revision=r.source_revision,
                source_partition=r.source_partition,
            )
        )
        self.warnings[wid] = w
        if r.trigger.kind.value == "mission_event":
            self.occurrences[wid] = [Occurrence(wid, w.definition_id, w.scope_key, r.trigger.ref, r.evidence_time, None)]

    # -- convenience -----------------------------------------------------------------------------
    def by_definition(self, definition_id: str, *, active_only: bool = True) -> list[Warning]:
        found = [w for w in self.warnings.values() if w.definition_id == definition_id]
        return [w for w in found if w.active] if active_only else found

    def advance(self, seconds: int) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now

    def sensor(
        self,
        *,
        at: datetime | None = None,
        sensor_type: str = "co2",
        channel: str = "ch_co2",
        results: Sequence[LimitResult],
        origin: OriginMode = OriginMode.LIVE,
        reading: str | None = None,
        clock_state: Literal["trusted", "uncertain", "unknown"] = "trusted",
        freshness: Literal["current", "stale"] = "current",
        obs: ObservedTime | None = None,
        receive_reference: datetime | None = None,
        release: str | None = None,
    ) -> SensorEvaluatedInput:
        e = at or self.now
        stale = {"co2": 300, "o2": 300}.get(sensor_type, 300)
        return SensorEvaluatedInput(
            config_release_id=release or self.release_id,
            sensor_type_id=sensor_type,
            channel_id=channel,
            location="cabin",
            origin_mode=origin,
            observed=obs or observed(e, origin=origin),
            receive_reference=receive_reference
            if receive_reference is not None
            else (self.now if origin is OriginMode.REPLAY else None),
            reading_id=reading or f"rdg_{channel}_{e.isoformat()}",
            clock_state=clock_state,
            freshness=freshness,
            stale_after_seconds=stale,
            results=tuple(results),
            channel=channel_facts(origin),
        )

    def freshness_input(
        self,
        state: Literal["current", "stale", "never_reported"],
        *,
        channel: str = "ch_co2",
        sensor_type: str = "co2",
        since: datetime | None = None,
        origin: OriginMode = OriginMode.LIVE,
        obs: ObservedTime | None = None,
        receive_reference: datetime | None = None,
    ) -> FreshnessInput:
        return FreshnessInput(
            config_release_id=self.release_id,
            channel_id=channel,
            sensor_type_id=sensor_type,
            state=state,
            stale_since=since if state == "stale" else None,
            last_reading_id="rdg_last" if state != "never_reported" else None,
            origin_mode=origin,
            observed=obs,
            receive_reference=receive_reference,
            channel=channel_facts(origin),
        )


def final_states() -> Iterable[WarningState]:
    return sorted(FINAL)


def clear_reasons() -> Iterable[ClearReason]:
    return list(ClearReason)
