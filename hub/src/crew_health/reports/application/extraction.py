"""Runs queued report_extraction tasks: claim, parse in the contained worker, publish the draft (E09-02, D17).

The contained parser runs outside any hub transaction; the result is published only through
`ReportWorkflows.record_extraction`, which fences the generation, the source version and the pinned release.
A parser that is absent, slow or wrong leaves the report unextracted with a failed task; the manual review and
entry paths still work. The model is never involved."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.tasks import TaskLease, TaskRunner
from crew_health.kernel.uow import Actor, SessionContext, UnitOfWorkFactory
from crew_health.reports.application.access import REPORT_ROLES
from crew_health.reports.application.workflows import EXTRACTION_TASK, ReportWorkflows
from crew_health.reports.domain.extraction import DraftProposal, ExtractionResult, parse_pin
from crew_health.reports.domain.media import detect_kind
from crew_health.reports.domain.model import SourceSpan

ENGINE_VERSION = "report-extract-1"
WORKER_ID = "reports.extraction"


@dataclass(frozen=True, slots=True)
class ParsedFile:
    ok: bool
    reason: str | None
    page_text_lengths: tuple[int, ...]
    proposals: tuple[tuple[int, int, int, str, str, str | None], ...]


type Parse = Callable[[str, bytes, dict[str, dict[str, object]]], ParsedFile]
type Vocabulary = Callable[[str], dict[str, dict[str, object]]]


class ExtractionRunner:
    def __init__(
        self,
        *,
        workflows: ReportWorkflows,
        runner: TaskRunner,
        uows: UnitOfWorkFactory,
        releases: ConfigReleaseReader,
        parse: Parse,
        vocabulary: Callable[..., dict[str, dict[str, object]]],
        batch: int,
    ) -> None:
        self.w, self.runner, self.uows, self.releases = workflows, runner, uows, releases
        self.parse, self.vocabulary, self.batch = parse, vocabulary, batch

    def drain(self) -> int:
        """Claim and run up to `batch` queued tasks. Returns how many completed."""
        with self.uows.begin(actor=Actor("engine", WORKER_ID), correlation_id="reports_claim") as uow:
            leases = self.runner.claim(
                uow, worker_id=WORKER_ID, handlers=frozenset({EXTRACTION_TASK}), limit=self.batch
            )
        return sum(self._run(lease) for lease in leases)

    def _run(self, lease: TaskLease) -> bool:
        owner = lease.task.actor.id
        session = SessionContext("extraction", "crew", lease.task.actor, owner, REPORT_ROLES)
        report_id, version = parse_pin(lease.task.input_ref)
        try:
            source = self.w.p.store.read_source(session, report_id)
            report = self.w.p.store.load(session, report_id)
            if source is None or report is None or report.source_version_id != version:
                return self._fail(lease, ErrorCode.NOT_FOUND, retryable=False)
            kind = detect_kind(source)
            release = self.releases.release(lease.task.release_id)
            parsed = self.parse(kind or "", source, self.vocabulary(release))
        except ProblemError as error:
            return self._fail(lease, error.code, retryable=error.retryable)
        except Exception:
            return self._fail(lease, ErrorCode.CAPABILITY_UNAVAILABLE, retryable=True)
        if not parsed.ok:
            return self._fail(lease, ErrorCode.INVALID_VALUE, retryable=False)
        # The date a draft proposes is the one the owner gave for the report at upload, shown on the draft for
        # the person to confirm; with none given it stays null and confirming a number then needs a date.
        source_date = report.examination_date or report.report_date
        result = ExtractionResult(
            version,
            ENGINE_VERSION,
            parsed.page_text_lengths,
            tuple(
                DraftProposal(SourceSpan(p, s, e), c, Decimal(v), u, source_date)
                for p, s, e, c, v, u in parsed.proposals
            ),
        )
        try:
            self.w.record_extraction(session, lease, result)
        except ProblemError as error:
            return self._fail(lease, error.code, retryable=False)
        return True

    def _fail(self, lease: TaskLease, code: ErrorCode, *, retryable: bool) -> bool:
        with self.uows.begin(actor=Actor("engine", WORKER_ID), correlation_id="reports_fail") as uow:
            self.runner.fail(uow, lease, code=code, retryable=retryable)
        return False


__all__ = ["ENGINE_VERSION", "ExtractionRunner", "ParsedFile"]
