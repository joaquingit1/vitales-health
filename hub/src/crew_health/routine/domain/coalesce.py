from collections.abc import Sequence
from datetime import datetime

from crew_health.routine.domain.occurrence import Occurrence
from crew_health.routine.domain.vocabulary import OPEN_STATES


def absorbing(
    open_occurrences: Sequence[Occurrence],
    *,
    item_id: str,
    person_id: str,
    due_from: datetime,
    due_until: datetime,
    repeat: bool,
) -> str | None:
    for occ in sorted(open_occurrences, key=lambda o: o.occurrence_id):
        if occ.state not in OPEN_STATES or occ.item_id != item_id or occ.person_id != person_id:
            continue
        if repeat and occ.due_kind == "all_day":
            continue
        if (
            occ.due_from is not None
            and occ.due_from <= due_from
            and (occ.due_until is None or occ.due_until >= due_until)
        ):
            return occ.occurrence_id
    return None
