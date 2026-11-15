"""E09-02 the extraction confirmation state machine (R14, R16, V21, V15).

proposed -> confirmed | rejected, nothing else. Confirmed and rejected are terminal: a reject stays rejected and
a confirmed draft cannot be confirmed again. Only a person's explicit confirmation yields a ConfirmedFinding;
an unconfirmed or rejected draft is never a fact. The batch is atomic: the first failure raises and no subset
is accepted. The ETag (report revision and source version) is compared before anything is decided; the same
check runs again under the private barrier in the application."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date as Day
from datetime import datetime
from typing import Literal

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import UnitRegistry
from crew_health.reports.domain.errors import (
    ReportError,
    identity_mismatch,
    invalid_transition,
    invalid_unit,
    invalid_value,
    not_found,
    stale_version,
)
from crew_health.reports.domain.extraction import ReportLimits
from crew_health.reports.domain.findings import FindingPolicy, check_unit, check_value
from crew_health.reports.domain.model import (
    ConfirmedFinding,
    ExtractionDraft,
    FindingValue,
    Report,
    ReviewStatus,
)


@dataclass(frozen=True, slots=True)
class ReportVersion:
    """The If-Match precondition: report revision and source version (ETag shape settled by P2)."""

    revision: int
    source_version_id: str


@dataclass(frozen=True, slots=True, repr=False)
class Decision:
    draft_id: str
    decision: Literal["confirm", "reject"]  # internal enum, copied from requests.schema.json
    value: object | None
    unit: str | None
    date: Day | None  # correction field: not yet in the public schema (P2)

    def __repr__(self) -> str:
        return f"Decision({self.draft_id}, {self.decision})"


@dataclass(frozen=True, slots=True, repr=False)
class ConfirmationCommand:
    expected: ReportVersion | None
    asserted_patient_id: str | None  # explicit identity acknowledgment: not yet in the public schema (P2)
    decisions: tuple[Decision, ...]
    confirmer_id: str
    confirmed_at: datetime

    def __repr__(self) -> str:
        return "ConfirmationCommand()"


@dataclass(frozen=True, slots=True)
class ConfirmationOutcome:
    report: Report
    findings: tuple[ConfirmedFinding, ...]


_TERMINAL: frozenset[ReviewStatus] = frozenset({"confirmed", "rejected"})


def check_preconditions(report: Report, command: ConfirmationCommand, limits: ReportLimits) -> None:
    if report.state == "deleted":
        raise not_found()
    if report.state != "accepted":
        raise invalid_transition("not_accepted")
    if command.expected is None:
        raise ReportError(ErrorCode.VERSION_REQUIRED, "version_required")
    if command.expected != ReportVersion(report.revision, report.source_version_id):
        raise stale_version()
    if command.asserted_patient_id != report.patient_id or command.confirmer_id != report.patient_id:
        raise identity_mismatch("patient_not_acknowledged")
    ids = [d.draft_id for d in command.decisions]
    if not ids or len(ids) > limits.max_drafts or len(set(ids)) != len(ids):
        raise invalid_value("decisions_invalid")


def _effective(draft: ExtractionDraft, decision: Decision) -> tuple[object, str | None, Day | None]:
    value = draft.value if decision.value is None else decision.value
    return value, draft.unit if decision.unit is None else decision.unit, decision.date or draft.date


def _numeric_unit_ok(draft: ExtractionDraft, unit: str | None, units: UnitRegistry) -> None:
    """An edited unit must stay in the dimension of the extracted one; the registry decides."""
    if draft.unit is None or unit is None or draft.unit == unit:
        return
    try:
        compatible = units.commensurable(units.parse(draft.unit), units.parse(unit))
    except ProblemError as error:
        raise invalid_unit("unit_unknown") from error
    if not compatible:
        raise invalid_unit("unit_incompatible")


def _confirm_one(
    report: Report,
    draft: ExtractionDraft,
    decision: Decision,
    command: ConfirmationCommand,
    policy: FindingPolicy | None,
    units: UnitRegistry,
    ids: IdFactory,
) -> ConfirmedFinding:
    if policy is None:
        raise invalid_value("code_unknown", field=draft.draft_id)
    if not draft.source_span.within(report.page_text_lengths):
        raise invalid_value("invalid_span", field=draft.draft_id)
    raw, unit, observed = _effective(draft, decision)
    value: FindingValue = check_value(raw, policy)
    parsed = check_unit(unit, policy, units)
    if policy.kind == "numeric":
        _numeric_unit_ok(draft, unit, units)
        if observed is None:
            raise invalid_value("date_required", field=draft.draft_id)
    return ConfirmedFinding(
        ids.derived("fnd", report.report_id, draft.draft_id, str(report.revision)),
        report.report_id,
        draft.draft_id,
        report.patient_id,
        draft.code,
        value,
        None if parsed is None else parsed.code,
        observed,
        draft.source_span,
        "extraction",
        "result",
        report.origin_mode,
        command.confirmer_id,
        command.confirmed_at,
        report.revision + 1,
        report.source_version_id,
        (("extraction_version", draft.extraction_version),),
        None,
        None,
    )


def decide(
    report: Report,
    command: ConfirmationCommand,
    *,
    policies: Callable[[str], FindingPolicy | None],
    units: UnitRegistry,
    ids: IdFactory,
    limits: ReportLimits,
) -> ConfirmationOutcome:
    """The next report revision and the findings a person confirmed. Raises on the first failure."""
    check_preconditions(report, command, limits)
    by_id = {d.draft_id: d for d in report.extraction_drafts}
    status: dict[str, ReviewStatus] = {}
    findings: list[ConfirmedFinding] = []
    for decision in command.decisions:
        draft = by_id.get(decision.draft_id)
        if draft is None:
            raise invalid_value("draft_unknown", field=decision.draft_id)
        if draft.review_status in _TERMINAL:
            raise invalid_transition("draft_decided")
        if decision.decision == "reject":
            if decision.value is not None or decision.unit is not None or decision.date is not None:
                raise invalid_value("reject_with_edit", field=decision.draft_id)
            status[draft.draft_id] = "rejected"
            continue
        findings.append(_confirm_one(report, draft, decision, command, policies(draft.code), units, ids))
        status[draft.draft_id] = "confirmed"
    updated = tuple(
        replace(d, review_status=status.get(d.draft_id, d.review_status)) for d in report.extraction_drafts
    )
    return ConfirmationOutcome(
        replace(report, extraction_drafts=updated, revision=report.revision + 1), tuple(findings)
    )
