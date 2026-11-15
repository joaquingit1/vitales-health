"""Freshness: the scheduled deadline per channel, the transition it records and the throttled status event.

Primary path (E23 3.4): one generation checked ScheduledJob per channel, committed with the reading that set it.
Backup: the independent sweep (sweep.py). Third line: every read recomputes (queries.decide_now). A stale channel
never becomes current from a clock change, only from an eligible advancing reading.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.scheduler import AbsoluteDeadline, ElapsedDeadline, ScheduledJob
from crew_health.kernel.uow import UnitOfWork
from crew_health.sensors.application.events import emit
from crew_health.sensors.application.exports import freshness_changed
from crew_health.sensors.application.queries import channel_model, decide_now, status_model
from crew_health.sensors.application.runtime import FRESHNESS_HANDLER, STATUS_HANDLER, Runtime
from crew_health.sensors.application.store import ChannelState, StatusRow

Cause = Literal["reading", "sweep", "job", "activation"]
_NEXT = timedelta(seconds=1)  # the inclusive boundary: stale is strictly greater, so look again just after it


def schedule_key(rt: Runtime, channel_id: str) -> str:
    return rt.ids.derived("sch", "sensors", channel_id)


def schedule_deadline(
    rt: Runtime, uow: UnitOfWork, channel: ChannelState, *, anchor: TimeInput | None, at: datetime | None
) -> None:
    """Replace the channel's job atomically. `anchor` is an S5.2 TimeInput (LIVE, SIM); `at` an absolute fallback."""
    key = schedule_key(rt, channel.facts.channel_id)
    duration = timedelta(seconds=channel.facts.stale_after_seconds)
    if anchor is not None and channel.facts.origin_mode != "REPLAY":
        deadline: AbsoluteDeadline | ElapsedDeadline = ElapsedDeadline(anchor, duration)
    elif at is not None:
        deadline = AbsoluteDeadline(at + duration)
    else:
        return
    current = rt.store.schedule_of(uow, key)
    job = ScheduledJob(key, FRESHNESS_HANDLER, deadline, {"channel_id": channel.facts.channel_id}, None)
    uow.schedule(job, expected_generation=None if current is None else current.generation)


def refresh(
    rt: Runtime, uow: UnitOfWork, channel: ChannelState, *, cause: Cause, status: bool = True
) -> ChannelState:
    """Recompute freshness at uow.now; persist and announce a transition. Never lifts stale without new data."""
    environment = rt.store.environment(uow)
    decision = decide_now(uow, channel, environment, sticky=cause != "reading")
    allow_current = cause == "reading"
    state = (
        decision.state
        if (decision.state != "current" or allow_current or channel.freshness != "stale")
        else "stale"
    )
    if channel.freshness == "stale" and state == "current" and not allow_current:
        state = "stale"
    updated = replace(
        channel,
        freshness=state,
        stale_since=decision.stale_since if state == "stale" else None,
        next_stale_check_at=decision.next_check_at,
        clock_state=decision.clock_state,
    )
    if updated == channel:
        return channel
    rt.store.save_channel(uow, updated)
    if state != channel.freshness:
        announce(
            rt,
            uow,
            channel,
            updated,
            environment_id=None if environment is None else environment.environment_id,
            status=status,
        )
    return updated


def announce(
    rt: Runtime,
    uow: UnitOfWork,
    before: ChannelState,
    after: ChannelState,
    *,
    environment_id: str | None,
    status: bool,
) -> None:
    model = channel_model(uow, after, rt.store.environment(uow))
    if before.freshness == "current" and after.freshness == "stale":
        emit(
            uow,
            EventType.SENSOR_QUIET,
            ("habitat", "operator"),
            after.facts.channel_id,
            model,
            environment_id,
        )
    elif before.freshness == "stale" and after.freshness == "current":
        emit(
            uow,
            EventType.SENSOR_RESUMED,
            ("habitat", "operator"),
            after.facts.channel_id,
            model,
            environment_id,
        )
    changed = freshness_changed(rt, uow, rt.config_of(uow.release), after)
    if changed is not None:
        uow.publish(changed)  # E24's sensor_quiet raise and dwell reset (R-E24-14)
    if environment_id is not None and status:
        note_status_change(rt, uow, after.facts.sensor_type_id, environment_id)


def note_status_change(rt: Runtime, uow: UnitOfWork, sensor_type_id: str, environment_id: str) -> None:
    """At most one sensor.status_changed per sensor type per expected interval (CONTRACTS 13); the rest trail."""
    config = rt.config_of(uow.release)
    kind = config.sensor_type(sensor_type_id)
    if kind is None:
        return
    row = rt.store.status(uow, sensor_type_id, environment_id) or StatusRow(
        sensor_type_id, environment_id, 0, None, None
    )
    interval = timedelta(seconds=kind.expected_interval_seconds or 0)
    if row.last_emitted_at is None or uow.now - row.last_emitted_at >= interval:
        _emit_status(
            rt, uow, replace(row, revision=row.revision + 1, last_emitted_at=uow.now, pending_emit_at=None)
        )
        return
    when = row.last_emitted_at + interval
    rt.store.save_status(uow, replace(row, revision=row.revision + 1, pending_emit_at=when))
    key = rt.ids.derived("sch", "status", sensor_type_id, environment_id)
    current = rt.store.schedule_of(uow, key)
    uow.schedule(
        ScheduledJob(
            key,
            STATUS_HANDLER,
            AbsoluteDeadline(when),
            {"sensor_type_id": sensor_type_id, "environment_id": environment_id},
            None,
        ),
        expected_generation=None if current is None else current.generation,
    )


def _emit_status(rt: Runtime, uow: UnitOfWork, row: StatusRow) -> None:
    rt.store.save_status(uow, row)
    emit(
        uow,
        EventType.SENSOR_STATUS_CHANGED,
        ("habitat",),
        row.sensor_type_id,
        status_model(rt, uow, row.sensor_type_id),
        row.environment_id,
    )


def status_job(rt: Runtime, uow: UnitOfWork, job: ScheduledJob) -> None:
    """The durable trailing emission: only the current, still pending row emits, with the then current status."""
    row = rt.store.status(uow, job.payload["sensor_type_id"], job.payload["environment_id"])
    if row is None or row.pending_emit_at is None or row.pending_emit_at > uow.now:
        return
    _emit_status(rt, uow, replace(row, last_emitted_at=uow.now, pending_emit_at=None))


def freshness_job(rt: Runtime, uow: UnitOfWork, job: ScheduledJob) -> None:
    """Scheduled handler. Channels outside the active environment are skipped (the r4 exclusion check)."""
    channel = rt.store.channel(uow, job.payload["channel_id"])
    environment = rt.store.environment(uow)
    if (
        channel is None
        or environment is None
        or environment.environment_id not in channel.facts.environment_ids
    ):
        return
    after = refresh(rt, uow, channel, cause="job")
    if after.freshness == "current" and after.next_stale_check_at is not None:
        schedule_deadline(
            rt,
            uow,
            after,
            anchor=None,
            at=after.next_stale_check_at + _NEXT - timedelta(seconds=after.facts.stale_after_seconds),
        )
