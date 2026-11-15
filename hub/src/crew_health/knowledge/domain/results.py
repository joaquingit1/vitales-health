from __future__ import annotations

from dataclasses import dataclass

from crew_health.knowledge.domain.passage import AllowedUse, PathwayCard, ReleaseMembership


@dataclass(frozen=True, slots=True)
class Eligible:
    passage: ReleaseMembership
    release_id: str
    purpose: AllowedUse


@dataclass(frozen=True, slots=True)
class NotApprovedForUse:
    release_id: str
    passage_id: str
    purpose: AllowedUse


@dataclass(frozen=True, slots=True)
class InReleaseHistory:
    release_id: str
    passage_id: str


@dataclass(frozen=True, slots=True)
class NotInRelease:
    release_id: str
    passage_id: str


@dataclass(frozen=True, slots=True)
class NoActiveRelease:
    reason: str = "no_active_release"


@dataclass(frozen=True, slots=True)
class ReleaseUnavailable:
    release_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class IndexUnavailable:
    release_id: str
    method: str
    reason: str


@dataclass(frozen=True, slots=True)
class QueryRejected:
    reason: str


@dataclass(frozen=True, slots=True)
class RankedPassage:
    rank: int
    method: str
    passage: ReleaseMembership


@dataclass(frozen=True, slots=True)
class Hits:
    release_id: str
    items: tuple[RankedPassage, ...]


@dataclass(frozen=True, slots=True)
class NoMatch:
    release_id: str


@dataclass(frozen=True, slots=True)
class CardFound:
    release_id: str
    card: PathwayCard


type ApprovedRead = Eligible | NotApprovedForUse | InReleaseHistory | NotInRelease | ReleaseUnavailable
type SearchResult = Hits | NoMatch | NoActiveRelease | ReleaseUnavailable | IndexUnavailable | QueryRejected
type CardRead = CardFound | NotInRelease | ReleaseUnavailable
type HistoryRead = tuple[ReleaseMembership, ...] | ReleaseUnavailable
