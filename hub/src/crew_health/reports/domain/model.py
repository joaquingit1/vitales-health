"""Report, ExtractionDraft and ConfirmedFinding (CONTRACTS 2; report.schema.json). Field names and enums are
copied from the schema; nullable stays nullable. The wire field `bytes` is `size_bytes` here only because it
would shadow the builtin. Private by construction: no repr of these types exposes name, hash or values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Day
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import require_id
from crew_health.reports.domain.errors import invalid_value

type AccessScope = Literal["self", "self_and_crew_medical_officer", "self_and_ground"]
type OriginMode = Literal["LIVE", "REPLAY", "SIM"]
type ReviewStatus = Literal["proposed", "confirmed", "rejected"]
type ReportState = Literal["accepted", "quarantined", "deleted"]
type FindingOrigin = Literal["clinician", "device", "extraction"]
type FindingRole = Literal["result", "interpretation"]
type FindingValue = Decimal | str

ACCESS_SCOPES: Final = frozenset({"self", "self_and_crew_medical_officer", "self_and_ground"})
ORIGIN_MODES: Final = frozenset({"LIVE", "REPLAY", "SIM"})
_SHA256 = re.compile(r"[a-f0-9]{64}")
_CONFIG_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def require_config_id(value: str) -> str:
    if not isinstance(value, str) or _CONFIG_ID.fullmatch(value) is None:
        raise invalid_value("config_id")
    return value


def access_scope_of(value: str | None) -> AccessScope:
    """No scope given means the narrowest, self (fail closed)."""
    if value is None or value == "self":
        return "self"
    if value == "self_and_crew_medical_officer":
        return "self_and_crew_medical_officer"
    if value == "self_and_ground":
        return "self_and_ground"
    raise invalid_value("access_scope")


def origin_mode_of(value: str) -> OriginMode:
    if value == "LIVE":
        return "LIVE"
    if value == "REPLAY":
        return "REPLAY"
    if value == "SIM":
        return "SIM"
    raise invalid_value("origin_mode")


def finite_number(value: object) -> Decimal:
    """A wire number as Decimal. Booleans, NaN and infinity are never numbers (AGENTS 1.5)."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise invalid_value("not_a_number")
    number = value if isinstance(value, Decimal) else Decimal(repr(value))
    if not number.is_finite():
        raise invalid_value("nonfinite")
    return number


def parse_source_date(text: str | None) -> Day | None:
    """A source date stays a date; unknown stays None. No midnight, no timezone is ever invented."""
    if text is None or text == "":
        return None
    if not isinstance(text, str) or _ISO_DATE.fullmatch(text) is None:
        raise invalid_value("date_format")
    try:
        return Day.fromisoformat(text)
    except ValueError as error:
        raise invalid_value("date_format") from error


@dataclass(frozen=True, slots=True)
class SourceSpan:
    page: int
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.page < 1 or self.start < 0 or self.end <= self.start:
            raise invalid_value("invalid_span")

    def within(self, page_text_lengths: tuple[int, ...]) -> bool:
        return self.page <= len(page_text_lengths) and self.end <= page_text_lengths[self.page - 1]


@dataclass(frozen=True, slots=True, repr=False)
class ExtractionDraft:
    draft_id: str
    source_span: SourceSpan
    code: str
    value: FindingValue
    unit: str | None
    date: Day | None
    extraction_version: str
    review_status: ReviewStatus
    generation: int

    def __post_init__(self) -> None:
        require_id(self.draft_id)
        require_config_id(self.code)
        if not self.extraction_version or self.unit == "" or self.generation < 1:
            raise invalid_value("draft_invalid")
        if isinstance(self.value, Decimal):
            finite_number(self.value)
        elif not isinstance(self.value, str) or self.value == "":
            raise invalid_value("draft_value")

    def __repr__(self) -> str:
        return f"ExtractionDraft({self.draft_id}, {self.review_status})"


@dataclass(frozen=True, slots=True, repr=False)
class Report:
    report_id: str
    file_sha256: str
    original_name: str
    media_type: str
    size_bytes: int
    patient_id: str
    author: str | None
    examination_date: Day | None
    report_date: Day | None
    access_scope: AccessScope
    uploaded_at: datetime
    extraction_drafts: tuple[ExtractionDraft, ...]
    origin_mode: OriginMode
    source_version_id: str
    revision: int
    state: ReportState
    quarantine_reason: str | None
    page_text_lengths: tuple[int, ...]
    extraction_generation: int | None
    extraction_release_id: str | None = None

    def __post_init__(self) -> None:
        require_id(self.report_id)
        require_id(self.patient_id)
        require_id(self.source_version_id)
        if self.extraction_release_id is not None:
            require_id(self.extraction_release_id)
        require_utc(self.uploaded_at)
        if _SHA256.fullmatch(self.file_sha256) is None:
            raise invalid_value("sha256")
        if (
            not self.original_name
            or not self.media_type
            or self.size_bytes < 1
            or self.revision < 1
            or self.author == ""
            or self.access_scope not in ACCESS_SCOPES
            or self.origin_mode not in ORIGIN_MODES
            or any(length < 1 for length in self.page_text_lengths)
        ):
            raise invalid_value("report_invalid")
        if (self.state == "quarantined") != (self.quarantine_reason is not None):
            raise invalid_value("quarantine_state")
        if self.state != "accepted" and self.extraction_drafts:
            raise invalid_value("no_drafts_outside_accepted")

    def __repr__(self) -> str:
        return f"Report({self.report_id}, r{self.revision}, {self.state})"


@dataclass(frozen=True, slots=True, repr=False)
class ConfirmedFinding:
    """A human-confirmed fact. The confirmer is never the interpreter: `origin` says who produced the value."""

    finding_id: str
    report_id: str | None
    draft_id: str | None
    patient_id: str
    code: str
    value: FindingValue
    unit: str | None
    observed_on: Day | None
    source_span: SourceSpan | None
    origin: FindingOrigin
    role: FindingRole
    origin_mode: OriginMode
    confirmer_id: str
    confirmed_at: datetime
    report_revision: int | None
    source_version_id: str | None
    attributes: tuple[tuple[str, str], ...]
    reference_interval: tuple[Decimal, Decimal] | None
    record_ref: str | None

    def __post_init__(self) -> None:
        require_id(self.finding_id)
        require_id(self.patient_id)
        require_id(self.confirmer_id)
        require_config_id(self.code)
        require_utc(self.confirmed_at)
        if isinstance(self.value, Decimal):
            finite_number(self.value)
            if not self.unit:
                raise invalid_value("unit_required")
        elif not self.value or self.unit is not None:
            raise invalid_value("text_finding")
        if self.role == "interpretation" and isinstance(self.value, Decimal):
            raise invalid_value("interpretation_text")

    def __repr__(self) -> str:
        return f"ConfirmedFinding({self.finding_id}, {self.role}, {self.origin})"
