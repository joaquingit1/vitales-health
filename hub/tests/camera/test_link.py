"""The hub's side of the worker protocol: probe, clock offset, staleness and reply mapping (A7, SYSTEM 3)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from camera_harness import START, ScriptedTransport, TickingClock, describe, reply

from crew_health.camera.application.link import WorkerLink
from crew_health.camera.application.ports import WorkerReply, WorkerUnreachable
from crew_health.kernel.testing import MemoryClock

pytestmark = pytest.mark.trace("A7", "R29", "V11", "V16", "AGENTS-6.bounds")


def link_with(transport: ScriptedTransport, step_ms: int = 0) -> tuple[WorkerLink, TickingClock]:
    clock = TickingClock(MemoryClock(START, 0, "epoch_1"), timedelta(milliseconds=step_ms))
    return WorkerLink(transport, clock, timeout_ms=2000, stale_ms=5000), clock


def test_probe_estimates_offset_and_uncertainty_from_the_round_trip() -> None:
    # Hub sends at START and receives at START + 40 ms; the worker read its clock 1.5 s ahead of the midpoint.
    worker_utc = START + timedelta(milliseconds=20) + timedelta(milliseconds=1500)
    link, _ = link_with(ScriptedTransport([describe(at=worker_utc)]), step_ms=40)
    probe = link.probe()
    assert probe is not None
    assert (probe.offset_ms, probe.uncertainty_ms) == (1500, 20)
    assert (probe.adapter_id, probe.origin_mode, probe.camera_count) == ("adp_camera", "SIM", 2)
    assert probe.ready_methods == frozenset({"camera_estimate"})
    assert probe.at == START + timedelta(milliseconds=40)


@pytest.mark.parametrize(
    "answer",
    [
        describe(version="2"),
        describe(origin="DEMO"),
        reply(200, {"protocol_version": "1"}),
        reply(200, ["not", "an", "object"]),
        WorkerReply(200, b"not json"),
        reply(503, {"code": "BUSY"}),
        WorkerUnreachable("refused"),
        TimeoutError("slow"),
    ],
)
def test_a_bad_or_failed_describe_leaves_readiness_unknown(answer: WorkerReply | Exception) -> None:
    link, clock = link_with(ScriptedTransport([describe(), answer]))
    assert link.probe() is not None
    assert link.probe() is None
    assert link.fresh(clock.clock.at) is None


def test_a_naive_or_garbled_worker_clock_is_refused() -> None:
    naive = reply(200, {**_body(describe()), "worker_utc": "2026-11-14T09:00:00"})
    garbled = reply(200, {**_body(describe()), "worker_utc": "yesterday"})
    link, _ = link_with(ScriptedTransport([naive, garbled]))
    assert link.probe() is None
    assert link.probe() is None


def test_a_probe_older_than_stale_ms_or_from_the_future_is_unknown() -> None:
    link, clock = link_with(ScriptedTransport([describe()]))
    probe = link.probe()
    assert probe is not None
    assert link.fresh(START + timedelta(milliseconds=5000)) is probe
    assert link.fresh(START + timedelta(milliseconds=5001)) is None
    assert link.fresh(START - timedelta(milliseconds=1)) is None  # hub clock moved back: never assume fresh
    assert clock.clock.at == START


def test_replies_map_to_typed_answers_and_each_call_is_bounded() -> None:
    transport = ScriptedTransport(
        [
            reply(202, {"state": "accepted"}),
            reply(404, {"code": "NOT_FOUND"}),
            reply(429, {"code": "BUSY"}),
            reply(409, {"detail": "no code"}),
            reply(500, {"code": "INTERNAL"}),
            WorkerReply(200, b""),
        ]
    )
    link, _ = link_with(transport)
    start = {"acquisition_id": "cad_1", "kind": "measurement", "capability_code": "pulse", "ttl_ms": 1000}
    start["warmup_max_ms"] = 5000
    kinds = [
        link.start(start, timeout_ms=60_000),
        link.status("cad_1", timeout_ms=500),
        link.start(start, timeout_ms=500),
        link.start(start, timeout_ms=500),
        link.status("cad_1", timeout_ms=500),
        link.cancel("cad_1", timeout_ms=500),
    ]
    assert [(a.kind, a.code) for a in kinds] == [
        ("ok", None),
        ("not_found", "NOT_FOUND"),
        ("refused", "BUSY"),
        ("unreachable", None),
        ("unreachable", None),
        ("ok", None),
    ]
    assert transport.calls[0][3] == 2.0  # capped at workers.camera.timeout_ms
    assert transport.calls[1][3] == 0.5
    assert [(m, p) for m, p, _, _ in transport.calls[-2:]] == [
        ("GET", "/v1/acquisitions/cad_1"),
        ("POST", "/v1/acquisitions/cad_1/cancel"),
    ]


def test_window_instants_are_rfc3339_utc_with_milliseconds() -> None:
    assert WorkerLink.window(START, START + timedelta(seconds=30)) == {
        "window_start": "2026-11-14T09:00:00.000Z",
        "window_end": "2026-11-14T09:00:30.000Z",
    }


def _body(answer: WorkerReply) -> dict[str, object]:
    import json

    value = json.loads(answer.body)
    assert isinstance(value, dict)
    return value
