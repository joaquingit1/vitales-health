"""The start-up verifier and the activation guard read the ledger in bounded keyset pages and never stop the hub: a
ledger past their bounds, or a row that breaks the chain, makes only that partition unknown (E32 5.1, AGENTS rule 3)."""

from __future__ import annotations

import copy
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease
from crew_health.kernel.uow import Actor
from crew_health.supplies.adapters.sqlite_ledger import SqliteLedger
from crew_health.supplies.adapters.verifier import verify_ledger
from crew_health.supplies.application.context import Corrupt
from crew_health.supplies.application.release_index import TOO_LARGE, SuppliesActivationGuard
from crew_health.supplies.domain.model import Partition

from .harness import (
    SUPPLIES_VALUES,
    Settings,
    Station,
    build_station,
    crew_session,
    document,
    example,
    headers,
    limits,
    lunar_mission,
    use_body,
)

USES = "/v1/supplies/eye_rinse_water/uses"


def rinse_stock(station: Station) -> dict[str, object]:
    response = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"})
    assert response.status_code == 200, response.text
    entry = next(e for e in response.json()["supplies"] if e["stock"]["supply_id"] == "eye_rinse_water")
    stock = entry["stock"]
    assert isinstance(stock, dict)
    return stock


def used_in_sim_and_live(tmp_path: Path, settings: Settings) -> Station:
    station = build_station(tmp_path, settings=settings)
    station.sessions.by_token["crew"] = crew_session()
    assert station.client.post(USES, headers=headers(key="k-sim-0001"), json=use_body(1)).status_code == 201
    station.mission.facts = lunar_mission("LIVE")
    assert station.client.post(USES, headers=headers(key="k-live-001"), json=use_body(1)).status_code == 201
    return station


@pytest.mark.trace("B1", "E32 5.1", "AGENTS rule 3")
def test_restart_with_more_partitions_than_max_supplies_starts(tmp_path: Path) -> None:
    values = dict(SUPPLIES_VALUES)
    values["supplies.max_supplies"] = 1
    used_in_sim_and_live(tmp_path, Settings(values))
    restarted = build_station(tmp_path, settings=Settings(values), mission=lunar_mission("LIVE"))
    restarted.sessions.by_token["crew"] = crew_session()
    assert rinse_stock(restarted)["used"] == 1
    again = restarted.client.post(USES, headers=headers(key="k-live-002"), json=use_body(1))
    assert again.status_code == 201


@pytest.mark.trace("B1", "E32 5.1", "fail safe")
def test_pairs_past_the_verifier_page_bound_are_unknown_not_a_failure(tmp_path: Path) -> None:
    station = used_in_sim_and_live(tmp_path, Settings())
    with station.db.snapshot() as view:
        found = verify_ledger(view, SqliteLedger(), page_rows=1, batch_rows=10, max_batches=10, max_pages=1)
    assert found.broken == frozenset() and found.unverified_after is not None
    corrupt = Corrupt()
    corrupt.mark(found.broken, found.unverified_after)
    live = Partition("mission_1", "LIVE", "lunar_surface", "lunar_surface")
    sim = Partition("mission_1", "SIM", "lunar_surface", "lunar_surface")
    assert corrupt.has(live, "eye_rinse_water") is False  # read on the first page and verified
    assert corrupt.has(sim, "eye_rinse_water") is True  # past the bound: unknown


@pytest.mark.trace("B1", "S-A3", "fail safe")
def test_activation_past_the_guard_page_bound_is_refused_with_a_finding(tmp_path: Path) -> None:
    station = used_in_sim_and_live(tmp_path, Settings())
    station.mission.facts = lunar_mission("SIM")
    small = replace(limits(), page_rows=1, verify_max_batches=1)
    guard = SuppliesActivationGuard(lambda: SqliteLedger(), lambda: station.mission, lambda: small)
    kit = example("kit-map.example.json")
    candidate = CandidateRelease("release_9", {ConfigName.KIT_MAP: document(ConfigName.KIT_MAP, kit)})
    with station.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor-guard") as uow:
        findings = guard.check(uow, candidate)
    assert [f.message for f in findings] == [TOO_LARGE]
    roomy = replace(limits(), page_rows=1, verify_max_batches=10)
    guard = SuppliesActivationGuard(lambda: SqliteLedger(), lambda: station.mission, lambda: roomy)
    below = copy.deepcopy(kit)
    assert isinstance(below, dict)
    for row in below["stock"]["lunar_surface"]:
        if row["supply_id"] == "eye_rinse_water":
            row["quantity"] = 0
    candidate = CandidateRelease("release_9", {ConfigName.KIT_MAP: document(ConfigName.KIT_MAP, below)})
    with station.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor-guard") as uow:
        assert any("S-A3" in f.check_id for f in guard.check(uow, candidate))  # every page was read


@pytest.mark.trace("M1", "E32 5.1", "INV-S13")
def test_restart_with_a_stored_row_in_another_unit_marks_that_partition_unknown(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    station.sessions.by_token["crew"] = crew_session()
    assert station.client.post(USES, headers=headers(), json=use_body(1)).status_code == 201
    with sqlite3.connect(station.db.path) as connection:
        row = list(connection.execute("SELECT * FROM supplies_use").fetchone())
        row[0], row[6], row[8], row[9], row[19] = "sup_x", "{litre}", 2, "2", "api:x"
        connection.execute(f"INSERT INTO supplies_use VALUES ({','.join('?' * len(row))})", row)
    restarted = build_station(tmp_path)  # starts: the pair is marked broken, nothing raises out of bind
    restarted.sessions.by_token["crew"] = crew_session()
    unreadable = restarted.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"})
    assert unreadable.status_code == 500 and unreadable.json()["retryable"] is False  # unknown, never a guess
    refused = restarted.client.post(USES, headers=headers(key="after-restart-1"), json=use_body(1))
    assert refused.status_code == 500 and refused.json()["retryable"] is False
    restarted.mission.facts = lunar_mission("LIVE")
    assert rinse_stock(restarted)["quantity_now"] == 6  # another partition is untouched


@pytest.mark.trace("m12", "SYSTEM 4.2 module map", "R-SYS-6")
def test_the_unit_registry_does_not_take_the_name_reserved_for_stock(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    station.sessions.by_token["crew"] = crew_session()
    assert station.client.post(USES, headers=headers(), json=use_body(1)).status_code == 201
    tables = {r[0] for r in station.rows("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "supplies_stock" not in tables  # SYSTEM reserves it for the stock and custody edits table
    assert station.rows("SELECT supply_id, unit_ucum FROM supplies_unit_registry") == [
        ("eye_rinse_water", "{rinse}")
    ]
