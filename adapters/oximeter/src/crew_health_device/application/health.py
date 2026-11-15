"""Connection health and honest integration capability evidence."""

from collections.abc import Sequence
from threading import Lock
from typing import Literal

from crew_health_device.application.ports import TimeSource
from crew_health_device.domain.acquisition import OriginMode
from crew_health_device.domain.profile import DeviceProfile

type HealthState = Literal["connected", "quiet", "disconnected"]


class HealthTracker:
    """Link state of a connect-per-acquisition device. Before any acquisition the adapter is up with no data
    (quiet). A lost connection (disconnect, timeout, radio or device absent) is disconnected until the next
    successful open, so the hub's readiness turns the method unavailable (E07-01)."""

    def __init__(self, clock: TimeSource, quiet_ms: int) -> None:
        self._clock = clock
        self._quiet_ns = quiet_ms * 1000000
        self._lost = False
        self._last_frame: int | None = None
        self._lock = Lock()

    def connected(self) -> None:
        with self._lock:
            self._lost = False
            self._last_frame = self._clock.monotonic_ns()

    def frame_received(self) -> None:
        with self._lock:
            self._last_frame = self._clock.monotonic_ns()

    def disconnected(self) -> None:
        with self._lock:
            self._lost = True

    def state(self) -> HealthState:
        with self._lock:
            if self._lost:
                return "disconnected"
            if self._last_frame is None:
                return "quiet"
            if self._clock.monotonic_ns() - self._last_frame >= self._quiet_ns:
                return "quiet"
            return "connected"


def manifest(
    profile: DeviceProfile,
    *,
    family: str,
    service: str,
    characteristics: Sequence[str],
    source_origin: OriginMode,
    connected_state: HealthState,
    simulated_device: bool = False,
) -> dict[str, object]:
    # A simulated twin is a connected device whose identity says SIM (AGENTS.md section 4), not a file replay.
    live = source_origin == "LIVE" or (simulated_device and source_origin == "SIM")
    connected = live and connected_state == "connected"
    recorded = not live
    return {
        "family": family,
        "profile": {
            "id": profile.profile_id,
            "version": profile.version,
            "device_label": profile.device_label,
            "model": profile.model,
            "calibration": profile.calibration,
        },
        "protocol": {"service": service, "characteristics": sorted(characteristics)},
        "decoder_evidence": "specification_golden_cases",
        "hardware_compatibility": profile.hardware_evidence,
        "evidence_ref": profile.evidence_ref,
        "operations": [
            {
                "mode": "connected",
                "origin": "SIM" if simulated_device else "LIVE",
                "status": "available" if connected else "unavailable",
                "reason": "connected" if connected else "source_not_connected",
            },
            {
                "mode": "recorded",
                "origin": source_origin if recorded else "REPLAY",
                "status": "available" if recorded else "unavailable",
                "reason": "recording_selected" if recorded else "recording_not_selected",
            },
            {"mode": "manual", "origin": "LIVE", "status": "hub_owned"},
        ],
    }
