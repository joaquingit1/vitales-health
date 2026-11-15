"""E07 scenario composition.

E05's real measurement router and SQLite store (its own test harness, with fakes only for the ports no
module on main provides yet) and a real E07 adapter process app (decoder, quality checks, file journal, HTTP
sender), driven over HTTP in both directions. The hub to worker start call is made by the scenario because
that protocol is E06's and not published yet (follow-up F3); the readiness bridge from the adapter's /health
to E05's AdapterReadinessPort is the same stand-in. Assets are SIM and labeled as such.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from importlib.resources import files
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
ASSETS = Path(__file__).resolve().parent / "assets"
for _path in (
    REPO / "hub" / "tests" / "measurement",
    REPO / "adapters" / "oximeter" / "src",
    REPO / "adapters" / "thermometer" / "src",
    REPO / "adapters" / "cuff" / "src",
    REPO / "simulators" / "clinical" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import measurement_harness as mh  # noqa: E402
from crew_health.kernel.testing import MemoryUnits  # noqa: E402
from crew_health.measurement.application.ports import AdapterAssignment  # noqa: E402
from crew_health_adapter_cuff import family as cuff  # noqa: E402
from crew_health_adapter_oximeter import family as oximeter  # noqa: E402
from crew_health_adapter_thermometer import family as thermometer  # noqa: E402
from crew_health_device.adapters.file_journal import FileJournal  # noqa: E402
from crew_health_device.adapters.http_hub import HttpHubAttempts  # noqa: E402
from crew_health_device.adapters.profile_file import load_document, load_profile_file  # noqa: E402
from crew_health_device.adapters.recorded_source import RecordedFrameSource, load_recording_file  # noqa: E402
from crew_health_device.adapters.twin_source import Family, twin_source  # noqa: E402
from crew_health_device.application.ports import FrameSource, HubAttempts, HubReply, TransportFailure  # noqa: E402
from crew_health_device.application.profiles import ProfileStore  # noqa: E402
from crew_health_device.application.settings import AdapterSettings, load_settings  # noqa: E402
from crew_health_device.domain.acquisition import OriginMode  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402

ADAPTER_ID = "adp_oximeter"
DEVICE_ID = "dev_kit_oximeter"
ADAPTER_TOKEN = "oximeter"
CONTROL_TOKEN = "control-token"
HUB_UNITS: dict[str, tuple[str, Decimal, Decimal]] = {
    "/min": ("frequency", Decimal(1), Decimal(0)),
    "%": ("ratio", Decimal(1), Decimal(0)),
    "Cel": ("temperature", Decimal(1), Decimal(0)),
    "mm[Hg]": ("pressure", Decimal(1), Decimal(0)),
}


class SteppedClock:
    """Adapter TimeSource: UTC instants and a monotonic counter that advance one step per read."""

    def __init__(self, start: datetime, step_ms: int) -> None:
        self.start = start
        self.step_ns = step_ms * 1_000_000
        self.ticks = 0

    def now(self) -> datetime:
        return self.start + timedelta(microseconds=self.ticks * self.step_ns // 1000)

    def monotonic_ns(self) -> int:
        self.ticks += 1
        return self.ticks * self.step_ns


def asset(name: str) -> dict[str, Any]:
    value = json.loads((ASSETS / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def validate(document: Mapping[str, object], schema: str) -> None:
    """Validate against the schema shipped in the adapter runtime package, the file the process loads."""
    resource = files("crew_health_device").joinpath("schemas", schema + ".schema.json")
    Draft202012Validator(json.loads(resource.read_text(encoding="utf-8"))).validate(document)


def adapter_settings(name: str = "adapter-settings.json") -> AdapterSettings:
    return load_settings(load_document(ASSETS / name, 1048576))


def connected_oximeter_kit(tmp_path: Path) -> Path:
    """Scratch kit map: the kit oximeter, thermometer and cuff are connected devices (method `device`)."""

    def edit(document: dict[str, Any]) -> None:
        for instrument in document["instruments"]:
            if instrument["id"] in ("pulse_oximeter", "thermometer", "bp_cuff"):
                instrument["method"] = "device"

    return mh.scratch_kit_map(tmp_path, edit)


def build_device_hub(tmp_path: Path, monkeypatch: Any) -> mh.Hub:
    monkeypatch.setattr(mh, "MemoryUnits", lambda _table: MemoryUnits(HUB_UNITS))
    hub = mh.build_hub(tmp_path, kit_map=connected_oximeter_kit(tmp_path), mode="SIM", ready=None)
    mh.seed_crew(hub)
    hub.sessions.other(ADAPTER_TOKEN, "adapter", "adapter", ADAPTER_ID)
    hub.assignments.by_method.clear()
    hub.assignments.by_method["device"] = AdapterAssignment(ADAPTER_ID, "device", DEVICE_ID)
    return hub


@dataclass
class AdapterProcess:
    client: TestClient
    journal: FileJournal
    profiles: ProfileStore
    clock: SteppedClock

    def health(self) -> dict[str, Any]:
        body = self.client.get("/health").json()
        assert isinstance(body, dict)
        return body


class LosesFirstResponse:
    """The hub records the attempt but its reply never reaches the adapter (response loss, R10)."""

    def __init__(self, inner: HubAttempts) -> None:
        self.inner = inner
        self.calls = 0

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        self.calls += 1
        reply = self.inner.post_attempt(request_id, idempotency_key, body)
        return TransportFailure("timeout") if self.calls == 1 else reply


class HubDown:
    def __init__(self) -> None:
        self.calls = 0

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        self.calls += 1
        return TransportFailure("transport")


def hub_sender(hub: mh.Hub, token: str = ADAPTER_TOKEN) -> HttpHubAttempts:
    return HttpHubAttempts(hub.client, base_url="http://testserver", token=token, timeout_ms=1000)


def _compose(
    tmp_path: Path,
    sender: HubAttempts,
    *,
    family: str,
    profile_path: Path | None,
    adapter_id: str,
    settings: AdapterSettings,
    clock: SteppedClock,
    source_factory: Callable[[], FrameSource],
    origin: OriginMode,
    simulated_device: bool = False,
) -> AdapterProcess:
    build = import_module(f"crew_health_adapter_{family}.family").build
    path = profile_path or ASSETS / f"{family}.profile.json"
    profiles = ProfileStore(lambda: load_profile_file(path, settings.profile_max_bytes))
    journal = FileJournal(
        tmp_path / f"journal-{family}",
        max_entries=settings.journal_max_entries,
        max_bytes=settings.journal_max_bytes,
    )
    app: FastAPI = build(
        profile_store=profiles,
        source_factory=source_factory,
        journal=journal,
        clock=clock,
        settings=settings,
        adapter_id=adapter_id,
        token=CONTROL_TOKEN,
        hub=sender,
        source_origin=origin,
        wait=lambda _seconds: False,
        simulated_device=simulated_device,
    )
    return AdapterProcess(TestClient(app), journal, profiles, clock)


def adapter_process(
    tmp_path: Path,
    sender: HubAttempts,
    recording: str,
    *,
    family: str = "oximeter",
    profile_path: Path | None = None,
    adapter_id: str = ADAPTER_ID,
) -> AdapterProcess:
    """One adapter process as its entry point composes it, with a recorded source instead of the radio."""
    settings = adapter_settings()
    document = load_recording_file(ASSETS / recording, settings.profile_max_bytes)
    return _compose(
        tmp_path,
        sender,
        family=family,
        profile_path=profile_path,
        adapter_id=adapter_id,
        settings=settings,
        clock=SteppedClock(datetime(2026, 11, 13, 9, 33, tzinfo=UTC), 250),
        source_factory=lambda: RecordedFrameSource(document, max_frames=settings.max_frames),
        origin=RecordedFrameSource(document, max_frames=settings.max_frames).origin,
    )


# The twin's own clock advances one second per notification, as the running adapter's twin does, so its device
# timestamps advance like a real device's; delivery is paced faster so the scenarios stay quick.
TWIN_INTERVAL_S = Decimal(1)
TWIN_PACE_S = Decimal("0.002")
TWIN_START = datetime(2026, 11, 13, 9, 33, tzinfo=UTC)


def twin_process(
    tmp_path: Path,
    sender: HubAttempts,
    spec: str,
    *,
    family: Family = "oximeter",
    adapter_id: str = ADAPTER_ID,
) -> AdapterProcess:
    """The adapter process its entry point builds for `--source sim:<scenario>:<seed>`: the simulated twin's
    GATT notifications behind the real BLE transport, decoder, checks, journal and HTTP sender."""
    settings = adapter_settings("adapter-settings.twin.json")
    clock = SteppedClock(TWIN_START, 250)
    uuids = {
        "oximeter": {
            oximeter.SPOT_CHECK: oximeter.SPOT_CHECK_UUID,
            oximeter.CONTINUOUS: oximeter.CONTINUOUS_UUID,
        },
        "cuff": {cuff.MEASUREMENT: cuff.MEASUREMENT_UUID},
        "thermometer": {thermometer.TEMPERATURE_MEASUREMENT: thermometer.TEMPERATURE_MEASUREMENT_UUID},
    }[family]
    factory = twin_source(
        family,
        spec,
        uuids,
        max_queue=settings.max_frames,
        start=lambda: TWIN_START,
        interval_s=TWIN_INTERVAL_S,
        pace_s=TWIN_PACE_S,
    )
    return _compose(
        tmp_path,
        sender,
        family=family,
        profile_path=None,
        adapter_id=adapter_id,
        settings=settings,
        clock=clock,
        source_factory=factory,
        origin="SIM",
        simulated_device=True,
    )


def readiness_from(process: AdapterProcess) -> frozenset[str]:
    """Stand-in for E05's AdapterReadinessPort provider (E06 protocol, F3).

    A reachable adapter whose link is not lost is ready for method `device`; a lost link is not."""
    state = process.health()["state"]
    return frozenset({"device"}) if state in ("connected", "quiet") else frozenset()


def request_body(capability: str) -> dict[str, object]:
    body = mh.pulse_body()
    body["capability_code"] = capability
    return body


def spo2_body() -> dict[str, object]:
    return request_body("spo2")


def started_request(hub: mh.Hub, body: dict[str, object], key: str) -> str:
    created = hub.client.post(
        "/v1/measurement-requests", json=body, headers=mh.headers("mara", "idem-key-" + key + "-create")
    )
    assert created.status_code == 201, created.json()
    request_id = str(created.json()["request_id"])
    mh.act(hub, "mara", request_id, "submit", 1, "idem-key-" + key + "-submit")
    mh.act(hub, "mara", request_id, "accept", 2, "idem-key-" + key + "-accept")
    mh.act(hub, "mara", request_id, "start", 3, "idem-key-" + key + "-start")
    return request_id


def binding_for(
    request_id: str,
    capability: str,
    *,
    patient: str = "mara",
    generation: int = 1,
    device_id: str = DEVICE_ID,
) -> dict[str, object]:
    """What E06's start call will carry (structural copy of R-E05-2); the scenario plays the dispatcher."""
    return {
        "request_id": request_id,
        "acquisition_id": f"{request_id}.g{generation}",
        "generation": generation,
        "patient_id": patient,
        "episode_id": str(mh.pulse_body()["episode_id"]),
        "capability_code": capability,
        "method": "device",
        "device_id": device_id,
        "origin_mode": "SIM",
        "reserved_sequence": 1,
        "expected_user_index": None,
    }


def start_acquisition(process: AdapterProcess, binding: dict[str, object]) -> str:
    response = process.client.post(
        "/acquisitions", json=binding, headers={"Authorization": "Bearer " + CONTROL_TOKEN}
    )
    assert response.status_code == 200, response.json()
    reply = response.json()["reply"]
    assert isinstance(reply, str)
    return reply


def stored_request(hub: mh.Hub, request_id: str) -> dict[str, Any]:
    body = hub.client.get(f"/v1/measurement-requests/{request_id}", headers=mh.headers("mara")).json()
    assert isinstance(body, dict)
    return body


def capability(hub: mh.Hub, code: str) -> dict[str, Any]:
    response = hub.client.get("/v1/capabilities", headers=mh.headers("mara"))
    assert response.status_code == 200, response.json()
    items = response.json()
    rows = items["items"] if isinstance(items, dict) else items
    (row,) = [r for r in rows if r["code"] == code]
    assert isinstance(row, dict)
    return row
