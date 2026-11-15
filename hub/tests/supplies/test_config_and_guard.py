"""Configuration edits change behavior with no code change (E32 8.5, E32-T-30), the activation guard keeps stock from
going negative (S-A1 to S-A3, E32-T-17), and a broken ledger chain is shown as unknown, never repaired (E32 5.1)."""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease
from crew_health.kernel.uow import Actor
from crew_health.supplies.adapters.sqlite_ledger import SqliteLedger
from crew_health.supplies.application.release_index import SuppliesActivationGuard, SuppliesReleaseCheck

from .harness import (
    EXAMPLES,
    SOURCES,
    Station,
    build_station,
    crew_session,
    document,
    example,
    headers,
    limits,
    release,
    use_body,
)


def scratch_release(directory: Path, release_id: str) -> object:
    """A release read from a scratch copy of the package example documents (wrapped ones keep their wrapper)."""
    values: dict[ConfigName, object] = {}
    for name, relative in SOURCES.items():
        data = json.loads((directory / relative).read_text(encoding="utf-8"))
        values[name] = data["value"] if isinstance(data, dict) and "value" in data else data
    return release(release_id, values)


def copy_examples(directory: Path) -> Path:
    for relative in SOURCES.values():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLES / relative, target)
    return directory


def edit(directory: Path, relative: str, change: object) -> None:
    path = directory / relative
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data["value"] if "value" in data and relative.startswith("config/") else data
    assert callable(change)
    change(value)
    path.write_text(json.dumps(data), encoding="utf-8")


def rinse(station: Station) -> dict[str, object]:
    body = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"}).json()
    return next(e for e in body["supplies"] if e["stock"]["supply_id"] == "eye_rinse_water")


@pytest.mark.trace("E32-T-30", "charter 1.1", "V75")
def test_edits_to_a_scratch_configuration_change_words_threshold_stock_and_applicability(
    tmp_path: Path,
) -> None:
    config = copy_examples(tmp_path / "config_copy")
    station = build_station(tmp_path / "db", active=scratch_release(config, "release_1"))  # type: ignore[arg-type]
    station.sessions.by_token["crew"] = crew_session()
    assert rinse(station)["cover_text"] == "cover for 6 uses" and rinse(station)["low"] is False

    edit(config, "config/ui-copy.example.json", lambda v: v["supply_words"].update(cover="{n} rinses left"))
    station.set_release(scratch_release(config, "release_2"))  # type: ignore[arg-type]
    assert rinse(station)["cover_text"] == "6 rinses left"

    def threshold(v: dict[str, object]) -> None:
        for definition in v["definitions"]:  # type: ignore[attr-defined]
            if definition["id"] == "supply_cover_low":
                definition["condition"]["below_uses"] = 7

    edit(config, "config/warning-rules.example.json", threshold)
    station.set_release(scratch_release(config, "release_3"))  # type: ignore[arg-type]
    assert rinse(station)["low"] is True

    def restock(v: dict[str, object]) -> None:
        for row in v["stock"]["lunar_surface"]:  # type: ignore[index]
            if row["supply_id"] == "eye_rinse_water":
                row["quantity"] = 10

    edit(config, "kit-map.example.json", restock)
    station.set_release(scratch_release(config, "release_4"))  # type: ignore[arg-type]
    assert rinse(station)["cover_uses"] == 10 and rinse(station)["low"] is False

    def not_on_the_moon(v: dict[str, object]) -> None:
        for plan in v["plans"]:  # type: ignore[attr-defined]
            if plan["plan_id"] == "plan_eye_injury":
                plan["environments"] = [e for e in plan["environments"] if e != "lunar_surface"]

    edit(config, "config/onboard-plans.example.json", not_on_the_moon)
    station.set_release(scratch_release(config, "release_5"))  # type: ignore[arg-type]
    assert rinse(station)["used_by"] == [] and rinse(station)["cover_text"] == "no plan uses it"


def candidate(kit: object) -> CandidateRelease:
    return CandidateRelease("release_9", {ConfigName.KIT_MAP: document(ConfigName.KIT_MAP, kit)})


def guard_findings(station: Station, kit: object) -> set[str]:
    guard = SuppliesActivationGuard(lambda: SqliteLedger(), lambda: station.mission, limits)
    with station.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor-guard") as uow:
        return {f.check_id for f in guard.check(uow, candidate(kit))}


@pytest.mark.trace("E32-T-17", "S-A1", "S-A2", "S-A3")
def test_the_activation_guard_rejects_any_release_that_would_make_stock_negative(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    station.sessions.by_token["crew"] = crew_session()
    assert (
        station.client.post(
            "/v1/supplies/eye_rinse_water/uses", headers=headers(), json=use_body(4)
        ).status_code
        == 201
    )
    base = example("kit-map.example.json")
    assert isinstance(base, dict)
    assert guard_findings(station, base) == set()

    below = copy.deepcopy(base)
    for row in below["stock"]["lunar_surface"]:
        if row["supply_id"] == "eye_rinse_water":
            row["quantity"] = 3
    assert any("S-A3" in f for f in guard_findings(station, below))

    unit = copy.deepcopy(base)
    unit["supplies"][0]["unit"] = "litre"
    assert any("S-A1" in f for f in guard_findings(station, unit))

    kept = copy.deepcopy(base)
    kept["supplies"][0]["consumable"] = False
    assert any("S-A1" in f for f in guard_findings(station, kept))

    removed = copy.deepcopy(base)
    removed["stock"]["lunar_surface"] = [
        r for r in removed["stock"]["lunar_surface"] if r["supply_id"] != "eye_rinse_water"
    ]
    assert any("S-A2" in f for f in guard_findings(station, removed))

    restock = copy.deepcopy(base)
    for row in restock["stock"]["lunar_surface"]:
        if row["supply_id"] == "eye_rinse_water":
            row["quantity"] = 12
    assert guard_findings(station, restock) == set()

    unused_gone = copy.deepcopy(base)
    unused_gone["supplies"] = [s for s in unused_gone["supplies"] if s["supply_id"] != "towels"]
    for rows in unused_gone["stock"].values():
        rows[:] = [r for r in rows if r["supply_id"] != "towels"]
    assert not any("S-A" in f for f in guard_findings(station, unused_gone))


@pytest.mark.trace("E32-T-17", "fail closed")
def test_an_unwired_guard_refuses_rather_than_passes(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    guard = SuppliesActivationGuard(lambda: None, lambda: None, limits)
    with station.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor-guard") as uow:
        assert guard.check(uow, candidate(example("kit-map.example.json")))


@pytest.mark.trace("E32-T-32", "corrupt configuration")
def test_the_release_check_reports_a_document_that_is_not_json(tmp_path: Path) -> None:
    from crew_health.kernel.config import ConfigDocumentView

    raw = b"{not json"
    import hashlib

    view = ConfigDocumentView(ConfigName.KIT_MAP, hashlib.sha256(raw).hexdigest(), raw)
    assert SuppliesReleaseCheck().check(CandidateRelease("release_9", {ConfigName.KIT_MAP: view}))


@pytest.mark.trace("E32 5.1", "INV-S12")
def test_a_broken_chain_shows_unknown_cover_and_refuses_uses_after_restart(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    station.sessions.by_token["crew"] = crew_session()
    assert (
        station.client.post(
            "/v1/supplies/eye_rinse_water/uses", headers=headers(), json=use_body(1)
        ).status_code
        == 201
    )
    with sqlite3.connect(station.db.path) as connection:  # a torn write: sequence 3 with no sequence 2
        columns = connection.execute("SELECT * FROM supplies_use").fetchone()
        broken = list(columns)
        broken[0], broken[8], broken[19] = "sup_torn", 3, "api:torn"
        connection.execute(f"INSERT INTO supplies_use VALUES ({','.join('?' * len(broken))})", broken)
    restarted = build_station(tmp_path)
    restarted.sessions.by_token["crew"] = crew_session()
    shown = rinse(restarted)
    stock = shown["stock"]
    assert isinstance(stock, dict)
    assert stock["quantity_now"] is None and shown["cover_uses"] is None
    refused = restarted.client.post(
        "/v1/supplies/eye_rinse_water/uses", headers=headers(key="after-restart-1"), json=use_body(1)
    )
    assert refused.status_code == 500 and refused.json()["retryable"] is False
    assert restarted.rows("SELECT count(*) FROM supplies_use") == [(2,)]
