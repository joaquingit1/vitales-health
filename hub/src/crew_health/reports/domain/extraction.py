"""E09-02 extraction publication: what a contained parser may propose and when a generation may publish.

A proposal is data. Text found in a source (including text that reads like an instruction) is stored as an
inert string and never interpreted. A late, cancelled or older generation cannot publish, and a deleted or
quarantined report accepts no draft (D17, D18)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date as Day
from decimal import Decimal

from crew_health.kernel.ids import IdFactory, require_id
from crew_health.reports.domain.errors import invalid_transition, invalid_value, not_found, stale_version
from crew_health.reports.domain.model import (
    ExtractionDraft,
    FindingValue,
    Report,
    SourceSpan,
    finite_number,
    require_config_id,
)


@dataclass(frozen=True, slots=True)
class ReportLimits:
    """Bounds the assembly takes from sourced settings (P6). No default exists in code."""

    max_drafts: int
    max_value_chars: int

    def __post_init__(self) -> None:
        if self.max_drafts < 1 or self.max_value_chars < 1:
            raise invalid_value("limits_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class DraftProposal:
    source_span: SourceSpan
    code: str
    value: FindingValue
    unit: str | None
    date: Day | None

    def __repr__(self) -> str:
        return "DraftProposal()"


@dataclass(frozen=True, slots=True, repr=False)
class ExtractionResult:
    source_version_id: str
    engine_version: str
    page_text_lengths: tuple[int, ...]
    proposals: tuple[DraftProposal, ...]

    def __repr__(self) -> str:
        return "ExtractionResult()"


def pin_ref(report_id: str, source_version_id: str) -> str:
    """The opaque task input reference: ids only, no private content (SYSTEM 5.10.1)."""
    return require_id(report_id) + ":" + require_id(source_version_id)


def parse_pin(input_ref: str) -> tuple[str, str]:
    report_id, separator, version = input_ref.partition(":")
    if not separator:
        raise invalid_value("pin_invalid")
    return require_id(report_id), require_id(version)


def _check_proposal(proposal: DraftProposal, lengths: tuple[int, ...], limits: ReportLimits) -> None:
    require_config_id(proposal.code)
    if not proposal.source_span.within(lengths):
        raise invalid_value("invalid_span")
    if isinstance(proposal.value, Decimal):
        finite_number(proposal.value)
    elif (
        not isinstance(proposal.value, str)
        or not proposal.value
        or len(proposal.value) > limits.max_value_chars
    ):
        raise invalid_value("draft_value")


def publish_extraction(
    report: Report,
    result: ExtractionResult,
    *,
    generation: int,
    ids: IdFactory,
    limits: ReportLimits,
) -> Report:
    """The report with its new proposed drafts and a new revision. Confirmed and rejected drafts are kept."""
    if report.state == "deleted":
        raise not_found()
    if report.state != "accepted":
        raise invalid_transition("not_accepted")
    if result.source_version_id != report.source_version_id:
        raise stale_version()
    if report.extraction_generation is not None and generation <= report.extraction_generation:
        raise invalid_transition("late_generation")
    lengths = report.page_text_lengths or result.page_text_lengths
    if report.page_text_lengths and report.page_text_lengths != result.page_text_lengths:
        raise stale_version()
    if not result.engine_version or len(result.proposals) > limits.max_drafts:
        raise invalid_value("result_invalid")
    for proposal in result.proposals:
        _check_proposal(proposal, lengths, limits)
    kept = tuple(d for d in report.extraction_drafts if d.review_status != "proposed")
    fresh = tuple(
        ExtractionDraft(
            ids.derived("drf", report.report_id, report.source_version_id, str(generation), str(index)),
            p.source_span,
            p.code,
            p.value,
            p.unit,
            p.date,
            result.engine_version,
            "proposed",
            generation,
        )
        for index, p in enumerate(result.proposals)
    )
    if len(kept) + len(fresh) > limits.max_drafts:
        raise invalid_value("result_invalid")
    return replace(
        report,
        extraction_drafts=kept + fresh,
        page_text_lengths=lengths,
        extraction_generation=generation,
        revision=report.revision + 1,
    )
