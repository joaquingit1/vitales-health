from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from crew_health_bench.domain.cases import ConfigPrecondition
from crew_health_bench.domain.documents import json_pointer, object_map, text
from crew_health_bench.domain.errors import BenchError

URGENCY_LEVELS = ("info", "watch", "act", "emergency")


def check_preconditions(requires: tuple[ConfigPrecondition, ...], documents: Mapping[str, object]) -> bool:
    for requirement in requires:
        value = json_pointer(documents, requirement.document, requirement.pointer)
        if (value is None) != (requirement.must_be == "null"):
            return False
    return True


@dataclass(frozen=True, slots=True)
class LabelConstraint:
    """A row of `suites.json` `label_constraints` (design 3.2): a decided label a case must carry."""

    family_id: str
    source: str
    expected_urgency: str


def label_constraints(document: Mapping[str, object]) -> tuple[LabelConstraint, ...]:
    """The rows of a loaded `suites.json`; a malformed or contradictory row refuses the document."""
    rows = document.get("label_constraints")
    if not isinstance(rows, list):
        raise BenchError("case_invalid")
    result: list[LabelConstraint] = []
    for value in rows:
        row = object_map(value)
        if row.keys() != {"family_id", "source", "expected_urgency"}:
            raise BenchError("case_invalid")
        constraint = LabelConstraint(
            text(row["family_id"]).strip(), text(row["source"]).strip(), text(row["expected_urgency"])
        )
        if (
            not constraint.family_id
            or not constraint.source
            or constraint.expected_urgency not in URGENCY_LEVELS
        ):
            raise BenchError("case_invalid")
        result.append(constraint)
    if len({(row.family_id, row.source) for row in result}) != len(result):
        raise BenchError("case_invalid")
    return tuple(result)


def require_independent_labels(
    source: str,
    source_ids: tuple[str, ...],
    urgency: str,
    *,
    family_id: str,
    constraints: tuple[LabelConstraint, ...],
) -> None:
    """Labels are golden cases (AGENTS rule 1): cited, never read from configuration, and true to decisions.

    A constraint applies to a case of its family whose label cites the constraint's source; the label's
    expected urgency must then equal the decided one (COORDINATOR-DECISIONS D2: storm shelter is emergency).
    """
    if not source_ids or source.casefold().startswith(("config:", "configuration:")):
        raise BenchError("case_invalid")
    for constraint in constraints:
        if (
            constraint.family_id == family_id
            and constraint.source in source
            and urgency != constraint.expected_urgency
        ):
            raise BenchError("case_invalid")
