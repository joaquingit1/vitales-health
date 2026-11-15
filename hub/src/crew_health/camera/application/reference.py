"""CameraReferencePort for E30's teach and check (A7: E06 provides it; E30 4.1, X8; SYSTEM 8.1 Q8).

Every call is bounded by the caller's own timeout or wait and never raises across the port (m2). begin_window asks
the worker for a reference window in the worker's own time (hub window plus the probed offset), on the cameras the
device configuration pairs with the record's person (B1), for the origin the check needs; outcome polls the worker
until the reading is decided or the wait is spent; abandon stops the capture.

Persisted windows (M8, Q8 "preserve every available camera Observation"): a reading of a `persist` window is
recorded once as an Observation through ObservationCommands (E01 records) before it is returned, under the ticket as
its idempotency key, and the worker releases it only when told the observation it became. Abandoning a persisted
window records a decided reading first, so abandoning never discards one. Without ObservationCommands a persisted
window is refused with a reason (the check is then unchecked, which Q8 allows), never shown and lost. A drill
(persist false) records nothing. A reading carries the selected camera's value with its UCUM unit (rule 8), the
origin the worker reports and the probe's clock uncertainty; a refusal carries its reason. The camera never vetoes
the manual count: the comparison is E30's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from crew_health.camera.application.dispatch import ENGINE
from crew_health.camera.application.link import Answer, WorkerLink
from crew_health.camera.application.ports import (
    CameraObservationDraft,
    ObservationCommands,
    OriginMode,
    ReferenceOutcome,
    ReferenceTicket,
    ReferenceWindowRequest,
)
from crew_health.kernel.clock import Clock
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.units import Quantity, UnitRegistry
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory

_PENDING = frozenset({"accepted", "capturing"})
_ORIGINS: tuple[OriginMode, ...] = ("LIVE", "REPLAY", "SIM")


@dataclass(frozen=True, slots=True)
class ReferenceLimits:
    timeout_ms: int
    poll_ms: int
    max_polls: int
    warmup_max_ms: int


@dataclass(frozen=True, slots=True)
class Ticket:
    ticket_id: str
    record_id: str
    persist: bool
    observation_id: str | None


class ReferenceStore(Protocol):
    def open(self, session: StorageSession, ticket: Ticket, *, at: datetime) -> None: ...
    def get(self, session: StorageSession, ticket_id: str) -> Ticket | None: ...
    def recorded(
        self, session: StorageSession, ticket_id: str, observation_id: str, *, at: datetime
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _Reading:
    value: Quantity
    origin: OriginMode
    uncertainty: int
    views: int
    code: str
    camera_name: str
    observed_at: datetime
    quality_rule: str


def _ms(value: timedelta) -> int:
    return max(0, value // timedelta(milliseconds=1))


class CameraReference:
    def __init__(
        self,
        *,
        link: WorkerLink,
        clock: Clock,
        ids: IdFactory,
        units: UnitRegistry | None,
        limits: ReferenceLimits,
        pause: Callable[[float], None],
        uows: UnitOfWorkFactory,
        snapshots: ReadSnapshotFactory,
        store: ReferenceStore,
        observations: ObservationCommands | None,
        adapter_id: str,
    ) -> None:
        self._link, self._clock, self._ids, self._units = link, clock, ids, units
        self._limits, self._pause, self._uows, self._snapshots = limits, pause, uows, snapshots
        self._store, self._observations, self._adapter_id = store, observations, adapter_id

    # ------------------------------------------------------------------------------------------- begin

    def begin_window(self, request: ReferenceWindowRequest, *, timeout: timedelta) -> ReferenceTicket:
        if request.window_end <= request.window_start or request.ticket_ttl <= timedelta(0):
            return ReferenceTicket(None, "invalid_window")
        probe = self._link.fresh(self._clock.now())
        if probe is None or not probe.camera_count:
            return ReferenceTicket(None, "camera_unavailable")
        if request.capability_code not in probe.capability_codes:
            return ReferenceTicket(None, "capability_not_supported")
        # before the record check: a camera on another source is never compared (E30 3.6), and the check says so
        if probe.origin_mode != request.required_origin:
            return ReferenceTicket(None, "camera_origin_mismatch")
        if request.persist and self._observations is None:
            return ReferenceTicket(None, "camera_record_unavailable")
        devices = probe.paired_with(request.record_id)
        if not devices:
            return ReferenceTicket(None, "camera_not_paired")
        ticket = self._ids.derived(
            "crf", request.guided_check_id, str(request.attempt_no), request.capability_code
        )
        with self._uows.begin(actor=ENGINE, correlation_id=self._ids.new("cor")) as uow:
            self._store.open(
                uow.connection, Ticket(ticket, request.record_id, request.persist, None), at=uow.now
            )
        body: dict[str, object] = {
            "acquisition_id": ticket,
            "kind": "reference",
            "capability_code": request.capability_code,
            "patient_id": request.record_id,
            "device_ids": list(devices),
            "required_origin": request.required_origin,
            "persist": request.persist,
            "ttl_ms": min(_ms(request.ticket_ttl), probe.max_ttl_ms),
            "clock_offset_ms": probe.offset_ms,
            "clock_uncertainty_ms": probe.uncertainty_ms,
            "warmup_max_ms": self._limits.warmup_max_ms,
            **WorkerLink.window(request.window_start, request.window_end),
        }
        answer = self._link.start(body, timeout_ms=_ms(timeout))
        if answer.kind == "ok":
            return ReferenceTicket(ticket, None)
        if answer.kind == "refused" and answer.code is not None:
            return ReferenceTicket(None, "camera_" + answer.code.lower())
        return ReferenceTicket(None, "camera_unreachable")

    # ------------------------------------------------------------------------------------------- outcome

    def outcome(self, ticket_id: str, *, wait: timedelta) -> ReferenceOutcome:
        """Polls within `wait` in total: each status call may take at most one poll interval (and never more than
        `workers.camera.timeout_ms`), and calls plus pauses never add up to more than the wait (m2)."""
        budget = _ms(wait)
        result = _unavailable("camera_unreachable")
        for index in range(self._limits.max_polls):
            allowed = min(self._limits.timeout_ms, self._limits.poll_ms, budget)
            if allowed <= 0:
                break
            budget -= allowed
            result = self._map(ticket_id, self._link.status(ticket_id, timeout_ms=allowed))
            pause = min(self._limits.poll_ms, budget)
            if result.kind != "pending" or pause <= 0 or index == self._limits.max_polls - 1:
                return result
            budget -= pause
            self._pause(pause / 1000)
        return result

    def abandon(self, ticket_id: str, *, timeout: timedelta) -> None:
        """Stops the capture. A persisted reading already decided is recorded first; if it cannot be recorded the
        worker keeps it (never discarded) and a later outcome or abandon records it."""
        ticket = self._ticket(ticket_id)
        if ticket is not None and ticket.persist and ticket.observation_id is None:
            answer = self._link.status(ticket_id, timeout_ms=_ms(timeout))
            if answer.kind == "ok" and answer.body.get("state") == "reading":
                self._map(ticket_id, answer)  # records it, then releases it on the worker
                return
        recorded = None if ticket is None else ticket.observation_id
        self._link.cancel(ticket_id, timeout_ms=_ms(timeout), recorded_observation_id=recorded)

    # ------------------------------------------------------------------------------------------- mapping

    def _map(self, ticket_id: str, answer: Answer) -> ReferenceOutcome:
        if answer.kind == "not_found":
            return _unavailable("expired")
        if answer.kind != "ok":
            return _unavailable("camera_unreachable")
        body = answer.body
        state, uncertainty = body.get("state"), body.get("clock_uncertainty_ms")
        if state in _PENDING:
            return ReferenceOutcome("pending", None, None, None, None, None, 0)
        outcome = body.get("outcome")
        if state not in ("reading", "no_reading") or not isinstance(outcome, Mapping):
            return _unavailable("expired" if state in ("cancelled", "expired") else "camera_unreachable")
        origin = _origin(outcome.get("origin_mode"))
        if origin is None or isinstance(uncertainty, bool) or not isinstance(uncertainty, int):
            return _unavailable("camera_unreachable")
        if state == "no_reading":
            reason = outcome.get("reason")
            if not isinstance(reason, str):
                return _unavailable("camera_unreachable")
            return ReferenceOutcome("no_reading", None, None, reason, origin, uncertainty, 0)
        reading = self._reading(outcome, origin, uncertainty)
        if isinstance(reading, ReferenceOutcome):
            return reading
        observation_id = self._persisted(ticket_id, reading)
        if observation_id == "":
            return _unavailable("camera_record_failed")
        return ReferenceOutcome(
            "reading", observation_id, reading.value, None, origin, uncertainty, reading.views
        )

    def _reading(
        self, outcome: Mapping[str, object], origin: OriginMode, uncertainty: int
    ) -> _Reading | ReferenceOutcome:
        selected, views, ended = outcome.get("selected"), outcome.get("views"), outcome.get("ended_at")
        code = outcome.get("capability_code")
        if not isinstance(selected, Mapping) or not isinstance(views, list) or not isinstance(ended, str):
            return _unavailable("camera_unreachable")
        if not isinstance(code, str):
            return _unavailable("camera_unreachable")
        if self._units is None:
            return _unavailable("units_unavailable")
        try:
            value = Decimal(str(selected.get("value")))
            if not value.is_finite():  # rule 5: never NaN or infinity
                return _unavailable("camera_unreachable")
            quantity = Quantity(value, self._units.parse(str(selected.get("unit"))))
            observed = datetime.fromisoformat(ended)
        except InvalidOperation, ProblemError, ValueError:
            return _unavailable("camera_unreachable")
        if observed.tzinfo is None:
            return _unavailable("camera_unreachable")
        name, rule = selected.get("camera_name"), outcome.get("quality_rule")
        return _Reading(
            quantity,
            origin,
            uncertainty,
            len(views),
            code,
            str(name),
            observed,
            rule if isinstance(rule, str) else "",
        )

    def _ticket(self, ticket_id: str) -> Ticket | None:
        with self._snapshots.snapshot() as view:
            return self._store.get(view.connection, ticket_id)

    def _persisted(self, ticket_id: str, reading: _Reading) -> str | None:
        """None for a drill; the observation ID of a persisted reading, recorded once; "" when it cannot be."""
        ticket = self._ticket(ticket_id)
        if ticket is None or not ticket.persist:
            return None
        observation_id = ticket.observation_id
        if observation_id is None:
            observation_id = self._record(ticket, reading)
            if observation_id is None:
                return ""
        self._link.cancel(
            ticket_id, timeout_ms=self._limits.timeout_ms, recorded_observation_id=observation_id
        )
        return observation_id

    def _record(self, ticket: Ticket, reading: _Reading) -> str | None:
        commands = self._observations
        if commands is None:
            return None
        try:
            with self._uows.begin(actor=ENGINE, correlation_id=self._ids.new("cor")) as uow:
                draft = CameraObservationDraft(
                    patient_id=ticket.record_id,
                    operator_id=self._adapter_id,
                    episode_id=None,
                    code=reading.code,
                    value=reading.value,
                    observed_at=reading.observed_at,
                    entered_at=uow.now,
                    origin_mode=reading.origin,
                    acquisition_method="camera_estimate",
                    quality_status="accepted",
                    quality_rule_version=reading.quality_rule,
                    body_region=None,
                    context={"camera_name": reading.camera_name, "camera_selection": "selected"},
                )
                observation_id = commands.append_observation(uow, draft, idempotency_key=ticket.ticket_id)
                self._store.recorded(uow.connection, ticket.ticket_id, observation_id, at=uow.now)
        except ProblemError:
            return None
        return observation_id


def _origin(value: object) -> OriginMode | None:
    return next((o for o in _ORIGINS if o == value), None)


def _unavailable(reason: str) -> ReferenceOutcome:
    return ReferenceOutcome("unavailable", None, None, reason, None, None, 0)
