"""Ports records provides to other modules (SYSTEM 4: `ObservationCommands`, consumers teach, measurement and
checks; E01 `AnswerCommands`). Consumers import these classes, so the registry resolves them by identity (SYSTEM 6.4).

This file imports nothing from records.domain: a consumer importing it must not reach records' domain layer through
it, which the generated independence contracts forbid. R-E01-17 is still pending, so each method keeps the signature
its consumer on main calls today; the request settles one canonical shape."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import UnitOfWork

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]  # internal enum


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    """A crewmate-entered observation (E30 teach) appended in the caller's unit of work."""

    patient_id: str
    operator_id: str
    episode_id: str | None
    code: str
    value: Quantity
    observed_at: datetime
    entered_at: datetime
    origin_mode: OriginMode
    acquisition_method: Literal["crewmate_palpation"]  # the only method teach writes
    quality_status: Literal["accepted"]
    quality_rule_version: str
    body_region: str | None
    context: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class AttemptObservationDraft:
    """A validated measurement attempt value (E05 measurement) appended in the attempt's unit of work."""

    patient_id: str
    episode_id: str | None
    code: str
    quantity: Quantity
    observed_at: datetime
    origin_mode: OriginMode
    acquisition_method: str
    device_id: str | None
    context: Mapping[str, str]
    body_region: str | None
    supersedes: str | None
    measurement_request_id: str
    attempt_id: str
    operator_id: str | None


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    record_id: str
    question_id: str
    questionnaire_version: str
    presence: Literal["present", "absent", "unknown"]  # internal enum
    value: str
    body_region: str | None
    actor_person_id: str
    answered_at: datetime
    origin_mode: OriginMode
    supersedes: str | None
    episode_id: str | None


class ObservationCommands(Protocol):
    def canonical_unit(self, code: str) -> Unit: ...
    def append_observation(
        self, uow: UnitOfWork, draft: ObservationDraft, *, idempotency_key: str
    ) -> str: ...
    def append_for_attempt(self, uow: UnitOfWork, draft: AttemptObservationDraft) -> str: ...


class AnswerCommands(Protocol):
    def append_answer(self, uow: UnitOfWork, draft: AnswerDraft, *, idempotency_key: str) -> str: ...
