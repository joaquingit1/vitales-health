"""Explicitly labeled recorded input through the normal frame source port."""

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from crew_health_device.adapters.profile_file import load_document, validate_document
from crew_health_device.application.ports import RawFrame, SourceDisconnected, SourceUnavailable
from crew_health_device.domain.acquisition import OriginMode
from crew_health_device.domain.errors import ProfileError


class RecordedFrameSource:
    def __init__(self, document: Mapping[str, object], *, max_frames: int) -> None:
        validate_document(document, "frame-recording.schema.json")
        frames = cast(list[dict[str, str]], document["frames"])
        if not 1 <= max_frames <= 4096 or len(frames) > max_frames:
            raise ProfileError("/frames", "too_many_frames")
        self._origin = cast(OriginMode, document["origin"])
        self._frames = tuple(dict(frame) for frame in frames)
        self._index = 0
        self._opened = False

    @property
    def origin(self) -> OriginMode:
        return self._origin

    def open(self, timeout_ms: int) -> None:
        if self._frames and self._frames[0].get("event") in ("radio_absent", "device_absent"):
            reason = self._frames[0]["event"]
            if reason == "radio_absent":
                raise SourceUnavailable("radio_absent")
            raise SourceUnavailable("device_absent")
        self._opened = True

    def next_frame(self, timeout_ms: int) -> RawFrame | None:
        if not self._opened:
            raise SourceDisconnected()
        if self._index >= len(self._frames):
            return None
        frame = self._frames[self._index]
        self._index += 1
        event = frame.get("event")
        if event == "timeout":
            return None
        if event is not None:
            raise SourceDisconnected()
        return RawFrame(frame["characteristic"], bytes.fromhex(frame["payload_hex"]))

    def close(self) -> None:
        self._opened = False


def load_recording_file(path: Path, max_bytes: int) -> Mapping[str, object]:
    document = load_document(path, max_bytes)
    validate_document(document, "frame-recording.schema.json")
    return document
