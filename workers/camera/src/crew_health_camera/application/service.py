"""The camera worker's acquisitions: start, status, cancel, capture and delivery (hub to worker protocol, A7).

Start is idempotent on the acquisition ID: a start the worker already holds is answered from its table before any
other check, the same start returns the same receipt (the clock probe is not part of a start's identity), and
another start under the same ID is a conflict. Every start names its cameras explicitly and each of them must be
paired with the start's subject by the device configuration (PLAN Pairing, R29): no camera is ever implied. A start
whose required origin is not this worker's origin is refused before capture (AGENTS rule 2).

One runner captures at a time, oldest due first; a reference window that opens later never blocks a measurement. A
measurement's result goes to the hub as one recordAttempt with a key derived from the acquisition. The attempt is
saved in the evidence store before its first delivery and retried forever with capped exponential backoff, so a hub
outage or a worker restart never loses it (AGENTS 6). A table full of undelivered evidence refuses new starts as busy
with that reason, never silently. Cancel stops a capture in flight; a decided reference reading the hub asked to
persist is kept until the hub has it (PLAN deviation 4), while an unpersisted drill's reading is discarded.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta

from crew_health_camera.application.attempt import attempt_body
from crew_health_camera.application.model import (
    TERMINAL,
    AcquisitionRefused,
    Outcome,
    StartSpec,
    State,
    StatusView,
    WorkerBounds,
)
from crew_health_camera.application.ports import (
    Camera,
    CameraNotSettled,
    CaptureFailed,
    EvidenceStore,
    EvidenceUnreadable,
    FrameSource,
    HubClient,
    WorkerClock,
)
from crew_health_camera.domain.analysis import Decision, NoReading, decide
from crew_health_camera.domain.pairing import Pairing, admit
from crew_health_camera.domain.policy import QualityPolicy
from crew_health_camera.domain.selection import Refusal, select

_DROPPABLE: frozenset[str] = frozenset({"delivered", "rejected", "cancelled", "expired"})


@dataclass
class _Record:
    spec: StartSpec  # as accepted (cameras validated); `requested` is the start as the hub sent it
    requested: StartSpec
    receipt_id: str
    state: State
    deadline_ns: int
    outcome: Outcome | None = None
    body: dict[str, object] | None = None
    delivery_code: str | None = None
    tries: int = 0
    next_try_ns: int = 0
    revoked: bool = False

    def view(self) -> StatusView:
        s = self.spec
        return StatusView(
            s.acquisition_id, self.receipt_id, s.kind, s.capability_code, self.state, self.outcome, self.delivery_code,
            s.clock_uncertainty_ms,
        )  # fmt: skip


class AcquisitionService:
    def __init__(
        self,
        *,
        policy: QualityPolicy,
        source: FrameSource,
        clock: WorkerClock,
        hub: HubClient,
        bounds: WorkerBounds,
        receipt_ids: Callable[[], str],
        evidence: EvidenceStore,
    ) -> None:
        self.policy, self.source, self.clock, self.hub, self.bounds = policy, source, clock, hub, bounds
        self._receipts, self._evidence = receipt_ids, evidence
        self._lock = threading.Lock()
        self._table: dict[str, _Record] = {}
        for document in evidence.pending():
            record = _restored(document, clock.monotonic_ns())
            self._table[record.spec.acquisition_id] = record

    def cameras(self) -> tuple[Camera, ...]:
        return self.source.cameras()[: self.bounds.max_devices]

    # ------------------------------------------------------------------------------------------- protocol calls

    def start(self, spec: StartSpec) -> StatusView:
        with self._lock:
            existing = self._table.get(spec.acquisition_id)
            if existing is not None:  # a retry is answered before any check that may have changed since (m8)
                if existing.requested.identity() != spec.identity():
                    raise AcquisitionRefused("CONFLICT", "acquisition_id_reused")
                return existing.view()
        accepted = self._validated(spec)
        with self._lock:
            existing = self._table.get(spec.acquisition_id)
            if existing is not None:
                if existing.requested.identity() != spec.identity():
                    raise AcquisitionRefused("CONFLICT", "acquisition_id_reused")
                return existing.view()
            self._make_room()
            deadline = self.clock.monotonic_ns() + spec.ttl_ms * 1_000_000
            record = _Record(accepted, spec, self._receipts(), "accepted", deadline)
            self._table[spec.acquisition_id] = record
            return record.view()

    def status(self, acquisition_id: str) -> StatusView | None:
        with self._lock:
            record = self._table.get(acquisition_id)
            return None if record is None else record.view()

    def cancel(self, acquisition_id: str, *, recorded_observation_id: str | None = None) -> str:
        """Stop an acquisition. A persisted reference reading is released only by a cancel that names the
        observation the hub recorded it as (M8): any other cancel answers cannot_stop and the reading stays."""
        with self._lock:
            record = self._table.get(acquisition_id)
            if record is None:
                return "unknown"
            decided = record.spec.kind == "reference" and record.state in ("reading", "no_reading")
            if (
                decided
                and record.spec.persist
                and record.state == "reading"
                and recorded_observation_id is None
            ):
                return "cannot_stop"
            if record.state == "accepted" or decided:
                record.state, record.outcome, record.revoked = "cancelled", None, True
                return "stopped"
            if record.state == "capturing":
                record.revoked = True
                return "stopped"
            return "cannot_stop"

    # ------------------------------------------------------------------------------------------- runner

    def run_next(self) -> bool:
        """Capture and decide the oldest due acquisition; False when none is due. A reference window that has not
        opened yet is not due, so it never holds the runner while measurements wait (m12)."""
        with self._lock:
            record = next(
                (r for r in self._table.values() if r.state == "accepted" and self._due(r.spec)), None
            )
            if record is None:
                return False
            record.state = "capturing"
        outcome = self._acquire(record)
        with self._lock:
            if record.revoked and (record.spec.kind == "reference" or outcome is None):
                record.state, record.outcome = "cancelled", None
                return True
            record.outcome = outcome
            if record.spec.kind == "reference" or outcome is None:
                record.state = (
                    "no_reading" if outcome is None or isinstance(outcome.result, Refusal) else "reading"
                )
                return True
            record.body = attempt_body(record.spec, outcome, self.policy)
            record.state = "delivering"
            self._evidence.save(record.spec.acquisition_id, _journal(record))
        self.deliver_pending()
        return True

    def deliver_pending(self) -> int:
        """Send every undelivered attempt whose backoff has elapsed, with its stable key; returns how many were sent.
        An unreachable hub schedules the next try after a capped exponential delay; nothing gives up."""
        now = self.clock.monotonic_ns()
        with self._lock:
            due = [r for r in self._table.values() if r.state == "delivering" and r.next_try_ns <= now]
        for record in due:
            spec, body = record.spec, record.body
            if spec.request_id is None or body is None:
                continue
            delivery = self.hub.record_attempt(
                spec.request_id, body, idempotency_key=spec.acquisition_id + ".attempt"
            )
            with self._lock:
                record.tries += 1
                record.delivery_code = delivery.code if delivery.kind != "accepted" else None
                if delivery.kind == "accepted":
                    record.state = "delivered"
                    self._evidence.delivered(spec.acquisition_id)
                elif delivery.kind == "rejected":
                    record.state = "rejected"
                    self._evidence.rejected(spec.acquisition_id)
                else:
                    record.delivery_code = record.delivery_code or "unreachable"
                    delay = self.bounds.delivery_delay_ms(record.tries) * 1_000_000
                    record.next_try_ns = self.clock.monotonic_ns() + delay
        return len(due)

    def expire(self) -> int:
        """Reference windows and never-started work past their lifetime; undelivered evidence is kept. The hub
        reconciles an expired measurement into "Not measured" with its reason."""
        now = self.clock.monotonic_ns()
        count = 0
        with self._lock:
            for record in self._table.values():
                if record.deadline_ns >= now or record.state in TERMINAL - {"reading", "no_reading"}:
                    continue
                if record.spec.kind == "reference" and record.spec.persist and record.state == "reading":
                    continue  # a persisted reading waits for the hub, never for a timer
                if (
                    record.state == "accepted"
                    or record.spec.kind == "reference"
                    and record.state != "capturing"
                ):
                    record.state, record.outcome = "expired", None
                    count += 1
        return count

    # ------------------------------------------------------------------------------------------- internals

    def _validated(self, spec: StartSpec) -> StartSpec:
        if not 1 <= spec.ttl_ms <= self.bounds.max_ttl_ms:
            raise AcquisitionRefused("INVALID", "ttl_ms")
        if self.policy.rules_for(spec.capability_code) is None:
            raise AcquisitionRefused("UNSUPPORTED", "capability_code")
        if spec.patient_id is None:
            raise AcquisitionRefused("INVALID", "patient_id")
        if spec.kind == "measurement" and (
            spec.request_id is None or spec.sequence is None or spec.sequence < 1
        ):
            raise AcquisitionRefused("INVALID", "measurement_identity")
        if spec.kind == "reference" and (
            spec.window_start is None or spec.window_end is None or spec.window_end <= spec.window_start
        ):
            raise AcquisitionRefused("INVALID", "window")
        if spec.required_origin is None:
            raise AcquisitionRefused("INVALID", "required_origin")
        if spec.required_origin != self.source.origin_mode:
            raise AcquisitionRefused("ORIGIN_MISMATCH", "required_origin")
        if not spec.device_ids:
            raise AcquisitionRefused("NOT_PAIRED", "device_ids_required")
        known = {c.device_id: c for c in self.cameras()}
        if not known:
            raise AcquisitionRefused("NO_CAMERA", "no_camera")
        devices = tuple(sorted(set(spec.device_ids)))
        if len(devices) > self.bounds.max_devices or any(d not in known for d in devices):
            raise AcquisitionRefused("NO_CAMERA", "device_ids")
        if any(known[d].subject_id != spec.patient_id for d in devices):
            raise AcquisitionRefused("NOT_PAIRED", "device_ids")
        return replace(spec, device_ids=devices)

    def _make_room(self) -> None:
        while len(self._table) >= self.bounds.max_acquisitions:
            oldest = next((k for k, r in self._table.items() if r.state in _DROPPABLE), None)
            if oldest is None:
                undelivered = any(r.state == "delivering" for r in self._table.values())
                raise AcquisitionRefused(
                    "BUSY", "undelivered_evidence" if undelivered else "max_acquisitions"
                )
            del self._table[oldest]

    def _due(self, spec: StartSpec) -> bool:
        window = self._window_ns(spec)
        return window is None or window[0] <= self.clock.monotonic_ns()

    def _window_ns(self, spec: StartSpec) -> tuple[int, int] | None:
        """Monotonic start and duration of the capture; None when a reference window has already ended."""
        now_ns, now_utc = self.clock.monotonic_ns(), self.clock.utc_now()
        rules = self.policy.rules_for(spec.capability_code)
        if spec.window_start is None or spec.window_end is None:
            frame_ns = round(2e9 / self.policy.frames.min_rate_hz)
            window_ns = 0 if rules is None else round(rules.window_s * 1e9)
            return now_ns, window_ns + frame_ns
        offset = timedelta(milliseconds=spec.clock_offset_ms)
        start = _ns(spec.window_start + offset - now_utc) + now_ns
        end = _ns(spec.window_end + offset - now_utc) + now_ns
        if end <= now_ns:
            return None
        start = max(start, now_ns)
        return start, end - start

    def _acquire(self, record: _Record) -> Outcome | None:
        spec = record.spec
        origin = self.source.origin_mode
        names = tuple((c.device_id, c.name) for c in self.cameras())
        now = self.clock.utc_now() - timedelta(milliseconds=spec.clock_offset_ms)
        if spec.required_origin != origin:  # the source changed since the start was accepted
            return Outcome(Refusal(spec.capability_code, "origin_mismatch", ()), now, now, origin, names)
        window = self._window_ns(spec)
        if window is None:
            return Outcome(Refusal(spec.capability_code, "window_missed", ()), now, now, origin, names)
        stop = lambda: record.revoked  # noqa: E731
        self.clock.wait_until_ns(window[0], stop)
        anchor_utc, anchor_ns = self.clock.utc_now(), self.clock.monotonic_ns()
        try:
            capture = self.source.capture(
                spec.device_ids,
                duration_ns=window[1],
                max_frames=self.bounds.max_frames,
                warmup_max_ns=spec.warmup_max_ms * 1_000_000,
                should_stop=stop,
            )
        except CameraNotSettled:
            return Outcome(Refusal(spec.capability_code, "camera_not_settled", ()), now, now, origin, names)
        except CaptureFailed:
            refusal = Refusal(spec.capability_code, "capture_failed", ())
            return Outcome(refusal, now, now, origin, names)
        if record.revoked:
            return None

        def hub_time(ns: int) -> datetime:
            return anchor_utc + timedelta(microseconds=(ns - anchor_ns) // 1000 - spec.clock_offset_ms * 1000)

        pairing = Pairing(
            spec.acquisition_id, spec.patient_id or "", frozenset(spec.device_ids), capture.segment,
            record.deadline_ns, record.revoked,
        )  # fmt: skip
        admitted = admit(pairing, capture.frames)
        decisions: list[Decision] = []
        for device in spec.device_ids:
            reason = admitted.reason_for(device)
            frames = admitted.frames.get(device, ())
            decisions.append(
                NoReading(spec.capability_code, device, reason)
                if reason is not None
                else decide(spec.capability_code, device, frames, self.policy)
            )
        result = select(spec.capability_code, decisions, self.policy.agreement_tolerance_per_min)
        return Outcome(result, hub_time(capture.started_ns), hub_time(capture.ended_ns), origin, names)


def _ns(delta: timedelta) -> int:
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def _journal(record: _Record) -> dict[str, object]:
    spec = asdict(record.spec)
    for key in ("window_start", "window_end"):
        value = spec[key]
        spec[key] = None if value is None else value.isoformat()
    spec["device_ids"] = list(record.spec.device_ids)
    return {"spec": spec, "receipt_id": record.receipt_id, "body": record.body}


def _restored(document: Mapping[str, object], now_ns: int) -> _Record:
    """An undelivered attempt saved before a restart. One this worker cannot read raises: it stays on disk for an
    operator and the worker does not start without it."""
    spec, receipt, body = document.get("spec"), document.get("receipt_id"), document.get("body")
    if not isinstance(spec, Mapping) or not isinstance(receipt, str) or not isinstance(body, dict):
        raise EvidenceUnreadable("saved attempt shape")
    try:
        values = dict(spec)
        values["device_ids"] = tuple(str(d) for d in values["device_ids"])
        for key in ("window_start", "window_end"):
            values[key] = None if values[key] is None else datetime.fromisoformat(str(values[key]))
        restored = StartSpec(**values)
    except (KeyError, TypeError, ValueError) as error:
        raise EvidenceUnreadable("saved attempt fields") from error
    return _Record(restored, restored, receipt, "delivering", now_ns, body=body)
