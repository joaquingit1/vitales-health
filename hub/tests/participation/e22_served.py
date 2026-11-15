import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient, Response

from crew_health.contracts_gen.enums import Role
from crew_health.identity.domain.roster import ProvisionedPrincipal
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.participation.application.consent import ConsentCommands
from crew_health.participation.application.ports import CompletionRecorder, IntakeOutcome, ParticipationSettle
from crew_health.participation.domain.types import CompletionBasis

__all__ = ["Hub", "at_day", "basis", "bearer", "hub_at", "problem", "run", "session_of", "sign_in", "start"]

# The served E02 station lives with the privacy tests, which are not a package: it is loaded by name (conftest.py
# puts its directory on sys.path) and typed here, so the participation tests stay fully typed.
_station: Any = import_module("e02_station")
_http: Any = import_module("e02_http")
MISSION_EPOCH: datetime = _station.MISSION_EPOCH
type Journey = Callable[[AsyncClient], Awaitable[None]]


def mission_id() -> str:
    return str(_station.mission_id())


def run(running: Any, journey: Journey) -> None:
    _http.run(running, journey)


async def sign_in(
    client: AsyncClient, person: str | None, *, kind: str = "crew", secret: str | None = None
) -> str:
    return str(await _http.sign_in(client, person, kind=kind, secret=secret))


def bearer(token: str, **headers: str) -> dict[str, str]:
    result: dict[str, str] = _http.bearer(token, **headers)
    return result


def problem(response: Response, status: int, code: str) -> None:
    _http.problem(response, status, code)


EDITOR = ProvisionedPrincipal("editor_sim", "editor", "Editor")
OPERATOR = ProvisionedPrincipal("operator_sim", "operator", "Operator")


def at_day(day: int, hours: int = 2) -> datetime:
    """An instant inside mission day `day` (day 1 began at the SIM mission epoch)."""
    return MISSION_EPOCH + timedelta(days=day - 1, hours=hours)


def session_of(person: str) -> SessionContext:
    return SessionContext(
        "session_" + person, "crew", Actor("person", person), person, frozenset({Role.CREW})
    )


def basis(person: str, day: int, ref: str | None = None, *, hours: int = 2) -> CompletionBasis:
    return CompletionBasis(
        basis="daily_check",
        basis_ref=ref or f"dck_{person}_{day}",
        patient_id=person,
        mission_id=mission_id(),
        origin_mode="SIM",
        occurred_at=at_day(day, hours),
        original_offset=None,
        actor_person_id=person,
        cause_kind=None,
        cause_ref=None,
        required_answers_complete=True,
    )


@dataclass
class Hub:
    station: Any
    running: Any

    @property
    def app(self) -> FastAPI:
        app: FastAPI = self.running.app
        return app

    def require[P](self, port: type[P]) -> P:
        result: P = self.app.state.runtime.resolver.require(port)
        return result

    def set_day(self, day: int, hours: int = 3) -> None:
        self.station.clock.at = at_day(day, hours)

    def produce(self, value: CompletionBasis) -> IntakeOutcome:
        """The producer's own transaction: the intent commits with it, then the producer settles."""
        recorder = self.require(CompletionRecorder)  # type: ignore[type-abstract]
        with self.station.database.begin(
            actor=Actor("person", value.patient_id), correlation_id="producer_" + uuid.uuid4().hex
        ) as uow:
            outcome = recorder.record(uow, value)
        if outcome.intent_key is not None:
            self.require(ParticipationSettle).settle(outcome.intent_key)  # type: ignore[type-abstract]
        return outcome

    def consent(self) -> ConsentCommands:
        return self.require(ConsentCommands)


def hub_at(directory: Path, day: int, *, staff: bool = False) -> Hub:
    # The harness sets every integer setting to its registered maximum; the privacy floor is its own minimum, 3.
    hub = _station.station(directory, settings={"participation.min_counting_members": 3})
    running = _station.serve(hub, seed=not staff)
    if staff:
        _station.seed_roster(
            running, _station.sim_bootstrap(principals=(_station.sim_station_principal(), EDITOR, OPERATOR))
        )
    result = Hub(hub, running)
    result.set_day(day)
    return result


def start(directory: Path, day: int = 2, *, staff: bool = False) -> Hub:
    return hub_at(directory, day, staff=staff)
