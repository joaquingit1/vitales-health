"""Applying one rule input in the producer's unit of work (E24 4.1 UC-R1, 4.3 order, 5.5 faults).

`run_input` is the isolation boundary of S8.6: the evaluation runs in the savepoint `rules_eval`; an evaluator fault
rolls back only that savepoint and leaves a durable `evaluation_pending` row; a storage fault propagates and the
producer's command commits nothing. Nothing here is a second transaction, clock, id or retry mechanism.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.config import ConfigBinding, TemplateRenderer
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import AbsoluteDeadline, HealthState, ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import ReadSnapshot, ReplicationChange, UnitOfWork
from crew_health.mission.application.ports import MissionStatePort
from crew_health.rules.application import facts as release_facts
from crew_health.rules.application import input_codec
from crew_health.rules.application.codec import dumps as warning_json
from crew_health.rules.application.facts import ReleaseFacts
from crew_health.rules.application.ports import WarningCleared, WarningRaised
from crew_health.rules.application.stores import (
    ActionResultStore,
    DeadlineStore,
    DwellStore,
    FactStore,
    MissionEventStore,
    OccurrenceStore,
    PendingStore,
    ReplicaStore,
    WarningStore,
)
from crew_health.rules.application.wire import AUDIENCE_RULE, PROJECTION, emit, fact_of
from crew_health.rules.domain import engine, rendering
from crew_health.rules.domain.decisions import (
    Attach,
    CancelDeadline,
    Clear,
    Decision,
    DeleteDwell,
    PutDwell,
    Raise,
    Refuse,
    RejectInsight,
    ResolveOccurrences,
    ScheduleDeadline,
)
from crew_health.rules.domain.definitions import MissionEventCondition, WithinDays
from crew_health.rules.domain.errors import InvariantViolation, RulesDomainError
from crew_health.rules.domain.inputs import (
    AnswerInput,
    FreshnessInput,
    GroundLinkInput,
    InsightInput,
    MissionEventInput,
    RuleInput,
    SensorEvaluatedInput,
    StateFactInput,
    SupplyCoverInput,
)
from crew_health.rules.domain.occurrences import Occurrence
from crew_health.rules.domain.views import EngineView
from crew_health.rules.domain.vocabulary import (
    ConditionKind,
    EnvironmentId,
    OriginMode,
    RaisedBy,
    Scope,
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

EXPIRE_PREFIX = "expire:"
DEADLINE_HANDLER = "rules.deadline"
RECOVER_HANDLER = "rules.recover_pending"
HEALTH_COMPONENT = "rules"
FAULT_CODE = "EVALUATION_UNAVAILABLE"
SAVEPOINT = "rules_eval"
FACT_REGRESSED = "clock_regressed"
FACT_GROUND = "ground_link"


@dataclass(slots=True)
class Runtime:
    settings: HubSettings
    ids: IdFactory
    mission: MissionStatePort | None
    binding: ConfigBinding | None
    health: HealthState | None
    warnings: WarningStore = field(default_factory=WarningStore)
    occurrences: OccurrenceStore = field(default_factory=OccurrenceStore)
    dwell: DwellStore = field(default_factory=DwellStore)
    deadlines: DeadlineStore = field(default_factory=DeadlineStore)
    pending: PendingStore = field(default_factory=PendingStore)
    mission_events: MissionEventStore = field(default_factory=MissionEventStore)
    facts_store: FactStore = field(default_factory=FactStore)
    results: ActionResultStore = field(default_factory=ActionResultStore)
    replicas: ReplicaStore = field(default_factory=ReplicaStore)
    cache: dict[str, ReleaseFacts] = field(default_factory=dict)

    def cap(self, key: str) -> int:
        """A scan bound: the hub setting, never above what one storage read may return (storage.max_rows)."""
        return min(self.settings.integer(key), self.settings.integer("storage.max_rows") - 1)

    def facts(self, uow: UnitOfWork) -> ReleaseFacts:
        """The active release's facts; the cache holds one release (3.8)."""
        found = self.cache.get(uow.release.release_id)
        if found is None:
            found = release_facts.build(uow.release)
            self.cache.clear()
            self.cache[found.release_id] = found
        return found

    def facts_for_snapshot(self, view: ReadSnapshot) -> ReleaseFacts:
        found = self.cache.get(view.release.release_id)
        if found is None:
            found = release_facts.build(view.release)
            self.cache.clear()
            self.cache[found.release_id] = found
        return found

    def node_id(self, uow: UnitOfWork | ReadSnapshot) -> str:
        configured = self.settings.text("rules.node_id")
        if configured:
            return configured
        current = None if self.mission is None else self.mission.current(uow)
        if current is None or not current.site_node_id:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "rules_node_unknown", retryable=True)
        return current.site_node_id

    def renderer(self, uow: UnitOfWork) -> TemplateRenderer:
        if self.binding is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "template_renderer_absent", retryable=True)
        return self.binding.bind(uow)


def note_audit(
    rt: Runtime, uow: UnitOfWork, event_type: str, refs: dict[str, str], meta: dict[str, str | int | bool]
) -> None:
    uow.audit(AuditEntry(rt.ids.new("aud"), event_type, 1, uow.actor, refs, uow.correlation_id, None, meta))


def replicate_local(uow: UnitOfWork, warning_id: str, operation: Literal["create", "version"]) -> None:
    """Local-only accounting (S5.7, export_projection=None): the reference, never a field."""
    from crew_health.kernel.uow import Audience

    uow.replicate(
        ReplicationChange(
            None, "warning", warning_id, operation,
            ('{"id":"' + warning_id + '"}').encode(), (), uow.release.release_id,
            Audience(AUDIENCE_RULE, "warning:" + warning_id, PROJECTION, "1"),
        )
    )  # fmt: skip


# -- view -----------------------------------------------------------------------------------------


def candidate_refs(inp: RuleInput) -> list[tuple[ConditionKind, str]]:
    """The trigger references this input can raise under (3.9), to load only the spent ones."""
    match inp:
        case SensorEvaluatedInput():
            return [(ConditionKind.SENSOR_LIMIT, inp.reading_id)]
        case FreshnessInput():
            since = inp.stale_since
            return (
                []
                if since is None
                else [(ConditionKind.SENSOR_QUIET, f"{inp.channel_id}@{since.isoformat()}")]
            )
        case MissionEventInput():
            return [(ConditionKind.MISSION_EVENT, inp.event_id)]
        case StateFactInput():
            return [(ConditionKind.STATE_FACT, inp.cause_event_id)]
        case AnswerInput():
            return [(ConditionKind.ANSWER, inp.answer_id)]
        case GroundLinkInput():
            at = inp.next_contact_at
            return [] if at is None else [(ConditionKind.GROUND_SILENCE, at.isoformat())]
        case SupplyCoverInput():
            return [
                (
                    ConditionKind.SUPPLY_COVER_LOW,
                    inp.supply_use_id or f"{row.release_id}:{inp.environment_id}",
                )
                for row in inp.rows
            ]
        case InsightInput():
            return [(ConditionKind.ASSISTANT_INSIGHT, inp.draft.insight_id)]
        case _:
            return []


def build_view(rt: Runtime, uow: UnitOfWork, facts: ReleaseFacts, inp: RuleInput) -> EngineView | None:
    """None when no mission exists: nothing is evaluated under a guessed environment (AGENTS 1 rule 2)."""
    mission = None if rt.mission is None else rt.mission.current(uow)
    if mission is None:
        return None
    c = uow.connection
    s = rt.settings
    active = rt.warnings.active(c, limit=rt.cap("rules.active_scan_max"))
    ids = [w.warning_id for w in active]
    spent: set[tuple[str, str, TriggerRef]] = set()
    for kind, ref in candidate_refs(inp):
        spent |= rt.warnings.spent(c, kind, [ref])
    channel = getattr(inp, "channel_id", None)
    sensor = getattr(inp, "sensor_type_id", None)
    group = (
        tuple(w for w in active if w.channel_id == channel and w.sensor_type_id == sensor) if channel else ()
    )
    types = sorted({
        d.condition.within_days_after_event.event_type
        for d in facts.rules.definitions
        if hasattr(d.condition, "within_days_after_event") and isinstance(d.condition.within_days_after_event, WithinDays)
    })  # fmt: skip
    regressed = rt.facts_store.get(c, FACT_REGRESSED)
    return EngineView(
        release_id=facts.release_id,
        environment_id=EnvironmentId(mission.active_environment_id),
        mission_mode=OriginMode(mission.mission_mode),
        now=uow.now,
        node_id=rt.node_id(uow),
        definitions=facts.rules.definitions,
        fingerprints=facts.fingerprints,
        content_hashes=facts.content_hashes,
        limits=facts.limits,
        active={w.key: w for w in active},
        group_active=group,
        dwell=rt.dwell.all(c, limit=rt.cap("rules.dwell_scan_max")),
        occurrences=rt.occurrences.for_warnings(c, ids, limit=rt.cap("rules.occurrence_scan_max")),
        latest_clear=rt.occurrences.latest_clears(c, limit=rt.cap("rules.clear_scan_max")),
        spent=frozenset(spent),
        clears_frozen=rt.pending.blocking(c),
        answer_events=rt.mission_events.latest(
            c, types, per_type=min(s.integer("rules.answer_event_window"), s.integer("storage.max_rows"))
        ),
        clock_regressed=bool(regressed and regressed.get("on")),
        origin_words=facts.origin_words,
    )


_FIELDS = {
    rendering.FIELD_TITLE: "title",
    rendering.FIELD_WHAT: "what",
    rendering.FIELD_WHY: "why",
    rendering.FIELD_FRAGMENT: "headline/fragment",
    rendering.FIELD_CITATION: "citation_template",
}


@dataclass(frozen=True, slots=True)
class PointerRenderer:
    """The domain names a template field `warning.title`; E01's renderer takes the document pointer of the field
    (`warning-rules/definitions/<i>/content/title`) and checks its placeholders against CONTRACTS 11 for that field."""

    inner: TemplateRenderer
    index: int

    def render(self, field_key: str, template: str, values: Mapping[str, str]) -> str:
        tail = _FIELDS.get(field_key)
        if tail is None:
            raise InvariantViolation("unknown template field: " + field_key)
        return self.inner.render(f"warning-rules/definitions/{self.index}/content/{tail}", template, values)


# -- applying a decision --------------------------------------------------------------------------


def time_label(facts: ReleaseFacts, r: Raise) -> str:
    hm = f"{r.render.time_label.at:%H:%M}"
    if r.render.replay:
        return facts.designed_states.get("replay_time", "{time}, replay time").replace("{time}", hm)
    return hm


def apply_raise(
    rt: Runtime,
    uow: UnitOfWork,
    facts: ReleaseFacts,
    view: EngineView,
    r: Raise,
    sensor_type_id: str | None = None,
) -> None:
    index = next(i for i, d in enumerate(facts.rules.definitions) if d.id == r.pin.definition.id)
    rendered = rendering.render_content(
        r.pin,
        r.render,
        PointerRenderer(rt.renderer(uow), index),
        facts.what_to_do.get(r.pin.definition.hazard_id or ""),
    )
    warning = raise_new(
        NewWarning(
            warning_id=rt.ids.new("wrn"), config_release_id=view.release_id, definition=r.pin.definition,
            pin=r.pin, limit_id=r.limit_id, channel_id=r.channel_id, person_id=r.person_id,
            environment_id=view.environment_id, location=r.location, origin_mode=r.origin_mode,
            title=rendered.title, what=rendered.what, why=rendered.why, steps=rendered.steps,
            headline_fragment=rendered.headline_fragment, evidence=r.evidence, evidence_time=r.evidence_time,
            evidence_time_label=time_label(facts, r),
            citation=Citation(rendered.citation.text, rendered.citation.source_ids, rendered.citation.basis),
            insight_id=r.insight_id, raised_at=uow.now, previous_warning_id=r.previous_warning_id,
            body_regions=rendered.body_regions, scope_key=r.scope_key, trigger=r.trigger,
            rule_fingerprint=r.rule_fingerprint, content_hash=r.content_hash, origin_node_id=view.node_id,
            source_revision=r.source_revision, source_partition=r.source_partition,
        )
    )  # fmt: skip
    if warning.sensor_type_id is None and sensor_type_id is not None:
        warning = replace(warning, sensor_type_id=sensor_type_id)  # a quiet warning names its channel's type
    rt.warnings.insert(uow.connection, warning)
    replicate_local(uow, warning.warning_id, "create")
    if r.trigger.kind is ConditionKind.MISSION_EVENT:
        rt.occurrences.record(
            uow.connection, definition_id=warning.definition_id, scope_key=warning.scope_key,
            trigger_ref=r.trigger.ref, warning_id=warning.warning_id, kind="trigger",
            occurred_at=r.evidence_time, recorded_at=uow.now,
        )  # fmt: skip
    emit(uow, EventType.WARNING_RAISED, warning)
    note_audit(rt, uow, "warning.raised", {"warning": warning.warning_id}, {})
    uow.publish(WarningRaised(fact_of(warning)))
    expires = r.pin.definition.clear.expires_after_seconds
    if warning.raised_by is RaisedBy.ASSISTANT and expires is not None:
        key = EXPIRE_PREFIX + warning.warning_id
        schedule(rt, uow, key, "elapsed", uow.now + timedelta(seconds=expires), expires)


def apply_clear(rt: Runtime, uow: UnitOfWork, step: Clear) -> None:
    w = rt.warnings.get(uow.connection, step.warning_id)
    if w is None or not w.active:
        return  # a duplicate or replayed clear changes nothing
    done = clear(w, engine_actor(rt.node_id(uow), origin_node_id=w.origin_node_id), step.reason, uow.now)
    if not rt.warnings.update(uow.connection, done, expected_revision=w.revision):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "warning_revision_moved", retryable=True)
    replicate_local(uow, w.warning_id, "version")
    rt.dwell.delete(uow.connection, w.warning_id)
    cancel(rt, uow, EXPIRE_PREFIX + w.warning_id)
    emit(uow, EventType.WARNING_CLEARED, done)
    note_audit(rt, uow, "warning.cleared", {"warning": w.warning_id}, {})
    uow.publish(WarningCleared(fact_of(done)))


def schedule(
    rt: Runtime, uow: UnitOfWork, key: str, kind: str, due: datetime | None, elapsed: int | None
) -> None:
    if due is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "deadline_without_time")
    previous = rt.deadlines.get(uow.connection, key)
    handler = RECOVER_HANDLER if key == RECOVER_HANDLER else DEADLINE_HANDLER
    generation = uow.schedule(
        ScheduledJob(key, handler, AbsoluteDeadline(due), {"key": key}, None),
        expected_generation=None if previous is None else previous[4],
    )
    rt.deadlines.put(uow.connection, key, kind, due, elapsed, uow.now, generation)


def cancel(rt: Runtime, uow: UnitOfWork, key: str) -> None:
    previous = rt.deadlines.get(uow.connection, key)
    if previous is not None and previous[1] is not None:
        uow.cancel_schedule(key, expected_generation=previous[4])
        rt.deadlines.delete(uow.connection, key)


def apply_decision(
    rt: Runtime,
    uow: UnitOfWork,
    facts: ReleaseFacts,
    view: EngineView,
    decision: Decision,
    sensor_type_id: str | None = None,
) -> None:
    """Order of 4.3: clears (supersessions first), occurrence resolution, raises, attaches, dwell, deadlines."""
    c = uow.connection
    for step in decision:
        if isinstance(step, Clear):
            apply_clear(rt, uow, step)
    for step in decision:
        if isinstance(step, ResolveOccurrences):
            rt.occurrences.resolve_before(c, step.warning_id, step.before, step.clear_event_id)
    for step in decision:
        if isinstance(step, Raise):
            apply_raise(rt, uow, facts, view, step, sensor_type_id)
    for step in decision:
        _apply_rest(rt, uow, step)


def _apply_rest(rt: Runtime, uow: UnitOfWork, step: object) -> None:
    c = uow.connection
    if isinstance(step, Attach):
        o: Occurrence = step.occurrence
        if rt.occurrences.record(
            c, definition_id=o.definition_id, scope_key=o.scope_key, trigger_ref=o.trigger_ref,
            warning_id=step.warning_id, kind="trigger", occurred_at=o.occurred_at, recorded_at=uow.now,
        ):  # fmt: skip
            note_audit(rt, uow, "warning.occurrence_attached", {"warning": step.warning_id}, {})
    elif isinstance(step, PutDwell):
        rt.dwell.put(c, step.marker)
    elif isinstance(step, DeleteDwell):
        rt.dwell.delete(c, step.warning_id)
    elif isinstance(step, ScheduleDeadline):
        schedule(rt, uow, step.key, step.kind, step.due_at, step.elapsed_seconds)
    elif isinstance(step, CancelDeadline):
        cancel(rt, uow, step.key)
    elif isinstance(step, Refuse):
        note_audit(rt, uow, "warning.refused", {}, {"code": step.code})
    elif isinstance(step, RejectInsight):
        for code in step.codes:
            note_audit(rt, uow, "insight.rejected", {}, {"code": code})


def record_clear_events(rt: Runtime, uow: UnitOfWork, facts: ReleaseFacts, inp: MissionEventInput) -> None:
    """The event log of 3.9: a clear event is remembered for each key whether or not a warning is active."""
    for d in facts.rules.definitions:
        cond = d.condition
        if isinstance(cond, MissionEventCondition) and inp.event_type in cond.clear_event_types:
            for key in scope_keys(d.scope, inp):
                rt.occurrences.record(
                    uow.connection, definition_id=d.id, scope_key=key, trigger_ref=inp.event_id, warning_id=None,
                    kind="clear", occurred_at=inp.occurred_at, recorded_at=uow.now,
                )  # fmt: skip


def scope_keys(scope: Scope, inp: MissionEventInput) -> list[str]:
    if scope is Scope.PERSON:
        return sorted(set(inp.person_ids))
    if scope is Scope.CREW:
        return [""]
    return [inp.location if inp.location else f"environment:{inp.environment_id}"]


def apply_input(rt: Runtime, uow: UnitOfWork, inp: RuleInput) -> Decision:
    facts = rt.facts(uow)
    if isinstance(inp, MissionEventInput):
        rt.mission_events.add(uow.connection, inp)
    view = build_view(rt, uow, facts, inp)
    if view is None:
        return ()
    decision = engine.evaluate(inp, view)
    apply_decision(rt, uow, facts, view, decision, getattr(inp, "sensor_type_id", None))
    if isinstance(inp, MissionEventInput):
        record_clear_events(rt, uow, facts, inp)
    return decision


# -- the isolation boundary (5.5) -----------------------------------------------------------------


def fault_code(fault: Exception) -> str:
    return fault.code if isinstance(fault, RulesDomainError) else type(fault).__name__[:60]


def coalesce_key(inp: RuleInput) -> str | None:
    """Sensor rows coalesce per channel: the earliest input with any crossed result and the latest (5.5)."""
    if isinstance(inp, SensorEvaluatedInput):
        crossed = any(r.crossed for r in inp.results)
        return f"{inp.channel_id}:{'crossed' if crossed else 'latest'}"
    return None


def record_pending(rt: Runtime, uow: UnitOfWork, inp: RuleInput, fault: Exception) -> None:
    kind, text = input_codec.dumps(inp)
    key = coalesce_key(inp)
    if key is not None and key.endswith(":crossed") and rt.pending.any(uow.connection):
        existing = uow.connection.rows("SELECT 1 FROM rules_pending WHERE coalesce_key = ?", (key,), limit=1)
        if existing:
            key = key + ":" + rt.ids.new("pnd")  # keep the earliest crossed input; this one is its own row
    rt.pending.record(
        uow.connection, pending_id=rt.ids.new("pnd"), kind=kind, ref=kind, coalesce_key=key, input_json=text,
        code=fault_code(fault), now=uow.now, max_rows=rt.settings.integer("rules.pending_max_rows"),
    )  # fmt: skip
    if isinstance(inp, SensorEvaluatedInput):
        rt.dwell.delete_for_channel(uow.connection, inp.channel_id)
    schedule(rt, uow, RECOVER_HANDLER, "absolute", uow.now, None)
    if rt.health is not None:
        rt.health.mark_degraded(HEALTH_COMPONENT, FAULT_CODE, uow.now)


STORAGE_CAUSES = frozenset({"constraint", "disk_full", "locked", "io_error", "corrupt"})


def is_storage_fault(fault: Exception) -> bool:
    """Storage and integrity errors are never converted into a pending evaluation (AGENTS 6): the command fails."""
    return (
        isinstance(fault, ProblemError)
        and fault.code is ErrorCode.INTERNAL_ERROR
        and str(fault) in STORAGE_CAUSES
    )


def run_input(rt: Runtime, uow: UnitOfWork, inp: RuleInput) -> None:
    """Capture first, evaluate second. A storage fault is never converted; anything else becomes `pending`."""
    try:
        with uow.savepoint(SAVEPOINT):
            apply_input(rt, uow, inp)
    except StorageUnavailable:
        raise
    except Exception as fault:
        if is_storage_fault(fault):
            raise  # noqa: BLE001 - the boundary of S8.6: converted, never propagated
        record_pending(rt, uow, inp, fault)


def recover_one(rt: Runtime, uow: UnitOfWork, pending_id: str, kind: str, text: str) -> None:
    """Re-run one recorded input as recorded; clears obey the dwell rules, so a backlog never clears (3.6)."""
    inp = input_codec.loads(kind, text)
    apply_input(rt, uow, inp)
    rt.pending.done(uow.connection, pending_id)


def ids_of(items: Sequence[Warning]) -> list[str]:
    return [w.warning_id for w in items]


def dump(w: Warning) -> str:
    return warning_json(w)


def record_recovery_due(rt: Runtime, uow: UnitOfWork) -> None:
    """Startup (5.3): recovery runs at once while rows exist, and health says so."""
    schedule(rt, uow, RECOVER_HANDLER, "absolute", uow.now, None)
    if rt.health is not None:
        rt.health.mark_degraded(HEALTH_COMPONENT, FAULT_CODE, uow.now)
