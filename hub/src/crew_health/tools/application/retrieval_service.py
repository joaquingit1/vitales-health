from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace

from crew_health.guidance.application.ports import (
    ContentRef,
    GuidanceEligibility,
    GuidanceRequest,
    Requirements,
)
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import ReadSnapshot

# E10's canonical ports (wave 2, 728c2b7) name their value types through the dto module without an explicit
# re-export; docs/requests/E10.md asks for `dto as dto`. The import rule allows only application.ports.
from crew_health.knowledge.application.ports import (  # type: ignore[attr-defined]
    EvidenceQueryPort,
    dto,
)
from crew_health.tools.application.ports import TokenCounter
from crew_health.tools.domain.arguments import require_public_literal
from crew_health.tools.domain.context import require_context_budget
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.retrieval import (
    RetrievalResult,
    RetrievalSettings,
    RetrievedPassage,
    fuse_ranks,
)
from crew_health.tools.domain.scope import ToolScope
from crew_health.tools.settings import validate_settings


class PublicQueryBoundary:
    """Exact public-topic vocabulary. The model's original free text never crosses this boundary."""

    def __init__(
        self,
        queries: tuple[str, ...],
        *,
        model_release_id: str,
        settings: HubSettings,
        public_scope: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._model_release_id = require_id(model_release_id)
        maximum = settings.integer("tools.query.max_filters")
        chars, terms = settings.integer("tools.query.max_chars"), settings.integer("tools.query.max_terms")
        self._max_chars, self._max_terms, self._public_scope = chars, terms, public_scope
        if not queries or len(queries) > maximum or len(set(queries)) != len(queries):
            raise ToolsInvalid("CONFIG_INVALID")
        if any(not query.strip() or len(query) > chars or len(query.split()) > terms for query in queries):
            raise ToolsInvalid("CONFIG_INVALID")
        for query in queries:
            require_public_literal(query, max_chars=chars, max_terms=terms)
        self._queries = {query: query for query in queries}

    @property
    def model_release_id(self) -> str:
        return self._model_release_id

    def validate(self, query: str, *, model_release_id: str) -> str:
        require_public_literal(query, max_chars=self._max_chars, max_terms=self._max_terms)
        public = self._queries.get(query)
        if model_release_id != self.model_release_id or public is None:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if self._public_scope is None or not self._public_scope(public, model_release_id):
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        return public


class RetrievalService:
    """Pinned local retrieval over E10's canonical EvidenceQueryPort (E12 4.2). Every candidate is reread through
    read_approved at the explicit pin and rechecked by E27 guidance eligibility; history and discrepancies stay
    visible as non-supporting rows."""

    def __init__(
        self,
        reader: EvidenceQueryPort,
        eligibility: GuidanceEligibility,
        tokens: TokenCounter | None,
        settings: HubSettings,
        retrieval: RetrievalSettings,
        *,
        boundary: PublicQueryBoundary | None,
        dense_unavailable: str,
        no_supported_passage: str,
        context_over_budget: str,
        history_incomplete: str,
    ) -> None:
        validate_settings(settings)
        k_max = settings.integer("tools.retrieval.k_max")
        if (
            not 1 <= retrieval.k_lexical <= k_max
            or not 0 <= retrieval.k_dense <= k_max
            or not 1 <= retrieval.final_max <= settings.integer("tools.retrieval.final_max")
            or not 1 <= retrieval.max_per_parent <= retrieval.final_max
            or not 1 <= retrieval.budget_tokens <= settings.integer("tools.retrieval.max_tokens")
            or not retrieval.k_rrf.is_finite()
            or retrieval.k_rrf <= 0
            or not dense_unavailable
            or not no_supported_passage
            or not context_over_budget
            or not history_incomplete
        ):
            raise ToolsInvalid("CONFIG_INVALID")
        self._reader, self._eligibility, self._tokens = reader, eligibility, tokens
        self._settings, self._retrieval, self._boundary = settings, retrieval, boundary
        self._dense_notice, self._empty_notice = dense_unavailable, no_supported_passage
        self._budget_notice, self._incomplete_notice = context_over_budget, history_incomplete

    def _ranking(
        self, result: dto.SearchResult, release_id: str, maximum: int
    ) -> tuple[dto.RankedPassage, ...]:
        if isinstance(result, dto.NoMatch) and result.release_id == release_id:
            return ()
        if isinstance(result, dto.QueryRejected):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if not isinstance(result, dto.Hits):
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if result.release_id != release_id or len(result.items) > maximum:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        ids = tuple(item.passage.passage.passage_id for item in result.items)
        if len(set(ids)) != len(ids):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        for rank, item in enumerate(result.items, 1):
            if item.rank != rank or item.passage.release_id != release_id:
                raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        return result.items

    def search(
        self,
        snapshot: ReadSnapshot,
        scope: ToolScope,
        query: str,
        *,
        use: dto.AllowedUse,
        filters: dto.ApplicabilityFilter,
        requirements: Requirements,
        patient_location_id: str | None,
        proposed_operator_id: str | None,
    ) -> RetrievalResult:
        if self._boundary is None or self._tokens is None or self._retrieval.rerank:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if snapshot.release.release_id != scope.config_release_id:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        query = self._boundary.validate(query, model_release_id=scope.model_release_id)
        if len(filters.fields) > self._settings.integer("tools.query.max_filters"):
            raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        tuning = self._retrieval
        fused, limitations = self._fused(scope, query, use, filters)
        selected: list[RetrievedPassage] = []
        parents: dict[str, int] = {}
        over_budget = False
        for passage_id in fused:
            read = self._approved(scope, passage_id, use)
            if read is None:
                continue
            parent = read.passage.passage.extraction_id
            if parents.get(parent, 0) >= tuning.max_per_parent:
                continue
            candidate = self._eligible(
                snapshot, scope, read, use, requirements, patient_location_id, proposed_operator_id
            )
            if candidate is None:
                continue
            if not self._fits(tuple((*selected, candidate)), scope):
                over_budget = True
                continue
            selected.append(candidate)
            parents[parent] = parents.get(parent, 0) + 1
            if len(selected) == tuning.final_max:
                break
        if not selected:
            if over_budget:
                return RetrievalResult("unavailable", (), (*limitations, self._budget_notice), ())
            return RetrievalResult("no_result", (), (*limitations, self._empty_notice), ())
        return self._with_history(scope, tuple(selected), use, limitations)

    def _approved(self, scope: ToolScope, passage_id: str, use: dto.AllowedUse) -> dto.Eligible | None:
        """A searched candidate reread at the pin; one withdrawn or reapproved since the search is skipped."""
        read = self._reader.read_approved(scope.evidence_release_id, passage_id, use)
        if isinstance(read, dto.ReleaseUnavailable):
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        return read if isinstance(read, dto.Eligible) else None

    def _fused(
        self, scope: ToolScope, query: str, use: dto.AllowedUse, filters: dto.ApplicabilityFilter
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        pin, tuning = scope.evidence_release_id, self._retrieval
        modes: tuple[dto.SearchMode, ...] = ("exact", "stemmed")
        rankings = [
            self._ranking(
                self._reader.lexical_search(pin, query, use, filters, mode, tuning.k_lexical),
                pin,
                tuning.k_lexical,
            )
            for mode in modes
        ]
        limitations: tuple[str, ...] = ()
        if tuning.k_dense:
            dense = self._reader.dense_search(pin, query, use, filters, tuning.k_dense)
            if (
                isinstance(dense, dto.IndexUnavailable)
                and dense.method == "dense"
                and dense.release_id == pin
            ):
                limitations = (self._dense_notice,)
            else:
                rankings.append(self._ranking(dense, pin, tuning.k_dense))
        fused = fuse_ranks(
            tuple(tuple(item.passage.passage.passage_id for item in ranking) for ranking in rankings),
            k_rrf=tuning.k_rrf,
            k_max=self._settings.integer("tools.retrieval.k_max"),
            candidates_max=self._settings.integer("tools.retrieval.candidates_max"),
        )
        return fused, limitations

    def read(
        self,
        snapshot: ReadSnapshot,
        scope: ToolScope,
        passage_id: str,
        *,
        use: dto.AllowedUse,
        filters: dto.ApplicabilityFilter,
        requirements: Requirements,
        patient_location_id: str | None,
        proposed_operator_id: str | None,
        neighbors: int,
    ) -> RetrievalResult:
        require_id(passage_id)
        if (
            snapshot.release.release_id != scope.config_release_id
            or type(neighbors) is not int
            or not 0 <= neighbors <= self._settings.integer("tools.retrieval.neighbors_max")
        ):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        read = self._reader.read_approved(scope.evidence_release_id, passage_id, use)
        if isinstance(read, dto.ReleaseUnavailable):
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if isinstance(read, dto.InReleaseHistory):
            return self._history(scope, passage_id, use)
        if not isinstance(read, dto.Eligible):
            return RetrievalResult("no_result", (), (self._empty_notice,), ())
        anchor = self._eligible(
            snapshot, scope, read, use, requirements, patient_location_id, proposed_operator_id
        )
        if anchor is None or not filters.admits(read.passage.applicability):
            return RetrievalResult("no_result", (), (self._empty_notice,), ())
        selected = [anchor]
        if not self._fits((anchor,), scope):
            return RetrievalResult("unavailable", (), (self._budget_notice,), ())
        if neighbors:
            expansion = self._ranking(
                self._reader.expand_context(scope.evidence_release_id, passage_id, use, filters, neighbors),
                scope.evidence_release_id,
                neighbors * 2 + 1,
            )
            for item in expansion:
                identifier = item.passage.passage.passage_id
                if identifier == passage_id or len(selected) == self._retrieval.final_max:
                    continue
                neighbor = self._approved(scope, identifier, use)
                candidate = (
                    None
                    if neighbor is None
                    else self._eligible(
                        snapshot,
                        scope,
                        neighbor,
                        use,
                        requirements,
                        patient_location_id,
                        proposed_operator_id,
                    )
                )
                if candidate is not None and self._fits(tuple((*selected, candidate)), scope):
                    selected.append(candidate)
        return self._with_history(scope, tuple(selected), use, ())

    def _history_rows(self, passage_id: str) -> tuple[tuple[dto.ReleaseMembership, ...], bool]:
        """Bounded owner history. The canonical port pages by the last release_id it returned; a short page ends
        it (requested from E10 as an explicit next cursor, docs/requests/E10.md)."""
        page_size = self._settings.integer("tools.max_array_items")
        maximum = self._settings.integer("tools.context.max_items")
        cursor: str | None = None
        rows: list[dto.ReleaseMembership] = []
        identities: set[str] = set()
        for _ in range((maximum + page_size - 1) // page_size):
            limit = min(page_size, maximum - len(rows))
            page = self._reader.inspect_history(passage_id, cursor, limit)
            if isinstance(page, dto.ReleaseUnavailable):
                raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
            if len(page) > limit:
                raise ToolsInvalid("PAYLOAD_TOO_LARGE")
            for member in page:
                if (
                    member.passage.passage_id != passage_id
                    or member.release_id in identities
                    or (cursor is not None and member.release_id <= cursor)
                ):
                    raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
                identities.add(member.release_id)
                rows.append(member)
            if len(page) < limit:
                return tuple(rows), True
            if len(rows) >= maximum:
                return tuple(rows), False
            cursor = page[-1].release_id
        return tuple(rows), False

    def _history(self, scope: ToolScope, passage_id: str, use: dto.AllowedUse) -> RetrievalResult:
        members, complete = self._history_rows(passage_id)
        if not complete:
            return RetrievalResult("unavailable", (), (self._incomplete_notice,), (), False)
        rows = tuple(self._passage(member, use, ()) for member in members)
        if not self._fits(rows, scope):
            return RetrievalResult("unavailable", (), (self._budget_notice,), (), False)
        if not rows:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        return RetrievalResult("no_result", (), (self._empty_notice,), rows)

    def _discrepancies(
        self, scope: ToolScope, ids: frozenset[str]
    ) -> tuple[tuple[dto.Discrepancy, ...], bool]:
        """Every discrepancy of the pinned release touching a selected passage, bounded by the item ceiling."""
        page_size = self._settings.integer("tools.max_array_items")
        maximum = self._settings.integer("tools.context.max_items")
        cursor: str | None = None
        seen: set[str] = set()
        found: list[dto.Discrepancy] = []
        for _ in range((maximum + page_size - 1) // page_size):
            page = self._reader.discrepancies(scope.evidence_release_id, cursor, page_size)
            if len(page.items) > page_size:
                raise ToolsInvalid("PAYLOAD_TOO_LARGE")
            found.extend(d for d in page.items if {d.previous_passage_id, d.current_passage_id} & ids)
            if page.next_cursor is None:
                return tuple(found), True
            if not page.items or page.next_cursor in seen:
                raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        return tuple(found), False

    def _with_history(
        self,
        scope: ToolScope,
        passages: tuple[RetrievedPassage, ...],
        use: dto.AllowedUse,
        limitations: tuple[str, ...],
    ) -> RetrievalResult:
        unavailable = RetrievalResult("unavailable", (), (*limitations, self._budget_notice), (), False)
        incomplete = RetrievalResult("unavailable", (), (*limitations, self._incomplete_notice), (), False)
        discrepancies, complete = self._discrepancies(scope, frozenset(p.passage_id for p in passages))
        if not complete:
            return incomplete
        refs: dict[str, tuple[str, ...]] = {}
        for item in discrepancies:
            for side in (item.previous_passage_id, item.current_passage_id):
                refs[side] = (*refs.get(side, ()), item.discrepancy_id)
        annotated = tuple(replace(p, discrepancy_refs=refs.get(p.passage_id, ())) for p in passages)
        history: list[RetrievedPassage] = []
        maximum = self._settings.integer("tools.context.max_items")
        selected = frozenset(p.passage_id for p in annotated)
        counterparts = {side for d in discrepancies for side in (d.previous_passage_id, d.current_passage_id)}
        for passage_id in sorted(selected | counterparts):
            members, complete = self._history_rows(passage_id)
            if not complete:
                return incomplete
            for member in members:
                if passage_id in selected and member.release_id == scope.evidence_release_id:
                    continue
                if len(history) == maximum:
                    return incomplete
                history.append(self._passage(member, use, refs.get(passage_id, ())))
        if not self._fits((*annotated, *history), scope):
            return unavailable
        return RetrievalResult("ok", annotated, limitations, tuple(history))

    @staticmethod
    def _passage(
        member: dto.ReleaseMembership, use: dto.AllowedUse, discrepancies: tuple[str, ...]
    ) -> RetrievedPassage:
        passage = member.passage
        return RetrievedPassage(
            passage.passage_id,
            member.release_id,
            use,
            passage.text_sha256,
            passage.text,
            passage.section,
            passage.page,
            member.clinical_review,
            dict(member.use_status).get(use),
            member.source_ids,
            passage.extraction_id,
            False,
            discrepancies,
        )

    def _eligible(
        self,
        snapshot: ReadSnapshot,
        scope: ToolScope,
        read: dto.Eligible,
        use: dto.AllowedUse,
        requirements: Requirements,
        location: str | None,
        operator: str | None,
    ) -> RetrievedPassage | None:
        member, passage = read.passage, read.passage.passage
        if (
            member.release_id != scope.evidence_release_id
            or read.release_id != scope.evidence_release_id
            or read.purpose != use
            or dict(member.use_status).get(use) != "approved"
        ):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if member.simulation_only and scope.origin_mode != "SIM":
            return None
        decision = self._eligibility.evaluate(
            snapshot,
            GuidanceRequest(
                ContentRef("passage", passage.passage_id, passage.text_sha256),
                use,
                scope.config_release_id,
                scope.origin_mode,
                scope.subject_person_id,
                location,
                requirements,
                False,
                operator,
            ),
        )
        if decision.outcome != "show" or decision.unknown:
            return None
        if decision.clinical_review != member.clinical_review:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        return replace(self._passage(member, use, ()), usable_as_support=True)

    def _fits(self, passages: tuple[RetrievedPassage, ...], scope: ToolScope) -> bool:
        if self._tokens is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        raw = canonical_json([asdict(passage) for passage in passages])
        maximum = self._settings.integer("tools.max_result_bytes")
        if len(raw) > maximum:
            return False
        count = self._tokens.count(raw, model_release_id=scope.model_release_id)
        try:
            require_context_budget(
                prompt=raw,
                prompt_tokens=count,
                output_reserve=0,
                max_bytes=maximum,
                max_tokens=self._retrieval.budget_tokens,
            )
        except ToolsInvalid as error:
            if error.code != "PAYLOAD_TOO_LARGE":
                raise
            return False
        return True
