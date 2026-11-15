from collections.abc import Callable

from crew_health.routine.domain.vocabulary import Audience, MemberBasis


def members(
    audience: Audience,
    *,
    subject_ids: tuple[str, ...],
    roster: tuple[str, ...] | None,
    location_of: Callable[[str], str | None] | None,
    source_location: str | None,
    walking: frozenset[str],
) -> tuple[tuple[str, MemberBasis], ...] | None:
    if audience == "person":
        return tuple((p, "subject") for p in sorted(set(subject_ids)))
    if roster is None:
        return None
    if audience == "all_crew":
        return tuple((p, "site") for p in sorted(set(roster)))
    if location_of is None or source_location is None:
        return tuple((p, "site") for p in sorted(set(roster) - walking))
    result: list[tuple[str, MemberBasis]] = []
    for person in sorted(set(roster)):
        location = location_of(person)
        if location == source_location:
            result.append((person, "location"))
        elif location is None:
            result.append((person, "location_unknown"))
    return tuple(result)
