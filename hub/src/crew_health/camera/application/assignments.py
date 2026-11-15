"""Camera readiness and assignment for E05 (A7: E06 provides AdapterReadinessPort and AdapterAssignmentPort).

Both answer from the last health probe, never by calling the worker inside a unit of work (lesson 9). A stale or
failed probe is unknown: readiness None, no assignment, so E05 fails closed (`adapter_unassigned`) and manual entry
stays the path (SYSTEM 3 camera row). The assignment names the worker's adapter only when the worker reports the
adapter ID the hub is configured to trust, the camera method, the requested capability and the origin the mission
runs in (M4: a SIM worker never serves a LIVE request, and the start says which origin it requires), and only when
the camera can be run at all: E01's task queue and the E05 attempt report are bound (`usable`). Which cameras serve
the request is decided at bind time from the device configuration's pairing (B1); the device is left to the
worker's best view selection among them, which reports the camera that carried the value (D09).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crew_health.camera.application.dispatch import DispatchStore
from crew_health.camera.application.link import Probe, WorkerLink
from crew_health.camera.application.ports import AdapterAssignment, MissionStatePort, View
from crew_health.kernel.storage import StorageSession

CAMERA_METHOD = "camera_estimate"  # contracts common.schema.json acquisition_method of a camera estimate


@runtime_checkable
class _Writable(Protocol):
    """A unit of work (as opposed to a read snapshot): it carries the command's correlation ID."""

    @property
    def correlation_id(self) -> str: ...
    @property
    def connection(self) -> StorageSession: ...


class CameraAssignments:
    def __init__(
        self,
        link: WorkerLink,
        store: DispatchStore,
        *,
        adapter_id: str,
        mission: MissionStatePort | None,
        usable: bool,
    ) -> None:
        self._link, self._store, self._adapter_id = link, store, adapter_id
        self._mission, self._usable = mission, usable

    def _serving(self, view: View) -> Probe | None:
        """The fresh probe of the trusted worker in the mission's origin; None when anything is unknown."""
        if not self._usable or self._mission is None:
            return None
        probe = self._link.fresh(view.now)
        mission = self._mission.current(view)
        if probe is None or mission is None or probe.adapter_id != self._adapter_id:
            return None
        return probe if probe.origin_mode == mission.mission_mode else None

    def ready_methods(self, view: View) -> frozenset[str] | None:
        probe = self._serving(view)
        return None if probe is None else probe.ready_methods

    def assign(
        self, view: View, *, capability_code: str, methods: tuple[str, ...]
    ) -> AdapterAssignment | None:
        if CAMERA_METHOD not in methods:
            return None
        probe = self._serving(view)
        if (
            probe is None
            or CAMERA_METHOD not in probe.ready_methods
            or capability_code not in probe.capability_codes
        ):
            return None
        if isinstance(view, _Writable):
            self._store.record(
                view.connection,
                correlation_id=view.correlation_id,
                capability_code=capability_code,
                adapter_id=self._adapter_id,
                at=view.now,
            )
        return AdapterAssignment(self._adapter_id, CAMERA_METHOD, None)
