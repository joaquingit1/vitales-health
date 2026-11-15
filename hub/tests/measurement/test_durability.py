"""Idempotency, concurrency and crash behavior of E05 commands (SYSTEM 5.7, 5.8; D6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from measurement_harness import (
    Hub,
    attempt,
    build_hub,
    demo_crew,
    ecg_body,
    failed_attempt,
    headers,
    pulse_body,
    started_pulse,
)

pytestmark = pytest.mark.trace("T-E05-08", "R25", "V16", "V35", "I4")


def _rows(hub: Hub) -> tuple[int, int, int, int]:
    return (
        hub.uows.count("measurement_request"),
        hub.uows.count("measurement_attempt"),
        hub.uows.count("measurement_gap"),
        len(hub.uows.state.events),
    )


def test_retry_with_same_key_replays_without_executing_again(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    first = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-dup-1")
    )
    before = _rows(hub)
    again = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-dup-1")
    )
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert _rows(hub) == before


def test_refusal_replays_as_the_same_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    first = hub.client.post(
        "/v1/measurement-requests", json=ecg_body(), headers=headers("mara", "idem-key-dup-ecg")
    )
    before = _rows(hub)
    again = hub.client.post(
        "/v1/measurement-requests", json=ecg_body(), headers=headers("mara", "idem-key-dup-ecg")
    )
    assert (first.status_code, again.status_code) == (422, 422)
    assert again.json()["current_state"] == first.json()["current_state"]
    assert _rows(hub) == before


def test_same_key_with_other_body_or_target_is_a_conflict(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    assert (
        hub.client.post(
            "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-dup-2")
        ).status_code
        == 201
    )
    changed = dict(pulse_body(), reason="another reason")
    conflict = hub.client.post(
        "/v1/measurement-requests", json=changed, headers=headers("mara", "idem-key-dup-2")
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "DUPLICATE_COMMAND"
    assert conflict.json()["current_state"] is None
    started = started_pulse(hub)
    request_id = str(started["request_id"])
    other_target = hub.client.post(
        f"/v1/measurement-requests/{request_id}/actions",
        json={"action": "cancel"},
        headers=headers("mara", "idem-key-dup-2", If_Match='"4"'),
    )
    assert other_target.status_code == 409 and other_target.json()["code"] == "DUPLICATE_COMMAND"


def test_replay_after_the_window_returns_only_the_receipt(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, overrides={"measurement.replay_window_ms": 1000})
    demo_crew(hub)
    first = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-old-1")
    )
    hub.advance(5)
    late = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-old-1")
    )
    assert late.status_code == 409
    state = late.json()["current_state"]
    assert state["kind"] == "accepted_command" and state["operation_id"] == "createMeasurementRequest"
    assert state["target"] == "/v1/measurement-requests" and state["command_key"] == "idem-key-old-1"
    assert first.json()["reason"] not in str(late.json())


def test_stale_and_missing_revision(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    created = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-rev-1")
    ).json()
    url = f"/v1/measurement-requests/{created['request_id']}/actions"
    missing = hub.client.post(url, json={"action": "submit"}, headers=headers("mara", "idem-key-rev-2"))
    assert missing.status_code == 428 and missing.json()["code"] == "VERSION_REQUIRED"
    ok = hub.client.post(
        url, json={"action": "submit"}, headers=headers("mara", "idem-key-rev-3", If_Match='"1"')
    )
    assert ok.status_code == 200
    stale = hub.client.post(
        url, json={"action": "cancel"}, headers=headers("mara", "idem-key-rev-4", If_Match='"1"')
    )
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_VERSION"
    assert stale.json()["current_state"]["revision"] == 2


@pytest.mark.parametrize("table", ["measurement_gap", "measurement_transition"])
def test_crash_mid_command_leaves_no_partial_record(tmp_path: Path, table: str) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    hub.uows.writer.fail_on = "INSERT INTO " + table
    crashed = hub.client.post(
        "/v1/measurement-requests", json=ecg_body(), headers=headers("mara", "idem-key-crash-1")
    )
    assert crashed.status_code == 500 and crashed.json()["retryable"] is True
    assert _rows(hub) == (0, 0, 0, 0)
    assert hub.uows.state.ledger == {} and hub.uows.state.audit == []
    hub.uows.writer.fail_on = None
    retried = hub.client.post(
        "/v1/measurement-requests", json=ecg_body(), headers=headers("mara", "idem-key-crash-1")
    )
    assert retried.status_code == 422
    assert _rows(hub)[:3] == (1, 0, 1)


def test_attempt_crash_keeps_request_running_and_retry_appends_once(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    hub.uows.writer.fail_on = "INSERT INTO measurement_attempt"
    assert attempt(hub, "camera", request_id, failed_attempt(), "idem-key-ac-1").status_code == 500
    hub.uows.writer.fail_on = None
    assert hub.uows.count("measurement_attempt") == 0
    assert attempt(hub, "camera", request_id, failed_attempt(), "idem-key-ac-1").status_code == 200
    duplicate = attempt(hub, "camera", request_id, failed_attempt(), "idem-key-ac-2")
    assert duplicate.status_code == 200 and len(duplicate.json()["attempts"]) == 1
    reused = attempt(
        hub, "camera", request_id, dict(failed_attempt(), failure_reason="other"), "idem-key-ac-3"
    )
    assert reused.status_code == 409 and reused.json()["field_errors"][0]["field"] == "/sequence"
    assert hub.uows.count("measurement_attempt") == 1


def test_config_release_mismatch_refuses_the_command(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    refused = hub.client.post(
        "/v1/measurement-requests",
        json=pulse_body(),
        headers=headers("mara", "idem-key-rel-1", Config_Release="rel_0"),
    )
    assert refused.status_code == 409 and refused.json()["code"] == "CONFIG_VERSION_MISMATCH"
    assert _rows(hub) == (0, 0, 0, 0)
    ok = hub.client.post(
        "/v1/measurement-requests",
        json=pulse_body(),
        headers=headers("mara", "idem-key-rel-2", Config_Release="rel_1"),
    )
    assert ok.status_code == 201 and ok.headers["Config-Release"] == "rel_1"
    assert ok.headers["Location"] == "/v1/measurement-requests/" + ok.json()["request_id"]


def test_replay_after_an_intervening_change_returns_the_original_response(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    demo_crew(hub)
    created = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-i-1")
    )
    request_id = created.json()["request_id"]
    url = f"/v1/measurement-requests/{request_id}/actions"
    submitted = hub.client.post(
        url, json={"action": "submit"}, headers=headers("mara", "idem-key-i-2", If_Match='"1"')
    )
    cancelled = hub.client.post(
        url, json={"action": "cancel"}, headers=headers("mara", "idem-key-i-3", If_Match='"2"')
    )
    assert cancelled.json()["state"] == "cancelled"
    before = _rows(hub)
    again = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-i-1")
    )
    assert again.status_code == 201
    assert again.json() == created.json()
    resubmit = hub.client.post(
        url, json={"action": "submit"}, headers=headers("mara", "idem-key-i-2", If_Match='"1"')
    )
    assert resubmit.status_code == 200
    assert resubmit.json() == submitted.json()
    assert _rows(hub) == before
