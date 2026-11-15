"""HTTP acceptance over real SQLite, durable ledger and intents, with other epic ports faked."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from httpx import Response

from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.plan_run_schema import PlanRun
from crew_health.contracts_gen.schemas.plan_view_schema import PlanView
from crew_health.kernel.errors import StorageUnavailable

from .http_harness import HttpStack
from .plans_stack import NOW, obj


def checked(response: Response, status: int = 200) -> dict[str, object]:
    assert response.status_code == status, response.text
    value = cast(dict[str, object], response.json())
    PlanRun.model_validate(value)
    assert response.headers["ETag"] == '"' + str(value["revision"]) + '"'
    assert response.headers["Config-Release"]
    assert response.headers["X-Correlation-Id"]
    return value


def action(
    h: HttpStack,
    run: dict[str, object],
    name: str,
    *,
    step: str | None = None,
    key: str | None = None,
    revision: int | None = None,
    **extra: object,
) -> Response:
    body = {"action": name, "client_time": NOW.isoformat().replace("+00:00", "Z"), **extra}
    if step is not None:
        body["step_id"] = step
    headers = {
        "Idempotency-Key": key or "action_" + name,
        "If-Match": '"' + str(run["revision"] if revision is None else revision) + '"',
    }
    return cast(
        Response,
        h.client.post("/v1/plan-runs/" + str(run["plan_run_id"]) + "/actions", json=body, headers=headers),
    )


def refusal(response: Response, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status, response.text
    ErrorEnvelope.model_validate(response.json())
    value = cast(dict[str, object], response.json())
    assert value["code"] == code
    assert response.headers["Config-Release"] and response.headers["X-Correlation-Id"]
    return value


@pytest.mark.trace("T28-29", "T28-11", "EI5", "AGENTS-3")
def test_http_condition_start_actions_help_and_close_with_model_and_link_absent(tmp_path: Path) -> None:
    h = HttpStack(tmp_path)
    view = h.client.get("/v1/conditions/ICL10/plan")
    assert view.status_code == 200
    PlanView.model_validate(view.json())
    response = h.client.post(
        "/v1/plan-runs",
        json={"plan_id": "plan_icl10", "subject_person_id": "mara", "episode_id": "ep_test"},
        headers={"Idempotency-Key": "open_path", "X-Correlation-Id": "cor_http"},
    )
    run = checked(response, 201)
    assert response.headers["Location"].endswith(str(run["plan_run_id"]))
    assert response.headers["X-Correlation-Id"] == "cor_http"
    run = checked(action(h, run, "step_done", step="icl10_note_episodes"))
    run = checked(action(h, run, "use_substitute", step="icl10_pulse_at_cameras"))
    run = checked(action(h, run, "escalate", step="icl10_worse_chest"))
    assert h.stack.help_entry.calls
    assert run["state"] == "escalated"
    run = checked(action(h, run, "close", disposition="other"))
    assert run["state"] == "closed" and run["allowed_actions"] == []
    assert {e["type"] for e in h.stack.events} == {
        "plan_run.changed",
        "plan_run.step_done",
        "plan_run.escalated",
    }


@pytest.mark.trace("T28-10", "SYSTEM-5.8", "AGENTS-6")
def test_http_idempotency_includes_body_and_target(tmp_path: Path) -> None:
    h = HttpStack(tmp_path)
    run = h.start()
    assert h.start() == run
    response = h.client.post(
        "/v1/plan-runs",
        json={"plan_id": "plan_eye_injury", "subject_person_id": "mara", "episode_id": "ep_test"},
        headers={"Idempotency-Key": "open_http"},
    )
    refusal(response, 409, "DUPLICATE_COMMAND")
    first = checked(action(h, run, "step_done", step="icl10_note_episodes", key="same_action"))
    assert checked(action(h, run, "step_done", step="icl10_note_episodes", key="same_action")) == first
    other = h.start(key="open_other", plan="plan_eye_injury")
    refusal(
        action(h, other, "step_done", step="icl10_note_episodes", key="same_action"), 409, "DUPLICATE_COMMAND"
    )


@pytest.mark.trace("D9", "T28-29", "AGENTS-7")
def test_http_per_object_privacy_and_ground_consent(tmp_path: Path) -> None:
    h = HttpStack(tmp_path)
    run = h.start()
    path = "/v1/plan-runs/" + str(run["plan_run_id"])
    for person in ("tomas", "station", "assistant"):
        headers = {"X-Test-Person": person}
        refusal(h.client.get(path, headers=headers), 403, "UNAUTHORIZED_SCOPE")
        response = h.client.post(
            path + "/actions",
            json={
                "action": "close",
                "disposition": "other",
                "client_time": NOW.isoformat().replace("+00:00", "Z"),
            },
            headers={**headers, "Idempotency-Key": "private_action", "If-Match": '"1"'},
        )
        refusal(response, 403, "UNAUTHORIZED_SCOPE")
    response = h.client.get("/v1/plan-runs", headers={"X-Test-Person": "tomas"})
    assert response.status_code == 200 and response.json()["items"] == []
    refusal(h.client.get("/v1/plan-runs", headers={"X-Test-Person": "station"}), 403, "UNAUTHORIZED_SCOPE")
    refusal(h.client.get(path, headers={"X-Test-Person": "ground"}), 403, "CONSENT_REQUIRED")
    refusal(
        h.client.get("/v1/plans/plan_icl10", headers={"X-Test-Person": "assistant"}),
        403,
        "UNAUTHORIZED_SCOPE",
    )


@pytest.mark.trace("T28-09", "T28-17", "PR-I9")
def test_http_missing_configuration_writes_nothing_and_corrupt_pins_are_restricted(tmp_path: Path) -> None:
    from crew_health.kernel.config import ConfigName
    from crew_health.kernel.testing import MemoryRelease

    h = HttpStack(tmp_path)
    saved = h.stack.release
    del h.stack.docs[ConfigName.ONBOARD_PLANS]
    h.stack.release = MemoryRelease("release_missing", h.stack.docs)
    refusal(h.client.get("/v1/conditions/ICL10/plan"), 422, "CONTEXT_INAPPLICABLE")
    refusal(h.client.get("/v1/plans/plan_icl10"), 404, "NOT_FOUND")
    refusal(
        h.client.post(
            "/v1/plan-runs",
            json={"plan_id": "plan_icl10", "subject_person_id": "mara", "episode_id": "ep_test"},
            headers={"Idempotency-Key": "missing_config"},
        ),
        422,
        "INVALID_VALUE",
    )
    assert h.stack.query("SELECT count(*) FROM plans_run")[0][0] == 0
    h.stack.release = saved
    run = h.start()
    with h.stack.db.begin(actor=h.stack.session.actor, correlation_id="cor_corrupt") as uow:
        uow.connection.execute("UPDATE plans_parsed_plan SET plan_json=?", ("{",))
    result = checked(h.client.get("/v1/plan-runs/" + str(run["plan_run_id"])))
    assert result["allowed_actions"] == ["escalate", "close"]


@pytest.mark.trace("T28-10", "T28-29")
def test_http_stale_and_missing_if_match_and_header_body_bounds(tmp_path: Path) -> None:
    h = HttpStack(tmp_path)
    run = h.start()
    newer = checked(action(h, run, "step_done", step="icl10_note_episodes"))
    result = refusal(action(h, run, "escalate", step="icl10_worse_chest"), 409, "STALE_VERSION")
    assert obj(result["current_state"])["revision"] == newer["revision"]
    response = h.client.post(
        "/v1/plan-runs/" + str(run["plan_run_id"]) + "/actions",
        json={
            "action": "escalate",
            "step_id": "icl10_worse_chest",
            "client_time": NOW.isoformat().replace("+00:00", "Z"),
        },
        headers={"Idempotency-Key": "missing_match"},
    )
    refusal(response, 428, "VERSION_REQUIRED")
    refusal(
        h.client.post("/v1/plan-runs", json={}, headers={"Idempotency-Key": "short"}),
        400,
        "MALFORMED_REQUEST",
    )
    refusal(
        h.client.post("/v1/plan-runs", content="{}", headers={"Idempotency-Key": "wrong_media"}),
        415,
        "UNSUPPORTED_MEDIA_TYPE",
    )
    refusal(
        h.client.post(
            "/v1/plan-runs",
            content=b" " * (h.runtime.need().settings.max_document_bytes + 1),
            headers={"Idempotency-Key": "huge_body", "Content-Type": "application/json"},
        ),
        413,
        "PAYLOAD_TOO_LARGE",
    )
    refusal(h.client.get("/v1/plan-runs?limit=201"), 422, "INVALID_VALUE")


@pytest.mark.trace("T28-10", "AGENTS-6", "SYSTEM-5.7")
def test_http_crash_in_ledger_rolls_back_action_event_and_audit_then_retry_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crew_health.platform.adapters.ledger import CommandLedger

    h = HttpStack(tmp_path)
    run = h.start()
    before = h.stack.events
    audit_before = h.stack.query("SELECT count(*) FROM platform_audit")
    ledger_before = h.stack.query("SELECT count(*) FROM platform_command_ledger")
    original = CommandLedger.record

    def crash(*args: object, **kwargs: object) -> None:
        raise StorageUnavailable("io_error")

    monkeypatch.setattr(CommandLedger, "record", crash)
    refusal(action(h, run, "step_done", step="icl10_note_episodes", key="retry_crash"), 500, "INTERNAL_ERROR")
    assert h.stack.events == before
    assert h.stack.query("SELECT count(*) FROM platform_audit") == audit_before
    assert h.stack.query("SELECT count(*) FROM platform_command_ledger") == ledger_before
    assert checked(h.client.get("/v1/plan-runs/" + str(run["plan_run_id"]))) == run
    monkeypatch.setattr(CommandLedger, "record", original)
    updated = checked(action(h, run, "step_done", step="icl10_note_episodes", key="retry_crash"))
    assert updated["revision"] == 2
    assert checked(action(h, run, "step_done", step="icl10_note_episodes", key="retry_crash")) == updated
    assert len(h.stack.events) == len(before) + 1


@pytest.mark.trace("T28-29", "SYSTEM-5.5")
def test_every_domain_error_has_its_contract_code_pointer_and_current_state() -> None:
    from crew_health.plans.api.errors import problem
    from crew_health.plans.domain.errors import (
        InvalidField,
        InvalidTransition,
        PlansDocumentInvalid,
        StaleVersion,
        StepNotAvailable,
        UnitMismatch,
    )

    current = {"revision": 7}
    cases = (
        (InvalidField("/step_id", "bad"), "INVALID_VALUE", 422),
        (StepNotAvailable("step_unknown"), "STEP_NOT_AVAILABLE", 422),
        (InvalidTransition("closed"), "INVALID_TRANSITION", 409),
        (StaleVersion(), "STALE_VERSION", 409),
        (UnitMismatch("supply_test"), "INVALID_VALUE", 422),
        (PlansDocumentInvalid("/plans", "invalid"), "INTERNAL_ERROR", 500),
    )
    for error, code, status in cases:
        mapped = problem(error, current)
        assert mapped.code.value == code and mapped.status == status
        if isinstance(error, (InvalidField, UnitMismatch)):
            assert mapped.field_errors
        if isinstance(error, StaleVersion):
            assert mapped.current_state == current
        if isinstance(error, PlansDocumentInvalid):
            assert mapped.retryable


@pytest.mark.trace("T28-29", "SYSTEM-5.8", "D9")
def test_http_pagination_config_headers_and_rejected_requests(tmp_path: Path) -> None:
    h = HttpStack(tmp_path)
    first = h.start()
    second = h.start(plan="plan_eye_injury", key="open_second")
    listed = h.client.get("/v1/plan-runs?limit=1")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    PlanRun.model_validate(item)
    cursor = listed.json()["page"]["next_cursor"]
    assert cursor == item["plan_run_id"]
    tail = h.client.get("/v1/plan-runs", params={"cursor": cursor, "limit": "1"})
    assert tail.status_code == 200
    assert {item["plan_run_id"], tail.json()["items"][0]["plan_run_id"]} == {
        first["plan_run_id"],
        second["plan_run_id"],
    }
    refusal(h.client.get("/v1/plan-runs?subject=tomas"), 403, "UNAUTHORIZED_SCOPE")
    refusal(h.client.get("/v1/plan-runs?limit=bad"), 400, "MALFORMED_REQUEST")
    refusal(h.client.get("/v1/plan-runs?cursor=%3Cscript%3E"), 400, "MALFORMED_REQUEST")
    refusal(
        h.client.post(
            "/v1/plan-runs",
            json={"plan_id": "plan_icl10", "subject_person_id": "mara", "episode_id": "ep_test"},
            headers={"Idempotency-Key": "config_wrong", "Config-Release": "release_wrong"},
        ),
        409,
        "CONFIG_VERSION_MISMATCH",
    )
    for raw in ("{", "[]"):
        refusal(
            h.client.post(
                "/v1/plan-runs",
                content=raw,
                headers={"Idempotency-Key": "invalid_json", "Content-Type": "application/json"},
            ),
            400,
            "MALFORMED_REQUEST",
        )
    path = "/v1/plan-runs/" + str(first["plan_run_id"]) + "/actions"
    for header in ("-1", '"1', 'W/"1"', "1,2"):
        refusal(
            h.client.post(
                path,
                json={
                    "action": "close",
                    "disposition": "other",
                    "client_time": NOW.isoformat().replace("+00:00", "Z"),
                },
                headers={"Idempotency-Key": "invalid_match", "If-Match": header},
            ),
            400,
            "MALFORMED_REQUEST",
        )


@pytest.mark.trace("T28-29", "D22")
@pytest.mark.parametrize("path", ["/v1/conditions/ICL00/plan", "/v1/plans/invalid", "/v1/plan-runs/!invalid"])
def test_http_path_parameters_obey_the_declared_patterns(tmp_path: Path, path: str) -> None:
    h = HttpStack(tmp_path)
    refusal(h.client.get(path), 400, "MALFORMED_REQUEST")


@pytest.mark.trace("D9", "AGENTS-7")
def test_station_detail_refusal_does_not_reveal_whether_a_run_exists(tmp_path: Path) -> None:
    h = HttpStack(tmp_path)
    refusal(
        h.client.get("/v1/plan-runs/prun_absent", headers={"X-Test-Person": "station"}),
        403,
        "UNAUTHORIZED_SCOPE",
    )
