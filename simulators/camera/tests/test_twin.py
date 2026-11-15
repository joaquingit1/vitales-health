"""The twin itself: the synthetic scene carries exactly the rates it declares, each fault changes the picture the way
its name says, the device declares itself simulated on the wire, and the sample is refused unless it matches its
published checksum. A flat skin toned image stands in for the sample frame so these run without the download."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest

from crew_health_camera_twin.sample import FACE_BOX, SAMPLE, STREAM_SIZE, SampleUnavailable, cached
from crew_health_camera_twin.scenes import EXPOSURE_RAMP, LOW_LIGHT_GAIN, SCENARIOS, Synthetic, skin_mask
from crew_health_camera_twin.server import ORIGIN_HEADER, Server, Twin, handler

FPS = 30.0


def skin_frame() -> np.ndarray:
    """A face toned block on a dark background with a bright horizontal edge below it (the shoulder line)."""
    image = np.full((STREAM_SIZE[1], STREAM_SIZE[0], 3), 40, dtype=np.uint8)
    x, y, w, h = FACE_BOX
    image[y : y + h, x : x + w] = (110, 140, 200)  # BGR skin tone inside the YCrCb skin box
    image[260:, 180:460] = 230
    return image


def dominant_per_min(series: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(series - series.mean()))
    return float(np.fft.rfftfreq(len(series), 1.0 / FPS)[int(np.argmax(spectrum[1:])) + 1] * 60.0)


@pytest.mark.trace("AGENTS-4.twin")
def test_the_synthetic_scene_carries_the_pulse_and_breathing_rates_it_declares() -> None:
    base = skin_frame()
    scenario = SCENARIOS["synthetic"]
    scene = Synthetic(base, scenario, seed=1)
    mask = skin_mask(base)
    x, y, w, h = FACE_BOX
    frames = [scene.frame(i / FPS) for i in range(int(20 * FPS))]
    green = np.array([f[y + 10 : y + h - 10, x + 10 : x + w - 10, 1].mean() for f in frames])
    edge = np.array(
        [float(np.argmax(f[:, 320, 0] > 135)) + f[255:265, 320, 0].mean() / 255.0 for f in frames]
    )
    assert mask[y + h // 2, x + w // 2] and not mask[5, 5]
    assert dominant_per_min(green) == pytest.approx(scenario.pulse_per_min, abs=3.0)
    assert dominant_per_min(edge) == pytest.approx(scenario.breaths_per_min, abs=3.0)


@pytest.mark.trace("AGENTS-4.twin", "V05", "V08", "V11")
def test_each_fault_scene_changes_the_picture_the_way_its_name_says() -> None:
    base = skin_frame()
    clean = Synthetic(base, SCENARIOS["synthetic"], seed=1).frame(0.5)
    dark = Synthetic(base, SCENARIOS["low_light"], seed=1).frame(0.5)
    assert dark.mean() == pytest.approx(clean.mean() * LOW_LIGHT_GAIN, rel=0.1)
    crowded = Synthetic(base, SCENARIOS["second_face"], seed=1)
    assert skin_mask(crowded.frame(0.5)).sum() > 1.8 * skin_mask(crowded.frame(2.0)).sum()
    away = Synthetic(base, SCENARIOS["face_leaves"], seed=1)
    assert skin_mask(away.frame(1.0)).sum() > 0 and skin_mask(away.frame(8.0)).sum() == 0
    moving = Synthetic(base, SCENARIOS["head_motion"], seed=1)
    columns = [np.flatnonzero(skin_mask(moving.frame(t)).any(axis=0)).mean() for t in (0.0, 0.83, 2.5)]
    assert max(columns) - min(columns) > 100
    assert SCENARIOS["stream_drop"].fault == "stream_drop"
    ramping = Synthetic(base, SCENARIOS["auto_exposure_ramp"], seed=1)
    start, settled, later = (ramping.frame(t).reshape(-1, 3).mean(axis=0) for t in (0.0, 1.5, 9.0))
    first, last = EXPOSURE_RAMP[0], EXPOSURE_RAMP[-1]
    assert start[2] / settled[2] == pytest.approx(first[1] / last[1], rel=0.03)  # red climbs as measured
    assert start[0] / settled[0] == pytest.approx(first[3] / last[3], rel=0.03)  # blue falls as measured
    assert later == pytest.approx(settled, abs=0.5)  # then flat


@pytest.fixture
def served(tmp_path: Path) -> Iterator[str]:
    server = Server(("127.0.0.1", 0), handler(Twin(tmp_path / "unused.webm", skin_frame(), fps=FPS, seed=1)))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.trace("AGENTS-4.twin", "AGENTS-1.2")
def test_the_device_declares_itself_simulated_in_its_identity_and_on_every_stream(served: str) -> None:
    with urllib.request.urlopen(f"{served}/cameras/synthetic/identity", timeout=5) as reply:
        document = json.loads(reply.read())
    assert (document["origin_mode"], document["simulated"]) == ("SIM", True)
    assert document["injected_pulse_per_min"] == SCENARIOS["synthetic"].pulse_per_min
    with urllib.request.urlopen(f"{served}/cameras/synthetic/stream.mjpg", timeout=5) as stream:
        assert stream.headers[ORIGIN_HEADER] == "SIM"
        assert stream.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        data = b""
        while b"\xff\xd9" not in data:
            data += stream.read(4096)
    part = data[data.index(b"\xff\xd8") : data.index(b"\xff\xd9") + 2]
    image = cv2.imdecode(np.frombuffer(part, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape == (STREAM_SIZE[1], STREAM_SIZE[0], 3)
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{served}/cameras/nothing/stream.mjpg", timeout=5)


@pytest.mark.trace("AGENTS-4.twin")
def test_a_sample_that_is_missing_or_differs_from_its_published_checksum_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SampleUnavailable, match="camera-twin-fetch"):
        cached(tmp_path)
    (tmp_path / SAMPLE.name).write_bytes(b"\0" * 16)
    with pytest.raises(SampleUnavailable, match="SHA-256"):
        cached(tmp_path)
