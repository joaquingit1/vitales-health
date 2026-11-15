"""The demo hub's mission facts while E23 (crew_health.mission) is not on main: SYSTEM 4 `MissionStatePort.current()`
for every consumer that holds a structural copy of it (records, measurement, camera, roles, ground link, help, kit
map), answered from the demo fixture and the demo environment switch.

Only `crew-health demo serve` registers it. A station hub keeps the fail-closed stand-ins of app/station.py. The
active environment is the one the operator's `demo cue environment-*` last set (VALIDATION 3 act 3, "entered through
the demo control"); it is read from the data directory on every call, so the switch needs no restart. Mara's
confirmation before it takes effect (D18, D33) is E23's and is not modeled here.

It is also E23's OverlayPort (`active`) for roles: the activity overlays the demo control recorded in the data
directory (`demo reset` records none; `demo cue eva-*` starts one). A missing or unreadable record raises, which roles
reports as Unknown operators, never as nobody away."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.roles.application.ports import OverlayFact

type Origin = Literal["LIVE", "REPLAY", "SIM"]  # internal enum

ENVIRONMENT_FILE = "environment.json"
MAX_ENVIRONMENT_BYTES = 4096
OVERLAYS_FILE = "overlays.json"
MAX_OVERLAYS_BYTES = 65536


@dataclass(frozen=True, slots=True)
class DemoMissionFacts:
    mission_id: str
    mission_mode: Origin
    active_environment_id: str
    planned_length_days: int | None
    site_node_id: str


def write_environment(directory: Path, environment_id: str, *, source: str, at: datetime) -> None:
    """The demo control: the active environment from now on, with its source and when it was set."""
    record = {"environment_id": environment_id, "source": source, "set_at": at.isoformat()}
    path = directory / ENVIRONMENT_FILE
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(record), encoding="utf-8")
    staged.replace(path)


def write_overlays(directory: Path, overlays: Sequence[Mapping[str, object]]) -> None:
    """The demo control's overlay record (E23 ActivityOverlay fields): replaced atomically, as the environment is."""
    path = directory / OVERLAYS_FILE
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps({"overlays": list(overlays)}), encoding="utf-8")
    staged.replace(path)


def read_overlays(directory: Path) -> list[dict[str, object]]:
    """The recorded overlays. A missing, oversized or malformed record fails closed: overlays unknown, never none."""
    try:
        with (directory / OVERLAYS_FILE).open("rb") as handle:
            raw = handle.read(MAX_OVERLAYS_BYTES + 1)
        rows = json.loads(raw)["overlays"]
    except OSError, ValueError, KeyError, TypeError:
        raise ProblemError(
            ErrorCode.CAPABILITY_UNAVAILABLE, "demo overlays unavailable", retryable=False
        ) from None
    if (
        len(raw) > MAX_OVERLAYS_BYTES
        or not isinstance(rows, list)
        or not all(isinstance(r, dict) for r in rows)
    ):
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "demo overlays invalid", retryable=False)
    return [dict(r) for r in rows]


def _instant(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "demo overlay time invalid", retryable=False)
    return datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class DemoMission:
    directory: Path
    mission_id: str
    mode: Origin
    planned_length_days: int | None
    site_node_id: str

    def _environment(self) -> str:
        try:
            with (self.directory / ENVIRONMENT_FILE).open("rb") as handle:
                raw = handle.read(MAX_ENVIRONMENT_BYTES + 1)
            value = json.loads(raw)["environment_id"]
        except OSError, ValueError, KeyError, TypeError:
            raise ProblemError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "demo environment unavailable", retryable=False
            ) from None
        if len(raw) > MAX_ENVIRONMENT_BYTES or not isinstance(value, str) or not value:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "demo environment invalid", retryable=False)
        return value

    def current(self, uow: object = None) -> DemoMissionFacts:
        return DemoMissionFacts(
            self.mission_id, self.mode, self._environment(), self.planned_length_days, self.site_node_id
        )

    # kitmap MissionStatePort
    def mission_mode(self, uow: object = None) -> Origin:
        return self.mode

    def active_environment_id(self, uow: object = None) -> str:
        return self._environment()

    # roles OverlayPort (E23 4.2 `active`): overlays active at `at`, per person asked
    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[OverlayFact]]:
        wanted = set(person_ids)
        found: dict[str, list[OverlayFact]] = {}
        for row in read_overlays(self.directory):
            fact = OverlayFact(
                overlay_id=str(row["overlay_id"]),
                person_id=str(row["person_id"]),
                kind=str(row["overlay"]),
                state=str(row["state"]),
                planned_start=_instant(row.get("planned_start")),
                started_at=_instant(row.get("started_at")),
                ended_at=_instant(row.get("ended_at")),
            )
            started = fact.started_at is not None and fact.started_at <= at
            if fact.person_id in wanted and fact.state == "active" and started:
                found.setdefault(fact.person_id, []).append(fact)
        return {person: tuple(facts) for person, facts in sorted(found.items())}

    # roles MissionStatePort: day boundaries need E23's environment day, which is not modeled here
    def day_end(self, instant: datetime) -> datetime:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)
