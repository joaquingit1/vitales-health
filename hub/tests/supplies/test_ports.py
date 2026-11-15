"""The ports supplies provides and consumes, registered through E01's DeclarationRegistry with the real provider and
consumer modules from main (lesson 15), plus the structural kitmap copy compared with main's class (DECISIONS A1)."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.guidance import module as guidance_module
from crew_health.guidance.application.ports import Known, StockFact, StockFacts, Unknown
from crew_health.help import module as help_module
from crew_health.help.application.ports import HelpSessionReader
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor
from crew_health.kitmap.application import ports as kitmap_ports
from crew_health.roles import module as roles_module
from crew_health.roles.application.ports import SupplyAboardPort
from crew_health.supplies import module as supplies_module
from crew_health.supplies.application import ports
from crew_health.supplies.application.ports import (
    CoverPlanningPort,
    StepUseConflict,
    StockBelowZero,
    StockReader,
    SupplyCoverChanged,
    SupplyCoverFacts,
    SupplyCoverPort,
    SupplyInputInvalid,
    SupplyLedgerPort,
    SupplyNeed,
    SupplyPartition,
)

from .harness import Station, build_station, crew_session, headers, use_body

RINSE = Unit("{rinse}")
AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def station(tmp_path: Path) -> Iterator[Station]:
    built = build_station(tmp_path)
    built.sessions.by_token["crew"] = crew_session()
    yield built


@pytest.mark.trace("lesson 15", "DECISIONS A4", "DECISIONS A5")
def test_supplies_registers_under_each_consumers_own_port_class() -> None:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    guidance_module.register(registry)
    roles_module.register(registry)
    help_module.register(registry)
    supplies_module.register(registry)
    for port in (
        StockFacts,
        SupplyAboardPort,
        StockReader,
        SupplyCoverPort,
        SupplyLedgerPort,
        SupplyCoverFacts,
    ):
        assert port in registry.providers
    assert CoverPlanningPort in registry.providers
    assert HelpSessionReader in registry.providers  # the real help module provides what supplies consumes


@pytest.mark.trace("DECISIONS A1", "E27 4.1")
def test_the_kitmap_cover_port_copy_matches_mains_signatures() -> None:
    mine = inspect.signature(ports.CoverPlanningPort.cover_changed)
    theirs = inspect.signature(kitmap_ports.CoverPlanningPort.cover_changed)
    assert [(p.name, p.kind, p.annotation) for p in mine.parameters.values()] == [
        (p.name, p.kind, p.annotation) for p in theirs.parameters.values()
    ]
    assert mine.return_annotation == theirs.return_annotation
    for name in ("supply_id", "from_uses", "to_uses"):
        assert name in kitmap_ports.CoverChange.__dataclass_fields__
        assert name in ports.CoverChange.__dataclass_fields__
    assert set(kitmap_ports.KitMapContext.__dataclass_fields__) == {
        "snapshot",
        "release",
        "environment_id",
        "at",
    }


@pytest.mark.trace("DECISIONS A5", "guidance StockFacts")
def test_stock_facts_answer_known_or_unknown_never_a_default(station: Station) -> None:
    facts = station.resolver.require(StockFacts)  # type: ignore[type-abstract]
    with station.db.snapshot() as snapshot:
        answer = facts.stock(
            snapshot, None, {"eye_rinse_water": Quantity(Decimal(1), Unit("rinse")), "nothing_here": None}
        )
    assert answer["eye_rinse_water"] == Known(
        StockFact(True, Quantity(Decimal(6), RINSE), Quantity(Decimal(1), RINSE), True)
    )
    assert isinstance(answer["nothing_here"], Unknown)
    station.mission.facts = None
    with station.db.snapshot() as snapshot:
        assert isinstance(facts.stock(snapshot, None, {"eye_rinse_water": None})["eye_rinse_water"], Unknown)


@pytest.mark.trace("DECISIONS A5", "roles SupplyAboardPort")
def test_aboard_is_false_unless_known_aboard(station: Station) -> None:
    port = station.resolver.require(SupplyAboardPort)  # type: ignore[type-abstract]
    assert port.aboard(["eye_rinse_water", "eye_shield", "unknown"], "lunar_surface") == {
        "eye_rinse_water": True,
        "eye_shield": False,
        "unknown": False,
    }
    station.mission.facts = None
    assert port.aboard(["eye_rinse_water"], "lunar_surface") == {"eye_rinse_water": False}


def _need(step: str, supply: str, per_use: int | None, unit: str = "rinse") -> SupplyNeed:
    return SupplyNeed(
        "plan_eye_injury", step, supply, None if per_use is None else Quantity(Decimal(per_use), Unit(unit))
    )


@pytest.mark.trace("R28-20", "CONTRACTS 22 rule 6")
def test_uses_after_one_divides_the_remainder_by_the_largest_per_use(station: Station) -> None:
    stock = station.resolver.require(StockReader).stock("lunar_surface")  # type: ignore[type-abstract]
    assert stock["eye_rinse_water"].quantity_now == Quantity(Decimal(6), RINSE)
    cover = station.resolver.require(SupplyCoverPort)  # type: ignore[type-abstract]
    needs = [_need("eye_rinse", "eye_rinse_water", 1), _need("second_rinse", "eye_rinse_water", 2)]
    assert cover.uses_after_one("lunar_surface", needs, stock) == {
        ("eye_rinse", "eye_rinse_water"): 2,  # (6 - 1) // 2
        ("second_rinse", "eye_rinse_water"): 2,  # (6 - 2) // 2
    }
    assert cover.uses_after_one("lunar_surface", [_need("s", "eye_shield", 1, "shield")], stock) == {
        ("s", "eye_shield"): None
    }
    grown = ports.with_added(stock, [("eye_rinse_water", Quantity(Decimal(4), RINSE))])
    assert cover.uses_after_one("lunar_surface", needs[:1], grown) == {("eye_rinse", "eye_rinse_water"): 9}


@pytest.mark.trace("E32-T-07", "INV-S10", "R28-20")
def test_step_uses_are_all_or_nothing_and_reentrant_by_occurrence(station: Station) -> None:
    ledger = station.resolver.require(SupplyLedgerPort)  # type: ignore[type-abstract]
    seen: list[SupplyCoverChanged] = []
    station.db.bus.subscribe(SupplyCoverChanged, lambda uow, e: seen.append(e), name="test.cover")

    def run(occurrence: int, uses: list[SupplyNeed]) -> tuple[str, ...]:
        with station.db.begin(actor=Actor("person", "person_a"), correlation_id="cor-step") as uow:
            return tuple(
                ledger.record_step_uses(
                    uow,
                    environment_id="lunar_surface",
                    plan_run_id="run_1",
                    step_id="eye_rinse",
                    occurrence=occurrence,
                    pinned_release_id="release_1",
                    uses=uses,
                    actor=Actor("person", "person_a"),
                    at=AT,
                )
            )

    short = [_need("eye_rinse", "eye_rinse_water", 1), _need("eye_rinse", "towels", 21, "towel")]
    with pytest.raises(StockBelowZero):
        run(1, short)
    assert station.rows("SELECT count(*) FROM supplies_use") == [(0,)] and not seen
    uses = [_need("eye_rinse", "eye_rinse_water", 1), _need("eye_rinse", "eye_wash_set", 1, "set")]
    first = run(1, uses)
    assert len(first) == 1  # the eye wash set is kept, not used up
    assert run(1, uses) == first  # the identical occurrence writes nothing new
    with pytest.raises(StepUseConflict):  # the same occurrence with another amount is not a replay
        run(1, [_need("eye_rinse", "eye_rinse_water", 3)])
    assert len(run(2, [_need("eye_rinse", "eye_rinse_water", 1)])) == 1
    assert station.rows("SELECT command_key FROM supplies_use ORDER BY partition_seq") == [
        ("step:run_1:eye_rinse:1:eye_rinse_water",),
        ("step:run_1:eye_rinse:2:eye_rinse_water",),
    ]
    assert seen[-1].partition == SupplyPartition("mission_1", "lunar_surface", "lunar_surface", "SIM")
    row = next(r for r in seen[-1].rows if r.supply_id == "eye_rinse_water")
    assert row.cover_uses == 4 and row.quantity_text == "4 rinse"
    assert row.stock_revision == (6 + 2) * 10**12  # packed plus used


@pytest.mark.trace("SYSTEM 8.1", "E24 rows")
def test_cover_facts_page_in_supply_order_after_the_cursor(station: Station) -> None:
    station.client.post("/v1/supplies/eye_rinse_water/uses", headers=headers(), json=use_body(1))
    facts = station.resolver.require(SupplyCoverFacts)  # type: ignore[type-abstract]
    where = SupplyPartition("mission_1", "lunar_surface", "lunar_surface", "SIM")
    with station.db.snapshot() as view:
        first = facts.rows(view, partition=where, after_supply_id=None, limit=2)
        rest = facts.rows(view, partition=where, after_supply_id=first[-1].supply_id, limit=100)
    ids = [f.supply_id for f in first + rest]
    assert ids == sorted(ids) and len(ids) == 6
    rinse = next(f for f in first + rest if f.supply_id == "eye_rinse_water")
    assert rinse.cover_uses == 5 and rinse.last_use_id is not None and rinse.release_id == "release_1"
    assert rinse.source_refs[-1] == "supply_use:" + rinse.last_use_id


@dataclass(frozen=True)
class _Release:
    release_id: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.trace("E32-T-21", "E32-T-20", "R59", "V92", "HZ4")
def test_the_station_what_if_moves_eye_shield_cover_from_null_to_two_and_writes_nothing(
    station: Station,
) -> None:
    planning = station.resolver.require(CoverPlanningPort)  # type: ignore[type-abstract]
    before = _sha(station.db.path)
    with station.db.snapshot() as view:
        context = kitmap_ports.KitMapContext(view, _Release("release_1"), "station_leo", AT)  # type: ignore[arg-type]
        additions = [kitmap_ports.SupplyAddition("eye_shield", Quantity(Decimal(2), Unit("shield")))]
        changes = planning.cover_changed(context, add_supplies=additions)
    assert [(c.supply_id, c.from_uses, c.to_uses) for c in changes] == [("eye_shield", None, 2)]
    assert _sha(station.db.path) == before
    assert station.rows("SELECT count(*) FROM events_outbox") == [(0,)]


@pytest.mark.trace("E32-T-22", "V92")
def test_the_what_if_refuses_repeats_bounds_and_other_releases(station: Station) -> None:
    planning = station.resolver.require(CoverPlanningPort)  # type: ignore[type-abstract]
    shield = kitmap_ports.SupplyAddition("eye_shield", Quantity(Decimal(2), Unit("shield")))
    with station.db.snapshot() as view:
        for release_id, additions in (
            ("release_1", [shield, shield]),
            (
                "release_1",
                [kitmap_ports.SupplyAddition("eye_shield", Quantity(Decimal(10**7), Unit("shield")))],
            ),
            ("release_0", [shield]),
        ):
            context = kitmap_ports.KitMapContext(view, _Release(release_id), "station_leo", AT)  # type: ignore[arg-type]
            with pytest.raises(SupplyInputInvalid):
                planning.cover_changed(context, add_supplies=additions)
