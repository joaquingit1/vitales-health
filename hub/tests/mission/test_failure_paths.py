"""Failure cases of AGENTS 6 for the mission module on the real stack (E23 12 and section 9).

Traces: T-E23-005 and T-E23-056 (CAS at the storage layer), T-E23-008 and T-E23-013 (absolute deadlines caught up
at restart), T-E23-015 and T-E23-067 (state facts), T-E23-044 (event order), T-E23-051 (nothing partial),
T-E23-055 (corrupt configuration), T-E23-073 (before init), T-E23-089 (fail closed until bound)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from e23_station import START, Station, build_station

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.mission import module as mission_module
from crew_health.mission.adapters.live_state import LiveMissionState, _unknown_word
from crew_health.mission.adapters.sqlite_repository import SqliteMissionStore
from crew_health.mission.api.router import MissionApi
from crew_health.mission.application.ports import MissionInitializer
from crew_health.mission.application.ports import MissionStatePort as OwnPort
from crew_health.mission.application.use_cases import put_fact
from crew_health.mission.domain.errors import IntegrityViolation, StaleVersion
from crew_health.mission.domain.state_facts import FactState

pytestmark = pytest.mark.e23


def iso(st: Station, seconds: int = 0) -> str:
    return (st.clock.at + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ready(tmp: Path, environment: str = "station_leo") -> Station:
    st = build_station(tmp / "hub")
    st.initialize("SIM", environment)
    st.advance(seconds=30)
    return st


def live(st: Station) -> Any:
    return st.resolver().require(MissionInitializer)._live  # type: ignore[attr-defined]


def store(st: Station) -> SqliteMissionStore:
    state = st.resolver().require(OwnPort)
    assert isinstance(state, LiveMissionState)
    found = state.reader
    assert isinstance(found, SqliteMissionStore)
    return found


def rows(st: Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def post(st: Station, path: str, body: Any, who: str, key: str, **headers: str) -> Any:
    return st.client.post(path, json=body, headers=st.headers(who, key, **headers))


def refused(call: Any) -> ProblemError:
    with pytest.raises(ProblemError) as caught:
        call()
    return caught.value


def event_body(st: Station, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "event_type": kind,
        "occurred_at": iso(st),
        "person_ids": ["per_a"],
        "note": None,
        "origin_mode": "SIM",
        **extra,
    }


# --- fail closed until bound (T-E23-089) -----------------------------------------------------------------


def test_every_late_bound_part_refuses_loudly_and_retryably_before_the_services_exist() -> None:
    state = mission_module._Live(MissionApi())  # type: ignore[attr-defined]
    calls = [
        state.services,
        state.api.bound,
        lambda: mission_module._storage_hook(state, None, None)(),  # type: ignore[attr-defined,arg-type]
        lambda: mission_module._Initializer(state).initialize(  # type: ignore[attr-defined]
            operator_id="o",
            mission_mode="SIM",
            planned_length_days=None,
            site_node_id="n",
            environment_id="e",
        ),
    ]
    for call in calls:
        error = refused(call)
        assert error.code == ErrorCode.INTERNAL_ERROR and error.retryable is True
    mission_module._sweep_hook(state)()  # type: ignore[attr-defined]  # nothing to finish yet, nothing raised


def test_an_initializer_without_its_platform_refuses_and_an_unbound_loader_check_is_a_finding() -> None:
    state = mission_module._Live(MissionApi())  # type: ignore[attr-defined]
    state.api.services = SimpleNamespace()  # type: ignore[assignment]
    error = refused(
        lambda: mission_module._Initializer(state).initialize(  # type: ignore[attr-defined]
            operator_id="o",
            mission_mode="SIM",
            planned_length_days=None,
            site_node_id="n",
            environment_id="e",
        )
    )
    assert error.code == ErrorCode.INTERNAL_ERROR and error.retryable is True
    check = mission_module._Late("7")  # type: ignore[attr-defined]
    (finding,) = check.check(SimpleNamespace())  # type: ignore[arg-type]
    assert finding.check_id == "7" and "unavailable" in finding.message


# --- corrupt configuration (T-E23-055) -------------------------------------------------------------------


def test_a_corrupt_unknown_word_in_ui_copy_is_a_config_problem_never_a_blank_label() -> None:
    def release(raw: bytes) -> Any:
        return SimpleNamespace(document=lambda name: SimpleNamespace(raw=raw))

    for raw in (b"{not json", b'{"designed_states": {}}', b'{"designed_states": {"unknown": ""}}'):
        assert refused(lambda raw=raw: _unknown_word(release(raw))).code == ErrorCode.CONFIG_INVALID  # type: ignore[misc]
    good = json.dumps({"designed_states": {"unknown": "Not known"}}).encode()
    assert _unknown_word(release(good)) == "Not known"


# --- before init and the consumer ports (T-E23-073) ---------------------------------------------------


def test_before_init_every_port_answers_nothing_and_the_day_label_is_retryable(tmp_path: Path) -> None:
    st = build_station(tmp_path / "fresh")
    state = st.resolver().require(OwnPort)
    assert state.current() is None and state.mission_mode() is None and state.active_environment_id() is None
    error = refused(lambda: state.inline_label_at(st.clock.at))  # type: ignore[attr-defined]
    assert error.code == ErrorCode.INTERNAL_ERROR and error.retryable is True


def test_the_live_state_serves_overlays_intervals_returns_and_the_day_end_from_the_real_store(
    tmp_path: Path,
) -> None:
    st = ready(tmp_path)
    state = st.resolver().require(OwnPort)
    assert isinstance(state, LiveMissionState)
    made = post(st, "/v1/overlays", {"overlay": "sleep", "started_at": iso(st)}, "crew_a", "key_0000a")
    assert made.status_code == 201, made.text
    st.advance(seconds=600)
    with st.begin() as uow:
        mine = state.overlays(uow, "per_a", START)
        assert [o.kind for o in mine] == ["sleep"] and state.overlays(uow, "per_b", START) == ()
        assert state.return_option(uow, "station_leo") == state.return_option(None, "station_leo")
    assert state.return_option(None, "no_such_environment") is None
    assert state.day_end(st.clock.at) > st.clock.at
    spans = state.intervals("per_a", ["sleep"], START, st.clock.at + timedelta(days=1), 10)
    assert [o.kind for o in spans] == ["sleep"]
    over = refused(lambda: state.intervals("per_a", ["sleep"], START, st.clock.at + timedelta(days=1), 0))
    assert over.code == ErrorCode.PAYLOAD_TOO_LARGE  # never a truncated list presented as complete
    assert state.place_facts(None, START, st.clock.at) == ()  # type: ignore[arg-type]  # nothing composed here


def test_a_missing_record_read_by_id_is_not_found(tmp_path: Path) -> None:
    st = ready(tmp_path)
    assert (
        st.client.get("/v1/environment/changes/env_missing", headers=st.headers("crew_a")).status_code == 404
    )
    gone = post(
        st,
        "/v1/overlays/ovl_missing/end",
        {"ended_at": iso(st)},
        "operator",
        "key_0000b",
        **{"If-Match": '"1"'},
    )
    assert gone.status_code == 404


# --- storage layer: CAS, bounds, tampering (T-E23-005, T-E23-056, AGENTS 6) ------------------------------


def test_a_stale_write_of_a_profile_or_overlay_is_refused_at_the_store_and_changes_nothing(
    tmp_path: Path,
) -> None:
    st = ready(tmp_path)
    assert (
        post(
            st, "/v1/overlays", {"overlay": "sleep", "started_at": iso(st)}, "crew_a", "key_0000c"
        ).status_code
        == 201
    )
    mission = store(st)
    with st.db.snapshot() as view:
        profile = mission.profiles(view)[0]
        overlay = mission.overlays_of(view, ["per_a"])[0]
    before = rows(st, "SELECT record_json FROM mission_environment") + rows(
        st, "SELECT record_json FROM mission_overlay"
    )
    for record, save in ((profile, mission.save_profile), (overlay, mission.save_overlay)):
        with pytest.raises(StaleVersion), st.begin() as uow:
            save(uow, replace(record, revision=record.revision + 5))  # type: ignore[call-arg]  # the revision it read is not current
    after = rows(st, "SELECT record_json FROM mission_environment") + rows(
        st, "SELECT record_json FROM mission_overlay"
    )
    assert after == before


def test_the_store_refuses_a_row_bound_outside_its_range_and_a_read_above_it(tmp_path: Path) -> None:
    for bad in (0, 100001):
        with pytest.raises(ValueError, match="bound"):
            SqliteMissionStore(row_limit=bad)
    st = ready(tmp_path)
    for who, key in (("crew_a", "key_ovl00"), ("crew_b", "key_ovl01")):
        made = post(st, "/v1/overlays", {"overlay": "sleep", "started_at": iso(st)}, who, key)
        assert made.status_code == 201, made.text
    with st.db.snapshot() as view:
        assert len(store(st).overlays_of(view, ["per_a", "per_b"])) == 2
        with pytest.raises(IntegrityViolation):  # more rows than the bound is a failure, never a cut list
            SqliteMissionStore(row_limit=1).overlays_of(view, ["per_a", "per_b"])


def test_the_schema_itself_refuses_a_tampered_mission_identity(tmp_path: Path) -> None:
    st = ready(tmp_path)
    connection = sqlite3.connect(st.db.path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE mission_identity SET mission_mode = 'BOGUS'")
        assert connection.execute("SELECT mission_mode FROM mission_identity").fetchone() == ("SIM",)
    finally:
        connection.close()


# --- state facts and the event order (T-E23-015, T-E23-044, T-E23-067) ----------------------------------


def test_a_listed_event_sets_then_clears_a_state_fact_and_an_older_one_changes_nothing(
    tmp_path: Path,
) -> None:
    st = ready(tmp_path, "mars_surface")
    mission = store(st)
    first = post(st, "/v1/mission-events", event_body(st, "local_water_source_added"), "crew_a", "key_fact01")
    assert first.status_code == 201, first.text
    with st.db.snapshot() as view:
        held = mission.fact_states(view)["local_water_untested"]
    assert held.value is True and held.environment_id == "mars_surface"
    st.advance(seconds=60)
    tested = post(st, "/v1/mission-events", event_body(st, "local_water_tested"), "crew_a", "key_fact02")
    assert tested.status_code == 201, tested.text
    with st.db.snapshot() as view:
        assert mission.fact_states(view)["local_water_untested"].value is False  # updated in place, one row
    assert len(rows(st, "SELECT fact_key FROM mission_state_fact")) == 1
    late = event_body(st, "local_water_source_added", occurred_at=iso(st, -3000))
    assert post(st, "/v1/mission-events", late, "crew_a", "key_fact03").status_code == 201
    with st.db.snapshot() as view:
        assert (
            mission.fact_states(view)["local_water_untested"].value is False
        )  # a delayed event never overwrites


def test_the_same_fact_in_two_environments_reads_as_the_one_with_the_newer_cause(tmp_path: Path) -> None:
    st = ready(tmp_path)
    mission = store(st)
    older = FactState("local_water_untested", "mars_surface", True, START, "mev_a", "SIM")
    newer = FactState("local_water_untested", "transit", False, START + timedelta(hours=1), "mev_b", "SIM")
    services = live(st).services()
    with st.begin() as uow:
        for fact in (newer, older):
            put_fact(services, uow, fact)
    with st.db.snapshot() as view:
        assert mission.fact_states(view)["local_water_untested"] == newer
    with st.begin() as uow:
        put_fact(services, uow, replace(older, cause_event_id="mev_c"))  # the update path of one key
    assert len(rows(st, "SELECT fact_key FROM mission_state_fact")) == 2


def test_events_page_in_both_directions_around_a_cursor_and_an_unknown_cursor_skips_nothing(
    tmp_path: Path,
) -> None:
    st = ready(tmp_path)
    mission = store(st)
    ids = []
    for n in range(3):
        st.advance(seconds=10)
        made = post(st, "/v1/mission-events", event_body(st, "eva_planned"), "crew_a", f"key_evt0{n}")
        assert made.status_code == 201, made.text
        ids.append(made.json()["event_id"])
    with st.db.snapshot() as view:
        ascending = [e.event_id for e in mission.events(view, since=START, after_event_id=None, limit=10)]
        after_first = [e.event_id for e in mission.events(view, since=START, after_event_id=ids[0], limit=10)]
        unknown = [e.event_id for e in mission.events(view, since=START, after_event_id="mev_nope", limit=10)]
        newest = [e.event_id for e in mission.events_before(view, before_event_id=None, limit=10)]
        before_last = [e.event_id for e in mission.events_before(view, before_event_id=ids[2], limit=10)]
        before_unknown = [
            e.event_id for e in mission.events_before(view, before_event_id="mev_nope", limit=10)
        ]
    assert ascending == ids and after_first == ids[1:] and unknown == ids
    assert newest == ids[::-1] and before_last == ids[:2][::-1] and before_unknown == ids[::-1]


# --- absolute deadlines caught up at restart (T-E23-008, T-E23-013, AGENTS 6) ----------------------------


def restart_sweep(st: Station) -> None:
    mission_module._sweep_hook(live(st))()  # type: ignore[attr-defined]  # what the hub runs before it serves again


def test_a_restart_after_the_effective_time_activates_the_proposal_the_scheduler_never_ran(
    tmp_path: Path,
) -> None:
    st = ready(tmp_path)
    later = iso(st, 3600)
    proposed = post(
        st,
        "/v1/environment/changes",
        {"environment_id": "transit", "reason": "demo_switch", "effective_at": later},
        "operator",
        "key_rest01",
    )
    assert proposed.status_code == 201, proposed.text
    profile = proposed.json()
    for n, who in enumerate(("crew_a", "crew_b")[: profile["confirmations_required"]]):
        done = post(
            st,
            f"/v1/environment/changes/{profile['profile_id']}/actions",
            {"action": "confirm"},
            who,
            f"key_rest1{n}",
            **{"If-Match": f'"{profile["revision"] + n}"'},
        )
        assert done.status_code == 200, done.text
    assert (
        st.client.get("/v1/environment", headers=st.headers("crew_a")).json()["environment_id"]
        == "station_leo"
    )
    restart_sweep(st)  # too early: nothing is done ahead of its absolute time
    assert (
        st.client.get("/v1/environment", headers=st.headers("crew_a")).json()["environment_id"]
        == "station_leo"
    )
    st.advance(seconds=7200)  # the hub was down, so no scheduled job ran
    restart_sweep(st)
    assert (
        st.client.get("/v1/environment", headers=st.headers("crew_a")).json()["environment_id"] == "transit"
    )


def test_a_restart_after_the_day_end_ends_a_post_eva_overlay_the_scheduler_never_ran(tmp_path: Path) -> None:
    st = ready(tmp_path)
    assert (
        post(st, "/v1/mission-events", event_body(st, "eva_started"), "crew_a", "key_eva001").status_code
        == 201
    )
    st.advance(seconds=60)
    assert (
        post(st, "/v1/mission-events", event_body(st, "eva_ended"), "crew_a", "key_eva002").status_code == 201
    )
    states = lambda: sorted(  # noqa: E731
        (i["overlay"], i["state"])
        for i in st.client.get("/v1/overlays", headers=st.headers("crew_a")).json()["items"]
    )
    assert ("post_eva", "active") in states()
    restart_sweep(st)
    assert ("post_eva", "active") in states()  # the day has not ended yet
    st.advance(days=3)
    restart_sweep(st)
    assert ("post_eva", "ended") in states() and ("post_eva", "active") not in states()


def test_the_restart_sweep_never_confirms_a_proposal_nobody_confirmed(tmp_path: Path) -> None:
    st = ready(tmp_path)
    proposed = post(
        st,
        "/v1/environment/changes",
        {"environment_id": "transit", "reason": "demo_switch"},
        "operator",
        "key_rest02",
    )
    assert proposed.status_code == 201
    restart_sweep(st)
    assert (
        st.client.get("/v1/environment", headers=st.headers("crew_a")).json()["environment_id"]
        == "station_leo"
    )


# --- refused init, unknown codes, no clock (T-E23-073, S5.2, AGENTS 5) ---------------------------------


def init_call(st: Station, caller: Any, **override: Any) -> Any:
    from crew_health.mission.application.init import initialize_mission

    arguments: dict[str, Any] = {
        "mission_mode": "SIM",
        "planned_length_days": None,
        "site_node_id": "node_1",
        "environment_id": "station_leo",
    }
    arguments.update(override)
    with st.begin() as uow:
        return initialize_mission(live(st).services(), uow, caller, **arguments)


def test_init_refuses_a_non_operator_a_non_positive_length_and_an_unknown_environment_and_stores_nothing(
    tmp_path: Path,
) -> None:
    from crew_health.kernel.uow import Actor
    from crew_health.mission.application.use_cases import Caller
    from crew_health.mission.domain.errors import InvalidValue, UnauthorizedScope

    st = build_station(tmp_path / "fresh")
    crew = Caller(Actor("person", "per_a"), "per_a", frozenset({"crew"}))
    operator = Caller(Actor("operator", "operator_sim"), None, frozenset({"operator"}))
    with pytest.raises(UnauthorizedScope):
        init_call(st, crew)
    for days in (0, -3):
        with pytest.raises(InvalidValue):
            init_call(st, operator, planned_length_days=days)
    with pytest.raises(InvalidValue):
        init_call(st, operator, environment_id="no_such_environment")
    assert rows(st, "SELECT count(*) FROM mission_identity")[0][0] == 0
    assert rows(st, "SELECT count(*) FROM mission_environment")[0][0] == 0


def test_a_domain_code_outside_the_contract_is_an_internal_fault_and_a_known_one_keeps_its_code() -> None:
    from crew_health.mission.application.commands import problem_of
    from crew_health.mission.domain.errors import MissionDomainError, NotFound

    unknown = problem_of(MissionDomainError("NOT_A_CONTRACT_CODE"))
    assert unknown.code == ErrorCode.INTERNAL_ERROR and unknown.retryable is False
    assert problem_of(NotFound("x")).code == ErrorCode.NOT_FOUND
    assert problem_of(MissionDomainError("INTERNAL_ERROR")).retryable is True


def test_a_session_that_is_not_the_identified_actor_is_refused_before_any_unit_of_work() -> None:
    from crew_health.mission.application.commands import execute

    session = SimpleNamespace(actor=SimpleNamespace(id="per_a"))
    identity = SimpleNamespace(actor_ref="per_b")
    error = refused(
        lambda: execute(SimpleNamespace(uows=None), session, identity, lambda uow: None, release_header=None)  # type: ignore[arg-type]
    )
    assert error.code == ErrorCode.UNAUTHORIZED_SCOPE


def test_without_a_clock_stream_an_event_instant_is_unknown_and_orders_nothing(tmp_path: Path) -> None:
    from crew_health.mission.application.hub_time import event_time, time_input

    st = ready(tmp_path)
    unknown = event_time(None, None, None, source_clock_id="hub_clock", at=st.clock.at, origin_mode="SIM")  # type: ignore[arg-type]
    assert unknown.state == "unknown" and unknown.effective_at is None
    no_segment = SimpleNamespace(for_sequence=lambda view, **kw: None)
    stamp = time_input(no_segment, None, source_clock_id="hub_clock", at=st.clock.at, origin_mode="SIM")  # type: ignore[arg-type]
    assert stamp.segment_id == "unmapped"  # an unmapped stamp normalizes to unknown, never to arrival time
