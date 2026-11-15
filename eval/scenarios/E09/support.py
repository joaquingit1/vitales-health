"""E09 served-app harness: the E02 station (real create_app, SQLite, identity, consent, middleware,
command ledger, audit, task service) with the report module's private storage ports composed on top.

Composition note (PLAN P1, P7): no served hub composes a PrivateRecordCoordinator, PrivateStore or a live
vault yet, and E02's `PrivateKeyVault` is the UnavailableVault. The harness composes E01's REAL
PrivateCoordinator, PrivateReferences and TaskService over the hub database, with kernel.testing's
MemoryVault and a fault store as the vault and store, replacing E02's unavailable vault in this
harness only. They carry no cryptography: the encryption claim belongs to the E02 vault and is
not proven here."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for sub in ("privacy", "reports"):
    sys.path.insert(0, str(ROOT / "hub" / "tests" / sub))
sys.path.insert(0, str(Path(__file__).parent / "inputs"))

import real_station  # type: ignore[import-not-found]  # noqa: E402
from crew_health.app.assembly import BuildCatalog, create_app  # noqa: E402
from crew_health.app.registry import ModuleRegistry, Resolver  # noqa: E402
from crew_health.app.station import bootstrap_release  # noqa: E402
from crew_health.clock.application.clock import PersistentClocks  # noqa: E402
from crew_health.identity.domain.roster import ProvisionedPrincipal  # noqa: E402
from crew_health.kernel.clock import TimeInput  # noqa: E402
from crew_health.kernel.private import PrivateKeyVault, PrivateRecordCoordinator, PrivateStore  # noqa: E402
from crew_health.kernel.scheduler import HealthState  # noqa: E402
from crew_health.kernel.tasks import TaskQueue, TaskRunner  # noqa: E402
from crew_health.kernel.testing import MemoryClock, MemoryVault  # noqa: E402
from crew_health.platform.adapters.intents import IntentRepository  # noqa: E402
from crew_health.platform.adapters.ledger import CommandLedger  # noqa: E402
from crew_health.platform.adapters.private import PrivateReferences  # noqa: E402
from crew_health.platform.adapters.tasks import TaskRepository  # noqa: E402
from crew_health.platform.application.private import PrivateCoordinator  # noqa: E402
from crew_health.platform.application.tasks import TaskService  # noqa: E402
from crew_health.platform.testing.sqlite import database  # noqa: E402
from crew_health.reports.adapters.store import PrivateReader  # noqa: E402
from crew_health.reports.adapters.writer import ReportReferenceWriter  # noqa: E402
from e02_station import (  # type: ignore[import-not-found]  # noqa: E402
    CONTRACT,
    OPERATOR,
    SCHEMAS,
    SOURCE,
    START,
    E02Station,
    Running,
    _clocks,
    _sim_clock,
    credential,
    mission_clock,
    resources,
    seed_roster,
    serve,
    sim_bootstrap,
    sim_members,
    sim_station_principal,
    station_settings,
)

INPUTS = Path(__file__).parent / "inputs"


@dataclass
class E09Station(E02Station):
    store: real_station.FaultStore | None = None
    vault: MemoryVault | None = None
    extra_configure: Callable[[ModuleRegistry], None] | None = None

    def configure(self, registry: ModuleRegistry) -> None:
        super().configure(registry)
        db = self.database
        vault = MemoryVault(
            ids=db.ids,
            settings=db.settings,
            secrets={m.person_id: b"sim-" + m.person_id.encode() for m in sim_members()},
            authorize=lambda session, ref: session.person_id == ref.owner_person_id,
        )
        store = real_station.FaultStore(vault=vault, settings=db.settings)
        self.vault, self.store = vault, store
        providers = getattr(registry, "providers")  # noqa: B009  (replace E02's UnavailableVault here only)
        providers.pop(PrivateKeyVault, None)
        getattr(registry, "declarations")["ports"].pop(PrivateKeyVault, None)  # noqa: B009
        registry.provide(PrivateKeyVault, lambda _: vault)  # type: ignore[type-abstract]
        registry.provide(PrivateStore, lambda _: store)  # type: ignore[type-abstract]
        registry.provide(PrivateReader, lambda _: real_station.Reader(store, vault))  # type: ignore[type-abstract]

        def coordinator(resolver: Resolver) -> PrivateCoordinator:
            ledger = resolver.require(CommandLedger)
            return PrivateCoordinator(
                transactions=db,
                snapshots=db,
                store=store,
                vault=vault,
                writers={"report": ReportReferenceWriter()},
                ledger=ledger,
                references=PrivateReferences(),
                ids=db.ids,
                settings=db.settings,
                authorize=lambda session, owner: session.person_id == owner,
                intents=IntentRepository(ledger),
                health=resolver.require(HealthState),  # type: ignore[type-abstract]
                clock=db.clock,
            )

        registry.provide(
            PrivateRecordCoordinator,  # type: ignore[type-abstract]
            coordinator,
            requires=(CommandLedger, HealthState),
        )
        shared: list[TaskService] = []

        def tasks(resolver: Resolver) -> TaskService:
            """One task service for queue and runner. Its capture uses the station's corrected SIM source: the
            hub's own capture segment carries no usable correction in this harness (first-start segment)."""
            if not shared:
                declared = getattr(registry, "declarations")["tasks"]  # noqa: B009
                shared.append(
                    TaskService(
                        settings=db.settings,
                        repository=TaskRepository(),
                        ids=db.ids,
                        registrations=tuple(v for _, v in sorted(declared.items(), key=lambda p: str(p[0]))),
                        authorize=lambda view, session, task: session.actor == task.actor,
                        authorize_completion=lambda uow, task: task.actor.kind == "person",
                        capture=lambda at: TimeInput(at, "sim_source", "sim_segment", 1, "source_raw", "SIM"),
                        elapsed=resolver.require(PersistentClocks).elapsed,
                    )
                )
            return shared[0]

        if TaskQueue not in getattr(registry, "providers", {}):  # the served hub may bind them already
            registry.provide(TaskQueue, tasks, requires=(PersistentClocks,))  # type: ignore[type-abstract]
            registry.provide(TaskRunner, tasks, requires=(PersistentClocks,))  # type: ignore[type-abstract]
        if self.extra_configure is not None:
            self.extra_configure(registry)

    def _start(self):  # type: ignore[no-untyped-def]
        return create_app(
            database=self.database,
            build=self.build,
            contract_path=CONTRACT,
            schema_directory=SCHEMAS,
            configure=self.configure,
            bootstrap=partial(bootstrap_release, resources()),
        )


def station(directory: Path) -> E09Station:
    clock = MemoryClock(START, 1_000_000_000, "epoch_first")
    db = database(directory / "state", clock=clock)
    db.settings = station_settings()
    hub = E09Station(directory, db, clock, BuildCatalog.installed(SOURCE), _clocks(db), None)
    runtime = hub.app().state.runtime
    if not runtime.serving or runtime.resolver is None or runtime.supervisor is None:
        raise AssertionError("first start did not serve: " + str(runtime.fault_code))
    hub.clocks = runtime.resolver.require(PersistentClocks)
    _sim_clock(db, hub.clocks)
    with db.begin(actor=OPERATOR, correlation_id="sim_time_confirmed") as uow:
        hub.clocks.confirm_hub_time(
            uow, correction_id=db.ids.new("correction"), offset_ms=0, uncertainty_ms=0
        )
    runtime.supervisor.run_once()
    mission_clock(db)
    clock.at, clock.monotonic = clock.at + timedelta(minutes=1), clock.monotonic + 60_000_000_000
    return hub


def seed(running: Running) -> None:
    principals = (
        sim_station_principal(),
        ProvisionedPrincipal("operator_sim", "operator", "Console operator"),
    )
    seed_roster(running, sim_bootstrap(principals=principals))


__all__ = ["INPUTS", "E09Station", "Running", "credential", "seed", "serve", "sim_members", "station"]
