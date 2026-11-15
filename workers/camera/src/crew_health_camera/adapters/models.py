"""The landmark model files the capture adapter loads (MediaPipe face detector and pose landmarker, Apache 2.0),
fetched once into a cache outside git and verified by SHA-256 before use. A missing or altered file keeps real capture
off (the worker refuses to start with real cameras); nothing falls back to another model.

Run `crew-health-camera-models` (or `uv run --project workers/camera crew-health-camera-models`) once.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from crew_health_camera.adapters.video_source import Models


@dataclass(frozen=True, slots=True)
class ModelFile:
    name: str
    url: str
    sha256: str


FACE_DETECTOR = ModelFile(
    "blaze_face_short_range.tflite",
    "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/"
    "blaze_face_short_range.tflite",
    "b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f",
)
POSE_LANDMARKER = ModelFile(
    "pose_landmarker_lite.task",
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/"
    "pose_landmarker_lite.task",
    "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a",
)
_MAX_BYTES = 64 * 1024 * 1024


class ModelUnavailable(RuntimeError):
    """A model file is missing or differs from its published SHA-256."""


def model_dir() -> Path:
    configured = os.environ.get("CREW_HEALTH_CAMERA_MODELS")
    return Path(configured) if configured else Path.home() / ".cache" / "crew-health" / "camera-models"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified(directory: Path | None = None) -> Models:
    folder = directory or model_dir()
    paths = []
    for model in (FACE_DETECTOR, POSE_LANDMARKER):
        path = folder / model.name
        if not path.is_file() or _digest(path) != model.sha256:
            raise ModelUnavailable(
                f"{path} missing or altered: run `uv run --project workers/camera crew-health-camera-models`"
            )
        paths.append(path)
    return Models(paths[0], paths[1])


def fetch(directory: Path | None = None, *, timeout_s: float = 60.0) -> Models:
    folder = directory or model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for model in (FACE_DETECTOR, POSE_LANDMARKER):
        path = folder / model.name
        if path.is_file() and _digest(path) == model.sha256:
            continue
        request = urllib.request.Request(model.url, headers={"User-Agent": "crew-health-camera/0.1"})
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            data = response.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES or hashlib.sha256(data).hexdigest() != model.sha256:
            raise ModelUnavailable(f"{model.url} does not match its published SHA-256")
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_bytes(data)
        os.replace(partial, path)
    return verified(folder)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and verify the camera worker's landmark models.")
    parser.add_argument("--dir", type=Path, default=None)
    models = fetch(parser.parse_args().dir)
    print(f"{models.face_detector}\n{models.pose_landmarker}")
