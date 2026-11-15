"""The twin's real sample video: where it comes from, its license and attribution, and a verified fetch into a cache
outside git (the video itself is never committed).

The sample is a NASA Scientific Visualization Studio canned interview: one person seated, facing the camera in a
medium shot (face and shoulders visible), under studio light. NASA SVS media are public domain (Wikimedia Commons
file page: "This file is in the public domain in the United States because it was solely created by NASA"); the
attribution below follows the Commons page and the SVS credit page. The interviewee talks and gestures, so the twin
plays only its stillest stretch: the whole file was scanned at 10 frames per second with the face detector and pose
landmarker the worker uses (BlazeFace short range, pose landmarker lite), for the 25 s window with exactly one face and
both shoulders in every sampled frame and the least motion. The best window starts at 357.0 s (face centre within
0.032 and torso centre within 0.011 of the frame from their medians). The synthetic scenes start from the frame at
360.0 s, where the same detector finds the face box below (score 0.91).

Run `camera-twin-fetch` (or `uv run --project simulators/camera camera-twin-fetch`) once; tests and the twin read the
cached copy and refuse a file whose SHA-256 differs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    url: str
    size_bytes: int
    sha256: str
    license: str
    attribution: str
    credit_url: str


SAMPLE = Sample(
    name="rebekah.webm",
    url=(
        "https://upload.wikimedia.org/wikipedia/commons/6/68/NASA_Interview_Opportunity-_Star_light%2C_star_bright"
        "%2C_check_out_the_evening_sky_on_your_Halloween_walk_tonight_%28SVS14704_-_RebekahCannedInterview%29.webm"
    ),
    size_bytes=78_944_341,
    sha256="222ea44427f14bac6889b4d0eb6f3f75b0b45af5ed5764f89fb2ef26968b94ad",
    license="Public domain (work of NASA, 17 U.S.C. 105); Wikimedia Commons PD-USGov-NASA",
    attribution=(
        "NASA's Scientific Visualization Studio, SVS 14704 'Rebekah canned interview' (producers Michelle Handleman "
        "eMITS, Lauren Colvin NASA/GSFC, Angel Kumari NASA/GSFC; Aaron E. Lepsch ADNET Systems; Scott Wiessinger "
        "eMITS)"
    ),
    credit_url="https://svs.gsfc.nasa.gov/14704",
)
STILL_START_S = 357.0
STILL_LENGTH_S = 25.0
BASE_FRAME_S = 360.0
FACE_BOX = (258, 69, 113, 113)  # x, y, width, height in stream pixels on the frame at BASE_FRAME_S
STREAM_SIZE = (640, 360)  # frames are served at this size, as a 360p IP camera stream


def cache_dir() -> Path:
    """`CREW_HEALTH_CAMERA_SIM_CACHE` or the user's cache folder; never inside the repository."""
    configured = os.environ.get("CREW_HEALTH_CAMERA_SIM_CACHE")
    return Path(configured) if configured else Path.home() / ".cache" / "crew-health" / "camera-sim"


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


class SampleUnavailable(RuntimeError):
    """The sample is missing or its checksum differs; the message says how to fetch it."""


def cached(directory: Path | None = None) -> Path:
    """The verified cached sample, or SampleUnavailable naming the fetch command."""
    path = (directory or cache_dir()) / SAMPLE.name
    if not path.is_file():
        raise SampleUnavailable(f"{path} missing: run `uv run --project simulators/camera camera-twin-fetch`")
    if path.stat().st_size != SAMPLE.size_bytes or digest(path) != SAMPLE.sha256:
        raise SampleUnavailable(f"{path} does not match the published SHA-256; delete it and fetch again")
    return path


def fetch(directory: Path | None = None, *, timeout_s: float = 60.0) -> Path:
    """Download into the cache (temporary name, verified, then renamed); an existing verified copy is kept."""
    target_dir = directory or cache_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        return cached(target_dir)
    except SampleUnavailable:
        pass
    partial = target_dir / (SAMPLE.name + ".part")
    request = urllib.request.Request(SAMPLE.url, headers={"User-Agent": "crew-health-camera-twin/0.1"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response, partial.open("wb") as handle:
        written = 0
        for block in iter(lambda: response.read(1 << 20), b""):
            written += len(block)
            if written > SAMPLE.size_bytes:
                raise SampleUnavailable("download larger than the published size")
            handle.write(block)
    if digest(partial) != SAMPLE.sha256:
        partial.unlink(missing_ok=True)
        raise SampleUnavailable("downloaded file does not match the published SHA-256")
    os.replace(partial, target_dir / SAMPLE.name)
    return cached(target_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and verify the camera twin's public domain sample video."
    )
    parser.add_argument("--dir", type=Path, default=None, help="cache folder (default: %(default)s)")
    args = parser.parse_args()
    path = fetch(args.dir)
    print(f"{path} sha256 {SAMPLE.sha256}\nlicense: {SAMPLE.license}\nattribution: {SAMPLE.attribution}")
    sys.stdout.flush()
