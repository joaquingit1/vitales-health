"""The ports E24 provides, the producers it listens to and the failures of its HTTP edge, on a real station.

Consumers see only the exported Protocols (`WarningQueries`, `WarningCommands`, E26's structural `WarningQueries`),
resolved through the registry as the composed hub resolves them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.help.application import ports as help_ports
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import Actor, SessionContext, UnitOfWorkFactory
from crew_health.mission.application.ports import (
    MissionEventRecorded,
    MissionStatePort,
    StateFactChanged,
)
from crew_health.rules.api.router import RulesApi, build_router
from crew_health.rules.application import commands, core, handlers
from crew_health.rules.application.ports import WarningCommands, WarningQueries
from crew_health.rules.application.presentation import WarningPresentation
from crew_health.rules.application.stores import WarningStore
from crew_health.rules.domain import engine
from crew_health.rules.domain.errors import InvariantViolation, NotVisible
from crew_health.rules.domain.vocabulary import WarningAction

from . import rules_station as base
from .e24_station import build, co2_act_hour

pytestmark = pytest.mark.E24


def rows(st: base.Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def station(tmp: Path, environment: str = "station_leo") -> base.Station:
    st = build(tmp, registry={"channels": [base.channel("ch_co2", "co2", "co2", "SIM", "mmHg")]})
    st.initialize("SIM", environment)
    st.advance(seconds=3600)
    return st


def publish(st: base.Station, *events: object) -> None:
    with st.begin() as uow:
        for event in events:
            uow.publish(event)


def storm(st: base.Station) -> MissionEventRecorded:
    return MissionEventRecorded(
        "evt_storm",
        "space_weather_alert",
        "Alert",
        st.clock.at,
        None,
        st.clock.at,
        "SIM",
        "station_leo",
        (),
        None,
    )


def runtime(st: base.Station) -> core.Runtime:
    return core.Runtime(
        settings=st.db.settings,
        ids=st.db.ids,
        mission=st.resolver().optional(MissionStatePort),
        binding=None,
        health=None,
    )


def warning_id(st: base.Station, definition: str) -> str:
    return str(rows(st, f"SELECT warning_id FROM rules_warning WHERE definition_id = '{definition}'")[0][0])


# --- the provided ports ------------------------------------------------------------------------------


@pytest.mark.trace("T-E24-112")
def test_the_query_ports_answer_what_consumers_need_and_nothing_more(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    publish(st, storm(st))
    co2_act_hour(st, "co2")
    queries = st.resolver().require(WarningQueries)
    crew = st.security.tokens["crew_a"]
    storm_id, sensor_id = warning_id(st, "radiation_storm_shelter"), warning_id(st, "co2_above_limit")
    assert queries.visible(crew, storm_id) and not queries.visible(crew, "wrn_none")
    assert queries.count_active_visible(crew) == 2
    shared = queries.active_shared_at_or_above("act")
    assert {w.warning_id for w in shared} == {storm_id, sensor_id}
    assert [w.level for w in queries.active_shared_at_or_above("emergency")] == ["emergency"]
    help_queries = st.resolver().require(help_ports.WarningQueries)
    assert help_queries.visible(crew, storm_id) and not help_queries.visible(crew, "wrn_none")
    fact = shared[0]
    assert not hasattr(fact, "steps") and not hasattr(fact, "notes")  # lifecycle facts only


@pytest.mark.trace("T-E24-112", "UC-R7")
def test_routine_overrides_are_a_set_union_and_a_final_warning_refuses_them(
    patched: None, tmp_path: Path
) -> None:
    st = station(tmp_path)
    publish(st, storm(st))
    wid = warning_id(st, "radiation_storm_shelter")
    port = st.resolver().require(WarningCommands)
    with st.begin() as uow:
        port.attach_routine_overrides(uow, wid, ["ovr_b", "ovr_a"])
    assert rows(st, f"SELECT revision FROM rules_warning WHERE warning_id = '{wid}'") == [(2,)]
    with st.begin() as uow:
        port.attach_routine_overrides(uow, wid, ["ovr_a"])  # already attached: nothing changes
    assert rows(st, f"SELECT revision FROM rules_warning WHERE warning_id = '{wid}'") == [(2,)]
    detail = st.client.get(f"/v1/warnings/{wid}", headers=st.headers("crew_a")).json()
    assert detail["routine_override_ids"] == ["ovr_a", "ovr_b"]
    with st.begin() as uow, pytest.raises(ProblemError) as caught:
        port.attach_routine_overrides(uow, "wrn_none", ["ovr_a"])
    assert caught.value.code is ErrorCode.INVALID_TRANSITION


@pytest.mark.trace("T-E24-080")
def test_the_presentation_port_serves_the_hub_internal_readers(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    publish(st, storm(st))
    presentation = st.resolver().require(WarningPresentation)
    shown = presentation.for_session(st.security.tokens["crew_a"])
    assert shown.emergencies == (warning_id(st, "radiation_storm_shelter"),)
    assert presentation.replicas(st.security.tokens["crew_a"]) == ()


# --- producers: state facts (3.5) -----------------------------------------------------------------------


@pytest.mark.trace("T-E24-038")
def test_a_state_fact_raises_while_it_holds_and_clears_when_it_stops(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path, "mars_surface")
    fact = StateFactChanged("local_water_untested", True, "mars_surface", st.clock.at, "evt_water", "SIM")
    publish(st, fact)
    assert rows(
        st, "SELECT state, level FROM rules_warning WHERE definition_id = 'local_water_untested'"
    ) == [("raised", "watch")]
    st.advance(seconds=60)
    publish(
        st, StateFactChanged("local_water_untested", False, "mars_surface", st.clock.at, "evt_water2", "SIM")
    )
    assert rows(
        st, "SELECT state, clear_reason FROM rules_warning WHERE definition_id = 'local_water_untested'"
    ) == [("cleared", "condition_false")]


# --- the scheduler's handlers on their own (UC-R10, UC-R13) ---------------------------------------------


def job(key: str) -> ScheduledJob:
    return ScheduledJob(key, core.DEADLINE_HANDLER, AbsoluteDeadline(base.START), {"key": key}, None)


@pytest.mark.trace("UC-R10")
def test_a_deadline_that_already_fired_or_never_existed_does_nothing(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    rt = runtime(st)
    with st.begin(Actor("engine", "scheduler")) as uow:
        handlers.fire_deadline(rt, uow, job("expire:wrn_nothing"))
        handlers.fire_deadline(rt, uow, job("ground_silence:nothing"))
    assert rows(st, "SELECT count(*) FROM rules_pending") == [(0,)]


@pytest.mark.trace("UC-R13")
def test_recovery_with_no_rows_marks_the_engine_healthy_and_a_storage_fault_is_never_swallowed(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    st = station(tmp_path)
    rt = runtime(st)
    with st.begin(Actor("engine", "scheduler")) as uow:
        handlers.recover_pending(rt, uow, job(core.RECOVER_HANDLER))  # nothing to recover: no error
    monkeypatch.setattr(engine, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    publish(st, storm(st))
    assert rows(st, "SELECT count(*) FROM rules_pending") == [(1,)]
    monkeypatch.undo()

    def full(*args: object) -> None:
        raise StorageUnavailable("disk_full")

    monkeypatch.setattr(core, "recover_one", full)
    with pytest.raises(StorageUnavailable), st.begin(Actor("engine", "scheduler")) as uow:
        handlers.recover_pending(rt, uow, job(core.RECOVER_HANDLER))
    assert rows(st, "SELECT count(*) FROM rules_pending") == [(1,)]  # the row waits for the disk, unharmed


# --- the HTTP edge (6.2) -------------------------------------------------------------------------------


@pytest.mark.trace("T-E24-054")
def test_no_session_is_unauthenticated_and_an_unbound_module_fails_closed_and_retryable() -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.include_router(build_router(RulesApi()))
    signed_in: list[SessionContext] = []

    @app.middleware("http")
    async def session(request: Request, call_next: Any) -> Any:
        if signed_in:
            request.state.session = signed_in[0]
        return await call_next(request)

    @app.exception_handler(ProblemError)
    async def problem(request: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse({"code": error.code.value, "retryable": error.retryable}, status_code=400)

    client = TestClient(app)
    assert client.get("/v1/warnings").json() == {"code": "UNAUTHENTICATED", "retryable": False}
    signed_in.append(SessionContext("s_x", "crew", Actor("person", "per_a"), "per_a", frozenset()))
    assert client.get("/v1/warnings").json() == {"code": "INTERNAL_ERROR", "retryable": True}  # not bound yet


@pytest.mark.trace("T-E24-058", "T-E24-054")
def test_a_concurrent_revision_move_is_a_stale_version_and_never_a_lost_update(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    st = station(tmp_path)
    publish(st, storm(st))
    wid = warning_id(st, "radiation_storm_shelter")
    monkeypatch.setattr(WarningStore, "update", lambda *a, **k: False)  # another writer moved the revision
    sent = st.client.post(
        f"/v1/warnings/{wid}/actions",
        content=b'{"action":"acknowledge"}',
        headers={**st.headers("crew_a", "c1"), "Content-Type": "application/json", "If-Match": '"1"'},
    )
    assert sent.status_code == 409 and sent.json()["code"] == "STALE_VERSION"
    monkeypatch.undo()
    assert rows(st, f"SELECT state, revision FROM rules_warning WHERE warning_id = '{wid}'") == [
        ("raised", 1)
    ]


@pytest.mark.trace("T-E24-054")
def test_an_action_by_someone_other_than_the_signed_in_actor_is_refused(
    patched: None, tmp_path: Path
) -> None:
    st = station(tmp_path)
    publish(st, storm(st))
    wid = warning_id(st, "radiation_storm_shelter")
    resolver = st.resolver()
    actions = commands.WarningActions(
        runtime(st),
        commands.Platform(
            resolver.require(UnitOfWorkFactory),
            resolver.require(IdempotencyStore),
            resolver.require(CommandReceipts),
        ),
    )
    session: SessionContext = st.security.tokens["crew_a"]
    from crew_health.kernel.commands import command_identity

    identity = command_identity(
        key="k_mismatch", actor_ref="per_b", operation="actOnWarning", target=f"/v1/warnings/{wid}/actions",
        parameters_json=b"{}", body_fingerprint="0" * 64, fingerprint_kind="sha256",
    )  # fmt: skip
    body = commands.ActBody(WarningAction.ACKNOWLEDGE, None, None)
    with pytest.raises(ProblemError) as caught:
        actions.act(session, wid, body, identity, if_match=1, config_release=None)
    assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("T-E24-054")
def test_an_unknown_domain_error_code_is_a_retryable_internal_error_and_never_leaks_its_text() -> None:
    problem = commands.problem_of(InvariantViolation("secret detail"), None)
    assert problem.code is ErrorCode.INTERNAL_ERROR and problem.retryable
    assert commands.problem_of(NotVisible("x"), None).code is ErrorCode.NOT_FOUND


@pytest.mark.trace("INV-E24-12")
def test_the_runtime_names_what_it_lacks_instead_of_guessing(patched: None, tmp_path: Path) -> None:
    st = station(tmp_path)
    rt = runtime(st)
    with st.begin() as uow:
        with pytest.raises(ProblemError) as caught:
            rt.renderer(uow)
        assert caught.value.code is ErrorCode.CAPABILITY_UNAVAILABLE and caught.value.retryable
    assert rt.day_label(st.clock.at) is None  # no label provider: no label
    bare = core.Runtime(settings=st.db.settings, ids=st.db.ids, mission=None, binding=None, health=None)
    with st.begin() as uow, pytest.raises(ProblemError) as unknown:
        bare.node_id(uow)
    assert unknown.value.code is ErrorCode.CAPABILITY_UNAVAILABLE
