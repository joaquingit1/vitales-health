"""T-E09-01 R14: report fields keep schema names, nullability and whole enums; dates stay dates."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.reports.domain.model import (
    ACCESS_SCOPES,
    ORIGIN_MODES,
    ConfirmedFinding,
    ExtractionDraft,
    Report,
    SourceSpan,
    access_scope_of,
    finite_number,
    origin_mode_of,
    parse_source_date,
)

from .reports_harness import AT

pytestmark = pytest.mark.trace("T-E09-01", "R14", "E09-01")

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "docs/product/api/schemas/report.schema.json").read_text(
        encoding="utf-8"
    )
)
SHA = "9c04133fc4618097e92486bfa8f883f8a8938fabf697518ad3a9f82675ec6cff"


def report(**changes: object) -> Report:
    base = Report(
        "rpt_1", SHA, "name.pdf", "application/pdf", 10, "mara", None, None, None, "self", AT, (),
        "SIM", "ver_1", 1, "accepted", None, (), None,
    )  # fmt: skip
    return replace(base, **changes)  # type: ignore[arg-type]


def test_enums_are_the_whole_schema_enums() -> None:
    assert ACCESS_SCOPES == set(SCHEMA["properties"]["access_scope"]["enum"])
    assert ORIGIN_MODES == {"LIVE", "REPLAY", "SIM"}
    drafts = SCHEMA["properties"]["extraction_drafts"]["items"]["properties"]
    assert set(drafts["review_status"]["enum"]) == {"proposed", "confirmed", "rejected"}
    for scope in ACCESS_SCOPES:
        assert access_scope_of(scope) == scope
    for mode in ORIGIN_MODES:
        assert origin_mode_of(mode) == mode


def test_every_schema_field_has_a_domain_field_and_nullable_ones_accept_none() -> None:
    names = set(Report.__dataclass_fields__)
    wire = set(SCHEMA["properties"]) - {"bytes"}
    assert wire <= names and "size_bytes" in names
    nullable = {k for k, v in SCHEMA["properties"].items() if "anyOf" in v}
    assert nullable == {"author", "examination_date", "report_date"}
    value = report(author=None, examination_date=None, report_date=None)
    assert (value.author, value.examination_date, value.report_date) == (None, None, None)
    draft_fields = set(ExtractionDraft.__dataclass_fields__)
    assert set(SCHEMA["properties"]["extraction_drafts"]["items"]["properties"]) <= draft_fields


def test_missing_access_scope_is_the_narrowest_and_unknown_is_refused() -> None:
    assert access_scope_of(None) == "self"
    for bad in ("everyone", "ground", "SELF"):
        with pytest.raises(ProblemError):
            access_scope_of(bad)
    with pytest.raises(ProblemError):
        origin_mode_of("live")


@pytest.mark.parametrize("text", ["2026-09-30", "2000-02-29"])
def test_a_date_only_source_date_stays_a_date(text: str) -> None:
    parsed = parse_source_date(text)
    assert type(parsed) is date and parsed.isoformat() == text


@pytest.mark.parametrize(
    "text",
    ["2026-09-30T00:00:00Z", "2026-09-30T00:00", "20260930", "2026-9-3", "2026-13-01", "2026-02-30", "x"],
)
def test_a_datetime_or_malformed_date_is_refused_never_given_a_midnight(text: str) -> None:
    with pytest.raises(ProblemError) as error:
        parse_source_date(text)
    assert error.value.code is ErrorCode.INVALID_VALUE


def test_unknown_date_stays_none() -> None:
    assert parse_source_date(None) is None
    assert parse_source_date("") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"file_sha256": "ABC"},
        {"size_bytes": 0},
        {"original_name": ""},
        {"revision": 0},
        {"author": ""},
        {"uploaded_at": datetime(2026, 1, 1)},
        {"state": "quarantined"},
        {"quarantine_reason": "truncated"},
        {"page_text_lengths": (0,)},
    ],
)
def test_invalid_reports_raise_typed_errors(changes: dict[str, object]) -> None:
    with pytest.raises(ProblemError):
        report(**changes)


def test_quarantined_report_carries_a_reason_and_no_drafts() -> None:
    value = report(state="quarantined", quarantine_reason="truncated")
    assert value.state == "quarantined"
    draft = ExtractionDraft("drf_1", SourceSpan(1, 0, 3), "code", "x", None, None, "v1", "proposed", 1)
    with pytest.raises(ProblemError):
        report(state="quarantined", quarantine_reason="truncated", extraction_drafts=(draft,))


@pytest.mark.parametrize(("page", "start", "end"), [(0, 0, 1), (1, -1, 2), (1, 5, 5), (1, 6, 5)])
def test_invalid_spans_are_refused(page: int, start: int, end: int) -> None:
    with pytest.raises(ProblemError):
        SourceSpan(page, start, end)


def test_span_must_lie_inside_the_page_text() -> None:
    span = SourceSpan(2, 10, 20)
    assert (
        span.within((50, 20)) and not span.within((50, 19)) and not span.within((50,)) and not span.within(())
    )


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), True, "3", None, Decimal("NaN"), Decimal("Infinity")]
)
def test_nonfinite_and_non_numbers_are_never_numbers(bad: object) -> None:
    with pytest.raises(ProblemError):
        finite_number(bad)


@given(st.one_of(st.integers(-(10**9), 10**9), st.floats(allow_nan=False, allow_infinity=False)))
def test_finite_numbers_pass_and_stay_finite(value: float) -> None:
    assert finite_number(value).is_finite()


def test_finding_types_keep_numbers_and_text_apart() -> None:
    base = dict(
        finding_id="fnd_1", report_id=None, draft_id=None, patient_id="mara", code="ecg_report",
        value=Decimal(1), unit="mm[Hg]", observed_on=None, source_span=None, origin="clinician",
        role="result", origin_mode="SIM", confirmer_id="mara", confirmed_at=AT, report_revision=None,
        source_version_id=None, attributes=(), reference_interval=None, record_ref=None,
    )  # fmt: skip
    ConfirmedFinding(**base)  # type: ignore[arg-type]
    for changes in (
        {"unit": None},
        {"role": "interpretation"},
        {"value": "text"},
        {"value": ""},
        {"value": Decimal("NaN")},
    ):
        with pytest.raises(ProblemError):
            ConfirmedFinding(**{**base, **changes})  # type: ignore[arg-type]
    text = ConfirmedFinding(**{**base, "value": "sinus rhythm", "unit": None, "role": "interpretation"})  # type: ignore[arg-type]
    assert text.unit is None


def test_utc_is_required_for_instants() -> None:
    assert report().uploaded_at.tzinfo is UTC


def test_invalid_drafts_and_codes_are_refused_and_reprs_hold_no_values() -> None:
    from crew_health.reports.domain.model import require_config_id

    span = SourceSpan(1, 0, 3)
    for changes in (
        {"generation": 0},
        {"unit": ""},
        {"extraction_version": ""},
        {"value": ""},
        {"code": "Bad Code"},
    ):
        args = dict(
            draft_id="drf_1", source_span=span, code="code", value="x", unit=None, date=None,
            extraction_version="v1", review_status="proposed", generation=1,
        )  # fmt: skip
        with pytest.raises(ProblemError):
            ExtractionDraft(**{**args, **changes})  # type: ignore[arg-type]
    with pytest.raises(ProblemError):
        require_config_id("Not Valid")
    secret = ExtractionDraft("drf_1", span, "code", "SECRET-VALUE", None, None, "v1", "proposed", 1)
    assert "SECRET-VALUE" not in repr(secret) and "SECRET-VALUE" not in repr(
        report(extraction_drafts=(secret,))
    )
