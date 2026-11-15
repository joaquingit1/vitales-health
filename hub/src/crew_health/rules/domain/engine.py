"""The rule engine: `evaluate(input, view) -> Decision` (E24 3.5).

Pure and total over valid inputs. It reads only the input and the view and returns a decision; it reads
no clock, store or model. Four safety properties are decided here and in the modules it calls:

* An active warning clears only by the rule it was raised under (D1), on eligible evidence observed
  after the raise (D3, D14), and never while an evaluation is pending.
* The assistant raises only through `evaluate` on an `InsightInput`, only at info or watch, and nothing
  in this module lets an insight clear or lower a rule or sensor warning.
* A null threshold, a null cover and a limit that is not active raise nothing.
* An activation or an environment change never clears, lowers or re-pins an active warning.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from crew_health.rules.domain import clear_evidence, dwell, escalation, insight_bounds, occurrences, origin
from crew_health.rules.domain.decisions import (
    Attach,
    CancelDeadline,
    Clear,
    Decision,
    DeleteDwell,
    PutDwell,
    Raise,
    Refuse,
    RefuseCode,
    RejectInsight,
    RenderValues,
    ResolveOccurrences,
    ScheduleDeadline,
    Step,
    TimeLabelSpec,
)
from crew_health.rules.domain.definitions import (
    AnswerCondition,
    GroundSilenceCondition,
    InsightCondition,
    MissionEventCondition,
    SensorLimitCondition,
    SensorQuietCondition,
    StateFactCondition,
    SupplyCoverLowCondition,
    WarningDefinition,
    WithinDays,
)
from crew_health.rules.domain.inputs import (
    ActivationInput,
    AnswerInput,
    DeadlineInput,
    FreshnessInput,
    GroundLinkInput,
    InsightExpiredInput,
    InsightInput,
    LimitResult,
    MissionEventInput,
    RuleInput,
    SensorEvaluatedInput,
    StateFactInput,
    SupplyCoverInput,
    SupplyCoverRow,
)
from crew_health.rules.domain.time_policy import (
    answer_window_satisfied,
    expiry_due,
    ground_silence_due_at,
    ground_silence_ended,
    ground_silence_holds,
)
from crew_health.rules.domain.views import EngineView, LimitFacts
from crew_health.rules.domain.vocabulary import (
    ClearBy,
    ClearReason,
    ConditionKind,
    EvidenceKind,
    OriginMode,
    RaisedBy,
    Scope,
)
from crew_health.rules.domain.warning import (
    DefinitionPin,
    EvidenceRef,
    LimitPin,
    TriggerRef,
    Warning,
)


def evaluate(inp: RuleInput, view: EngineView) -> Decision:
    """The decision for one input under one view."""
    match inp:
        case SensorEvaluatedInput():
            return _sensor(inp, view)
        case FreshnessInput():
            return _freshness(inp, view)
        case MissionEventInput():
            return _mission_event(inp, view)
        case StateFactInput():
            return _state_fact(inp, view)
        case AnswerInput():
            return _answer(inp, view)
        case GroundLinkInput():
            return _ground(inp, view)
        case SupplyCoverInput():
            return _supply(inp, view)
        case InsightInput():
            return _insight(inp, view)
        case InsightExpiredInput():
            return _insight_expired(inp, view)
        case ActivationInput() | DeadlineInput():
            # An activation re-evaluates through the producers' current state; it never clears (D1).
            return ()


def reconcile_clear_events(view: EngineView) -> Decision:
    """Clears active clear-event warnings whose occurrences were all resolved while clears were frozen."""
    if view.clears_frozen:
        return ()
    steps: list[Step] = []
    for w in sorted(view.active.values(), key=lambda x: x.warning_id):
        if not w.active or w.pin.definition.clear.by_engine is not ClearBy.CLEAR_EVENT:
            continue
        if occurrences.fully_resolved(view.occurrences.get(w.warning_id, ())):
            steps.append(Clear(w.warning_id, ClearReason.CLEAR_EVENT))
    return tuple(steps)


# ---------------------------------------------------------------------------------------------
# shared helpers


def _in_environment(d: WarningDefinition, view: EngineView) -> bool:
    return view.environment_id in d.environments


def _defs[C](view: EngineView, cls: type[C]) -> list[tuple[WarningDefinition, C]]:
    """The definitions of one condition kind that apply in the active environment, sorted by id."""
    found: list[tuple[WarningDefinition, C]] = []
    for d in sorted(view.definitions, key=lambda x: x.id):
        cond = d.condition
        if isinstance(cond, cls) and _in_environment(d, view):
            found.append((d, cond))
    return found


def _stamp(d: WarningDefinition, view: EngineView) -> tuple[str, str] | None:
    fingerprint = view.fingerprints.get(d.id)
    content = view.content_hashes.get(d.id)
    if fingerprint is None or content is None:
        return None
    return (fingerprint, content)


def _fill(view: EngineView, base: Mapping[str, str], origin_mode: OriginMode) -> dict[str, str]:
    values: dict[str, str] = dict(view.context_placeholders or {})
    values.update(base)
    if "source_label" not in values and view.origin_words is not None:
        words = view.origin_words.get(origin_mode.value)
        if words is not None:
            values["source_label"] = words
    return values


def _render(
    view: EngineView,
    base: Mapping[str, str],
    *,
    origin_mode: OriginMode,
    evidence_time: datetime,
    summaries: tuple[str | None, ...],
    assistant_steps: tuple[str, ...] = (),
    body_region: str | None = None,
) -> RenderValues:
    replay = origin_mode is OriginMode.REPLAY
    label = TimeLabelSpec("replay" if replay else "mission", evidence_time)
    return RenderValues(
        _fill(view, base, origin_mode), summaries, label, assistant_steps, body_region, replay
    )


class _Fields:
    """Per-raise facts that differ by condition kind; everything else is derived from the definition."""

    def __init__(
        self,
        *,
        scope_key: str,
        trigger: TriggerRef,
        origin_mode: OriginMode,
        evidence: tuple[EvidenceRef, ...],
        evidence_time: datetime,
        render: RenderValues,
    ) -> None:
        self.scope_key = scope_key
        self.trigger = trigger
        self.origin_mode = origin_mode
        self.evidence = evidence
        self.evidence_time = evidence_time
        self.render = render
        self.person_id: str | None = None
        self.channel_id: str | None = None
        self.location: str | None = None
        self.limit_pin: LimitPin | None = None
        self.supersedes: tuple[str, ...] = ()
        self.previous: str | None = None
        self.insight_id: str | None = None
        self.source_revision: int | None = None
        self.source_partition: str | None = None


def _raise(d: WarningDefinition, view: EngineView, f: _Fields) -> Raise | None:
    stamp = _stamp(d, view)
    if stamp is None:
        return None  # a definition without its fingerprints cannot be raised or pinned
    limit_id = f.limit_pin.limit_id if f.limit_pin is not None else None
    return Raise(
        definition_id=d.id,
        scope_key=f.scope_key,
        trigger=f.trigger,
        pin=DefinitionPin(d, f.limit_pin),
        rule_fingerprint=stamp[0],
        content_hash=stamp[1],
        limit_id=limit_id,
        channel_id=f.channel_id,
        person_id=f.person_id,
        location=f.location,
        evidence=f.evidence,
        evidence_time=f.evidence_time,
        origin_mode=f.origin_mode,
        render=f.render,
        supersedes=f.supersedes,
        previous_warning_id=f.previous,
        insight_id=f.insight_id,
        source_revision=f.source_revision,
        source_partition=f.source_partition,
    )


def _spent(view: EngineView, definition_id: str, scope_key: str, trigger: TriggerRef) -> bool:
    return (definition_id, scope_key, trigger) in view.spent


def _refuse(d_id: str, key: str, code: RefuseCode) -> Refuse:
    return Refuse(d_id, key, code)


def _person_ok(view: EngineView, origins: Sequence[OriginMode]) -> bool:
    return origin.person_origin_allowed(evidence_origins=origins, mission_mode=view.mission_mode)


# ---------------------------------------------------------------------------------------------
# sensor limits


def _limit_pin(facts: LimitFacts) -> LimitPin:
    return LimitPin(
        facts.limit_id,
        facts.limit_fingerprint,
        facts.dwell_seconds,
        facts.stale_after_seconds,
        facts.basis,
        facts.clear_basis,
    )


def _crossed(
    inp: SensorEvaluatedInput, view: EngineView
) -> list[tuple[WarningDefinition, LimitFacts, LimitResult]]:
    """Definitions whose own limit result is crossed, with an active limit that has a threshold."""
    found: list[tuple[WarningDefinition, LimitFacts, LimitResult]] = []
    for d, cond in _defs(view, SensorLimitCondition):
        facts = view.limits.get(cond.limit_id)
        result = inp.result_for(cond.limit_id)
        if facts is None or result is None or result.crossed is not True:
            continue
        if facts.status != "active" or facts.role != "warning" or facts.value is None:
            continue  # a null threshold or a display-only limit raises nothing
        if facts.sensor_type_id != inp.sensor_type_id:
            continue
        found.append((d, facts, result))
    return found


def _sensor_fields(
    inp: SensorEvaluatedInput, facts: LimitFacts, result: LimitResult, view: EngineView
) -> _Fields:
    base: dict[str, str] = {
        "sensor_name": inp.channel.sensor_name,
        "aggregation_label": result.render.aggregation_label,
        "value": result.render.value_text,
        "unit": result.render.unit,
        "unit_display": result.render.unit_display,
        "limit_value": result.render.limit_value_text,
    }
    if inp.channel.location_label is not None:
        base["location"] = inp.channel.location_label
    base["source_label"] = inp.channel.source_label
    if inp.channel.last_received_time is not None:
        base["last_received_time"] = inp.channel.last_received_time
    at = inp.observed.display_at()
    fields = _Fields(
        scope_key=inp.channel_id,
        trigger=TriggerRef(ConditionKind.SENSOR_LIMIT, inp.reading_id),
        origin_mode=inp.origin_mode,
        evidence=(EvidenceRef(EvidenceKind.READING, inp.reading_id, None, at),),
        evidence_time=at,
        render=_render(view, base, origin_mode=inp.origin_mode, evidence_time=at, summaries=(None,)),
    )
    fields.channel_id = inp.channel_id
    fields.location = inp.location
    fields.limit_pin = _limit_pin(facts)
    return fields


def _sensor_group(inp: SensorEvaluatedInput, view: EngineView) -> list[Warning]:
    group = [
        w
        for w in view.group_active
        if w.active
        and w.channel_id == inp.channel_id
        and w.sensor_type_id == inp.sensor_type_id
        and w.pin.definition.condition.kind is ConditionKind.SENSOR_LIMIT
    ]
    return sorted(group, key=lambda w: w.warning_id)


def _sensor_dwell(
    inp: SensorEvaluatedInput, view: EngineView, group: Sequence[Warning]
) -> tuple[list[Warning], list[Step]]:
    """Advances each active warning's dwell on this evaluation. Returns the warnings that may clear."""
    ready: list[Warning] = []
    steps: list[Step] = []
    for w in group:
        marker = view.dwell.get(w.warning_id)
        pin = w.pin.limit
        if view.clears_frozen or pin is None:
            if marker is not None:
                steps.append(DeleteDwell(w.warning_id))
            continue
        verdict = clear_evidence.eligible(inp, w, view.now)
        outcome = dwell.step(
            marker,
            warning_id=w.warning_id,
            channel_id=inp.channel_id,
            e=inp.observed.axis() if verdict.eligible else None,
            segment_key=inp.observed.segment_key,
            eligible=verdict.eligible,
            dwell_seconds=pin.dwell_seconds,
            gap_bound_seconds=min(inp.stale_after_seconds, pin.stale_after_seconds),
            fingerprint=w.rule_fingerprint,
        )
        if outcome.clear:
            ready.append(w)
        elif outcome.action == "put" and outcome.marker is not None:
            steps.append(PutDwell(outcome.marker))
        elif outcome.action == "delete":
            steps.append(DeleteDwell(w.warning_id))
    return ready, steps


def _sensor_raises(
    inp: SensorEvaluatedInput,
    view: EngineView,
    plan: escalation.EscalationPlan,
    facts_by_def: Mapping[str, tuple[LimitFacts, LimitResult]],
) -> tuple[list[Raise], list[Step]]:
    raises: list[Raise] = []
    refusals: list[Step] = []
    for d in plan.raises:
        trigger = TriggerRef(ConditionKind.SENSOR_LIMIT, inp.reading_id)
        if _spent(view, d.id, inp.channel_id, trigger):
            refusals.append(_refuse(d.id, inp.channel_id, "trigger_spent"))
            continue
        facts, result = facts_by_def[d.id]
        fields = _sensor_fields(inp, facts, result, view)
        fields.supersedes = tuple(w.warning_id for w in plan.supersedes)
        fields.previous = plan.previous.warning_id if plan.previous is not None else None
        built = _raise(d, view, fields)
        if built is not None:
            raises.append(built)
    return raises, refusals


def _sensor(inp: SensorEvaluatedInput, view: EngineView) -> Decision:
    group = _sensor_group(inp, view)
    ready, dwell_steps = _sensor_dwell(inp, view, group)
    stale_release = inp.config_release_id != view.release_id
    crossed = [] if stale_release else _crossed(inp, view)
    facts_by_def = {d.id: (facts, result) for d, facts, result in crossed}
    plan = escalation.plan_group([d for d, _, _ in crossed], group, cleared=ready, frozen=view.clears_frozen)
    raises, refusals = _sensor_raises(inp, view, plan, facts_by_def)
    steps: list[Step] = []
    if stale_release:
        steps.append(_refuse("", inp.channel_id, "stale_release"))
    steps.extend(Clear(w.warning_id, ClearReason.SUPERSEDED) for w in plan.supersedes)
    steps.extend(Clear(w.warning_id, ClearReason.CONDITION_FALSE) for w in ready)
    cleared_ids = {w.warning_id for w in plan.supersedes} | {w.warning_id for w in ready}
    steps.extend(DeleteDwell(wid) for wid in sorted(cleared_ids) if wid in view.dwell)
    steps.extend(raises)
    steps.extend(refusals)
    steps.extend(s for s in dwell_steps if not _drops_cleared(s, cleared_ids))
    return tuple(steps)


def _drops_cleared(step: Step, cleared_ids: set[str]) -> bool:
    if isinstance(step, DeleteDwell):
        return step.warning_id in cleared_ids
    if isinstance(step, PutDwell):
        return step.marker.warning_id in cleared_ids
    return False


# ---------------------------------------------------------------------------------------------
# sensor quiet


def _quiet_fields(inp: FreshnessInput, view: EngineView, since: datetime) -> _Fields:
    base = {
        "sensor_name": inp.channel.sensor_name,
        "source_label": inp.channel.source_label,
    }
    if inp.channel.location_label is not None:
        base["location"] = inp.channel.location_label
    if inp.channel.last_received_time is not None:
        base["last_received_time"] = inp.channel.last_received_time
    evidence: tuple[EvidenceRef, ...] = ()
    if inp.last_reading_id is not None:
        evidence = (EvidenceRef(EvidenceKind.READING, inp.last_reading_id, None, since),)
    fields = _Fields(
        scope_key=inp.channel_id,
        trigger=TriggerRef(ConditionKind.SENSOR_QUIET, f"{inp.channel_id}@{since.isoformat()}"),
        origin_mode=inp.origin_mode,
        evidence=evidence,
        evidence_time=since,
        render=_render(
            view,
            base,
            origin_mode=inp.origin_mode,
            evidence_time=since,
            summaries=tuple(None for _ in evidence),
        ),
    )
    fields.channel_id = inp.channel_id
    return fields


def _quiet_covers(cond: SensorQuietCondition, sensor_type_id: str) -> bool:
    return not cond.sensor_type_ids or sensor_type_id in cond.sensor_type_ids


def _quiet_raises(inp: FreshnessInput, view: EngineView) -> list[Step]:
    if inp.state != "stale" or inp.stale_since is None:
        return []
    steps: list[Step] = []
    for d, cond in _defs(view, SensorQuietCondition):
        if not _quiet_covers(cond, inp.sensor_type_id) or (d.id, inp.channel_id) in view.active:
            continue
        fields = _quiet_fields(inp, view, inp.stale_since)
        if _spent(view, d.id, inp.channel_id, fields.trigger):
            steps.append(_refuse(d.id, inp.channel_id, "trigger_spent"))
            continue
        built = _raise(d, view, fields)
        if built is not None:
            steps.append(built)
    return steps


def _fresh_after(inp: FreshnessInput, w: Warning, now: datetime) -> bool:
    """Whether the channel's return to current rests on evidence observed after the raise (D3, D14)."""
    if inp.origin_mode is not w.origin_mode:
        return False
    if inp.origin_mode is OriginMode.REPLAY:
        return inp.receive_reference is not None and w.raised_at < inp.receive_reference <= now
    if inp.observed is None:
        return False
    interval = inp.observed.interval()
    if interval is None:
        return False
    low, high = interval
    return high <= now and low > w.raised_at


def _quiet_clears(inp: FreshnessInput, view: EngineView) -> list[Step]:
    if inp.state != "current" or view.clears_frozen:
        return []
    steps: list[Step] = []
    for w in sorted(view.active.values(), key=lambda x: x.warning_id):
        quiet = w.pin.definition.condition.kind is ConditionKind.SENSOR_QUIET
        if w.active and quiet and w.channel_id == inp.channel_id and _fresh_after(inp, w, view.now):
            steps.append(Clear(w.warning_id, ClearReason.CONDITION_FALSE))
    return steps


def _freshness(inp: FreshnessInput, view: EngineView) -> Decision:
    steps: list[Step] = []
    if inp.state == "stale":
        # Continuity of every dwell on this channel is broken (E24 3.6 resets).
        steps.extend(DeleteDwell(m.warning_id) for m in view.dwell.values() if m.channel_id == inp.channel_id)
    steps.extend(_quiet_clears(inp, view))
    steps.extend(_quiet_raises(inp, view))
    return tuple(steps)


# ---------------------------------------------------------------------------------------------
# mission events


def _event_keys(scope: Scope, inp: MissionEventInput) -> list[str]:
    if scope is Scope.PERSON:
        return sorted(set(inp.person_ids))
    if scope is Scope.CREW:
        return [""]
    return [inp.location if inp.location else f"environment:{inp.environment_id}"]


def _event_fields(inp: MissionEventInput, view: EngineView, key: str, scope: Scope) -> _Fields:
    base: dict[str, str] = {}
    if inp.location:
        base["location"] = inp.location
    fields = _Fields(
        scope_key=key,
        trigger=TriggerRef(ConditionKind.MISSION_EVENT, inp.event_id),
        origin_mode=inp.origin_mode,
        evidence=(EvidenceRef(EvidenceKind.MISSION_EVENT, inp.event_id, inp.label, inp.occurred_at),),
        evidence_time=inp.occurred_at,
        render=_render(
            view, base, origin_mode=inp.origin_mode, evidence_time=inp.occurred_at, summaries=(inp.label,)
        ),
    )
    if scope is Scope.PERSON:
        fields.person_id = key
    if scope is Scope.HABITAT:
        fields.location = inp.location
    return fields


def _occurrence(inp: MissionEventInput, w: Warning) -> occurrences.Occurrence:
    return occurrences.Occurrence(
        w.warning_id, w.definition_id, w.scope_key, inp.event_id, inp.occurred_at, None
    )


def _event_for_key(d: WarningDefinition, inp: MissionEventInput, view: EngineView, key: str) -> Step | None:
    trigger = TriggerRef(ConditionKind.MISSION_EVENT, inp.event_id)
    if d.scope is Scope.PERSON and not _person_ok(view, [inp.origin_mode]):
        return _refuse(d.id, key, "origin_mismatch")
    if _spent(view, d.id, key, trigger):
        return _refuse(d.id, key, "trigger_spent")
    active = view.active.get((d.id, key))
    known = view.occurrences.get(active.warning_id, ()) if active is not None else None
    verdict = occurrences.classify_trigger(
        trigger_ref=inp.event_id,
        occurred_at=inp.occurred_at,
        latest_clear=view.latest_clear.get((d.id, key)),
        known=known,
    )
    if verdict == "clear_later":
        return _refuse(d.id, key, "clear_later")
    if verdict == "raise":
        return _raise(d, view, _event_fields(inp, view, key, d.scope))
    if verdict == "attach" and active is not None:
        return Attach(active.warning_id, _occurrence(inp, active))
    return None


def _event_raises(inp: MissionEventInput, view: EngineView) -> list[Step]:
    steps: list[Step] = []
    for d, cond in _defs(view, MissionEventCondition):
        if inp.event_type not in cond.event_types:
            continue
        for key in _event_keys(d.scope, inp):
            built = _event_for_key(d, inp, view, key)
            if built is not None:
                steps.append(built)
    return steps


def _pinned_clears_event(w: Warning, inp: MissionEventInput) -> bool:
    cond = w.pin.definition.condition
    if (
        not isinstance(cond, MissionEventCondition)
        or w.pin.definition.clear.by_engine is not ClearBy.CLEAR_EVENT
    ):
        return False
    return inp.event_type in cond.clear_event_types


def _event_clears(inp: MissionEventInput, view: EngineView) -> list[Step]:
    steps: list[Step] = []
    for w in sorted(view.active.values(), key=lambda x: x.warning_id):
        if not w.active or not _pinned_clears_event(w, inp):
            continue
        if w.scope_key not in _event_keys(w.pin.definition.scope, inp):
            continue
        if not origin.clears_across_origin(evidence_origin=inp.origin_mode, warning_origin=w.origin_mode):
            continue
        effect = occurrences.apply_clear_event(view.occurrences.get(w.warning_id, ()), inp.occurred_at)
        if effect.resolves:
            steps.append(ResolveOccurrences(w.warning_id, inp.occurred_at, inp.event_id))
        if effect.clears_warning and not view.clears_frozen:
            steps.append(Clear(w.warning_id, ClearReason.CLEAR_EVENT))
    return steps


def _mission_event(inp: MissionEventInput, view: EngineView) -> Decision:
    return tuple(_event_clears(inp, view) + _event_raises(inp, view))


# ---------------------------------------------------------------------------------------------
# state facts


def _compare_fact(a: object, b: object) -> bool | None:
    """True when equal, False when different, `None` when the types are not comparable (never a guess)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return (a == b) if isinstance(a, bool) and isinstance(b, bool) else None
    if isinstance(a, str) or isinstance(b, str):
        return (a == b) if isinstance(a, str) and isinstance(b, str) else None
    return a == b


def _fact_key(scope: Scope, inp: StateFactInput) -> str | None:
    if scope is Scope.HABITAT:
        return f"environment:{inp.environment_id}"
    if scope is Scope.CREW:
        return ""
    return None


def _fact_clears(inp: StateFactInput, view: EngineView) -> list[Step]:
    if view.clears_frozen:
        return []
    steps: list[Step] = []
    for w in sorted(view.active.values(), key=lambda x: x.warning_id):
        cond = w.pin.definition.condition
        if not w.active or not isinstance(cond, StateFactCondition) or cond.fact != inp.fact:
            continue
        if w.scope_key != _fact_key(w.pin.definition.scope, inp):
            continue
        holds = _compare_fact(inp.value, cond.equals) is False  # an incomparable value never clears
        if holds and inp.origin_mode is w.origin_mode and inp.at > w.raised_at:
            steps.append(Clear(w.warning_id, ClearReason.CONDITION_FALSE))
    return steps


def _fact_raises(inp: StateFactInput, view: EngineView) -> list[Step]:
    steps: list[Step] = []
    for d, cond in _defs(view, StateFactCondition):
        key = _fact_key(d.scope, inp)
        if cond.fact != inp.fact or key is None:
            continue
        if _compare_fact(inp.value, cond.equals) is not True or (d.id, key) in view.active:
            continue
        trigger = TriggerRef(ConditionKind.STATE_FACT, inp.cause_event_id)
        if _spent(view, d.id, key, trigger):
            steps.append(_refuse(d.id, key, "trigger_spent"))
            continue
        fields = _Fields(
            scope_key=key,
            trigger=trigger,
            origin_mode=inp.origin_mode,
            evidence=(EvidenceRef(EvidenceKind.STATE_FACT, inp.cause_event_id, None, inp.at),),
            evidence_time=inp.at,
            render=_render(view, {}, origin_mode=inp.origin_mode, evidence_time=inp.at, summaries=(None,)),
        )
        built = _raise(d, view, fields)
        if built is not None:
            steps.append(built)
    return steps


def _state_fact(inp: StateFactInput, view: EngineView) -> Decision:
    return tuple(_fact_clears(inp, view) + _fact_raises(inp, view))


# ---------------------------------------------------------------------------------------------
# answers


def _window_event(within: WithinDays, inp: AnswerInput, view: EngineView) -> MissionEventInput | None:
    """The latest event of the configured type naming this person or nobody."""
    pool = [
        e
        for e in view.answer_events
        if e.event_type == within.event_type and (not e.person_ids or inp.person_id in e.person_ids)
    ]
    return max(pool, key=lambda e: (e.occurred_at, e.event_id), default=None)


def _answer_matches(cond: AnswerCondition, inp: AnswerInput, view: EngineView) -> bool:
    if cond.question_id != inp.question_id or inp.presence not in {p.value for p in cond.presence}:
        return False
    within = cond.within_days_after_event
    if within is None:
        return True
    event = _window_event(within, inp, view)
    if event is None:
        return False
    return answer_window_satisfied(
        answered_at=inp.answered_at,
        answered_corrected_at=inp.answered_corrected_at,
        occurred_at=event.occurred_at,
        occurred_corrected_at=event.occurred_corrected_at,
        days=within.days,
    )


def _answer(inp: AnswerInput, view: EngineView) -> Decision:
    steps: list[Step] = []
    for d, cond in _defs(view, AnswerCondition):
        if not _answer_matches(cond, inp, view) or (d.id, inp.person_id) in view.active:
            continue
        trigger = TriggerRef(ConditionKind.ANSWER, inp.answer_id)
        if not _person_ok(view, [inp.origin_mode]):
            steps.append(_refuse(d.id, inp.person_id, "origin_mismatch"))
            continue
        if _spent(view, d.id, inp.person_id, trigger):
            steps.append(_refuse(d.id, inp.person_id, "trigger_spent"))
            continue
        base = {"answer_day_label": inp.day_label, "answer_time": inp.time_label}
        fields = _Fields(
            scope_key=inp.person_id,
            trigger=trigger,
            origin_mode=inp.origin_mode,
            evidence=(EvidenceRef(EvidenceKind.ANSWER, inp.answer_id, None, inp.answered_at),),
            evidence_time=inp.answered_at,
            render=_render(
                view, base, origin_mode=inp.origin_mode, evidence_time=inp.answered_at, summaries=(None,)
            ),
        )
        fields.person_id = inp.person_id
        built = _raise(d, view, fields)
        if built is not None:
            steps.append(built)
    return tuple(steps)


# ---------------------------------------------------------------------------------------------
# ground silence


def _pinned_contact(w: Warning) -> datetime | None:
    try:
        return datetime.fromisoformat(w.trigger.ref)
    except ValueError:
        return None


def _ground_clears(inp: GroundLinkInput, view: EngineView) -> list[Step]:
    steps: list[Step] = []
    link_origin = origin.ground_origin(inp.source, view.mission_mode)
    for w in sorted(view.active.values(), key=lambda x: x.warning_id):
        if not w.active or not isinstance(w.pin.definition.condition, GroundSilenceCondition):
            continue
        raised_for = _pinned_contact(w)
        if raised_for is None or view.clears_frozen:
            continue
        ended = ground_silence_ended(
            status=inp.status, next_contact_at=inp.next_contact_at, raised_for=raised_for
        )
        if ended and link_origin is w.origin_mode and inp.observed_at > w.raised_at:
            steps.append(Clear(w.warning_id, ClearReason.CONDITION_FALSE))
            steps.append(CancelDeadline(_ground_key(w.definition_id)))
    return steps


def _ground_key(definition_id: str) -> str:
    return f"ground_silence:{definition_id}"


def _ground_raises(inp: GroundLinkInput, view: EngineView) -> list[Step]:
    steps: list[Step] = []
    link_origin = origin.ground_origin(inp.source, view.mission_mode)
    for d, cond in _defs(view, GroundSilenceCondition):
        if (d.id, "") in view.active:
            continue
        threshold = cond.after_next_contact_seconds
        if threshold is None or inp.next_contact_at is None or inp.gap_planned or inp.status != "down":
            if inp.status != "down" or inp.gap_planned:
                steps.append(CancelDeadline(_ground_key(d.id)))
            continue
        due = ground_silence_due_at(inp.next_contact_at, threshold)
        if not ground_silence_holds(
            now=view.now,
            status=inp.status,
            next_contact_at=inp.next_contact_at,
            threshold_seconds=threshold,
            gap_planned=inp.gap_planned,
        ):
            steps.append(ScheduleDeadline(_ground_key(d.id), "absolute", due, None))
            continue
        trigger = TriggerRef(ConditionKind.GROUND_SILENCE, inp.next_contact_at.isoformat())
        if _spent(view, d.id, "", trigger):
            steps.append(_refuse(d.id, "", "trigger_spent"))
            continue
        fields = _Fields(
            scope_key="",
            trigger=trigger,
            origin_mode=link_origin,
            evidence=(),
            evidence_time=due,
            render=_render(view, {}, origin_mode=link_origin, evidence_time=due, summaries=()),
        )
        built = _raise(d, view, fields)
        if built is not None:
            steps.append(built)
    return steps


def _ground(inp: GroundLinkInput, view: EngineView) -> Decision:
    return tuple(_ground_clears(inp, view) + _ground_raises(inp, view))


# ---------------------------------------------------------------------------------------------
# supply cover


def _supply_clears(inp: SupplyCoverInput, view: EngineView) -> list[Step]:
    if view.clears_frozen:
        return []
    rows = {r.supply_id: r for r in inp.rows}
    steps: list[Step] = []
    for w in sorted(view.active.values(), key=lambda x: x.warning_id):
        cond = w.pin.definition.condition
        row = rows.get(w.scope_key)
        if not w.active or not isinstance(cond, SupplyCoverLowCondition) or row is None:
            continue
        if cond.below_uses is None or row.cover_uses is None or row.cover_uses < cond.below_uses:
            continue  # a null cover changes nothing
        newer = (
            w.source_partition == row.partition
            and w.source_revision is not None
            and row.stock_revision > w.source_revision
        )
        if newer and inp.origin_mode is w.origin_mode and inp.observed_at > w.raised_at:
            steps.append(Clear(w.warning_id, ClearReason.CONDITION_FALSE))
    return steps


def _supply_trigger(inp: SupplyCoverInput, row: SupplyCoverRow) -> TriggerRef:
    if inp.supply_use_id is not None:
        return TriggerRef(ConditionKind.SUPPLY_COVER_LOW, inp.supply_use_id)
    return TriggerRef(ConditionKind.SUPPLY_COVER_LOW, f"{row.release_id}:{inp.environment_id}")


def _supply_fields(inp: SupplyCoverInput, row: SupplyCoverRow, view: EngineView) -> _Fields:
    at = row.last_use_at if inp.cause == "use" and row.last_use_at is not None else inp.observed_at
    base = {"supply_name": row.supply_name, "uses": str(row.cover_uses)}
    if row.quantity_now is not None:
        base["quantity"] = f"{row.quantity_now.value} {row.quantity_now.unit.code}"
    if row.per_use is not None:
        base["per_use"] = f"{row.per_use.value} {row.per_use.unit.code}"
    evidence: tuple[EvidenceRef, ...] = ()
    if inp.supply_use_id is not None:
        evidence = (EvidenceRef(EvidenceKind.SUPPLY_USE, inp.supply_use_id, None, at),)
    fields = _Fields(
        scope_key=row.supply_id,
        trigger=_supply_trigger(inp, row),
        origin_mode=inp.origin_mode,
        evidence=evidence,
        evidence_time=at,
        render=_render(
            view, base, origin_mode=inp.origin_mode, evidence_time=at, summaries=tuple(None for _ in evidence)
        ),
    )
    fields.source_revision = row.stock_revision
    fields.source_partition = row.partition
    return fields


def _supply_raises(inp: SupplyCoverInput, view: EngineView) -> list[Step]:
    steps: list[Step] = []
    for d, cond in _defs(view, SupplyCoverLowCondition):
        if cond.below_uses is None:
            continue  # a null threshold raises nothing
        for row in sorted(inp.rows, key=lambda r: r.supply_id):
            if row.cover_uses is None or row.cover_uses >= cond.below_uses:
                continue
            if (d.id, row.supply_id) in view.active:
                continue
            fields = _supply_fields(inp, row, view)
            if _spent(view, d.id, row.supply_id, fields.trigger):
                steps.append(_refuse(d.id, row.supply_id, "trigger_spent"))
                continue
            built = _raise(d, view, fields)
            if built is not None:
                steps.append(built)
    return steps


def _supply(inp: SupplyCoverInput, view: EngineView) -> Decision:
    return tuple(_supply_clears(inp, view) + _supply_raises(inp, view))


# ---------------------------------------------------------------------------------------------
# assistant insights


def _insight_definition(inp: InsightInput, view: EngineView) -> WarningDefinition | None:
    topic = inp.policy.topic(inp.draft.topic_id)
    if topic is None or topic.warning_definition_id is None:
        return None
    for d, cond in _defs(view, InsightCondition):
        if d.id == topic.warning_definition_id and inp.draft.topic_id in cond.topic_ids:
            return d
    return None


def _insight_evidence(inp: InsightInput) -> tuple[EvidenceRef, ...]:
    draft = inp.draft
    facts = dict(inp.facts)
    refs = [EvidenceRef(EvidenceKind.INSIGHT, draft.insight_id, draft.title, draft.created_at)]
    for key in draft.evidence:
        record = facts.get(key)
        refs.append(
            EvidenceRef(
                EvidenceKind(key.kind),
                key.ref_id,
                record.summary if record is not None else None,
                record.observed_at if record is not None else None,
            )
        )
    return tuple(refs)


def _insight(inp: InsightInput, view: EngineView) -> Decision:
    draft = inp.draft
    violations = insight_bounds.validate(draft, dict(inp.facts), inp.policy)
    if violations:
        return (RejectInsight(draft.insight_id, tuple(violations)),)
    d = _insight_definition(inp, view)
    if d is None or (d.id, draft.person_id) in view.active:
        return ()
    if d.level not in inp.policy.allowed_warning_levels or d.raised_by is not RaisedBy.ASSISTANT:
        return (RejectInsight(draft.insight_id, ("level_not_allowed",)),)
    if not _person_ok(view, [draft.origin_mode]):
        return (_refuse(d.id, draft.person_id, "origin_mismatch"),)
    trigger = TriggerRef(ConditionKind.ASSISTANT_INSIGHT, draft.insight_id)
    if _spent(view, d.id, draft.person_id, trigger):
        return (_refuse(d.id, draft.person_id, "trigger_spent"),)
    topic = inp.policy.topic(draft.topic_id)
    base = {
        "insight_title": draft.title,
        "insight_text": draft.text,
        "insight_why": draft.why,
        "insight_fragment": draft.headline_fragment,
    }
    evidence = _insight_evidence(inp)
    region = topic.body_region if topic is not None else None
    fields = _Fields(
        scope_key=draft.person_id,
        trigger=trigger,
        origin_mode=draft.origin_mode,
        evidence=evidence,
        evidence_time=draft.created_at,
        render=_render(
            view,
            base,
            origin_mode=draft.origin_mode,
            evidence_time=draft.created_at,
            summaries=tuple(e.summary for e in evidence),
            assistant_steps=draft.steps,
            body_region=region,
        ),
    )
    fields.person_id = draft.person_id
    fields.insight_id = draft.insight_id
    built = _raise(d, view, fields)
    return (built,) if built is not None else ()


def _insight_expired(inp: InsightExpiredInput, view: EngineView) -> Decision:
    if view.clears_frozen or view.clock_regressed:
        return ()
    for w in sorted(view.active.values(), key=lambda x: x.warning_id):
        if w.active and w.insight_id == inp.insight_id and w.raised_by is RaisedBy.ASSISTANT:
            expires = w.pin.definition.clear.expires_after_seconds
            if expiry_due(
                elapsed_seconds=inp.elapsed_seconds, expires_after_seconds=expires, clock_regressed=False
            ):
                return (Clear(w.warning_id, ClearReason.EXPIRED),)
    return ()
