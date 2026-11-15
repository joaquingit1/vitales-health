"""Durably reserve once, capture with bounds, then journal the exact delivery body."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from threading import Lock
from typing import cast

from crew_health_device.application.health import HealthTracker
from crew_health_device.application.ports import (
    FrameSource,
    Journal,
    JournalCorrupt,
    JournalRecord,
    SourceDisconnected,
    SourceUnavailable,
    TimeSource,
)
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.application.serialization import attempt_body as attempt_body
from crew_health_device.application.serialization import binding_body
from crew_health_device.application.settings import AdapterSettings
from crew_health_device.domain.acquisition import (
    AcquisitionState,
    AttemptDraft,
    Binding,
    CancelReply,
    CaptureEnd,
    ReceivedFrame,
    StartReply,
    build_attempt,
)
from crew_health_device.domain.errors import BindingError, FrameError
from crew_health_device.domain.profile import DeviceProfile
from crew_health_device.domain.readings import Decoder

_LOST: frozenset[str] = frozenset(("disconnected", "timeout", "radio_absent", "device_absent"))


@dataclass(slots=True)
class _Active:
    binding: Binding
    profile: DeviceProfile
    cancelled: bool = False
    capturing: bool = False
    source_notes: tuple[str, ...] = ()


def _same_binding(key: str, stored: bytes, binding: Binding) -> bool:
    try:
        fields = json.loads(stored)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise JournalCorrupt(key) from error
    if not isinstance(fields, dict):
        raise JournalCorrupt(key)
    fields.pop("adapter_profile", None)
    return bool(fields == asdict(binding))


class AcquisitionService:
    def __init__(
        self,
        decoder: Decoder,
        profile_store: ProfileStore,
        source_factory: Callable[[], FrameSource],
        journal: Journal,
        clock: TimeSource,
        settings: AdapterSettings,
        adapter_id: str,
        *,
        health: HealthTracker | None = None,
    ) -> None:
        self._decoder = decoder
        self._profiles = profile_store
        self._source_factory = source_factory
        self._journal = journal
        self._clock = clock
        self._settings = settings
        self.adapter_id = adapter_id
        self._health = health
        self._active: _Active | None = None
        self._lock = Lock()

    def start(self, binding: Binding) -> StartReply:
        with self._lock:
            key = self._key(binding.acquisition_id)
            existing = self._journal.get(key)
            if existing is not None:
                # Same acquisition, same binding: an idempotent replay. Same id, another request, patient,
                # capability or device: a conflict, never a silent "duplicate" that captures for the first one.
                return "duplicate" if _same_binding(key, existing.body, binding) else "conflict"
            if self._active is not None:
                return "busy"
            if binding.method != "device":
                return "rejected"
            profile = self._profiles.current()
            record = JournalRecord(
                key,
                "acquisition",
                "started",
                binding.request_id,
                binding_body(binding, f"{profile.profile_id}@{profile.version}"),
                None,
            )
            self._journal.put(record)
            self._active = _Active(binding, profile)
            return "started"

    def capture(self, acquisition_id: str) -> AttemptDraft:
        with self._lock:
            active = self._active
            if active is None or active.binding.acquisition_id != acquisition_id or active.capturing:
                raise BindingError("acquisition_not_running")
            active.capturing = True
        started_at = self._clock.now()
        source = self._source_factory()
        end: CaptureEnd = "window_complete"
        lost_on_close = False
        try:
            frames, end = self._collect(source, active)
        finally:
            try:
                source.close()
            except SourceDisconnected as error:
                # The link dropped while closing: the reading already collected stays complete (B1 is about a
                # link lost before the window or frame budget was used); health still reports the loss.
                self._note_disconnect(active, error)
                lost_on_close = True
            finally:
                # A connect-per-acquisition device stays reachable after a normal end (quiet, then
                # connected on the next frame); only a failed connection reports disconnected.
                if self._health is not None and (end in _LOST or lost_on_close):
                    self._health.disconnected()
        with self._lock:
            if active.cancelled:
                # Frames read before the cancel are real evidence: keep them; the hub quarantines a late result.
                end = "cancelled"
            draft = build_attempt(
                active.binding,
                active.profile,
                frames,
                started_at=started_at,
                ended_at=self._clock.now(),
                source_origin=source.origin,
                end=end,
            )
            if active.source_notes:
                draft = replace(
                    draft, quality_notes=tuple(sorted(set(draft.quality_notes) | set(active.source_notes)))
                )
            self._complete(active, draft)
            return draft

    def _collect(self, source: FrameSource, active: _Active) -> tuple[list[ReceivedFrame], CaptureEnd]:
        frames: list[ReceivedFrame] = []
        start_ns = self._clock.monotonic_ns()
        if self._cancelled(active):
            return frames, "cancelled"
        try:
            source.open(self._settings.connect_timeout_ms)
        except SourceUnavailable as error:
            return frames, "timeout" if error.reason == "connect_timeout" else error.reason
        if self._health is not None:
            self._health.connected()
        for _ in range(self._settings.max_frames):
            if self._cancelled(active):
                return frames, "cancelled"
            elapsed = self._clock.monotonic_ns() - start_ns
            remaining_ns = self._settings.acquisition_window_ms * 1000000 - elapsed
            if remaining_ns <= 0:
                return frames, "window_complete"
            timeout_ms = min(self._settings.frame_timeout_ms, max(1, remaining_ns // 1000000))
            try:
                raw = source.next_frame(timeout_ms)
            except SourceDisconnected as error:
                self._note_disconnect(active, error)
                return frames, "disconnected"
            if self._cancelled(active):
                return frames, "cancelled"
            if raw is None:
                return frames, "timeout"
            if self._health is not None:
                self._health.frame_received()
            received_at, monotonic_ns = self._clock.now(), self._clock.monotonic_ns()
            try:
                if len(raw.payload) > self._settings.max_frame_bytes:
                    raise FrameError("frame_too_large")
                frame = self._decoder.decode(raw.characteristic, raw.payload)
            except FrameError as error:
                frames.append(ReceivedFrame(error, received_at, monotonic_ns))
            else:
                frames.append(ReceivedFrame(frame, received_at, monotonic_ns))
        return frames, "window_complete"

    @staticmethod
    def _note_disconnect(active: _Active, error: SourceDisconnected) -> None:
        if error.reason == "queue_overflow":
            active.source_notes = ("source:queue_overflow",)

    def _complete(self, active: _Active, draft: AttemptDraft) -> None:
        binding = active.binding
        send = JournalRecord(
            f"e07:{binding.acquisition_id}:{binding.reserved_sequence}",
            "send",
            "pending",
            binding.request_id,
            attempt_body(draft),
            None,
        )
        self._journal.put(send)
        record = self._journal.get(self._key(binding.acquisition_id))
        if record is None:
            raise BindingError("acquisition_record_missing")
        state = (
            "stopped" if active.cancelled else "completed" if draft.outcome == "success" else draft.outcome
        )
        self._journal.put(replace(record, state=state))
        self._active = None

    def _cancelled(self, active: _Active) -> bool:
        with self._lock:
            return active.cancelled

    def cancel(self, acquisition_id: str, generation: int) -> CancelReply:
        with self._lock:
            record = self._matching(acquisition_id, generation)
            if record is None:
                return "unknown"
            active = self._active
            if active is not None and active.binding.acquisition_id == acquisition_id:
                active.cancelled = True
                return "stopped"
            return "unknown" if record.state == "started" else "cannot_stop"

    def status(self, acquisition_id: str, generation: int) -> AcquisitionState:
        with self._lock:
            record = self._matching(acquisition_id, generation)
            if record is None:
                return "unknown"
            if record.state == "started":
                active = self._active
                running = (
                    active is not None
                    and active.binding.acquisition_id == acquisition_id
                    and active.binding.generation == generation
                )
                return "started" if running else "unknown"
            if record.state in ("completed", "failed", "rejected", "stopped"):
                return cast(AcquisitionState, record.state)
            return "unknown"

    def _matching(self, acquisition_id: str, generation: int) -> JournalRecord | None:
        record = self._journal.get(self._key(acquisition_id))
        if record is None:
            return None
        try:
            document = json.loads(record.body)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise JournalCorrupt(record.key) from error
        if not isinstance(document, dict) or type(document.get("generation")) is not int:
            raise JournalCorrupt(record.key)
        return record if document["generation"] == generation else None

    @staticmethod
    def _key(acquisition_id: str) -> str:
        return f"acquisition:{acquisition_id}"
