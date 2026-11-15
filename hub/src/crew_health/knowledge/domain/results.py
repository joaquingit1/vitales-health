from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from crew_health.knowledge.domain.policy import AllowedUse
from crew_health.knowledge.domain.release import ReleaseMembership

type SearchMode = Literal["exact", "stemmed"]
type SearchMethod = Literal["lexical", "dense", "source", "context"]


@dataclass(frozen=True, slots=True)
class ActivePointer:
    release_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class NoActiveRelease:
    pass


@dataclass(frozen=True, slots=True)
class ReleaseUnavailable:
    release_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class NotApprovedForUse:
    release_id: str
    passage_id: str
    purpose: str


@dataclass(frozen=True, slots=True)
class InReleaseHistory:
    release_id: str
    passage_id: str
    purpose: str


@dataclass(frozen=True, slots=True)
class NotInRelease:
    release_id: str
    passage_id: str


@dataclass(frozen=True, slots=True)
class Eligible:
    membership: ReleaseMembership
    purpose: AllowedUse


@dataclass(frozen=True, slots=True)
class HistoryRead:
    items: tuple[ReleaseMembership, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RankedItem:
    membership: ReleaseMembership
    rank: int
    method: SearchMethod


@dataclass(frozen=True, slots=True)
class Hits:
    release_id: str
    items: tuple[RankedItem, ...]


@dataclass(frozen=True, slots=True)
class NoMatch:
    release_id: str


@dataclass(frozen=True, slots=True)
class IndexUnavailable:
    release_id: str
    method: SearchMethod
    reason: str


@dataclass(frozen=True, slots=True)
class QueryRejected:
    reason: str


type ApprovedRead = Eligible | NotApprovedForUse | InReleaseHistory | NotInRelease | ReleaseUnavailable
type SearchResult = Hits | NoMatch | NoActiveRelease | ReleaseUnavailable | IndexUnavailable | QueryRejected
