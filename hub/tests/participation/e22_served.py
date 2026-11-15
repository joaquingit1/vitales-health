"""Helpers for the served-app tests: the E02 station (real identity, clock, configuration and HTTP over SQLite) with
the participation module composed in, producers that call the real `CompletionRecorder` the way E03 and E15 will."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from e02_http import run
from e02_station import (
    MISSION_EPOCH,
    E02Station,
    Running,
    mission_id,
    seed_roster,
    serve,
    sim_bootstrap,
    sim_station_principal,
    station,
)
from fastapi import FastAPI

from crew_health.contracts_gen.enums import Role
from crew_health.identity.domain.roster import ProvisionedPrincipal
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.participation.application.consent import ConsentCommands
from crew_health.participation.application.ports import CompletionRecorder, IntakeOutcome, ParticipationSettle
from crew_health.participation.domain.types import CompletionBasis

__all__ = ["Hub", "at_day", "basis", "hub_at", "run", "session_of", "start"]

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
    station: E02Station
    running: Running

    @property
    def app(self) -> FastAPI:
        return self.running.app

    def require[P](self, port: type[P]) -> P:
        return self.app.state.runtime.resolver.require(port)  # type: ignore[no-any-return]

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
    hub = station(directory, settings={"participation.min_counting_members": 3})
    running = serve(hub, seed=not staff)
    if staff:
        seed_roster(running, sim_bootstrap(principals=(sim_station_principal(), EDITOR, OPERATOR)))
    result = Hub(hub, running)
    result.set_day(day)
    return result


def start(directory: Path, day: int = 2, *, staff: bool = False) -> Hub:
    return hub_at(directory, day, staff=staff)
