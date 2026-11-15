from __future__ import annotations

from collections.abc import Sequence

from crew_health.kernel.ids import IdFactory
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.registry import (
    Active,
    Condition,
    DeclaredSource,
    Membership,
    RegistryCounts,
    RegistrySnapshot,
    RegistryUnavailable,
    RemovedOrSplit,
    ResolutionReport,
    SourceSummary,
    SourceUnknown,
    UnknownCondition,
)
from crew_health.knowledge.domain.urls import normalize_url


class RegistryService:
    def __init__(
        self,
        snapshot: RegistrySnapshot,
        *,
        ids: IdFactory,
        max_rows: int,
        max_source_ids: int,
        max_url_chars: int,
        tracking_parameters: tuple[str, ...],
    ) -> None:
        for count in (
            len(snapshot.conditions),
            len(snapshot.memberships),
            len(snapshot.sources),
            len(snapshot.families),
        ):
            require_bound(count, max_rows)
        require_bound(max_source_ids, max_rows)
        self.snapshot = snapshot
        self._max_source_ids = max_source_ids
        self._ids = ids
        self._max_url_chars = max_url_chars
        self._tracking_parameters = tracking_parameters
        self._conditions = {c.condition_id: c for c in snapshot.conditions}
        self._sources, conflicts = self._summaries(snapshot.sources)
        if conflicts:
            raise EvidenceInvalid("source_conflict")

    def _url(self, url: str) -> str:
        return normalize_url(
            url, tracking_parameters=self._tracking_parameters, max_chars=self._max_url_chars
        )

    def _summaries(
        self, declarations: Sequence[DeclaredSource]
    ) -> tuple[dict[str, SourceSummary], tuple[str, ...]]:
        grouped: dict[str, list[DeclaredSource]] = {}
        for declaration in declarations:
            grouped.setdefault(declaration.source_id, []).append(declaration)
        summaries: dict[str, SourceSummary] = {}
        conflicts = []
        for source_id, rows in sorted(grouped.items()):
            urls = {self._url(r.url) for r in rows}
            declarations_by_input: dict[str, set[str]] = {}
            for row in rows:
                declarations_by_input.setdefault(row.declaring_input, set()).add(self._url(row.url))
            if not any(declared == urls for declared in declarations_by_input.values()):
                conflicts.append(source_id)
                continue
            ordered = tuple(sorted(rows, key=lambda r: (self._url(r.url), r.declaring_input, r.title)))
            first = ordered[0]
            summaries[source_id] = SourceSummary(
                source_id,
                first.title,
                first.url,
                first.date_text,
                first.access_text,
                first.saved_as,
                self._ids.derived("cat", self._url(first.url)),
                tuple(sorted({r.title for r in rows})),
                tuple(sorted({r.declaring_input for r in rows})),
                ordered,
                tuple(self._ids.derived("cat", url) for url in sorted(urls)),
            )
        return summaries, tuple(conflicts)

    def summary(self, source_id: str) -> SourceSummary | SourceUnknown:
        return self._sources.get(source_id, SourceUnknown(source_id))

    def resolve_many(
        self, source_ids: Sequence[str], extra: Sequence[DeclaredSource] = ()
    ) -> ResolutionReport:
        require_bound(len(source_ids), self._max_source_ids)
        require_bound(len(extra), self._max_source_ids)
        existing = tuple(e for e in extra if e.source_id in self._sources)
        conflicts = tuple(
            sorted(
                {
                    e.source_id
                    for e in existing
                    if self._url(e.url)
                    not in {self._url(d.url) for d in self._sources[e.source_id].documents}
                }
            )
        )
        base = tuple(
            document
            for source_id in sorted({e.source_id for e in existing})
            for document in self._sources[source_id].documents
        )
        summaries, new_conflicts = self._summaries(
            (*base, *(e for e in extra if e.source_id not in conflicts))
        )
        conflicts = tuple(sorted(set((*conflicts, *new_conflicts))))
        selected = sorted(set(source_ids))
        resolved = []
        unknown = []
        for source_id in selected:
            if source_id in conflicts:
                continue
            summary = summaries.get(source_id, self._sources.get(source_id))
            if summary is None:
                unknown.append(source_id)
            else:
                resolved.append(summary)
        return ResolutionReport(
            tuple(resolved),
            tuple(unknown),
            conflicts,
        )

    def get(self, condition_id: str) -> Condition | UnknownCondition:
        return self._conditions.get(condition_id, UnknownCondition(condition_id))

    def condition_status(self, condition_id: str) -> Active | RemovedOrSplit | UnknownCondition:
        condition = self.get(condition_id)
        if isinstance(condition, UnknownCondition):
            return condition
        return Active(condition) if condition.status == "active" else RemovedOrSplit(condition)

    def active_ids(self) -> tuple[str, ...]:
        return tuple(sorted(c.condition_id for c in self.snapshot.conditions if c.status == "active"))

    def memberships(self, condition_id: str) -> tuple[Membership, ...]:
        return tuple(
            m
            for m in self.snapshot.memberships
            if m.source_entry_id == condition_id
            or condition_id in m.published_mapping
            or condition_id in m.title_match_mapping
        )

    def aliases(self, condition_id: str) -> tuple[str, ...]:
        condition = self.get(condition_id)
        if isinstance(condition, UnknownCondition):
            return ()
        return tuple(sorted({condition.title, *(m.published_title for m in self.memberships(condition_id))}))

    def counts(self) -> RegistryCounts:
        return RegistryCounts(
            len(self.snapshot.memberships),
            len(self.active_ids()),
            sum(c.status == "removed_or_split" for c in self.snapshot.conditions),
            len({m.published_title for m in self.snapshot.memberships}),
        )

    def resolve(self, condition_id: str) -> RegistryUnavailable:
        return RegistryUnavailable("R-E10-17")
