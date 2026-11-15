"""Configuration activation and the definition state of an active warning (E24 3.8, D1, D21).

An activation never clears, lowers or re-pins an active warning: a warning keeps the definition and
limit it was raised under and clears by that rule or by a medical officer's resolution with a reason.
New inputs use the new release for new raises. This module only classifies how an active warning stands
against the active release, so the projection can show "definition withdrawn" and so resolution can be
offered; it changes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from crew_health.rules.domain.decisions import Clear, Decision
from crew_health.rules.domain.definitions import WarningDefinition
from crew_health.rules.domain.errors import InvariantViolation
from crew_health.rules.domain.views import DefinitionStatus
from crew_health.rules.domain.vocabulary import EnvironmentId
from crew_health.rules.domain.warning import Warning


def definition_status(
    w: Warning,
    definitions: Mapping[str, WarningDefinition],
    fingerprints: Mapping[str, str],
    content_hashes: Mapping[str, str],
    *,
    environment_id: EnvironmentId,
) -> DefinitionStatus:
    """How the warning's own definition compares with the active release.

    A definition that no longer exists, or no longer applies in the active environment, is withdrawn.
    """
    current = definitions.get(w.definition_id)
    if current is None or environment_id not in current.environments:
        return "withdrawn"
    if fingerprints.get(w.definition_id) != w.rule_fingerprint:
        return "rule_changed"
    if content_hashes.get(w.definition_id) != w.content_hash:
        return "content_changed"
    return "current"


def forbid_clears(decision: Decision) -> Decision:
    """Guard for the decision of an activation or environment change: it must clear nothing (D1)."""
    for step in decision:
        if isinstance(step, Clear):
            raise InvariantViolation("an activation never clears or lowers an active warning")
    return decision


def withdrawn(
    active: Sequence[Warning],
    definitions: Mapping[str, WarningDefinition],
    fingerprints: Mapping[str, str],
    content_hashes: Mapping[str, str],
    *,
    environment_id: EnvironmentId,
) -> tuple[str, ...]:
    """Ids of active warnings whose definition is withdrawn, in sorted order."""
    found = [
        w.warning_id
        for w in active
        if definition_status(w, definitions, fingerprints, content_hashes, environment_id=environment_id)
        == "withdrawn"
    ]
    return tuple(sorted(found))
