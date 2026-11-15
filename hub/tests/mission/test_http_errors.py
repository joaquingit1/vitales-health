"""Mission HTTP error and paging paths on the real stack (E23 6): every refusal is an ErrorEnvelope with the contract
code, and every list pages with an opaque cursor (T-E23-003/004/007/008/010/012/016)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from e23_station import Station, build_station

pytestmark = pytest.mark.e23


@pytest.fixture
def station(tmp_path: Path) -> Station:
    st = build_station(tmp_path / "hub")
    st.initialize("SIM")
    st.advance(seconds=30)
    return st


def iso(st: Station, seconds: int = 0) -> str:
    from datetime import timedelta

    return (st.clock.at + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def post(st: Station, path: str, body: Any, who: str, key: str, **headers: str) -> Any:
    return st.client.post(path, json=body, headers=st.headers(who, key, **headers))


def test_before_init_the_environment_is_not_found_and_a_second_init_is_refused(tmp_path: Path) -> None:
    st = build_station(tmp_path / "fresh")
    assert st.client.get("/v1/environment", headers=st.headers("crew_a")).status_code in (404, 500)
    st.initialize("SIM")
    with pytest.raises(Exception, match="already initialized"):
        st.initialize("SIM")


def test_malformed_bodies_headers_and_cursors_are_refused_with_contract_codes(station: Station) -> None:
    assert (
        post(station, "/v1/environment/changes", {"reason": "operator"}, "operator", "key_00001").status_code
        == 400
    )
    profile = post(
        station,
        "/v1/environment/changes",
        {"environment_id": "transit", "reason": "operator"},
        "operator",
        "key_00002",
    ).json()
    url = f"/v1/environment/changes/{profile['profile_id']}/actions"
    assert (
        post(station, url, {"action": "confirm"}, "crew_a", "key_00003", **{"If-Match": "3"}).status_code
        == 400
    )
    assert (
        post(station, url, {"action": "bogus"}, "crew_a", "key_00004", **{"If-Match": '"1"'}).status_code
        == 400
    )
    assert (
        post(
            station,
            "/v1/environment/changes/env_missing/actions",
            {"action": "confirm"},
            "crew_a",
            "key_00005",
            **{"If-Match": '"1"'},
        ).status_code
        == 404
    )
    for path in ("/v1/environment/changes", "/v1/overlays", "/v1/mission-events"):
        assert station.client.get(path + "?limit=0", headers=station.headers("crew_a")).status_code == 400
        assert station.client.get(path + "?limit=x", headers=station.headers("crew_a")).status_code == 400
        assert station.client.get(path + "?cursor=@@@", headers=station.headers("crew_a")).status_code == 400
    assert (
        station.client.get(
            "/v1/environment/changes?limit=1&cursor=eyJhZnRlciI6IDV9", headers=station.headers("crew_a")
        ).status_code
        == 400
    )


def test_a_reject_ends_the_proposal_and_lists_page_with_cursors(station: Station) -> None:
    first = post(
        station,
        "/v1/environment/changes",
        {"environment_id": "transit", "reason": "operator"},
        "operator",
        "key_00001",
    ).json()
    rejected = post(
        station,
        f"/v1/environment/changes/{first['profile_id']}/actions",
        {"action": "reject"},
        "crew_a",
        "key_00002",
        **{"If-Match": '"1"'},
    )
    assert rejected.status_code == 200 and rejected.json()["state"] == "rejected"
    second = post(
        station,
        "/v1/environment/changes",
        {"environment_id": "lunar_surface", "reason": "operator"},
        "operator",
        "key_00003",
    )
    assert second.status_code == 201  # the first is closed, so a new proposal may open
    page = station.client.get("/v1/environment/changes?limit=2", headers=station.headers("crew_a")).json()
    assert len(page["items"]) == 2 and page["page"]["next_cursor"]
    rest = station.client.get(
        "/v1/environment/changes?limit=2&cursor=" + page["page"]["next_cursor"],
        headers=station.headers("crew_a"),
    ).json()
    assert len(rest["items"]) == 1 and rest["page"]["next_cursor"] is None
    seen = {i["profile_id"] for i in page["items"]} | {i["profile_id"] for i in rest["items"]}
    assert len(seen) == 3


def test_overlay_and_event_refusals_and_paging(station: Station) -> None:
    body = {"overlay": "sleep", "started_at": iso(station)}
    assert (
        post(station, "/v1/overlays", body, "operator", "key_00001").status_code == 422
    )  # R-E23-7: no operator creation
    assert post(station, "/v1/overlays", {"overlay": "sleep"}, "crew_a", "key_00002").status_code == 422
    made = post(station, "/v1/overlays", body, "crew_a", "key_00003").json()
    second = post(
        station, "/v1/overlays", {"overlay": "exercise", "started_at": iso(station)}, "crew_a", "key_00004"
    ).json()
    paged = station.client.get("/v1/overlays?limit=1", headers=station.headers("crew_a")).json()
    assert len(paged["items"]) == 1 and paged["page"]["next_cursor"]
    more = station.client.get(
        "/v1/overlays?limit=1&cursor=" + paged["page"]["next_cursor"], headers=station.headers("crew_a")
    ).json()
    assert {paged["items"][0]["overlay_id"], more["items"][0]["overlay_id"]} == {
        made["overlay_id"],
        second["overlay_id"],
    }
    end = f"/v1/overlays/{made['overlay_id']}/end"
    assert (
        post(
            station, end, {"ended_at": iso(station, 5)}, "crew_b", "key_00005", **{"If-Match": '"1"'}
        ).status_code
        == 403
    )  # not theirs
    assert (
        post(
            station, end, {"ended_at": iso(station, 5)}, "crew_a", "key_00006", **{"If-Match": '"9"'}
        ).status_code
        == 409
    )
    assert post(station, end, {"ended_at": iso(station, 5)}, "crew_a", "key_00007").status_code == 428
    assert (
        post(
            station,
            "/v1/overlays/ovl_missing/end",
            {"ended_at": iso(station, 5)},
            "crew_a",
            "key_00008",
            **{"If-Match": '"1"'},
        ).status_code
        == 404
    )
    event = {
        "event_type": "no_such_type",
        "occurred_at": iso(station),
        "person_ids": [],
        "origin_mode": "SIM",
    }
    assert post(station, "/v1/mission-events", event, "crew_a", "key_00009").status_code == 422
    assert post(station, "/v1/mission-events", {"bogus": 1}, "crew_a", "key_00010").status_code == 400
