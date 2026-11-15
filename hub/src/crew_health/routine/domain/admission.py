from dataclasses import dataclass

from crew_health.routine.domain.vocabulary import OriginMode


@dataclass(frozen=True, slots=True)
class Admission:
    ok: bool
    code: str | None


def admissible(
    *, mission_mode: OriginMode, input_origin: OriginMode, group_audience: bool, on_roster: bool | None
) -> Admission:
    if on_roster is False:
        return Admission(False, "routine.not_on_roster")
    accepted = input_origin == mission_mode
    if mission_mode == "SIM":
        accepted = input_origin in ("SIM", "REPLAY") or (input_origin == "LIVE" and not group_audience)
    return Admission(True, None) if accepted else Admission(False, "routine.origin_rejected")
