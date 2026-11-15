"""Fixtures for the E23 mission tests on kernel fakes only (no SQLite, no HTTP: those wait on E01 T2 and E02).

Configuration comes from the package example documents. The store below is an in-memory fake of the
MissionStore protocol; its commit and rollback are emulated by `Station.run`, since the kernel fake unit of work
owns only its own state.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.met import MissionClockVersion
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryMissionClock,
    MemoryRelease,
    MemorySettings,
    MemoryStorage,
    MemoryUowFactory,
)
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.mission.adapters.config_reader import MissionConfig, lists_for, load_mission_config
from crew_health.mission.application.queries import MissionIdentity, MissionState
from crew_health.mission.application.use_cases import Caller, Services
from crew_health.mission.domain.events import EventRecord
from crew_health.mission.domain.overlay import OverlayRecord
from crew_health.mission.domain.profile import ProfileRecord
from crew_health.mission.domain.state_facts import FactState

ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "docs" / "product"
EXAMPLES = PRODUCT / "api" / "examples" / "config"
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
START = datetime(2026, 1, 20, 9, 10, 16, tzinfo=UTC)  # mission day 20, as in CONTRACTS 12's MET example
ROSTER = frozenset({"per_a", "per_b", "per_c"})


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e23: E23 environment profile and habitat sensors")


def example(name: str) -> bytes:
    value = json.loads((EXAMPLES / f"{name}.example.json").read_text(encoding="utf-8"))["value"]
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def view_of(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    import hashlib

    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def documents() -> dict[ConfigName, bytes]:
    return {
        ConfigName.ENVIRONMENT_PROFILES: example("environment-profiles"),
        ConfigName.SENSOR_CATALOG: example("sensor-catalog"),
        ConfigName.SENSOR_LIMITS: example("sensor-limits"),
        ConfigName.WARNING_RULES: example("warning-rules"),
        ConfigName.ENVIRONMENT_HAZARDS: (PRODUCT / "environment-hazards.json").read_bytes(),
        ConfigName.ROUTINE_SCHEDULE: (PRODUCT / "routine-schedule.json").read_bytes(),
    }


def make_release(release_id: str, raw: Mapping[ConfigName, bytes]) -> MemoryRelease:
    return MemoryRelease(release_id, {n: view_of(n, b) for n, b in raw.items()})


def edited(
    raw: Mapping[ConfigName, bytes], name: ConfigName, edit: Callable[[dict[str, Any]], None]
) -> dict[ConfigName, bytes]:
    document = json.loads(raw[name])
    edit(document)
    return {**raw, name: json.dumps(document, ensure_ascii=False).encode("utf-8")}


class CounterIds:
    """Test fake of IdFactory: unbounded and deterministic."""

    def __init__(self) -> None:
        self.count = 0

    def new(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}_{self.count:05d}"

    def derived(self, prefix: str, *parts: str) -> str:
        return prefix + "_" + "_".join(parts)


@dataclass
class FakeStore:
    """In-memory MissionStore. `fail_after` raises on the nth write to inject a crash inside a command."""

    identity_row: MissionIdentity | None = None
    profile_rows: dict[str, ProfileRecord] = field(default_factory=dict)
    overlay_rows: dict[str, OverlayRecord] = field(default_factory=dict)
    event_rows: list[EventRecord] = field(default_factory=list)
    fact_rows: dict[str, FactState] = field(default_factory=dict)
    writes: int = 0
    fail_after: int | None = None

    def _write(self) -> None:
        self.writes += 1
        if self.fail_after is not None and self.writes > self.fail_after:
            raise RuntimeError("injected storage failure")

    def identity(self, view: object) -> MissionIdentity | None:
        return self.identity_row

    def profile(self, view: object, profile_id: str) -> ProfileRecord | None:
        return self.profile_rows.get(profile_id)

    def profiles(self, view: object) -> Sequence[ProfileRecord]:
        return tuple(self.profile_rows.values())

    def overlay(self, view: object, overlay_id: str) -> OverlayRecord | None:
        return self.overlay_rows.get(overlay_id)

    def overlays_of(self, view: object, person_ids: Collection[str]) -> Sequence[OverlayRecord]:
        return tuple(r for r in self.overlay_rows.values() if r.person_id in person_ids)

    def events(
        self, view: object, *, since: datetime, after_event_id: str | None, limit: int
    ) -> Sequence[EventRecord]:
        return tuple(e for e in self.event_rows if e.occurred_at >= since)[:limit]

    def events_before(
        self, view: object, *, before_event_id: str | None, limit: int
    ) -> Sequence[EventRecord]:
        return tuple(reversed(self.event_rows))[:limit]

    def fact_states(self, view: object) -> Mapping[str, FactState]:
        return dict(self.fact_rows)

    def create_identity(self, uow: UnitOfWork, identity: MissionIdentity) -> None:
        self._write()
        if self.identity_row is not None:
            raise RuntimeError("single row")
        self.identity_row = identity

    def save_profile(self, uow: UnitOfWork, record: ProfileRecord) -> None:
        self._write()
        self.profile_rows[record.profile_id] = record

    def save_overlay(self, uow: UnitOfWork, record: OverlayRecord) -> None:
        self._write()
        self.overlay_rows[record.overlay_id] = record

    def add_event(self, uow: UnitOfWork, record: EventRecord) -> None:
        self._write()
        self.event_rows.append(record)

    def save_fact(self, uow: UnitOfWork, fact: FactState) -> None:
        self._write()
        self.fact_rows[fact.fact] = fact

    def copy_rows(self) -> tuple[Any, ...]:
        return (
            self.identity_row,
            dict(self.profile_rows),
            dict(self.overlay_rows),
            list(self.event_rows),
            dict(self.fact_rows),
        )

    def restore(self, rows: tuple[Any, ...]) -> None:
        self.identity_row, self.profile_rows, self.overlay_rows, self.event_rows, self.fact_rows = rows


class Spy:
    """ActivationParticipant fake that records its calls and what the store held when it was called."""

    def __init__(self, station: Station) -> None:
        self.station = station
        self.calls: list[tuple[str, int]] = []
        self.fail = False

    def on_activated(self, uow: UnitOfWork, profile: Any) -> None:
        self.calls.append((profile.environment_id.root, len(uow.state.events)))  # type: ignore[attr-defined]
        if self.fail:
            raise RuntimeError("participant failure")


@dataclass
class Station:
    factory: MemoryUowFactory
    store: FakeStore
    services: Services
    clock: MemoryClock
    spy: Spy
    release: MemoryRelease
    config: MissionConfig
    state: MissionState
    raw: dict[ConfigName, bytes]

    @contextmanager
    def run(self, actor: Actor, *, release: MemoryRelease | None = None) -> Iterator[UnitOfWork]:
        """One unit of work. A failure restores the fake store, as a real transaction would."""
        saved = self.store.copy_rows()
        if release is not None:
            self.factory.release = release
        try:
            with self.factory.begin(actor=actor, correlation_id="cor_test") as uow:
                yield uow
        except BaseException:
            self.store.restore(saved)
            raise

    def advance(self, **delta: float) -> None:
        self.clock.at = self.clock.at + timedelta(**delta)


SETTINGS = {
    "storage.max_rows": 100000,
    "storage.max_statement_bytes": 1000,
    "event_bus.max_events": 64,
    "events.max_subscribers": 8,
    "scheduler.max_jobs": 1000,
    "registry.max_hooks": 8,
    "commands.cache_entries": 8,
    "commands.max_bytes": 1024,
    "clock.max_uncertainty_ms": 100,
    "clock.history_page_size": 10,
    "clock.future_tolerance_ms": 100,
    "clock.hlc_future_ms": 100,
    "clock.hlc_logical_max": 100,
    "tasks.max_pending": 4,
    "tasks.claim_batch": 2,
    "tasks.max_attempts": 2,
    "tasks.lease_ms": 1000,
    "tasks.max_input_bytes": 1000,
    "tasks.max_result_bytes": 1000,
    "evaluation.page_size": 4,
    "evaluation.retry_max": 2,
    "sync.pending_max": 8,
    "sync.page_entries": 4,
    "private.max_versions": 8,
    "private.max_entry_bytes": 20000000,
    "private.recovery_batch": 4,
}


def build_station(raw: Mapping[ConfigName, bytes] | None = None) -> Station:
    documents_raw = dict(documents() if raw is None else raw)
    release = make_release("rel_test_0001", documents_raw)
    specs = [
        SettingSpec(k, "integer", None, Decimal(1), Decimal(v), None, "test profile")
        for k, v in SETTINGS.items()
    ]
    settings = MemorySettings(specs, SETTINGS)
    clock = MemoryClock(START, 0, "epoch")
    storage = MemoryStorage(settings=settings, responses={}, results={})
    factory = MemoryUowFactory(
        clock=clock,
        ids=CounterIds(),
        release=release,
        settings=settings,
        connection=storage,
        node_id="node",
        incarnation="inc",
    )
    store = FakeStore()
    station = Station.__new__(Station)
    station.factory, station.store, station.clock, station.release, station.raw = (
        factory,
        store,
        clock,
        release,
        documents_raw,
    )
    station.spy = Spy(station)
    station.config = load_mission_config(lambda name: release.document(name).raw)
    mission_clock = MemoryMissionClock(
        (MissionClockVersion("v1", 1, EPOCH, EPOCH, None, "flight day {day}"),)
    )

    def config_of(handle: Any) -> MissionConfig:
        return load_mission_config(lambda name: handle.document(name).raw)

    def lists_of(uow: UnitOfWork, config: MissionConfig, env: str) -> Any:
        return lists_for(lambda name: uow.release.document(name).raw, config, env, ("ch_demo",))

    def day_end(instant: datetime) -> datetime:
        number = mission_clock.mission_day(instant)
        return mission_clock.day_bounds(number, version="v1")[1]

    station.services = Services(
        store=store,
        ids=factory.ids,
        config_of=config_of,
        lists_of=lists_of,
        roster=lambda uow: ROSTER,
        participants=lambda: (station.spy,),
        day_end=day_end,
    )
    station.state = MissionState(
        store,
        factory,
        {},
        mission_clock,
        lambda record: station.config.profile(record.environment_id).day_label,  # type: ignore[union-attr]
    )
    return station


@pytest.fixture
def station() -> Station:
    return build_station()


def person(person_id: str, *roles: str) -> Caller:
    return Caller(Actor("person", person_id), person_id, frozenset(roles))


def operator() -> Caller:
    return Caller(Actor("operator", "op_1"), None, frozenset({"operator"}))


def ground() -> Caller:
    return Caller(Actor("ground", "gnd_1"), None, frozenset({"ground"}))


def station_session() -> Caller:
    return Caller(Actor("station", "stn_1"), None, frozenset({"station"}))


def _publish() -> None:
    helpers = types.ModuleType("e23m_helpers")
    for name in (
        "START",
        "EPOCH",
        "ROSTER",
        "ROOT",
        "PRODUCT",
        "Station",
        "build_station",
        "documents",
        "edited",
        "make_release",
        "person",
        "operator",
        "ground",
        "station_session",
        "FakeStore",
        "CounterIds",
    ):
        setattr(helpers, name, globals()[name])
    sys.modules["e23m_helpers"] = helpers


_publish()
