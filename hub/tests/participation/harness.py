"""Shared helpers for the participation domain tests.

Rules come from the repository's configuration document; the independent golden cases come from the package
(CONTRACTS section 20 and the package examples). No clinical or label content is typed here."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from hypothesis import settings
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.kernel.met import MetLabel, MissionClockVersion
from crew_health.participation.domain.identity import completion_id, intent_key, logical_identity
from crew_health.participation.domain.rules import ParticipationRules, parse_rules
from crew_health.participation.domain.types import CompletionBasis, CompletionFact

REPO: Final[Path] = Path(__file__).resolve().parents[3]
CONFIG: Final[Path] = REPO / "config" / "participation-rules.json"
EXAMPLES: Final[Path] = REPO / "docs" / "product" / "api" / "examples"
SCHEMAS: Final[Path] = REPO / "docs" / "product" / "api" / "schemas"
EPOCH: Final[datetime] = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
DAY: Final[timedelta] = timedelta(days=1)


def config_document() -> dict[str, object]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def example_value(relative: str) -> dict[str, object]:
    return json.loads((EXAMPLES / relative).read_text(encoding="utf-8"))["value"]  # type: ignore[no-any-return]


def rules() -> ParticipationRules:
    return parse_rules(config_document())


def at_day(day: int, hours: float = 12.0) -> datetime:
    """An instant inside mission day `day` (day 1 starts at EPOCH)."""
    return EPOCH + (day - 1) * DAY + timedelta(hours=hours)


@dataclass(frozen=True, slots=True)
class FakeClock:
    """A floor-division mission clock with versions, also for pre-flight days (the kernel memory clock rejects them)."""

    versions: tuple[MissionClockVersion, ...]

    def version_at(self, at: datetime) -> MissionClockVersion:
        found = [
            v
            for v in self.versions
            if v.effective_from <= at and (v.effective_until is None or at < v.effective_until)
        ]
        return max(found, key=lambda v: v.generation)

    def mission_day(self, at: datetime, *, version: str | None = None) -> int:
        chosen = (
            self.version_at(at)
            if version is None
            else next(v for v in self.versions if v.clock_version == version)
        )
        return int((at - chosen.epoch) // DAY) + 1

    def met(self, at: datetime, *, version: str | None = None) -> MetLabel:
        chosen = (
            self.version_at(at)
            if version is None
            else next(v for v in self.versions if v.clock_version == version)
        )
        day = self.mission_day(at, version=version)
        return MetLabel(str(at - chosen.epoch), day, f"Day {day}", chosen.clock_version)

    def label(self, at: datetime, *, version: str | None = None) -> tuple[str, int]:
        value = self.met(at, version=version)
        return value.day_label, value.mission_day

    def day_bounds(self, mission_day: int, *, version: str) -> tuple[datetime, datetime]:
        chosen = next(v for v in self.versions if v.clock_version == version)
        return chosen.epoch + (mission_day - 1) * DAY, chosen.epoch + mission_day * DAY


def clock_version(
    name: str = "clock-1",
    *,
    generation: int = 1,
    epoch: datetime = EPOCH,
    effective_from: datetime = datetime(2000, 1, 1, tzinfo=UTC),
    effective_until: datetime | None = None,
) -> MissionClockVersion:
    return MissionClockVersion(name, generation, epoch, effective_from, effective_until, "Day {day}")


def clock(*versions: MissionClockVersion) -> FakeClock:
    return FakeClock(versions or (clock_version(),))


def basis(
    kind: str = "daily_check",
    *,
    ref: str = "dck_1",
    patient: str = "mara",
    origin: str = "SIM",
    when: datetime | None = None,
    actor: str | None = "mara",
    complete: bool | None = None,
    cause_kind: str | None = None,
    cause_ref: str | None = None,
    offset: str | None = "+00:00",
) -> CompletionBasis:
    if kind == "daily_check" and complete is None:
        complete = True
    if kind == "episode_updated" and cause_kind is None:
        cause_kind, cause_ref = "answer", "ans_1"
    return CompletionBasis(
        basis=kind,  # type: ignore[arg-type]
        basis_ref=ref,
        patient_id=patient,
        mission_id="demo-mission",
        origin_mode=origin,  # type: ignore[arg-type]
        occurred_at=when or at_day(1),
        original_offset=offset,
        actor_person_id=actor,
        cause_kind=cause_kind,  # type: ignore[arg-type]
        cause_ref=cause_ref,
        required_answers_complete=complete,
    )


def fact(day: int, *, patient: str = "mara", ref: str | None = None, hours: float = 12.0) -> CompletionFact:
    key = intent_key(
        logical_identity(basis(ref=ref or f"dck_{patient}_{day}", patient=patient, when=at_day(day, hours)))
    )
    return CompletionFact(completion_id(key), patient, day, at_day(day, hours))


PROP = settings(max_examples=1000, deadline=None)


def validator(schema_file: str) -> Draft202012Validator:
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8"))))
        for p in sorted(SCHEMAS.glob("*.schema.json"))
    ]
    registry: Registry = Registry().with_resources(resources)
    return Draft202012Validator(
        json.loads((SCHEMAS / schema_file).read_text(encoding="utf-8")), registry=registry
    )


def facts_for(days: Iterable[int], *, patient: str = "mara") -> list[CompletionFact]:
    return [fact(d, patient=patient) for d in sorted(set(days))]
