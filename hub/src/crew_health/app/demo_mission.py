"""The demo hub's mission facts while E23 (crew_health.mission) is not on main: SYSTEM 4 `MissionStatePort.current()`
for every consumer that holds a structural copy of it (records, measurement, camera, roles, ground link, help, kit
map), answered from the demo fixture and the demo environment switch.

Only `crew-health demo serve` registers it. A station hub keeps the fail-closed stand-ins of app/station.py. The
active environment is the one the operator's `demo cue environment-*` last set (VALIDATION 3 act 3, "entered through
the demo control"); it is read from the data directory on every call, so the switch needs no restart. Mara's
confirmation before it takes effect (D18, D33) is E23's and is not modeled here."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError

type Origin = Literal["LIVE", "REPLAY", "SIM"]  # internal enum

ENVIRONMENT_FILE = "environment.json"
MAX_ENVIRONMENT_BYTES = 4096


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

    # roles MissionStatePort: day boundaries need E23's environment day, which is not modeled here
    def day_end(self, instant: datetime) -> datetime:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)
