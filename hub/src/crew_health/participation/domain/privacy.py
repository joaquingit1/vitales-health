"""Privacy between crew members (AGENTS hard rule 7; CONTRACTS 8 invariants): a person's streak, days and patches
reach only that person, and the crew goal serves one aggregate and nothing finer."""

from __future__ import annotations

from collections.abc import Mapping

from crew_health.participation.domain.errors import AggregateOnly, UnauthorizedScope


def require_owner(session_person_id: str | None, view_patient_id: str) -> None:
    """Only the session person may receive their own view. No session person (station, ground, operator) fails."""
    if session_person_id is None or session_person_id != view_patient_id:
        raise UnauthorizedScope("a participation view belongs to its person")


def require_no_selector(query: Mapping[str, str]) -> None:
    """The own streak and patch reads take no person and no other selector: any query parameter is refused."""
    if query:
        raise UnauthorizedScope("the own participation reads take no query")


def reject_breakdown(query: Mapping[str, str]) -> None:
    """The crew goal takes no query: any breakdown request (by person, person id, day) is AGGREGATE_ONLY."""
    if query:
        raise AggregateOnly("the crew goal returns the aggregate only")
