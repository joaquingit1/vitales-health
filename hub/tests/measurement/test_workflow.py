"""HTTP end to end through FastAPI TestClient and real SQLite: a camera pulse request with a failed then a live attempt,
and an ECG request on a kit without an ECG."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from measurement_harness import (
    attempt,
    build_hub,
    ecg_body,
    example,
    failed_attempt,
    headers,
    schema_validator,
    seed_crew,
    started_pulse,
    success_attempt,
)

from crew_health.contracts_gen.enums import EventType


def _valid(schema: str, body: object) -> None:
    validator: Any = schema_validator(schema)
    validator.validate(body)


@pytest.mark.trace("T-E05-04", "T-E05-14", "R08", "E05-02", "V05", "V11", "D08", "D09", "D11")
def test_pulse_failed_attempt_kept_then_live_camera_value(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    started = started_pulse(hub)
    assert started["state"] == "in_progress"
    request_id = str(started["request_id"])

    refused = attempt(hub, "camera", request_id, failed_attempt(), "idem-key-att-1")
    assert refused.status_code == 200
    body = refused.json()
    _valid("measurement-request.schema.json", body)
    kept = body["attempts"][0]
    reason = failed_attempt()["failure_reason"]
    assert (kept["outcome"], kept["failure_reason"], kept["observation_ids"]) == ("failed", reason, [])
    assert body["state"] == "in_progress" and body["result_observation_ids"] == []
    assert hub.observations.drafts == []

    accepted = attempt(hub, "camera", request_id, success_attempt(value=96), "idem-key-att-2")
    assert accepted.status_code == 200
    body = accepted.json()
    _valid("measurement-request.schema.json", body)
    assert body["state"] == "completed"
    assert [a["outcome"] for a in body["attempts"]] == ["failed", "success"]
    assert body["attempts"][0]["failure_reason"] == reason
    assert body["result_observation_ids"] == body["attempts"][1]["observation_ids"] != []
    assert body["allowed_actions"] == []

    (draft,) = hub.observations.drafts
    assert draft.origin_mode == "LIVE"
    assert draft.device_id == "camera_2"
    assert draft.acquisition_method == "camera_estimate"
    assert draft.quantity.value == Decimal(96) and draft.quantity.unit.code == "/min"
    assert draft.measurement_request_id == request_id and draft.operator_id == "mara"

    read = hub.client.get(f"/v1/measurement-requests/{request_id}", headers=headers("mara"))
    assert read.status_code == 200 and read.headers["ETag"] == f'"{body["revision"]}"'
    assert read.json()["attempts"][0]["failure_reason"] == reason


@pytest.mark.trace("T-E05-02", "R07", "R08", "V18", "V19", "D06", "D12", "D30")
def test_ecg_without_kit_is_durable_unavailable_with_open_gap(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    response = hub.client.post(
        "/v1/measurement-requests", json=ecg_body(), headers=headers("mara", "idem-key-ecg-1")
    )
    assert response.status_code == 422
    envelope = response.json()
    _valid("error.schema.json", envelope)
    assert envelope["code"] == "KIT_CANNOT_PERFORM"
    state = envelope["current_state"]
    _valid("measurement-request.schema.json", state)
    expected = example("errors/kit-cannot-perform.json")["current_state"]
    assert isinstance(expected, dict)
    assert state["state"] == "unavailable"
    assert state["kit_check"]["can"] is False
    for name in ("missing_instrument_ids", "condition_ids", "cliff_urls", "text", "environment_id"):
        assert state["kit_check"][name] == expected["kit_check"][name]

    stored = hub.client.get(f"/v1/measurement-requests/{state['request_id']}", headers=headers("mara"))
    assert stored.status_code == 200 and stored.json()["state"] == "unavailable"
    gap = hub.uows.reader.db.execute(
        "SELECT episode_id, capability_code, reason, state, plan_id, consequence FROM measurement_gap"
    ).fetchall()
    assert gap == [
        (
            ecg_body()["episode_id"],
            "ecg_rhythm",
            "no_capability",
            "open",
            "plan_icl10",
            ecg_body()["decision_affected"],
        )
    ]
    assert "measurement.gap_opened" in hub.audit_types()
    assert hub.uows.count("measurement_attempt") == 0


@pytest.mark.trace("T-E05-02", "R07", "I7")
def test_request_without_episode_invents_no_episode_and_no_gap(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    body = ecg_body()
    del body["episode_id"]
    response = hub.client.post(
        "/v1/measurement-requests", json=body, headers=headers("mara", "idem-key-ecg-2")
    )
    assert response.status_code == 422
    assert response.json()["current_state"]["episode_id"] is None
    assert hub.uows.count("measurement_gap") == 0
    assert hub.uows.count("measurement_request") == 1
    assert "measurement.gap_not_opened" in hub.audit_types()


@pytest.mark.trace("T-E05-04", "R08", "D08")
def test_exhausted_attempts_end_failed_and_nothing_is_guessed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, overrides={"measurement.max_attempts": 2})
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    assert attempt(hub, "camera", request_id, failed_attempt(1), "idem-key-f-1").status_code == 200
    last = attempt(hub, "camera", request_id, failed_attempt(2), "idem-key-f-2")
    assert last.json()["state"] == "failed"
    assert last.json()["result_observation_ids"] == []
    late = attempt(hub, "camera", request_id, success_attempt(3), "idem-key-f-3")
    assert late.status_code == 200
    assert late.json()["state"] == "failed"
    assert late.json()["result_observation_ids"] == []
    assert hub.observations.drafts == []
    held = hub.uows.reader.db.execute(
        "SELECT disposition FROM measurement_attempt WHERE sequence = 3"
    ).fetchone()
    assert held == ("quarantined",)


@pytest.mark.trace("T-E05-05", "V13", "I3")
def test_late_result_after_cancel_is_held_not_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    started = started_pulse(hub)
    request_id = str(started["request_id"])
    assert attempt(hub, "camera", request_id, failed_attempt(1), "idem-key-l-1").status_code == 200
    cancelled = hub.client.post(
        f"/v1/measurement-requests/{request_id}/actions",
        json={"action": "cancel"},
        headers=headers("mara", "idem-key-cancel", If_Match='"5"'),
    )
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    late = attempt(hub, "camera", request_id, success_attempt(2), "idem-key-l-2")
    assert late.status_code == 200
    assert late.json()["state"] == "cancelled" and late.json()["result_observation_ids"] == []
    assert hub.observations.drafts == []
    held = hub.uows.reader.db.execute(
        "SELECT disposition, held_values_json FROM measurement_attempt WHERE sequence = 2"
    ).fetchone()
    assert held[0] == "quarantined" and '"96"' in held[1] and "/min" in held[1]


@pytest.mark.trace("T-E05-04", "R08", "SYSTEM-8.4")
def test_every_change_publishes_measurement_request_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    events = hub.uows.state.events
    assert [e.type for e in events] == [EventType.MEASUREMENT_REQUEST_CHANGED] * 4
    assert all(e.scope.kind == "person" and e.scope.person_id == "mara" for e in events)
    assert all(e.audience is not None and e.audience.object_ref == request_id for e in events)
    payloads = hub.events()
    assert [p["state"] for p in payloads] == ["proposed", "awaiting_acceptance", "accepted", "in_progress"]
    assert all(p["allowed_actions"] == [] for p in payloads)
    for payload in payloads:
        _valid("measurement-request.schema.json", payload)


@pytest.mark.trace("T-E05-11", "V36")
def test_capability_list_validates_and_pages(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    first = hub.client.get("/v1/capabilities?limit=2", headers=headers("mara"))
    assert first.status_code == 200
    page = first.json()
    for item in page["items"]:
        _valid("capability.schema.json", item)
    assert len(page["items"]) == 2 and page["page"]["next_cursor"] == page["items"][-1]["code"]
    rest = hub.client.get(
        f"/v1/capabilities?limit=200&cursor={page['page']['next_cursor']}", headers=headers("mara")
    )
    assert rest.json()["page"]["next_cursor"] is None
    assert len(page["items"]) + len(rest.json()["items"]) == page["page"]["total"]
    assert hub.client.get("/v1/capabilities?cursor=nope", headers=headers("mara")).status_code == 400
    assert hub.client.get("/v1/capabilities?limit=0", headers=headers("mara")).status_code == 400


@pytest.mark.trace("T-E05-05", "V13", "I3")
def test_first_result_after_cancel_is_retained_as_quarantined(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    cancelled = hub.client.post(
        f"/v1/measurement-requests/{request_id}/actions",
        json={"action": "cancel"},
        headers=headers("mara", "idem-key-q-1", If_Match='"4"'),
    )
    assert cancelled.status_code == 200
    rogue = attempt(hub, "rogue", request_id, success_attempt(1), "idem-key-q-2")
    assert rogue.status_code == 403
    late = attempt(hub, "camera", request_id, success_attempt(1), "idem-key-q-3")
    assert late.status_code == 200
    assert late.json()["state"] == "cancelled"
    assert late.json()["result_observation_ids"] == []
    assert hub.observations.drafts == []
    held = hub.uows.reader.db.execute("SELECT disposition, adapter_id FROM measurement_attempt").fetchall()
    assert held == [("quarantined", "adp_camera")]
