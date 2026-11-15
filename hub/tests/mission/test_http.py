"""Mission operations on the real stack: temporary on-disk SQLite, create_app and real HTTP (E23 6).

Traces: T-E23-001..016, 044, 046, 073, 086; concurrent CAS, duplicate and retried submissions, privacy of notes
and overlays, the unavailable aggregate views and the consumer port wiring."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import START, Station, build_station

pytestmark = pytest.mark.e23


@pytest.fixture
def station(tmp_path: Path) -> Station:
    st = build_station(tmp_path / "hub")
    assert st.app.state.runtime.serving, st.app.state.runtime.fault_code
    st.initialize("SIM")
    st.advance(seconds=30)
    return st


def propose(st: Station, key: str = "key_0000p", environment: str = "transit") -> Any:
    return st.client.post(
        "/v1/environment/changes",
        json={"environment_id": environment, "reason": "demo_switch"},
        headers=st.headers("operator", key),
    )


def act(st: Station, profile: dict[str, Any], who: str, key: str, revision: int | None = None) -> Any:
    headers = st.headers(who, key, **{"If-Match": f'"{revision or profile["revision"]}"'})
    return st.client.post(
        f"/v1/environment/changes/{profile['profile_id']}/actions",
        json={"action": "confirm"},
        headers=headers,
    )


@pytest.mark.trace("T-E23-073", "R34")
def test_getenvironment_serves_the_one_active_record_with_its_etag(station: Station) -> None:
    reply = station.client.get("/v1/environment", headers=station.headers("crew_a"))
    assert reply.status_code == 200 and reply.headers["ETag"] == '"1"' and reply.headers["Config-Release"]
    assert reply.json()["state"] == "active" and reply.json()["environment_id"] == "station_leo"
    assert station.client.get("/v1/environment").status_code == 401  # no token, no answer


@pytest.mark.trace("T-E23-001", "T-E23-005", "T-E23-006", "T-E23-056", "R34")
def test_propose_confirm_activates_only_when_confirmed_and_a_second_proposal_is_refused(
    station: Station,
) -> None:
    created = propose(station)
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["state"] == "proposed" and profile["allowed_actions"] == []  # the operator may not confirm
    assert (
        station.client.get("/v1/environment", headers=station.headers("crew_a")).json()["environment_id"]
        == "station_leo"
    )
    second = propose(station, "key_0000q", "lunar_surface")
    assert second.status_code == 409 and second.json()["code"] == "INVALID_TRANSITION"
    listed = station.client.get("/v1/environment/changes", headers=station.headers("crew_a")).json()
    assert [i["state"] for i in listed["items"]][:1] == ["proposed"]  # newest first
    for n, who in enumerate(("crew_a", "crew_b")[: profile["confirmations_required"]]):
        profile = act(station, profile, who, f"key_c{n}").json()
    assert profile["state"] == "active"
    assert (
        station.client.get("/v1/environment", headers=station.headers("crew_a")).json()["environment_id"]
        == "transit"
    )
    old = station.client.get("/v1/environment/changes", headers=station.headers("crew_a")).json()["items"]
    assert sorted(i["state"] for i in old).count("active") == 1  # exactly one active record, ever


@pytest.mark.trace("T-E23-056", "T-E23-005", "R34")
def test_two_clients_one_commit_the_second_sees_the_committed_state(station: Station) -> None:
    profile = propose(station).json()
    needed = profile["confirmations_required"]
    first = act(station, profile, "crew_a", "key_c1")
    assert first.status_code == 200
    stale = act(
        station, profile, "crew_b", "key_c2", revision=profile["revision"]
    )  # the revision it read is old
    if needed > 1:
        assert stale.status_code == 409 and stale.json()["code"] == "STALE_VERSION"
        assert (
            stale.json()["current_state"]["profile_id"] == profile["profile_id"]
        )  # the latest object comes back
    missing = station.client.post(
        f"/v1/environment/changes/{profile['profile_id']}/actions",
        json={"action": "confirm"},
        headers=station.headers("crew_b", "key_c3"),
    )
    assert missing.status_code == 428 and missing.json()["code"] == "VERSION_REQUIRED"


@pytest.mark.trace("T-E23-052", "T-E23-006")
def test_duplicate_and_retried_submissions_and_wrong_identity(station: Station) -> None:
    first = propose(station)
    assert first.status_code == 201
    retried = propose(station)  # the same key and body after the acknowledgement
    assert retried.status_code == 409 and retried.json()["code"] == "DUPLICATE_COMMAND"
    assert retried.json()["current_state"]["operation_id"] == "proposeEnvironmentChange"
    changed = station.client.post(
        "/v1/environment/changes",
        json={"environment_id": "lunar_surface", "reason": "demo_switch"},
        headers=station.headers("operator", "key_0000p"),
    )
    assert changed.status_code == 409  # one key never covers another body
    wrong_role = station.client.post(
        f"/v1/environment/changes/{first.json()['profile_id']}/actions",
        json={"action": "confirm"},
        headers=station.headers("operator", "key_0000z", **{"If-Match": '"1"'}),
    )
    assert wrong_role.status_code == 403  # a station or operator session cannot confirm
    stale_release = propose(station, "key_0000r", "lunar_surface")
    assert stale_release.status_code == 409
    mismatch = station.client.post(
        "/v1/overlays",
        json={"overlay": "sleep", "started_at": START.strftime("%Y-%m-%dT%H:%M:%SZ")},
        headers=station.headers("crew_a", "key_0000o", **{"Config-Release": "release_other"}),
    )
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "CONFIG_VERSION_MISMATCH"


@pytest.mark.trace("T-E23-014", "T-E23-057", "R34")
def test_overlays_are_private_to_their_owner_and_an_operator_can_end_one(station: Station) -> None:
    made = station.client.post(
        "/v1/overlays",
        json={"overlay": "sleep", "started_at": station.clock.at.strftime("%Y-%m-%dT%H:%M:%SZ")},
        headers=station.headers("crew_a", "key_0000o"),
    )
    assert made.status_code == 201, made.text
    overlay = made.json()
    assert (
        station.client.get("/v1/overlays", headers=station.headers("crew_a")).json()["items"][0]["overlay_id"]
        == overlay["overlay_id"]
    )
    assert (
        station.client.get("/v1/overlays", headers=station.headers("crew_b")).json()["items"] == []
    )  # not another person's
    again = station.client.post(
        "/v1/overlays",
        json={"overlay": "sleep", "started_at": station.clock.at.strftime("%Y-%m-%dT%H:%M:%SZ")},
        headers=station.headers("crew_a", "key_0000p"),
    )
    assert again.status_code == 409  # one active overlay per kind
    ended = station.client.post(
        f"/v1/overlays/{overlay['overlay_id']}/end",
        json={"ended_at": (station.clock.at + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        headers=station.headers("operator", "key_0000e", **{"If-Match": '"1"'}),
    )
    assert ended.status_code == 200 and ended.json()["state"] == "ended"


@pytest.mark.trace("T-E23-044", "T-E23-046", "R34")
def test_mission_events_keep_their_note_with_the_writer_and_reject_foreign_people_and_origin(
    station: Station,
) -> None:
    kinds = station.client.get("/v1/mission-events", headers=station.headers("crew_a"))
    assert kinds.status_code == 200 and kinds.json()["items"] == []
    event_type = _event_type(station)
    body = {
        "event_type": event_type,
        "occurred_at": station.clock.at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "person_ids": ["per_a"],
        "note": "private note text",
        "origin_mode": "SIM",
    }
    done = station.client.post(
        "/v1/mission-events", json=body, headers=station.headers("crew_a", "key_0000m")
    )
    assert done.status_code == 201, done.text
    assert done.json()["note"] == "private note text"  # the writer sees it
    other = station.client.get("/v1/mission-events", headers=station.headers("crew_b")).json()["items"]
    assert other and other[0]["note"] is None  # nobody else does
    bad_origin = station.client.post(
        "/v1/mission-events",
        json={**body, "origin_mode": "LIVE"},
        headers=station.headers("crew_a", "key_0000n"),
    )
    assert bad_origin.status_code == 422 and bad_origin.json()["code"] == "ORIGIN_MISMATCH"
    stranger = station.client.post(
        "/v1/mission-events",
        json={**body, "person_ids": ["per_zzz"]},
        headers=station.headers("crew_a", "key_0000x"),
    )
    assert stranger.status_code == 422
    connection = sqlite3.connect(station.db.path)
    logged = connection.execute(
        "SELECT count(*) FROM platform_audit WHERE envelope LIKE '%private note%'"
    ).fetchone()[0]
    connection.close()
    assert logged == 0  # a note never reaches the audit trail or any log


def _event_type(station: Station) -> str:
    import json

    from crew_health.contracts_gen.enums import ConfigName

    with station.db.snapshot() as view:
        document = json.loads(view.release.document(ConfigName.ENVIRONMENT_PROFILES).raw)
    return str(next(t["id"] for t in document["mission_event_types"] if "crew" in t["who_may_record"]))


@pytest.mark.trace("T-E23-086", "R-E23-6")
def test_aggregate_views_that_need_the_ground_link_fail_loud_and_retryable_never_fabricated(
    station: Station,
) -> None:
    for path in ("/v1/mission", "/v1/context", "/v1/views/habitat"):
        reply = station.client.get(path, headers=station.headers("crew_a"))
        assert reply.status_code == 500 and reply.json()["retryable"] is True


@pytest.mark.trace("S5.12", "T-E23-072")
def test_consumer_ports_are_satisfied_by_real_providers(station: Station) -> None:
    from crew_health.guidance.application.ports import EnvironmentFacts as GuidanceFacts
    from crew_health.guidance.application.ports import Known
    from crew_health.help.application.ports import MissionStatePort as HelpPort
    from crew_health.roles.application.ports import DayLabelPort, LocationPort, OverlayPort
    from crew_health.roles.application.ports import MissionStatePort as RolesPort

    resolver = station.resolver()
    from crew_health.mission.adapters.consumers import KitmapMission, MeasurementMission
    from crew_health.mission.adapters.live_state import LiveMissionState
    from crew_health.mission.application.ports import MissionStatePort as OwnPort

    own = resolver.require(OwnPort)
    assert isinstance(own, LiveMissionState)
    facts = MeasurementMission(own).current()  # registered under measurement's port by the composition root
    assert KitmapMission(own).mission_mode() == "SIM"
    assert facts is not None and facts.mission_mode == "SIM" and facts.active_environment_id == "station_leo"
    help_facts = resolver.require(HelpPort).current()
    assert help_facts is not None and help_facts.mission_id == facts.mission_id
    assert KitmapMission(own).active_environment_id() == "station_leo"
    assert resolver.require(RolesPort).current() is not None
    assert resolver.require(OverlayPort).active(["per_a"], station.clock.at) == {"per_a": ()}
    assert resolver.require(LocationPort).location_of("per_a", station.clock.at) is None  # S8.5 interim
    assert isinstance(resolver.require(DayLabelPort).inline_label_at(station.clock.at), str)
    with station.db.snapshot() as view:
        applicable = resolver.require(GuidanceFacts).applicable(view, None, ["station_leo"])
        assert applicable == Known(True)
        assert (
            resolver.require(GuidanceFacts).applicable(view, "elsewhere", ["station_leo"]).__class__.__name__
            == "Unknown"
        )
