"""E23 section 12 tests T-E23-040, 048, 050, 073 (mission and view portions that run on fakes), and the loader checks.

Waiting on E01 T2 and E02, so not written here: getContext, getMission and getHabitatView over HTTP (T-E23-086, the
living_here view of T-E23-071, T-E23-047 against a real link), and EnvironmentFacts.applicable (T-E23-078).
"""

from __future__ import annotations

import json

import pytest
from e23m_helpers import (
    PRODUCT,
    ROOT,
    START,
    Station,
    build_station,
    documents,
    edited,
    make_release,
    operator,
    person,
)

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease
from crew_health.mission.adapters.config_reader import (
    Check7Profiles,
    Check12EffectFilters,
    ProblemError,
    load_mission_config,
    strip_source_ids,
)
from crew_health.mission.application.init import initialize_mission
from crew_health.mission.application.queries import event_model, overlay_model, profile_model
from crew_health.mission.application.use_cases import act_on_change, propose_change
from crew_health.mission.domain import loader_checks as checks

pytestmark = pytest.mark.e23
BOUNDS = checks.MissionBounds(max_event_types=128, max_state_facts=128)


def candidate(raw: dict[ConfigName, bytes]) -> CandidateRelease:
    import hashlib

    from crew_health.kernel.config import ConfigDocumentView

    views = {n: ConfigDocumentView(n, hashlib.sha256(b).hexdigest(), b) for n, b in raw.items()}
    return CandidateRelease("rel_candidate", views)


def findings(raw: dict[ConfigName, bytes]) -> list[str]:
    release = candidate(raw)
    return [
        f"{f.check_id}:{f.pointer}:{f.message}"
        for c in (Check7Profiles(BOUNDS), Check12EffectFilters())
        for f in c.check(release)
    ]


@pytest.mark.trace("T-E23-050", "T-E23-055", "R43", "CONTRACTS11")
def test_the_shipped_profiles_document_is_the_package_example_and_passes_its_checks() -> None:
    shipped = json.loads((ROOT / "config/environment-profiles.json").read_text(encoding="utf-8"))
    example = json.loads(
        (PRODUCT / "api/examples/config/environment-profiles.example.json").read_text(encoding="utf-8")
    )
    assert shipped == example["value"]  # content, notice and provenance exactly as the package states them
    assert shipped["notice"] == "Unreviewed draft: needs clinician review before live guidance."
    raw = {
        **documents(),
        ConfigName.ENVIRONMENT_PROFILES: (ROOT / "config/environment-profiles.json").read_bytes(),
    }
    assert findings(raw) == []
    config = load_mission_config(lambda name: raw[name])
    assert [p.template.environment_id for p in config.profiles] == [
        "station_leo",
        "transit",
        "lunar_surface",
        "mars_surface",
    ]
    assert (
        config.confirmations_required == 1 and len(config.event_types) == 10 and len(config.state_facts) == 1
    )


@pytest.mark.trace("T-E23-040", "C12")
def test_the_ground_delay_words_come_from_earth_delay_with_source_ids_moved_out(station: Station) -> None:
    lunar = station.config.profile("lunar_surface")
    assert lunar is not None
    assert lunar.template.ground_delay_text == (
        "Seconds. NASA gives 6 to 10 seconds on the surface of the Moon as an example of a small delay."
    )  # CONTRACTS 12's worked example, checked independently of the document
    assert (
        lunar.template.ground_delay_seconds == 10
    )  # comms_queue.simulated_one_way_delay_seconds of the profile
    assert strip_source_ids("Near real time (EV01, C25). Close.") == "Near real time. Close."


@pytest.mark.trace("T-E23-048", "R43", "C13")
def test_wire_models_validate_and_carry_exactly_the_contract_fields(station: Station) -> None:
    with station.run(operator().actor) as uow:
        active = initialize_mission(
            station.services,
            uow,
            operator(),
            mission_mode="SIM",
            planned_length_days=7,
            site_node_id="node",
            environment_id="station_leo",
        )
    with station.run(operator().actor) as uow:
        proposed = propose_change(
            station.services,
            uow,
            operator(),
            environment_id="lunar_surface",
            reason="operator",
            effective_at=None,
        )
    example = json.loads(
        (PRODUCT / "api/examples/environment/change-proposed.json").read_text(encoding="utf-8")
    )["value"]
    crew = person("per_a", "crew")
    model = profile_model(proposed, is_crew=True, person_id="per_a")
    assert set(model.model_dump()) == set(example)  # no field added, none missing
    assert [a.root for a in model.allowed_actions.root] == ["confirm", "reject"]
    assert profile_model(proposed, is_crew=False, person_id=None).allowed_actions.root == []
    assert profile_model(active, is_crew=True, person_id="per_a").allowed_actions.root == []
    with station.run(crew.actor) as uow:
        counted = act_on_change(
            station.services,
            uow,
            crew,
            profile_id=proposed.profile_id,
            action="confirm",
            expected_revision=proposed.revision,
        )
    assert counted.state == "active" and counted.activated_at == START
    event_example = json.loads(
        (PRODUCT / "api/examples/environment/mission-event-eva-ended.json").read_text(encoding="utf-8")
    )["value"]
    from datetime import timedelta

    from crew_health.mission.domain.events import EventRecord

    record = EventRecord(
        "mev_1",
        "eva_ended",
        START,
        START,
        "person",
        "per_a",
        "SIM",
        "lunar_surface",
        ("per_a",),
        None,
        "known",
        START,
        "a note",
    )
    assert set(event_model(record).model_dump()) == set(event_example)
    from crew_health.mission.domain.overlay import OverlayRecord

    overlay = OverlayRecord(
        "ovl_1",
        1,
        "per_a",
        "eva",
        "planned",
        START + timedelta(hours=1),
        None,
        None,
        "person",
        None,
        None,
        "rel",
        None,
        None,
    )
    assert overlay_model(overlay).planned_start is not None and overlay_model(overlay).source.ref_id is None


@pytest.mark.trace("T-E23-050", "R43", "V75")
def test_a_nonclinical_label_edit_changes_the_new_record_with_no_code_change() -> None:
    renamed = edited(
        documents(),
        ConfigName.ENVIRONMENT_PROFILES,
        lambda d: d["profiles"][2].__setitem__("label", "Moon base Alpha"),
    )
    station = build_station(renamed)
    with station.run(operator().actor) as uow:
        initialize_mission(
            station.services,
            uow,
            operator(),
            mission_mode="SIM",
            planned_length_days=None,
            site_node_id="node",
            environment_id="lunar_surface",
        )
    assert next(iter(station.store.profile_rows.values())).label == "Moon base Alpha"


@pytest.mark.trace("T-E23-055", "AGENTS3", "R43")
def test_a_corrupt_or_missing_release_rejects_the_command_and_changes_nothing(station: Station) -> None:
    garbage = make_release("rel_bad", {**station.raw, ConfigName.ENVIRONMENT_PROFILES: b"\x00 not json"})
    with pytest.raises(ProblemError) as caught:
        with station.run(operator().actor, release=garbage) as uow:
            initialize_mission(
                station.services,
                uow,
                operator(),
                mission_mode="SIM",
                planned_length_days=None,
                site_node_id="node",
                environment_id="station_leo",
            )
    assert caught.value.code.value == "CONFIG_INVALID"
    assert station.store.identity_row is None and station.factory.state.events == []
    missing = {k: v for k, v in station.raw.items() if k != ConfigName.WARNING_RULES}
    with pytest.raises(ProblemError):
        load_missing = make_release("rel_missing", missing)
        with station.run(operator().actor, release=load_missing) as uow:
            initialize_mission(
                station.services,
                uow,
                operator(),
                mission_mode="SIM",
                planned_length_days=None,
                site_node_id="node",
                environment_id="station_leo",
            )
    assert station.store.identity_row is None


@pytest.mark.trace("T-E23-055", "CONTRACTS11")
@pytest.mark.parametrize(
    ("edit_document", "edit", "expect"),
    [
        (ConfigName.ENVIRONMENT_PROFILES, lambda d: d["profiles"].pop(), "fails its schema"),
        (
            ConfigName.ENVIRONMENT_PROFILES,
            lambda d: d["profiles"].append(dict(d["profiles"][0])),
            "fails its schema",
        ),
        (
            ConfigName.ENVIRONMENT_PROFILES,
            lambda d: d["profiles"][0]["sensor_type_ids"].append("ghost"),
            "not in the catalog",
        ),
        (
            ConfigName.ENVIRONMENT_PROFILES,
            lambda d: d["effect_filters"][0].__setitem__("effect_index", 99),
            "no such hazard effect",
        ),
        (
            ConfigName.ENVIRONMENT_PROFILES,
            lambda d: d["effect_filters"][0].__setitem__("effect_starts_with", "Wrong"),
            "does not match",
        ),
        (
            ConfigName.ENVIRONMENT_PROFILES,
            lambda d: d["mission_event_types"].append(dict(d["mission_event_types"][0])),
            "duplicate event type id",
        ),
        (
            ConfigName.ENVIRONMENT_PROFILES,
            lambda d: d["state_facts"][0]["set_by_event_types"].append("ghost"),
            "unknown event type",
        ),
    ],
)
def test_each_mission_loader_check_rejects_its_violation(
    edit_document: ConfigName, edit: object, expect: str
) -> None:
    found = findings(edited(documents(), edit_document, edit))  # type: ignore[arg-type]
    assert any(expect in line for line in found), found


@pytest.mark.trace("T-E23-055", "CONTRACTS11")
def test_check_7_rule_one_profile_per_environment_and_known_sensor_types() -> None:
    environments = ("station_leo", "transit", "lunar_surface", "mars_surface")
    ok = checks.check_profiles(list(environments), [["co2"]] * 4, environments, {"co2"})
    assert ok == ()
    missing = checks.check_profiles(["station_leo"], [["co2"]], environments, {"co2"})
    assert [f.message for f in missing] == [
        "0 profiles for lunar_surface",
        "0 profiles for mars_surface",
        "0 profiles for transit",
    ]
    doubled = checks.check_profiles(
        ["station_leo", "station_leo"], [["ghost"], []], ("station_leo",), {"co2"}
    )
    assert [f.message for f in doubled] == ["2 profiles for station_leo", "not in the catalog"]


@pytest.mark.trace("T-E23-055", "T-E23-092", "S5.11")
def test_event_type_and_fact_bounds_at_the_limit_and_over_it() -> None:
    raw = documents()
    assert findings(raw) == []
    tight = checks.MissionBounds(max_event_types=10, max_state_facts=1)
    release = candidate(raw)
    assert Check7Profiles(tight).check(release) == ()
    over = checks.MissionBounds(max_event_types=9, max_state_facts=0)
    messages = [f.message for f in Check7Profiles(over).check(release)]
    assert any("max_event_types" in m for m in messages) and any("max_state_facts" in m for m in messages)
    assert checks.check_events_and_facts(["a"], [("ghost", None)], ("eva",), [], BOUNDS, 0) != ()


@pytest.mark.trace("T-E23-090", "8.1", "D1")
def test_the_guard_rejects_a_release_without_the_active_or_proposed_profile() -> None:
    assert checks.guard_environments_kept({"station_leo", "transit"}, "station_leo", "transit") == ()
    assert len(checks.guard_environments_kept({"transit"}, "station_leo", None)) == 1
    assert len(checks.guard_environments_kept({"station_leo"}, "station_leo", "mars_surface")) == 1
    assert checks.guard_environments_kept(set(), None, None) == ()
