"""Blood pressure protocol identifiers and process composition."""

from collections.abc import Callable

from fastapi import FastAPI

from crew_health_adapter_cuff.bpm import BPMDecoder
from crew_health_device.api.app import build_app
from crew_health_device.application.capture import AcquisitionService
from crew_health_device.application.delivery import deliver_with_retry
from crew_health_device.application.health import HealthTracker, manifest
from crew_health_device.application.ports import FrameSource, HubAttempts, Journal, TimeSource
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.application.settings import AdapterSettings
from crew_health_device.domain.acquisition import OriginMode

SERVICE = "0x1810"
MEASUREMENT = "blood_pressure_measurement"
MEASUREMENT_UUID = "00002a35-0000-1000-8000-00805f9b34fb"


def build(
    *,
    profile_store: ProfileStore,
    source_factory: Callable[[], FrameSource],
    journal: Journal,
    clock: TimeSource,
    settings: AdapterSettings,
    adapter_id: str,
    token: str,
    hub: HubAttempts,
    source_origin: OriginMode,
    wait: Callable[[float], bool],
    simulated_device: bool = False,
) -> FastAPI:
    health = HealthTracker(clock, settings.health_quiet_ms)
    service = AcquisitionService(
        BPMDecoder(), profile_store, source_factory, journal, clock, settings, adapter_id, health=health
    )
    return build_app(
        service=service,
        health=health,
        token=token,
        adapter_id=adapter_id,
        family="cuff",
        manifest=lambda: manifest(
            profile_store.current(),
            family="cuff",
            service=SERVICE,
            characteristics=("0x2A35",),
            source_origin=source_origin,
            simulated_device=simulated_device,
            connected_state=health.state(),
        ),
        after_capture=lambda: deliver_with_retry(journal, hub, settings, wait),
        journal=journal,
        journal_limit=settings.journal_max_entries,
    )
