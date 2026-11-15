"""POST /v1/supplies/{supply_id}/uses: identity before mutable checks (PLAN.md rows 62 and 63), one snapshot for
identity and write, known references only, evidence of every refused help-session use (HZ14) and a Config-Release
header from the snapshot the body came from (DD8)."""

from __future__ import annotations

import copy
import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.help.application.ports import HelpContext
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.supplies.application.ports import MissionFacts

from .harness import Station, build_station, crew_session, example, headers, lunar_mission, release, use_body

USES = "/v1/supplies/eye_rinse_water/uses"


@pytest.fixture
def station(tmp_path: Path) -> Iterator[Station]:
    built = build_station(tmp_path)
    built.sessions.by_token["crew"] = crew_session()
    yield built


def count(station: Station, table: str) -> int:
    value = station.rows(f"SELECT count(*) FROM {table}")[0][0]
    assert isinstance(value, int)
    return value


@pytest.mark.trace("m3", "PLAN.md row 63", "AGENTS 6")
def test_an_identical_late_retry_gets_its_201_after_the_session_closed(station: Station) -> None:
    station.help.contexts["help_1"] = HelpContext(True, "SIM", "lunar_surface")
    body = use_body(1, help_session_id="help_1")
    first = station.client.post(USES, headers=headers(), json=body)
    assert first.status_code == 201
    station.help.contexts["help_1"] = HelpContext(False, None, None)  # closed, or its provider gone
    retry = station.client.post(USES, headers=headers(), json=body)
    assert retry.status_code == 201 and retry.content == first.content
    other = station.client.post(USES, headers=headers(key="use-key-0002"), json=body)
    assert other.status_code == 403  # a new use still needs a visible session
    assert count(station, "supplies_use") == 1


@pytest.mark.trace("m4", "DD4", "PLAN.md row 63")
def test_a_mode_switch_between_identity_and_write_is_refused_and_the_retry_succeeds(station: Station) -> None:
    calls = itertools.count()

    def flipping(uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts:
        return lunar_mission("SIM" if next(calls) == 0 else "LIVE")

    station.mission.current = flipping  # type: ignore[method-assign]
    first = station.client.post(USES, headers=headers(), json=use_body(1))
    assert first.status_code == 500 and first.json()["retryable"] is True
    assert count(station, "supplies_use") == 0
    retry = station.client.post(USES, headers=headers(), json=use_body(1))
    assert retry.status_code == 201 and retry.json()["origin_mode"] == "LIVE"


@pytest.mark.trace("m7", "INV-S9", "AGENTS rule 2")
@pytest.mark.parametrize(
    "context",
    [HelpContext(True, None, "lunar_surface"), HelpContext(True, "SIM", None)],
)
def test_a_help_session_with_unknown_mode_or_environment_is_refused(
    station: Station, context: HelpContext
) -> None:
    station.help.contexts["help_1"] = context
    refused = station.client.post(USES, headers=headers(), json=use_body(1, help_session_id="help_1"))
    assert refused.status_code == 422 and refused.json()["code"] == "INVALID_VALUE"
    assert refused.json()["field_errors"][0]["field"] == "/help_session_id"
    assert count(station, "supplies_use") == 0


@pytest.mark.trace("m13", "E32-T-42", "HZ14")
def test_every_422_refusal_of_a_help_session_use_leaves_evidence(station: Station) -> None:
    station.help.contexts["help_1"] = HelpContext(True, "SIM", "lunar_surface")
    body = use_body(1, help_session_id="help_1")
    for _ in range(2):  # the retry replays the refusal and writes no second row
        kept = station.client.post("/v1/supplies/eye_wash_set/uses", headers=headers(), json=body)
        assert kept.status_code == 422 and kept.json()["code"] == "INVALID_VALUE"
    station.help.contexts["help_2"] = HelpContext(True, "SIM", "mars_surface")
    elsewhere = station.client.post(
        USES, headers=headers(key="use-key-0002"), json=use_body(1, help_session_id="help_2")
    )
    assert elsewhere.status_code == 422
    assert station.rows("SELECT supply_id, code FROM supplies_use_refused ORDER BY supply_id") == [
        ("eye_rinse_water", "INVALID_VALUE"),
        ("eye_wash_set", "INVALID_VALUE"),
    ]
    assert count(station, "supplies_use") == 0


@pytest.mark.trace("m9", "DD8")
def test_the_config_release_header_names_the_release_the_body_came_from(station: Station) -> None:
    kit = copy.deepcopy(example("kit-map.example.json"))
    assert isinstance(kit, dict)
    for row in kit["stock"]["lunar_surface"]:
        if row["supply_id"] == "eye_rinse_water":
            row["quantity"] = 20
    releases = itertools.cycle([release("release_1"), release("release_2", {ConfigName.KIT_MAP: kit})])
    station.db.set_release_provider(lambda: next(releases))  # every snapshot pins the next release
    for _ in range(4):
        shown = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"})
        rinse = next(e for e in shown.json()["supplies"] if e["stock"]["supply_id"] == "eye_rinse_water")
        expected = "release_1" if rinse["stock"]["quantity_packed"] == 6 else "release_2"
        assert shown.headers["Config-Release"] == expected
    written = station.client.post(USES, headers=headers(), json=use_body(1))
    assert written.status_code == 201
    stored = station.rows("SELECT config_release_id FROM supplies_use")[0][0]
    assert written.headers["Config-Release"] == stored
