"""Typed reading of the configuration E32 consumes (E32 8.1): kit-map supplies and stock, onboard-plans supply needs,
the supply_cover_low definition and ui-copy supply words. Input is JSON already decoded with Decimal numbers.

Every problem becomes an Issue with its JSON pointer: the release check reports them (S-L1 to S-L7) and the runtime
index refuses a release that has any (fail closed, never a default)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.units import Quantity
from crew_health.supplies.domain.model import Need, StockRow, SupplyDef, SupplyWords, Threshold
from crew_health.supplies.domain.quantity import annotation_unit

THRESHOLD_ID = "supply_cover_low"
_WORD_KEYS = ("cover", "cover_unknown", "not_used", "not_aboard", "not_consumable")
_BASES = ("sourced", "team default")


@dataclass(frozen=True, slots=True)
class Issue:
    check_id: str  # S-L1 .. S-L7, or S-DOC for a shape problem
    document: str  # configuration document name
    pointer: str
    message: str


@dataclass(frozen=True, slots=True)
class KitSupplies:
    version: str
    example: bool
    supplies: tuple[SupplyDef, ...]  # document order
    stock: Mapping[str, Mapping[str, StockRow]]  # environment, supply
    candidate_supplies: tuple[str, ...]
    candidate_skills: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanStep:
    plan_id: str
    step_id: str
    environments: frozenset[str] | None  # None: every environment of its plan
    needs: tuple[tuple[str, Decimal | None, str], ...]  # supply_id, per_use number, pointer


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    environments: frozenset[str]
    rejected: bool
    steps: tuple[PlanStep, ...]  # stage order, then step order; prepare items included


class _Reader:
    def __init__(self, document: str) -> None:
        self.document = document
        self.issues: list[Issue] = []

    def issue(self, pointer: str, message: str, check_id: str = "S-DOC") -> None:
        self.issues.append(Issue(check_id, self.document, pointer, message))

    def obj(self, value: object, pointer: str) -> Mapping[str, object]:
        if isinstance(value, Mapping):
            return value
        self.issue(pointer, "expected an object")
        return {}

    def seq(self, value: object, pointer: str) -> Sequence[object]:
        if isinstance(value, list):
            return value
        self.issue(pointer, "expected an array")
        return ()

    def text(self, value: object, pointer: str) -> str:
        if isinstance(value, str) and value:
            return value
        self.issue(pointer, "expected a non empty string")
        return ""

    def flag(self, value: object, pointer: str) -> bool:
        if isinstance(value, bool):
            return value
        self.issue(pointer, "expected a boolean")
        return False

    def number(self, value: object, pointer: str) -> Decimal | None:
        """A JSON number as an exact Decimal; booleans are not numbers."""
        if isinstance(value, bool) or not isinstance(value, Decimal | int):
            self.issue(pointer, "expected a number")
            return None
        number = Decimal(value)
        if not number.is_finite():
            self.issue(pointer, "expected a finite number")
            return None
        return number


def _supplies(r: _Reader, doc: Mapping[str, object]) -> tuple[SupplyDef, ...]:
    out: list[SupplyDef] = []
    seen: set[str] = set()
    for i, raw in enumerate(r.seq(doc.get("supplies"), "/supplies")):
        p = f"/supplies/{i}"
        item = r.obj(raw, p)
        supply_id = r.text(item.get("supply_id"), p + "/supply_id")
        word = r.text(item.get("unit"), p + "/unit")
        unit = annotation_unit(word)
        if unit is None:
            r.issue(p + "/unit", "unit is not a valid UCUM annotation word", "S-L7")
            continue
        if supply_id in seen:
            r.issue(p + "/supply_id", "supply listed twice", "S-L1")
            continue
        seen.add(supply_id)
        out.append(
            SupplyDef(
                supply_id=supply_id,
                name=r.text(item.get("name"), p + "/name"),
                unit_word=word,
                unit=unit,
                consumable=r.flag(item.get("consumable"), p + "/consumable"),
            )
        )
    return tuple(out)


def _stock_row(r: _Reader, raw: object, p: str, supply: SupplyDef | None) -> StockRow | None:
    item = r.obj(raw, p)
    supply_id = r.text(item.get("supply_id"), p + "/supply_id")
    if supply is None:
        r.issue(p + "/supply_id", "stock row names a supply that is not listed", "S-L1")
        return None
    quantity = item.get("quantity")
    packed: Quantity | None = None
    if quantity is not None:
        number = r.number(quantity, p + "/quantity")
        if number is not None and number < 0:
            r.issue(p + "/quantity", "packed quantity below zero")
        elif number is not None:
            packed = Quantity(number, supply.unit)
    basis = item.get("basis")
    if basis not in _BASES:
        r.issue(p + "/basis", "basis is not sourced or team default")
    to_be_set_by = item.get("to_be_set_by")
    if to_be_set_by is not None and not isinstance(to_be_set_by, str):
        r.issue(p + "/to_be_set_by", "expected a string or null")
        to_be_set_by = None
    if (quantity is None or basis == "team default") and to_be_set_by is None:
        r.issue(p + "/to_be_set_by", "a null or team default quantity names who sets it", "S-L3")
    if basis == "sourced":
        r.issue(p + "/basis", "a sourced stock row needs source_ids (R-E32-03)", "S-L4")
    return StockRow(supply_id, packed, str(basis), to_be_set_by, p)


def read_kit_supplies(doc: object) -> tuple[KitSupplies, tuple[Issue, ...]]:
    r = _Reader("kit-map")
    top = r.obj(doc, "")
    supplies = _supplies(r, top)
    by_id = {s.supply_id: s for s in supplies}
    stock: dict[str, dict[str, StockRow]] = {}
    for env, rows in sorted(r.obj(top.get("stock"), "/stock").items()):
        per_env: dict[str, StockRow] = {}
        for i, raw in enumerate(r.seq(rows, f"/stock/{env}")):
            p = f"/stock/{env}/{i}"
            supply_id = r.obj(raw, p).get("supply_id")
            row = _stock_row(r, raw, p, by_id.get(supply_id) if isinstance(supply_id, str) else None)
            if row is None:
                continue
            if row.supply_id in per_env:
                r.issue(p + "/supply_id", "two stock rows for one supply here", "S-L1")
                continue
            per_env[row.supply_id] = row
        stock[env] = per_env
    candidates = tuple(str(c) for c in r.seq(top.get("candidate_supplies", []), "/candidate_supplies"))
    for i, supply_id in enumerate(candidates):
        if supply_id not in by_id:
            r.issue(f"/candidate_supplies/{i}", "candidate supply is not listed", "S-L2")
    skills = tuple(str(c) for c in r.seq(top.get("candidate_skills", []), "/candidate_skills"))
    version = r.text(top.get("version"), "/version")
    example = r.flag(top.get("example"), "/example")
    return KitSupplies(version, example, supplies, stock, candidates, skills), tuple(r.issues)


def _step(r: _Reader, plan_id: str, raw: object, p: str) -> PlanStep:
    item = r.obj(raw, p)
    envs = item.get("environments")
    environments = (
        None
        if envs is None
        else frozenset(
            r.text(e, f"{p}/environments/{k}") for k, e in enumerate(r.seq(envs, p + "/environments"))
        )
    )
    needs: list[tuple[str, Decimal | None, str]] = []
    supplies = r.seq(r.obj(item.get("needs"), p + "/needs").get("supplies"), p + "/needs/supplies")
    for k, need in enumerate(supplies):
        q = f"{p}/needs/supplies/{k}"
        entry = r.obj(need, q)
        per_use = entry.get("per_use")
        number = None if per_use is None else r.number(per_use, q + "/per_use")
        if number is not None and number <= 0:
            r.issue(q + "/per_use", "per_use is above zero")
            number = None
        needs.append((r.text(entry.get("supply_id"), q + "/supply_id"), number, q))
    return PlanStep(plan_id, r.text(item.get("step_id"), p + "/step_id"), environments, tuple(needs))


def _stage_steps(r: _Reader, plan_id: str, body: Mapping[str, object], sp: str) -> list[PlanStep]:
    """A stage holds `steps`, or `prepare` items limited to the stage's `environments` (the return stage), or
    neither (the ground_log stage lists log items, which need no supplies)."""
    if "steps" in body:
        return [
            _step(r, plan_id, s, f"{sp}/steps/{k}") for k, s in enumerate(r.seq(body["steps"], sp + "/steps"))
        ]
    if "prepare" not in body:
        return []
    stage_envs = frozenset(
        r.text(e, f"{sp}/environments/{k}")
        for k, e in enumerate(r.seq(body.get("environments"), sp + "/environments"))
    )
    out: list[PlanStep] = []
    for k, raw in enumerate(r.seq(body["prepare"], sp + "/prepare")):
        step = _step(r, plan_id, raw, f"{sp}/prepare/{k}")
        envs = stage_envs if step.environments is None else step.environments & stage_envs
        out.append(PlanStep(step.plan_id, step.step_id, envs, step.needs))
    return out


def read_plans(doc: object) -> tuple[tuple[Plan, ...], tuple[Issue, ...]]:
    r = _Reader("onboard-plans")
    plans: list[Plan] = []
    for i, raw in enumerate(r.seq(r.obj(doc, "").get("plans"), "/plans")):
        p = f"/plans/{i}"
        item = r.obj(raw, p)
        plan_id = r.text(item.get("plan_id"), p + "/plan_id")
        steps: list[PlanStep] = []
        for s, stage in enumerate(r.seq(item.get("stages"), p + "/stages")):
            sp = f"{p}/stages/{s}"
            body = r.obj(stage, sp)
            steps.extend(_stage_steps(r, plan_id, body, sp))
        environments = frozenset(
            r.text(e, f"{p}/environments/{k}")
            for k, e in enumerate(r.seq(item.get("environments"), p + "/environments"))
        )
        plans.append(Plan(plan_id, environments, item.get("review_state") == "rejected", tuple(steps)))
    return tuple(plans), tuple(r.issues)


def read_threshold(doc: object) -> tuple[Threshold | None, tuple[Issue, ...]]:
    """S-L6: exactly one supply_cover_low definition; below_uses null or an integer of at least 0."""
    r = _Reader("warning-rules")
    found: list[Threshold] = []
    for i, raw in enumerate(r.seq(r.obj(doc, "").get("definitions"), "/definitions")):
        item = r.obj(raw, f"/definitions/{i}")
        if item.get("id") != THRESHOLD_ID:
            continue
        p = f"/definitions/{i}/condition/below_uses"
        below = r.obj(item.get("condition"), f"/definitions/{i}/condition").get("below_uses")
        if below is not None and (isinstance(below, bool) or not isinstance(below, int) or below < 0):
            r.issue(p, "below_uses is null or an integer of at least 0", "S-L6")
            below = None
        envs = r.seq(item.get("environments"), f"/definitions/{i}/environments")
        found.append(Threshold(below if isinstance(below, int) else None, frozenset(str(e) for e in envs)))
    if len(found) != 1:
        r.issue("/definitions", "supply_cover_low is defined exactly once", "S-L6")
        return None, tuple(r.issues)
    return found[0], tuple(r.issues)


def read_words(doc: object) -> tuple[SupplyWords | None, tuple[Issue, ...]]:
    r = _Reader("ui-copy")
    words = r.obj(r.obj(doc, "").get("supply_words"), "/supply_words")
    texts = [r.text(words.get(k), "/supply_words/" + k) for k in _WORD_KEYS]
    if r.issues:
        return None, tuple(r.issues)
    return SupplyWords(*texts), ()


def resolve_needs(
    plans: Sequence[Plan], supplies: Mapping[str, SupplyDef]
) -> tuple[tuple[tuple[Plan, PlanStep, tuple[Need, ...]], ...], tuple[Issue, ...]]:
    """Every step's needs as typed Needs in the supply's unit; S-L5 for a need of an unlisted supply."""
    issues: list[Issue] = []
    out: list[tuple[Plan, PlanStep, tuple[Need, ...]]] = []
    for plan in plans:
        for step in plan.steps:
            needs: list[Need] = []
            for supply_id, per_use, pointer in step.needs:
                supply = supplies.get(supply_id)
                if supply is None:
                    issues.append(
                        Issue("S-L5", "onboard-plans", pointer, "need names a supply not in kit-map")
                    )
                    continue
                quantity = None if per_use is None else Quantity(per_use, supply.unit)
                needs.append(Need(plan.plan_id, step.step_id, supply_id, quantity, pointer))
            out.append((plan, step, tuple(needs)))
    return tuple(out), tuple(issues)
