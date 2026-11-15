"""The pinned skills catalog as typed values (E30 3.1) with the structural loader checks C1 to C5 and the teach
parts of CONTRACTS 11 check 16. Every word, step, tolerance and band comes from the document; none from code."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal, cast

from crew_health.teach.domain.errors import CatalogShapeError

type StepKind = Literal["instruction", "count", "question"]
type PhraseKey = Literal["ok", "too_slow", "too_fast", "mismatch", "cannot_see", "no_reference"]

CONFIG_ID = r"[a-z][a-z0-9_]{0,63}"
PHRASE_KEYS: tuple[PhraseKey, ...] = ("ok", "too_slow", "too_fast", "mismatch", "cannot_see", "no_reference")
COUNT_PHRASES: tuple[PhraseKey, ...] = ("ok", "too_slow", "too_fast", "no_reference")
RATE_PHRASES: tuple[PhraseKey, ...] = ("ok", "too_slow", "too_fast", "cannot_see")


@dataclass(frozen=True, slots=True)
class Choice:
    answer: str
    label: str


@dataclass(frozen=True, slots=True)
class FlowStepRef:
    flow_id: str
    index: int


@dataclass(frozen=True, slots=True)
class TeachStep:
    step_no: int
    kind: StepKind
    actor: str
    text: str | None
    from_flow_step: FlowStepRef | None
    text_first_time: str | None
    landmark_id: str | None
    body_region: str | None
    environments: frozenset[str] | None
    count_seconds: int | None
    choices: tuple[Choice, ...]
    source_ids: tuple[str, ...]

    def applies_in(self, environment_id: str | None) -> bool:
        if self.environments is None:
            return True
        return environment_id is not None and environment_id in self.environments


@dataclass(frozen=True, slots=True)
class Tolerance:
    value: Fraction | None  # None: pending; the result can only be no_reference (I30-07)
    unit_words: str
    basis: str
    to_be_set_by: str | None
    raw_value: int | Decimal | None  # the document's number, echoed on the wire unchanged


@dataclass(frozen=True, slots=True)
class CountAgreementCheck:
    check_id: str
    experimental: bool
    reference_code: str
    reference_what: str
    tolerance: Tolerance | None
    phrases: Mapping[str, str | None]


@dataclass(frozen=True, slots=True)
class CompressionRateCheck:
    check_id: str
    experimental: bool
    target_min: Fraction
    target_max: Fraction
    target_unit_words: str
    update_interval_seconds: int
    phrases: Mapping[str, str | None]


@dataclass(frozen=True, slots=True)
class WhatItShows:
    does: str
    does_not: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    skill_id: str
    name: str
    short_name: str
    who_may: Literal["any_crew", "crew_medical_officer"]
    card_title: str
    steps: tuple[TeachStep, ...]
    count_check: CountAgreementCheck | None
    compression_check: CompressionRateCheck | None
    what_it_shows: WhatItShows | None
    review_state: str

    def steps_for(self, environment_id: str | None) -> tuple[TeachStep, ...]:
        return tuple(step for step in self.steps if step.applies_in(environment_id))

    def count_step(self, environment_id: str | None) -> TeachStep | None:
        counts = [s for s in self.steps_for(environment_id) if s.kind == "count"]
        return counts[0] if len(counts) == 1 else None


@dataclass(frozen=True, slots=True)
class Landmark:
    landmark_id: str
    body_region: str
    label: str


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    notice: str
    landmarks: Mapping[str, Landmark]
    skills: Mapping[str, SkillDefinition]

    def skill(self, skill_id: str) -> SkillDefinition | None:
        return self.skills.get(skill_id)

    @staticmethod
    def from_document(document: object) -> SkillCatalog:
        """Builds the catalog or raises CatalogShapeError; never another exception (T30-37)."""
        try:
            return _catalog(document)
        except CatalogShapeError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as error:
            raise CatalogShapeError("catalog_shape") from error


def _obj(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CatalogShapeError("object_expected")
    return cast(Mapping[str, object], value)


def _list(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise CatalogShapeError("array_expected")
    return cast(list[object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogShapeError("text_expected")
    return value


def _opt_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _int(value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CatalogShapeError("integer_expected")
    return value


def _number(value: object) -> Fraction:
    """Exact number from an int or a Decimal; floats, NaN and infinity are refused (rule 5)."""
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise CatalogShapeError("exact_number_expected")
    if isinstance(value, Decimal) and not value.is_finite():
        raise CatalogShapeError("finite_number_expected")
    return Fraction(value)


def _catalog(document: object) -> SkillCatalog:
    doc = _obj(document)
    landmarks: dict[str, Landmark] = {}
    for raw in _list(doc["landmarks"]):
        item = _obj(raw)
        mark = Landmark(_text(item["landmark_id"]), _text(item["body_region"]), _text(item["label"]))
        if mark.landmark_id in landmarks:
            raise CatalogShapeError("duplicate_landmark")
        landmarks[mark.landmark_id] = mark
    skills: dict[str, SkillDefinition] = {}
    for raw in _list(doc["skills"]):
        skill = _skill(_obj(raw), landmarks)
        if skill.skill_id in skills:
            raise CatalogShapeError("duplicate_skill")
        skills[skill.skill_id] = skill
    if not skills:
        raise CatalogShapeError("no_skills")
    return SkillCatalog(_text(doc["notice"]), landmarks, skills)


def _skill(item: Mapping[str, object], landmarks: Mapping[str, Landmark]) -> SkillDefinition:
    steps = tuple(_step(_obj(raw), landmarks) for raw in _list(item["teach_steps"]))
    if not steps or [s.step_no for s in steps] != list(range(1, len(steps) + 1)):
        raise CatalogShapeError("steps_not_numbered_from_1")
    count_checks: list[CountAgreementCheck] = []
    rate_checks: list[CompressionRateCheck] = []
    for raw in _list(item["checks"]):
        check = _obj(raw)
        kind = check["kind"]
        if kind == "count_agreement":
            count_checks.append(_count_check(check))
        elif kind == "compression_rate":
            rate_checks.append(_rate_check(check))
        elif kind != "hand_position":
            raise CatalogShapeError("unknown_check_kind")
    if len(count_checks) > 1 or len(rate_checks) > 1:
        raise CatalogShapeError("C1_one_check_per_kind")
    who_may = item["who_may"]
    if who_may not in ("any_crew", "crew_medical_officer"):
        raise CatalogShapeError("who_may")
    skill = SkillDefinition(
        skill_id=_text(item["skill_id"]),
        name=_text(item["name"]),
        short_name=_text(item["short_name"]),
        who_may=who_may,
        card_title=_text(item["card_title"]),
        steps=steps,
        count_check=count_checks[0] if count_checks else None,
        compression_check=rate_checks[0] if rate_checks else None,
        what_it_shows=_what(item["what_it_shows"]),
        review_state=_text(item["review_state"]),
    )
    if skill.count_check is not None:
        _check_count_layout(skill)
    for step in steps:
        if (
            step.kind == "question"
            and re.fullmatch(CONFIG_ID, question_id(skill.skill_id, step.step_no)) is None
        ):
            raise CatalogShapeError("C4_question_id_pattern")
    return skill


def _step(item: Mapping[str, object], landmarks: Mapping[str, Landmark]) -> TeachStep:
    kind = item["kind"]
    if kind not in ("instruction", "count", "question"):
        raise CatalogShapeError("step_kind")
    flow = item["from_flow_step"]
    flow_ref = None
    if flow is not None:
        flow_obj = _obj(flow)
        flow_ref = FlowStepRef(_text(flow_obj["flow_id"]), _int(flow_obj["index"], minimum=0))
    text = _opt_text(item["text"])
    if (text is None) == (flow_ref is None):
        raise CatalogShapeError("exactly_one_of_text_or_from_flow_step")
    count_seconds = item["count_seconds"]
    seconds = None if count_seconds is None else _int(count_seconds, minimum=1)
    if (kind == "count") != (seconds is not None):
        raise CatalogShapeError("count_seconds_only_on_count_steps")
    choices = tuple(Choice(_text(_obj(c)["answer"]), _text(_obj(c)["label"])) for c in _list(item["choices"]))
    if (kind == "question") != bool(choices):
        raise CatalogShapeError("choices_only_on_question_steps")
    landmark_id = _opt_text(item["landmark_id"])
    region = _opt_text(item["body_region"])
    if landmark_id is not None:
        mark = landmarks.get(landmark_id)
        if mark is None or mark.body_region != region:
            raise CatalogShapeError("landmark_in_step_region")
    environments = item["environments"]
    envs = None if environments is None else frozenset(_text(e) for e in _list(environments))
    return TeachStep(
        step_no=_int(item["step_no"], minimum=1),
        kind=kind,
        actor=_text(item["actor"]),
        text=text,
        from_flow_step=flow_ref,
        text_first_time=_opt_text(item["text_first_time"]),
        landmark_id=landmark_id,
        body_region=region,
        environments=envs,
        count_seconds=seconds,
        choices=choices,
        source_ids=tuple(_text(s) for s in _list(item["source_ids"])),
    )


def _phrases(check: Mapping[str, object], required: tuple[PhraseKey, ...]) -> Mapping[str, str | None]:
    raw = _obj(check["phrases"])
    phrases: dict[str, str | None] = {key: _opt_text(raw[key]) for key in PHRASE_KEYS}
    if any(phrases[key] is None for key in required):
        raise CatalogShapeError("C3_phrase_missing")
    return phrases


def _count_check(check: Mapping[str, object]) -> CountAgreementCheck:
    reference = _obj(check["reference"])
    tolerance_raw = check["tolerance"]
    if tolerance_raw is None:
        raise CatalogShapeError("count_agreement_needs_tolerance")
    tol = _obj(tolerance_raw)
    value_raw = tol["value"]
    value = None if value_raw is None else _number(value_raw)
    if value is not None and value < 0:
        raise CatalogShapeError("C5_tolerance_negative")
    raw_number = None if value_raw is None else cast(int | Decimal, value_raw)
    tolerance = Tolerance(
        value, _text(tol["unit"]), _text(tol["basis"]), _opt_text(tol["to_be_set_by"]), raw_number
    )
    return CountAgreementCheck(
        check_id=_text(check["check_id"]),
        experimental=_bool(check["experimental"]),
        reference_code=_text(reference["capability_code"]),
        reference_what=_text(reference["what"]),
        tolerance=tolerance,
        phrases=_phrases(check, COUNT_PHRASES),
    )


def _rate_check(check: Mapping[str, object]) -> CompressionRateCheck:
    target = _obj(check["target"])
    low, high = _number(target["min"]), _number(target["max"])
    if not 0 < low < high or not _list(target["source_ids"]):
        raise CatalogShapeError("C5_sourced_target_band")
    interval = _int(check["update_interval_seconds"], minimum=1)
    if interval > 60:
        raise CatalogShapeError("C5_update_interval")
    return CompressionRateCheck(
        check_id=_text(check["check_id"]),
        experimental=_bool(check["experimental"]),
        target_min=low,
        target_max=high,
        target_unit_words=_text(target["unit"]),
        update_interval_seconds=interval,
        phrases=_phrases(check, RATE_PHRASES),
    )


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise CatalogShapeError("boolean_expected")
    return value


def _what(value: object) -> WhatItShows | None:
    if value is None:
        return None
    item = _obj(value)
    return WhatItShows(
        _text(item["does"]), _text(item["does_not"]), tuple(_text(s) for s in _list(item["source_ids"]))
    )


def _check_count_layout(skill: SkillDefinition) -> None:
    """C2: exactly one count step per environment (and with the environment unknown) and only question steps
    after it."""
    environments: set[str | None] = {None}
    for step in skill.steps:
        environments.update(step.environments or ())
    for environment in sorted(environments, key=lambda e: e or ""):
        steps = skill.steps_for(environment)
        counts = [i for i, s in enumerate(steps) if s.kind == "count"]
        if len(counts) != 1 or any(s.kind != "question" for s in steps[counts[0] + 1 :]):
            raise CatalogShapeError("C2_count_step_layout")


def question_id(skill_id: str, step_no: int) -> str:
    """Interim question ID of a question step until X3 adds it to the catalog (C4 checks the pattern)."""
    return f"{skill_id}_q{step_no}"
