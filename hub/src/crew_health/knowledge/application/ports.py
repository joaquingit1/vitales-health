from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from crew_health.kernel.artifacts import ArtifactReader as ArtifactReader
from crew_health.knowledge.domain.export import ReviewExport
from crew_health.knowledge.domain.extraction import FormPage
from crew_health.knowledge.domain.manifest import ReleaseContent, ReleaseManifest
from crew_health.knowledge.domain.registry import (
    Active,
    Condition,
    DeclaredSource,
    Membership,
    RegistryCounts,
    RegistryUnavailable,
    RemovedOrSplit,
    ResolutionReport,
    SourceSummary,
    SourceUnknown,
    UnknownCondition,
)
from crew_health.knowledge.domain.release import ApplicabilityFilter as ApplicabilityFilter
from crew_health.knowledge.domain.release import ReleaseMembership as ReleaseMembership
from crew_health.knowledge.domain.results import (
    ApprovedRead,
    HistoryRead,
    SearchMode,
)
from crew_health.knowledge.domain.results import Eligible as Eligible
from crew_health.knowledge.domain.results import Hits as Hits
from crew_health.knowledge.domain.results import IndexUnavailable as IndexUnavailable
from crew_health.knowledge.domain.results import InReleaseHistory as InReleaseHistory
from crew_health.knowledge.domain.results import NoMatch as NoMatch
from crew_health.knowledge.domain.results import NotApprovedForUse as NotApprovedForUse
from crew_health.knowledge.domain.results import NotInRelease as NotInRelease
from crew_health.knowledge.domain.results import RankedItem as RankedItem
from crew_health.knowledge.domain.results import ReleaseUnavailable as ReleaseUnavailable
from crew_health.knowledge.domain.results import SearchResult as SearchResult


class SourceRegistryPort(Protocol):
    def summary(self, source_id: str) -> SourceSummary | SourceUnknown | RegistryUnavailable: ...
    def resolve_many(
        self,
        source_ids: Sequence[str],
        extra: Sequence[DeclaredSource] = (),
    ) -> ResolutionReport | RegistryUnavailable: ...


class ConditionRegistryPort(Protocol):
    def condition_status(
        self, condition_id: str
    ) -> Active | RemovedOrSplit | UnknownCondition | RegistryUnavailable: ...
    def active_ids(self) -> tuple[str, ...] | RegistryUnavailable: ...
    def get(self, condition_id: str) -> Condition | UnknownCondition | RegistryUnavailable: ...
    def memberships(self, condition_id: str) -> tuple[Membership, ...] | RegistryUnavailable: ...
    def aliases(self, condition_id: str) -> tuple[str, ...] | RegistryUnavailable: ...
    def counts(self) -> RegistryCounts | RegistryUnavailable: ...
    def resolve(self, condition_id: str) -> RegistryUnavailable:
        """R-E10-17: successful configuration/evidence pin binding remains unavailable."""
        ...


class FormTextPort(Protocol):
    def quote_on_page(self, source_id: str, page: int, quote: str) -> bool: ...
    def pages(self, source_id: str) -> Sequence[FormPage] | None: ...


class LexicalIndexPort(Protocol):
    def search(
        self,
        release_id: str,
        query: str,
        use: str,
        filters: ApplicabilityFilter,
        mode: SearchMode,
        k: int,
    ) -> tuple[str, ...] | IndexUnavailable: ...


class EvidenceQueryPort(Protocol):
    def read_approved(self, release_id: str, passage_id: str, use: str) -> ApprovedRead: ...
    def inspect_history(self, passage_id: str, cursor: str | None, limit: int) -> HistoryRead: ...
    def passages_for_sources(
        self,
        release_id: str,
        source_ids: Sequence[str],
        use: str,
        filters: ApplicabilityFilter,
        limit: int,
    ) -> SearchResult: ...

    def lexical_search(
        self,
        release_id: str,
        query: str,
        use: str,
        filters: ApplicabilityFilter,
        mode: SearchMode,
        k: int,
    ) -> SearchResult: ...
    def dense_search(
        self,
        release_id: str,
        query: str,
        use: str,
        filters: ApplicabilityFilter,
        k: int,
    ) -> SearchResult: ...
    def expand_context(
        self,
        release_id: str,
        passage_id: str,
        use: str,
        filters: ApplicabilityFilter,
        neighbors: int,
    ) -> SearchResult: ...


class ReleaseCodec(Protocol):
    def encode_export(self, export: ReviewExport) -> bytes: ...
    def encode_content(self, content: ReleaseContent) -> bytes: ...
    def encode_manifest(self, manifest: ReleaseManifest, *, body_only: bool = False) -> bytes: ...


class BundleWriter(Protocol):
    def write(self, members: Sequence[tuple[str, bytes]]) -> bytes: ...


class ReviewAuthority(Protocol):
    def verify(self, export_sha256: str, checkpoint: bytes) -> None: ...


class ReleaseHistoryReader(Protocol):
    def members(self, release_id: str) -> Sequence[tuple[str, bytes]]: ...
