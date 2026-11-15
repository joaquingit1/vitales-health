from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.episodes.application.dto import LinkedFact
from crew_health.episodes.application.overdue import NoticeOverdue
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor

from .builders import GROUND, NOW, OFFICER, PATIENT, ROOT, Station, station


@pytest.fixture
def s(tmp_path: Path) -> Station:
    return station(tmp_path)


@pytest.mark.trace("T-E15-023", "T-E15-009", "D05")
def test_draft_only_exits_after_all_pinned_red_flags(s: Station) -> None:
    ep = s.create(red_flags=("red_one", "red_two"))
    s.answer(ep)
    assert s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"}).json()["state"] == "draft"
    s.answer(ep, "red_two", "unknown")
    result = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"})
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "open"
    with s.db.snapshot() as view:
        assert view.connection.rows(
            "SELECT count(*) FROM episodes_change WHERE episode_id=?", (ep,), limit=1
        ) == ((3,),)


@pytest.mark.trace("T-E15-002", "T-E15-003", "T-E15-004", "R18", "V04")
def test_review_leaves_unresolved_owned_followup(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "request_review"})
    s.act(ep, {"action": "acknowledge"}, session=OFFICER)
    body = {
        "decisions": [{"item": "token", "decision": "needs_more", "rationale": "token"}],
        "unresolved": ["unresolved-token"],
    }
    with s.db.begin(actor=OFFICER.actor, correlation_id="corr_review") as uow:
        rev = s.service.repo.require(uow, ep).revision
        result = s.service.review(
            uow, OFFICER, ep, body, rev, s.identity("cmd_review", body, ep, "recordReview", OFFICER)
        )
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["state"] == "followup"
    assert len(data["acknowledgments"]) == 1
    tasks = data["followup_tasks"]
    assert len(tasks) == 2
    assert [t["kind"] for t in tasks if t["state"] == "open"] == ["review_item"]
    assert next(t for t in tasks if t["kind"] == "review_item")["owner"]["id"] == "officer_a"
    assert next(t for t in tasks if t["kind"] == "review")["closed"]["review_id"] == result["review_id"]


@pytest.mark.trace("T-E15-011", "R48", "D40", "D43")
def test_ground_advice_changes_no_state_or_tasks(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "request_review"})
    s.facts.consent = True
    with s.db.begin(actor=GROUND.actor, correlation_id="corr_review") as uow:
        rev = s.service.repo.require(uow, ep).revision
        body = {"decisions": [], "unresolved": ["token"]}
        s.service.review(
            uow, GROUND, ep, body, rev, s.identity("cmd_ground", body, ep, "recordReview", GROUND)
        )
    data = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"}).json()
    assert data["state"] == "awaiting_review"
    assert len(data["followup_tasks"]) == 1
    assert data["followup_tasks"][0]["state"] == "open"


@pytest.mark.trace("T-E15-014", "V12", "V25", "V26")
def test_privacy_ground_and_restricted_answers(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.answer(ep, "private_question", "declined")
    assert s.client.get("/v1/episodes/" + ep, headers={"Authorization": "other"}).status_code == 404
    assert s.client.get("/v1/episodes/" + ep, headers={"Authorization": "ground"}).status_code == 403
    r = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"})
    assert r.status_code == 200, r.text
    assert r.json()["withheld_answer_count"] == 1
    assert all(a["question_id"] != "private_question" for a in r.json()["answers"])
    s.facts.consent = True
    assert (
        s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"}).json()[
            "withheld_answer_count"
        ]
        == 0
    )
    assert (
        s.client.get("/v1/episodes?patient_id=person_a", headers={"Authorization": "other"}).status_code
        == 403
    )
    listed = s.client.get("/v1/episodes", headers={"Authorization": "officer"}).json()
    assert "private-concern-canary" not in json.dumps(listed)


@pytest.mark.trace("T-E15-026", "V16", "R25")
def test_retry_precedes_changed_preconditions_and_conflicts_body_target(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    body = {"action": "add_followup", "action_text": "token"}
    first = s.act(ep, body, key="cmd_retry")
    second = s.act(ep, body, key="cmd_retry", expected=1)
    assert first == second
    with pytest.raises(ProblemError) as err:
        s.act(ep, {"action": "add_followup", "action_text": "different"}, key="cmd_retry")
    assert err.value.code.value == "DUPLICATE_COMMAND"
    ep2 = s.create()
    s.answer(ep2)
    with pytest.raises(ProblemError) as err:
        s.act(ep2, body, key="cmd_retry")
    assert err.value.code.value == "DUPLICATE_COMMAND"


@pytest.mark.trace("T-E15-007", "T-E15-008", "T-E15-024")
def test_resolution_blocked_then_human_resolution_and_answer_reopens(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.facts.urgency = "active_response"
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "resolve", "reason": "symptoms_reported_resolved"})
    s.facts.urgency = "disposition_recorded"
    s.act(ep, {"action": "resolve", "reason": "symptoms_reported_resolved"})
    s.answer(ep, "red_one", "present")
    data = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"}).json()
    assert data["state"] == "open"
    assert len(data["reopenings"]) == 1
    assert data["urgency_state"] == "disposition_recorded"


@pytest.mark.trace("T-E15-010", "T-E15-027", "V35")
def test_sql_backups_and_rollback(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    for sql in (
        "UPDATE episodes_episode SET state='resolved',revision=revision+1 WHERE episode_id=?",
        "UPDATE episodes_episode SET concern_text='changed',revision=revision+1 WHERE episode_id=?",
        "DELETE FROM episodes_episode WHERE episode_id=?",
    ):
        with (
            pytest.raises(ProblemError),
            s.db.begin(actor=PATIENT.actor, correlation_id="corr_bypass") as uow,
        ):
            uow.connection.execute(sql, (ep,))
    with pytest.raises(ValueError), s.db.begin(actor=PATIENT.actor, correlation_id="corr_crash") as uow:
        s.service.act(
            uow,
            PATIENT,
            ep,
            {"action": "add_followup", "action_text": "token"},
            2,
            s.identity("cmd_crash", {"action": "add_followup", "action_text": "token"}, ep),
        )
        raise ValueError("crash")
    with s.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM episodes_task", (), limit=1) == ((0,),)


@pytest.mark.trace("T-E15-032", "V40", "T-E15-022")
def test_origin_mismatch_keeps_producer_and_gap_never_resolves(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.db.migrate("CREATE TABLE producer_survives (id TEXT) STRICT;")
    with s.db.begin(actor=Actor("engine", "engine_one"), correlation_id="corr_link") as uow:
        s.handlers.linked(
            uow, LinkedFact("event_one", ep, "measurement_request", "mrq_one", "completed", "LIVE", NOW)
        )
        uow.connection.execute("INSERT INTO producer_survives VALUES (?)", ("committed",))
    with s.db.snapshot() as view:
        assert s.service.repo.links(view, ep) == ()
        assert view.connection.rows("SELECT id FROM producer_survives", (), limit=1) == (("committed",),)
    with s.db.begin(actor=Actor("engine", "engine_one"), correlation_id="corr_link") as uow:
        s.handlers.linked(uow, LinkedFact("event_two", ep, "capability_gap", "gap_one", "open", "SIM", NOW))
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).state.value == "open"


@pytest.mark.trace("T-E15-044", "T-E15-018")
def test_onset_history_and_overdue_once(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "correct_onset", "onset": {"at": NOW.isoformat(), "precision": "exact"}})
    s.act(
        ep,
        {
            "action": "add_followup",
            "action_text": "token",
            "due_at": (NOW + timedelta(minutes=5)).isoformat(),
        },
    )
    s.clock.at = NOW + timedelta(minutes=6)
    with s.db.begin(actor=Actor("engine", "engine_one"), correlation_id="corr_due") as uow:
        assert NoticeOverdue(s.service).run(uow) == 1
    with s.db.begin(actor=Actor("engine", "engine_one"), correlation_id="corr_due") as uow:
        assert NoticeOverdue(s.service).run(uow) == 0
    r = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"})
    assert r.status_code == 200, r.text
    assert r.json()["followup_tasks"][0]["overdue"] is True
    assert r.json()["state"] == "followup"
    assert len(r.json()["onset_history"]) == 2


@pytest.mark.trace("T-E15-048", "T-E15-013", "T-E15-012")
def test_review_task_cannot_be_completed_and_patient_cannot_acknowledge(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "request_review"})
    with s.db.snapshot() as view:
        task = s.queries.tasks(view, ep)[0]
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "complete_followup", "task_id": task["task_id"], "task_revision": 1})
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "acknowledge"})
    with pytest.raises(ProblemError):
        s.act(
            ep,
            {
                "action": "cancel_followup",
                "task_id": task["task_id"],
                "task_revision": 1,
                "cancellation_reason": "token",
            },
        )


@pytest.mark.trace("T-E15-041", "T-E15-042")
def test_config_is_real_and_invalid_activation_keeps_previous(s: Station) -> None:
    raw = json.loads((ROOT / "config/episode-rules.json").read_bytes())
    raw["due_policies"][0]["duration"]["value"] = 12
    changed = json.dumps(raw).encode()
    s.service.rules.activate("release_two", changed)
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "request_review"})
    r = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"})
    assert r.status_code == 200, r.text
    assert r.json()["followup_tasks"][0]["due_at"] == "2026-11-14T00:00:00Z"
    raw["due_policies"][0]["duration"]["value"] = -1
    with pytest.raises(ProblemError):
        s.service.rules.activate("release_bad", json.dumps(raw).encode())
    assert s.service.rules.active_release_id == "release_two"


@pytest.mark.trace("T-E15-033", "T-E15-025")
def test_http_revision_retry_and_malformed_boundary(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    headers = {"Authorization": "patient", "If-Match": '"2"', "Idempotency-Key": "cmd_http"}
    body = {"action": "add_followup", "action_text": "token"}
    first = s.client.post("/v1/episodes/" + ep + "/actions", json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert s.client.post("/v1/episodes/" + ep + "/actions", json=body, headers=headers).status_code == 200
    headers["Idempotency-Key"] = "cmd_other"
    assert s.client.post("/v1/episodes/" + ep + "/actions", json=body, headers=headers).status_code == 409
    assert (
        s.client.post(
            "/v1/episodes/" + ep + "/actions",
            content='{"action":"acknowledge","action":"resolve"}',
            headers={**headers, "Content-Type": "application/json"},
        ).status_code
        == 400
    )
    assert s.client.get("/v1/episodes/" + ep).status_code == 401


@pytest.mark.trace("T-E15-033", "T-E15-034")
def test_legacy_episode_wire_remains_exact(s: Station) -> None:
    from crew_health.contracts_gen.schemas.episode_schema import Episode

    ep = s.create()
    s.answer(ep)
    r = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient", "Accept": "application/json"})
    assert r.status_code == 200, r.text
    Episode.model_validate(r.json())
    assert set(r.json()) == set(Episode.model_fields)


@pytest.mark.trace("T-E15-030", "V26")
def test_private_text_not_in_audit_replication_or_timeline(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "add_followup", "action_text": "private-task-canary"})
    with closing(sqlite3.connect(s.db.path)) as connection:
        records = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%audit%' OR name='repl_log')"
        ).fetchall()
        for (table,) in records:
            data = repr(connection.execute("SELECT * FROM " + table).fetchall())
            assert "private-concern-canary" not in data
            assert "private-task-canary" not in data
    page = s.client.get("/v1/episodes/" + ep + "/timeline", headers={"Authorization": "patient"}).json()
    assert "private-concern-canary" not in json.dumps(page)
    assert "private-task-canary" not in json.dumps(page)


@pytest.mark.trace("T-E15-025", "T-E15-026")
def test_two_clients_concurrent_resolution_have_one_winner(s: Station) -> None:
    from concurrent.futures import ThreadPoolExecutor

    ep = s.create()
    s.answer(ep)
    body = {"action": "resolve", "reason": "symptoms_reported_resolved"}

    def run(key: str) -> int:
        response = s.client.post(
            "/v1/episodes/" + ep + "/actions",
            json=body,
            headers={"Authorization": "patient", "If-Match": '"2"', "Idempotency-Key": key},
        )
        return int(response.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("cmd_first", "cmd_second")))
    assert sorted(results) == [200, 409]
    with s.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM episodes_resolution", (), limit=1) == ((1,),)


@pytest.mark.trace("T-E15-013", "T-E15-012")
def test_authenticated_assistant_actor_cannot_use_human_session(s: Station) -> None:
    from dataclasses import replace

    ep = s.create()
    s.answer(ep)
    assistant = replace(PATIENT, actor=Actor("assistant", "assistant_one"))
    with s.db.begin(actor=assistant.actor, correlation_id="corr_reject") as uow:
        with pytest.raises(ProblemError):
            s.service.act(
                uow,
                assistant,
                ep,
                {"action": "resolve", "reason": "symptoms_reported_resolved"},
                2,
                s.identity("cmd_assistant", {}, ep, session=assistant),
            )
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).state.value == "open"


@pytest.mark.trace("T-E15-033", "T-E15-034")
def test_opt_in_schema_validates_attributed_task_and_resolution_history(s: Station) -> None:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    paths = list((ROOT / "contracts/api/schemas").glob("*.json"))
    paths += list((ROOT / "hub/src/crew_health/episodes/api/schemas").glob("*.json"))
    registry = Registry().with_resources(
        [
            (
                path.name,
                Resource.from_contents(json.loads(path.read_bytes()), default_specification=DRAFT202012),
            )
            for path in paths
        ]
    )
    schema = json.loads(
        (ROOT / "hub/src/crew_health/episodes/api/schemas/episode-details.schema.json").read_bytes()
    )
    validator = Draft202012Validator(schema, registry=registry)
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "acknowledge"}, session=OFFICER)
    s.act(ep, {"action": "add_followup", "action_text": "token"})
    with s.db.snapshot() as view:
        task = s.queries.tasks(view, ep)[0]
    s.act(ep, {"action": "complete_followup", "task_id": task["task_id"], "task_revision": 1})
    s.act(ep, {"action": "resolve", "reason": "symptoms_reported_resolved"})
    s.act(ep, {"action": "reopen", "reason": "symptoms_recurred"})
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"})
    assert response.status_code == 200, response.text
    validator.validate(response.json())
    assert response.json()["reopenings"][0]["reason"] == "symptoms_recurred"
