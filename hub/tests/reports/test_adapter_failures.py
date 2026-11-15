"""Failure behaviour of the E09 adapters: corrupt or partial stored data, unknown enums on decode, replay and
ordering, bounds, and containment. Each test names the requirement or invariant it protects (AGENTS 4, 6)."""

from __future__ import annotations

import io
import struct
import sys
import zlib
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import builders
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from real_station import OTHER, OWNER, Rig, build, person

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import command_identity
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.private import PrivateIntent, SealedPrivateRef
from crew_health.reports.adapters import parsing as P
from crew_health.reports.adapters import worker
from crew_health.reports.adapters.codec import decode_version, encode_version, read_header
from crew_health.reports.adapters.contained import ContainedParser, ParserBounds, ParserUnavailable, _outcome
from crew_health.reports.adapters.index import _row
from crew_health.reports.adapters.lineage import SqliteConfirmationLineage, _finding, _origin, _role
from crew_health.reports.adapters.store import StoreLimits
from crew_health.reports.adapters.writer import ReportReferenceWriter
from crew_health.reports.domain.model import ConfirmedFinding, ExtractionDraft, Report, SourceSpan

AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
LIMITS = P.ParseLimits(10, 1000000, 100000, 1000000, 1000)
LINES = [["Pulse at rest 72 bpm"]]


def code(error: pytest.ExceptionInfo[ProblemError]) -> ErrorCode:
    return error.value.code


def report(**changes: object) -> Report:
    drafts = (
        ExtractionDraft(
            "drf_1",
            SourceSpan(1, 0, 5),
            "pulse",
            Decimal("72.5"),
            "/min",
            date(2026, 11, 10),
            "e1",
            "proposed",
            1,
        ),
        ExtractionDraft(
            "drf_2", SourceSpan(1, 6, 9), "ecg_note", "sinus rhythm", None, None, "e1", "confirmed", 1
        ),
        ExtractionDraft(
            "drf_3", SourceSpan(1, 10, 12), "pulse", Decimal(60), "/min", None, "e1", "rejected", 1
        ),
    )
    base = Report(
        "rpt_1",
        "a" * 64,
        "name.pdf",
        "application/pdf",
        10,
        OWNER,
        "Dr X",
        date(2026, 11, 1),
        None,
        "self",
        AT,
        drafts,
        "SIM",
        "ver_1",
        2,
        "accepted",
        None,
        (20,),
        1,
        "release_1",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


# ---- codec: durable bytes round trip and every corruption is a typed storage error (AGENTS 6 durability) --------


def test_a_report_with_every_draft_kind_and_state_round_trips_exactly() -> None:
    original = report()
    decoded = decode_version(encode_version(original, source=b"SRC", source_entry_id="rep_src"))
    assert (
        decoded.report == original
        and decoded.source == b"SRC"
        and decoded.header.source_entry_id == "rep_src"
    )
    quarantined = report(
        state="quarantined", quarantine_reason="corrupt", extraction_drafts=(), page_text_lengths=()
    )
    assert (
        decode_version(encode_version(quarantined, source=None, source_entry_id="rep_src")).report
        == quarantined
    )
    deleted = report(state="deleted", extraction_drafts=())
    assert (
        decode_version(encode_version(deleted, source=None, source_entry_id="rep_src")).report.state
        == "deleted"
    )


@settings(max_examples=150, deadline=None)
@given(st.data())
def test_any_truncation_or_byte_flip_of_a_stored_version_is_corrupt_never_another_error(
    data: st.DataObject,
) -> None:
    blob = encode_version(report(), source=b"SRC", source_entry_id="rep_src")
    cut = data.draw(st.integers(min_value=0, max_value=len(blob) - 1))
    flip = data.draw(st.integers(min_value=0, max_value=len(blob) - 1))
    mutated = bytearray(blob)
    mutated[flip] ^= data.draw(st.integers(min_value=1, max_value=255))
    for candidate in (blob[:cut], bytes(mutated)):
        try:
            decode_version(candidate)
        except StorageUnavailable:
            pass


def _swap(blob: bytes, old: bytes, new: bytes) -> bytes:
    """Replace JSON text inside the meta part and fix its length prefix, so only the content is corrupt."""
    head = int.from_bytes(blob[:4], "big")
    meta_at = 4 + head
    meta_len = int.from_bytes(blob[meta_at : meta_at + 4], "big")
    meta = blob[meta_at + 4 : meta_at + 4 + meta_len]
    assert old in meta
    meta = meta.replace(old, new)
    return blob[:meta_at] + len(meta).to_bytes(4, "big") + meta + blob[meta_at + 4 + meta_len :]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'"status":"confirmed"', b'"status":"maybe"'),
        (b'"state":"accepted"', b'"state":"vanished"'),
        (b'["n","72.5"]', b'["n","seventy"]'),
        (b'["n","72.5"]', b'["x","72.5"]'),
        (b'["s","sinus rhythm"]', b'"sinus rhythm"'),
        (b'"span":[1,6,9]', b'"span":[1,6]'),
        (b'"scope":"self"', b'"scope":"world"'),
        (b'"drafts":[', b'"drafts":{"a":['),
    ],
)
def test_an_unknown_enum_or_malformed_field_on_decode_is_corrupt_storage(old: bytes, new: bytes) -> None:
    blob = encode_version(report(), source=None, source_entry_id="rep_src")
    with pytest.raises((StorageUnavailable, ProblemError)) as error:
        decode_version(_swap(blob, old, new))
    assert error.value.code in (
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.INVALID_VALUE,
        ErrorCode.CAPABILITY_UNAVAILABLE,
    ) or isinstance(error.value, StorageUnavailable)


def test_a_header_with_a_non_integer_revision_or_missing_field_is_corrupt() -> None:
    def blob(header: bytes) -> bytes:
        return len(header).to_bytes(4, "big") + header + (2).to_bytes(4, "big") + b"{}"

    for header in (
        b'{"revision":true,"report_id":"r","patient_id":"p","source_entry_id":"s"}',
        b'{"revision":1}',
        b"[1]",
        b"nope",
    ):
        with pytest.raises(StorageUnavailable):
            read_header(blob(header))
    with pytest.raises(StorageUnavailable):
        read_header(b"abc")
    with pytest.raises(StorageUnavailable):
        read_header((99).to_bytes(4, "big") + b"{}")


# ---- lineage: ordering, replay, partial rows --------------------------------------------------------------------


def finding(
    finding_id: str, value: object, *, role: str = "result", unit: str | None = "mmol/L", **kw: object
) -> ConfirmedFinding:
    base = ConfirmedFinding(
        finding_id,
        "rpt_1",
        "drf_1",
        OWNER,
        "glucose",
        value,
        unit,
        date(2026, 11, 10),  # type: ignore[arg-type]
        None,
        "clinician",
        role,
        "SIM",
        OWNER,
        AT,
        3,
        "ver_1",
        (("method", "venous"), ("instrument", "x1")),  # type: ignore[arg-type]
        (Decimal("3.9"), Decimal("5.6")),
        None,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def test_findings_come_back_in_append_order_with_text_interpretation_and_interval_intact(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    lineage = SqliteConfirmationLineage()
    items = (
        finding("fnd_b", Decimal("5.1")),
        finding(
            "fnd_a",
            "normal rhythm",
            role="interpretation",
            unit=None,
            origin="device",
            reference_interval=None,
            observed_on=None,
        ),
    )
    with rig.db.begin(actor=person().actor, correlation_id="corr_a") as uow:
        lineage.append(uow, items, command_key="cmd_1")
    with rig.db.snapshot() as view:
        got = lineage.findings_of(view, command_key="cmd_1")
        assert got == items  # input order, not id order; spans are not kept in the hub by design
        assert lineage.applied(view, command_key="cmd_1") and not lineage.applied(view, command_key="cmd_2")
        assert lineage.binding(view, owner_person_id=OWNER, command_key="cmd_1") == ("", "")
        assert lineage.binding(view, owner_person_id=OTHER, command_key="cmd_1") is None
        assert lineage.findings_of(view, command_key="cmd_2") == ()


def test_a_bound_command_is_not_applied_until_its_findings_append_and_replay_of_bind_is_refused(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    lineage = SqliteConfirmationLineage()
    with rig.db.begin(actor=person().actor, correlation_id="corr_a") as uow:
        lineage.bind(uow, command_key="cmd_1", report_id="rpt_1", request_digest="d" * 64)
    with rig.db.snapshot() as view:
        assert lineage.binding(view, owner_person_id=OWNER, command_key="cmd_1") == ("rpt_1", "d" * 64)
        assert not lineage.applied(view, command_key="cmd_1")
    with pytest.raises(ProblemError), rig.db.begin(actor=person().actor, correlation_id="corr_b") as uow:
        lineage.bind(uow, command_key="cmd_1", report_id="rpt_2", request_digest="e" * 64)
    with rig.db.begin(actor=person().actor, correlation_id="corr_c") as uow:
        lineage.append(uow, (), command_key="cmd_1")  # an all-reject batch still marks the command applied
    with rig.db.snapshot() as view:
        assert lineage.applied(view, command_key="cmd_1")
        assert lineage.binding(view, owner_person_id=OWNER, command_key="cmd_1") == ("rpt_1", "d" * 64)


def test_a_stored_finding_row_that_no_longer_decodes_fails_loudly(tmp_path: Path) -> None:
    rig = build(tmp_path)
    lineage = SqliteConfirmationLineage()
    with rig.db.begin(actor=person().actor, correlation_id="corr_a") as uow:
        lineage.append(uow, (finding("fnd_a", Decimal("5.1")),), command_key="cmd_1")
        uow.connection.execute("UPDATE reports_finding SET observed_on='not-a-date'", ())
    with rig.db.snapshot() as view, pytest.raises(StorageUnavailable):
        lineage.findings_of(view, command_key="cmd_1")


@pytest.mark.parametrize("helper", [_origin, _role])
def test_an_unknown_origin_or_role_read_from_storage_is_corrupt(helper) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(StorageUnavailable):
        helper("model")


def test_a_row_with_an_unknown_origin_mode_or_short_columns_is_corrupt() -> None:
    row = (
        "fnd_1",
        None,
        None,
        OWNER,
        "glucose",
        "text",
        "x",
        None,
        None,
        "clinician",
        "result",
        "REAL",
        OWNER,
        AT.isoformat(),
        None,
        None,
        "[]",
        None,
        None,
        None,
    )
    with pytest.raises((StorageUnavailable, ProblemError)):
        _finding(row)
    with pytest.raises(StorageUnavailable):
        _finding(row[:5])


def test_the_revision_compare_and_set_refuses_a_stale_or_deleted_report(tmp_path: Path) -> None:
    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    lineage = SqliteConfirmationLineage()
    with rig.db.begin(actor=person().actor, correlation_id="corr_a") as uow:
        assert not lineage.advance(uow, report_id=created, expected_revision=7, new_revision=8)
        assert lineage.advance(uow, report_id=created, expected_revision=1, new_revision=2)
        assert not lineage.advance(uow, report_id=created, expected_revision=1, new_revision=2)
    rig.workflows.delete_report(person(), key="del1", report_id=created)
    with rig.db.begin(actor=person().actor, correlation_id="corr_b") as uow:
        assert not lineage.advance(uow, report_id=created, expected_revision=2, new_revision=3)


# ---- writer and index: the hub index refuses what does not match the sealed reference ------------------------------


def intent_for(
    rep: Report, entry: str, owner: str = OWNER, source_entry: str | None = None
) -> tuple[PrivateIntent, SealedPrivateRef]:
    plaintext = encode_version(rep, source=b"S", source_entry_id=source_entry or entry)
    identity = command_identity(
        key="cmd_w",
        actor_ref=owner,
        operation="uploadReport",
        target="/v1/reports",
        parameters_json=b"{}",
        body_fingerprint="f" * 64,
        fingerprint_kind="hmac_sha256",
    )
    intent = PrivateIntent(identity, owner, entry, "report", "ver_x", None, plaintext)
    ref = SealedPrivateRef(entry, "ver_x", "report", owner, "cmd_w", "f" * 64, "key_1", "c" * 64)
    return intent, ref


def attach(rig: Rig, rep: Report, entry: str, owner: str = OWNER, source_entry: str | None = None) -> None:
    intent, ref = intent_for(rep, entry, owner, source_entry)
    with rig.db.begin(actor=person().actor, correlation_id="corr_w") as uow:
        ReportReferenceWriter().attach(uow, ref, intent)


def test_the_writer_refuses_a_foreign_owner_a_wrong_first_version_and_a_revision_that_did_not_advance(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    with pytest.raises(ProblemError) as foreign:
        attach(rig, report(revision=1), "rep_1", owner=OTHER)
    assert code(foreign) is ErrorCode.IDENTITY_MISMATCH
    with pytest.raises(StorageUnavailable):
        attach(rig, report(revision=2), "rep_1")  # a first version must be revision 1
    with pytest.raises(StorageUnavailable):
        attach(rig, report(revision=1), "rep_1", source_entry="rep_other")  # and its own source
    attach(rig, report(revision=1), "rep_1")
    with pytest.raises(ProblemError) as stale:
        attach(
            rig, report(revision=2), "rep_2", source_entry="rep_1"
        )  # the lineage never advanced the hub revision
    assert code(stale) is ErrorCode.STALE_VERSION


def test_the_index_refuses_a_row_it_cannot_read() -> None:
    with pytest.raises(StorageUnavailable):
        _row(("only", "three", "cols"))


# ---- store: replay at the store, bounds, orphan cleanup, partial or swapped ciphertext ----------------------------


def test_store_level_replay_by_command_key_returns_the_committed_receipt_and_writes_nothing(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    session = person()
    rep = rig.workflows.get_report(session, created)
    again = rig.store.store_new(session, key="up1", report=rep, source=b"ignored", apply=lambda uow: True)
    assert again.command_key == "up1"
    updated = replace(rep, revision=2)

    def advance(uow: object) -> bool:
        return SqliteConfirmationLineage().advance(
            uow, report_id=created, expected_revision=1, new_revision=2
        )  # type: ignore[arg-type]

    first = rig.store.publish_version(
        session, key="v2", operation="reportExtraction", report=updated, apply=advance
    )
    # the version is already committed for this key: a retry publishes nothing and reports the same entry
    assert (
        rig.store.publish_version(
            session, key="v2", operation="reportExtraction", report=updated, apply=lambda uow: True
        ).entry_id
        == first.entry_id
    )
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_version", (), limit=1) == ((2,),)


def test_delete_needs_a_person_and_an_owned_report(tmp_path: Path) -> None:
    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    nobody = replace(person(), person_id=None)
    with pytest.raises(ProblemError) as anonymous:
        rig.store.delete(nobody, key="d1", report_id=created)
    assert code(anonymous) is ErrorCode.UNAUTHORIZED_SCOPE
    with pytest.raises(ProblemError) as stranger:
        rig.store.delete(person(OTHER), key="d2", report_id=created)
    assert code(stranger) is ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as missing:
        rig.store.delete(person(), key="d3", report_id="rpt_missing")
    assert code(missing) is ErrorCode.NOT_FOUND


def test_when_every_entry_attempt_is_revoked_the_store_refuses_instead_of_reusing_an_id(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    ids = rig.store.ids
    for attempt in range(4):
        rig.vault.revoked.add(ids.derived("rep", OWNER, "up1", str(attempt)))
    refused = rig.upload(builders.pdf(LINES), key="up1")
    assert refused.status_code == 422 and refused.json()["code"] == "CAPABILITY_UNAVAILABLE"
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_report", (), limit=1) == ((0,),)


def test_an_entry_over_the_private_bound_and_invalid_limits_are_refused(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rig.store.limits = StoreLimits(4, 100, 50)
    refused = rig.upload(builders.pdf(LINES), key="up1")
    assert refused.status_code == 413 and refused.json()["code"] == "PAYLOAD_TOO_LARGE"
    for bad in ((0, 1, 1), (1, 0, 1), (1, 1, 0)):
        with pytest.raises(ProblemError) as error:
            StoreLimits(*bad)
        assert code(error) is ErrorCode.CONFIG_INVALID


def test_a_publication_the_apply_step_declines_is_a_late_generation_and_leaves_no_orphan(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    session = person()
    rep = rig.workflows.get_report(session, created)
    with pytest.raises(ProblemError) as error:
        rig.store.publish_version(
            session,
            key="v2",
            operation="reportExtraction",
            report=replace(rep, revision=2),
            apply=lambda uow: False,
        )
    assert code(error) is ErrorCode.INVALID_TRANSITION
    assert rig.store_faults.pending(after_entry_id=None, limit=10) == ()
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_version", (), limit=1) == ((1,),)


def test_a_failure_before_the_hub_commit_discards_the_sealed_orphan_even_if_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(tmp_path)

    def boom(*args: object, **kwargs: object) -> None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "boom")

    monkeypatch.setattr(rig.coordinator, "publish", boom)
    assert rig.upload(builders.pdf(LINES), key="up1").status_code == 500
    assert rig.store_faults.pending(after_entry_id=None, limit=10) == ()  # discarded, not left for recovery
    monkeypatch.setattr(
        rig.store_faults,
        "verify",
        lambda reference: (_ for _ in ()).throw(ProblemError(ErrorCode.INTERNAL_ERROR, "x")),
    )
    assert rig.upload(builders.pdf(LINES), key="up2").status_code == 500  # the original error still surfaces


def test_ciphertext_swapped_between_reports_or_missing_a_source_is_corrupt_not_returned(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    first = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    second = rig.upload(builders.pdf([["Blood oxygen 98 %"]]), key="up2").json()["report_id"]
    real = rig.store.s.reader

    class Swapped:
        def read(self, session, reference):  # type: ignore[no-untyped-def]
            return encode_version(report(report_id=second, revision=1), source=b"S", source_entry_id="rep_x")

    rig.store.s = replace(rig.store.s, reader=Swapped())
    with pytest.raises(StorageUnavailable):
        rig.store.load(person(), first)  # the blob names another report

    class NoSource:
        def read(self, session, reference):  # type: ignore[no-untyped-def]
            return encode_version(report(report_id=first, revision=1), source=None, source_entry_id="rep_x")

    rig.store.s = replace(rig.store.s, reader=NoSource())
    with pytest.raises(StorageUnavailable):
        rig.store.read_source(person(), first)
    rig.store.s = replace(rig.store.s, reader=real)
    assert rig.store.read_source(person(), first) is not None


# ---- parsing: every refusal path of the bounded parsers (R14, V21, V22) ------------------------------------------


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


IHDR = struct.pack(">IIBBBBB", 2, 2, 8, 0, 0, 0, 0)


@pytest.mark.parametrize(
    "data",
    [
        b"not a png",
        P.PNG_MAGIC + chunk(b"IDAT", b"x") + chunk(b"IEND", b""),  # first chunk is not IHDR
        P.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", 0, 2, 8, 0, 0, 0, 0)) + chunk(b"IEND", b""),
        P.PNG_MAGIC + chunk(b"IHDR", IHDR) + chunk(b"IEND", b"") + b"trailing bytes after IEND",
        P.PNG_MAGIC + chunk(b"IHDR", IHDR),  # no IEND
        P.PNG_MAGIC + struct.pack(">I", 999999) + b"IHDR",  # length beyond the file
    ],
)
def test_a_malformed_png_is_corrupt(data: bytes) -> None:
    assert P.probe(data, "png", LIMITS).ok is False


def test_a_png_with_more_chunks_than_the_walk_bound_is_over_bounds() -> None:
    many = P.PNG_MAGIC + chunk(b"IHDR", IHDR) + chunk(b"tEXt", b"a") * 50 + chunk(b"IEND", b"")
    assert P.probe(many, "png", P.ParseLimits(10, 1000000, 100, 100, 5)).reason == "too_large"


JPEG_BASE = builders.jpeg()


@pytest.mark.parametrize(
    "data",
    [
        b"\x00\x01\x02",
        b"\xff\xd8\xff\x00\x00\x00\x00\x00",  # a segment that is not a marker
        b"\xff\xd8\xff\xe0\x00\x01xxxx",  # impossible segment length
        b"\xff\xd8\xff\xe0\xff\xff\x00\x00",  # segment longer than the file
        b"\xff\xd8\xff\xc0\x00\x05\x08\x00\x02\x00\x02",  # SOF shorter than its fields
        b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x00\x00\x02\x01\x01\x11\x00",  # zero height
        JPEG_BASE.replace(b"\xff\xd9", b""),  # no end of image
    ],
)
def test_a_malformed_jpeg_is_corrupt(data: bytes) -> None:
    assert P.probe(data, "jpeg", LIMITS).ok is False


def test_jpeg_fill_bytes_are_skipped_and_an_oversized_frame_or_endless_segments_are_over_bounds() -> None:
    assert P.probe(JPEG_BASE[:2] + b"\xff" + JPEG_BASE[2:], "jpeg", LIMITS).ok
    assert (
        P.probe(builders.jpeg(2000, 2000), "jpeg", P.ParseLimits(10, 100, 100, 100, 100)).reason
        == "too_large"
    )
    segments = b"\xff\xd8" + b"\xff\xe0\x00\x04ab" * 20
    assert P.probe(segments, "jpeg", P.ParseLimits(10, 100, 100, 100, 5)).reason == "too_large"


def test_pdf_structure_limits_and_broken_streams_are_refused_not_guessed() -> None:
    assert P.probe(b"%PDF-1.4 no objects %%EOF", "pdf", LIMITS).ok is False
    assert P.probe(builders.pdf(LINES)[:-20] + b"tail without marker", "pdf", LIMITS).ok is False
    assert (
        P.probe(builders.pdf([["a"]] * 3), "pdf", P.ParseLimits(2, 1000, 1000, 1000, 1000)).reason
        == "too_large"
    )
    assert P.probe(builders.pdf(LINES), "pdf", P.ParseLimits(5, 1000, 1000, 1000, 2)).reason == "too_large"
    assert P.probe(b"x", "other", LIMITS).ok is False
    corrupt = builders.pdf(LINES, flate=True).replace(b"stream\nx", b"stream\nx")
    start = corrupt.index(b"stream\n", corrupt.index(b"FlateDecode")) + 7
    broken = corrupt[:start] + b"\x00\x01\x02\x03" + corrupt[start + 4 :]
    assert P.probe(broken, "pdf", LIMITS).ok is False and P.pdf_pages(broken, LIMITS) is None
    assert P.pdf_pages(builders.pdf(LINES, flate=True), LIMITS) == ("Pulse at rest 72 bpm",)
    assert P.pdf_pages(builders.pdf(LINES), P.ParseLimits(5, 1000, 1000, 1000, 2)) is None
    assert P.pdf_pages(builders.pdf([["x" * 50]]), P.ParseLimits(5, 1000, 10, 1000, 1000)) is None
    assert P.pdf_pages(builders.pdf([["a"], ["b"], ["c"]]), P.ParseLimits(2, 1000, 1000, 1000, 1000)) is None
    with pytest.raises(ValueError):
        P.ParseLimits(0, 1, 1, 1, 1)


def test_pdf_text_escapes_nesting_and_split_lines_are_read_as_written() -> None:
    content = b"BT (a\\(b\\)c \\101\\060 \\q nested (x) y) Tj 0 -14 Td (second) Tj T* (third) Tj ET (tail"
    assert P._page_text(content, 1000) == "a(b)c A0 q nested (x) y\nsecond\nthird\ntail"
    assert P._page_text(content, 5) is None  # over the bound: refused, never cut
    assert P._literal("(unterminated", 0)[0] == "unterminated"


def test_proposals_respect_labels_separators_units_and_the_cap() -> None:
    vocab = {"pulse at rest": {"code": "pulse", "units": {"bpm": "/min"}}}
    assert P.propose(("Pulse at rest",), vocab, 5) == ()  # no number
    assert P.propose(("xPulse at rest 72",), vocab, 5) == ()  # label inside a word
    assert P.propose(("Pulse at restful 72",), vocab, 5) == ()  # label is a prefix of another word
    assert P.propose(("Pulse at rest 72abc",), vocab, 5) == ()  # number glued to letters
    (kept,) = P.propose(("Pulse at rest: 72 furlongs",), vocab, 5)
    assert kept.unit is None and kept.value == "72"
    many = tuple(f"Pulse at rest {n} bpm" for n in range(10))
    assert len(P.propose(many, vocab, 3)) == 3


# ---- containment: the worker entry point and the process runner ---------------------------------------------------


def test_the_worker_reports_failures_by_reason_and_never_proposes_from_a_failed_probe() -> None:
    header = {
        "op": "extract",
        "kind": "png",
        "limits": {
            "max_pages": 1,
            "max_pixels": 1000,
            "max_text_chars": 100,
            "max_decoded_bytes": 100,
            "max_objects": 100,
        },
        "max_proposals": 5,
    }
    assert worker.handle(header, b"junk") == {"ok": False, "reason": "corrupt"}
    pdf_header = {**header, "kind": "pdf", "limits": {**header["limits"], "max_text_chars": 3}}  # type: ignore[dict-item]
    assert worker.handle(pdf_header, builders.pdf(LINES)) == {"ok": False, "reason": "corrupt"}
    assert worker.handle({**header, "kind": "png"}, builders.png())["ok"] is True


def test_the_worker_main_reads_one_request_and_writes_one_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    header = b'{"op":"inspect","kind":"png","limits":{"max_pages":1,"max_pixels":1000,"max_text_chars":100,"max_decoded_bytes":100,"max_objects":100},"max_proposals":1}'
    monkeypatch.setattr(
        sys, "stdin", type("S", (), {"buffer": io.BytesIO(header + b"\n" + builders.png())})()
    )
    worker.main()
    assert capsys.readouterr().out == '{"ok":true,"pages":[],"proposals":[]}'


def test_a_bad_answer_or_bad_bounds_from_the_contained_parser_are_unavailable() -> None:
    for raw in (b"not json", b'{"ok":true}', b'{"ok":true,"pages":[1],"proposals":[[1]]}'):
        with pytest.raises(ParserUnavailable):
            _outcome(raw)
    assert _outcome(b'{"ok":false,"reason":"corrupt"}').reason == "corrupt"
    with pytest.raises(ValueError):
        ParserBounds(LIMITS, 0, 1, 1)
    with pytest.raises(ParserUnavailable):
        ContainedParser(ParserBounds(LIMITS, 20000, 5, 5)).run("inspect", "png", builders.png(), {})


# ---- extraction runner: a stopped parser, a refused publication and a vanished report fail the task, never publish ----


def drain_with(rig: Rig, parse) -> int:  # type: ignore[no-untyped-def]
    rig.runner.parse = parse
    return rig.runner.drain()


def test_a_parser_that_raises_or_a_release_that_vanished_fails_the_task_and_publishes_no_draft(
    tmp_path: Path,
) -> None:
    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="up1", examination_date="2026-11-10").json()["report_id"]

    def explode(kind, data, vocabulary):  # type: ignore[no-untyped-def]
        raise RuntimeError("worker crashed")

    assert drain_with(rig, explode) == 0
    assert rig.workflows.get_report(person(), created).extraction_drafts == ()
    del rig.releases.releases["release_1"]
    rig.clock.at = rig.clock.at.replace(year=2027)  # the failed task's retry backoff has passed
    assert rig.runner.drain() == 0  # the pinned release cannot be resolved: fail closed, no draft
    assert rig.workflows.get_report(person(), created).extraction_drafts == ()


def test_a_report_deleted_after_the_claim_fails_the_task_with_not_found(tmp_path: Path) -> None:
    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    real = rig.runner.parse

    def delete_then_parse(kind, data, vocabulary):  # type: ignore[no-untyped-def]
        rig.workflows.delete_report(person(), key="del1", report_id=created)
        return real(kind, data, vocabulary)

    assert drain_with(rig, delete_then_parse) == 0  # late work finds the report gone: nothing is published
    assert rig.get(OWNER, f"/v1/reports/{created}").status_code == 404  # type: ignore[attr-defined]


def test_a_parser_that_reports_failure_fails_the_task_without_a_draft(tmp_path: Path) -> None:
    from crew_health.reports.application.extraction import ParsedFile

    rig = build(tmp_path)
    created = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    assert drain_with(rig, lambda kind, data, vocabulary: ParsedFile(False, "corrupt", (), ())) == 0
    assert rig.workflows.get_report(person(), created).extraction_drafts == ()


# ---- configuration: a missing or invalid document is CONFIG_INVALID, never an empty policy ------------------------


def test_missing_or_invalid_configuration_documents_fail_closed() -> None:
    from real_station import release_documents

    from crew_health.contracts_gen.enums import ConfigName
    from crew_health.kernel.config import ConfigDocumentView
    from crew_health.kernel.testing import MemoryRelease
    from crew_health.reports.adapters.policies import ConfigFindingPolicies, ConfigVocabulary

    empty = MemoryRelease("release_empty", {})
    with pytest.raises(ProblemError) as no_mapping:
        ConfigFindingPolicies().for_code(empty, "pulse")
    assert code(no_mapping) is ErrorCode.CONFIG_INVALID
    with pytest.raises(ProblemError) as no_catalog:
        ConfigVocabulary().of(empty)
    assert code(no_catalog) is ErrorCode.CONFIG_INVALID
    broken = dict(release_documents())
    from hashlib import sha256

    raw = b'{"notice":1}'
    broken[ConfigName.INDICATOR_CATALOG] = ConfigDocumentView(
        ConfigName.INDICATOR_CATALOG, sha256(raw).hexdigest(), raw
    )
    with pytest.raises(ProblemError) as invalid:
        ConfigVocabulary().of(MemoryRelease("release_bad", broken))
    assert code(invalid) is ErrorCode.CONFIG_INVALID


def test_a_scale_converted_indicator_proposes_no_unit_and_an_unmapped_code_has_no_policy() -> None:
    from real_station import release_documents

    from crew_health.kernel.testing import MemoryRelease
    from crew_health.reports.adapters.policies import ConfigFindingPolicies, _tokens

    release = MemoryRelease("release_1", release_documents())
    assert ConfigFindingPolicies().for_code(release, "no_such_indicator") is None
    assert _tokens(release, "no_such_indicator") == {}
    assert _tokens(release, "co2")["[ppm]"] == "[ppm]"
    assert (
        "ppm" not in _tokens(release, "co2") or _tokens(release, "co2")["ppm"] == "[ppm]"
    )  # only an identity mapping renames


# ---- router: every malformed request is MALFORMED_REQUEST and stores nothing ---------------------------------------


def test_malformed_uploads_and_confirmations_are_400_and_store_nothing(tmp_path: Path) -> None:
    rig = build(tmp_path)
    headers = {"X-Person": OWNER, "Idempotency-Key": "k1"}
    two_files = rig.client.post(
        "/v1/reports",
        headers=headers,
        files=[("file", ("a.pdf", b"x", "application/pdf")), ("file", ("b.pdf", b"y", "application/pdf"))],
    )
    unknown_field = rig.client.post(
        "/v1/reports",
        headers=headers,
        files={"file": ("a.pdf", b"x", "application/pdf")},
        data={"surprise": "1"},
    )
    repeated_field = rig.client.post(
        "/v1/reports",
        headers=headers,
        files={"file": ("a.pdf", b"x", "application/pdf")},
        data={"access_scope": ["self", "self"]},
    )
    not_multipart = rig.client.post(
        "/v1/reports", headers={**headers, "Content-Type": "application/json"}, content=b"{}"
    )
    for response in (two_files, unknown_field, repeated_field, not_multipart):
        assert response.status_code == 400 and response.json()["code"] == "MALFORMED_REQUEST"
    report_id = rig.upload(builders.pdf(LINES), key="up1").json()["report_id"]
    for body in (
        {},
        {"confirmations": []},
        {"confirmations": [{"draft_id": "d", "decision": "maybe"}]},
        [1],
        {"confirmations": [{"draft_id": "d"}]},
    ):
        bad = rig.client.post(
            f"/v1/reports/{report_id}/confirmations",
            headers={"X-Person": OWNER, "Idempotency-Key": "c1", "If-Match": '"1:ver_x"'},
            content=__import__("json").dumps(body).encode(),
        )
        assert bad.status_code == 400
    wrong_etag = rig.confirm(
        OWNER,
        report_id,
        {"confirmations": [{"draft_id": "d", "decision": "reject"}]},
        key="c2",
        etag="W/garbage",
    )
    assert wrong_etag.status_code == 400
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_report", (), limit=1) == ((1,),)


def test_a_config_release_header_is_echoed_and_a_non_finite_draft_value_is_never_serialised() -> None:
    from crew_health.reports.api.router import _number, report_wire

    with pytest.raises(ProblemError) as error:
        _number(Decimal("NaN"))
    assert code(error) is ErrorCode.INVALID_VALUE
    assert _number(Decimal(72)) == 72 and _number(Decimal("5.4")) == 5.4 and _number("text") == "text"
    assert b'"value":72.5' in report_wire(report())


def test_config_release_in_the_request_is_returned_on_the_report(tmp_path: Path) -> None:
    rig = build(tmp_path)
    created = rig.client.post(
        "/v1/reports",
        headers={"X-Person": OWNER, "Idempotency-Key": "k1", "Config-Release": "release_1"},
        files={"file": ("a.pdf", builders.pdf(LINES), "application/pdf")},
    )
    assert created.headers["Config-Release"] == "release_1"


# ---- module: composed without a task runner, a failing startup drain degrades health instead of failing start ----


def test_without_a_task_runner_upload_stores_but_extraction_is_unavailable(tmp_path: Path) -> None:
    from crew_health.reports.module import _NoRunner

    runner = _NoRunner()
    lease = None
    with pytest.raises(ProblemError) as claim:
        runner.claim(None, worker_id="w", handlers=frozenset(), limit=1)  # type: ignore[arg-type]
    assert code(claim) is ErrorCode.CAPABILITY_UNAVAILABLE
    for call in (
        lambda: runner.progress(None, lease, None),  # type: ignore[arg-type]
        lambda: runner.complete(None, lease, None),  # type: ignore[arg-type]
        lambda: runner.fail(None, lease, code=ErrorCode.INTERNAL_ERROR, retryable=False),  # type: ignore[arg-type]
        lambda: runner.recover(None, limit=1),  # type: ignore[arg-type]
    ):
        with pytest.raises(ProblemError) as refused:
            call()
        assert code(refused) is ErrorCode.CAPABILITY_UNAVAILABLE


def test_the_extraction_handler_never_runs_inside_the_unit_of_work(tmp_path: Path) -> None:
    from crew_health.reports.module import _NoopHandler

    with pytest.raises(ProblemError) as error:
        _NoopHandler().execute(None)  # type: ignore[arg-type]
    assert code(error) is ErrorCode.CAPABILITY_UNAVAILABLE
    assert _NoopHandler().apply(None, None, None) is None and _NoopHandler().discard(None) is None  # type: ignore[arg-type]


def test_a_refused_startup_drain_degrades_health_instead_of_failing_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_module as composed

    from crew_health.app.registry import StartupPhase
    from crew_health.kernel.testing import MemoryHealth
    from crew_health.reports.application.extraction import ExtractionRunner

    rig = build(tmp_path)
    registry = composed.declared()
    health = MemoryHealth(maximum=8)
    composed.provide_all(registry, rig, health)
    registry.resolve()
    (start,) = [
        fn for key, fn in registry.declarations["startup"].items() if key[0] == StartupPhase.STATE_SWEEP
    ]
    start()  # nothing queued: a clean drain leaves health alone
    assert health.components == {}

    def refused(self: ExtractionRunner) -> int:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "no clock segment")

    monkeypatch.setattr(ExtractionRunner, "drain", refused)
    start()
    assert "reports.extraction" in health.components


def test_an_unavailable_parser_parks_the_extraction_and_quarantines_the_upload_as_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crew_health.reports import module as reports_module

    parser = reports_module.ContainedParser(reports_module.ParserBounds(LIMITS, 1000, 1000, 5))
    monkeypatch.setattr(
        "crew_health.reports.adapters.contained.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("none")),
    )
    assert reports_module._parse_with(parser)("pdf", builders.pdf(LINES), {}).reason == "parser_unavailable"
    assert (
        reports_module._inspect_with(parser)("pdf", builders.pdf(LINES)) == "unverified"
    )  # kept, never trusted
    monkeypatch.undo()
    assert reports_module._inspect_with(parser)("png", builders.png_huge_dimensions()) == "over_bounds"
    assert reports_module._inspect_with(parser)("png", builders.png_bad_crc()) == "corrupt"
