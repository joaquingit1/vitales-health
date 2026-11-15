"""CameraReferencePort for E30's teach and check: bounded by the caller's wait, never raising, nothing fabricated,
and a persisted reading recorded once and never discarded (A7, E30 X8, Q8; B1, M8, m2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from camera_harness import START, ScriptedTransport, Step, camera_store, conforms, describe, reply

from crew_health.camera.adapters.repository import SqliteReferenceStore
from crew_health.camera.application.link import WorkerLink
from crew_health.camera.application.ports import (
    CameraObservationDraft,
    ReferenceWindowRequest,
    WorkerReply,
    WorkerUnreachable,
)
from crew_health.camera.application.reference import CameraReference, ReferenceLimits
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryUnits
from crew_health.kernel.units import Unit, UnitRegistry
from crew_health.kernel.uow import UnitOfWork

pytestmark = pytest.mark.trace("A7", "E30-X8", "R09", "V05", "V06", "V11", "V13", "AGENTS-1.7")

UNITS = MemoryUnits({"/min": ("frequency", Decimal(1), Decimal(0))})


def window(**overrides: object) -> ReferenceWindowRequest:
    values: dict[str, object] = {
        "guided_check_id": "gck_1",
        "attempt_no": 1,
        "record_id": "mara",
        "capability_code": "respiratory_rate",
        "window_start": START + timedelta(seconds=5),
        "window_end": START + timedelta(seconds=35),
        "required_origin": "LIVE",
        "persist": False,
        "ticket_ttl": timedelta(minutes=2),
    }
    values.update(overrides)
    return ReferenceWindowRequest(**values)  # type: ignore[arg-type]


@dataclass
class Records:
    """ObservationCommands double (E01 records): append is idempotent per key; `fail` makes the next append fail."""

    drafts: dict[str, CameraObservationDraft] = field(default_factory=dict)
    fail: bool = False

    def canonical_unit(self, code: str) -> Unit:
        return Unit("/min")

    def append_observation(
        self, uow: UnitOfWork, draft: CameraObservationDraft, *, idempotency_key: str
    ) -> str:
        if self.fail:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "storage")
        self.drafts.setdefault(idempotency_key, draft)
        return "obs_" + str(list(self.drafts).index(idempotency_key) + 1)


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        steps: list[Step],
        units: UnitRegistry | None = UNITS,
        records: Records | None = None,
    ) -> None:
        self.store = camera_store(tmp_path)
        self.clock, self.ids = self.store.clock, self.store.ids
        self.transport = ScriptedTransport(steps)
        self.link = WorkerLink(self.transport, self.clock, timeout_ms=2000, stale_ms=5000)
        self.pauses: list[float] = []
        self.records = records if records is not None else Records()
        self.reference = CameraReference(
            link=self.link,
            clock=self.clock,
            ids=self.ids,
            units=units,
            limits=ReferenceLimits(timeout_ms=2000, poll_ms=100, max_polls=5, warmup_max_ms=5_000),
            pause=self.pauses.append,
            uows=self.store.uows,
            snapshots=self.store.uows,
            store=SqliteReferenceStore(),
            observations=self.records,
            adapter_id="adp_camera",
        )

    def begin(self, **overrides: object) -> str:
        self.link.probe()
        ticket = self.reference.begin_window(window(**overrides), timeout=timedelta(seconds=1))
        assert ticket.ticket_id is not None, ticket
        return ticket.ticket_id


def status(
    state: str, outcome: dict[str, object] | None = None, uncertainty: int = 12, *, odd: bool = False
) -> WorkerReply:
    body = {
        "acquisition_id": "crf_1",
        "receipt_id": "rcp_1",
        "kind": "reference",
        "state": state,
        "outcome": outcome,
        "delivery_code": None,
        "clock_uncertainty_ms": uncertainty,
    }
    if not odd:
        conforms(
            "StatusReply", body
        )  # well formed replies match the worker's schema; odd ones are malformed on purpose
    return reply(200, body)


def outcome(kind: str = "reading", origin: str = "LIVE", **extra: object) -> dict[str, object]:
    view = {
        "device_id": "camera_2",
        "camera_name": "Galley camera",
        "value": "14",
        "unit": "/min",
        "selected": True,
    }
    base: dict[str, object] = {
        "kind": kind,
        "capability_code": "respiratory_rate",
        "origin_mode": origin,
        "selected": view if kind == "reading" else None,
        "views": [view, {**view, "device_id": "camera_1", "selected": False}] if kind == "reading" else [],
        "reason": None if kind == "reading" else "shoulders_not_visible",
        "reason_text": None if kind == "reading" else "shoulders not visible",
        "started_at": "2026-11-14T09:00:05.000Z",
        "ended_at": "2026-11-14T09:00:35.000Z",
        "selection_reason": None,
        "disagree": False,
        "spread": None,
        "refused": [],
        "quality_rule": "camera-quality@1",
    }
    base.update(extra)
    return base


def test_begin_window_asks_the_worker_in_its_own_clock_for_the_required_origin(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [describe(at=START + timedelta(milliseconds=300), origin="LIVE"), reply(202, {})])
    ticket = rig.begin()
    assert ticket == rig.ids.derived("crf", "gck_1", "1", "respiratory_rate")
    _, path, body, timeout = rig.transport.calls[-1]
    assert path == "/v1/acquisitions" and timeout == 1.0
    assert body == {
        "acquisition_id": ticket,
        "kind": "reference",
        "capability_code": "respiratory_rate",
        "patient_id": "mara",
        "device_ids": ["camera_1", "camera_2"],
        "required_origin": "LIVE",
        "persist": False,
        "ttl_ms": 120_000,
        "clock_offset_ms": 300,
        "clock_uncertainty_ms": 0,
        "warmup_max_ms": 5_000,  # workers.camera.warmup_max_ms, so the window never holds an exposure ramp
        "window_start": "2026-11-14T09:00:05.000Z",
        "window_end": "2026-11-14T09:00:35.000Z",
    }


@pytest.mark.parametrize(
    ("steps", "request_", "reason"),
    [
        ([describe(origin="LIVE")], window(window_end=START), "invalid_window"),
        ([describe(origin="LIVE")], window(ticket_ttl=timedelta(0)), "invalid_window"),
        ([], window(), "camera_unavailable"),
        ([describe(origin="LIVE", cameras=0)], window(), "camera_unavailable"),
        ([describe(origin="LIVE", codes=("pulse",))], window(), "capability_not_supported"),
        ([describe(origin="SIM")], window(), "camera_origin_mismatch"),
        ([describe(origin="LIVE", subject="nadia")], window(), "camera_not_paired"),
        (
            [describe(origin="LIVE"), reply(409, {"code": "NO_CAMERA", "detail": "x"})],
            window(),
            "camera_no_camera",
        ),
        ([describe(origin="LIVE"), WorkerUnreachable("timeout")], window(), "camera_unreachable"),
    ],
)
def test_begin_window_never_raises_and_names_why_no_ticket(
    tmp_path: Path, steps: list[Step], request_: ReferenceWindowRequest, reason: str
) -> None:
    rig = Rig(tmp_path, steps)
    rig.link.probe()
    ticket = rig.reference.begin_window(request_, timeout=timedelta(seconds=1))
    assert (ticket.ticket_id, ticket.unavailable_reason) == (None, reason)


@pytest.mark.trace("M8", "Q8")
def test_a_persisted_window_is_refused_with_a_reason_when_nothing_can_record_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [describe(origin="LIVE")])
    rig.reference = CameraReference(
        link=rig.link, clock=rig.clock, ids=rig.ids, units=UNITS,
        limits=ReferenceLimits(timeout_ms=2000, poll_ms=100, max_polls=5, warmup_max_ms=5_000), pause=rig.pauses.append,
        uows=rig.store.uows, snapshots=rig.store.uows, store=SqliteReferenceStore(), observations=None,
        adapter_id="adp_camera",
    )  # fmt: skip
    rig.link.probe()
    ticket = rig.reference.begin_window(window(persist=True), timeout=timedelta(seconds=1))
    assert (ticket.ticket_id, ticket.unavailable_reason) == (None, "camera_record_unavailable")


def test_a_reading_carries_value_unit_origin_and_clock_uncertainty(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path, [describe(origin="LIVE"), reply(202, {}), status("capturing"), status("reading", outcome())]
    )
    ticket = rig.begin()
    result = rig.reference.outcome(ticket, wait=timedelta(seconds=1))
    assert result.kind == "reading" and result.value is not None
    assert (result.value.value, result.value.unit.code) == (Decimal("14"), "/min")
    assert (result.origin_mode, result.clock_uncertainty_ms, result.cameras, result.observation_id) == (
        "LIVE",
        12,
        2,
        None,  # a drill (persist false) records nothing
    )
    assert rig.pauses == [0.1] and rig.records.drafts == {}


@pytest.mark.trace("M8", "Q8", "AGENTS-6.safety")
def test_a_persisted_reading_is_recorded_once_then_released_on_the_worker(tmp_path: Path) -> None:
    reading = status("reading", outcome())
    rig = Rig(
        tmp_path, [describe(origin="LIVE"), reply(202, {}), reading, reply(200, {}), reading, reply(200, {})]
    )
    ticket = rig.begin(persist=True)
    first = rig.reference.outcome(ticket, wait=timedelta(seconds=1))
    again = rig.reference.outcome(ticket, wait=timedelta(seconds=1))
    assert first.observation_id == again.observation_id == "obs_1"
    (draft,) = rig.records.drafts.values()
    assert (draft.patient_id, draft.code, draft.origin_mode, draft.acquisition_method) == (
        "mara",
        "respiratory_rate",
        "LIVE",
        "camera_estimate",
    )
    cancels = [body for _, path, body, _ in rig.transport.calls if path.endswith("/cancel")]
    assert cancels == [{"recorded_observation_id": "obs_1"}] * 2


@pytest.mark.trace("M8", "Q8", "AGENTS-6.safety")
def test_abandoning_a_persisted_window_records_its_reading_first_and_never_discards_it(
    tmp_path: Path,
) -> None:
    reading = status("reading", outcome())
    rig = Rig(tmp_path, [describe(origin="LIVE"), reply(202, {}), reading, reading, reply(200, {})])
    ticket = rig.begin(persist=True)
    rig.reference.abandon(ticket, timeout=timedelta(seconds=1))
    assert list(rig.records.drafts) == [ticket]
    cancels = [body for _, path, body, _ in rig.transport.calls if path.endswith("/cancel")]
    assert cancels == [{"recorded_observation_id": "obs_1"}]


@pytest.mark.trace("M8", "AGENTS-6.storage")
def test_a_reading_that_cannot_be_recorded_is_not_shown_and_stays_on_the_worker(tmp_path: Path) -> None:
    reading = status("reading", outcome())
    rig = Rig(tmp_path, [describe(origin="LIVE"), reply(202, {}), reading], records=Records(fail=True))
    ticket = rig.begin(persist=True)
    result = rig.reference.outcome(ticket, wait=timedelta(seconds=1))
    assert (result.kind, result.value, result.reason) == ("unavailable", None, "camera_record_failed")
    assert not any(path.endswith("/cancel") for _, path, _, _ in rig.transport.calls)


def test_no_reading_keeps_the_quality_reason_and_never_a_value(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [status("no_reading", outcome("no_reading"))])
    result = rig.reference.outcome("crf_1", wait=timedelta(milliseconds=500))
    assert (result.kind, result.value, result.reason, result.origin_mode) == (
        "no_reading",
        None,
        "shoulders_not_visible",
        "LIVE",
    )


def test_polling_is_bounded_by_max_polls_and_stays_pending(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [status("capturing")] * 10)
    result = rig.reference.outcome("crf_1", wait=timedelta(seconds=60))
    assert result.kind == "pending"
    assert len(rig.transport.calls) == 5 and len(rig.pauses) == 4


@pytest.mark.trace("m2", "AGENTS-3.2")
@pytest.mark.parametrize("wait_ms", [0, 50, 500, 3000])
def test_an_outcome_never_takes_longer_than_the_callers_wait_even_against_a_slow_worker(
    tmp_path: Path, wait_ms: int
) -> None:
    rig = Rig(tmp_path, [], records=None)
    rig.transport.fallback = status("capturing")
    rig.reference = CameraReference(
        link=rig.link, clock=rig.clock, ids=rig.ids, units=UNITS,
        limits=ReferenceLimits(timeout_ms=2000, poll_ms=50, max_polls=1000, warmup_max_ms=5_000), pause=rig.pauses.append,
        uows=rig.store.uows, snapshots=rig.store.uows, store=SqliteReferenceStore(), observations=None,
        adapter_id="adp_camera",
    )  # fmt: skip
    rig.reference.outcome("crf_1", wait=timedelta(milliseconds=wait_ms))
    worst_case_s = sum(t for *_, t in rig.transport.calls) + sum(rig.pauses)
    assert worst_case_s <= wait_ms / 1000


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (reply(404, {"code": "NOT_FOUND"}), "expired"),
        (status("cancelled"), "expired"),
        (status("expired"), "expired"),
        (WorkerUnreachable("down"), "camera_unreachable"),
        (status("reading", outcome(origin="DEMO"), odd=True), "camera_unreachable"),
        (
            status("reading", outcome(value="x", selected={"value": "NaN", "unit": "/min"}), odd=True),
            "camera_unreachable",
        ),
        (
            status("reading", outcome(selected={"value": "14", "unit": "beats"}), odd=True),
            "camera_unreachable",
        ),
        (status("reading", outcome(selected=None)), "camera_unreachable"),
        (status("reading", outcome(ended_at="yesterday")), "camera_unreachable"),
        (status("no_reading", outcome("no_reading", reason=None), odd=True), "camera_unreachable"),
        (status("delivered", outcome()), "camera_unreachable"),
        (
            reply(200, {"state": "reading", "outcome": outcome(), "clock_uncertainty_ms": "12"}),
            "camera_unreachable",
        ),
    ],
)
def test_anything_unexpected_is_unavailable_never_a_value(
    tmp_path: Path, answer: WorkerReply | Exception, reason: str
) -> None:
    rig = Rig(tmp_path, [answer])
    result = rig.reference.outcome("crf_1", wait=timedelta(milliseconds=500))
    assert (result.kind, result.value, result.reason) == ("unavailable", None, reason)


def test_without_a_unit_registry_no_value_crosses(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [status("reading", outcome())], units=None)
    result = rig.reference.outcome("crf_1", wait=timedelta(milliseconds=500))
    assert (result.kind, result.reason) == ("unavailable", "units_unavailable")


def test_abandon_cancels_on_the_worker_and_never_raises(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [WorkerUnreachable("down")])
    rig.reference.abandon("crf_1", timeout=timedelta(milliseconds=250))
    method, path, body, timeout = rig.transport.calls[-1]
    assert (method, path, body, timeout) == ("POST", "/v1/acquisitions/crf_1/cancel", {}, 0.25)
