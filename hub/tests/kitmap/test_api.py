"""End to end over HTTP: the registered module, a sqlite file, the authored config and the package examples."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.contracts_gen.schemas.kit_map_view_schema import KitMapView
from crew_health.contracts_gen.schemas.kit_map_what_if_schema import KitMapWhatIf
from crew_health.kernel.testing import MemoryRelease
from crew_health.kitmap.adapters.d22 import replace_ids, stand_ins
from crew_health.kitmap.application.ports import (
    KitMapContext,
    PlanChange,
    PlanColumns,
    PlanColumnsPort,
    PlanningDelta,
    RosterPort,
)

from .conftest import (
    CONFIG,
    EXAMPLE,
    SESSION_HEADER,
    FakeMission,
    Hub,
    documents,
    edited,
    example_value,
    release,
    ui_copy_raw,
    view,
    x_roles,
)

pytestmark = [pytest.mark.trace("R44", "V76", "V77", "D28", "D29", "KM-I7", "KM-I8")]

CREW = {SESSION_HEADER: "crew"}
WHAT_IF_ECG = example_value(EXAMPLE.parent / "requests/kit-map-what-if.json")
DEMO_ENVIRONMENT = "lunar_surface"  # DEMO-PLAN shot A and DEMO-CONDITIONS steps 4 and 5: the Moon base


def rows(body: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(c["condition_id"]): c for c in cast(list[dict[str, object]], body["conditions"])}


def get(hub: Hub, environment_id: str | None = None) -> dict[str, object]:
    params = {"environment_id": environment_id} if environment_id else {}
    response = hub.client.get("/v1/kit-map", params=params, headers=CREW)
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


def post_what_if(hub: Hub, body: object, **headers: str) -> dict[str, object]:
    response = hub.client.post("/v1/kit-map/what-if", json=body, headers={**CREW, **headers})
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


@pytest.fixture
def hub(make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes) -> Hub:
    return make_hub(release("rel_1", config_raw, ui_copy=ui_copy), mission=FakeMission(DEMO_ENVIRONMENT))


def test_shot_a_counts_and_the_icl10_ecg_gap(hub: Hub) -> None:
    before = hub.writes()
    response = hub.client.get("/v1/kit-map", headers=CREW)
    assert response.status_code == 200
    assert response.headers["Config-Release"] == "rel_1"
    payload = json.loads(response.content)
    body = KitMapView.model_validate(replace_ids(payload, stand_ins(payload)))  # D22 suffixed IDs
    assert body.environment_id.root == DEMO_ENVIRONMENT
    assert (body.counts.measured_here, body.counts.report_only, body.counts.needs_test_not_aboard) == (
        9,
        82,
        28,
    )
    assert body.counts.total == 119
    assert {"ICL36A", "ICL36B"} <= set(rows(payload))
    icl10 = rows(response.json())["ICL10"]
    # DEMO-CONDITIONS ICL10, "Can this kit confirm or exclude it? No. Needs an ECG": checked against the package.
    assert icl10["state"] == "needs_test_not_aboard"
    assert icl10["missing_instruments"] == ["ecg_single_lead"]
    assert icl10["missing_text"] == "This kit can't: needs an ECG"
    assert icl10["cliff_url"] == next(
        c["url"]
        for c in json.loads(
            (CONFIG.parents[1] / "docs/product/cliff/cliff-confirming-tests.json").read_text(encoding="utf-8")
        )
        if c["condition_id"] == "ICL10"
    )
    assert icl10["review_state"] == "unreviewed"
    assert hub.writes() == before


def test_same_context_gives_the_same_bytes(hub: Hub) -> None:
    first = hub.client.get("/v1/kit-map", headers=CREW)
    second = hub.client.get("/v1/kit-map", headers=CREW)
    assert first.content == second.content


def test_ecg_what_if_moves_only_the_rows_it_changes_and_persists_nothing(hub: Hub) -> None:
    base = get(hub, "station_leo")
    before = hub.writes()
    response = hub.client.post("/v1/kit-map/what-if", json=WHAT_IF_ECG, headers=CREW)
    assert response.status_code == 200, response.text
    payload = json.loads(response.content)
    body = KitMapWhatIf.model_validate(replace_ids(payload, stand_ins(payload)))
    assert body.persisted is False
    assert [i.root for i in body.added_instrument_ids] == ["ecg_single_lead"]
    changed = {c.condition_id: (c.from_, c.to) for c in body.changed}
    assert changed["ICL10"] == ("needs_test_not_aboard", "measured_here")
    # Only rows whose state differs between the two kits are listed, and every listed row really moved.
    after_kit = set(
        cast(dict[str, list[str]], json.loads(CONFIG.read_text(encoding="utf-8"))["kits"])["station_leo"]
    ) | {"ecg_single_lead"}
    for condition_id, row in rows(base).items():
        tests = cast(list[dict[str, object]], row["tests"])
        aboard = any(t["instruments"] and set(cast(list[str], t["instruments"])) <= after_kit for t in tests)
        moves = row["state"] != "measured_here" and aboard
        assert (condition_id in changed) == moves, condition_id
    counts = body.what_if_counts
    assert counts.measured_here == cast(dict[str, int], base["counts"])["measured_here"] + len(changed)
    assert hub.writes() == before
    # Removing it restores: the view was never touched.
    assert get(hub, "station_leo") == base


def test_environment_switch_changes_kit_states(hub: Hub) -> None:
    hub.mission.environment_id = "station_leo"
    station = get(hub)
    hub.mission.environment_id = "mars_surface"
    mars = get(hub)
    assert station["environment_id"] == "station_leo" and mars["environment_id"] == "mars_surface"
    assert station["counts"] != mars["counts"]
    moved = {c for c, row in rows(mars).items() if row["state"] != rows(station)[c]["state"]}
    assert moved, "the Mars kit adds the ultrasound"
    for condition_id in moved:
        assert rows(mars)[condition_id]["state"] == "measured_here"


def test_no_active_environment_fails_loudly(
    make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy), mission=FakeMission(None))
    response = hub.client.get("/v1/kit-map", headers=CREW)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert response.json()["retryable"] is False
    assert get(hub, "transit")["environment_id"] == "transit"


def test_config_edit_changes_content_with_no_code_change(
    make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy))
    assert rows(get(hub, "station_leo"))["ICL10"]["state"] == "needs_test_not_aboard"

    def pack_ecg(doc: dict[str, object]) -> None:
        cast(dict[str, list[str]], doc["kits"])["station_leo"].append("ecg_single_lead")

    copy = ui_copy_raw(cannot="Not on this ship: {instruments}")
    assert hub.activate(release("rel_2", edited(CONFIG, pack_ecg), ui_copy=copy))
    icl10 = rows(get(hub, "station_leo"))["ICL10"]
    assert icl10["state"] == "measured_here"
    icl3 = rows(get(hub, "station_leo"))["ICL3"]
    assert cast(str, icl3["missing_text"]).startswith("Not on this ship: ")


def test_corrupt_config_is_rejected_and_the_last_good_release_stays(
    make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy))
    good = hub.client.get("/v1/kit-map", params={"environment_id": "station_leo"}, headers=CREW)
    corrupt = config_raw[: len(config_raw) // 2]
    assert not hub.activate(release("rel_2", corrupt, ui_copy=ui_copy))
    assert all(f.document is ConfigName.KIT_MAP for f in hub.rejected[-1])
    again = hub.client.get("/v1/kit-map", params={"environment_id": "station_leo"}, headers=CREW)
    assert again.headers["Config-Release"] == "rel_1"
    assert again.content == good.content


def test_a_camera_estimate_can_never_confirm_a_condition(
    make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy))

    def camera_confirms_icl10(doc: dict[str, object]) -> None:
        conditions = cast(list[dict[str, list[dict[str, list[str]]]]], doc["conditions"])
        icl10 = next(c for c in conditions if c["condition_id"] == "ICL10")  # type: ignore[comparison-overlap]
        icl10["confirming_tests"][0]["instruments"] = ["camera_station"]

    assert not hub.activate(release("rel_2", edited(CONFIG, camera_confirms_icl10), ui_copy=ui_copy))
    assert any(f.check_id == "kitmap.CAM" for f in hub.rejected[-1])
    assert rows(get(hub, "station_leo"))["ICL10"]["state"] == "needs_test_not_aboard"


def test_example_document_is_refused_while_live(
    make_hub: Callable[..., Hub], config_raw: bytes, example_raw: bytes, ui_copy: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy), mission=FakeMission("station_leo", "LIVE"))
    assert not hub.activate(release("rel_2", example_raw, ui_copy=ui_copy))
    assert any(f.check_id == "kitmap.example_in_live" for f in hub.rejected[-1])
    hub.mission.mode = "SIM"
    assert hub.activate(release("rel_3", example_raw, ui_copy=ui_copy))
    assert get(hub, "station_leo")["example"] is True


def test_example_view_matches_the_package_examples(
    make_hub: Callable[..., Hub], example_raw: bytes, ui_copy: bytes
) -> None:
    """Golden: the package's own example responses (view-station, view-mars, what-if-ecg), plan columns aside."""
    hub = make_hub(release("rel_1", example_raw, ui_copy=ui_copy), mission=FakeMission("station_leo", "SIM"))
    for name, environment in (("view-station.json", "station_leo"), ("view-mars.json", "mars_surface")):
        expected = cast(dict[str, object], example_value(EXAMPLE.parent / "kit-map" / name))
        actual = get(hub, environment)
        for key in ("environment_id", "kit_map_label", "example", "kit", "candidates", "counts", "regions"):
            assert actual[key] == expected[key], (name, key)
        for condition_id, row in rows(expected).items():
            mine = rows(actual)[condition_id]
            for key in ("state", "tests", "missing_instruments", "missing_text", "cliff_url", "review_state"):
                assert mine[key] == row[key], (name, condition_id, key)
    expected_what_if = cast(dict[str, object], example_value(EXAMPLE.parent / "kit-map/what-if-ecg.json"))
    actual_what_if = post_what_if(hub, WHAT_IF_ECG)
    for key in ("added_instrument_ids", "ignored_instrument_ids", "base_counts", "what_if_counts", "regions"):
        assert actual_what_if[key] == expected_what_if[key], key
    assert [c["condition_id"] for c in cast(list[dict[str, str]], actual_what_if["changed"])] == [
        c["condition_id"] for c in cast(list[dict[str, str]], expected_what_if["changed"])
    ]


def test_sessions_and_roles(
    hub: Hub, make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    assert hub.client.get("/v1/kit-map").status_code == 401
    assert hub.client.get("/v1/kit-map").json()["code"] == "UNAUTHENTICATED"
    for role in sorted(x_roles("getKitMap")):
        assert hub.client.get("/v1/kit-map", headers={SESSION_HEADER: role}).status_code == 200
    assert hub.client.get("/v1/kit-map", headers={SESSION_HEADER: "visitor"}).status_code == 403
    no_identity = make_hub(release("rel_1", config_raw, ui_copy=ui_copy), roles=False)
    assert no_identity.client.get("/v1/kit-map", headers=CREW).status_code == 401  # fails closed


@pytest.mark.parametrize(
    ("body", "content_type", "status", "code"),
    [
        (b'{"add_instruments": ["ecg_single_lead"]}', "text/plain", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"{not json", "application/json", 400, "MALFORMED_REQUEST"),
        (
            b'{"add_supplies": [{"supply_id": "x", "quantity": NaN}]}',
            "application/json",
            400,
            "MALFORMED_REQUEST",
        ),
        (
            b'{"add_instruments": ["ecg_single_lead"], "environment_id": "venus"}',
            "application/json",
            400,
            "MALFORMED_REQUEST",
        ),
        (b"{}", "application/json", 422, "INVALID_VALUE"),
        (b'{"add_instruments": []}', "application/json", 422, "INVALID_VALUE"),
        (b'{"add_instruments": ["tricorder"]}', "application/json", 422, "INVALID_VALUE"),
        (b'{"add_instruments": ["oct", "oct"]}', "application/json", 422, "INVALID_VALUE"),
        (b'{"add_instruments": ["oct"], "extra": 1}', "application/json", 422, "INVALID_VALUE"),
        (
            b'{"add_supplies": [{"supply_id": "eye_shield", "quantity": 2}]}',
            "application/json",
            422,
            "INVALID_VALUE",
        ),
    ],
)
def test_what_if_failures_use_the_error_envelope(
    hub: Hub, body: bytes, content_type: str, status: int, code: str
) -> None:
    before = hub.writes()
    response = hub.client.post(
        "/v1/kit-map/what-if",
        content=body,
        headers={**CREW, "content-type": content_type, "X-Correlation-Id": "corr_case"},
    )
    assert response.status_code == status, response.text
    envelope = response.json()
    assert envelope["code"] == code
    assert envelope["correlation_id"] == "corr_case"
    assert response.headers["X-Correlation-Id"] == "corr_case"
    assert set(envelope) == {
        "code",
        "message",
        "retryable",
        "field_errors",
        "correlation_id",
        "current_state",
        "supported_alternative",
    }
    assert hub.writes() == before


def test_unknown_instrument_points_at_its_index(hub: Hub) -> None:
    response = hub.client.post(
        "/v1/kit-map/what-if", json={"add_instruments": ["oct", "tricorder", "warp_core"]}, headers=CREW
    )
    assert [f["field"] for f in response.json()["field_errors"]] == [
        "/add_instruments/1",
        "/add_instruments/2",
    ]


def test_what_if_already_packed_instrument_is_ignored(hub: Hub) -> None:
    body = post_what_if(hub, {"add_instruments": ["bp_cuff", "oct"], "environment_id": "station_leo"})
    assert body["ignored_instrument_ids"] == ["bp_cuff"]
    assert body["added_instrument_ids"] == ["oct"]


def test_skills_what_if_without_a_roster_fails_loudly(hub: Hub) -> None:
    response = hub.client.post(
        "/v1/kit-map/what-if",
        json={"add_skills": [{"skill_id": "pulse_check_wrist", "person_ids": ["mara"]}]},
        headers=CREW,
    )
    assert response.status_code == 500
    assert response.json()["retryable"] is True


def test_missing_ui_copy_gives_null_missing_text_never_invented_words(
    make_hub: Callable[..., Hub], config_raw: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw))
    assert rows(get(hub, "station_leo"))["ICL10"]["missing_text"] is None


def test_request_correlation_id_is_generated_when_absent_or_unusable(hub: Hub) -> None:
    response = hub.client.get("/v1/kit-map", headers={**CREW, "X-Correlation-Id": "not valid!"})
    assert response.headers["X-Correlation-Id"].startswith("corr_")


ONBOARD_PLANS = json.dumps(example_value(EXAMPLE.parent / "config/onboard-plans.example.json")).encode()
SKILLS = json.dumps(example_value(EXAMPLE.parent / "config/skills-catalog.example.json")).encode()


@dataclass
class FakePlans:
    """PlanColumnsPort fake (E28 is not on main): every row with a plan ID is aboard once the ECG is packed."""

    calls: list[frozenset[str]] = field(default_factory=list)

    def row_columns(
        self, ctx: KitMapContext, *, kit: frozenset[str], plan_ids: Sequence[str | None]
    ) -> Sequence[PlanColumns]:
        self.calls.append(kit)
        return [PlanColumns(None, None, "ecg_single_lead" in kit) for _ in plan_ids]

    def plans_changed(
        self, ctx: KitMapContext, *, base_kit: frozenset[str], delta: PlanningDelta
    ) -> Sequence[PlanChange]:
        return (
            [PlanChange("plan_icl10", "ICL10", None, False, True, True, False)]
            if delta.add_instruments
            else []
        )


@dataclass
class FakeRoster:
    people: frozenset[str] = frozenset({"mara", "sofia"})

    def person_ids(self) -> frozenset[str]:
        return self.people


def test_plan_columns_come_from_the_provider_when_plans_exist(
    make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    plans = FakePlans()
    hub = make_hub(
        release("rel_1", config_raw, ui_copy=ui_copy, onboard_plans=ONBOARD_PLANS),
        ports={PlanColumnsPort: plans, RosterPort: FakeRoster()},
    )
    view_body = get(hub, "station_leo")
    assert view_body["plans_aboard"] == 0
    assert len(cast(list[str], view_body["plans_to_write"])) == 119
    what_if = post_what_if(hub, {"add_instruments": ["ecg_single_lead"], "environment_id": "station_leo"})
    assert cast(list[dict[str, object]], what_if["plans_changed"])[0]["to"] == {
        "plan_aboard": True,
        "waits_for_medical_officer": False,
    }
    assert plans.calls[-1] >= {"ecg_single_lead"}


def test_plans_present_without_a_provider_fail_closed(
    make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy, onboard_plans=ONBOARD_PLANS))
    response = hub.client.get("/v1/kit-map", params={"environment_id": "station_leo"}, headers=CREW)
    assert response.status_code == 500 and response.json()["retryable"] is True


def test_skill_what_if_checks_the_roster(
    make_hub: Callable[..., Hub], config_raw: bytes, ui_copy: bytes
) -> None:
    hub = make_hub(
        MemoryRelease(
            "rel_1",
            {
                **documents(config_raw, ui_copy=ui_copy),
                ConfigName.SKILLS_CATALOG: view(ConfigName.SKILLS_CATALOG, SKILLS),
            },
        ),
        ports={RosterPort: FakeRoster()},
    )
    skill = cast(list[dict[str, str]], json.loads(SKILLS)["skills"])[0]["skill_id"]
    body = post_what_if(
        hub, {"add_skills": [{"skill_id": skill, "person_ids": ["mara"]}], "environment_id": "transit"}
    )
    assert body["added_skills"] == [{"skill_id": skill, "person_ids": ["mara"]}]
    assert body["changed"] == [] and body["base_counts"] == body["what_if_counts"]  # skills never move a ring
    response = hub.client.post(
        "/v1/kit-map/what-if",
        json={"add_skills": [{"skill_id": skill, "person_ids": ["mara", "nobody"]}]},
        headers=CREW,
    )
    assert response.status_code == 422
    assert [f["field"] for f in response.json()["field_errors"]] == ["/add_skills/0/person_ids/1"]
