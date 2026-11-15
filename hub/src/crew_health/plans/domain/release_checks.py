"""C11 checks 14 and 15 on filtered, typed plans and caller-supplied facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from crew_health.plans.domain.availability import EnvContext, StockLevel, required_skills
from crew_health.plans.domain.model import STAGE_ORDER, Plan, PlansDocument, Step, SupplyInfo, all_steps
from crew_health.plans.domain.recheck import recheck_shape_ok
from crew_health.plans.domain.text_check import find_amounts, medicine_words, needs_medicine_phrase
from crew_health.plans.domain.view import plan_aboard

Severity = Literal["reject", "warning"]


@dataclass(frozen=True, slots=True)
class Finding:
    check_id: str
    pointer: str
    message: str
    severity: Severity


@dataclass(frozen=True, slots=True)
class KitFacts:
    instrument_ids: frozenset[str]
    supplies: Mapping[str, SupplyInfo]
    kits: Mapping[str, frozenset[str]]
    stock: Mapping[str, Mapping[str, StockLevel]]
    rows: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class CatalogFacts:
    skill_ids: frozenset[str]
    landmark_ids: frozenset[str]
    teach_texts: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ReleaseFacts:
    kit: KitFacts
    catalog: CatalogFacts
    flow_ids: frozenset[str]
    return_options: Mapping[str, bool]
    quote_verified: Mapping[tuple[str, int | None, str], bool]
    rtdc_verified: Mapping[str, bool]
    mode: Literal["LIVE", "REPLAY", "SIM"]
    max_plans_per_document: int
    max_steps_per_plan: int


def _finding(check: str, pointer: str, subject: str, message: str) -> Finding:
    return Finding(check, pointer, f"{subject}: {message}", "reject")


def _locations(
    raw: Mapping[str, object],
) -> tuple[dict[str, str], dict[tuple[str, str], tuple[str, Mapping[str, object]]]]:
    from crew_health.plans.domain.model import _array, _object

    plans: dict[str, str] = {}
    steps: dict[tuple[str, str], tuple[str, Mapping[str, object]]] = {}
    for i, v in enumerate(_array(raw["plans"], "/plans")):
        pp = f"/plans/{i}"
        plan = _object(v, pp)
        pid = str(plan["plan_id"])
        plans[pid] = pp
        for j, item in enumerate(_array(plan["stages"], pp + "/stages")):
            sp = f"{pp}/stages/{j}"
            stage = _object(item, sp)
            key = "prepare" if stage["stage_id"] == "return" else "steps"
            for k, step_value in enumerate(_array(stage.get(key, ()), sp + "/" + key)):
                pointer = f"{sp}/{key}/{k}"
                step = _object(step_value, pointer)
                steps[(pid, str(step["step_id"]))] = pointer, step
    return plans, steps


def _plan_checks(plan: Plan, pointer: str, facts: ReleaseFacts) -> tuple[Finding, ...]:
    result: list[Finding] = []
    sequence = plan.stage_sequence
    ranks = tuple(STAGE_ORDER.index(s) if s in STAGE_ORDER else -1 for s in sequence)
    if (
        not sequence
        or sequence[-1] != "ground_log"
        or any(r < 0 for r in ranks)
        or any(a >= b for a, b in zip(ranks, ranks[1:], strict=False))
        or plan.ground_log_blocking
    ):
        result.append(
            _finding("14a", pointer + "/stages", plan.plan_id, "invalid stage order or blocking ground_log")
        )
    ret = plan.return_stage
    if ret is not None:
        for env in sorted(ret.environments):
            if env not in plan.environments or not facts.return_options.get(env, False):
                result.append(
                    _finding("14b", pointer + "/stages", plan.plan_id, f"return unavailable in {env}")
                )
        if ret.rtdc_json is not None and not facts.rtdc_verified.get(plan.plan_id, False):
            result.append(_finding("14f", pointer + "/stages", plan.plan_id, "rtdc unverifiable"))
    if plan.flow_id is not None and plan.flow_id not in facts.flow_ids:
        result.append(
            _finding("14c", pointer + "/scope/flow_id", plan.plan_id, f"unknown flow {plan.flow_id}")
        )
    if plan.condition_id is not None and not any(c == plan.condition_id for c, _ in facts.kit.rows):
        result.append(
            _finding(
                "14c", pointer + "/scope/condition_id", plan.plan_id, f"unknown condition {plan.condition_id}"
            )
        )
    if len(all_steps(plan)) > facts.max_steps_per_plan:
        result.append(_finding("14h", pointer + "/stages", plan.plan_id, "step bound exceeded"))
    return tuple(result)


def _declared_unknown(raw: Mapping[str, object], supply_id: str) -> bool:
    """A supply need with no quantity and a named owner to set it (basis plus to_be_set_by)."""
    from crew_health.plans.domain.model import _array, _object

    needs = _object(raw["needs"], "/needs")
    for value in _array(needs["supplies"], "/needs/supplies"):
        row = _object(value, "/needs/supplies")
        if row["supply_id"] == supply_id:
            return (
                row["per_use"] is None and isinstance(row["to_be_set_by"], str) and bool(row["to_be_set_by"])
            )
    return False


def _references(
    plan: Plan, step: Step, pointer: str, facts: ReleaseFacts, raw: Mapping[str, object]
) -> tuple[Finding, ...]:
    result: list[Finding] = []
    label = f"{plan.plan_id} step {step.step_id}"
    references = tuple(("instrument", i, i in facts.kit.instrument_ids) for i in step.needs.instruments)
    # Coordinator decision (review item 2): a supply the catalog does not list yet is accepted only when its
    # quantity is declared unknown; availability then treats it as missing, so the step fails closed.
    references += tuple(
        (
            "supply",
            n.supply_id,
            (n.known and n.supply_id in facts.kit.supplies)
            or (not n.known and _declared_unknown(raw, n.supply_id)),
        )
        for n in step.needs.supplies
    )
    skills = required_skills(step) + ((step.guided_check_skill_id,) if step.guided_check_skill_id else ())
    references += tuple(("skill", k, k in facts.catalog.skill_ids) for k in skills)
    if step.landmark_id is not None:
        references += (("landmark", step.landmark_id, step.landmark_id in facts.catalog.landmark_ids),)
    if step.opens_flow_id is not None:
        references += (("flow", step.opens_flow_id, step.opens_flow_id in facts.flow_ids),)
    for kind, identifier, known in references:
        if not known:
            result.append(_finding("14c", pointer, label, f"unknown {kind} {identifier}"))
    if step.substitute_step_id is not None:
        sub = plan.step(step.substitute_step_id)
        if sub is None or not sub.substitute_only:
            result.append(
                _finding(
                    "14c", pointer + "/substitute_step_id", label, "substitute missing or not substitute_only"
                )
            )
    if step.substitute_only and not any(s.substitute_step_id == step.step_id for s in all_steps(plan)):
        result.append(
            _finding("14c", pointer + "/substitute_only", label, "substitute_only has no principal")
        )
    return tuple(result)


def _placement(plan: Plan, step: Step, pointer: str, raw: Mapping[str, object]) -> tuple[Finding, ...]:
    from crew_health.plans.domain.model import _array, _object

    result: list[Finding] = []
    label = f"{plan.plan_id} step {step.step_id}"
    if step.who.kind == "crew_medical_officer" and step.stage_id not in ("medical_officer", "return"):
        result.append(_finding("14d", pointer + "/who", label, "officer outside medical_officer or return"))
    if step.guided_check_skill_id is not None and (
        step.stage_id != "check_another_way"
        or step.who.kind != "skill"
        or step.who.skill_id != step.guided_check_skill_id
    ):
        result.append(
            _finding(
                "14d",
                pointer + "/guided_check_skill_id",
                label,
                "guided check requires matching check_another_way skill",
            )
        )
    if (step.opens_help or step.opens_flow_id is not None) and step.stage_id != "if_worse":
        result.append(_finding("14d", pointer, label, "only if_worse opens help or a flow"))
    needs = _object(raw["needs"], pointer + "/needs")
    raw_needs = tuple(
        _object(v, pointer + "/needs/supplies")
        for v in _array(needs["supplies"], pointer + "/needs/supplies")
    )
    for i, need in enumerate(step.needs.supplies):
        if not need.consumable and raw_needs[i]["per_use"] is not None:
            result.append(
                _finding(
                    "14d",
                    f"{pointer}/needs/supplies/{i}/per_use",
                    label,
                    f"nonconsumable {need.supply_id} has per_use",
                )
            )
    return tuple(result)


def _text(text: str, pointer: str, label: str, *, step: Step | None = None) -> tuple[Finding, ...]:
    try:
        if find_amounts(text):
            return (_finding("14e", pointer, label, "amount in text"),)
        if needs_medicine_phrase(text) or (
            step is not None and medicine_words(text) and step.medicine_named_as is None
        ):
            return (_finding("14e", pointer, label, "medicine requires phrase and step annotation"),)
    except Exception:
        # C11 14e is fail closed, including an unavailable or broken checker.
        return (_finding("14e", pointer, label, "text check failed"),)
    return ()


def _step_checks(
    plan: Plan, step: Step, pointer: str, raw: Mapping[str, object], facts: ReleaseFacts
) -> tuple[Finding, ...]:
    result = list(_references(plan, step, pointer, facts, raw) + _placement(plan, step, pointer, raw))
    label = f"{plan.plan_id} step {step.step_id}"
    for name, text in (
        ("text", step.text),
        ("text_first_time", step.text_first_time),
        ("cannot_replace_text", step.cannot_replace_text),
    ):
        if text is not None:
            result.extend(_text(text, pointer + "/" + name, label, step=step))
    quote = step.replaces_ground_task
    if quote is not None and not facts.quote_verified.get((quote.source_id, quote.page, quote.quote), False):
        result.append(_finding("14f", pointer + "/replaces_ground_task", label, "form quote unverifiable"))
    if step.recheck is not None and not recheck_shape_ok(step.recheck):
        result.append(_finding("14g", pointer + "/recheck", label, "invalid recheck shape"))
    return tuple(result)


def _other_texts(plan: Plan, raw: Mapping[str, object], pointer: str) -> tuple[Finding, ...]:
    from crew_health.plans.domain.model import _array, _object

    result: list[Finding] = []
    for i, value in enumerate(_array(raw["stages"], pointer + "/stages")):
        stage = _object(value, pointer + "/stages")
        sp = f"{pointer}/stages/{i}"
        if stage["stage_id"] == "return" and plan.return_stage is not None:
            for j, criterion in enumerate(plan.return_stage.criteria):
                result.extend(_text(criterion.text, f"{sp}/criteria/{j}/text", plan.plan_id))
        if stage["stage_id"] == "ground_log":
            for j, item in enumerate(plan.ground_log_items):
                result.extend(_text(item.text, f"{sp}/items/{j}/text", plan.plan_id))
    return tuple(result)


def _row_context(env: str, facts: ReleaseFacts) -> EnvContext:
    return EnvContext(env, facts.kit.kits[env], facts.kit.stock.get(env), None, None, {}, False)


def _row_findings(doc: PlansDocument, facts: ReleaseFacts) -> tuple[Finding, ...]:
    result: list[Finding] = []
    severity: Severity = "reject" if facts.mode == "LIVE" else "warning"
    for condition, pid in facts.kit.rows:
        pointer = "/conditions/" + condition
        plan = doc.plan(pid) if pid is not None else None
        if pid is None:
            result.append(Finding("15", pointer, f"row {condition} has no plan_id", severity))
        elif plan is None:
            result.append(Finding("15", pointer, f"row {condition} plan {pid} missing or rejected", severity))
        elif plan.condition_id != condition:
            result.append(Finding("15", pointer, f"row {condition} plan {pid} scope differs", severity))
        else:
            for env in sorted(facts.kit.kits):
                if not plan_aboard(plan, _row_context(env, facts)):
                    result.append(
                        Finding(
                            "15",
                            pointer + "/" + env,
                            f"row {condition} plan {pid} not aboard in {env}",
                            severity,
                        )
                    )
    return tuple(result)


def plan_rows_aboard(doc: PlansDocument, facts: ReleaseFacts) -> tuple[int, int]:
    count = 0
    for condition, pid in facts.kit.rows:
        plan = doc.plan(pid) if pid is not None else None
        if (
            plan is not None
            and plan.condition_id == condition
            and all(plan_aboard(plan, _row_context(env, facts)) for env in sorted(facts.kit.kits))
        ):
            count += 1
    return count, len(facts.kit.rows)


def check_plans(doc: PlansDocument, raw: Mapping[str, object], facts: ReleaseFacts) -> tuple[Finding, ...]:
    from crew_health.plans.domain.model import _array, _object

    result: list[Finding] = []
    plan_locations, step_locations = _locations(raw)
    if len(doc.plans) > facts.max_plans_per_document:
        result.append(_finding("14h", "/plans", "document", "plan bound exceeded"))
    raw_plans = _array(raw["plans"], "/plans")
    for plan in doc.plans:
        pp = plan_locations[plan.plan_id]
        result.extend(_plan_checks(plan, pp, facts))
        for step in all_steps(plan):
            sp, raw_step = step_locations[(plan.plan_id, step.step_id)]
            result.extend(_step_checks(plan, step, sp, raw_step, facts))
        raw_plan = _object(raw_plans[int(pp.rsplit("/", 1)[1])], pp)
        result.extend(_other_texts(plan, raw_plan, pp))
    for pointer, text in facts.catalog.teach_texts:
        result.extend(_text(text, pointer, "catalog teach text"))
    return tuple(result) + _row_findings(doc, facts)
