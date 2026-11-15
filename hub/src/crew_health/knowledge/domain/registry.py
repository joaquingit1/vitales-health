from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from crew_health.knowledge.domain.errors import EvidenceInvalid, require_hash


@dataclass(frozen=True, slots=True)
class Condition:
    condition_id: str
    title: str
    status: Literal["active", "removed_or_split"]
    nasa_page: str
    family: str | None
    assessment_fields: tuple[tuple[str, str | None], ...]
    workup_source_ids: tuple[str, ...]
    workup_review_required: bool

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"ICL[1-9][0-9]{0,2}[A-Z]?", self.condition_id) is None
            or not self.title
            or not self.nasa_page
        ):
            raise EvidenceInvalid("condition")
        if self.status not in ("active", "removed_or_split"):
            raise EvidenceInvalid("condition_status")
        for source_id in self.workup_source_ids:
            require_source_id(source_id)


@dataclass(frozen=True, slots=True)
class Membership:
    inventory_id: str
    source_entry_id: str
    published_title: str
    source_url: str
    original_row_sha256: str
    published_mapping_raw: str
    published_mapping: tuple[str, ...]
    title_match_mapping: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeclaredSource:
    source_id: str
    title: str
    url: str
    declaring_input: str
    date_text: str | None
    access_text: str | None
    saved_as: str | None

    def __post_init__(self) -> None:
        require_source_id(self.source_id)
        if not self.title or not self.url or not self.declaring_input:
            raise EvidenceInvalid("source")


def require_source_id(value: str) -> None:
    if re.fullmatch(r"(?:EV|RT|RS|SK|S|C|N|L)[0-9]{2}", value) is None:
        raise EvidenceInvalid("source_id")


@dataclass(frozen=True, slots=True)
class SourceSummary:
    source_id: str
    title: str
    url: str
    date_text: str | None
    access_text: str | None
    saved_as: str | None
    catalog_document_key: str
    titles: tuple[str, ...]
    declaring_inputs: tuple[str, ...]
    documents: tuple[DeclaredSource, ...]
    catalog_document_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceUnknown:
    source_id: str


@dataclass(frozen=True, slots=True)
class RegistryUnavailable:
    reason: str


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
class ResolutionReport:
    resolved: tuple[SourceSummary, ...]
    unknown: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegistryCounts:
    memberships_total: int
    active_conditions: int
    removed_or_split: int
    distinct_titles: int


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    input_manifest_sha256: str
    conditions: tuple[Condition, ...]
    memberships: tuple[Membership, ...]
    sources: tuple[DeclaredSource, ...]
    families: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]

    def __post_init__(self) -> None:
        require_hash(self.input_manifest_sha256)
        identifiers = tuple(c.condition_id for c in self.conditions)
        if len(set(identifiers)) != len(identifiers):
            raise EvidenceInvalid("duplicate_condition")
        memberships = tuple((m.inventory_id, m.source_entry_id) for m in self.memberships)
        if len(set(memberships)) != len(memberships):
            raise EvidenceInvalid("duplicate_membership")
        for member in self.memberships:
            require_hash(member.original_row_sha256)
