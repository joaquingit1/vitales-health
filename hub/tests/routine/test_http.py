"""HTTP contract, command durability and private crew projections."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from .http_harness import HttpHarness, headers, problem, request_example, validate, validator


@pytest.mark.trace("T-E25-015", "T-E25-016", "T-E25-022")
def test_schedules_and_every_resource_match_the_json_schemas(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    for range_ in ("today", "week"):
        response = hub.client.get(
            "/v1/routine/" + range_, headers={**headers(), "X-Correlation-Id": "cor_http_test"}
        )
        body = validate(response, "routine-schedule.schema.json")
        assert response.headers["Config-Release"] == hub.release.release_id
        assert response.headers["X-Correlation-Id"] == "cor_http_test"
        assert body["range"] == range_
        if range_ == "week":
            assert body["groups"] == []
        for group in cast(list[dict[str, object]], body["groups"]):
            for occurrence in cast(list[dict[str, object]], group["occurrences"]):
                validator("routine-occurrence.schema.json").validate(occurrence)
    for row in hub.items("/v1/routine/occurrences"):
        response = hub.client.get(f"/v1/routine/occurrences/{row['occurrence_id']}", headers=headers())
        body = validate(response, "routine-occurrence.schema.json")
        assert response.headers["ETag"] == f'"{body["revision"]}"'
    for row in hub.items("/v1/routine/tasks"):
        validator("routine-task.schema.json").validate(row)
        validate(
            hub.client.get(f"/v1/routine/tasks/{row['item_id']}", headers=headers()),
            "routine-task.schema.json",
        )


@pytest.mark.trace("T-E25-018")
def test_complete_is_idempotent_and_replays_after_thirty_days(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    path = f"/v1/routine/occurrences/{row['occurrence_id']}/actions"
    auth = headers(key="complete_http", revision=int(str(row["revision"])))
    problem(
        hub.client.post(path, json={"action": "complete"}, headers=headers(key="missing_version")),
        "VERSION_REQUIRED",
        428,
    )
    problem(
        hub.client.post(path, json={"action": "complete"}, headers=headers(key="stale_version", revision=0)),
        "STALE_VERSION",
        409,
    )
    first = hub.client.post(path, json={"action": "complete"}, headers=auth)
    validate(first, "routine-occurrence.schema.json")
    assert hub.client.post(path, json={"action": "complete"}, headers=auth).json() == first.json()
    problem(
        hub.client.post(path, json={"action": "skip", "skip_reason": "no_time"}, headers=auth),
        "DUPLICATE_COMMAND",
        409,
    )
    hub.advance(hub.clock.now() + timedelta(days=30))
    assert hub.client.post(path, json={"action": "complete"}, headers=auth).json() == first.json()
    assert hub.rows(
        "SELECT count(*) FROM routine_occurrence_transition WHERE occurrence_id=? AND to_state='done'",
        (str(row["occurrence_id"]),),
    ) == ((1,),)
    assert hub.rows(
        "SELECT count(*) FROM platform_command_ledger WHERE command_key=?", ("complete_http",)
    ) == ((1,),)


@pytest.mark.trace("T-E25-027", "T-E25-101")
def test_foreign_occurrences_are_hidden_and_skip_notes_stay_private(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    identifier = str(row["occurrence_id"])
    path = f"/v1/routine/occurrences/{identifier}"
    body = request_example("occurrence-skip")
    body["note"] = "private HTTP skip note"
    note = str(body["note"])
    own = hub.client.post(path + "/actions", json=body, headers=headers(key="private_http", revision=1))
    assert validate(own, "routine-occurrence.schema.json")["skip"] == {
        "reason": body["skip_reason"],
        "note": note,
        "at": hub.clock.now().isoformat().replace("+00:00", "Z"),
    }
    for response in (
        hub.client.get(path, headers=headers("person_tomas")),
        hub.client.post(
            path + "/actions", json=body, headers=headers("person_tomas", key="foreign_http", revision=1)
        ),
    ):
        problem(response, "NOT_FOUND", 404)
        for private in (identifier, note, "person_mara", "Mara"):
            assert private not in response.text
    for path_ in ("/v1/routine/occurrences", "/v1/routine/today", "/v1/routine/week"):
        response = hub.client.get(path_, headers=headers("person_tomas"))
        assert response.status_code == 200
        assert identifier not in response.text and note not in response.text
    assert note not in str(hub.rows("SELECT envelope FROM events_outbox"))
    assert note not in str(hub.rows("SELECT * FROM platform_audit"))


@pytest.mark.trace("T-E25-024", "T-E25-027", "T-E25-101")
def test_manual_override_roles_visibility_duplicate_replay_and_stop(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    path = "/v1/routine/overrides"
    body = hub.manual_body()
    problem(hub.client.post(path, json=body, headers=headers(key="crew_override")), "UNAUTHORIZED_SCOPE", 403)
    medical = headers("person_tomas", "crew_medical_officer", key="officer_override")
    first = hub.client.post(path, json=body, headers=medical)
    override = validate(first, "routine-override.schema.json", 201)
    assert first.headers["Location"] == path + "/" + str(override["override_id"])
    assert first.headers["ETag"] == '"1"'
    assert override["reason_text"] and cast(dict[str, object], override["ends"])["text"]
    assert hub.items(path, "person_ilya") == []
    assert hub.items(path, "person_mara")
    assert hub.items(path, "person_tomas", "crew_medical_officer")
    assert hub.items(path, "person_ilya", "ground")
    changed = hub.client.get(f"/v1/routine/tasks/{body['item_id']}", headers=headers()).json()
    assert changed["effective_cadence"] == override["new_cadence"]
    problem(
        hub.client.post(
            path,
            json=hub.manual_body(bound=True),
            headers=headers("person_tomas", "crew_medical_officer", key="bound_override"),
        ),
        "INVALID_VALUE",
        422,
    )
    problem(
        hub.client.post(
            path, json=body, headers=headers("person_tomas", "crew_medical_officer", key="duplicate_override")
        ),
        "INVALID_TRANSITION",
        409,
    )
    hub.advance(hub.clock.now() + timedelta(days=30))
    replay = hub.client.post(path, json=body, headers=medical)
    assert replay.json() == first.json()
    stop = path + f"/{override['override_id']}/actions"
    problem(
        hub.client.post(
            stop,
            json={"action": "end"},
            headers=headers("person_ilya", "ground", key="wrong_stop_role", revision=1),
        ),
        "ACTION_NOT_ALLOWED",
        403,
    )
    problem(
        hub.client.post(
            stop,
            json={"action": "end"},
            headers=headers("person_tomas", "crew_medical_officer", key="missing_stop_revision"),
        ),
        "VERSION_REQUIRED",
        428,
    )
    ended = hub.client.post(
        stop,
        json={"action": "end"},
        headers=headers("person_tomas", "crew_medical_officer", key="stop_override", revision=1),
    )
    assert validate(ended, "routine-override.schema.json")["state"] == "ended"
    assert (
        hub.client.post(
            stop,
            json={"action": "end"},
            headers=headers("person_tomas", "crew_medical_officer", key="stop_override", revision=1),
        ).json()
        == ended.json()
    )

    assert hub.client.post(path, json=body, headers=medical).json() == first.json()


@pytest.mark.trace("T-E25-015", "T-E25-018")
def test_headers_queries_and_hostile_bodies_use_only_contract_envelopes(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    path = f"/v1/routine/occurrences/{hub.occurrence()['occurrence_id']}/actions"
    for request_headers in ({}, headers(role="station"), headers(role="ground")):
        code, status = ("UNAUTHENTICATED", 401) if not request_headers else ("UNAUTHORIZED_SCOPE", 403)
        problem(hub.client.get("/v1/routine/today", headers=request_headers), code, status)
    for query in ("limit=0", "limit=201", "limit=no", "limit=1.5", "range=no", "state=no", "cursor=invalid"):
        problem(
            hub.client.get("/v1/routine/occurrences?" + query, headers=headers()), "MALFORMED_REQUEST", 400
        )
    for endpoint in ("tasks", "overrides"):
        for query in ("limit=201", "cursor=invalid"):
            problem(
                hub.client.get(f"/v1/routine/{endpoint}?{query}", headers=headers()), "MALFORMED_REQUEST", 400
            )
    for value in ("", "short", "x" * 129):
        problem(
            hub.client.post(path, json={"action": "complete"}, headers=headers(key=value, revision=1)),
            "MALFORMED_REQUEST",
            400,
        )
    for value in ('W/"1"', '"1', '1"', "-1", "1,2", "a"):
        problem(
            hub.client.post(
                path, json={"action": "complete"}, headers={**headers(key="invalid_match"), "If-Match": value}
            ),
            "MALFORMED_REQUEST",
            400,
        )
    auth = headers(key="body_http", revision=1)
    problem(hub.client.post(path, content="{}", headers=auth), "UNSUPPORTED_MEDIA_TYPE", 415)
    for body in (
        "[]",
        "{",
        '{"action":"complete","action":"skip"}',
        '{"action":"complete","note":NaN}',
        '"' + "x" * 16384 + '"',
        '{"action":"invalid","note":"private text"}',
    ):
        response = hub.client.post(path, content=body, headers={**auth, "Content-Type": "application/json"})
        problem(response, "MALFORMED_REQUEST", 400)
        assert "private text" not in response.text
    response = hub.client.post(path, json={"action": "later", "later_until": "not a date"}, headers=auth)
    error_body = problem(response, "MALFORMED_REQUEST", 400)
    assert str(cast(list[dict[str, object]], error_body["field_errors"])[0]["field"]).startswith(
        "/later_until"
    )
    problem(
        hub.client.post(path, json={"action": "complete"}, headers={**auth, "Config-Release": "rel_unknown"}),
        "CONFIG_VERSION_MISMATCH",
        409,
    )


@pytest.mark.trace("T-E25-018")
def test_later_replay_returns_the_original_result_after_completion(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    path = f"/v1/routine/occurrences/{row['occurrence_id']}/actions"
    until = hub.clock.now() + timedelta(minutes=1)
    body = {"action": "later", "later_until": until.isoformat().replace("+00:00", "Z")}
    auth = headers(key="later_then_done", revision=int(str(row["revision"])))
    first = hub.client.post(path, json=body, headers=auth)
    assert validate(first, "routine-occurrence.schema.json")["state"] == "later"
    done = hub.client.post(
        path,
        json={"action": "complete"},
        headers=headers(key="done_after_later", revision=first.json()["revision"]),
    )
    assert done.status_code == 200 and done.json()["state"] == "done"
    hub.advance(hub.clock.now() + timedelta(days=30))
    assert hub.client.post(path, json=body, headers=auth).json() == first.json()


@pytest.mark.trace("T-E25-015")
def test_every_collection_accepts_full_http_limit_and_pages_without_loss(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    for endpoint in ("occurrences", "tasks", "overrides"):
        path = "/v1/routine/" + endpoint
        for limit in (1, 50, 200):
            response = hub.client.get(path, params={"limit": limit}, headers=headers())
            assert response.status_code == 200, response.text
        all_items = hub.items(path)
        found: list[dict[str, object]] = []
        cursor: str | None = None
        for _ in range(len(all_items) + 1):
            params: dict[str, str | int] = {"limit": 1}
            if cursor is not None:
                params["cursor"] = cursor
            response = hub.client.get(path, params=params, headers=headers())
            assert response.status_code == 200
            found.extend(response.json()["items"])
            cursor = response.json()["page"]["next_cursor"]
            if cursor is None:
                break
        assert found == all_items


@pytest.mark.trace("T-E25-018", "INV-E25-16")
def test_replay_versions_survive_restart_and_are_append_only(tmp_path: Path) -> None:
    from crew_health.kernel.errors import StorageUnavailable
    from crew_health.kernel.uow import Actor

    hub = HttpHarness(tmp_path)
    row = hub.occurrence()
    path = f"/v1/routine/occurrences/{row['occurrence_id']}/actions"
    body = {"action": "complete", "note": "private durable note"}
    auth = headers(key="durable_http_result", revision=int(str(row["revision"])))
    first = hub.client.post(path, json=body, headers=auth)
    assert validate(first, "routine-occurrence.schema.json")["state"] == "done"
    for sql in (
        "UPDATE routine_record_version SET revision=revision+1",
        "DELETE FROM routine_record_version",
        "UPDATE routine_command_version SET revision=revision+1",
        "DELETE FROM routine_command_version",
    ):
        with (
            pytest.raises(StorageUnavailable),
            hub.db.begin(actor=Actor("engine", "routine"), correlation_id=hub.ids.new("cor")) as uow,
        ):
            uow.connection.execute(sql)
    restarted = HttpHarness(tmp_path, initialize=False)
    restarted.advance(restarted.clock.now() + timedelta(days=30))
    replay = restarted.client.post(path, json=body, headers=auth)
    assert replay.status_code == first.status_code
    assert replay.json() == first.json()
    with restarted.db.snapshot() as view:
        assert restarted.engine.deps.store.remembered(view, "person_missing", "missing_key") is None


@pytest.mark.trace("T-E25-024", "T-E25-018")
def test_override_commands_honor_release_headers_and_body_identity(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    body = hub.manual_body()
    auth = headers("person_tomas", "crew_medical_officer", key="release_create_key", release="rel_foreign")
    problem(hub.client.post("/v1/routine/overrides", json=body, headers=auth), "CONFIG_VERSION_MISMATCH", 409)
    auth["Config-Release"] = hub.release.release_id
    first = hub.client.post("/v1/routine/overrides", json=body, headers=auth)
    assert first.status_code == 201
    path = f"/v1/routine/overrides/{first.json()['override_id']}/actions"
    end_headers = headers(
        "person_tomas", "crew_medical_officer", key="release_end_key", revision=1, release="rel_foreign"
    )
    problem(
        hub.client.post(path, json={"action": "end"}, headers=end_headers), "CONFIG_VERSION_MISMATCH", 409
    )
    end_headers["Config-Release"] = hub.release.release_id
    end_headers["If-Match"] = "0"
    problem(hub.client.post(path, json={"action": "end"}, headers=end_headers), "STALE_VERSION", 409)
    end_headers["If-Match"] = "1"
    ended = hub.client.post(path, json={"action": "end"}, headers=end_headers)
    assert validate(ended, "routine-override.schema.json")["state"] == "ended"
    problem(
        hub.client.post(path, json={"action": "end", "note": "private ending note"}, headers=end_headers),
        "DUPLICATE_COMMAND",
        409,
    )
    end_headers["Idempotency-Key"] = "end_again_key"
    end_headers["If-Match"] = str(ended.json()["revision"])
    problem(hub.client.post(path, json={"action": "end"}, headers=end_headers), "INVALID_TRANSITION", 409)


@pytest.mark.trace("T-E25-018", "T-E25-027")
def test_key_reuse_on_another_target_conflicts_and_invalid_actions_leave_no_records(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    rows = [r for r in hub.items("/v1/routine/occurrences") if r["state"] == "due"]
    first, second = rows[:2]
    first_path = f"/v1/routine/occurrences/{first['occurrence_id']}/actions"
    second_path = f"/v1/routine/occurrences/{second['occurrence_id']}/actions"
    auth = headers(key="target_bound_key", revision=1)
    completed = hub.client.post(first_path, json={"action": "complete"}, headers=auth)
    assert completed.status_code == 200
    problem(hub.client.post(second_path, json={"action": "complete"}, headers=auth), "DUPLICATE_COMMAND", 409)
    problem(
        hub.client.post(
            first_path,
            json={"action": "complete"},
            headers=headers(key="already_final_key", revision=completed.json()["revision"]),
        ),
        "INVALID_TRANSITION",
        409,
    )
    now = hub.clock.now()
    for index, body in enumerate(
        (
            {"action": "skip"},
            {"action": "skip", "skip_reason": "no_time", "observation_ids": ["obs_private"]},
            {
                "action": "complete",
                "completed_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            },
            {
                "action": "complete",
                "completed_at": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            },
            {"action": "later", "later_until": now.isoformat().replace("+00:00", "Z")},
        )
    ):
        problem(
            hub.client.post(
                second_path, json=body, headers=headers(key=f"invalid_action_{index}", revision=1)
            ),
            "MALFORMED_REQUEST",
            400,
        )
    problem(
        hub.client.post(
            second_path,
            json={"action": "complete", "answer_ids": ["ans_private"]},
            headers=headers(key="unverified_record_link", revision=1),
        ),
        "UNAUTHORIZED_SCOPE",
        403,
    )
    assert hub.rows(
        "SELECT revision FROM routine_occurrence WHERE occurrence_id=?", (str(second["occurrence_id"]),)
    ) == ((1,),)
    assert hub.rows("SELECT count(*) FROM platform_command_ledger") == ((1,),)


@pytest.mark.trace("T-E25-024", "T-E25-027")
def test_manual_override_shape_errors_rule_errors_and_ground_stop_role(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    body = hub.manual_body()
    for index, invalid in enumerate(
        (
            {**body, "item_id": "unknown_item"},
            {**body, "trigger_index": 100},
            {**body, "person_id": "person_missing"},
            {**body, "until": {"kind": "days"}},
        )
    ):
        problem(
            hub.client.post(
                "/v1/routine/overrides",
                json=invalid,
                headers=headers("person_tomas", "crew_medical_officer", key=f"invalid_rule_{index}"),
            ),
            "INVALID_VALUE",
            422,
        )
    malformed = dict(body)
    malformed.pop("person_id")
    response = hub.client.post(
        "/v1/routine/overrides",
        json=malformed,
        headers=headers("person_tomas", "crew_medical_officer", key="missing_subject_key"),
    )
    assert problem(response, "MALFORMED_REQUEST", 400)["field_errors"] == [
        {"field": "/person_id", "code": "malformed", "message": "malformed"}
    ]
    ground = headers("person_ilya", "ground", key="ground_create_key")
    created = hub.client.post("/v1/routine/overrides", json=body, headers=ground)
    assert validate(created, "routine-override.schema.json", 201)["started_by"] == "person"
    path = f"/v1/routine/overrides/{created.json()['override_id']}/actions"
    problem(
        hub.client.post(
            path,
            json={"action": "end"},
            headers=headers(
                "person_tomas", "crew_medical_officer", key="officer_cannot_stop_ground", revision=1
            ),
        ),
        "ACTION_NOT_ALLOWED",
        403,
    )
    ended = hub.client.post(
        path,
        json={"action": "end"},
        headers=headers("person_ilya", "ground", key="ground_stop_key", revision=1),
    )
    assert validate(ended, "routine-override.schema.json")["state"] == "ended"
