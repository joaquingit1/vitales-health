"""T-E09-01 T-E09-02 T-E09-04 T-E09-05 E09-01 E09-02 E09-03: application workflows on kernel fakes.

Covers upload, extraction publication under task fences, confirmation atomicity and replay, the manual
structured-entry backup path, owner-only authorization and the private-text canary."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from reports_harness import (
    AT,
    JPEG,
    OTHER,
    OWNER,
    PDF,
    PNG,
    Station,
    form_from_dictionary,
    numeric,
    session,
    station,
    text,
)

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.commands import CommandIdentity, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.reports.domain.confirmation import ConfirmationCommand, Decision, ReportVersion
from crew_health.reports.domain.extraction import DraftProposal, ExtractionResult
from crew_health.reports.domain.findings import FieldEntry, SpecialistEntry
from crew_health.reports.domain.model import ConfirmedFinding, Report, SourceSpan

pytestmark = pytest.mark.trace(
    "T-E09-01", "T-E09-02", "T-E09-04", "T-E09-05", "R14", "R16", "V21", "D17", "D18"
)

DAY = date(2026, 9, 30)
POLICIES = {
    "cardiac_troponin": numeric("cardiac_troponin", "mmol/L"),
    "rhythm_interpretation": text("rhythm_interpretation"),
    "radiation_dose": numeric("radiation_dose", "mGy"),
    "radiation_dose_reading": text("radiation_dose_reading"),
}
ASSISTANT = session(actor_kind="assistant")
STATION_SESSION = session(kind="station", roles=frozenset({Role.STATION}))
GROUND_SESSION = session(kind="ground", actor_kind="ground", roles=frozenset({Role.GROUND}))
OFFICER_OTHER = session(OTHER, roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
NOT_OWNERS = [ASSISTANT, STATION_SESSION, GROUND_SESSION, session(None)]


def fresh(**kw: object) -> Station:
    forms = {
        "radiation_dose": form_from_dictionary(
            "radiation_dose",
            result=POLICIES["radiation_dose"],
            interpretation=POLICIES["radiation_dose_reading"],
        )
    }
    return station(policies=POLICIES, forms=forms, **kw)  # type: ignore[arg-type]


def extraction(report: Report, *proposals: DraftProposal) -> ExtractionResult:
    return ExtractionResult(report.source_version_id, "extract-1", (100,), proposals)


DEFAULT = (
    DraftProposal(SourceSpan(1, 0, 10), "cardiac_troponin", Decimal("5.2"), "mmol/L", DAY),
    DraftProposal(SourceSpan(1, 10, 20), "rhythm_interpretation", "sinus rhythm", None, None),
)


def drafted(st: Station, proposals: tuple[DraftProposal, ...] = DEFAULT) -> Report:
    report = st.upload()
    st.workflows.record_extraction(session(), st.lease(report), extraction(report, *proposals))
    return st.store.reports[report.report_id]


def cmd(report: Report, *indexes: int, reject: tuple[int, ...] = ()) -> ConfirmationCommand:
    decisions = tuple(
        Decision(report.extraction_drafts[i].draft_id, "confirm", None, None, None) for i in indexes
    )
    decisions += tuple(
        Decision(report.extraction_drafts[i].draft_id, "reject", None, None, None) for i in reject
    )
    return ConfirmationCommand(
        ReportVersion(report.revision, report.source_version_id), OWNER, decisions, "ignored", AT
    )


def code(error: pytest.ExceptionInfo[ProblemError]) -> ErrorCode:
    return error.value.code


# ---- E09-01 upload -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "media"), [(PDF, "application/pdf"), (PNG, "image/png"), (JPEG, "image/jpeg")]
)
def test_upload_retains_bytes_hash_patient_source_and_dates(data: bytes, media: str) -> None:
    st = fresh()
    report = st.upload(data, declared_type=media)
    assert st.store.sources[report.report_id] == data
    assert (report.media_type, report.size_bytes, report.patient_id, report.origin_mode) == (
        media,
        len(data),
        OWNER,
        "SIM",
    )
    assert (report.examination_date, report.report_date) == (DAY, DAY)
    assert report.state == "accepted" and report.extraction_drafts == () and report.revision == 1
    assert st.store.reports[report.report_id] == report
    import hashlib

    assert report.file_sha256 == hashlib.sha256(data).hexdigest()
    assert report.access_scope == "self" and report.author is None


def test_unknown_dates_stay_null_and_a_datetime_is_refused() -> None:
    st = fresh()
    report = st.upload(examination_date=None, report_date="")
    assert (report.examination_date, report.report_date) == (None, None)
    with pytest.raises(ProblemError):
        st.upload(key="up2", examination_date="2026-09-30T00:00:00Z")
    assert len(st.store.reports) == 1


def test_unsafe_input_is_quarantined_stored_but_never_extracted_or_confirmed() -> None:
    st = fresh()
    report = st.upload(PDF.replace(b"/Catalog", b"/JavaScript"))
    assert (report.state, report.quarantine_reason, report.extraction_drafts) == (
        "quarantined",
        "active_content",
        (),
    )
    with pytest.raises(ProblemError) as late:
        st.workflows.record_extraction(session(), st.lease(report), extraction(report, *DEFAULT))
    assert code(late) is ErrorCode.INVALID_TRANSITION
    with pytest.raises(ProblemError):
        st.workflows.confirm(
            session(),
            key="c1",
            report_id=report.report_id,
            command=ConfirmationCommand(
                ReportVersion(1, report.source_version_id),
                OWNER,
                (Decision("drf_1", "confirm", None, None, None),),
                OWNER,
                AT,
            ),
        )
    assert st.recorder.recorded == []


def test_unsupported_oversize_and_empty_input_store_nothing() -> None:
    st = fresh(max_report=len(PDF))
    assert st.upload(PDF).state == "accepted"  # exactly at the injected bound
    for key, data, expected in (
        ("a", b"GIF89a....", ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        ("b", PDF + b"\x00", ErrorCode.PAYLOAD_TOO_LARGE),
        ("c", b"", ErrorCode.INVALID_VALUE),
    ):
        with pytest.raises(ProblemError) as error:
            st.upload(data, key=key)
        assert code(error) is expected
    assert len(st.store.reports) == 1


def test_the_size_bound_comes_from_the_hub_setting_not_code() -> None:
    assert fresh().ports.settings.integer("private.max_report_bytes") == 20000000
    small = fresh(max_report=len(PDF) - 1)
    with pytest.raises(ProblemError) as error:
        small.upload(PDF)
    assert code(error) is ErrorCode.PAYLOAD_TOO_LARGE


def test_retry_with_the_same_key_returns_the_same_report_and_a_changed_body_conflicts() -> None:
    st = fresh()
    first = st.upload()
    st.clock.at = AT + timedelta(minutes=5)
    assert st.upload() == first
    with pytest.raises(ProblemError) as error:
        st.upload(PNG)
    assert code(error) is ErrorCode.DUPLICATE_COMMAND
    assert len(st.store.reports) == 1


def test_a_storage_failure_is_never_swallowed_or_acknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    st = fresh()

    def broken(*args: object, **kwargs: object) -> None:
        raise StorageUnavailable("disk_full")

    monkeypatch.setattr(st.store, "store_new", broken)
    with pytest.raises(StorageUnavailable):
        st.upload()
    assert st.store.reports == {}


@pytest.mark.parametrize("who", NOT_OWNERS)
def test_only_an_owner_session_uploads(who: SessionContext) -> None:
    st = fresh()
    with pytest.raises(ProblemError) as error:
        st.upload(who=who)
    assert code(error) is ErrorCode.UNAUTHORIZED_SCOPE
    assert st.store.reports == {}


@pytest.mark.parametrize("name", ["", "a\x00b.pdf", "x" * 256])
def test_file_name_is_bounded_and_clean(name: str) -> None:
    with pytest.raises(ProblemError):
        fresh().upload(original_name=name)


def test_scope_and_origin_are_validated() -> None:
    st = fresh()
    with pytest.raises(ProblemError):
        st.upload(access_scope="everyone")
    with pytest.raises(ProblemError):
        st.upload(origin_mode="DEMO")
    assert st.upload(access_scope="self_and_ground").access_scope == "self_and_ground"


# ---- E09-02 extraction under task fences ------------------------------------------------------------


def test_extraction_publishes_proposed_drafts_and_completes_the_task_once() -> None:
    st = fresh()
    report = st.upload()
    lease = st.lease(report)
    after = st.workflows.record_extraction(session(), lease, extraction(report, *DEFAULT))
    stored = st.store.reports[report.report_id]
    assert stored == after and [d.review_status for d in stored.extraction_drafts] == ["proposed", "proposed"]
    assert st.factory.state.tasks[lease.task.task_id][1].state == "succeeded"
    assert st.recorder.recorded == [] and st.lineage.all_findings() == ()
    assert st.lineage.revisions[report.report_id] == 2


def test_a_cancelled_generation_cannot_publish_a_draft() -> None:
    st = fresh()
    report = st.upload()
    lease = st.lease(report)
    st.cancel(lease)
    with pytest.raises(ProblemError) as error:
        st.workflows.record_extraction(session(), lease, extraction(report, *DEFAULT))
    assert code(error) is ErrorCode.INVALID_TRANSITION
    assert st.store.reports[report.report_id] == report
    assert st.lineage.revisions[report.report_id] == 1


def test_an_expired_lease_cannot_publish_after_a_clock_jump() -> None:
    st = fresh()
    report = st.upload()
    lease = st.lease(report)
    st.clock.at = AT + timedelta(minutes=2)
    with pytest.raises(ProblemError):
        st.workflows.record_extraction(session(), lease, extraction(report, *DEFAULT))
    assert st.store.reports[report.report_id].extraction_drafts == ()


def test_late_work_after_a_newer_publication_is_refused() -> None:
    st = fresh()
    report = st.upload()
    first, second = st.lease(report), st.lease(report)
    st.workflows.record_extraction(session(), first, extraction(report, *DEFAULT[:1]))
    current = st.store.reports[report.report_id]
    with pytest.raises(ProblemError):
        st.workflows.record_extraction(session(), second, extraction(current, *DEFAULT[1:]))
    assert len(st.store.reports[report.report_id].extraction_drafts) == 1


def test_deletion_wins_over_late_extraction_and_late_confirmation() -> None:
    st = fresh()
    report = st.upload()
    lease = st.lease(report)
    st.store.deleted.add(report.report_id)
    with pytest.raises(ProblemError) as late:
        st.workflows.record_extraction(session(), lease, extraction(report, *DEFAULT))
    assert code(late) is ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as read:
        st.workflows.get_report(session(), report.report_id)
    assert code(read) is ErrorCode.NOT_FOUND


def test_the_pin_the_engine_and_the_actor_must_match() -> None:
    st = fresh()
    report = st.upload()
    lease = st.lease(report)
    with pytest.raises(ProblemError) as engine:
        st.workflows.record_extraction(
            session(), lease, replace(extraction(report, *DEFAULT), engine_version="extract-2")
        )
    assert code(engine) is ErrorCode.INVALID_TRANSITION
    st.store.reports[report.report_id] = replace(report, source_version_id="ver_changed")
    with pytest.raises(ProblemError) as stale:
        st.workflows.record_extraction(session(), lease, extraction(report, *DEFAULT))
    assert code(stale) is ErrorCode.STALE_VERSION
    st.store.reports[report.report_id] = report
    stranger = replace(lease, task=replace(lease.task, actor=Actor("person", OTHER)))
    with pytest.raises(ProblemError) as actor:
        st.workflows.record_extraction(session(), stranger, extraction(report, *DEFAULT))
    assert code(actor) is ErrorCode.UNAUTHORIZED_SCOPE
    other_kind = replace(lease, task=replace(lease.task, handler="other_handler"))
    with pytest.raises(ProblemError):
        st.workflows.record_extraction(session(), other_kind, extraction(report, *DEFAULT))


# ---- E09-02 confirmation -----------------------------------------------------------------------------


def test_nothing_unconfirmed_reaches_records() -> None:
    st = fresh()
    drafted(st)
    assert st.recorder.recorded == [] and st.lineage.all_findings() == ()


def test_confirmation_appends_through_the_recorder_in_one_unit_and_text_stays_text() -> None:
    st = fresh()
    report = drafted(st)
    after = st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0, 1))
    assert [d.review_status for d in after.extraction_drafts] == ["confirmed", "confirmed"]
    assert (
        st.store.reports[report.report_id] == after
        and st.lineage.revisions[report.report_id] == after.revision
    )
    (recorded,) = st.recorder.recorded
    assert recorded.value == Decimal("5.2") and recorded.unit == "mmol/L" and recorded.confirmer_id == OWNER
    kept = st.lineage.applied_keys["c1"]
    assert [f.record_ref is not None for f in kept] == [True, False]
    assert kept[1].value == "sinus rhythm" and kept[1].unit is None
    (audit,) = st.factory.state.audit
    assert audit.event_type == "reports.confirmed" and dict(audit.safe_metadata) == {
        "findings": 2,
        "rejected_total": 0,
        "revision": 3,
    }


def test_the_confirmer_is_the_session_never_the_request() -> None:
    st = fresh()
    report = drafted(st)
    st.workflows.confirm(
        session(), key="c1", report_id=report.report_id, command=replace(cmd(report, 1), confirmer_id=OTHER)
    )
    assert st.lineage.applied_keys["c1"][0].confirmer_id == OWNER


def test_reject_records_nothing_and_stays_rejected() -> None:
    st = fresh()
    report = drafted(st)
    after = st.workflows.confirm(
        session(), key="c1", report_id=report.report_id, command=cmd(report, reject=(0, 1))
    )
    assert st.recorder.recorded == [] and st.lineage.applied_keys["c1"] == ()
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="c2", report_id=report.report_id, command=cmd(after, 0))
    assert code(error) is ErrorCode.INVALID_TRANSITION


def test_same_key_replay_does_not_duplicate_findings() -> None:
    st = fresh()
    report = drafted(st)
    first = st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0))
    again = st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0))
    assert again == first and len(st.recorder.recorded) == 1 and len(st.lineage.all_findings()) == 1


def test_two_clients_on_one_revision_produce_one_accepted_result() -> None:
    st = fresh()
    report = drafted(st)
    st.workflows.confirm(session(), key="a", report_id=report.report_id, command=cmd(report, 0))
    with pytest.raises(ProblemError) as second:
        st.workflows.confirm(session(), key="b", report_id=report.report_id, command=cmd(report, 0))
    assert code(second) is ErrorCode.STALE_VERSION
    assert len(st.recorder.recorded) == 1


def test_a_race_under_the_barrier_is_stale_and_records_nothing_more() -> None:
    st = fresh()
    report = drafted(st)
    st.workflows.confirm(session(), key="a", report_id=report.report_id, command=cmd(report, 0))
    st.store.serve_stale = report  # the second client's read predates the first commit
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="b", report_id=report.report_id, command=cmd(report, 1))
    assert code(error) is ErrorCode.STALE_VERSION
    assert len(st.recorder.recorded) == 1 and "b" not in st.lineage.applied_keys
    assert st.store.reports[report.report_id].extraction_drafts[1].review_status == "proposed"


def test_a_crash_while_recording_leaves_no_partial_acceptance_and_the_retry_succeeds() -> None:
    st = fresh()
    report = drafted(st)
    st.recorder.fail = StorageUnavailable("io_error")
    with pytest.raises(StorageUnavailable):
        st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0, 1))
    assert st.store.reports[report.report_id] == report and st.lineage.applied_keys == {}
    assert st.lineage.revisions[report.report_id] == report.revision and st.factory.state.audit == []
    st.recorder.fail = None
    st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0, 1))
    assert len(st.recorder.recorded) == 1 and len(st.lineage.applied_keys["c1"]) == 2


def test_invalid_batches_are_refused_before_any_write() -> None:
    st = fresh()
    report = drafted(st)
    bad = replace(cmd(report, 0, 1), asserted_patient_id=None)
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=bad)
    assert code(error) is ErrorCode.IDENTITY_MISMATCH
    assert st.recorder.recorded == [] and st.store.reports[report.report_id] == report


def test_a_policy_edit_changes_what_confirmation_accepts_with_no_code_change() -> None:
    st = fresh()
    report = drafted(st)
    st.policies.policies["cardiac_troponin"] = numeric("cardiac_troponin", "mg/dL")
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0))
    assert code(error) is ErrorCode.INVALID_UNIT


def test_confirmation_judges_by_the_release_pinned_at_extraction_not_the_active_one() -> None:
    st = fresh()
    report = drafted(st)
    assert report.extraction_release_id == "release"
    st.activate_release("release_2", {**POLICIES, "cardiac_troponin": numeric("cardiac_troponin", "mg/dL")})
    after = st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0))
    assert st.lineage.applied_keys["c1"][0].unit == "mmol/L" and after.extraction_release_id == "release"
    fresh_extraction = st.upload(key="up2")
    st.workflows.record_extraction(
        session(), st.lease(fresh_extraction, release_id="release_2"), extraction(fresh_extraction, *DEFAULT)
    )
    pinned_new = st.store.reports[fresh_extraction.report_id]
    assert pinned_new.extraction_release_id == "release_2"
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="c2", report_id=pinned_new.report_id, command=cmd(pinned_new, 0))
    assert code(error) is ErrorCode.INVALID_UNIT


def test_an_unresolvable_pinned_release_refuses_confirmation_and_records_nothing() -> None:
    st = fresh()
    report = drafted(st)
    st.activate_release("release_2", POLICIES)
    del st.ports.releases.releases["release"]  # type: ignore[attr-defined]
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0))
    assert code(error) is ErrorCode.CONFIG_INVALID
    assert st.recorder.recorded == [] and st.lineage.applied_keys == {}
    assert st.store.reports[report.report_id] == report


def test_a_report_with_no_pinned_release_cannot_be_confirmed() -> None:
    st = fresh()
    report = st.upload()
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report))
    assert code(error) is ErrorCode.INVALID_TRANSITION


@pytest.mark.parametrize("coordinator_replays", [True, False])
def test_same_key_submitted_twice_from_one_revision_replays_instead_of_failing(
    coordinator_replays: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = fresh()
    report = drafted(st)
    if not coordinator_replays:
        monkeypatch.setattr(st.commands, "lookup", lambda uow, identity: None)
    real = st.lineage.binding
    state = {"first": True}

    def interleaved(view: object, *, owner_person_id: str, command_key: str) -> tuple[str, str] | None:
        if state["first"]:
            state["first"] = False
            st.workflows.confirm(session(), key="k", report_id=report.report_id, command=cmd(report, 0))
            return None  # this submission's snapshot read predates the other commit
        return real(view, owner_person_id=owner_person_id, command_key=command_key)

    monkeypatch.setattr(st.lineage, "binding", interleaved)
    st.store.serve_stale = report
    second = st.workflows.confirm(session(), key="k", report_id=report.report_id, command=cmd(report, 0))
    assert second == st.store.reports[report.report_id] and second.revision == report.revision + 1
    assert len(st.recorder.recorded) == 1 and len(st.lineage.applied_keys["k"]) == 1


def test_a_report_deleted_right_after_the_commit_is_not_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    st = fresh()
    report = drafted(st)
    real = st.store.load
    calls = {"n": 0}

    def load(who: SessionContext, report_id: str) -> Report | None:
        calls["n"] += 1
        return real(who, report_id) if calls["n"] == 1 else None

    monkeypatch.setattr(st.store, "load", load)
    with pytest.raises(ProblemError) as error:
        st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0))
    assert code(error) is ErrorCode.NOT_FOUND


# ---- authorization ----------------------------------------------------------------------------------


@pytest.mark.parametrize("who", NOT_OWNERS)
def test_non_owner_sessions_never_read_or_confirm(who: SessionContext) -> None:
    st = fresh()
    report = drafted(st)
    with pytest.raises(ProblemError) as read:
        st.workflows.get_report(who, report.report_id)
    assert code(read) is ErrorCode.UNAUTHORIZED_SCOPE
    with pytest.raises(ProblemError) as write:
        st.workflows.confirm(who, key="c1", report_id=report.report_id, command=cmd(report, 0))
    assert code(write) is ErrorCode.UNAUTHORIZED_SCOPE
    assert st.recorder.recorded == []


@pytest.mark.parametrize("who", [session(OTHER), OFFICER_OTHER])
def test_another_crew_member_gets_not_found_even_an_officer_without_a_grant(who: SessionContext) -> None:
    st = fresh()
    report = drafted(st)
    with pytest.raises(ProblemError) as read:
        st.workflows.get_report(who, report.report_id)
    assert code(read) is ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as write:
        st.workflows.confirm(who, key="c1", report_id=report.report_id, command=cmd(report, 0))
    assert code(write) is ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as missing:
        st.workflows.get_report(session(OTHER), "rpt_nope")
    assert code(missing) is ErrorCode.NOT_FOUND
    assert st.recorder.recorded == []


def test_deletion_wins_over_a_late_confirmation() -> None:
    st = fresh()
    report = drafted(st)
    for tombstone in (True, False):
        if tombstone:
            st.store.deleted.add(report.report_id)
        st.store.reports[report.report_id] = replace(report, state="deleted", extraction_drafts=())
        with pytest.raises(ProblemError) as error:
            st.workflows.confirm(session(), key="c1", report_id=report.report_id, command=cmd(report, 0))
        assert code(error) is ErrorCode.NOT_FOUND
    assert st.recorder.recorded == []


def test_a_report_the_store_still_serves_as_deleted_is_not_found() -> None:
    st = fresh()
    report = st.upload()
    st.store.reports[report.report_id] = replace(report, state="deleted")
    with pytest.raises(ProblemError) as error:
        st.workflows.get_report(session(), report.report_id)
    assert code(error) is ErrorCode.NOT_FOUND


def test_the_owner_reads_a_quarantined_report_with_its_state() -> None:
    st = fresh()
    report = st.upload(PDF[:-6])
    assert st.workflows.get_report(session(), report.report_id).state == "quarantined"


# ---- E09-03 manual structured entry (the backup path) -------------------------------------------------


def identity(key: str = "m1") -> CommandIdentity:
    return command_identity(
        key=key, actor_ref=OWNER, operation="enterFinding", target="/findings", parameters_json=b"{}",
        body_fingerprint="0" * 64, fingerprint_kind="sha256",
    )  # fmt: skip


def entry(**changes: object) -> SpecialistEntry:
    base = SpecialistEntry(
        "radiation_dose", OWNER, DAY,
        (("actual_quantity", "absorbed dose"), ("instrument", "badge"), ("location", "hab"), ("time_interval", "P1D")),
        (
            FieldEntry("result", Decimal("1.5"), "mGy", None, None, None),
            FieldEntry("interpretation", "within crew note", None, None, None, "clinician"),
        ),
        "device", "SIM",
    )  # fmt: skip
    return replace(base, **changes)  # type: ignore[arg-type]


def test_manual_entry_works_with_no_report_parser_or_model_and_keeps_text_out_of_records() -> None:
    st = fresh()
    findings = st.workflows.enter_finding(session(), identity(), entry())
    assert [f.role for f in findings] == ["result", "interpretation"]
    assert [f.record_ref is not None for f in findings] == [True, False]
    assert [r.finding_id for r in st.recorder.recorded] == [findings[0].finding_id]
    assert findings[0].unit == "mGy" and findings[0].origin == "device" and findings[1].origin == "clinician"
    assert st.lineage.applied_keys["m1"] == findings


def test_manual_entry_replay_is_idempotent() -> None:
    st = fresh()
    first = st.workflows.enter_finding(session(), identity(), entry())
    again = st.workflows.enter_finding(session(), identity(), entry())
    assert again == first and len(st.recorder.recorded) == 1


def test_manual_entry_refusals_record_nothing() -> None:
    st = fresh()
    bad_unit = entry(fields=(FieldEntry("result", Decimal(1), "uSv", None, None, None),))
    for who, subject, expected in (
        (session(), bad_unit, ErrorCode.INVALID_UNIT),
        (session(), entry(form_id="unknown_form"), ErrorCode.INVALID_VALUE),
        (session(OTHER), entry(), ErrorCode.NOT_FOUND),
        (ASSISTANT, entry(), ErrorCode.UNAUTHORIZED_SCOPE),
        (session(), entry(examined_on=None), ErrorCode.INVALID_VALUE),
    ):
        with pytest.raises(ProblemError) as error:
            st.workflows.enter_finding(who, identity("bad"), subject)
        assert code(error) is expected
    assert st.recorder.recorded == [] and st.lineage.applied_keys == {}


# ---- privacy: no private text in errors, reprs or hub metadata ------------------------------------------


def test_private_text_never_appears_in_errors_reprs_or_hub_state() -> None:
    canary = "CANARY-7731-private"
    st = fresh()
    report = st.upload(original_name=canary + ".pdf")
    hostile = DraftProposal(SourceSpan(1, 0, 10), "rhythm_interpretation", canary + " value", None, None)
    st.workflows.record_extraction(session(), st.lease(report), extraction(report, hostile))
    drafted_report = st.store.reports[report.report_id]
    command = cmd(drafted_report, 0)
    seen: list[ProblemError] = []

    def attempt(call: object) -> None:
        try:
            call()  # type: ignore[operator]
        except ProblemError as error:
            seen.append(error)

    attempt(
        lambda: st.workflows.confirm(OFFICER_OTHER, key="x1", report_id=report.report_id, command=command)
    )
    attempt(lambda: st.workflows.confirm(ASSISTANT, key="x2", report_id=report.report_id, command=command))
    attempt(
        lambda: st.workflows.confirm(
            session(),
            key="x3",
            report_id=report.report_id,
            command=replace(command, asserted_patient_id=None),
        )
    )
    attempt(
        lambda: st.workflows.confirm(
            session(),
            key="x4",
            report_id=report.report_id,
            command=replace(command, expected=ReportVersion(1, "v")),
        )
    )
    attempt(lambda: st.upload(PDF + canary.encode(), key="x5", original_name=canary + "\x00"))
    attempt(lambda: st.upload(canary.encode(), key="x6", original_name=canary))
    st.policies.policies["rhythm_interpretation"] = numeric("rhythm_interpretation", "mmol/L")
    attempt(lambda: st.workflows.confirm(session(), key="x7", report_id=report.report_id, command=command))
    assert len(seen) >= 6
    for error in seen:
        rendered = " ".join(
            [
                str(error),
                repr(error),
                repr(error.args),
                *(repr(f) for f in error.field_errors),
                repr(error.current_state),
            ]
        )
        assert canary not in rendered
    finding = decide_one(st, drafted_report)
    for item in (
        report,
        drafted_report,
        drafted_report.extraction_drafts[0],
        command,
        command.decisions[0],
        finding,
    ):
        assert canary not in repr(item)
    assert canary not in repr(st.factory.state)


def decide_one(st: Station, report: Report) -> ConfirmedFinding:
    st.policies.policies["rhythm_interpretation"] = text("rhythm_interpretation")
    st.workflows.confirm(session(), key="ok", report_id=report.report_id, command=cmd(report, 0))
    return st.lineage.applied_keys["ok"][0]


def test_a_key_reused_on_another_report_or_body_is_a_conflict_never_a_replay() -> None:
    st = fresh()
    first = drafted(st)
    st.workflows.confirm(session(), key="k", report_id=first.report_id, command=cmd(first, 0))
    other_upload = st.upload(key="up2", data=PDF + b"%second\n")
    st.workflows.record_extraction(session(), st.lease(other_upload), extraction(other_upload, *DEFAULT))
    other = st.store.reports[other_upload.report_id]
    with pytest.raises(ProblemError) as other_report:
        st.workflows.confirm(session(), key="k", report_id=other.report_id, command=cmd(other, 0))
    assert code(other_report) is ErrorCode.DUPLICATE_COMMAND
    after = st.store.reports[first.report_id]
    with pytest.raises(ProblemError) as other_body:
        st.workflows.confirm(session(), key="k", report_id=first.report_id, command=cmd(after, 1))
    assert code(other_body) is ErrorCode.DUPLICATE_COMMAND


def test_deleting_a_report_hides_it_and_a_late_extraction_finds_nothing() -> None:
    st = fresh()
    report = st.upload()
    lease = st.lease(report)
    st.workflows.delete_report(session(), key="del1", report_id=report.report_id)
    with pytest.raises(ProblemError) as late:
        st.workflows.record_extraction(session(), lease, extraction(report, *DEFAULT))
    assert code(late) is ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as read:
        st.workflows.get_report(session(), report.report_id)
    assert code(read) is ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as stranger:
        st.workflows.delete_report(session(OTHER), key="del2", report_id=report.report_id)
    assert code(stranger) in (ErrorCode.NOT_FOUND, ErrorCode.UNAUTHORIZED_SCOPE)


def test_a_retried_upload_with_any_other_body_field_conflicts_in_both_directions() -> None:
    narrow = fresh()
    first = narrow.upload(access_scope=None)
    for change in (
        {"access_scope": "self_and_ground"},
        {"report_date": "2026-10-02"},
        {"examination_date": "2026-10-01"},
        {"original_name": "other.pdf"},
    ):
        with pytest.raises(ProblemError) as clash:
            narrow.upload(**change)
        assert code(clash) is ErrorCode.DUPLICATE_COMMAND
    assert narrow.upload() == first
    broad = fresh()
    broad.upload(access_scope="self_and_ground")
    with pytest.raises(ProblemError) as narrowed:
        broad.upload(access_scope=None)
    assert code(narrowed) is ErrorCode.DUPLICATE_COMMAND
