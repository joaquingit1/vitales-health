"""E09 on the served app: create_app over real SQLite with E02 identity, the report module, its three routes,
the real private coordinator and task service (harness vault and store, see support.py).

Upload a PDF, quarantine a disguised file, the owner reads, another crew member gets NOT_FOUND, confirm with
If-Match, a stale revision is refused, and the same idempotency key replays."""

from __future__ import annotations

from pathlib import Path

import builders
import pytest
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.kernel.errors import StorageUnavailable
from crew_health.platform.adapters.audit import SQLiteAuditVerifier
from fastapi.testclient import TestClient
from httpx import Response
from support import credential, seed, serve, sim_members, station

pytestmark = [pytest.mark.trace("R14", "R16", "V21", "D18")]
LINES = [["Pulse at rest 72 bpm"]]


def boot(tmp_path: Path) -> tuple[TestClient, str, str]:
    running = serve(station(tmp_path), seed=False)
    seed(running)
    first, second = (m.person_id for m in sim_members()[:2])
    return TestClient(running.app), first, second


def sign_in(client: TestClient, person: str) -> dict[str, str]:
    response = client.post(
        "/v1/sessions", json={"kind": "crew", "person_id": person, "credential": credential(person)}
    )
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def upload(
    client: TestClient,
    auth: dict[str, str],
    key: str,
    data: bytes,
    declared: str = "application/pdf",
    name: str = "report.pdf",
    **f: str,
) -> Response:
    return client.post(
        "/v1/reports",
        headers={**auth, "Idempotency-Key": key},
        files={"file": (name, data, declared)},
        data=f,
    )


def code(response: Response) -> str:
    return ErrorEnvelope.model_validate(response.json()).code


def test_served_report_journey(tmp_path: Path) -> None:
    client, owner, other = boot(tmp_path)
    mine, theirs = sign_in(client, owner), sign_in(client, other)
    pdf = builders.pdf(LINES)
    created = upload(client, mine, "up_key_1", pdf, examination_date="2026-11-10")
    assert created.status_code == 201, created.text
    report_id = created.json()["report_id"]
    assert created.json()["patient_id"] == owner
    assert created.headers["location"] == "/v1/reports/" + report_id

    read = client.get("/v1/reports/" + report_id, headers=mine)
    assert read.status_code == 200
    (draft,) = read.json()["extraction_drafts"]
    assert draft["code"]
    assert draft["review_status"] == "proposed"
    assert draft["value"] == 72
    etag = read.headers["etag"]

    replay = upload(client, mine, "up_key_1", pdf, examination_date="2026-11-10")
    assert replay.status_code == 201
    assert replay.json()["report_id"] == report_id
    clash = upload(client, mine, "up_key_1", builders.pdf([["Pulse at rest 99 bpm"]]))
    assert clash.status_code == 409
    assert code(clash) == "DUPLICATE_COMMAND"

    hidden = client.get("/v1/reports/" + report_id, headers=theirs)
    assert hidden.status_code == 404
    assert code(hidden) == "NOT_FOUND"

    path = f"/v1/reports/{report_id}/confirmations"
    body = {"confirmations": [{"draft_id": draft["draft_id"], "decision": "confirm"}]}
    missing = client.post(path, headers={**mine, "Idempotency-Key": "cf_key_0"}, json=body)
    assert missing.status_code == 428
    assert code(missing) == "VERSION_REQUIRED"
    confirmed = client.post(
        path, headers={**mine, "Idempotency-Key": "cf_key_1", "If-Match": etag}, json=body
    )
    assert confirmed.status_code == 200, confirmed.text
    assert [d["review_status"] for d in confirmed.json()["extraction_drafts"]] == ["confirmed"]

    again = client.post(path, headers={**mine, "Idempotency-Key": "cf_key_1", "If-Match": etag}, json=body)
    assert again.status_code == 200
    assert again.json() == confirmed.json()

    stale = client.post(path, headers={**mine, "Idempotency-Key": "cf_key_2", "If-Match": etag}, json=body)
    assert stale.status_code == 409
    assert code(stale) == "STALE_VERSION"
    foreign = client.post(
        path,
        headers={**theirs, "Idempotency-Key": "cf_key_3", "If-Match": confirmed.headers["etag"]},
        json=body,
    )
    assert foreign.status_code == 404


def test_disguised_file_is_quarantined_and_never_extracted(tmp_path: Path) -> None:
    client, owner, _ = boot(tmp_path)
    mine = sign_in(client, owner)
    disguised = upload(client, mine, "up_key_png", builders.png(), declared="application/pdf")
    assert disguised.status_code == 201, disguised.text
    report_id = disguised.json()["report_id"]
    assert client.get("/v1/reports/" + report_id, headers=mine).json()["extraction_drafts"] == []
    refused = client.post(
        f"/v1/reports/{report_id}/confirmations",
        headers={**mine, "Idempotency-Key": "cf_key_q", "If-Match": disguised.headers["etag"]},
        json={"confirmations": [{"draft_id": "drf_" + "0" * 32, "decision": "confirm"}]},
    )
    assert refused.status_code == 409
    assert code(refused) == "INVALID_TRANSITION"
    unsupported = upload(client, mine, "up_key_txt", b"hello", declared="text/plain")
    assert unsupported.status_code == 415
    assert code(unsupported) == "UNSUPPORTED_MEDIA_TYPE"


def test_scenario_audit_chain_and_hub_tables_hold_no_report_content(tmp_path: Path) -> None:
    """End to end: upload, extract, confirm. The module's audit policy accepts the confirmation (a module
    without one fails with INVALID_VALUE on its first audit), the audit chain verifies, and no hub table holds
    the file name, the source text or the draft value (V25, AGENTS 6 privacy)."""
    client, owner, _ = boot(tmp_path)
    mine = sign_in(client, owner)
    canary_name = "CANARYNAME-4471.pdf"
    created = upload(client, mine, "up_key_a", builders.pdf([["Pulse at rest 73 bpm"]]), name=canary_name)
    report_id = created.json()["report_id"]
    read = client.get("/v1/reports/" + report_id, headers=mine)
    (draft,) = read.json()["extraction_drafts"]
    confirmed = client.post(
        f"/v1/reports/{report_id}/confirmations",
        headers={**mine, "Idempotency-Key": "cf_key_a", "If-Match": read.headers["etag"]},
        json={"confirmations": [{"draft_id": draft["draft_id"], "decision": "reject"}]},
    )
    assert confirmed.status_code == 200, confirmed.text
    db = client.app.state.runtime.database
    with db.snapshot() as view:
        head = view.connection.rows(
            "SELECT seq, entry_hash FROM platform_audit ORDER BY seq DESC LIMIT 1", (), limit=1
        )[0]
        events = view.connection.rows("SELECT envelope FROM platform_audit", (), limit=500)
        tables = view.connection.rows(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'", (), limit=500
        )
        chunks: list[str] = []
        for (name,) in tables:
            try:
                chunks.append(str(view.connection.rows(f"SELECT * FROM {name} LIMIT 500", (), limit=500)))
            except StorageUnavailable:
                print("UNREADABLE", name)
        dump = " ".join(chunks)
    assert any(b"reports.confirmed" in bytes(row[0]) for row in events)
    verdict = SQLiteAuditVerifier(db, page_size=5).verify(
        expected_head_seq=int(str(head[0])), expected_head_hash=str(head[1])
    )
    assert verdict.ok
    assert canary_name not in dump
    assert "73" not in canary_name
    assert "Pulse at rest 73" not in dump
