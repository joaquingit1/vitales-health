"""Real capture through the camera twin (marker hardware; AGENTS 4: hardware tests run against the simulated device by
default). The twin (simulators/camera) runs as its own process and streams MJPEG over HTTP; the worker reads it with
the same network camera source, MediaPipe models and acquisition service it runs in production, so the stream, the
feature reduction, the quality gate and the attempt body all run for real. The twin declares itself simulated, so
every reading is SIM through the normal source rule.

Each scenario is captured once (in rounds of at most three parallel streams, about 90 s in all) and checked against
what the twin injected: the synthetic pulse and breathing within the selection tolerance, the real sample measured or
not measured with a reason (never a value outside the band), each fault its own reason, and a camera still ramping
its exposure when opened measured at its injected pulse. A person runs the real webcam separately with
CREW_HEALTH_CAMERA_STREAM set (an index or a URL); see test_a_real_camera_reads_live_or_refuses_with_a_reason.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.request
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from crew_health_camera.adapters.models import fetch
from crew_health_camera.adapters.system_clock import SystemClock
from crew_health_camera.adapters.video_source import Stream, VideoSource
from crew_health_camera.application.ports import Camera, CameraNotSettled
from crew_health_camera.application.service import AcquisitionService
from crew_health_camera.tests.support import BOUNDS, SUBJECT, FakeHub, MemoryEvidence, measurement, policy

pytestmark = pytest.mark.hardware
TWIN = Path(__file__).resolve().parents[5] / "simulators" / "camera"
QUALITY = policy()
ROUND = 3  # streams analysed at once: real time landmark detection on more starves the frame rate on a laptop
FAULTS = {
    "head_motion": "too_much_movement",
    "low_light": "too_dark",
    "second_face": "ambiguous_subject",
    "face_leaves": "face_not_visible",
    "stream_drop": "capture_failed",
}


def _uv() -> str:
    found = shutil.which("uv")
    if found is None:
        raise RuntimeError("uv not on PATH: the twin runs in its own uv project")
    return found


@pytest.fixture(scope="module")
def twin() -> Iterator[str]:
    """The twin process on a free port; the sample video is fetched and verified first (no-op when cached)."""
    subprocess.run([_uv(), "run", "--project", str(TWIN), "camera-twin-fetch"], check=True, timeout=900)
    process = subprocess.Popen(
        [_uv(), "run", "--project", str(TWIN), "camera-twin", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    ready: list[str] = []
    found = threading.Event()

    def watch() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if "listening on" in line:
                ready.append(line.rsplit(" ", 1)[-1].strip())
                found.set()

    threading.Thread(target=watch, daemon=True).start()
    try:
        if not found.wait(120):
            raise RuntimeError("camera twin did not start")
        yield ready[0]
    finally:
        process.terminate()
        process.wait(10)


def camera_name(stream: Stream) -> str:
    """The name the quality notes show, from the stream itself: a local index or the network camera's path."""
    return f"Camera {stream}" if isinstance(stream, int) else "Camera " + urlsplit(stream).path.strip("/")


def measure(stream: Stream, code: str) -> dict[str, Any]:
    """One acquisition through the production service and source; returns the attempt body sent to the hub."""
    clock = SystemClock(segment="seg_" + uuid.uuid4().hex, poll_ms=50)
    source = VideoSource(
        clock=clock,
        cameras=(Camera("camera_1", camera_name(stream), "station", SUBJECT),),
        streams={"camera_1": stream},
        models=fetch(),
        rules=QUALITY.capture,
        timeout_s=3.0,
    )
    hub = FakeHub()
    service = AcquisitionService(
        policy=QUALITY, source=source, clock=clock, hub=hub, bounds=BOUNDS,
        receipt_ids=lambda: "rcp_" + uuid.uuid4().hex, evidence=MemoryEvidence(),
    )  # fmt: skip
    service.start(measurement(code=code, origin=source.origin_mode))
    assert service.run_next()
    ((_, body, _),) = hub.calls
    return body


@pytest.fixture(scope="module")
def results(twin: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Rounds of at most ROUND streams, the ground truth scenes first: more streams of real time landmark detection
    at once starve the frame rate, which the gate rightly refuses (frame gaps), and a busy laptop makes that worse."""
    rounds = [[("synthetic", "pulse"), ("synthetic", "respiratory_rate"), ("sample", "pulse")]]
    faults = [(name, "pulse") for name in FAULTS] + [("auto_exposure_ramp", "pulse")]
    rounds += [faults[i : i + ROUND] for i in range(0, len(faults), ROUND)]
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for jobs in rounds:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {
                job: pool.submit(measure, f"{twin}/cameras/{job[0]}/stream.mjpg", job[1]) for job in jobs
            }
            found |= {job: future.result(timeout=180) for job, future in futures.items()}
    return found


def injected(twin: str, scenario: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{twin}/cameras/{scenario}/identity", timeout=5) as reply:
        value: dict[str, Any] = json.loads(reply.read())
    return value


@pytest.mark.trace("E06-01", "R09", "D08", "AGENTS-4.twin")
@pytest.mark.parametrize(
    ("code", "key"), [("pulse", "injected_pulse_per_min"), ("respiratory_rate", "injected_breaths_per_min")]
)
def test_the_synthetic_video_reads_its_injected_rate_within_the_gate_tolerance(
    twin: str, results: dict[tuple[str, str], dict[str, Any]], code: str, key: str
) -> None:
    truth = injected(twin, "synthetic")
    body = results[("synthetic", code)]
    assert body["outcome"] == "success", body["quality_notes"]
    (reading,) = body["observations"]
    assert abs(reading["value"] - truth[key]) <= QUALITY.agreement_tolerance_per_min
    assert (reading["unit"], reading["origin_mode"], reading["device_id"]) == ("/min", "SIM", "camera_1")
    assert truth["origin_mode"] == "SIM" and truth["simulated"] is True


@pytest.mark.trace("E06-01", "AGENTS-1.5", "AGENTS-4.twin")
def test_a_camera_still_settling_its_exposure_when_opened_reads_its_injected_pulse(
    twin: str, results: dict[tuple[str, str], dict[str, Any]]
) -> None:
    """The real-webcam run found it: a camera that ramps its auto exposure and white balance for its first 1.3 s
    put that ramp inside the analysis window and a still face with good light came out signal_quality_too_low.
    The window now starts at the first settled frame."""
    body = results[("auto_exposure_ramp", "pulse")]
    assert body["outcome"] == "success", body["quality_notes"]
    (reading,) = body["observations"]
    truth = injected(twin, "auto_exposure_ramp")["injected_pulse_per_min"]
    assert abs(reading["value"] - truth) <= QUALITY.agreement_tolerance_per_min


@pytest.mark.trace("E06-01", "AGENTS-1.5")
def test_a_picture_still_brightening_at_the_warm_up_bound_is_never_analysed() -> None:
    import numpy

    clock = SystemClock(segment="seg_" + uuid.uuid4().hex, poll_ms=50)

    class Brightening:
        level = 40.0

        def read(self) -> Any:
            self.level += 0.5  # 15 levels per second at 30 frames per second, far above the policy limit
            clock.wait_until_ns(clock.monotonic_ns() + 33_000_000, lambda: False)
            return numpy.full((48, 64, 3), min(self.level, 255.0), dtype=numpy.uint8)

        def close(self) -> None:
            return

    class NoLandmarks:
        def detect(self, rgb: Any, t_ms: int) -> tuple[list[Any], list[Any]]:
            raise AssertionError("an unsettled frame reached analysis")

    source = VideoSource(
        clock=clock, cameras=(Camera("camera_1", "Camera 0", "station", SUBJECT),), streams={"camera_1": 0},
        models=fetch(), rules=QUALITY.capture, timeout_s=1.0, identify=lambda stream: "LIVE",
    )  # fmt: skip
    out: list[Any] = []
    with pytest.raises(CameraNotSettled):
        source._read(
            "camera_1", "seg", clock.monotonic_ns(), (2_000_000_000, 20_000_000_000), 1000, lambda: False,
            Brightening(), NoLandmarks(), out,  # type: ignore[arg-type]
        )  # fmt: skip
    assert out == []


@pytest.mark.trace("E06-01", "AGENTS-1.5", "AGENTS-4.twin")
def test_the_real_sample_video_is_measured_or_not_measured_with_a_reason_never_estimated(
    results: dict[tuple[str, str], dict[str, Any]],
) -> None:
    body = results[("sample", "pulse")]
    if body["outcome"] == "success":
        band = QUALITY.pulse.signal.band
        for reading in body["observations"]:
            assert (
                band.min_hz * 60 <= reading["value"] <= band.max_hz * 60 and reading["origin_mode"] == "SIM"
            )
    else:
        assert body["observations"] == [] and body["failure_reason"] in QUALITY.reasons.values()


@pytest.mark.trace("V05", "V08", "V11", "R29", "AGENTS-4.twin")
@pytest.mark.parametrize(("scenario", "reason"), sorted(FAULTS.items()))
def test_each_fault_scenario_is_not_measured_with_its_own_reason(
    results: dict[tuple[str, str], dict[str, Any]], scenario: str, reason: str
) -> None:
    body = results[(scenario, "pulse")]
    assert (body["outcome"], body["failure_reason"]) == ("failed", QUALITY.reasons[reason])
    assert body["observations"] == []
    assert len(set(FAULTS.values())) == len(FAULTS)  # every fault has a reason of its own


@pytest.mark.trace("V05", "D09", "AGENTS-4.twin")
def test_a_real_camera_reads_live_or_refuses_with_a_reason() -> None:
    """The extra real-device run, done by a person in front of the camera (never by an agent):
    CREW_HEALTH_CAMERA_STREAM=0 uv run --project workers/camera --extra capture pytest -m hardware -k real_camera"""
    configured = os.environ.get("CREW_HEALTH_CAMERA_STREAM")
    if configured is None:
        pytest.skip("real device run: set CREW_HEALTH_CAMERA_STREAM; the twin tests above cover this path")
    stream: Stream = int(configured) if configured.isdigit() else configured
    body = measure(stream, "pulse")
    if body["outcome"] == "success":
        assert {o["origin_mode"] for o in body["observations"]} == {"LIVE"}
    else:
        assert body["failure_reason"] in QUALITY.reasons.values()
    print(json.dumps(body, indent=2))
