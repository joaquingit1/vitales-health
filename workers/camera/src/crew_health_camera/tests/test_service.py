"""Acquisitions end to end inside the worker: attempt bodies, idempotent start, bounds, cancel, delivery retries and
reference windows (E06-01, E06-02, D08, D09, V07, V08, V11, V13, V16, V35)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from crew_health_camera.adapters.sim_source import Scene
from crew_health_camera.application.model import AcquisitionRefused
from crew_health_camera.application.ports import CameraNotSettled, Delivery, EvidenceUnreadable
from crew_health_camera.tests.support import (
    BOUNDS,
    START,
    STEADY,
    Rig,
    cameras,
    measurement,
    policy,
    reference,
    rig,
)

MOVING = replace(STEADY, head_motion=0.2)


def run_when_due(r: Rig) -> None:
    """The runner loop: a window that has not opened yet is not due, so time passes until it is."""
    for _ in range(600):
        if r.service.run_next():
            return
        r.clock.advance(1)
    raise AssertionError("never due")


@pytest.mark.trace("D08", "V05", "E06-01")
def test_movement_is_a_failed_attempt_with_its_reason_and_no_value() -> None:
    r = rig({"camera_1": MOVING})
    r.service.start(measurement())
    assert r.service.run_next()
    ((request_id, body, key),) = r.hub.calls
    assert (request_id, key) == ("mrq_1", "acq_1.attempt")
    assert body["outcome"] == "failed" and body["failure_reason"] == "too much movement"
    assert body["observations"] == [] and body["sequence"] == 1
    assert body["quality_notes"] == ["too much movement", "Camera 1: too much movement"]
    view = r.service.status("acq_1")
    assert view is not None and view.state == "delivered"


@pytest.mark.trace("D09", "R11", "V07", "E06-02")
def test_two_cameras_keep_both_values_the_choice_and_the_disagreement() -> None:
    r = rig({"camera_1": STEADY, "camera_2": replace(STEADY, pulse_hz=1.8, noise=0.2)})
    r.service.start(measurement(devices=("camera_1", "camera_2")))
    r.service.run_next()
    body = r.hub.calls[0][1]
    assert body["outcome"] == "success"
    first, second = body["observations"]
    assert (first["device_id"], first["value"], first["context"]["camera_selection"]) == (
        "camera_1",
        96,
        "selected",
    )
    assert (second["device_id"], second["value"], second["context"]["camera_selection"]) == (
        "camera_2",
        108,
        "also_qualified",
    )
    assert first["context"]["views_disagree"] == "true" and first["context"]["views_spread"] == "12 /min"
    assert first["origin_mode"] == "SIM" and first["acquisition_method"] == "camera_estimate"
    assert first["unit"] == "/min" and first["patient_id"] == "person_a"
    assert body["quality_notes"] == [
        "Camera 1 selected: best signal of 2 usable views",
        "views disagree by 12 /min",
    ]


@pytest.mark.trace("R29", "V08", "E06-02")
def test_only_the_paired_camera_is_analysed() -> None:
    r = rig({"camera_1": STEADY, "camera_2": replace(STEADY, pulse_hz=1.2)})
    r.service.start(measurement(devices=("camera_2",)))
    r.service.run_next()
    (observation,) = r.hub.calls[0][1]["observations"]
    assert observation["device_id"] == "camera_2" and observation["value"] == 72


@pytest.mark.trace("R29", "V08", "E06-02", "B1")
def test_a_start_names_its_cameras_and_each_must_be_paired_with_the_subject() -> None:
    r = rig({"camera_1": STEADY, "camera_2": replace(STEADY, pulse_hz=1.2)})
    with pytest.raises(AcquisitionRefused) as empty:
        r.service.start(measurement(devices=()))  # never expanded to every camera on the worker
    assert (empty.value.code, empty.value.detail) == ("NOT_PAIRED", "device_ids_required")
    other = rig({"camera_1": STEADY})
    other.source._cameras = cameras("camera_1", subject="someone_else")
    with pytest.raises(AcquisitionRefused) as foreign:
        other.service.start(measurement())
    assert foreign.value.code == "NOT_PAIRED"
    assert r.hub.calls == [] and other.hub.calls == [] and not r.service.run_next()


@pytest.mark.trace("R29", "V08", "V39", "E06-02")
def test_two_faces_in_view_create_no_value() -> None:
    r = rig({"camera_1": replace(STEADY, faces=2)})
    r.service.start(measurement())
    r.service.run_next()
    body = r.hub.calls[0][1]
    assert body["outcome"] == "failed" and body["failure_reason"] == "more than one face in view"
    assert body["observations"] == []


@pytest.mark.trace("V13", "V16")
def test_duplicate_start_returns_the_same_receipt_and_a_changed_body_conflicts() -> None:
    r = rig({"camera_1": STEADY})
    first = r.service.start(measurement())
    assert r.service.start(measurement()) == first
    with pytest.raises(AcquisitionRefused) as refused:
        r.service.start(measurement(sequence=2))
    assert refused.value.code == "CONFLICT"
    r.service.run_next()
    assert not r.service.run_next()
    assert len(r.hub.calls) == 1


@pytest.mark.trace("V16", "M1")
def test_a_retried_start_with_a_fresh_clock_probe_is_the_same_start() -> None:
    r = rig({"camera_1": STEADY})
    first = r.service.start(replace(measurement(), clock_offset_ms=2000, clock_uncertainty_ms=4))
    again = r.service.start(replace(measurement(), clock_offset_ms=2003, clock_uncertainty_ms=7))
    assert again == first
    assert (
        r.service.start(replace(measurement(), warmup_max_ms=9_000)) == first
    )  # a changed hub setting, same start
    r.service.run_next()
    assert r.hub.calls[0][1]["started_at"] == "2026-11-14T08:59:58.000Z"  # the offset accepted first


@pytest.mark.trace("V16", "m8")
def test_a_retried_start_is_answered_before_the_camera_list_is_checked_again() -> None:
    r = rig({"camera_1": STEADY, "camera_2": STEADY})
    spec = measurement(devices=("camera_1", "camera_2"))
    first = r.service.start(spec)
    r.source._cameras = r.source._cameras[:1]  # camera_2 unplugged while the start is queued
    assert r.service.start(spec) == first


@pytest.mark.trace("V11", "AGENTS-6.bounds")
def test_a_full_worker_refuses_before_capture_and_frees_finished_slots() -> None:
    r = rig({"camera_1": STEADY}, bounds=replace(BOUNDS, max_acquisitions=1))
    r.service.start(measurement("acq_1"))
    with pytest.raises(AcquisitionRefused) as busy:
        r.service.start(measurement("acq_2"))
    assert busy.value.code == "BUSY"
    r.service.run_next()
    r.service.start(measurement("acq_2"))
    assert r.service.status("acq_1") is None


@pytest.mark.trace("V11")
@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (measurement(code="spo2"), "UNSUPPORTED"),
        (replace(measurement(), ttl_ms=0), "INVALID"),
        (replace(measurement(), request_id=None), "INVALID"),
        (replace(measurement(), sequence=0), "INVALID"),
        (replace(reference(), window_end=START), "INVALID"),
        (measurement(devices=("camera_9",)), "NO_CAMERA"),
        (measurement(devices=()), "NOT_PAIRED"),
        (measurement(origin="LIVE"), "ORIGIN_MISMATCH"),
        (replace(measurement(), required_origin=None), "INVALID"),
        (replace(measurement(), patient_id=None), "INVALID"),
    ],
)
def test_invalid_starts_are_refused_with_a_typed_code(spec: object, code: str) -> None:
    r = rig({"camera_1": STEADY})
    with pytest.raises(AcquisitionRefused) as refused:
        r.service.start(spec)  # type: ignore[arg-type]
    assert refused.value.code == code


@pytest.mark.trace("V11", "AGENTS-1.3")
def test_no_camera_and_a_failing_camera_never_produce_a_value() -> None:
    with pytest.raises(AcquisitionRefused) as refused:
        rig({}).service.start(measurement())
    assert refused.value.code == "NO_CAMERA"
    r = rig({"camera_1": STEADY}, failing=True)
    r.service.start(measurement())
    r.service.run_next()
    body = r.hub.calls[0][1]
    assert body["outcome"] == "failed" and body["failure_reason"] == "the camera could not capture"


@pytest.mark.trace("V35", "V13", "M3")
def test_an_unreachable_hub_is_retried_with_capped_backoff_and_never_given_up() -> None:
    r = rig({"camera_1": STEADY})
    r.hub.answers = [Delivery("unreachable", "unreachable")] * 6
    r.service.start(measurement())
    r.service.run_next()  # first try fails at once
    assert r.service.deliver_pending() == 0  # not due before its backoff
    waits = []
    for _ in range(6):
        before = len(r.hub.calls)
        while len(r.hub.calls) == before:
            r.clock.advance(0.5)
            r.service.deliver_pending()
        waits.append(r.clock.ns)
    gaps = [round((b - a) / 1e9) for a, b in zip(waits, waits[1:], strict=False)]
    assert gaps == [2, 4, 8, 8, 8]  # 1 s doubling, capped at delivery_backoff_max_ms
    assert {key for _, _, key in r.hub.calls} == {"acq_1.attempt"}
    view = r.service.status("acq_1")
    assert view is not None and view.state == "delivered" and r.evidence.saved == {}
    assert r.service.cancel("acq_1") == "cannot_stop"


@pytest.mark.trace("AGENTS-1.6", "AGENTS-6.safety", "M3")
def test_undelivered_evidence_survives_a_worker_restart_and_a_full_table_refuses_with_its_reason() -> None:
    r = rig({"camera_1": STEADY}, bounds=replace(BOUNDS, max_acquisitions=2))
    r.hub.answers = [Delivery("unreachable", "unreachable")] * 20
    for n in (1, 2):
        r.service.start(measurement(f"acq_{n}", sequence=n))
        r.service.run_next()
    with pytest.raises(AcquisitionRefused) as full:
        r.service.start(measurement("acq_3", sequence=3))
    assert (full.value.code, full.value.detail) == ("BUSY", "undelivered_evidence")
    assert sorted(r.evidence.saved) == ["acq_1", "acq_2"]
    r.hub.answers = []
    restarted = r.restart()  # the hub is back; a new worker process over the same state directory
    view = restarted.status("acq_1")
    assert view is not None and view.state == "delivering"
    assert restarted.deliver_pending() == 2
    assert [c[2] for c in r.hub.calls[-2:]] == ["acq_1.attempt", "acq_2.attempt"]
    assert r.evidence.saved == {}
    restarted.start(measurement("acq_3", sequence=3))


@pytest.mark.trace("AGENTS-1.6", "AGENTS-6.storage")
@pytest.mark.parametrize(
    "damage", [{"spec": {}}, {"receipt_id": 7}, {"spec": {"device_ids": "x"}, "receipt_id": "r", "body": {}}]
)
def test_a_worker_with_unreadable_saved_evidence_refuses_to_start_instead_of_dropping_it(
    damage: dict[str, object],
) -> None:
    r = rig({"camera_1": STEADY})
    r.evidence.saved["acq_9"] = damage
    with pytest.raises(EvidenceUnreadable):
        r.restart()


@pytest.mark.trace("V08", "V09")
def test_a_hub_rejection_is_kept_with_its_code_and_never_resent() -> None:
    r = rig({"camera_1": STEADY})
    r.hub.answers = [Delivery("rejected", "ORIGIN_MISMATCH")]
    r.service.start(measurement())
    r.service.run_next()
    assert r.service.deliver_pending() == 0
    view = r.service.status("acq_1")
    assert view is not None and (view.state, view.delivery_code) == ("rejected", "ORIGIN_MISMATCH")
    assert list(r.evidence.kept_rejected) == ["acq_1"] and r.evidence.saved == {}


@pytest.mark.trace("V16")
def test_cancel_before_capture_stops_and_captures_nothing() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(measurement())
    assert r.service.cancel("acq_1") == "stopped"
    assert not r.service.run_next() and r.hub.calls == []
    assert r.service.cancel("missing") == "unknown"


@pytest.mark.trace("V16", "AGENTS-1.6")
def test_cancel_during_capture_stops_without_an_attempt() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(measurement())
    original = r.source.capture

    def capture_then_cancel(*args: object, **kwargs: object) -> object:
        assert r.service.cancel("acq_1") == "stopped"
        return original(*args, **kwargs)  # type: ignore[arg-type]

    r.source.capture = capture_then_cancel  # type: ignore[method-assign,assignment]
    r.service.run_next()
    view = r.service.status("acq_1")
    assert view is not None and view.state == "cancelled" and r.hub.calls == []


@pytest.mark.trace("E06-01", "AGENTS-1.5")
def test_a_camera_that_never_settles_is_not_measured_with_its_own_reason_and_the_hub_bound() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(measurement())
    bounds: list[int] = []

    def still_adjusting(*args: object, warmup_max_ns: int, **kwargs: object) -> object:
        bounds.append(warmup_max_ns)
        raise CameraNotSettled("exposure still changing")

    r.source.capture = still_adjusting  # type: ignore[method-assign,assignment]
    r.service.run_next()
    ((_, body, _),) = r.hub.calls
    assert (body["outcome"], body["failure_reason"]) == ("failed", policy().reasons["camera_not_settled"])
    assert body["observations"] == [] and bounds == [measurement().warmup_max_ms * 1_000_000]


@pytest.mark.trace("E30-X8", "V85")
def test_reference_window_waits_for_its_start_and_reports_without_writing() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(reference(start_in_s=5.0, length_s=30.0, offset_ms=2000))
    run_when_due(r)
    view = r.service.status("ref_1")
    assert view is not None and view.state == "reading" and r.hub.calls == []
    assert view.outcome is not None and view.clock_uncertainty_ms == 12
    assert view.outcome.started_at == START + timedelta(seconds=5)
    assert r.service.cancel("ref_1") == "stopped"  # a drill (persist false) keeps nothing
    view = r.service.status("ref_1")
    assert view is not None and view.state == "cancelled" and view.outcome is None


@pytest.mark.trace("E30-X8", "Q8", "M8")
def test_a_persisted_reference_reading_is_never_discarded_by_cancel_or_expiry() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(reference(persist=True))
    run_when_due(r)
    assert r.service.cancel("ref_1") == "cannot_stop"
    r.clock.advance(1000)
    r.service.expire()
    view = r.service.status("ref_1")
    assert view is not None and view.state == "reading" and view.outcome is not None
    assert r.service.cancel("ref_1", recorded_observation_id="obs_1") == "stopped"  # recorded: released
    view = r.service.status("ref_1")
    assert view is not None and view.state == "cancelled"


@pytest.mark.trace("E30-X8", "m12")
def test_a_reference_window_that_opens_later_never_blocks_a_measurement() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(reference(start_in_s=300.0))
    r.service.start(measurement())
    assert r.service.run_next()
    assert r.hub.calls and r.hub.calls[0][2] == "acq_1.attempt"
    ref = r.service.status("ref_1")
    assert ref is not None and ref.state == "accepted"


@pytest.mark.trace("E30-X8", "V09", "V47")
def test_reference_with_another_origin_or_a_past_window_is_not_measured() -> None:
    r = rig({"camera_1": STEADY})
    with pytest.raises(AcquisitionRefused) as live:
        r.service.start(reference("ref_live", origin="LIVE"))
    assert live.value.code == "ORIGIN_MISMATCH"
    r.clock.advance(100)
    r.service.start(reference("ref_past", start_in_s=10, length_s=20))
    r.service.run_next()
    past = r.service.status("ref_past")
    assert past is not None and past.outcome is not None
    assert (past.state, past.outcome.result.reason) == ("no_reading", "window_missed")


@pytest.mark.trace("E30-X8", "AGENTS-6.bounds")
def test_unclaimed_work_and_reference_results_expire_but_undelivered_evidence_stays() -> None:
    r = rig({"camera_1": STEADY})
    r.hub.answers = [Delivery("unreachable", "unreachable")] * 10
    r.service.start(measurement("acq_kept"))
    r.service.run_next()
    r.service.start(reference("ref_1"))
    r.service.run_next()
    r.service.start(measurement("acq_idle"))
    r.clock.advance(1000)
    assert r.service.expire() == 2
    states = {k: v.state for k in ("acq_kept", "ref_1", "acq_idle") if (v := r.service.status(k)) is not None}
    assert states == {"acq_kept": "delivering", "ref_1": "expired", "acq_idle": "expired"}


@pytest.mark.trace("V15", "D3")
def test_attempt_instants_are_hub_time_from_the_offset() -> None:
    r = rig({"camera_1": STEADY})
    r.service.start(replace(measurement(), clock_offset_ms=3000))
    r.service.run_next()
    body = r.hub.calls[0][1]
    assert body["started_at"] == "2026-11-14T08:59:57.000Z"
    assert body["observations"][0]["observed_at"] == body["ended_at"]


@pytest.mark.trace("R09", "E06-01")
def test_breathing_request_fails_alone_when_shoulders_are_missing() -> None:
    r = rig({"camera_1": Scene(pulse_hz=1.6, breath_hz=0.25, shoulders=False)})
    r.service.start(measurement("acq_pulse"))
    r.service.start(measurement("acq_breath", code="respiratory_rate"))
    r.service.run_next()
    r.service.run_next()
    pulse_body, breath_body = (c[1] for c in r.hub.calls)
    assert pulse_body["outcome"] == "success" and pulse_body["observations"][0]["value"] == 96
    assert breath_body["outcome"] == "failed" and breath_body["failure_reason"] == "shoulders not visible"
