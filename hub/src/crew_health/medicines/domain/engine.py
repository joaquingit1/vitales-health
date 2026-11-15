"""The dose engine (E32 3.5 and 3.6): pure, deterministic, one amount per proposal with a trace for every number.

Every limit is enforced on the final amount through the accounting amount `Acc_i(a) = a * S_i * max(1, u_i)`
(INV-M1); an excluded lot never reaches a proposal (INV-M3); compensation needs a fresh `adjust` assessment, every gate
check passing and, in LIVE, an authorization entry (INV-M2). A refusal is a closed code, never overridable here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, localcontext
from typing import Literal

from crew_health.medicines.domain.alerts import alerts_for
from crew_health.medicines.domain.model import (
    REVIEWED,
    Alert,
    ContextPart,
    DoseContext,
    DoseRequest,
    DoseRule,
    FormularyProduct,
    IndicationLine,
    IngredientLine,
    IngredientRule,
    LotSummary,
    LotView,
    PastAdministration,
    Path,
    Potency,
    ProductRule,
    Proposal,
    Refusal,
    TraceEntry,
)
from crew_health.medicines.domain.units import Amount, UnitIncompatible, convert, display, text

MAX_INCREMENTS = 10000  # a product needing more deliverable increments for one dose has no deliverable amount
ONE = Decimal(1)


class Refused(Exception):
    def __init__(self, refusal: Refusal, next_dose: datetime | None = None) -> None:
        super().__init__(refusal.code)
        self.refusal = refusal
        self.next_dose = next_dose


@dataclass(frozen=True, slots=True)
class _Limits:
    """One ingredient's rule in the strength unit."""

    rule: IngredientRule
    strength: Amount
    target: Decimal
    max_per_administration: Decimal
    catastrophic: Decimal | None
    periods: tuple[tuple[int, Decimal], ...]
    cumulative: Decimal | None
    min_effective: Decimal | None


def fresh(summary: LotSummary | None, ctx: DoseContext, product: FormularyProduct) -> LotSummary | None:
    """The lot's latest completed assessment when it is fresh and covers every ingredient, else None."""
    if summary is None:
        return None
    age = (ctx.now - summary.as_of).total_seconds()
    if age < 0 or age > ctx.policy.max_assessment_age_seconds:
        return None
    if any(i.ingredient_id not in summary.potency for i in product.ingredients):
        return None
    return summary


def exclusions(lot: LotView, now: datetime) -> tuple[str, ...]:
    """E32 3.5 step 4, synchronous: independent of the assessment age and of the worker (INV-M3)."""
    reasons = []
    if lot.lot_status != "active":
        reasons.append("lot_status_" + lot.lot_status)
    if lot.adverse_inspection:
        reasons.append("adverse_inspection")
    if now >= lot.labeled_expiry:
        reasons.append("past_labeled_expiry")
    if lot.summary is not None and lot.summary.state == "do_not_use":
        reasons.append("do_not_use")
    if lot.quantity_now <= 0:
        reasons.append("no_stock")
    return tuple(reasons)


def _part_fresh(part: ContextPart | None, ctx: DoseContext) -> bool:
    if part is None or part.state == "unknown":
        return False
    return (ctx.now - part.recorded_at).total_seconds() <= ctx.treatments.context_max_age_seconds


class _Run:
    def __init__(self, request: DoseRequest, ctx: DoseContext) -> None:
        self.request = request
        self.ctx = ctx
        self.trace: list[TraceEntry] = []
        self.doc = f"treatments.json@{ctx.treatments.version}"

    def note(self, step: str, source: str, value: str) -> None:
        self.trace.append(TraceEntry(step, source, value))

    def refuse(
        self, code: str, detail: str, lots: Sequence[str] = (), products: Sequence[str] = ()
    ) -> Refused:
        self.note("refusal", "computed", code)
        return Refused(Refusal(code, detail, tuple(sorted(set(lots))), tuple(sorted(set(products)))))

    # ---- step 1: preconditions ----

    def rule(self) -> tuple[IndicationLine, DoseRule, ProductRule, FormularyProduct]:
        diagnosis = self.request.diagnosis
        if diagnosis is None:
            raise self.refuse("no_working_diagnosis", "a person records a working diagnosis first")
        lines = self.ctx.treatments.indications.get(diagnosis.condition_id, ())
        line = next((x for x in lines if x.product_id == self.request.product_id), None)
        if line is None or line.rule_id not in self.ctx.treatments.rules:
            raise self.refuse(
                "rule_not_found",
                f"no reviewed formulary line for {self.request.product_id} under {diagnosis.condition_id}",
                products=[x.product_id for x in lines],
            )
        rule = self.ctx.treatments.rules[line.rule_id]
        self.note("rule", f"{self.doc}#{rule.pointer}", f"{rule.rule_id} ({rule.review_state})")
        if self.ctx.mode == "LIVE" and rule.review_state not in REVIEWED:
            raise self.refuse("rule_not_reviewed_live", f"rule {rule.rule_id} is {rule.review_state}")
        product = self.ctx.treatments.products.get(line.product_id)
        formulary = self.ctx.formulary.get(line.product_id)
        if (
            product is None
            or formulary is None
            or {i.ingredient_id for i in rule.ingredients} != {i.ingredient_id for i in formulary.ingredients}
        ):
            raise self.refuse("formulary_mismatch", f"{line.product_id} differs from the pharmacy formulary")
        return line, rule, product, formulary

    def context(self, rule: DoseRule) -> None:
        parts = self.ctx.context
        allergies = parts.allergies
        if allergies is None or not _part_fresh(allergies, self.ctx):
            raise self.refuse("allergy_list_unknown", "the allergy list is not documented or too old")
        self.note("context", f"record:{allergies.statement_id}", f"allergies: {allergies.state}")
        needed = [("other_medicines", parts.other_medicines)]
        needed += [(organ, getattr(parts, organ)) for organ in rule.organ_cautions]
        if rule.condition_cautions:
            needed.append(("conditions", parts.conditions))
        for name, part in needed:
            if part is None or not _part_fresh(part, self.ctx):
                raise self.refuse("patient_context_missing", name)
            self.note("context", f"record:{part.statement_id}", f"{name}: {part.state}")

    def weight(self, rule: DoseRule) -> Decimal | None:
        if all(i.dose_per_kg is None for i in rule.ingredients):
            return None
        policy = self.ctx.treatments.weight
        observations = sorted(self.ctx.weights, key=lambda w: w.at, reverse=True)
        if policy is None or not observations:
            raise self.refuse("weight_missing", "no accepted body mass for a per-kilogram rule")
        latest = observations[0]
        if (self.ctx.now - latest.at).total_seconds() > policy.max_age_seconds:
            raise self.refuse("weight_stale", f"body mass of {latest.at.isoformat()} is too old")
        if not policy.plausible_min_kg <= latest.kg <= policy.plausible_max_kg:
            raise self.refuse("weight_implausible", f"{text(latest.kg)} kg is outside the plausible range")
        if len(observations) > 1:
            previous = observations[1]
            within = (latest.at - previous.at).total_seconds() <= policy.change_window_seconds
            if (
                within
                and previous.kg > 0
                and abs(latest.kg - previous.kg) / previous.kg > policy.max_change_fraction
            ):
                raise self.refuse("weight_change_unconfirmed", "a second agreeing measurement is needed")
        self.note("weight", f"record:{latest.observation_id}", f"{text(latest.kg)} kg")
        return latest.kg

    def limits(
        self, rule: DoseRule, product: ProductRule, formulary: FormularyProduct, kg: Decimal | None
    ) -> dict[str, _Limits]:
        if product.deliverable_increment.ucum != formulary.per:
            raise self.refuse(
                "unit_incompatible", f"increment {product.deliverable_increment.ucum} vs {formulary.per}"
            )
        strengths = {i.ingredient_id: i.strength for i in formulary.ingredients}
        out: dict[str, _Limits] = {}
        try:
            for index, ing in enumerate(rule.ingredients):
                pointer = f"{self.doc}#{rule.pointer}/ingredients/{index}"
                unit = strengths[ing.ingredient_id].ucum
                cap = convert(ing.max_per_administration, unit).value
                if ing.dose is not None:
                    target = convert(ing.dose, unit).value
                    self.note("target", pointer + "/dose", display(Amount(target, unit)))
                else:
                    if ing.dose_per_kg is None or kg is None:
                        raise self.refuse(
                            "weight_missing", f"{ing.ingredient_id} has neither dose nor body mass"
                        )
                    target = min(convert(ing.dose_per_kg, unit).value * kg, cap)
                    self.note("target", pointer + "/dose_per_kg", display(Amount(target, unit)))
                catastrophic = self.ctx.treatments.catastrophic.get(ing.ingredient_id)
                out[ing.ingredient_id] = _Limits(
                    rule=ing,
                    strength=strengths[ing.ingredient_id],
                    target=target,
                    max_per_administration=cap,
                    catastrophic=None if catastrophic is None else convert(catastrophic, unit).value,
                    periods=tuple(
                        (p.period_seconds, convert(p.limit, unit).value) for p in ing.max_per_period
                    ),
                    cumulative=None
                    if ing.max_cumulative is None
                    else convert(ing.max_cumulative, unit).value,
                    min_effective=None
                    if ing.min_effective is None
                    else convert(ing.min_effective, unit).value,
                )
                self.note(
                    "max_per_administration", pointer + "/max_per_administration", display(Amount(cap, unit))
                )
                for p_index, (seconds, limit) in enumerate(out[ing.ingredient_id].periods):
                    self.note(
                        "max_per_period",
                        f"{pointer}/max_per_period/{p_index}",
                        f"{display(Amount(limit, unit))} per {seconds} s",
                    )
                self.note(
                    "strength",
                    f"pharmacy formulary#/products/{formulary.product_id}/{ing.ingredient_id}",
                    display(strengths[ing.ingredient_id]),
                )
        except UnitIncompatible as error:
            raise self.refuse("unit_incompatible", str(error)) from error
        return out

    # ---- step 3: interval and course ----

    def interval(self, rule: DoseRule) -> None:
        latest_ok: datetime | None = None
        for ing in rule.ingredients:
            times = [p.at for p in self.ctx.history if ing.ingredient_id in p.accounting]
            if not times:
                continue
            earliest = max(times) + timedelta(seconds=ing.min_interval_seconds)
            if earliest > self.ctx.now:
                latest_ok = earliest if latest_ok is None else max(latest_ok, earliest)
        if latest_ok is not None:
            refused = self.refuse("min_interval_not_passed", f"next dose at {latest_ok.isoformat()}")
            refused.next_dose = latest_ok
            raise refused
        course = [p.at for p in self._course(rule)]
        seconds = [i.course_seconds for i in rule.ingredients if i.course_seconds is not None]
        if course and seconds and self.ctx.now >= min(course) + timedelta(seconds=min(seconds)):
            raise self.refuse("course_ended", f"course started {min(course).isoformat()}")

    def _course(self, rule: DoseRule) -> list[PastAdministration]:
        diagnosis = self.request.diagnosis
        if (
            diagnosis is None
        ):  # a manual administration has no course: every use of the rule counts (stricter)
            return [p for p in self.ctx.history if p.rule_id == rule.rule_id]
        return [
            p for p in self.ctx.history if p.rule_id == rule.rule_id and p.lineage_id == diagnosis.lineage_id
        ]

    # ---- step 4: lot ----

    def lot(
        self, line: IndicationLine, product: ProductRule, formulary: FormularyProduct
    ) -> tuple[LotView, LotSummary | None]:
        candidates = [lot for lot in self.ctx.lots if lot.product_id == line.product_id]
        usable = [
            (lot, fresh(lot.summary, self.ctx, formulary))
            for lot in candidates
            if not exclusions(lot, self.ctx.now)
        ]
        alternatives_lots = [x for lot in candidates if lot.summary for x in lot.summary.alternative_lot_ids]
        alternatives_products = [
            x for lot in candidates if lot.summary for x in lot.summary.alternative_product_ids
        ]
        condition = self.request.diagnosis.condition_id if self.request.diagnosis is not None else ""
        alternatives_products += [
            x.product_id
            for x in self.ctx.treatments.indications.get(condition, ())
            if x.product_id != line.product_id
        ]
        if self.request.lot_id is not None:
            chosen = next((lot for lot in candidates if lot.lot_id == self.request.lot_id), None)
            if chosen is None:
                raise self.refuse(
                    "no_usable_lot", f"{self.request.lot_id} is not a lot of {line.product_id} here"
                )
            reasons = exclusions(chosen, self.ctx.now)
            if reasons:
                raise self.refuse(
                    "no_usable_lot",
                    f"{chosen.lot_id} excluded: {', '.join(reasons)}",
                    [lot.lot_id for lot, _ in usable] + alternatives_lots,
                    alternatives_products,
                )
            usable = [(chosen, fresh(chosen.summary, self.ctx, formulary))]
        if not usable:
            raise self.refuse(
                "no_usable_lot",
                f"no usable lot of {line.product_id}",
                alternatives_lots,
                alternatives_products,
            )
        rank = {"in_specification": 0, "adjust": 1}
        usable.sort(
            key=lambda x: (
                rank.get(x[1].state if x[1] else "in_specification", 0),
                x[0].labeled_expiry,
                x[0].lot_id,
            )
        )
        if self.ctx.policy.potency.mode == "information_only":
            plain = [x for x in usable if x[1] is None or x[1].state != "adjust"]
            if not plain:
                raise self.refuse(
                    "potency_adjustment_disabled",
                    "every usable lot needs adjustment and potency_policy.mode is information_only",
                    alternatives_lots,
                    alternatives_products,
                )
            usable = plain
        lot, summary = usable[0]
        self.note("lot", f"record:{lot.lot_id}", f"{lot.lot_id}, {text(lot.quantity_now)} left")
        if summary is not None:
            self.note(
                "assessment",
                f"assessment:{summary.run_id}",
                f"{summary.state} as of {summary.as_of.isoformat()}",
            )
        return lot, summary

    # ---- step 5: the gate ----

    def gate(self, summary: LotSummary | None, product: ProductRule, formulary: FormularyProduct) -> Path:
        if summary is None or summary.state != "adjust":
            return "labeled"
        policy = self.ctx.policy.potency
        failed = []
        if not summary.adjustment_eligible:
            failed.append("adjustment.eligible")
        if not formulary.potency_adjustment_allowed:
            failed.append("potency_adjustment.allowed")
        if policy.require_measured_assay and not summary.assay_used:
            failed.append("require_measured_assay")
        if policy.exclude_narrow_therapeutic_index and formulary.narrow_therapeutic_index:
            failed.append("exclude_narrow_therapeutic_index")
        if policy.exclude_high_alert and product.high_alert:
            failed.append("exclude_high_alert")
        if policy.require_degradant_profile_reviewed and not formulary.degradant_profile_reviewed:
            failed.append("require_degradant_profile_reviewed")
        if policy.require_measured_storage_history and summary.custody_declared_or_gapped:
            failed.append("require_measured_storage_history")
        if policy.require_validated_engine and summary.engine_version not in policy.validated_engine_versions:
            failed.append("require_validated_engine")
        if failed:
            raise self.refuse(
                "adjustment_not_permitted",
                "failed: " + ", ".join(failed),
                summary.alternative_lot_ids,
                summary.alternative_product_ids,
            )
        if self.request.labeled_only:
            return "labeled_with_estimate"
        if self.ctx.mode == "LIVE" and product.product_id not in self.ctx.policy.live_authorizations:
            return "labeled_with_estimate"
        return "compensated"


def _sum(values: Sequence[Decimal]) -> Decimal:
    total = Decimal(0)
    for value in values:
        total += value
    return total


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def propose(request: DoseRequest, ctx: DoseContext) -> Proposal:
    """E32 3.5 steps 1 to 8. Never raises for a clinical reason: a refusal is a Proposal with outcome `refusal`."""
    run = _Run(request, ctx)
    rule_id: str | None = None
    try:
        with localcontext() as decimal_ctx:
            decimal_ctx.prec = 34
            return _propose(run)
    except Refused as refused:
        if request.diagnosis is not None:
            line = next(
                (
                    x
                    for x in ctx.treatments.indications.get(request.diagnosis.condition_id, ())
                    if x.product_id == request.product_id
                ),
                None,
            )
            rule_id = line.rule_id if line else None
        return Proposal(
            outcome="refusal",
            product_id=request.product_id,
            rule_id=rule_id,
            lot_id=None,
            amount=None,
            amount_text=None,
            ingredients=(),
            path="labeled",
            potency_status="none",
            assessment_run_id=None,
            intervals={},
            refusal=refused.refusal,
            alerts=(),
            trace=tuple(run.trace),
            next_dose_earliest_at=refused.next_dose,
            review_state=ctx.treatments.rules[rule_id].review_state
            if rule_id is not None and rule_id in ctx.treatments.rules
            else None,
        )


def _terms(run: _Run, rule: DoseRule, ing_id: str, limits: _Limits) -> list[tuple[Decimal, str]]:
    """Every remaining allowance of one ingredient with the refusal code it binds as (E32 3.6 R_i)."""
    ctx = run.ctx
    terms: list[tuple[Decimal, str]] = [(limits.max_per_administration, "per_administration_max_exceeded")]
    if limits.catastrophic is not None:
        terms.append((limits.catastrophic, "catastrophic_limit"))
    unit = limits.strength.ucum
    for seconds, limit in limits.periods:
        start = ctx.now - timedelta(seconds=seconds)
        rows = [p for p in ctx.history if start < p.at <= ctx.now and ing_id in p.accounting]
        if len(rows) > ctx.max_window_rows:
            raise run.refuse("history_too_large", f"{len(rows)} administrations in one window")
        used = _sum([convert(p.accounting[ing_id], unit).value for p in rows])
        if rows:
            run.note(
                "period_used",
                "records:" + ",".join(p.administration_id for p in rows),
                f"{display(Amount(used, unit))} in the last {seconds} s",
            )
        terms.append((limit - used, "period_max_exceeded"))
    if limits.cumulative is not None:
        scope = run._course(rule) if limits.rule.cumulative_scope == "course" else list(ctx.history)
        used = _sum([convert(p.accounting[ing_id], unit).value for p in scope if ing_id in p.accounting])
        terms.append((limits.cumulative - used, "cumulative_max_exceeded"))
    return terms


def _remaining(run: _Run, rule: DoseRule, ing_id: str, limits: _Limits) -> tuple[Decimal, str]:
    value, code = min(_terms(run, rule, ing_id, limits), key=lambda t: t[0])
    run.note(
        "remaining_allowance",
        "computed",
        f"{ing_id}: {display(Amount(value, limits.strength.ucum))} ({code})",
    )
    return value, code


def _best(targets: dict[str, Decimal], per_unit: dict[str, Decimal], k_max: int) -> tuple[int, Decimal]:
    """The k minimizing the worst ingredient's relative deviation, ties to the smaller k."""
    best_k, best_dev = 0, Decimal(-1)
    for k in range(1, k_max + 1):
        dev = max(abs(k * per_unit[i] - targets[i]) / targets[i] for i in targets)
        if best_dev < 0 or dev < best_dev:
            best_k, best_dev = k, dev
    return best_k, best_dev


@dataclass(frozen=True, slots=True)
class _Count:
    final: int  # deliverable increments proposed
    labeled: int  # a_x: the increments nearest the target
    nominal: dict[str, Decimal]  # label amount of one increment per ingredient, strength unit
    upper: dict[str, Decimal]  # max(1, u_i), 1 without a fresh estimate


def _count(
    run: _Run,
    limits: dict[str, _Limits],
    remaining: dict[str, tuple[Decimal, str]],
    summary: LotSummary | None,
    path: Path,
    product: ProductRule,
) -> _Count:
    """E32 3.5 steps 6 and 7: a_x over the deliverable set, the feasible set F, then the bound by path."""
    for i, lim in limits.items():
        if lim.target > remaining[i][0]:
            raise run.refuse(remaining[i][1], f"{i}: the target is over a limit", products=[])
    increment = product.deliverable_increment.value
    nominal = {i: increment * lim.strength.value for i, lim in limits.items()}
    k_max = min(
        min(_floor(lim.max_per_administration / nominal[i]) for i, lim in limits.items()), MAX_INCREMENTS
    )
    if k_max < 1:
        raise run.refuse("no_deliverable_amount", "one increment is over the per-administration maximum")
    effective = (
        {i: nominal[i] * summary.potency[i].median for i in limits}
        if path == "compensated" and summary is not None
        else nominal
    )
    k_x, deviation = _best({i: lim.target for i, lim in limits.items()}, effective, k_max)
    if deviation > product.rounding_tolerance_fraction:
        raise run.refuse(
            "no_deliverable_amount", f"nearest amount deviates {text(deviation)} from the target"
        )
    upper = {i: max(ONE, summary.potency[i].high) if summary is not None else ONE for i in limits}
    k_f = min(
        min(k_max, _floor(remaining[i][0] / (nominal[i] * upper[i]))) if remaining[i][0] >= 0 else 0
        for i in limits
    )
    if k_f < 1 or (path != "compensated" and k_x > k_f):
        binding = min(limits, key=lambda i: remaining[i][0] / (nominal[i] * upper[i]))
        raise run.refuse(remaining[binding][1], f"{binding}: the amount is over the remaining allowance")
    final = min(k_x, k_f) if path == "compensated" else k_x
    return _Count(final, k_x, nominal, upper)


def _lines(
    run: _Run,
    limits: dict[str, _Limits],
    remaining: dict[str, tuple[Decimal, str]],
    summary: LotSummary | None,
    count: _Count,
) -> tuple[IngredientLine, ...]:
    """Per ingredient: target, accounting Acc_i(a) = a * S_i * max(1, u_i), expected amounts and R_i."""
    lines = []
    for i, lim in limits.items():
        unit = lim.strength.ucum
        acc = Amount(count.final * count.nominal[i] * count.upper[i], unit)
        run.note("accounting", "computed", f"{i}: {display(acc)}")
        potency = summary.potency[i] if summary is not None else None
        lines.append(
            IngredientLine(
                ingredient_id=i,
                target=Amount(lim.target, unit),
                accounting=acc,
                expected_median=None
                if potency is None
                else Amount(count.final * count.nominal[i] * potency.median, unit),
                expected_low=None
                if potency is None
                else Amount(count.final * count.nominal[i] * potency.low, unit),
                remaining_allowance=Amount(remaining[i][0], unit),
            )
        )
    return tuple(lines)


def _propose(run: _Run) -> Proposal:
    ctx = run.ctx
    line, rule, product, formulary = run.rule()
    run.context(rule)
    kg = run.weight(rule)
    limits = run.limits(rule, product, formulary, kg)
    if ctx.clock_discontinuity:
        raise run.refuse("clock_discontinuity", "the hub clock jumped; a person records a clock correction")
    run.interval(rule)
    lot, summary = run.lot(line, product, formulary)
    path = run.gate(summary, product, formulary)
    remaining = {i: _remaining(run, rule, i, lim) for i, lim in limits.items()}
    count = _count(run, limits, remaining, summary, path, product)
    amount = Amount(count.final * product.deliverable_increment.value, product.deliverable_increment.ucum)
    run.note("amount", "computed", f"{display(amount)} ({path})")
    if amount.value > lot.quantity_now:
        raise run.refuse(
            "insufficient_stock",
            f"{lot.lot_id} holds {text(lot.quantity_now)}, short {text(amount.value - lot.quantity_now)}",
            [x.lot_id for x in ctx.lots if x.product_id == lot.product_id and x.lot_id != lot.lot_id],
        )
    lines = _lines(run, limits, remaining, summary, count)
    alerts = alerts_for(
        ctx,
        rule,
        product,
        formulary,
        path,
        capped=count.final < count.labeled,
        lines=lines,
        min_effective={i: lim.min_effective for i, lim in limits.items()},
        binding={i: remaining[i][1] for i in limits},
    )
    words = ctx.treatments.unit_display
    ingredient_text = ", ".join(
        f"{x.ingredient_id} {display(Amount(count.final * count.nominal[x.ingredient_id], x.target.ucum), words)}"
        for x in lines
    )
    status: Literal["estimated", "stale", "none"] = (
        "estimated" if summary is not None else ("stale" if lot.summary is not None else "none")
    )
    return Proposal(
        outcome="proposal",
        product_id=line.product_id,
        rule_id=rule.rule_id,
        lot_id=lot.lot_id,
        amount=amount,
        amount_text=f"{formulary.name}: {display(amount, words)} ({ingredient_text})",
        ingredients=lines,
        path=path,
        potency_status=status,
        assessment_run_id=summary.run_id if summary is not None else None,
        intervals={} if summary is None else {i: summary.potency[i] for i in limits},
        refusal=None,
        alerts=alerts,
        trace=tuple(run.trace),
        next_dose_earliest_at=ctx.now
        + timedelta(seconds=max(lim.rule.min_interval_seconds for lim in limits.values())),
        live_authorized_at=ctx.policy.live_authorizations.get(line.product_id)
        if ctx.mode == "LIVE"
        else None,
        review_state=rule.review_state,
    )


def manual_findings(
    ctx: DoseContext, request: DoseRequest, lot: LotView, amount: Amount
) -> tuple[dict[str, Amount], tuple[str, ...]]:
    """A manual administration (decision 9 of the brief) is recorded, never refused here: its accounting amounts per
    ingredient (the same `Acc_i` as a proposal, so later limits count it) and every check it fails, stated."""
    run = _Run(request, ctx)
    formulary = ctx.formulary[lot.product_id]
    summary = fresh(lot.summary, ctx, formulary)
    findings = [f"lot_excluded:{reason}" for reason in exclusions(lot, ctx.now)]
    accounting: dict[str, Amount] = {}
    with localcontext() as decimal_ctx:
        decimal_ctx.prec = 34
        for ing in formulary.ingredients:
            upper = max(ONE, summary.potency[ing.ingredient_id].high) if summary is not None else ONE
            accounting[ing.ingredient_id] = Amount(
                amount.value * ing.strength.value * upper, ing.strength.ucum
            )
        rule_id = next(
            (
                x.rule_id
                for lines in ctx.treatments.indications.values()
                for x in lines
                if x.product_id == lot.product_id
            ),
            None,
        )
        rule = ctx.treatments.rules.get(rule_id) if rule_id is not None else None
        if rule is None:
            findings.append("no_reviewed_rule_for_product")
        else:
            product = ctx.treatments.products.get(lot.product_id)
            try:
                if product is None:
                    raise Refused(Refusal("formulary_mismatch", lot.product_id))
                limits = run.limits(rule, product, formulary, None)
                for ingredient_id, lim in limits.items():
                    amount_in_unit = convert(accounting[ingredient_id], lim.strength.ucum).value
                    for remaining, code in _terms(run, rule, ingredient_id, lim):
                        finding = f"{code}:{ingredient_id}"
                        if amount_in_unit > remaining and finding not in findings:
                            findings.append(finding)
            except Refused as refused:
                findings.append(refused.refusal.code)
    ingredients = {i.ingredient_id for i in formulary.ingredients}
    for item in ctx.context.allergies.items if ctx.context.allergies is not None else ():
        if str(item.get("substance_id")) in ingredients | {
            c for c, members in ctx.treatments.allergy_classes.items() if members & ingredients
        }:
            findings.append(f"documented_allergy:{item.get('substance_id')}")
    return accounting, tuple(findings)


__all__ = ["Alert", "Potency", "Refused", "exclusions", "fresh", "propose"]
