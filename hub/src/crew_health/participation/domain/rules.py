"""The participation rules document, parsed and checked (E22 section 8 loader check `participation_rules`).

The derivation is fixed code; only the values of this document are configuration. A document that fails any check is
rejected whole, so the last good release stays active."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from crew_health.participation.domain.errors import InvalidRules

MAX_THRESHOLDS: Final[int] = 16
MAX_WINDOW_DAYS: Final[int] = 31
_CONFIG_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BASES: Final[tuple[str, ...]] = ("sourced", "team default")
_SETTERS: Final[tuple[str, ...]] = ("clinical review", "the flight surgeon", "flight rules")
_PRACTICE_KINDS: Final[tuple[str, ...]] = ("first_drill", "drill_count", "skill_fresh")
_TOP_KEYS: Final[frozenset[str]] = frozenset(
    {"patch_thresholds", "crew_goal_window_days", "patch_designs", "derivation_version", "basis", "practice"}
)


@dataclass(frozen=True, slots=True)
class PatchDesign:
    threshold: int
    patch_design_id: str
    label: str


@dataclass(frozen=True, slots=True)
class PracticePatchDesign:
    kind: str
    threshold: int | None
    patch_design_id: str
    label: str


@dataclass(frozen=True, slots=True)
class PracticeRules:
    patch_designs: tuple[PracticePatchDesign, ...]
    core_skill_ids: tuple[str, ...]
    basis: str
    to_be_set_by: str | None


@dataclass(frozen=True, slots=True)
class ParticipationRules:
    patch_designs: tuple[PatchDesign, ...]
    crew_goal_window_days: int
    derivation_version: str
    basis: str
    practice: PracticeRules | None

    @property
    def patch_thresholds(self) -> tuple[int, ...]:
        return tuple(d.threshold for d in self.patch_designs)

    def design_for(self, threshold: int) -> PatchDesign | None:
        for design in self.patch_designs:
            if design.threshold == threshold:
                return design
        return None


def _int(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidRules(f"{name} must be an integer of at least {minimum}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidRules(f"{name} must be a non empty string")
    return value


def _config_id(value: object, name: str) -> str:
    if not isinstance(value, str) or _CONFIG_ID.fullmatch(value) is None:
        raise InvalidRules(f"{name} must be a config id")
    return value


def _sequence(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidRules(f"{name} must be an array")
    return value


def _mapping(value: object, name: str, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise InvalidRules(f"{name} must have exactly its contracted fields")
    return value


def _mission_designs(raw: object) -> tuple[PatchDesign, ...]:
    items = _sequence(raw, "patch_designs")
    if not items:
        raise InvalidRules("patch_designs must not be empty")
    designs = []
    for item in items:
        row = _mapping(item, "patch_design", frozenset({"threshold", "patch_design_id", "label"}))
        designs.append(
            PatchDesign(
                _int(row["threshold"], "threshold", minimum=1),
                _config_id(row["patch_design_id"], "patch_design_id"),
                _text(row["label"], "label"),
            )
        )
    return tuple(designs)


def _practice(raw: object) -> PracticeRules:
    keys = frozenset(
        {"counts", "drill_measurements_count", "patch_designs", "core_skill_ids", "basis", "to_be_set_by"}
    )
    body = _mapping(raw, "practice", keys)
    if body["counts"] != "drill_completed" or body["drill_measurements_count"] is not False:
        raise InvalidRules("practice counts only a completed drill and never its measurements")
    designs = []
    for item in _sequence(body["patch_designs"], "practice.patch_designs"):
        row = _mapping(
            item, "practice patch design", frozenset({"kind", "threshold", "patch_design_id", "label"})
        )
        if row["kind"] not in _PRACTICE_KINDS:
            raise InvalidRules("practice patch kind is unknown")
        threshold = row["threshold"]
        designs.append(
            PracticePatchDesign(
                str(row["kind"]),
                None if threshold is None else _int(threshold, "practice threshold", minimum=1),
                _config_id(row["patch_design_id"], "patch_design_id"),
                _text(row["label"], "label"),
            )
        )
    if not designs:
        raise InvalidRules("practice.patch_designs must not be empty")
    skills = tuple(
        _config_id(s, "core_skill_id") for s in _sequence(body["core_skill_ids"], "core_skill_ids")
    )
    setter = body["to_be_set_by"]
    if setter is not None and setter not in _SETTERS:
        raise InvalidRules("to_be_set_by is not a contract value")
    if body["basis"] not in _BASES or len(set(skills)) != len(skills):
        raise InvalidRules("practice basis or skills are invalid")
    return PracticeRules(tuple(designs), skills, str(body["basis"]), None if setter is None else str(setter))


def parse_rules(document: Mapping[str, object]) -> ParticipationRules:
    """Parse and check a participation-rules document; raise InvalidRules on any defect."""
    if (
        not isinstance(document, Mapping)
        or not {
            "patch_thresholds",
            "crew_goal_window_days",
            "patch_designs",
            "derivation_version",
            "basis",
        }
        <= set(document)
        <= _TOP_KEYS
    ):
        raise InvalidRules("document fields do not match the contract")
    thresholds = [
        _int(t, "patch_thresholds", minimum=1)
        for t in _sequence(document["patch_thresholds"], "patch_thresholds")
    ]
    designs = _mission_designs(document["patch_designs"])
    _check_thresholds(thresholds, designs)
    window = _int(document["crew_goal_window_days"], "crew_goal_window_days", minimum=1)
    if window > MAX_WINDOW_DAYS:
        raise InvalidRules("crew_goal_window_days is above its maximum")
    if document["basis"] not in _BASES:
        raise InvalidRules("basis is not a contract value")
    practice = _practice(document["practice"]) if "practice" in document else None
    if practice is not None:
        ids = {d.patch_design_id for d in designs}
        practice_ids = [d.patch_design_id for d in practice.patch_designs]
        if len(set(practice_ids)) != len(practice_ids) or ids & set(practice_ids):
            raise InvalidRules("practice design ids must be distinct from mission design ids")
    return ParticipationRules(
        tuple(sorted(designs, key=lambda d: d.threshold)),
        window,
        _text(document["derivation_version"], "derivation_version"),
        str(document["basis"]),
        practice,
    )


def _check_thresholds(thresholds: list[int], designs: tuple[PatchDesign, ...]) -> None:
    if not thresholds or len(thresholds) > MAX_THRESHOLDS:
        raise InvalidRules("patch_thresholds must hold between 1 and 16 values")
    if thresholds != sorted(set(thresholds)):
        raise InvalidRules("patch_thresholds must be ascending and unique")
    if sorted(d.threshold for d in designs) != thresholds:
        raise InvalidRules("every threshold needs exactly one design and every design one threshold")
    ids = [d.patch_design_id for d in designs]
    if len(set(ids)) != len(ids):
        raise InvalidRules("patch design ids must be distinct")
