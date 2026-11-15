"""T2 durability against real SQLite, the real E01 coordinator and task service (R14, V16, V35, AGENTS 6).

Each test traces to AGENTS section 6 failure cases: crash at each step of a command, duplicate and retried
submissions, concurrent edits from two clients, disk full, deletion racing late work."""

from __future__ import annotations

import threading
from pathlib import Path

from . import builders
import pytest
from .real_station import OTHER, OWNER, Rig, build, person

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.reports.domain.confirmation import ConfirmationCommand, Decision, ReportVersion

pytestmark = [pytest.mark.trace("R14", "V16", "V35")]
LINES = [["Pulse at rest 72 bpm", "Blood oxygen 98 %"]]


def code(error: pytest.ExceptionInfo[ProblemError]) -> ErrorCode:
    return error.value.code


def upload(rig: Rig, key: str = "up1", data: bytes | None = None, who: str = OWNER) -> str:
    response = rig.upload(data or builders.pdf(LINES), key=key, who=who, examination_date="2026-11-10")
    assert response.status_code == 201, response.text
    return str(response.json()["report_id"])


def extracted(rig: Rig, key: str = "up1") -> tuple[str, dict[str, object], str]:
    report_id = upload(rig, key)
    assert rig.runner.drain() == 1
    response = rig.get(OWNER, f"/v1/reports/{report_id}")
    return report_id, response.json(), response.headers["ETag"]  # type: ignore[attr-defined]


def rows(rig: Rig, table: str) -> int:
    with rig.db.snapshot() as view:
        return int(str(view.connection.rows(f"SELECT count(*) FROM {table}", (), limit=1)[0][0]))


def confirm_all(
    rig: Rig, report_id: str, body: dict[str, object], etag: str, key: str = "c1", who: str = OWNER
):  # type: ignore[no-untyped-def]
    drafts = body["extraction_drafts"]
    assert isinstance(drafts, list)
    payload = {"confirmations": [{"draft_id": d["draft_id"], "decision": "confirm"} for d in drafts]}
    return rig.confirm(who, report_id, payload, key=key, etag=etag)


# ---- the happy path and what it stores ----------------------------------------------------------------


def test_upload_round_trips_one_report_and_extracts_drafts_end_to_end(tmp_path: Path) -> None:
    rig = build(tmp_path)
    _, body, _ = extracted(rig)
    drafts = {d["code"]: d for d in body["extraction_drafts"]}  # type: ignore[attr-defined]
    assert set(drafts) == {"pulse", "spo2"}
    assert (drafts["pulse"]["value"], drafts["pulse"]["unit"]) == (72, "/min")
    assert (drafts["spo2"]["value"], drafts["spo2"]["unit"]) == (98, "%")
    # the drafts propose the date the owner gave at upload; the person confirms it
    assert all(d["review_status"] == "proposed" and d["date"] == "2026-11-10" for d in drafts.values())
    assert body["examination_date"] == "2026-11-10" and body["report_date"] is None and body["author"] is None


def test_the_source_bytes_come_back_identical_and_only_to_the_owner(tmp_path: Path) -> None:
    rig = build(tmp_path)
    data = builders.pdf(LINES, flate=True)
    report_id = upload(rig, data=data)
    assert rig.store.read_source(person(), report_id) == data
    assert rig.store.read_source(person(OTHER), report_id) is None


def test_confirmation_appends_the_attributed_finding_and_the_revision_advances(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = extracted(rig)
    response = confirm_all(rig, report_id, body, etag)
    assert response.status_code == 200, response.text
    assert {d["review_status"] for d in response.json()["extraction_drafts"]} == {"confirmed"}
    assert response.headers["ETag"] != etag
    with rig.db.snapshot() as view:
        stored = view.connection.rows(
            "SELECT code,value,unit,origin,role,confirmer_id,record_ref FROM reports_finding ORDER BY code",
            (),
            limit=10,
        )
    assert stored == (
        ("pulse", "72", "/min", "extraction", "result", OWNER, None),
        ("spo2", "98", "%", "extraction", "result", OWNER, None),
    )


# ---- crash at each step of upload --------------------------------------------------------------------


def test_disk_full_before_the_seal_stores_nothing_and_a_retry_succeeds(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rig.store_faults.fail_seal = True
    failed = rig.upload(builders.pdf(LINES), key="up1")
    assert failed.status_code >= 500 or failed.status_code == 503
    assert rows(rig, "reports_report") == 0 and rows(rig, "platform_private_reference") == 0
    assert upload(rig, "up1")
    assert rows(rig, "reports_report") == 1


def test_a_crash_after_the_seal_before_the_hub_commit_leaves_no_report_and_recovery_discards_it(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)

    def crash(reference: object) -> None:
        raise StorageUnavailable("crash")

    rig.store_faults.after_seal = crash
    assert rig.upload(builders.pdf(LINES), key="up1").status_code >= 500
    assert rows(rig, "reports_report") == 0
    rig.crash_and_restart()
    again = rig.upload(builders.pdf(LINES), key="up1")  # same key after the restart
    assert again.status_code == 201
    assert rows(rig, "reports_report") == 1 and rows(rig, "reports_version") == 1


def test_a_crash_after_the_hub_commit_before_the_ack_still_yields_exactly_one_report(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rig.store_faults.fail_ack = True
    assert rig.upload(builders.pdf(LINES), key="up1").status_code >= 500
    assert rows(rig, "reports_report") == 1  # the hub commit is durable
    assert rig.crash_and_restart() >= 1  # recovery completes the acknowledgment
    again = rig.upload(builders.pdf(LINES), key="up1")
    assert again.status_code == 201 and rows(rig, "reports_report") == 1
    assert rig.get(OWNER, f"/v1/reports/{again.json()['report_id']}").status_code == 200  # type: ignore[attr-defined]


def test_an_upload_rejected_inside_the_hub_transaction_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(tmp_path)

    def refuse(uow: object, task: object) -> None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "boom")

    monkeypatch.setattr(rig.tasks, "submit", refuse)
    assert rig.upload(builders.pdf(LINES), key="up1").status_code == 500
    assert rows(rig, "reports_report") == 0
    assert rig.store_faults.pending(after_entry_id=None, limit=10) == ()  # no orphan was left sealed


# ---- duplicate and retried submissions ---------------------------------------------------------------


def test_the_same_key_and_body_replays_the_same_report_and_never_a_second(tmp_path: Path) -> None:
    rig = build(tmp_path)
    first = rig.upload(builders.pdf(LINES), key="up1")
    second = rig.upload(builders.pdf(LINES), key="up1")
    assert first.status_code == second.status_code == 201
    assert first.json()["report_id"] == second.json()["report_id"]
    assert rows(rig, "reports_report") == 1


def test_the_same_key_with_another_body_is_a_conflict(tmp_path: Path) -> None:
    rig = build(tmp_path)
    upload(rig, "up1")
    other = rig.upload(builders.pdf([["Breathing 14 per minute"]]), key="up1")
    assert other.status_code == 409 and other.json()["code"] == "DUPLICATE_COMMAND"
    assert rows(rig, "reports_report") == 1


def test_a_replayed_confirmation_returns_the_result_and_appends_no_second_finding(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = extracted(rig)
    first = confirm_all(rig, report_id, body, etag)
    second = confirm_all(rig, report_id, body, etag)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert rows(rig, "reports_finding") == 2


def test_a_key_reused_on_another_report_is_a_conflict(tmp_path: Path) -> None:
    rig = build(tmp_path)
    first, body, etag = extracted(rig, "up1")
    confirm_all(rig, first, body, etag, key="shared")
    second = upload(rig, "up2", builders.pdf([["Pulse at rest 60 bpm"]]))
    assert rig.runner.drain() == 1
    fresh = rig.get(OWNER, f"/v1/reports/{second}")
    clash = confirm_all(rig, second, fresh.json(), fresh.headers["ETag"], key="shared")  # type: ignore[attr-defined]
    assert clash.status_code == 409 and clash.json()["code"] == "DUPLICATE_COMMAND"
    assert rows(rig, "reports_finding") == 2


# ---- concurrent confirm from two clients -------------------------------------------------------------


def test_two_clients_confirming_one_revision_produce_exactly_one_accepted_result(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = extracted(rig)
    report = rig.workflows.get_report(person(), report_id)
    drafts = report.extraction_drafts
    revision = ReportVersion(report.revision, report.source_version_id)
    outcomes: list[object] = []
    gate = threading.Barrier(2)

    def client(key: str) -> None:
        command = ConfirmationCommand(
            revision,
            OWNER,
            tuple(Decision(d.draft_id, "confirm", None, None, None) for d in drafts),
            OWNER,
            report.uploaded_at,
        )
        gate.wait()
        try:
            outcomes.append(rig.workflows.confirm(person(), key=key, report_id=report_id, command=command))
        except ProblemError as error:
            outcomes.append(error.code)

    threads = [threading.Thread(target=client, args=(key,)) for key in ("a1", "b1")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(not isinstance(o, ErrorCode) for o in outcomes) == 1
    assert [o for o in outcomes if isinstance(o, ErrorCode)] == [ErrorCode.STALE_VERSION]
    assert rows(rig, "reports_finding") == len(drafts)


def test_a_stale_etag_and_a_missing_etag_are_refused(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = extracted(rig)
    assert confirm_all(rig, report_id, body, etag, key="c1").status_code == 200
    stale = confirm_all(rig, report_id, body, etag, key="c2")
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_VERSION"
    missing = rig.confirm(
        OWNER, report_id, {"confirmations": [{"draft_id": "x", "decision": "reject"}]}, key="c3", etag=None
    )
    assert missing.status_code == 428 and missing.json()["code"] == "VERSION_REQUIRED"
    assert rows(rig, "reports_finding") == 2


# ---- deletion wins -----------------------------------------------------------------------------------


def test_deleting_a_report_wins_over_a_late_extraction(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id = upload(rig)
    rig.workflows.delete_report(person(), key="del1", report_id=report_id)
    assert rig.runner.drain() == 0  # the claimed task finds nothing to extract and fails
    assert rig.get(OWNER, f"/v1/reports/{report_id}").status_code == 404  # type: ignore[attr-defined]
    assert rig.store.read_source(person(), report_id) is None
    with rig.db.snapshot() as view:
        states = view.connection.rows("SELECT deleted FROM reports_version", (), limit=10)
    assert states == ((1,),)


def test_deleting_a_report_wins_over_a_late_confirmation(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = extracted(rig)
    rig.workflows.delete_report(person(), key="del1", report_id=report_id)
    late = confirm_all(rig, report_id, body, etag)
    assert late.status_code == 404
    assert rows(rig, "reports_finding") == 0


def test_a_deletion_that_lands_inside_a_confirmation_rejects_the_publication(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id, body, etag = extracted(rig)
    report = rig.workflows.get_report(person(), report_id)
    real = rig.store.load

    def late_delete(session, rid):  # type: ignore[no-untyped-def]
        found = real(session, rid)
        rig.store.load = real  # type: ignore[method-assign]
        rig.workflows.delete_report(person(), key="del1", report_id=report_id)
        return found

    rig.store.load = late_delete  # type: ignore[method-assign]
    command = ConfirmationCommand(
        ReportVersion(report.revision, report.source_version_id),
        OWNER,
        tuple(Decision(d.draft_id, "confirm", None, None, None) for d in report.extraction_drafts),
        OWNER,
        report.uploaded_at,
    )
    with pytest.raises(ProblemError) as error:
        rig.workflows.confirm(person(), key="c1", report_id=report_id, command=command)
    assert code(error) is ErrorCode.NOT_FOUND
    assert rows(rig, "reports_finding") == 0


def test_a_key_whose_report_was_deleted_never_yields_an_accepted_result(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id = upload(rig)
    rig.workflows.delete_report(person(), key="del1", report_id=report_id)
    with pytest.raises(ProblemError) as again:
        rig.workflows.upload(
            person(),
            key="up1",
            data=builders.png(),
            original_name="other.png",
            declared_type="image/png",
            access_scope=None,
            examination_date=None,
            report_date=None,
            origin_mode="SIM",
        )
    assert code(again) is ErrorCode.DUPLICATE_COMMAND
    assert rows(rig, "reports_report") == 1
