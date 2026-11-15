"""Start, idempotency and subject-only access with the real platform command ledger."""

from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.contracts_gen.schemas.plan_run_schema import PlanRun
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, SessionContext

from .plans_stack import obj, rows, session, stack


@pytest.mark.trace("T28-12", "T28-30", "D9")
def test_start_pins_plan_and_emits_contract_valid_subject_event(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    parsed = PlanRun.model_validate(run)
    assert parsed.revision == 1
    assert parsed.subject.person_id.root == "mara"
    assert all(step.state == "open" for step in parsed.steps)
    assert {pin.name.root for pin in parsed.config_pins} == {
        "onboard-plans",
        "skills-catalog",
        "role-rules",
        "kit-map",
        "ui-copy",
    }
    assert [event["type"] for event in s.events] == ["plan_run.changed"]
    event = s.events[0]
    assert event["data"] == run
    assert event["scope"] == {"kind": "person", "person_id": "mara", "environment_id": "lunar_surface"}
    assert obj(event["data"])["allowed_actions"]
    assert s.query("SELECT plan_version FROM plans_run")[0][0]
    assert s.query("SELECT safe_body FROM platform_command_ledger") == ((None,),)


@pytest.mark.trace("T28-12", "SYSTEM-5.8")
def test_replay_reconstructs_current_run_and_changed_body_conflicts(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    changed = s.act(run, "step_done", step_id="icl10_note_episodes")
    assert s.start() == changed
    assert len(s.events) == 2
    with pytest.raises(ProblemError) as error:
        s.start(plan_id="plan_eye_injury")
    assert error.value.code is ErrorCode.DUPLICATE_COMMAND
    assert len(s.events) == 2


@pytest.mark.trace("T28-12", "R28-22")
def test_help_session_start_and_unverifiable_episode_fail_closed(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start(episode_id=None, help_session_id="hlp_test")
    assert run["episode_id"] is None
    assert run["help_session_id"] == "hlp_test"
    s.rebind(episodes=None)
    with pytest.raises(ProblemError) as error:
        s.start(key="episode_unverifiable")
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == "/episode_id"
    assert len(s.events) == 1


@pytest.mark.trace("T28-12", "T28-08")
def test_start_requires_an_active_environment_covered_by_plan(tmp_path: Path) -> None:
    s = stack(tmp_path)
    for environment in (None, "unknown_environment"):
        s.mission.environment = environment
        with pytest.raises(ProblemError) as error:
            s.start(key="environment_" + str(environment))
        assert error.value.code is ErrorCode.CONTEXT_INAPPLICABLE
    assert s.query("SELECT count(*) FROM plans_run") == ((0,),)
    assert s.events == []


@pytest.mark.trace("T28-12")
def test_duplicate_open_run_returns_existing_run_in_current_state(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    with pytest.raises(ProblemError) as error:
        s.start(key="another_start")
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.current_state is not None
    assert error.value.current_state["plan_run_id"] == run["plan_run_id"]
    assert len(s.events) == 1


@pytest.mark.trace("T28-30", "D9", "AGENTS-4")
@pytest.mark.parametrize(
    "person",
    [
        SessionContext(
            "ses_station", "station", Actor("station", "station_1"), None, frozenset({Role.STATION})
        ),
        SessionContext("ses_ground", "ground", Actor("ground", "ground_1"), None, frozenset({Role.GROUND})),
        session("tomas"),
        session("ilya", officer=True),
        SessionContext(
            "ses_assistant", "crew", Actor("assistant", "assistant_1"), "mara", frozenset({Role.CREW})
        ),
    ],
)
def test_only_subject_person_can_start_or_act(tmp_path: Path, person: SessionContext) -> None:
    s = stack(tmp_path)
    run = s.start()
    code = ErrorCode.ACTION_NOT_ALLOWED if person.actor.kind != "person" else ErrorCode.UNAUTHORIZED_SCOPE
    with pytest.raises(ProblemError) as error:
        s.runs.start(
            person,
            {"plan_id": "plan_icl10", "subject_person_id": "mara", "episode_id": "ep_test"},
            "unauthorized_start",
        )
    assert error.value.code is code
    with pytest.raises(ProblemError) as error:
        s.actions.act(
            person,
            str(run["plan_run_id"]),
            {"action": "close", "disposition": "other", "client_time": "2026-11-14T13:37:00Z"},
            "unauthorized_act",
            1,
        )
    assert error.value.code is code
    assert len(s.events) == 1


@pytest.mark.trace("T28-12", "T28-30", "D9")
def test_get_and_list_expose_only_own_open_runs(tmp_path: Path) -> None:
    s = stack(tmp_path)
    own = s.start()
    other = s.start(key="other_person", episode_id="ep_other", person=session("tomas"))
    page = s.runs.list(s.session, None, None, 20)
    assert [item["plan_run_id"] for item in rows(page["items"])] == [own["plan_run_id"]]
    assert s.runs.get(s.session, str(own["plan_run_id"])) == own
    with pytest.raises(ProblemError) as error:
        s.runs.get(s.session, str(other["plan_run_id"]))
    assert error.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    s.act(own, "close", disposition="resolved_on_board")
    assert s.runs.list(s.session, None, None, 20)["items"] == []
    with pytest.raises(ProblemError) as error:
        s.runs.list(s.session, "tomas", None, 20)
    assert error.value.code is ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("R28-22", "AGENTS-5")
@pytest.mark.parametrize("missing", ["roster", "subject", "ui_copy"])
def test_run_wire_never_invents_a_subject_name_or_copy(tmp_path: Path, missing: str) -> None:
    from crew_health.contracts_gen.enums import ConfigName
    from crew_health.kernel.testing import MemoryRelease

    s = stack(tmp_path)
    if missing == "roster":
        s.rebind(roster=None)
    elif missing == "subject":
        s.crew.members = tuple(m for m in s.crew.members if m.person_id != "mara")
    else:
        del s.docs[ConfigName.UI_COPY]
        s.release = MemoryRelease("release_no_copy", s.docs)
    with pytest.raises(ProblemError) as error:
        s.start()
    assert error.value.code is ErrorCode.INTERNAL_ERROR
    assert error.value.retryable is True
    assert s.query("SELECT count(*) FROM plans_run") == ((0,),)
    assert s.events == []
