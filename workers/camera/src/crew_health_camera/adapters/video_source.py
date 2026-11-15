"""Real capture: a local camera (a capture index, read with OpenCV) or a network camera (an MJPEG stream over HTTP, as
IP cameras serve it), reduced frame by frame to FrameFeatures with MediaPipe's face detector and pose landmarker
(extra `capture`). The pixels are dropped at once on this device (SYSTEM 7.1).

Which kind each camera is comes from the device configuration (`stream`: an index or an http(s) URL), and the
origin comes from the device: a network camera's stream response may declare it in the `X-Device-Origin` header (a
simulated camera says SIM there, AGENTS 4 twin rule), and a camera that declares nothing is a real one, LIVE. Every
camera of one worker must have the same origin; a mix refuses to start, and a camera whose declared origin changes
fails its capture. There is no other branch for simulated devices.

Each camera is read on its own thread with its own detector instances, bounded by `max_frames` per camera (m4) and by
the capture's duration; a stream that stops or drops mid capture fails the capture (`capture_failed`). A camera opened
fresh ramps its exposure and white balance first: frames are discarded until the picture has settled
(domain/exposure.py, policy capture.settled_change_per_s), the window counts from the first settled frame, and a
camera still changing at the warm up bound the hub sent is `camera_not_settled`, never analysed. The limits
used while reducing a frame (clipped level, skin region margin, landmark visibility) come from the quality policy
(m6). Hermetic tests use the SIM source through the same port; this adapter runs against the camera twin
(simulators/camera) in the tests marked `hardware`, and against a real webcam only when a person runs it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from crew_health_camera.application.ports import (
    Camera,
    CameraNotSettled,
    Capture,
    CaptureFailed,
    OriginMode,
    WorkerClock,
)
from crew_health_camera.domain.exposure import Settling
from crew_health_camera.domain.features import FaceFeatures, FrameFeatures, TorsoFeatures
from crew_health_camera.domain.policy import CaptureRules

ORIGIN_HEADER = "x-device-origin"
_ORIGINS: tuple[OriginMode, ...] = ("LIVE", "REPLAY", "SIM")
_MAX_PART_BYTES = 4 * 1024 * 1024  # one JPEG of a camera stream; a larger part is a broken stream
_SHOULDERS = (11, 12)  # MediaPipe pose landmark indexes of the left and right shoulder

type Stream = int | str


@dataclass(frozen=True, slots=True)
class Models:
    """The landmark model files (fetched with their SHA-256 by scripts/fetch_models.py), loaded at startup."""

    face_detector: Path
    pose_landmarker: Path


class Reader(Protocol):
    def read(self) -> Any | None: ...  # a BGR image, or None when the stream ended
    def close(self) -> None: ...


class StreamDropped(Exception):
    """The camera stopped sending mid capture (unplugged, network drop, device restart)."""


def declared_origin(stream: Stream, *, timeout_s: float) -> OriginMode:
    """The origin a camera declares: the `X-Device-Origin` header of a network camera's stream response, LIVE when it
    declares none or is a local capture device. An unreachable or malformed declaration is an error, never LIVE."""
    if isinstance(stream, int):
        return "LIVE"
    try:
        with httpx.stream("GET", stream, timeout=timeout_s) as response:
            response.raise_for_status()
            declared = response.headers.get(ORIGIN_HEADER)
    except httpx.HTTPError as error:
        raise CaptureFailed("camera identity unreachable") from error
    if declared is None:
        return "LIVE"
    origin = next((o for o in _ORIGINS if o == declared.strip()), None)
    if origin is None:
        raise CaptureFailed("camera identity malformed")
    return origin


class _LocalReader:
    def __init__(self, index: int) -> None:
        import cv2

        self._video = cv2.VideoCapture(index)
        if not self._video.isOpened():
            self._video.release()
            raise CaptureFailed("camera not available")

    def read(self) -> Any | None:
        ok, image = self._video.read()
        return image if ok else None

    def close(self) -> None:
        self._video.release()


class _MjpegReader:
    """An MJPEG over HTTP stream: JPEG parts found by their start and end markers, so any boundary works."""

    def __init__(self, url: str, *, timeout_s: float, origin: OriginMode) -> None:
        self._client = httpx.Client(timeout=timeout_s)
        try:
            self._response = self._client.send(self._client.build_request("GET", url), stream=True)
            self._response.raise_for_status()
        except httpx.HTTPError as error:
            self._client.close()
            raise CaptureFailed("camera not available") from error
        if (self._response.headers.get(ORIGIN_HEADER) or "LIVE").strip() != origin:
            self.close()
            raise CaptureFailed("camera origin changed")
        self._chunks: Iterator[bytes] = self._response.iter_raw()
        self._buffer = b""

    def read(self) -> Any | None:
        import cv2
        import numpy

        while True:
            start = self._buffer.find(b"\xff\xd8")
            end = self._buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
            if end >= 0:
                part, self._buffer = self._buffer[start : end + 2], self._buffer[end + 2 :]
                image = cv2.imdecode(numpy.frombuffer(part, dtype=numpy.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise StreamDropped("corrupt frame")
                return image
            if len(self._buffer) > _MAX_PART_BYTES:
                raise StreamDropped("part too large")
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                return None
            except httpx.HTTPError as error:
                raise StreamDropped(type(error).__name__) from error

    def close(self) -> None:
        self._response.close()
        self._client.close()


class _Landmarks:
    """MediaPipe tasks (face detector, pose landmarker) in video mode for one camera's capture."""

    def __init__(self, models: Models) -> None:
        import mediapipe

        vision = mediapipe.tasks.vision
        base = mediapipe.tasks.BaseOptions
        self._mp = mediapipe
        self._faces = vision.FaceDetector.create_from_options(
            vision.FaceDetectorOptions(
                base_options=base(model_asset_path=str(models.face_detector)),
                running_mode=vision.RunningMode.VIDEO,
            )
        )
        self._pose = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=base(model_asset_path=str(models.pose_landmarker)),
                running_mode=vision.RunningMode.VIDEO,
                num_poses=2,
            )
        )

    def detect(self, rgb: Any, t_ms: int) -> tuple[list[Any], list[Any]]:
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        faces = self._faces.detect_for_video(image, t_ms).detections
        poses = self._pose.detect_for_video(image, t_ms).pose_landmarks
        return list(faces), list(poses)

    def close(self) -> None:
        self._faces.close()
        self._pose.close()


class VideoSource:
    """FrameSource over configured cameras. `origin` is resolved once at startup from what the devices declare."""

    def __init__(
        self,
        *,
        clock: WorkerClock,
        cameras: tuple[Camera, ...],
        streams: dict[str, Stream],
        models: Models,
        rules: CaptureRules,
        timeout_s: float,
        identify: Callable[[Stream], OriginMode] | None = None,
    ) -> None:
        if set(streams) != {c.device_id for c in cameras}:
            raise ValueError("every camera needs exactly one stream")
        self._clock, self._cameras, self._streams = clock, cameras, dict(streams)
        self._models, self._rules, self._timeout = models, rules, timeout_s
        ask = identify or (lambda s: declared_origin(s, timeout_s=timeout_s))
        origins = {ask(streams[c.device_id]) for c in cameras}
        if len(origins) > 1:
            raise ValueError("cameras of one worker declare different origins")
        self._origin: OriginMode = origins.pop() if origins else "LIVE"

    @property
    def origin_mode(self) -> OriginMode:
        return self._origin

    def cameras(self) -> tuple[Camera, ...]:
        return self._cameras

    def capture(
        self,
        device_ids: tuple[str, ...],
        *,
        duration_ns: int,
        max_frames: int,
        warmup_max_ns: int,
        should_stop: Callable[[], bool],
    ) -> Capture:
        try:
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401
        except ImportError as error:
            raise CaptureFailed("capture extra not installed") from error
        segment, start = self._clock.segment(), self._clock.monotonic_ns()
        results: dict[str, list[FrameFeatures]] = {d: [] for d in device_ids}
        errors: list[BaseException] = []
        threads = [
            threading.Thread(
                target=self._run,
                args=(
                    device,
                    segment,
                    start,
                    (warmup_max_ns, duration_ns),
                    max_frames,
                    should_stop,
                    results[device],
                    errors,
                ),  # fmt: skip
                name="camera-" + device,
                daemon=True,
            )
            for device in device_ids
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join((warmup_max_ns + duration_ns) / 1e9 + 10 * self._timeout)
        if any(isinstance(e, CameraNotSettled) for e in errors):
            raise CameraNotSettled("exposure still changing")
        if errors or any(t.is_alive() for t in threads):
            raise CaptureFailed("capture interrupted")
        frames = sorted((f for found in results.values() for f in found), key=lambda f: (f.t_ns, f.device_id))
        if not frames:
            raise CaptureFailed("no frames")
        return Capture(tuple(frames), segment, frames[0].t_ns, frames[-1].t_ns)

    def _run(
        self,
        device: str,
        segment: str,
        start: int,
        spans: tuple[int, int],
        max_frames: int,
        should_stop: Callable[[], bool],
        out: list[FrameFeatures],
        errors: list[BaseException],
    ) -> None:
        try:
            stream = self._streams[device]
            reader: Reader = (
                _LocalReader(stream)
                if isinstance(stream, int)
                else _MjpegReader(stream, timeout_s=self._timeout, origin=self._origin)
            )
            landmarks = _Landmarks(self._models)
            try:
                self._read(device, segment, start, spans, max_frames, should_stop, reader, landmarks, out)
            finally:
                reader.close()
                landmarks.close()
        except (CaptureFailed, StreamDropped, OSError, RuntimeError, ValueError) as error:
            errors.append(error)

    def _read(
        self,
        device: str,
        segment: str,
        start: int,
        spans: tuple[int, int],
        max_frames: int,
        should_stop: Callable[[], bool],
        reader: Reader,
        landmarks: _Landmarks,
        out: list[FrameFeatures],
    ) -> None:
        """`spans` is (warm up bound, window) in ns. Frames before the picture settles are dropped unanalysed."""
        import cv2

        warmup_max_ns, duration_ns = spans
        settling = Settling(self._rules.settled_change_per_s)
        last_ms, opened, first = -1, None, None
        while len(out) < max_frames and not should_stop():
            image = reader.read()
            now = self._clock.monotonic_ns()
            if image is None:
                raise StreamDropped("stream ended")
            if first is None:
                opened = now if opened is None else opened
                blue, green, red = (float(v) for v in cv2.mean(image)[:3])
                if not settling.settled(now, (red, green, blue)):
                    if now - opened > warmup_max_ns:
                        raise CameraNotSettled("exposure still changing")
                    continue
                first = now  # the window counts from the first settled frame, not from opening the camera
            if now - first > duration_ns:
                return
            t_ms = max(last_ms + 1, (now - start) // 1_000_000)
            last_ms = t_ms
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            faces, poses = landmarks.detect(rgb, t_ms)
            out.append(features(device, segment, now, image, faces, poses, self._rules))


def features(
    device: str, segment: str, t_ns: int, image: Any, faces: list[Any], poses: list[Any], rules: CaptureRules
) -> FrameFeatures:
    """One frame's features. The skin region is the face box minus the policy margin on every side; a shoulder counts
    only at the policy's landmark visibility; luma and the clipped fraction come from the grey image."""
    import cv2

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    luma = float(gray.mean())
    saturated = float((gray >= rules.saturated_level).mean())
    face = None
    if len(faces) == 1:
        box = faces[0].bounding_box
        m = rules.face_region_margin
        x0, y0 = int(box.origin_x + box.width * m), int(box.origin_y + box.height * m)
        x1, y1 = int(box.origin_x + box.width * (1 - m)), int(box.origin_y + box.height * (1 - m))
        region = image[max(y0, 0) : max(y1, 1), max(x0, 0) : max(x1, 1)]
        if region.size:
            b, g, r = (float(region[..., c].mean()) for c in range(3))
            centre_x = (box.origin_x + box.width / 2) / width
            centre_y = (box.origin_y + box.height / 2) / height
            face = FaceFeatures(r, g, b, centre_x, centre_y)
    torso = None
    if len(poses) == 1:
        left, right = (poses[0][i] for i in _SHOULDERS)
        if min(left.visibility, right.visibility) >= rules.min_landmark_visibility:
            torso = TorsoFeatures(float(left.y), float(right.y), float((left.x + right.x) / 2))
    return FrameFeatures(device, segment, t_ns, luma, saturated, len(faces), face, torso)
