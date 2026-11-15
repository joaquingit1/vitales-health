"""T3 HTTP boundary, privacy between crew members, configuration and the audit chain (R14, R16, V25, V69, V75)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import builders
import pytest
from real_station import OFFICER, OTHER, OWNER, Rig, build, person, release_documents

from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.platform.adapters.audit import SQLiteAuditVerifier
from crew_health.platform.application.private import private_identity_bytes as platform_identity_bytes
from crew_health.reports.adapters.store import private_identity_bytes

LINES = [["Pulse at rest 72 bpm"]]
CANARY_TEXT = "CANARYWORD-7731"
pytestmark = [pytest.mark.trace("R14", "R16", "V25", "V69", "V75")]


def ready(
    rig: Rig, lines: list[list[str]] | None = None, key: str = "up1", name: str = "report.pdf"
) -> tuple[str, dict[str, object], str]:
    response = rig.upload(builders.pdf(lines or LINES), key=key, name=name, examination_date="2026-11-10")
    report_id = str(response.json()["report_id"])
    rig.runner.drain()
    got = rig.get(OWNER, f"/v1/reports/{report_id}")
    return report_id, got.json(), got.headers["ETag"]  # type: ignore[attr-defined]


def dump(rig: Rig) -> str:
    """Every row of every hub table as text, for canary checks."""
    chunks: list[str] = []
    with rig.db.snapshot() as view:
        names = view.connection.rows("SELECT name FROM sqlite_master WHERE type='table'", (), limit=500)
        for (name,) in names:
            chunks.append(str(view.connection.rows(f"SELECT * FROM {name}", (), limit=1000)))
    return "\n".join(chunks)


# ---- contract ---------------------------------------------------------------------------------------


def test_routes_status_codes_and_error_envelopes_follow_the_contract(tmp_path: Path) -> None:
    rig = build(tmp_path)
    assert rig.client.get("/v1/reports/rpt_x").json()["code"] == "UNAUTHENTICATED"
    assert rig.get(OWNER, "/v1/reports/rpt_missing").status_code == 404  # type: ignore[attr-defined]
    no_key = rig.client.post(
        "/v1/reports", headers={"X-Person": OWNER}, files={"file": ("a.pdf", b"x", "application/pdf")}
    )
    assert no_key.status_code == 400 and no_key.json()["code"] == "MALFORMED_REQUEST"
    no_file = rig.client.post(
        "/v1/reports",
        headers={"X-Person": OWNER, "Idempotency-Key": "k1"},
        data={"access_scope": "self"},
        files={"x": ("a", b"x")},
    )
    assert no_file.status_code == 400
    text = rig.upload(b"plain text, not a report", key="k2", name="a.txt", ctype="text/plain")
    assert text.status_code == 415 and text.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"
    mismatch = rig.upload(builders.png(), key="k3", name="a.pdf", ctype="application/pdf")
    assert mismatch.status_code == 201  # accepted for storage, quarantined for the disagreement
    assert (
        rig.workflows.get_report(person(), mismatch.json()["report_id"]).quarantine_reason == "type_mismatch"
    )
    bad_scope = rig.upload(builders.pdf(LINES), key="k4", access_scope="everyone")
    assert bad_scope.status_code == 422 and bad_scope.json()["code"] == "INVALID_VALUE"
    for response in (text, no_key, bad_scope):
        assert set(response.json()) == {
            "code",
            "message",
            "retryable",
            "field_errors",
            "correlation_id",
            "current_state",
            "supported_alternative",
        }


def test_too_large_is_413_and_the_narrowest_scope_is_the_default(tmp_path: Path) -> None:
    rig = build(tmp_path)
    big = b"%PDF-1.4\n" + b"0" * 20_000_000 + b"%%EOF"
    with pytest.raises(ProblemError) as error:
        rig.workflows.upload(
            person(),
            key="big",
            data=big,
            original_name="big.pdf",
            declared_type=None,
            access_scope=None,
            examination_date=None,
            report_date=None,
            origin_mode="SIM",
        )
    assert error.value.code is ErrorCode.PAYLOAD_TOO_LARGE
    ok = rig.upload(builders.pdf(LINES), key="k1")
    assert ok.json()["access_scope"] == "self" and ok.headers["Location"].endswith(ok.json()["report_id"])


def test_a_report_with_no_dates_keeps_them_null_and_a_number_cannot_then_be_confirmed(tmp_path: Path) -> None:
    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="k1")
    body = created.json()
    assert body["examination_date"] is None and body["report_date"] is None and body["author"] is None
    rig.runner.drain()
    got = rig.get(OWNER, f"/v1/reports/{body['report_id']}")
    draft = got.json()["extraction_drafts"][0]  # type: ignore[attr-defined]
    assert draft["date"] is None
    refused = rig.confirm(
        OWNER,
        body["report_id"],
        {"confirmations": [{"draft_id": draft["draft_id"], "decision": "confirm"}]},
        key="c1",
        etag=got.headers["ETag"],  # type: ignore[attr-defined]
    )
    assert refused.status_code == 422 and refused.json()["code"] == "INVALID_VALUE"
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_finding", (), limit=1) == ((0,),)


# ---- privacy between crew members ---------------------------------------------------------------------


def test_another_crew_member_and_an_officer_without_a_grant_cannot_see_or_act(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = ready(rig)
    for who in (OTHER, OFFICER):
        assert rig.get(who, f"/v1/reports/{report_id}").status_code == 404  # type: ignore[attr-defined]
        refused = rig.confirm(
            who,
            report_id,
            {"confirmations": [{"draft_id": "x", "decision": "reject"}]},
            key="c" + who,
            etag=etag,
        )
        assert refused.status_code == 404
    assert rig.store.read_source(person(OTHER), report_id) is None
    assert (
        rig.store.read_source(person(OFFICER, roles=frozenset({Role.CREW_MEDICAL_OFFICER})), report_id)
        is None
    )
    assert rig.get(OWNER, f"/v1/reports/{report_id}").status_code == 200  # type: ignore[attr-defined]


@pytest.mark.parametrize("kind", ["station", "ground", "adapter"])
def test_station_ground_adapter_and_assistant_sessions_are_refused(tmp_path: Path, kind: str) -> None:
    rig = build(tmp_path)
    report_id, _, _ = ready(rig)
    other = SessionContext("s", kind, Actor("person", OWNER), OWNER, frozenset({Role.CREW}))  # type: ignore[arg-type]
    with pytest.raises(ProblemError) as error:
        rig.workflows.get_report(other, report_id)
    assert error.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    assistant = SessionContext("s", "crew", Actor("assistant", "assistant"), OWNER, frozenset({Role.CREW}))
    with pytest.raises(ProblemError) as refused:
        rig.workflows.get_report(assistant, report_id)
    assert refused.value.code is ErrorCode.UNAUTHORIZED_SCOPE


def test_no_private_text_reaches_hub_tables_audit_events_or_error_bodies(tmp_path: Path) -> None:
    rig = build(tmp_path)
    name = "canaryname-patient.pdf"
    report_id, body, etag = ready(rig, [[f"{CANARY_TEXT}", "Pulse at rest 72 bpm"]], name=name)
    confirmed = rig.confirm(
        OWNER,
        report_id,
        {
            "confirmations": [
                {"draft_id": body["extraction_drafts"][0]["draft_id"], "decision": "confirm", "unit": "kg"}
            ]
        },  # type: ignore[index]
        key="bad",
        etag=etag,
    )
    assert confirmed.status_code == 422 and "kg" not in confirmed.text and name not in confirmed.text
    ok = rig.confirm(
        OWNER,
        report_id,
        {"confirmations": [{"draft_id": body["extraction_drafts"][0]["draft_id"], "decision": "confirm"}]},  # type: ignore[index]
        key="good",
        etag=etag,
    )
    assert ok.status_code == 200
    stored = dump(rig)
    from hashlib import sha256

    for secret in (
        CANARY_TEXT,
        name,
        "canaryname",
        sha256(builders.pdf([[CANARY_TEXT, "Pulse at rest 72 bpm"]])).hexdigest(),
    ):
        assert secret not in stored
    for reference in rig.store_faults.entries.values():  # the sealed store holds only what the vault returned
        assert reference[1].owner_person_id == OWNER


def test_the_audit_chain_stays_intact_across_upload_confirm_and_delete(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = ready(rig)
    rig.confirm(
        OWNER,
        report_id,
        {"confirmations": [{"draft_id": body["extraction_drafts"][0]["draft_id"], "decision": "confirm"}]},  # type: ignore[index]
        key="c1",
        etag=etag,
    )
    rig.workflows.delete_report(person(), key="del1", report_id=report_id)
    with rig.db.snapshot() as view:
        head = view.connection.rows(
            "SELECT seq,entry_hash FROM platform_audit ORDER BY seq DESC LIMIT 1", (), limit=1
        )[0]
        kinds = [r[0] for r in view.connection.rows("SELECT event_id FROM platform_audit", (), limit=100)]
    verdict = SQLiteAuditVerifier(rig.db, page_size=5).verify(
        expected_head_seq=int(str(head[0])), expected_head_hash=str(head[1])
    )
    assert verdict.ok and len(kinds) >= 4


def test_the_owner_task_audience_admits_only_the_current_owner(tmp_path: Path) -> None:
    from crew_health.kernel.uow import Audience
    from crew_health.reports.adapters.audience import ReportOwnerAudience
    from crew_health.reports.adapters.index import ReportIndex
    from crew_health.reports.application.workflows import REPORT_PROJECTION

    rig = build(tmp_path)
    report_id, _, _ = ready(rig)
    rule = ReportOwnerAudience(ReportIndex())
    audience = Audience("reports.owner", report_id, REPORT_PROJECTION, "owner_current_grant")
    with rig.db.snapshot() as view:
        assert rule.admits(view, person(OWNER), audience)
        assert not rule.admits(view, person(OTHER), audience)
        assert not rule.admits(view, replace(person(OWNER), kind="ground"), audience)
    rig.workflows.delete_report(person(), key="del1", report_id=report_id)
    with rig.db.snapshot() as view:
        assert not rule.admits(view, person(OWNER), audience)  # revoked while the task was running


# ---- configuration: content comes from the document, validated, last good kept -------------------------


def test_a_configuration_edit_changes_what_is_proposed_with_no_code_change(tmp_path: Path) -> None:
    example = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "docs/product/api/examples/config/indicator-catalog.example.json"
        ).read_text("utf-8")
    )
    catalog = json.loads(json.dumps(example["value"]))
    for indicator in catalog["indicators"]:
        if indicator["code"] == "pulse":
            indicator["label"] = "Heart beats"
    renamed = build(tmp_path / "a", catalog=catalog)
    for_new = renamed.upload(builders.pdf([["Heart beats 66 bpm", "Pulse at rest 70 bpm"]]), key="k1")
    renamed.runner.drain()
    drafts = renamed.workflows.get_report(person(), for_new.json()["report_id"]).extraction_drafts
    assert [(d.code, str(d.value)) for d in drafts] == [("pulse", "66")]  # the old label no longer matches
    original = build(tmp_path / "b")
    old = original.upload(builders.pdf([["Heart beats 66 bpm", "Pulse at rest 70 bpm"]]), key="k1")
    original.runner.drain()
    assert [
        (d.code, str(d.value))
        for d in original.workflows.get_report(person(), old.json()["report_id"]).extraction_drafts
    ] == [("pulse", "70")]


def test_confirmation_judges_by_the_pinned_release_and_fails_closed_when_it_is_gone(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = ready(rig)
    # a new, corrupt release becomes active: the report keeps the release it was extracted under
    broken = dict(release_documents())
    raw = b'{"version":"x","mappings":[]}'
    from hashlib import sha256

    broken[ConfigName.UNIT_MAPPING] = ConfigDocumentView(
        ConfigName.UNIT_MAPPING, sha256(raw).hexdigest(), raw
    )
    rig.releases.releases["release_2"] = MemoryRelease("release_2", broken)
    rig.releases.active_id = "release_2"
    draft_id = body["extraction_drafts"][0]["draft_id"]  # type: ignore[index]
    ok = rig.confirm(
        OWNER,
        report_id,
        {"confirmations": [{"draft_id": draft_id, "decision": "confirm"}]},
        key="c1",
        etag=etag,
    )
    assert ok.status_code == 200
    other_id, other_body, other_etag = ready(rig, key="up2", lines=[["Pulse at rest 61 bpm"]])
    del rig.releases.releases["release_1"]
    gone = rig.confirm(
        OWNER,
        other_id,
        {
            "confirmations": [
                {"draft_id": other_body["extraction_drafts"][0]["draft_id"], "decision": "confirm"}
            ]
        },  # type: ignore[index]
        key="c2",
        etag=other_etag,
    )
    assert gone.status_code in (409, 422, 500) and gone.json()["code"] in (
        "CONFIG_INVALID",
        "INVALID_TRANSITION",
    )
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_finding", (), limit=1) == ((1,),)


def test_the_private_identity_bytes_copy_matches_the_coordinators() -> None:
    from crew_health.kernel.commands import command_identity
    from crew_health.kernel.private import PrivateIntent

    identity = command_identity(
        key="k",
        actor_ref="a",
        operation="uploadReport",
        target="/v1/reports",
        parameters_json=b'{"a":1}',
        body_fingerprint="",
        fingerprint_kind="hmac_sha256",
    )
    intent = PrivateIntent(identity, "a", "ent_1", "report", "ver_1", "ver_0", b"payload")
    assert private_identity_bytes(intent) == platform_identity_bytes(intent)


def test_the_migration_is_expand_only_and_names_only_report_tables() -> None:
    import re

    from crew_health.platform.adapters.migrations import require_expansion

    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "../src/crew_health/reports/adapters/migrations/0001_reports.sql"
        )
        .resolve()
        .read_text("utf-8")
    )
    require_expansion(sql, module="reports")
    assert set(re.findall(r"CREATE TABLE (\w+)", sql)) == {
        "reports_report",
        "reports_version",
        "reports_confirmation",
        "reports_finding",
    }
