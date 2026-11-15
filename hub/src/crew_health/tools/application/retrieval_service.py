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
from crew_health.knowledge.application.ports import (
    ApplicabilityFilter,
    Eligible,
    EvidenceQueryPort,
    Hits,
    IndexUnavailable,
    InReleaseHistory,
    NoMatch,
    RankedItem,
    ReleaseMembership,
    ReleaseUnavailable,
    SearchResult,
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
    """Snapshot-bound local retrieval. Dense providers must have completed external work before binding."""

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
        ):
            raise ToolsInvalid("CONFIG_INVALID")
        self._reader, self._eligibility, self._tokens = reader, eligibility, tokens
        self._settings, self._retrieval, self._boundary = settings, retrieval, boundary
        self._dense_notice, self._empty_notice = dense_unavailable, no_supported_passage
        self._budget_notice = context_over_budget

    def _ranking(self, result: SearchResult, release_id: str, maximum: int) -> tuple[RankedItem, ...]:
        if isinstance(result, NoMatch) and result.release_id == release_id:
            return ()
        if not isinstance(result, Hits):
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if result.release_id != release_id or len(result.items) > maximum:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        ids = tuple(item.membership.passage.passage_id for item in result.items)
        if len(set(ids)) != len(ids):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        for rank, item in enumerate(result.items, 1):
            if item.rank != rank or item.membership.release_id != release_id:
                raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        return result.items

    def search(
        self,
        snapshot: ReadSnapshot,
        scope: ToolScope,
        query: str,
        *,
        use: str,
        filters: ApplicabilityFilter,
        requirements: Requirements,
        patient_location_id: str | None,
        proposed_operator_id: str | None,
    ) -> RetrievalResult:
        if self._boundary is None or self._tokens is None or self._retrieval.rerank:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if snapshot.release.release_id != scope.config_release_id:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        query = self._boundary.validate(query, model_release_id=scope.model_release_id)
        if filters.environments is not None and len(filters.environments) > self._settings.integer(
            "tools.query.max_filters"
        ):
            raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        pin, tuning = scope.evidence_release_id, self._retrieval
        fused, limitations = self._fused(scope, query, use, filters)
        selected: list[RetrievedPassage] = []
        parents: dict[str, int] = {}
        over_budget = False
        for passage_id in fused:
            read = self._reader.read_approved(pin, passage_id, use)
            if not isinstance(read, Eligible):
                raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
            member = read.membership
            if parents.get(member.extraction.extraction_id, 0) >= tuning.max_per_parent:
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
            parents[member.extraction.extraction_id] = parents.get(member.extraction.extraction_id, 0) + 1
            if len(selected) == tuning.final_max:
                break
        if not selected:
            if over_budget:
                return RetrievalResult("unavailable", (), (*limitations, self._budget_notice), ())
            return RetrievalResult("no_result", (), (*limitations, self._empty_notice), ())
        return self._with_history(scope, tuple(selected), use, limitations)

    def _fused(
        self, scope: ToolScope, query: str, use: str, filters: ApplicabilityFilter
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        pin, tuning = scope.evidence_release_id, self._retrieval
        rankings = [
            self._ranking(
                self._reader.lexical_search(pin, query, use, filters, mode, tuning.k_lexical),
                pin,
                tuning.k_lexical,
            )
            for mode in ("exact", "stemmed")
        ]
        limitations: tuple[str, ...] = ()
        if tuning.k_dense:
            dense = self._reader.dense_search(pin, query, use, filters, tuning.k_dense)
            if isinstance(dense, IndexUnavailable) and dense.method == "dense" and dense.release_id == pin:
                limitations = (self._dense_notice,)
            else:
                rankings.append(self._ranking(dense, pin, tuning.k_dense))
        fused = fuse_ranks(
            tuple(tuple(item.membership.passage.passage_id for item in ranking) for ranking in rankings),
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
        use: str,
        filters: ApplicabilityFilter,
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
        if isinstance(read, ReleaseUnavailable):
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if isinstance(read, InReleaseHistory):
            return self._history(scope, passage_id, use)
        if not isinstance(read, Eligible):
            return RetrievalResult("no_result", (), (self._empty_notice,), ())
        anchor = self._eligible(
            snapshot, scope, read, use, requirements, patient_location_id, proposed_operator_id
        )
        if anchor is None or not filters.admits(read.membership.environments):
            return RetrievalResult("no_result", (), (self._empty_notice,), ())
        selected_passages = [anchor]
        if not self._fits((anchor,), scope):
            return RetrievalResult("unavailable", (), (self._budget_notice,), ())
        if neighbors:
            expansion = self._ranking(
                self._reader.expand_context(scope.evidence_release_id, passage_id, use, filters, neighbors),
                scope.evidence_release_id,
                neighbors * 2 + 1,
            )
            for item in expansion:
                identifier = item.membership.passage.passage_id
                if identifier == passage_id or len(selected_passages) == self._retrieval.final_max:
                    continue
                neighbor = self._reader.read_approved(scope.evidence_release_id, identifier, use)
                if not isinstance(neighbor, Eligible):
                    raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
                candidate = self._eligible(
                    snapshot, scope, neighbor, use, requirements, patient_location_id, proposed_operator_id
                )
                if candidate is not None and self._fits(tuple((*selected_passages, candidate)), scope):
                    selected_passages.append(candidate)
        return self._with_history(scope, tuple(selected_passages), use, ())

    def _history_rows(self, passage_id: str) -> tuple[tuple[ReleaseMembership, ...], bool]:
        page_size = self._settings.integer("tools.max_array_items")
        maximum = self._settings.integer("tools.context.max_items")
        cursor: str | None = None
        seen: set[str] = set()
        rows: list[ReleaseMembership] = []
        identities: set[str] = set()
        for _ in range((maximum + page_size - 1) // page_size):
            limit = min(page_size, maximum - len(rows))
            page = self._reader.inspect_history(passage_id, cursor, limit)
            if len(page.items) > limit:
                raise ToolsInvalid("PAYLOAD_TOO_LARGE")
            for member in page.items:
                if member.passage.passage_id != passage_id or member.release_id in identities:
                    raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
                identities.add(member.release_id)
                rows.append(member)
            if page.next_cursor is None:
                return tuple(rows), True
            if not page.items or page.next_cursor in seen:
                raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        return tuple(rows), False

    def _history(self, scope: ToolScope, passage_id: str, use: str) -> RetrievalResult:
        members, complete = self._history_rows(passage_id)
        rows = tuple(
            self._passage(member, use) for member in members if member.release_id == scope.evidence_release_id
        )
        if not complete or not self._fits(rows, scope):
            return RetrievalResult("unavailable", (), (self._budget_notice,), (), False)
        if not rows:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        return RetrievalResult("no_result", (), (self._empty_notice,), rows)

    def _with_history(
        self, scope: ToolScope, passages: tuple[RetrievedPassage, ...], use: str, limitations: tuple[str, ...]
    ) -> RetrievalResult:
        history: list[RetrievedPassage] = []
        maximum = self._settings.integer("tools.context.max_items")
        for passage in passages:
            members, complete = self._history_rows(passage.passage_id)
            for member in members:
                if member.release_id == passage.release_id:
                    continue
                if len(history) == maximum:
                    return RetrievalResult("unavailable", (), (*limitations, self._budget_notice), (), False)
                history.append(self._passage(member, use))
            if not complete or not self._fits((*passages, *history), scope):
                return RetrievalResult("unavailable", (), (*limitations, self._budget_notice), (), False)
        return RetrievalResult("ok", passages, limitations, tuple(history))

    def _passage(self, member: ReleaseMembership, use: str) -> RetrievedPassage:
        passage = member.passage
        status = member.use_status(use)
        return RetrievedPassage(
            passage.passage_id,
            member.release_id,
            use,
            passage.text_sha256,
            passage.text,
            passage.page_start,
            passage.page_end,
            member.clinical_review,
            None if status is None else status.status,
            None if status is None else status.successor_ref,
            member.source_ids,
            member.extraction.document_version_id,
            False,
        )

    def _eligible(
        self,
        snapshot: ReadSnapshot,
        scope: ToolScope,
        read: Eligible,
        use: str,
        requirements: Requirements,
        location: str | None,
        operator: str | None,
    ) -> RetrievedPassage | None:
        member, passage = read.membership, read.membership.passage
        status = member.use_status(use)
        if (
            member.release_id != scope.evidence_release_id
            or read.purpose != use
            or member.withdrawn
            or status is None
            or status.status != "approved"
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
        return replace(self._passage(member, use), usable_as_support=True)

    def _fits(self, passages: tuple[RetrievedPassage, ...], scope: ToolScope) -> bool:
        if self._tokens is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        raw = canonical_json([asdict(passage) for passage in passages])
        maximum = int(self._settings.quantity("tools.max_result_bytes").value)
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
