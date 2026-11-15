"""The private plaintext of one report version and the hub-visible header of it (E09, SYSTEM 5.8.1).

Layout: 4-byte big-endian header length, header JSON (opaque ids only, read by the reference writer inside the
hub transaction), 4-byte meta length, meta JSON (every private field of the report), then the source bytes (the
first version only). Everything is bounded by the caller's byte limit before parsing. Nothing here logs or
formats private text, and a malformed envelope is a typed corrupt-storage error, never a guess."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as Day
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.ids import require_id
from crew_health.reports.domain.model import (
    ExtractionDraft,
    FindingValue,
    Report,
    ReportState,
    ReviewStatus,
    SourceSpan,
    access_scope_of,
    origin_mode_of,
)

_LENGTH_BYTES = 4


@dataclass(frozen=True, slots=True)
class VersionHeader:
    report_id: str
    revision: int
    patient_id: str
    source_entry_id: str


@dataclass(frozen=True, slots=True)
class DecodedVersion:
    header: VersionHeader
    report: Report
    source: bytes | None


def _corrupt() -> StorageUnavailable:
    return StorageUnavailable("corrupt")


def _value_out(value: FindingValue) -> list[str]:
    return ["n", str(value)] if isinstance(value, Decimal) else ["s", value]


def _value_in(raw: object) -> FindingValue:
    if not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[1], str):
        raise _corrupt()
    if raw[0] == "s":
        return raw[1]
    if raw[0] == "n":
        try:
            return Decimal(raw[1])
        except InvalidOperation as error:
            raise _corrupt() from error
    raise _corrupt()


def _day(raw: object) -> Day | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _corrupt()
    return Day.fromisoformat(raw)


def _review(raw: object) -> ReviewStatus:
    if raw == "proposed":
        return "proposed"
    if raw == "confirmed":
        return "confirmed"
    if raw == "rejected":
        return "rejected"
    raise _corrupt()


def _draft_out(d: ExtractionDraft) -> dict[str, object]:
    return {
        "id": d.draft_id,
        "span": [d.source_span.page, d.source_span.start, d.source_span.end],
        "code": d.code,
        "value": _value_out(d.value),
        "unit": d.unit,
        "date": None if d.date is None else d.date.isoformat(),
        "version": d.extraction_version,
        "status": d.review_status,
        "generation": d.generation,
    }


def _draft_in(raw: object) -> ExtractionDraft:
    if not isinstance(raw, dict):
        raise _corrupt()
    span = raw["span"]
    unit = raw["unit"]
    if not isinstance(span, list) or len(span) != 3:
        raise _corrupt()
    return ExtractionDraft(
        str(raw["id"]),
        SourceSpan(int(span[0]), int(span[1]), int(span[2])),
        str(raw["code"]),
        _value_in(raw["value"]),
        None if unit is None else str(unit),
        _day(raw["date"]),
        str(raw["version"]),
        _review(raw["status"]),
        int(raw["generation"]),
    )


def encode_version(report: Report, *, source: bytes | None, source_entry_id: str) -> bytes:
    header = canonical_json(
        {
            "report_id": report.report_id,
            "revision": report.revision,
            "patient_id": report.patient_id,
            "source_entry_id": source_entry_id,
        }
    )
    meta = canonical_json(
        {
            "sha256": report.file_sha256,
            "name": report.original_name,
            "media_type": report.media_type,
            "size": report.size_bytes,
            "author": report.author,
            "examination_date": None
            if report.examination_date is None
            else report.examination_date.isoformat(),
            "report_date": None if report.report_date is None else report.report_date.isoformat(),
            "scope": report.access_scope,
            "uploaded_at": report.uploaded_at.isoformat(),
            "drafts": [_draft_out(d) for d in report.extraction_drafts],
            "origin_mode": report.origin_mode,
            "source_version_id": report.source_version_id,
            "state": report.state,
            "quarantine_reason": report.quarantine_reason,
            "page_text_lengths": list(report.page_text_lengths),
            "generation": report.extraction_generation,
            "release_id": report.extraction_release_id,
        }
    )
    return (
        len(header).to_bytes(_LENGTH_BYTES, "big")
        + header
        + len(meta).to_bytes(_LENGTH_BYTES, "big")
        + meta
        + (source or b"")
    )


def _split(blob: bytes) -> tuple[bytes, bytes, bytes]:
    if len(blob) < 2 * _LENGTH_BYTES:
        raise _corrupt()
    head = int.from_bytes(blob[:_LENGTH_BYTES], "big")
    start = _LENGTH_BYTES + head
    if start + _LENGTH_BYTES > len(blob):
        raise _corrupt()
    meta = int.from_bytes(blob[start : start + _LENGTH_BYTES], "big")
    body = start + _LENGTH_BYTES
    if body + meta > len(blob):
        raise _corrupt()
    return blob[_LENGTH_BYTES:start], blob[body : body + meta], blob[body + meta :]


def _object(raw: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise _corrupt() from error
    if not isinstance(value, dict):
        raise _corrupt()
    return cast(dict[str, object], value)


def read_header(blob: bytes) -> VersionHeader:
    """Only the opaque ids. This is what the hub-side writer reads inside its transaction."""
    raw = _object(_split(blob)[0])
    try:
        revision = raw["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise _corrupt()
        return VersionHeader(
            require_id(str(raw["report_id"])),
            revision,
            require_id(str(raw["patient_id"])),
            require_id(str(raw["source_entry_id"])),
        )
    except (KeyError, ProblemError) as error:
        raise _corrupt() from error


def decode_version(blob: bytes) -> DecodedVersion:
    head_raw, meta_raw, source = _split(blob)
    header = read_header(blob)
    meta = _object(meta_raw)
    try:
        drafts = meta["drafts"]
        lengths = meta["page_text_lengths"]
        if not isinstance(drafts, list) or not isinstance(lengths, list):
            raise _corrupt()
        generation = meta["generation"]
        author = meta["author"]
        reason = meta["quarantine_reason"]
        release_id = meta["release_id"]
        report = Report(
            header.report_id,
            str(meta["sha256"]),
            str(meta["name"]),
            str(meta["media_type"]),
            int(cast(int, meta["size"])),
            header.patient_id,
            None if author is None else str(author),
            _day(meta["examination_date"]),
            _day(meta["report_date"]),
            access_scope_of(str(meta["scope"])),
            datetime.fromisoformat(str(meta["uploaded_at"])),
            tuple(_draft_in(d) for d in drafts),
            origin_mode_of(str(meta["origin_mode"])),
            str(meta["source_version_id"]),
            header.revision,
            _state(meta["state"]),
            None if reason is None else str(reason),
            tuple(int(cast(int, n)) for n in lengths),
            None if generation is None else int(cast(int, generation)),
            None if release_id is None else str(release_id),
        )
    except (KeyError, ValueError, TypeError, ProblemError) as error:
        if isinstance(error, StorageUnavailable):
            raise
        raise _corrupt() from error
    return DecodedVersion(header, report, source or None)


def _state(raw: object) -> ReportState:
    if raw == "accepted":
        return "accepted"
    if raw == "quarantined":
        return "quarantined"
    if raw == "deleted":
        return "deleted"
    raise _corrupt()
