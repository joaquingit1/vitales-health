"""Access, privacy, adapter trust and boundary validation (E05 6, 10; CONTRACTS 4, 5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from measurement_harness import (
    Hub,
    attempt,
    build_hub,
    camera_value,
    failed_attempt,
    headers,
    pulse_body,
    scratch_kit_map,
    seed_crew,
    started_pulse,
    success_attempt,
)


def _crewmate_pulse(document: dict[str, Any]) -> None:
    for capability in document["capabilities"]:
        if capability["code"] == "pulse":
            capability["operator_requirement"] = "crewmate"


def _proposed(hub: Hub, key: str = "idem-key-p-1") -> str:
    created = hub.client.post("/v1/measurement-requests", json=pulse_body(), headers=headers("mara", key))
    assert created.status_code == 201, created.json()
    return str(created.json()["request_id"])


@pytest.mark.trace("T-E05-12", "R06", "V25", "I5")
def test_privacy_between_crew_members(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = _proposed(hub)
    url = f"/v1/measurement-requests/{request_id}"
    for token in ("kai", "cmo", "station", "camera"):
        denied = hub.client.get(url, headers=headers(token))
        assert denied.status_code == 403, token
        assert pulse_body()["reason"] not in denied.text
    assert hub.client.get(url, headers=headers("nobody")).status_code == 401
    hub.consent.grants.add(("mara", "kai", "pulse"))
    assert hub.client.get(url, headers=headers("kai")).status_code == 200
    assert hub.client.get(url, headers=headers("cmo")).status_code == 403
    hub.consent.grants.clear()
    assert hub.client.get(url, headers=headers("kai")).status_code == 403


@pytest.mark.trace("T-E05-12", "T-E05-03", "R06", "V08", "V25", "I5")
def test_another_crew_member_cannot_act_or_propose_for_the_subject(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = _proposed(hub)
    act = hub.client.post(
        f"/v1/measurement-requests/{request_id}/actions",
        json={"action": "cancel"},
        headers=headers("kai", "idem-key-k-1", If_Match='"1"'),
    )
    assert act.status_code == 403 and act.json()["current_state"] is None
    foreign = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("kai", "idem-key-k-2")
    )
    assert foreign.status_code == 403
    station = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("station", "idem-key-s-1")
    )
    assert station.status_code == 403
    assert hub.uows.count("measurement_request") == 1


@pytest.mark.trace("T-E05-03", "T-E05-12", "R06", "V09", "V25", "I2", "I5")
def test_crewmate_operator_needs_consent_and_reads_only_after_accepting(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, kit_map=scratch_kit_map(tmp_path, _crewmate_pulse))
    seed_crew(hub)
    self_operator = dict(pulse_body(), operator_id="mara")
    refused = hub.client.post(
        "/v1/measurement-requests", json=self_operator, headers=headers("mara", "idem-key-c-0")
    )
    assert refused.status_code == 422 and refused.json()["code"] == "INVALID_VALUE"
    body = dict(pulse_body(), operator_id="kai")
    created = hub.client.post("/v1/measurement-requests", json=body, headers=headers("mara", "idem-key-c-1"))
    request_id = str(created.json()["request_id"])
    url = f"/v1/measurement-requests/{request_id}"
    hub.client.post(
        url + "/actions", json={"action": "submit"}, headers=headers("mara", "idem-key-c-2", If_Match='"1"')
    )
    assert hub.client.get(url, headers=headers("kai")).status_code == 403
    no_consent = hub.client.post(
        url + "/actions", json={"action": "accept"}, headers=headers("kai", "idem-key-c-3", If_Match='"2"')
    )
    assert no_consent.status_code == 403 and no_consent.json()["code"] == "CONSENT_REQUIRED"
    hub.consent.grants.add(("mara", "kai", "pulse"))
    accepted = hub.client.post(
        url + "/actions", json={"action": "accept"}, headers=headers("kai", "idem-key-c-4", If_Match='"2"')
    )
    assert accepted.status_code == 200 and accepted.json()["operator_id"] == "kai"
    assert "start" in accepted.json()["allowed_actions"]
    assert hub.client.get(url, headers=headers("kai")).status_code == 200


@pytest.mark.trace("T-E05-03", "R06", "V08", "I2")
def test_adapter_trust_and_no_attempt_before_start(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = _proposed(hub)
    early = attempt(hub, "camera", request_id, failed_attempt(), "idem-key-a-0")
    assert early.status_code == 409 and early.json()["code"] == "INVALID_TRANSITION"
    crew = attempt(hub, "mara", request_id, failed_attempt(), "idem-key-a-1")
    assert crew.status_code == 403
    started = started_pulse(hub)
    running = str(started["request_id"])
    assert attempt(hub, "camera", running, failed_attempt(1), "idem-key-a-2").status_code == 200
    rogue = attempt(hub, "rogue", running, success_attempt(2), "idem-key-a-3")
    assert rogue.status_code == 403 and rogue.json()["code"] == "ADAPTER_NOT_TRUSTED"
    assert hub.observations.drafts == []
    unknown = attempt(hub, "camera", "mrq_missing", failed_attempt(), "idem-key-a-4")
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    ("edit", "code", "field"),
    [
        ({"origin": "SIM"}, "IDENTITY_MISMATCH", "/observations/0/origin_mode"),
        ({"unit": "bpm"}, "INVALID_UNIT", "/observations/0/unit"),
        ({"patient": "kai"}, "IDENTITY_MISMATCH", "/observations/0/patient_id"),
        ({"code": "spo2"}, "IDENTITY_MISMATCH", "/observations/0/code"),
        ({"method": "device"}, "IDENTITY_MISMATCH", "/observations/0/acquisition_method"),
    ],
)
@pytest.mark.trace("T-E05-06", "E05-03", "R06", "V15", "I8")
def test_attempt_values_are_checked_before_any_write(
    tmp_path: Path, edit: dict[str, str], code: str, field: str
) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    body = success_attempt(1)
    value = camera_value(96, origin=edit.get("origin", "LIVE"), unit=edit.get("unit", "/min"))
    value["patient_id"] = edit.get("patient", value["patient_id"])
    value["code"] = edit.get("code", value["code"])
    value["acquisition_method"] = edit.get("method", value["acquisition_method"])
    body["observations"] = [value]
    refused = attempt(hub, "camera", request_id, body, "idem-key-v-1")
    assert refused.status_code == 422 and refused.json()["code"] == code
    assert refused.json()["field_errors"][0]["field"] == field
    assert hub.uows.count("measurement_attempt") == 0 and hub.observations.drafts == []


@pytest.mark.trace("T-E05-04", "R08", "D08")
def test_success_without_a_value_is_not_a_result(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    empty = dict(success_attempt(1), observations=[])
    response = attempt(hub, "camera", request_id, empty, "idem-key-e-1")
    assert response.status_code == 200
    assert response.json()["state"] == "in_progress" and response.json()["result_observation_ids"] == []
    missing_reason = dict(failed_attempt(2))
    del missing_reason["failure_reason"]
    assert attempt(hub, "camera", request_id, missing_reason, "idem-key-e-2").status_code == 400


def _raw(hub: Hub, url: str, content: bytes, key: str, content_type: str = "application/json") -> Any:
    return hub.client.post(
        url, content=content, headers=dict(headers("mara", key), **{"Content-Type": content_type})
    )


@pytest.mark.trace("T-E05-11", "R06", "R08", "V36")
def test_malformed_bodies_are_refused_with_the_envelope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    url = "/v1/measurement-requests"
    good = json.dumps(pulse_body())
    cases: list[tuple[bytes, str, int, str]] = [
        (good.encode(), "text/plain", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"{", "application/json", 400, "MALFORMED_REQUEST"),
        (b"[]", "application/json", 400, "MALFORMED_REQUEST"),
        (good[:-1].encode() + b', "reason": "x"}', "application/json", 400, "MALFORMED_REQUEST"),
        (
            json.dumps(dict(pulse_body(), operator_id=None)).encode(),
            "application/json",
            400,
            "MALFORMED_REQUEST",
        ),
        (json.dumps(dict(pulse_body(), extra=1)).encode(), "application/json", 400, "MALFORMED_REQUEST"),
        (json.dumps(dict(pulse_body(), reason="")).encode(), "application/json", 400, "MALFORMED_REQUEST"),
        (
            json.dumps(dict(pulse_body(), capability_code="nope")).encode(),
            "application/json",
            422,
            "INVALID_VALUE",
        ),
        (
            json.dumps(dict(pulse_body(), condition_ids=["ICL10", "ICL10"])).encode(),
            "application/json",
            400,
            "MALFORMED_REQUEST",
        ),
    ]
    for index, (content, content_type, status, code) in enumerate(cases):
        response = _raw(hub, url, content, f"idem-key-m-{index:02d}", content_type)
        assert (response.status_code, response.json()["code"]) == (status, code), index
        assert response.json()["message"] == code
    no_key = hub.client.post(url, json=pulse_body(), headers=headers("mara"))
    assert no_key.status_code == 400
    assert hub.uows.count("measurement_request") == 0


@pytest.mark.trace("T-E05-06", "R06", "I8")
def test_attempt_numbers_are_exact_and_finite(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    url = f"/v1/measurement-requests/{request_id}/attempts"
    base = json.dumps(success_attempt(1, value=0))
    camera = {
        "Authorization": "Bearer camera",
        "Content-Type": "application/json",
        "Idempotency-Key": "idem-key-n-ok",
    }
    exact = hub.client.post(
        url, content=base.replace('"value": 0', '"value": 96.40').encode(), headers=camera
    )
    assert exact.status_code == 200
    assert str(hub.observations.drafts[0].quantity.value) == "96.40"
    for index, literal in enumerate(("NaN", "Infinity", "true", '"96"')):
        bad = dict(camera, **{"Idempotency-Key": f"idem-key-n-bad-{index}"})
        response = hub.client.post(
            url, content=base.replace('"value": 0', '"value": ' + literal).encode(), headers=bad
        )
        assert response.status_code == 400, literal


def _officer_pulse(document: dict[str, Any]) -> None:
    for capability in document["capabilities"]:
        if capability["code"] == "pulse":
            capability["operator_requirement"] = "crew_medical_officer"


@pytest.mark.trace("T-E05-03", "T-E05-12", "R06", "V25", "I5")
def test_unconsented_accept_commits_and_returns_nothing(tmp_path: Path) -> None:
    kit_map = scratch_kit_map(tmp_path, _crewmate_pulse)
    hub = build_hub(tmp_path, kit_map=kit_map)
    seed_crew(hub)
    request_id = _proposed(hub)
    url = f"/v1/measurement-requests/{request_id}"
    hub.client.post(
        url + "/actions", json={"action": "submit"}, headers=headers("mara", "idem-key-u-2", If_Match='"1"')
    )
    document = json.loads(kit_map.read_text(encoding="utf-8"))
    pulse = {i["id"] for i in document["instruments"] if "pulse" in i["capability_codes"]}
    document["kits"]["lunar_surface"] = [i for i in document["kits"]["lunar_surface"] if i not in pulse]
    kit_map.write_text(json.dumps(document), encoding="utf-8")
    events = len(hub.uows.state.events)
    refused = hub.client.post(
        url + "/actions", json={"action": "accept"}, headers=headers("kai", "idem-key-u-3", If_Match='"2"')
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "CONSENT_REQUIRED"
    assert refused.json()["current_state"] is None
    assert pulse_body()["reason"] not in refused.text
    assert hub.client.get(url, headers=headers("mara")).json()["state"] == "awaiting_acceptance"
    assert hub.uows.count("measurement_gap") == 0
    assert len(hub.uows.state.events) == events


@pytest.mark.trace("T-E05-03", "R06", "V09", "I2")
def test_officer_only_capability_needs_a_qualified_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, kit_map=scratch_kit_map(tmp_path, _officer_pulse))
    seed_crew(hub)
    assigned = dict(pulse_body(), operator_id="kai")
    refused = hub.client.post(
        "/v1/measurement-requests", json=assigned, headers=headers("mara", "idem-key-o-1")
    )
    assert refused.status_code == 422
    assert refused.json()["field_errors"][0]["field"] == "/operator_id"
    request_id = _proposed(hub, "idem-key-o-2")
    url = f"/v1/measurement-requests/{request_id}/actions"
    hub.client.post(url, json={"action": "submit"}, headers=headers("mara", "idem-key-o-3", If_Match='"1"'))
    hub.consent.grants.update({("mara", "kai", "pulse"), ("mara", "cmo", "pulse")})
    plain = hub.client.post(
        url, json={"action": "accept"}, headers=headers("kai", "idem-key-o-4", If_Match='"2"')
    )
    assert plain.status_code == 403
    assert plain.json()["code"] == "ACTION_NOT_ALLOWED"
    officer = hub.client.post(
        url, json={"action": "accept"}, headers=headers("cmo", "idem-key-o-5", If_Match='"2"')
    )
    assert officer.status_code == 200
    assert officer.json()["operator_id"] == "cmo"


@pytest.mark.trace("T-E05-03", "T-E05-12", "R06", "V08", "I2", "I5")
def test_only_the_adapter_assigned_at_start_may_write(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    rogue = attempt(hub, "rogue", request_id, success_attempt(1), "idem-key-r-1")
    assert rogue.status_code == 403
    assert rogue.json()["code"] == "ADAPTER_NOT_TRUSTED"
    assert hub.uows.count("measurement_attempt") == 0
    other_camera = attempt(hub, "camera", request_id, success_attempt(1, device="camera_9"), "idem-key-r-2")
    assert other_camera.status_code == 422
    assert other_camera.json()["field_errors"][0]["field"] == "/observations/0/device_id"
    camera = attempt(hub, "camera", request_id, success_attempt(1), "idem-key-r-3")
    assert camera.status_code == 200
    assert camera.json()["state"] == "completed"
    assert hub.observations.drafts[0].patient_id == "mara"


@pytest.mark.trace("T-E05-03", "T-E05-10", "R08", "I2")
def test_start_without_an_assigned_adapter_fails_closed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    hub.assignments.by_method.clear()
    request_id = _proposed(hub)
    url = f"/v1/measurement-requests/{request_id}/actions"
    for revision, action in enumerate(("submit", "accept"), start=1):
        response = hub.client.post(
            url,
            json={"action": action},
            headers=headers("mara", f"idem-key-n-{action}", If_Match=f'"{revision}"'),
        )
        assert response.status_code == 200
    start = hub.client.post(
        url, json={"action": "start"}, headers=headers("mara", "idem-key-n-start", If_Match='"3"')
    )
    assert start.status_code == 403
    assert start.json()["field_errors"][0]["code"] == "adapter_unassigned"
    assert hub.uows.count("measurement_acquisition") == 0
    late = attempt(hub, "camera", request_id, success_attempt(1), "idem-key-n-late")
    assert late.status_code == 409
