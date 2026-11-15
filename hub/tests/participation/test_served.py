"""E22 over the served hub: create_app over real SQLite with E02 identity, E01 clock and configuration.

Producers call the real `CompletionRecorder`; every read goes through HTTP with a real signed in session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from e02_http import bearer, problem, sign_in
from e22_served import Hub, basis, run, session_of, start
from httpx import AsyncClient

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, SessionContext

pytestmark = [pytest.mark.E22, pytest.mark.trace("R31", "R32", "R33", "D10", "V41", "V44", "V45")]
RULES = json.loads(
    (Path(__file__).resolve().parents[3] / "config/participation-rules.json").read_text("utf-8")
)
THRESHOLDS = RULES["patch_thresholds"]
WINDOW = RULES["crew_goal_window_days"]
PEOPLE = ("mara", "ilya", "sofia", "tomas")


def check_days(hub: Hub, person: str, first: int, last: int) -> None:
    for day in range(first, last + 1):
        hub.set_day(day)
        assert hub.produce(basis(person, day)).status == "queued"


async def get(client: AsyncClient, who: str, path: str) -> dict[str, object]:
    """A fresh sign in per read: the hub clock moves by days and sessions expire with it."""
    token = await sign_in(client, None, kind="station") if who == "station" else await sign_in(client, who)
    response = await client.get(path, headers=bearer(token))
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_streak_patch_and_crew_goal_consent_over_http(tmp_path: Path) -> None:
    hub = start(tmp_path, day=2)

    async def journey(client: AsyncClient) -> None:
        first = await get(client, "mara", "/v1/me/streak")
        assert (first["today"], first["current_streak_days"], first["patient_id"]) == ("due", 0, "mara")
        assert (await get(client, "mara", "/v1/me/patches"))["items"] == []
        # Seven consecutive days of checks reach the first configured threshold, whatever its number is.
        span = THRESHOLDS[0]
        await asyncio_noop()
        check_days(hub, "mara", 2, 1 + span)
        streak = await get(client, "mara", "/v1/me/streak")
        assert streak["current_streak_days"] == span and streak["completed_days_total"] == span
        patches = await get(client, "mara", "/v1/me/patches")
        items = patches["items"]
        assert isinstance(items, list) and [i["threshold"] for i in items] == [span]
        design = {d["threshold"]: d["patch_design_id"] for d in RULES["patch_designs"]}
        assert items[0]["patch_design_id"] == design[span]
        assert items[0]["reached_on_mission_day"] == 1 + span
        # Crew goal: off by default, nobody counts.
        goal = await get(client, "tomas", "/v1/crew-goal")
        assert (goal["completed_total"], goal["due_total"], goal["reached"]) == (0, 0, False)

    run(hub.running, journey)
    consent = hub.consent()
    assert consent.get(session_of("mara")).enabled is False
    assert consent.get(session_of("mara")).revision == 0
    day = 1 + THRESHOLDS[0]
    for person in ("mara", "ilya", "sofia"):
        state = consent.put(session_of(person), enabled=True, if_match=0, key="consent_" + person + "_on")
        assert state.enabled and state.revision == 1
    # Enabling counts prospectively: nothing is due until the next mission day.
    hub.set_day(day + 1)
    hub.produce(basis("mara", day + 1))
    hub.produce(basis("ilya", day + 1))
    hub.set_day(day + 2)

    async def after(client: AsyncClient) -> None:
        goal = await get(client, "tomas", "/v1/crew-goal")
        # Three members count on two days (day+1, day+2): 6 due; mara and ilya checked day+1: 2 completed.
        assert (goal["due_total"], goal["completed_total"], goal["reached"]) == (6, 2, False)
        assert set(goal) == {
            "mission_id", "origin_mode", "window_first_mission_day", "window_last_mission_day",
            "completed_total", "due_total", "reached", "as_of", "derivation_version", "mission_clock_version",
        }  # fmt: skip
        assert goal["window_last_mission_day"] - goal["window_first_mission_day"] + 1 == WINDOW

    run(hub.running, after)
    # Revocation removes the member at once: two counting members is below the floor of 3, so no totals at all.
    state = consent.put(session_of("sofia"), enabled=False, if_match=1, key="consent_sofia_off")
    assert (state.enabled, state.revision) == (False, 2)

    async def revoked(client: AsyncClient) -> None:
        goal = await get(client, "tomas", "/v1/crew-goal")
        assert (goal["due_total"], goal["completed_total"], goal["reached"]) == (0, 0, False)

    run(hub.running, revoked)


def test_consent_is_the_crew_persons_own_and_fails_closed(tmp_path: Path) -> None:
    hub = start(tmp_path, day=3)
    consent = hub.consent()
    station = SessionContext(
        "session_station", "station", Actor("station", "stn_galley"), None, frozenset({Role.STATION})
    )
    assistant = SessionContext(
        "session_assistant", "crew", Actor("assistant", "assistant_1"), "mara", frozenset({Role.CREW})
    )
    for session in (station, assistant):
        with pytest.raises(ProblemError) as refused:
            consent.put(session, enabled=True, if_match=0, key="consent_refused_1")
        assert refused.value.code is ErrorCode.UNAUTHORIZED_SCOPE
        with pytest.raises(ProblemError):
            consent.get(session)
    assert consent.get(session_of("mara")).revision == 0


async def asyncio_noop() -> None:
    return None


def test_privacy_between_crew_members(tmp_path: Path) -> None:
    hub = start(tmp_path, day=3)
    check_days(hub, "mara", 2, 3)

    async def journey(client: AsyncClient) -> None:
        _mara, ilya = await sign_in(client, "mara"), await sign_in(client, "ilya")
        galley = await sign_in(client, None, kind="station")
        assert (await get(client, "ilya", "/v1/me/streak"))["patient_id"] == "ilya"
        assert (await get(client, "ilya", "/v1/me/streak"))["completed_days_total"] == 0
        assert (await get(client, "mara", "/v1/me/streak"))["completed_days_total"] == 2
        for path in ("/v1/me/streak?person_id=mara", "/v1/me/streak?patient=mara"):
            problem(await client.get(path, headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE")
        problem(
            await client.get("/v1/me/patches?person_id=mara", headers=bearer(ilya)), 403, "UNAUTHORIZED_SCOPE"
        )
        for path in ("/v1/crew-goal?by=person", "/v1/crew-goal?person_id=mara"):
            problem(await client.get(path, headers=bearer(ilya)), 403, "AGGREGATE_ONLY")
        # The station reads the aggregate only and has no personal view.
        assert (await client.get("/v1/crew-goal", headers=bearer(galley))).status_code == 200
        assert (await client.get("/v1/me/streak", headers=bearer(galley))).status_code in (401, 403)

    run(hub.running, journey)


def test_duplicate_conflict_and_rollback(tmp_path: Path) -> None:
    hub = start(tmp_path, day=2)
    assert hub.produce(basis("mara", 2, "dck_dup")).status == "queued"
    assert hub.produce(basis("mara", 2, "dck_dup")).status == "duplicate"
    # Same identity, different payload: the first is kept and the second recorded.
    assert hub.produce(basis("mara", 2, "dck_dup", hours=1)).status == "conflict"
    rows = hub.station.database
    with rows.snapshot() as view:
        done = view.connection.rows("SELECT COUNT(*) FROM participation_completion", (), limit=1)
        clash = view.connection.rows("SELECT COUNT(*) FROM participation_intent_conflict", (), limit=1)
        audit = view.connection.rows("SELECT COUNT(*) FROM platform_audit", (), limit=1)
    assert (done[0][0], clash[0][0]) == (1, 1)
    assert audit[0][0] >= 1
    # A producer that rolls back leaves no intent.
    recorder = hub.require(
        __import__("crew_health.participation.application.ports", fromlist=["x"]).CompletionRecorder
    )
    with (
        pytest.raises(RuntimeError),
        rows.begin(actor=Actor("person", "mara"), correlation_id="rolled") as uow,
    ):
        recorder.record(uow, basis("mara", 2, "dck_rolled"))
        raise RuntimeError("producer failed")
    with rows.snapshot() as view:
        count = view.connection.rows("SELECT COUNT(*) FROM participation_intent", (), limit=1)
    assert count[0][0] == 1


def test_clock_jump_never_double_counts_or_loses_a_day(tmp_path: Path) -> None:
    hub = start(tmp_path, day=2)
    check_days(hub, "mara", 2, 4)

    async def read(client: AsyncClient, token: str) -> dict[str, object]:
        return await get(client, "mara", "/v1/me/streak")

    async def journey(client: AsyncClient) -> None:
        token = ""
        assert (await read(client, token))["current_streak_days"] == 3
        hub.set_day(9)  # a long outage: days 5 to 8 were missed
        view = await read(client, token)
        assert (view["current_streak_days"], view["longest_streak_days"], view["completed_days_total"]) == (
            0,
            3,
            3,
        )
        hub.set_day(4, 20)  # the clock is corrected back: later days are not eligible yet, nothing is lost
        back = await read(client, token)
        assert (back["current_mission_day"], back["completed_days_total"]) == (4, 3)

    run(hub.running, journey)


def test_the_counting_floor_cannot_be_set_below_three() -> None:
    from crew_health.participation.application.settings import MIN_COUNTING_FLOOR, Limits, specs

    spec = next(s for s in specs() if s.key == "participation.min_counting_members")
    assert MIN_COUNTING_FLOOR == 3 and spec.minimum is not None and int(spec.minimum) == 3
    assert Limits.__dataclass_fields__["min_counting_members"] is not None
