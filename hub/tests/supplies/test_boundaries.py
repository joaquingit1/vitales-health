"""Boundary rules at the provided ports and the release check: the bare kit-map unit word is the supply's unit in
with_added too (m5), a packed quantity past the exact cover range is refused before activation and never crashes a
read (m6), and a need missing from the pinned kit map is reported, never dropped (m8)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor
from crew_health.supplies.application import ports
from crew_health.supplies.application.ports import (
    PinnedSupplyMissing,
    StockReader,
    SupplyCoverPort,
    SupplyLedgerPort,
    SupplyNeed,
)
from crew_health.supplies.domain.config_checks import release_findings

from .harness import SOURCES, build_station, crew_session, example, release

WORD = Unit("rinse")  # the bare kit-map unit word
RINSE = Unit("{rinse}")
AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.mark.trace("m5", "E32 3 boundary unit", "AGENTS rule 8")
def test_with_added_takes_the_bare_kit_map_word_like_every_other_port(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    stock = station.resolver.require(StockReader).stock("lunar_surface")  # type: ignore[type-abstract]
    grown = ports.with_added(stock, [("eye_rinse_water", Quantity(Decimal(4), WORD))])
    assert grown["eye_rinse_water"].quantity_now == Quantity(Decimal(10), RINSE)
    cover = station.resolver.require(SupplyCoverPort)  # type: ignore[type-abstract]
    need = SupplyNeed("plan_eye_injury", "eye_rinse", "eye_rinse_water", Quantity(Decimal(1), WORD))
    trimmed = {k: v for k, v in stock.items() if k != "eye_rinse_water"}
    added = ports.with_added(trimmed, [("eye_rinse_water", Quantity(Decimal(4), WORD))])
    assert cover.uses_after_one("lunar_surface", [need], added) == {("eye_rinse", "eye_rinse_water"): 3}
    with pytest.raises(ports.StockUnitMismatch):
        ports.with_added(stock, [("eye_rinse_water", Quantity(Decimal(4), Unit("towel")))])


@pytest.mark.trace("m6", "E32-T-22", "AGENTS 3")
def test_a_packed_quantity_past_the_cover_range_is_refused_and_never_crashes_a_read(tmp_path: Path) -> None:
    kit = copy.deepcopy(example("kit-map.example.json"))
    assert isinstance(kit, dict)
    for row in kit["stock"]["lunar_surface"]:
        if row["supply_id"] == "eye_rinse_water":
            row["quantity"] = 10**20
    docs = {name: example(path) for name, path in SOURCES.items()}
    findings = release_findings(
        kit,
        docs[ConfigName.ONBOARD_PLANS],
        docs[ConfigName.WARNING_RULES],
        docs[ConfigName.UI_COPY],
        docs[ConfigName.SKILLS_CATALOG],
    )
    assert [(f.check_id, f.message) for f in findings] == [("S-DOC", "cover past 2^53 uses")]
    station = build_station(tmp_path, active=release("release_big", {ConfigName.KIT_MAP: kit}))
    station.sessions.by_token["crew"] = crew_session()
    shown = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"})
    assert shown.status_code == 200  # a release from before the bound shows unknown cover, never a raw 500
    rinse = next(e for e in shown.json()["supplies"] if e["stock"]["supply_id"] == "eye_rinse_water")
    assert rinse["cover_uses"] is None


@pytest.mark.trace("m8", "INV-S14", "AGENTS rule 2")
def test_a_need_missing_from_the_pinned_kit_map_is_reported_not_dropped(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    ledger = station.resolver.require(SupplyLedgerPort)  # type: ignore[type-abstract]
    needs = [
        SupplyNeed("plan_eye_injury", "eye_rinse", "eye_rinse_water", Quantity(Decimal(1), WORD)),
        SupplyNeed("plan_eye_injury", "eye_rinse", "not_in_the_kit_map", Quantity(Decimal(1), Unit("{x}"))),
    ]
    with pytest.raises(PinnedSupplyMissing) as raised:
        with station.db.begin(actor=Actor("person", "p"), correlation_id="cor-step") as uow:
            ledger.record_step_uses(
                uow,
                environment_id="lunar_surface",
                plan_run_id="run_1",
                step_id="eye_rinse",
                occurrence=1,
                pinned_release_id="release_1",
                uses=needs,
                actor=Actor("person", "p"),
                at=AT,
            )
    assert raised.value.code == "INVALID_VALUE" and raised.value.status == 422
    assert raised.value.field_errors[0].field == "/uses/1/supply_id"
    assert station.rows("SELECT count(*) FROM supplies_use") == [(0,)]
