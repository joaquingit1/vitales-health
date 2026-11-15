"""R61: both real modules, platform SQLite, real command ledger and transport truth."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.catchup.application.composer import parse_since
from crew_health.contracts_gen.schemas.ground_log_view_schema import GroundLogView
from crew_health.groundlink.application.ports import OutboxItem
from crew_health.kernel.uow import Actor

from .support import AT, Harness, feed, message_event
from .test_compose import Snapshot

END = parse_since("2026-12-03T15:51:35Z")


def log(h: Harness, **params: str) -> dict[str, object]:
    response = h.client.get("/v1/ground/log", headers={"role": "ground"}, params=params)
    assert response.status_code == 200, response.text
    GroundLogView.model_validate(response.json())
    return response.json()  # type: ignore[no-any-return]


def deliver(h: Harness) -> None:
    assert h.runtime.agent.run_once().sent > 0
    h.clock.at += timedelta(seconds=10)
    assert h.runtime.agent.run_once().arrived > 0


@pytest.mark.trace("R61", "E29-04", "D42", "V95", "EI5")
def test_outage_delivers_only_at_contact_and_freezes_the_same_bytes(tmp_path: Path) -> None:
    h = Harness(tmp_path, environment="lunar_surface")
    feed(h, message_event())
    feed(h, message_event())
    for _ in range(3):
        assert h.runtime.agent.run_once().sent == 0
    assert log(h)["items"] == []
    h.clock.at = END
    deliver(h)
    response = h.client.get("/v1/ground/log", headers={"role": "ground"})
    assert response.status_code == 200, response.text
    body = response.json()
    GroundLogView.model_validate(body)
    assert body["since"] == "2026-11-20T00:00:00Z"
    assert body["gap"] == {
        "from": "2026-11-20T00:00:00Z",
        "to": "2026-12-03T15:51:35Z",
        "gap_kind": "planned_line_of_sight",
        "gap_planned": True,
    }
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["ref_id"] == "msg_1" and item["delivered_at"] == "2026-12-03T15:51:45Z"
    assert item["catch_up"]["what_was_done"] == [
        "12:02 Do not rub the eye.",
        "12:03 Call the CMO.",
        "Crew called at 12:04. Mara Okafor is helping.",
    ]
    h.snapshots.value = replace(Snapshot(), subject_name="Changed after approval")
    assert h.client.get("/v1/ground/log", headers={"role": "ground"}).content == response.content
    assert h.snapshots.calls == 1
    assert log(h, since="2026-12-03T15:51:46Z")["items"] == []
    assert log(h, since="2026-12-03T15:51:45Z")["items"] == body["items"]
    assert response.headers["Config-Release"] == "release_1"
    assert response.headers["X-Correlation-Id"].startswith("cuc")


@pytest.mark.trace("R61", "E29-04", "D42", "V95", "EI5")
@pytest.mark.parametrize(
    "since",
    [
        "",
        "2026-11-20",
        "2026-11-20T00:00:00+00:00",
        "2026-02-30T00:00:00Z",
        "2026-11-20 00:00:00Z",
        "0",
        "2026-11-20T00:00:00Zjunk",
    ],
)
def test_malformed_since_is_a_safe_400(tmp_path: Path, since: str) -> None:
    h = Harness(tmp_path)
    response = h.client.get("/v1/ground/log", params={"since": since}, headers={"role": "ground"})
    assert response.status_code == 400 and response.json()["code"] == "MALFORMED_REQUEST"
    assert since not in response.json().get("field_errors", [])


@pytest.mark.trace("R61", "E29-04", "V95", "EI5")
def test_ground_only_with_no_gate_401_and_crew_privacy_403(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    for role in ("crew", "crew_medical_officer", "operator", "station", "editor", "unknown"):
        response = h.client.get("/v1/ground/log", headers={"role": role})
        assert response.status_code == 403 and response.json()["code"] == "UNAUTHORIZED_SCOPE"
        assert "items" not in response.json()
    absent_path = tmp_path / "absent"
    absent_path.mkdir()
    absent = Harness(absent_path, gate=False)
    assert absent.client.get("/v1/ground/log").status_code == 401


@pytest.mark.trace("R61", "E29-04", "D42", "V95", "EI5")
def test_log_limit_is_retryable_error_never_a_partial_200(tmp_path: Path) -> None:
    h = Harness(tmp_path, log_max_items=1)
    for i in range(2):
        facts = replace(Snapshot(), help_session_id=f"help_{i}")
        h.snapshots.values[f"msg_{i}"] = facts
        feed(h, message_event(f"msg_{i}", snapshot=facts))
    deliver(h)
    calls = h.snapshots.calls
    response = h.client.get("/v1/ground/log", headers={"role": "ground"})
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR" and response.json()["retryable"] is True
    assert "items" not in response.json() and h.snapshots.calls == calls


@pytest.mark.trace("R61", "E29-04", "EI5")
def test_handoff_without_a_known_subject_is_shown_to_nobody(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    with h.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor_enqueue") as uow:
        h.outbox.enqueue(uow, OutboxItem("handoff", "handoff_1", "a" * 64, AT, True))
    deliver(h)
    assert log(h)["items"] == []


@pytest.mark.trace("R61", "E29-04")
def test_duplicate_since_is_malformed_and_correlation_header_is_preserved(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    response = h.client.get(
        "/v1/ground/log",
        params=[("since", "2026-11-20T00:00:00Z")] * 2,
        headers={"role": "ground", "X-Correlation-Id": "cor_client"},
    )
    assert response.status_code == 400 and response.json()["code"] == "MALFORMED_REQUEST"
    assert response.headers["X-Correlation-Id"] == "cor_client"
