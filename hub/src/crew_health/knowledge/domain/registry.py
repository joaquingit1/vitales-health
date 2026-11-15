from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class DeclaredSource:
    source_id: str
    title: str
    url: str
    date_text: str | None = None
    access_text: str | None = None
    saved_as: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSummary:
    source_id: str
    title: str
    url: str
    date_text: str | None
    access_text: str | None
    saved_as: str | None
    urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceUnknown:
    source_id: str


@dataclass(frozen=True, slots=True)
class ResolutionReport:
    resolved: tuple[SourceSummary, ...]
    unknown: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Membership:
    inventory_id: str
    source_entry_id: str
    title: str
    source_url: str
    row_sha256: str
    published_mapping: str
    title_match_mapping: str


@dataclass(frozen=True, slots=True)
class Condition:
    condition_id: str
    title: str
    status: Literal["active", "removed_or_split"]
    nasa_url: str
    family: str | None
    workup_source_ids: tuple[str, ...]
    workup_review_required: bool
    assessment: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RegistryCounts:
    active: int
    memberships: int
    inventory_counts: tuple[tuple[str, int], ...]
    unique_diseases: None = None


@dataclass(frozen=True, slots=True)
class ConditionResolution:
    condition: Condition
    nasa_page: str
    form_url: str | None
    plan_id: str | None
    written_for: str | None
    kit_review_status: str | None
    plan_review_status: str | None
    card_review_status: str | None
    config_release_id: str | None
    evidence_release_id: str | None


@dataclass(frozen=True, slots=True)
class Active:
    condition: Condition


@dataclass(frozen=True, slots=True)
class RemovedOrSplit:
    condition: Condition


@dataclass(frozen=True, slots=True)
class UnknownCondition:
    condition_id: str


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    sha256: str
    sources: tuple[SourceSummary, ...]
    conditions: tuple[Condition, ...]
    memberships: tuple[Membership, ...]
    inventory_counts: tuple[tuple[str, int], ...]
