"""Subscriptions, deadlines, recovery and the clock (E24 2, 3.11, 5.3, 5.5).

Each producer event is mapped to E24's private input in this anti-corruption layer and applied through `run_input`
(the savepoint boundary). Nothing here reads a wall clock: time is `uow.now` or an observed instant carried by the
input.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import ValidationError

from crew_health.contracts_gen.schemas.ground_link_schema import GroundLink
from crew_health.kernel.clock import ClockCorrected, ClockRegressed, NormalizedTime, TimeInput
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import StoredEvent, UnitOfWork
from crew_health.mission.application.ports import MissionEventRecorded, StateFactChanged
from crew_health.records.application.ports import AnswerAcceptedFact
from crew_health.rules.application import core, input_codec
from crew_health.rules.application.core import Runtime
from crew_health.rules.domain.inputs import (
    AnswerInput,
    ChannelRenderFacts,
    FreshnessInput,
    GroundLinkInput,
    InsightExpiredInput,
    LimitRenderFacts,
    LimitResult,
    MissionEventInput,
    ObservedTime,
    SensorEvaluatedInput,
    StateFactInput,
)
from crew_health.rules.domain.vocabulary import OriginMode
from crew_health.sensors.application.ports import ChannelFreshnessChanged, SensorEvaluated


def on_answer(rt: Runtime, uow: UnitOfWork, event: AnswerAcceptedFact) -> None:
    inp = AnswerInput(
        answer_id=event.answer_id, person_id=event.patient_id, question_id=event.question_id,
        presence=event.presence, answered_at=event.answered_at, answered_corrected_at=None,
        origin_mode=OriginMode(event.origin_mode),
        day_label=rt.day_label(event.answered_at) or f"{event.answered_at:%Y-%m-%d}",
        time_label=f"{event.answered_at:%H:%M}",
    )  # fmt: skip
    core.run_input(rt, uow, inp)


def on_clock_regressed(rt: Runtime, uow: UnitOfWork, event: ClockRegressed) -> None:
    """Expiry clears are suspended until the correction (3.11); raises continue."""
    rt.facts_store.put(uow.connection, core.FACT_REGRESSED, uow.now, {"on": True})


def on_clock_corrected(rt: Runtime, uow: UnitOfWork, event: ClockCorrected) -> None:
    """A relative timer moves with the clock: every pending expiry shifts by the correction (3.11)."""
    c = uow.connection
    shift = timedelta(milliseconds=event.offset_ms - event.previous_offset_ms)
    after = ""
    while True:
        keys = rt.deadlines.page(c, after=after, limit=rt.settings.integer("rules.reeval_batch"))
        if not keys:
            break
        for key in keys:
            after = key
            row = rt.deadlines.get(c, key)
            if row is not None and row[0] == "elapsed" and row[1] is not None:
                core.schedule(rt, uow, key, "elapsed", row[1] + shift, row[2])
    cleared_at = uow.now + timedelta(microseconds=1)
    rt.facts_store.put(c, core.FACT_REGRESSED, cleared_at, {"on": False})
    expire_overdue(rt, uow)  # expiries that came due while clears were suspended
    reevaluate_ground(
        rt, uow
    )  # an absolute deadline does not move: the new clock may put it in the past (3.11)


def on_ground_link(rt: Runtime, uow: UnitOfWork, inp: GroundLinkInput) -> None:
    """Newer observation wins: an older or duplicate delivery never replaces the stored link state (D3)."""
    body: dict[str, object] = {"kind": "GroundLinkInput", "json": input_codec.dumps(inp)[1]}
    if rt.facts_store.put(uow.connection, core.FACT_GROUND, inp.observed_at, body):
        core.run_input(rt, uow, inp)


def expire_overdue(rt: Runtime, uow: UnitOfWork) -> None:
    """An expiry that fired while `ClockRegressed` suspended clears was consumed without effect; once the clock is
    corrected every active insight warning is checked again against its own expiry (3.11). The engine decides."""
    for w in rt.warnings.active(uow.connection, limit=rt.cap("rules.active_scan_max")):
        if w.insight_id is not None:
            elapsed = max(0, int((uow.now - w.raised_at).total_seconds()))
            core.run_input(rt, uow, InsightExpiredInput(w.insight_id, elapsed))


def reevaluate_ground(rt: Runtime, uow: UnitOfWork) -> None:
    """Re-run the last stored link state under the current clock and release. A forward correction or a new rule may
    raise; nothing here clears, because the engine clears only on the link itself (3.11)."""
    stored = rt.facts_store.get(uow.connection, core.FACT_GROUND)
    if stored is not None:
        core.run_input(rt, uow, input_codec.loads("GroundLinkInput", str(stored["json"])))


def on_ground_link_event(rt: Runtime, uow: UnitOfWork, event: StoredEvent) -> None:
    """The outbox event `ground_link.changed` of the link owner (E29), mapped to E24's private input. A payload that
    does not match the contract is never guessed at: the engine is marked degraded and the event is skipped, loudly."""
    try:
        link = GroundLink.model_validate_json(event.data_json)
    except ValidationError:
        if rt.health is not None:
            rt.health.mark_degraded(core.HEALTH_COMPONENT, core.FAULT_CODE, uow.now)
        return
    on_ground_link(
        rt, uow,
        GroundLinkInput(
            link.status,
            None if link.next_contact_at is None else link.next_contact_at.root,
            link.source,
            link.environment_id.root,
            link.gap_planned,
            event.occurred_at,
        ),
    )  # fmt: skip


def on_activation(rt: Runtime, uow: UnitOfWork, event: StoredEvent) -> None:
    """`config.release_activated` and `environment.activated` (3.8, C12, D1). An activation never clears, lowers or
    re-pins an active warning: the active ones keep their pinned definition and clear by it. What it does:

    - the release facts of the new release are built now, so a document that cannot be read fails here and not at the
      next input (the old facts stay in the cache until this succeeds);
    - rows the evaluator gave up on are retried (`stuck` back to `pending`), and recovery runs at once;
    - the stored link state is re-evaluated under the new release, which may raise a new ground silence.

    Sensor, mission and answer state is re-evaluated by its producers after activation (an input computed under
    another release is refused), so E24 holds no copy of it.
    """
    rt.facts(uow)
    c = uow.connection
    if rt.pending.release_stuck(c, uow.now):
        core.record_recovery_due(rt, uow)
    reevaluate_ground(rt, uow)


def fire_deadline(rt: Runtime, uow: UnitOfWork, job: ScheduledJob) -> None:
    """UC-R10: the fired key is deleted in the same transaction."""
    key = job.payload.get("key", "")
    row = rt.deadlines.get(uow.connection, key)
    if row is None or row[1] is None:
        return  # already fired or cancelled
    rt.deadlines.delete(uow.connection, key)
    if key.startswith(core.EXPIRE_PREFIX):
        w = rt.warnings.get(uow.connection, key.removeprefix(core.EXPIRE_PREFIX))
        if w is not None and w.insight_id is not None:
            elapsed = int((uow.now - w.raised_at).total_seconds())
            core.run_input(rt, uow, InsightExpiredInput(w.insight_id, max(0, elapsed)))
    elif key.startswith("ground_silence:"):
        stored = rt.facts_store.get(uow.connection, core.FACT_GROUND)
        if stored is not None:
            inp = input_codec.loads("GroundLinkInput", str(stored["json"]))
            core.run_input(rt, uow, inp)


def recover_pending(rt: Runtime, uow: UnitOfWork, job: ScheduledJob) -> None:
    """UC-R13: one recovery row per unit of work, in input order; a failing row backs off and eventually sticks."""
    s, c = rt.settings, uow.connection
    rows = rt.pending.due(c, uow.now, 1)
    if not rows:
        _finish(rt, uow)
        return
    pending_id, kind, _, text, attempts = rows[0]
    try:
        with uow.savepoint("rules_recover"):
            core.recover_one(rt, uow, pending_id, kind, text)
    except Exception as fault:  # noqa: BLE001 - a failing row stays; storage faults surface through the same path
        from crew_health.kernel.errors import StorageUnavailable

        if isinstance(fault, StorageUnavailable):
            raise
        stuck = attempts + 1 >= s.integer("rules.reeval_max_attempts")
        wait = min(
            s.integer("rules.reeval_backoff_max_s"), s.integer("rules.reeval_backoff_initial_s") * 2**attempts
        )
        retry_at = uow.now + timedelta(seconds=wait)
        rt.pending.failed(c, pending_id, core.fault_code(fault), retry_at, stuck=stuck)
        if not stuck:
            core.schedule(
                rt, uow, core.RECOVER_HANDLER, "absolute", retry_at, None
            )  # the next attempt is a job
        return
    if rt.pending.due(c, uow.now, 1):
        core.schedule(rt, uow, core.RECOVER_HANDLER, "absolute", uow.now, None)
    else:
        _finish(rt, uow)


def _finish(rt: Runtime, uow: UnitOfWork) -> None:
    """When no row remains the engine is healthy again; clears resume with the next eligible evidence (5.5)."""
    if rt.health is not None and not rt.pending.any(uow.connection):
        rt.health.mark_ok(core.HEALTH_COMPONENT)


def dump_fact(body: dict[str, object]) -> str:
    return json.dumps(body, sort_keys=True)


def _observed(
    original: datetime, corrected: datetime | None, uncertainty_ms: int | None,
    state: Literal["known", "unknown", "future", "replay"], clock: str, segment: str, version: int, origin: OriginMode,
) -> ObservedTime:  # fmt: skip
    source = TimeInput(original, clock, segment, version, "source_raw", origin.value)
    return ObservedTime(source, NormalizedTime(original, corrected, None, uncertainty_ms, state))


def on_sensor_evaluated(rt: Runtime, uow: UnitOfWork, e: SensorEvaluated) -> None:
    """E23's evaluation of one channel, mapped to E24's private input (R-E24-14). The limit pin comes from E24's
    own release facts, never from the producer."""
    facts = rt.facts(uow)
    origin = OriginMode(e.origin_mode)
    results = tuple(
        LimitResult(
            o.limit_id, o.crossed, o.clear_holds,
            None if o.value is None or o.value_unit is None else Quantity(o.value, Unit(o.value_unit)),
            o.coverage, facts.limits[o.limit_id].limit_fingerprint if o.limit_id in facts.limits else None,
            LimitRenderFacts(o.aggregation_id, o.aggregation_label, o.window_seconds, o.value_text,
                             o.value_unit or "", o.unit_display, o.limit_value_text),
        )
        for o in e.results
    )  # fmt: skip
    inp = SensorEvaluatedInput(
        config_release_id=e.config_release_id, sensor_type_id=e.sensor_type_id, channel_id=e.channel_id,
        location=e.location, origin_mode=origin,
        observed=_observed(e.observed_original_at, e.observed_corrected_at, e.observed_uncertainty_ms,
                           e.observed_state, e.source_clock_id, e.segment_id, e.source_clock_version, origin),
        receive_reference=e.receive_reference, reading_id=e.reading_id, clock_state=e.clock_state,
        freshness=e.freshness, stale_after_seconds=e.stale_after_seconds, results=results,
        channel=ChannelRenderFacts(e.sensor_name, e.location_label, e.source_label, e.last_received_time, e.replay_dataset),
    )  # fmt: skip
    core.run_input(rt, uow, inp)


def on_freshness(rt: Runtime, uow: UnitOfWork, e: ChannelFreshnessChanged) -> None:
    origin = OriginMode(e.origin_mode)
    observed = None
    if e.observed_original_at is not None and e.observed_state is not None:
        observed = _observed(e.observed_original_at, e.observed_corrected_at, e.observed_uncertainty_ms,
                             e.observed_state, e.source_clock_id or "", e.segment_id or "",
                             e.source_clock_version or 0, origin)  # fmt: skip
    inp = FreshnessInput(
        e.config_release_id, e.channel_id, e.sensor_type_id, e.state, e.stale_since, e.last_reading_id, origin,
        observed, e.receive_reference, ChannelRenderFacts(e.sensor_name, e.location_label, e.source_label, None, e.replay_dataset),
    )  # fmt: skip
    core.run_input(rt, uow, inp)


def on_mission_event(rt: Runtime, uow: UnitOfWork, e: MissionEventRecorded) -> None:
    core.run_input(
        rt, uow,
        MissionEventInput(e.event_id, e.mission_event_type, e.label, e.occurred_at, e.occurred_corrected_at,
                          e.recorded_at, OriginMode(e.origin_mode), e.environment_id, e.person_ids, e.location),
    )  # fmt: skip


def on_state_fact(rt: Runtime, uow: UnitOfWork, e: StateFactChanged) -> None:
    core.run_input(
        rt,
        uow,
        StateFactInput(e.fact, e.value, e.environment_id, e.at, e.cause_event_id, OriginMode(e.origin_mode)),
    )


_ = Decimal
