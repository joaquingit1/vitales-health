"""Frozen plan content parsed defensively from already decoded JSON."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast

from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.domain.errors import PlansDocumentInvalid

StageId = Literal[
    "check_another_way", "do_now", "watch", "if_worse", "medical_officer", "return", "ground_log"
]
STAGE_ORDER: tuple[StageId, ...] = (
    "check_another_way",
    "do_now",
    "watch",
    "if_worse",
    "medical_officer",
    "return",
    "ground_log",
)
PlanMode = Literal["resolve", "talk_through", "stabilize", "prepare_return"]
WrittenFor = Literal["condition", "family", "flow"]
ReviewState = Literal["unreviewed", "reviewed"]


@dataclass(frozen=True, slots=True)
class Who:
    kind: Literal["any_crew", "crew_medical_officer", "skill"]
    skill_id: str | None


@dataclass(frozen=True, slots=True)
class SupplyInfo:
    supply_id: str
    unit: Unit
    consumable: bool


@dataclass(frozen=True, slots=True)
class SupplyNeed:
    plan_id: str
    step_id: str
    supply_id: str
    per_use: Quantity | None
    consumable: bool
    known: bool


@dataclass(frozen=True, slots=True)
class Needs:
    instruments: tuple[str, ...]
    supplies: tuple[SupplyNeed, ...]
    skills: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecheckSpec:
    cadence: str | None
    n: int | None
    interval_seconds: int | None
    basis: str
    to_be_set_by: str | None


@dataclass(frozen=True, slots=True)
class FormQuote:
    quote: str
    source_id: str
    page: int | None  # The contract permits an unknown page; never fabricate one.


@dataclass(frozen=True, slots=True)
class Step:
    step_id: str
    stage_id: StageId
    ordinal: int
    text: str
    text_first_time: str | None
    who: Who
    needs: Needs
    substitute_step_id: str | None
    substitute_only: bool
    environments: frozenset[str] | None
    body_region: str | None
    landmark_id: str | None
    guided_check_skill_id: str | None
    cannot_replace_text: str | None
    recheck: RecheckSpec | None
    opens_help: bool
    opens_flow_id: str | None
    medicine_named_as: str | None
    replaces_ground_task: FormQuote | None
    source_ids: tuple[str, ...]
    basis: str
    review_state: ReviewState
    content_sha256: str


@dataclass(frozen=True, slots=True)
class Criterion:
    text: str
    source_ids: tuple[str, ...]
    to_be_set_by: str | None


@dataclass(frozen=True, slots=True)
class ReturnStage:
    environments: frozenset[str]
    criteria: tuple[Criterion, ...]
    prepare: tuple[Step, ...]
    decided_by: tuple[str, ...]
    rtdc_json: str | None


@dataclass(frozen=True, slots=True)
class GroundLogItem:
    text: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    title: str
    condition_id: str | None
    flow_id: str | None
    written_for: WrittenFor
    mode: PlanMode
    environments: frozenset[str]
    stage_sequence: tuple[str, ...]
    steps: tuple[Step, ...]
    return_stage: ReturnStage | None
    ground_log_items: tuple[GroundLogItem, ...]
    ground_log_blocking: bool
    source_ids: tuple[str, ...]
    review_state: ReviewState
    written_by: str | None
    checked_by: str | None
    content_sha256: str

    def step(self, step_id: str) -> Step | None:
        return next((s for s in all_steps(self) if s.step_id == step_id), None)


@dataclass(frozen=True, slots=True)
class PlansDocument:
    version: str
    example: bool
    notice: str
    document_sha256: str
    plans: tuple[Plan, ...]
    rejected_plan_ids: tuple[str, ...]
    rejected_step_ids: tuple[tuple[str, str], ...]

    def plan(self, plan_id: str) -> Plan | None:
        return next((p for p in self.plans if p.plan_id == plan_id), None)

    def plan_for_condition(self, condition_id: str) -> Plan | None:
        return next((p for p in self.plans if p.condition_id == condition_id), None)

    def plan_for_flow(self, flow_id: str) -> Plan | None:
        return next((p for p in self.plans if p.flow_id == flow_id), None)


def all_steps(plan: Plan) -> tuple[Step, ...]:
    return plan.steps + (plan.return_stage.prepare if plan.return_stage else ())


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _object(value: object, pointer: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise PlansDocumentInvalid(pointer, "expected_object")
    return cast(Mapping[str, object], value)


def _shape(
    raw: Mapping[str, object], pointer: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> None:
    for key in required:
        if key not in raw:
            raise PlansDocumentInvalid(pointer + "/" + key, "missing_field")
    for key in raw:
        if key not in required + optional:
            raise PlansDocumentInvalid(
                pointer + "/" + key.replace("~", "~0").replace("/", "~1"), "unexpected_field"
            )


def _string(value: object, pointer: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlansDocumentInvalid(pointer, "expected_nonempty_string")
    return value


def _nullable(value: object, pointer: str) -> str | None:
    return None if value is None else _string(value, pointer)


def _bool(value: object, pointer: str) -> bool:
    if not isinstance(value, bool):
        raise PlansDocumentInvalid(pointer, "expected_boolean")
    return value


def _integer(value: object, pointer: str, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlansDocumentInvalid(pointer, "expected_integer")
    return value


def _array(value: object, pointer: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise PlansDocumentInvalid(pointer, "expected_array")
    return tuple(value)


def _strings(value: object, pointer: str) -> tuple[str, ...]:
    return tuple(_string(v, f"{pointer}/{i}") for i, v in enumerate(_array(value, pointer)))


def _choice(value: object, pointer: str, choices: tuple[str, ...]) -> str:
    result = _string(value, pointer)
    if result not in choices:
        raise PlansDocumentInvalid(pointer, "invalid_choice")
    return result


def _who(value: object, pointer: str) -> Who:
    text = _string(value, pointer)
    if text in ("any_crew", "crew_medical_officer"):
        return Who(cast(Literal["any_crew", "crew_medical_officer"], text), None)
    if re.fullmatch(r"skill:[a-z][a-z0-9_]{0,63}", text):
        return Who("skill", text[6:])
    raise PlansDocumentInvalid(pointer, "invalid_who")


def _recheck(value: object, pointer: str) -> RecheckSpec | None:
    if value is None:
        return None
    r = _object(value, pointer)
    _shape(r, pointer, ("cadence", "n", "interval_seconds", "basis", "to_be_set_by"))
    return RecheckSpec(
        _nullable(r["cadence"], pointer + "/cadence"),
        _integer(r["n"], pointer + "/n", 1),
        _integer(r["interval_seconds"], pointer + "/interval_seconds", 60),
        _string(r["basis"], pointer + "/basis"),
        _nullable(r["to_be_set_by"], pointer + "/to_be_set_by"),
    )


def _quote(value: object, pointer: str) -> FormQuote | None:
    if value is None:
        return None
    r = _object(value, pointer)
    _shape(r, pointer, ("quote", "source_id", "page"))
    return FormQuote(
        _string(r["quote"], pointer + "/quote"),
        _string(r["source_id"], pointer + "/source_id"),
        _integer(r["page"], pointer + "/page", 1),
    )


def _medicine(value: object, pointer: str) -> str | None:
    if value is None:
        return None
    r = _object(value, pointer)
    _shape(r, pointer, ("named_as",))
    return _choice(r["named_as"], pointer + "/named_as", ("as in the onboard procedure",))


def _need(value: object, pointer: str, pid: str, sid: str, supplies: Mapping[str, SupplyInfo]) -> SupplyNeed:
    r = _object(value, pointer)
    _shape(r, pointer, ("supply_id", "per_use", "basis", "to_be_set_by"))
    supply_id = _string(r["supply_id"], pointer + "/supply_id")
    _string(r["basis"], pointer + "/basis")
    _nullable(r["to_be_set_by"], pointer + "/to_be_set_by")
    amount = r["per_use"]
    quantity = None
    info = supplies.get(supply_id)
    if amount is not None:
        if isinstance(amount, bool) or not isinstance(amount, (int, float, Decimal)):
            raise PlansDocumentInvalid(pointer + "/per_use", "expected_positive_number")
        number = Decimal(str(amount))
        if not number.is_finite() or number <= 0:
            raise PlansDocumentInvalid(pointer + "/per_use", "expected_positive_number")
        if info is not None:
            quantity = Quantity(number, info.unit)
    return SupplyNeed(pid, sid, supply_id, quantity, info.consumable if info else False, info is not None)


def _needs(value: object, pointer: str, pid: str, sid: str, supplies: Mapping[str, SupplyInfo]) -> Needs:
    r = _object(value, pointer)
    _shape(r, pointer, ("instruments", "supplies", "skills"))
    return Needs(
        _strings(r["instruments"], pointer + "/instruments"),
        tuple(
            _need(v, f"{pointer}/supplies/{i}", pid, sid, supplies)
            for i, v in enumerate(_array(r["supplies"], pointer + "/supplies"))
        ),
        _strings(r["skills"], pointer + "/skills"),
    )


def _step_shape(raw: Mapping[str, object], pointer: str) -> None:
    _shape(
        raw,
        pointer,
        (
            "step_id",
            "text",
            "text_first_time",
            "who",
            "needs",
            "substitute_step_id",
            "substitute_only",
            "environments",
            "body_region",
            "landmark_id",
            "guided_check_skill_id",
            "cannot_replace_text",
            "recheck",
            "opens_help",
            "opens_flow_id",
            "medicine",
            "replaces_ground_task",
            "source_ids",
            "basis",
            "review_state",
        ),
    )


def _step(
    raw: Mapping[str, object],
    pointer: str,
    pid: str,
    stage: StageId,
    ordinal: int,
    supplies: Mapping[str, SupplyInfo],
) -> Step:
    _step_shape(raw, pointer)
    sid = _string(raw["step_id"], pointer + "/step_id")
    environments = raw["environments"]
    return Step(
        sid,
        stage,
        ordinal,
        _string(raw["text"], pointer + "/text"),
        _nullable(raw["text_first_time"], pointer + "/text_first_time"),
        _who(raw["who"], pointer + "/who"),
        _needs(raw["needs"], pointer + "/needs", pid, sid, supplies),
        _nullable(raw["substitute_step_id"], pointer + "/substitute_step_id"),
        _bool(raw["substitute_only"], pointer + "/substitute_only"),
        None if environments is None else frozenset(_strings(environments, pointer + "/environments")),
        _nullable(raw["body_region"], pointer + "/body_region"),
        _nullable(raw["landmark_id"], pointer + "/landmark_id"),
        _nullable(raw["guided_check_skill_id"], pointer + "/guided_check_skill_id"),
        _nullable(raw["cannot_replace_text"], pointer + "/cannot_replace_text"),
        _recheck(raw["recheck"], pointer + "/recheck"),
        _bool(raw["opens_help"], pointer + "/opens_help"),
        _nullable(raw["opens_flow_id"], pointer + "/opens_flow_id"),
        _medicine(raw["medicine"], pointer + "/medicine"),
        _quote(raw["replaces_ground_task"], pointer + "/replaces_ground_task"),
        _strings(raw["source_ids"], pointer + "/source_ids"),
        _string(raw["basis"], pointer + "/basis"),
        cast(
            ReviewState, _choice(raw["review_state"], pointer + "/review_state", ("unreviewed", "reviewed"))
        ),
        canonical_sha256(raw),
    )


def _criteria(value: object, pointer: str) -> tuple[Criterion, ...]:
    result = []
    for i, value_item in enumerate(_array(value, pointer)):
        p = f"{pointer}/{i}"
        item = _object(value_item, p)
        _shape(item, p, ("text", "source_ids", "to_be_set_by"))
        result.append(
            Criterion(
                _string(item["text"], p + "/text"),
                _strings(item["source_ids"], p + "/source_ids"),
                _nullable(item["to_be_set_by"], p + "/to_be_set_by"),
            )
        )
    return tuple(result)


def _rtdc(value: object, pointer: str) -> str | None:
    if value is None:
        return None
    raw = _object(value, pointer)
    ranges = ("treated_best", "treated_worst", "untreated_best", "untreated_worst")
    _shape(raw, pointer, (*ranges, "source_id", "page"))
    _string(raw["source_id"], pointer + "/source_id")
    if _integer(raw["page"], pointer + "/page", 1) is None:
        raise PlansDocumentInvalid(pointer + "/page", "expected_integer")
    for name in ranges:
        p = pointer + "/" + name
        interval = _object(raw[name], p)
        _shape(interval, p, ("min_pct", "max_pct"))
        for key in ("min_pct", "max_pct"):
            number = interval[key]
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise PlansDocumentInvalid(p + "/" + key, "expected_percentage")
            decimal = Decimal(str(number))
            if not decimal.is_finite() or decimal < 0 or decimal > 100:
                raise PlansDocumentInvalid(p + "/" + key, "expected_percentage")
    return canonical_json(raw)


def _stages(
    raw: Mapping[str, object],
    pointer: str,
    pid: str,
    supplies: Mapping[str, SupplyInfo],
    rejected: list[tuple[str, str]],
) -> tuple[tuple[str, ...], tuple[Step, ...], ReturnStage | None, tuple[GroundLogItem, ...], bool]:
    sequence: list[str] = []
    steps: list[Step] = []
    ret = None
    items: tuple[GroundLogItem, ...] = ()
    blocking = False
    ordinal = 0
    for i, v in enumerate(_array(raw["stages"], pointer + "/stages")):
        p = f"{pointer}/stages/{i}"
        stage = _object(v, p)
        if "stage_id" not in stage:
            raise PlansDocumentInvalid(p + "/stage_id", "missing_field")
        kind = cast(StageId, _choice(stage["stage_id"], p + "/stage_id", STAGE_ORDER))
        sequence.append(kind)
        if kind == "ground_log":
            _shape(stage, p, ("stage_id", "blocking", "items"))
            blocking = _bool(stage["blocking"], p + "/blocking")
            items = _ground_items(stage["items"], p + "/items")
            continue
        key = "prepare" if kind == "return" else "steps"
        _shape(
            stage,
            p,
            ("stage_id", "environments", "criteria", "prepare", "decided_by", "rtdc")
            if kind == "return"
            else ("stage_id", "steps"),
        )
        parsed = []
        for j, raw_step in enumerate(_array(stage[key], p + "/" + key)):
            sp = f"{p}/{key}/{j}"
            step = _object(raw_step, sp)
            if step.get("review_state") == "rejected":
                rejected.append((pid, _string(step.get("step_id"), sp + "/step_id")))
            else:
                parsed.append(_step(step, sp, pid, kind, ordinal, supplies))
            ordinal += 1
        if kind == "return":
            ret = ReturnStage(
                frozenset(_strings(stage["environments"], p + "/environments")),
                _criteria(stage["criteria"], p + "/criteria"),
                tuple(parsed),
                _strings(stage["decided_by"], p + "/decided_by"),
                _rtdc(stage["rtdc"], p + "/rtdc"),
            )
        else:
            steps.extend(parsed)
    return tuple(sequence), tuple(steps), ret, items, blocking


def _ground_items(value: object, pointer: str) -> tuple[GroundLogItem, ...]:
    result = []
    for i, v in enumerate(_array(value, pointer)):
        p = f"{pointer}/{i}"
        item = _object(v, p)
        _shape(item, p, ("text", "source_ids"))
        result.append(
            GroundLogItem(_string(item["text"], p + "/text"), _strings(item["source_ids"], p + "/source_ids"))
        )
    return tuple(result)


def _scope(value: object, pointer: str) -> tuple[str | None, str | None]:
    scope = _object(value, pointer)
    if tuple(scope) not in (("condition_id",), ("flow_id",)):
        raise PlansDocumentInvalid(pointer, "exactly_one_target")
    condition = _string(scope["condition_id"], pointer + "/condition_id") if "condition_id" in scope else None
    flow = _string(scope["flow_id"], pointer + "/flow_id") if "flow_id" in scope else None
    return condition, flow


def _plan(
    raw: Mapping[str, object],
    pointer: str,
    supplies: Mapping[str, SupplyInfo],
    rejected: list[tuple[str, str]],
) -> Plan:
    _shape(
        raw,
        pointer,
        (
            "plan_id",
            "title",
            "scope",
            "written_for",
            "mode",
            "environments",
            "stages",
            "source_ids",
            "review_state",
            "written_by",
            "checked_by",
        ),
    )
    pid = _string(raw["plan_id"], pointer + "/plan_id")
    condition, flow = _scope(raw["scope"], pointer + "/scope")
    sequence, steps, ret, items, blocking = _stages(raw, pointer, pid, supplies, rejected)
    ids = [s.step_id for s in steps + (ret.prepare if ret else ())]
    if len(ids) != len(set(ids)):
        raise PlansDocumentInvalid(pointer + "/stages", "duplicate_step_id")
    return Plan(
        pid,
        _string(raw["title"], pointer + "/title"),
        condition,
        flow,
        cast(
            WrittenFor, _choice(raw["written_for"], pointer + "/written_for", ("condition", "family", "flow"))
        ),
        cast(
            PlanMode,
            _choice(
                raw["mode"], pointer + "/mode", ("resolve", "talk_through", "stabilize", "prepare_return")
            ),
        ),
        frozenset(_strings(raw["environments"], pointer + "/environments")),
        sequence,
        steps,
        ret,
        items,
        blocking,
        _strings(raw["source_ids"], pointer + "/source_ids"),
        cast(
            ReviewState, _choice(raw["review_state"], pointer + "/review_state", ("unreviewed", "reviewed"))
        ),
        _nullable(raw["written_by"], pointer + "/written_by"),
        _nullable(raw["checked_by"], pointer + "/checked_by"),
        canonical_sha256(raw),
    )


def parse_plan(raw: Mapping[str, object], *, supplies: Mapping[str, SupplyInfo]) -> Plan:
    return _plan(raw, "", supplies, [])


def _sources(value: object) -> None:
    for i, v in enumerate(_array(value, "/sources")):
        p = f"/sources/{i}"
        source = _object(v, p)
        _shape(source, p, ("source_id", "title", "url", "date", "quote"), ("note",))
        for key in ("source_id", "title", "url", "date"):
            _string(source[key], p + "/" + key)
        _nullable(source["quote"], p + "/quote")
        if "note" in source:
            _string(source["note"], p + "/note")


def parse_document(
    raw: Mapping[str, object], *, document_sha256: str, supplies: Mapping[str, SupplyInfo]
) -> PlansDocument:
    _shape(raw, "", ("version", "example", "notice", "sources", "plans"))
    _sources(raw["sources"])
    plans = []
    rejected_plans = []
    rejected_steps: list[tuple[str, str]] = []
    ids: set[str] = set()
    for i, v in enumerate(_array(raw["plans"], "/plans")):
        p = f"/plans/{i}"
        item = _object(v, p)
        pid = _string(item.get("plan_id"), p + "/plan_id")
        if pid in ids:
            raise PlansDocumentInvalid(p + "/plan_id", "duplicate_plan_id")
        ids.add(pid)
        if item.get("review_state") == "rejected":
            rejected_plans.append(pid)
        else:
            plans.append(_plan(item, p, supplies, rejected_steps))
    return PlansDocument(
        _string(raw["version"], "/version"),
        _bool(raw["example"], "/example"),
        _string(raw["notice"], "/notice"),
        document_sha256,
        tuple(plans),
        tuple(rejected_plans),
        tuple(rejected_steps),
    )
