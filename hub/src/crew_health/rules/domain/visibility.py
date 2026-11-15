"""Who sees a warning and which fields (E24 3.10, S8.3, D9, R-E24-21).

Authorization comes from the session and from explicit grants, never from a role label alone. Habitat
and crew warnings are visible to every session kind that may call the warning operations. A person
warning is visible to its owner and, fail closed, to nobody else unless a current explicit grant for this
warning exists; the grant names the fields it covers and nothing outside them is returned. Being a
helper, a station session or an officer is not a grant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from crew_health.rules.domain.vocabulary import Scope
from crew_health.rules.domain.warning import Warning

type ViewerKind = Literal["crew", "station", "ground", "operator", "adapter", "editor"]  # internal enum

# The fields of the wire `Warning`; storage-only fields (pin, trigger, fingerprints, scope key) are never exposed.
WIRE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "warning_id", "revision", "definition_id", "config_release_id", "level", "raised_by", "hazard_id",
        "limit_id", "sensor_type_id", "channel_id", "scope", "person_id", "environment_id", "location",
        "origin_mode", "title", "what", "why", "steps", "body_regions", "headline", "evidence",
        "evidence_time", "evidence_time_label", "citation", "insight_id", "state", "raised_at",
        "acknowledgements", "cleared_at", "clear_reason", "resolved", "dismissed", "notes",
        "previous_warning_id", "routine_override_ids",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Viewer:
    kind: ViewerKind
    person_id: str | None


@dataclass(frozen=True, slots=True)
class ReadGrant:
    """A current explicit grant to read one warning, limited to the named fields."""

    warning_id: str
    fields: frozenset[str]


@dataclass(frozen=True, slots=True)
class Visibility:
    visible: bool
    full: bool
    fields: frozenset[str]


_NONE: Final = Visibility(False, False, frozenset())


def can_see(w: Warning, viewer: Viewer, grant: ReadGrant | None = None) -> Visibility:
    if w.scope is not Scope.PERSON:
        return Visibility(True, True, WIRE_FIELDS)
    if viewer.kind == "crew" and viewer.person_id is not None and viewer.person_id == w.person_id:
        return Visibility(True, True, WIRE_FIELDS)
    if grant is not None and grant.warning_id == w.warning_id:
        covered = grant.fields & WIRE_FIELDS
        if covered:
            return Visibility(True, covered == WIRE_FIELDS, covered)
    return _NONE


def project_fields(w: Warning, visibility: Visibility) -> Mapping[str, object]:
    """Only the fields the grant covers, in sorted order."""
    if not visibility.visible:
        return {}
    return {name: getattr(w, name) for name in sorted(visibility.fields)}
