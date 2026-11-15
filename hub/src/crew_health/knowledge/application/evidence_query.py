from __future__ import annotations

from collections.abc import Sequence
from heapq import merge

from crew_health.kernel.clock import Clock
from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.application.ports import LexicalIndexPort
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.release import ApplicabilityFilter, ReleaseMembership
from crew_health.knowledge.domain.results import (
    ApprovedRead,
    Eligible,
    HistoryRead,
    Hits,
    IndexUnavailable,
    InReleaseHistory,
    NoMatch,
    NotApprovedForUse,
    NotInRelease,
    QueryRejected,
    RankedItem,
    ReleaseUnavailable,
    SearchMode,
    SearchResult,
)


class EvidenceQueryService:
    """Immutable, bounded membership view for one pinned reader lease."""

    def __init__(
        self,
        memberships: tuple[ReleaseMembership, ...],
        *,
        settings: HubSettings,
        clock: Clock,
        index: LexicalIndexPort | None = None,
    ) -> None:
        require_bound(len(memberships), settings.integer("knowledge.archive.max_memberships"))
        self._settings = settings
        self._index = index
        self._clock = clock
        self._members = {(m.release_id, m.passage.passage_id): m for m in memberships}
        if len(self._members) != len(memberships):
            raise EvidenceInvalid("duplicate_membership")
        self._releases = frozenset(m.release_id for m in memberships)
        self._history: dict[str, tuple[ReleaseMembership, ...]] = {}
        sources: dict[tuple[str, str], list[ReleaseMembership]] = {}
        grouped: dict[str, list[ReleaseMembership]] = {}
        sections: dict[tuple[str, str, str], list[ReleaseMembership]] = {}
        for member in memberships:
            grouped.setdefault(member.passage.passage_id, []).append(member)
            require_bound(len(member.source_ids), settings.integer("knowledge.query.max_source_ids"))
            require_bound(len(member.passage.text), settings.integer("knowledge.passage.max_chars"))
            for source_id in member.source_ids:
                sources.setdefault((member.release_id, source_id), []).append(member)
            sections.setdefault(
                (member.release_id, member.extraction.extraction_id, member.section), []
            ).append(member)
        self._sections = {
            key: tuple(sorted(rows, key=lambda m: (m.passage.char_start, m.passage.passage_id)))
            for key, rows in sections.items()
        }
        self._positions = {
            (member.release_id, member.passage.passage_id): index
            for rows in self._sections.values()
            for index, member in enumerate(rows)
        }
        self._sources = {
            key: tuple(sorted(rows, key=lambda m: m.passage.passage_id)) for key, rows in sources.items()
        }
        for passage_id, rows in grouped.items():
            ordered = tuple(sorted(rows, key=lambda m: m.release_id))
            if any(m.passage != ordered[0].passage for m in ordered):
                raise EvidenceInvalid("artifact_conflict")
            self._history[passage_id] = ordered
        self._history_positions = {
            (passage_id, member.release_id): position
            for passage_id, rows in self._history.items()
            for position, member in enumerate(rows)
        }

    def _started(self) -> tuple[str, int, int]:
        timeout = self._settings.quantity("knowledge.query.timeout_ms")
        if timeout.unit.code != "ms" or timeout.value <= 0:
            raise EvidenceInvalid("query_timeout")
        return self._clock.process_epoch(), self._clock.monotonic_ns(), int(timeout.value * 1000000)

    def _expired(self, started: tuple[str, int, int]) -> bool:
        epoch, at, timeout_ns = started
        elapsed = self._clock.monotonic_ns() - at
        return self._clock.process_epoch() != epoch or elapsed < 0 or elapsed >= timeout_ns

    def read_approved(self, release_id: str, passage_id: str, use: str) -> ApprovedRead:
        if release_id not in self._releases:
            return ReleaseUnavailable(release_id, "release_unavailable")
        member = self._members.get((release_id, passage_id))
        if member is None:
            return NotInRelease(release_id, passage_id)
        status = member.use_status(use)
        if status is None:
            return NotApprovedForUse(release_id, passage_id, use)
        if member.withdrawn or status.status != "approved":
            return InReleaseHistory(release_id, passage_id, use)
        return Eligible(member, status.use)

    def inspect_history(self, passage_id: str, cursor: str | None, limit: int) -> HistoryRead:
        require_bound(limit, self._settings.integer("knowledge.query.limit_max"))
        if limit < 1:
            raise EvidenceInvalid("page_limit")
        rows = self._history.get(passage_id, ())
        position = 0
        if cursor is not None:
            previous = self._history_positions.get((passage_id, cursor))
            if previous is None:
                raise EvidenceInvalid("history_cursor")
            position = previous + 1
        selected = rows[position : position + limit + 1]
        return HistoryRead(
            selected[:limit], selected[limit - 1].release_id if len(selected) > limit else None
        )

    def passages_for_sources(
        self,
        release_id: str,
        source_ids: Sequence[str],
        use: str,
        filters: ApplicabilityFilter,
        limit: int,
    ) -> SearchResult:
        require_bound(len(source_ids), self._settings.integer("knowledge.query.max_source_ids"))
        require_bound(limit, self._settings.integer("knowledge.query.limit_max"))
        if limit < 1:
            raise EvidenceInvalid("page_limit")
        if release_id not in self._releases:
            return ReleaseUnavailable(release_id, "release_unavailable")
        started = self._started()
        results: list[RankedItem] = []
        streams = tuple(
            self._sources.get((release_id, source_id), ()) for source_id in sorted(set(source_ids))
        )
        previous: str | None = None
        for member in merge(*streams, key=lambda m: m.passage.passage_id):
            if self._expired(started):
                return IndexUnavailable(release_id, "source", "query_timeout")
            if member.passage.passage_id == previous:
                continue
            previous = member.passage.passage_id
            status = member.use_status(use)
            if (
                not member.withdrawn
                and status is not None
                and status.status == "approved"
                and filters.admits(member.environments)
            ):
                results.append(RankedItem(member, len(results) + 1, "source"))
                if len(results) == limit:
                    break
        return Hits(release_id, tuple(results)) if results else NoMatch(release_id)

    def expand_context(
        self,
        release_id: str,
        passage_id: str,
        use: str,
        filters: ApplicabilityFilter,
        neighbors: int,
    ) -> SearchResult:
        maximum = self._settings.integer("knowledge.query.expand_neighbors_max")
        if type(neighbors) is not int or not 0 <= neighbors <= maximum:
            return QueryRejected("neighbors")
        anchor = self.read_approved(release_id, passage_id, use)
        if isinstance(anchor, ReleaseUnavailable):
            return anchor
        if not isinstance(anchor, Eligible) or not filters.admits(anchor.membership.environments):
            return NoMatch(release_id)
        member = anchor.membership
        rows = self._sections[(release_id, member.extraction.extraction_id, member.section)]
        position = self._positions[(release_id, passage_id)]
        selected = rows[max(0, position - neighbors) : position + neighbors + 1]
        results: list[RankedItem] = []
        for row in selected:
            status = row.use_status(use)
            if (
                not row.withdrawn
                and status is not None
                and status.status == "approved"
                and filters.admits(row.environments)
            ):
                results.append(RankedItem(row, len(results) + 1, "context"))
        return Hits(release_id, tuple(results))

    def _query(self, release_id: str, query: str, k: int) -> ReleaseUnavailable | QueryRejected | None:
        if type(k) is not int or not 1 <= k <= self._settings.integer("knowledge.query.k_max"):
            return QueryRejected("k")
        if not query.strip() or len(query) > self._settings.integer("knowledge.query.max_chars"):
            return QueryRejected("query")
        if release_id not in self._releases:
            return ReleaseUnavailable(release_id, "release_unavailable")
        return None

    def lexical_search(
        self,
        release_id: str,
        query: str,
        use: str,
        filters: ApplicabilityFilter,
        mode: SearchMode,
        k: int,
    ) -> SearchResult:
        fault = self._query(release_id, query, k)
        if fault is not None:
            return fault
        if mode not in ("exact", "stemmed"):
            return QueryRejected("mode")
        if self._index is None:
            return IndexUnavailable(release_id, "lexical", "index_unavailable")
        started = self._started()
        identifiers = self._index.search(release_id, query, use, filters, mode, k)
        if self._expired(started):
            return IndexUnavailable(release_id, "lexical", "query_timeout")
        if isinstance(identifiers, IndexUnavailable):
            return identifiers
        require_bound(len(identifiers), k)
        results: list[RankedItem] = []
        for passage_id in dict.fromkeys(identifiers):
            read = self.read_approved(release_id, passage_id, use)
            if not isinstance(read, Eligible) or not filters.admits(read.membership.environments):
                raise EvidenceInvalid("index_membership")
            results.append(RankedItem(read.membership, len(results) + 1, "lexical"))
        return Hits(release_id, tuple(results)) if results else NoMatch(release_id)

    def dense_search(
        self,
        release_id: str,
        query: str,
        use: str,
        filters: ApplicabilityFilter,
        k: int,
    ) -> SearchResult:
        fault = self._query(release_id, query, k)
        return fault if fault is not None else IndexUnavailable(release_id, "dense", "R-E10-19")
