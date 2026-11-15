"""treatments.json, medicines.json and the pharmacy formulary as domain values, with checks M-L1 to M-L9 (E32 8.4).

Inputs are schema-valid JSON objects (the adapter validates against contracts/api/schemas first). A finding is a
(check, pointer, text) triple; any finding keeps the documents from loading, so the last good version stays."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast

from crew_health.medicines.domain.model import (
    ALERT_KINDS,
    AlertPolicy,
    DoseRule,
    FormularyIngredient,
    FormularyProduct,
    IndicationLine,
    IngredientRule,
    Interaction,
    MedicinesPolicy,
    PeriodLimit,
    PotencyPolicy,
    ProductRule,
    Treatments,
    WeightPolicy,
)
from crew_health.medicines.domain.units import Amount, UnitIncompatible, convert, known

type Json = Mapping[str, object]
PER_UNITS: Mapping[str, str] = {"tablet": "{tbl}", "capsule": "{cap}", "dose": "{dose}", "mL": "mL"}


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    pointer: str
    text: str


class DocumentsInvalid(ValueError):
    def __init__(self, findings: Sequence[Finding]) -> None:
        super().__init__("; ".join(f"{f.check} {f.pointer}: {f.text}" for f in findings))
        self.findings = tuple(findings)


def _o(value: object) -> Json:
    return cast(Json, value)


def _list(value: object) -> list[Json]:
    return cast(list[Json], value) if isinstance(value, list) else []


def _amount(value: object) -> Amount | None:
    if value is None:
        return None
    raw = _o(value)
    return Amount(Decimal(str(raw["value"])), str(raw["ucum"]))


def _req(value: object) -> Amount:
    amount = _amount(value)
    if amount is None:
        raise ValueError("amount required")
    return amount


def formulary_products(formulary: Json) -> dict[str, FormularyProduct]:
    out = {}
    for raw in _list(formulary.get("products")):
        ingredients = []
        per = "{unknown}"
        for ing in _list(raw.get("ingredients")):
            strength = _o(ing["strength"])
            per = PER_UNITS.get(str(strength["per"]), "{" + str(strength["per"]) + "}")
            ingredients.append(
                FormularyIngredient(
                    str(ing["ingredient_id"]), Amount(Decimal(str(strength["value"])), str(strength["unit"]))
                )
            )
        adjustment = _o(raw.get("potency_adjustment") or {})
        out[str(raw["product_id"])] = FormularyProduct(
            product_id=str(raw["product_id"]),
            name=str(raw["name"]),
            per=per,
            ingredients=tuple(ingredients),
            narrow_therapeutic_index=bool(raw.get("narrow_therapeutic_index")),
            potency_adjustment_allowed=bool(adjustment.get("allowed")),
            degradant_profile_reviewed=bool(raw.get("degradant_profile_reviewed")),
        )
    return out


def _rule(index: int, raw: Json) -> DoseRule:
    ingredients = tuple(
        IngredientRule(
            ingredient_id=str(i["ingredient_id"]),
            dose=_amount(i.get("dose")),
            dose_per_kg=_amount(i.get("dose_per_kg")),
            max_per_administration=_req(i["max_per_administration"]),
            max_per_period=tuple(
                PeriodLimit(int(cast(int, p["period_seconds"])), _req(p["limit"]))
                for p in _list(i["max_per_period"])
            ),
            max_cumulative=_amount(i.get("max_cumulative")),
            cumulative_scope=cast(Literal["course", "mission"], i["cumulative_scope"]),
            min_interval_seconds=int(cast(int, i["min_interval_seconds"])),
            min_effective=_amount(i.get("min_effective")),
            course_seconds=None if i.get("course_seconds") is None else int(cast(int, i["course_seconds"])),
        )
        for i in _list(raw["ingredients"])
    )
    return DoseRule(
        rule_id=str(raw["rule_id"]),
        review_state=str(raw["review_state"]),
        ingredients=ingredients,
        organ_cautions=tuple(str(x) for x in cast(list[object], raw["organ_cautions"])),
        condition_cautions=tuple(str(x) for x in cast(list[object], raw["condition_cautions"])),
        source_ids=tuple(
            sorted({str(c["source_id"]) for p in _list(raw["provenance"]) for c in _list(p["citations"])})
        ),
        pointer=f"/dose_rules/{index}",
    )


def parse_treatments(raw: Json, sha256: str) -> Treatments:
    weight = raw.get("weight")
    w = _o(weight) if weight is not None else None
    return Treatments(
        version=str(raw["version"]),
        sha256=sha256,
        example=bool(raw["example"]),
        review_state=str(raw["review_state"]),
        indications={
            str(i["condition_id"]): tuple(
                IndicationLine(
                    cast(Literal["first_choice", "alternative"], x["line"]),
                    str(x["product_id"]),
                    str(x["rule_id"]),
                )
                for x in _list(i["lines"])
            )
            for i in _list(raw["indications"])
        },
        rules={str(r["rule_id"]): _rule(n, r) for n, r in enumerate(_list(raw["dose_rules"]))},
        products={
            str(p["product_id"]): ProductRule(
                product_id=str(p["product_id"]),
                deliverable_increment=_req(p["deliverable_increment"]),
                rounding_tolerance_fraction=Decimal(str(p["rounding_tolerance_fraction"])),
                high_alert=bool(p["high_alert"]),
                confirm_by=cast(Literal["crew_medical_officer", "any_trained_crew"], p["confirm_by"]),
                consult_ground_first=bool(p["consult_ground_first"]),
                therapeutic_classes=tuple(str(x) for x in cast(list[object], p["therapeutic_classes"])),
                spaceflight_cautions=tuple(str(x) for x in cast(list[object], p["spaceflight_cautions"])),
                pointer=f"/products/{n}",
                assay_acceptance=_acceptance(p.get("assay_acceptance")),
            )
            for n, p in enumerate(_list(raw["products"]))
        },
        weight=None
        if w is None
        else WeightPolicy(
            Decimal(str(w["plausible_min_kg"])),
            Decimal(str(w["plausible_max_kg"])),
            int(cast(int, w["max_age_seconds"])),
            Decimal(str(w["max_change_fraction"])),
            int(cast(int, w["change_window_seconds"])),
        ),
        catastrophic={
            str(c["ingredient_id"]): _req(c["per_administration"]) for c in _list(raw["catastrophic_limits"])
        },
        allergy_classes={
            str(c["class_id"]): frozenset(str(x) for x in cast(list[object], c["ingredient_ids"]))
            for c in _list(raw["allergy_classes"])
        },
        cross_sensitivity=tuple(
            frozenset(str(x) for x in cast(list[object], g["members"]))
            for g in _list(raw["cross_sensitivity_groups"])
        ),
        interactions=tuple(
            Interaction(
                (str(cast(list[object], x["pair"])[0]), str(cast(list[object], x["pair"])[1])),
                str(x["severity"]),
                str(x["management_text"]),
            )
            for x in _list(raw["interactions"])
        ),
        policies={
            str(p["kind"]): AlertPolicy(
                str(p["kind"]),
                cast(Literal["override_required", "acknowledge", "info"], p["level"]),
                tuple(str(x) for x in cast(list[object], p["override_roles"])),
                tuple(str(x) for x in cast(list[object], p["reason_codes"])),
            )
            for p in _list(raw["alert_policies"])
        },
        context_max_age_seconds=int(cast(int, raw["context_max_age_seconds"])),
        unit_display={str(k): str(v) for k, v in _o(raw["unit_display"]).items()},
    )


def parse_policy(raw: Json, sha256: str) -> MedicinesPolicy:
    potency = _o(raw["potency_policy"])
    return MedicinesPolicy(
        version=str(raw["version"]),
        sha256=sha256,
        example=bool(raw["example"]),
        review_state=str(raw["review_state"]),
        environment_zones={
            str(k): tuple(str(z) for z in cast(list[object], v))
            for k, v in _o(raw["environment_zones"]).items()
        },
        proposal_ttl_seconds=int(cast(int, raw["proposal_ttl_seconds"])),
        max_assessment_age_seconds=int(cast(int, raw["max_assessment_age_seconds"])),
        potency=PotencyPolicy(
            mode=cast(Literal["compensate", "information_only"], potency["mode"]),
            require_measured_assay=bool(potency["require_measured_assay"]),
            exclude_narrow_therapeutic_index=bool(potency["exclude_narrow_therapeutic_index"]),
            exclude_high_alert=bool(potency["exclude_high_alert"]),
            require_degradant_profile_reviewed=bool(potency["require_degradant_profile_reviewed"]),
            require_measured_storage_history=bool(potency["require_measured_storage_history"]),
            require_validated_engine=bool(potency["require_validated_engine"]),
            validated_engine_versions=tuple(
                str(x) for x in cast(list[object], potency["validated_engine_versions"])
            ),
        ),
        live_authorizations={
            str(a["product_id"]): str(a["authorized_at"])
            for a in _list(raw["live_compensation_authorizations"])
        },
    )


def _acceptance(raw: object) -> tuple[Decimal, Decimal] | None:
    if not isinstance(raw, dict):
        return None
    band = cast(Json, raw)
    return Decimal(str(band["lower_fraction"])), Decimal(str(band["upper_fraction"]))


def _cited(raw: Json, sources: set[str], prefix: str, out: list[Finding]) -> None:
    for key, value in raw.items():
        if key == "citations":
            for n, c in enumerate(_list(value)):
                if str(c["source_id"]) not in sources:
                    out.append(
                        Finding("M-SRC", f"{prefix}/citations/{n}", f"unknown source {c['source_id']}")
                    )
        elif isinstance(value, dict):
            _cited(cast(Json, value), sources, f"{prefix}/{key}", out)
        elif isinstance(value, list):
            for n, item in enumerate(value):
                if isinstance(item, dict):
                    _cited(cast(Json, item), sources, f"{prefix}/{key}/{n}", out)


def _check_provenance(treatments_raw: Json, out: list[Finding]) -> None:
    """M-SRC: every non-null dose field of every rule carries a citation."""
    sources = {str(s["source_id"]) for s in _list(treatments_raw["sources"])}
    _cited(treatments_raw, sources, "", out)
    optional = ("dose", "dose_per_kg", "max_cumulative", "min_effective", "course_seconds")
    for n, raw in enumerate(_list(treatments_raw["dose_rules"])):
        fields = {str(x["field"]) for x in _list(raw["provenance"])}
        for m, ing in enumerate(_list(raw["ingredients"])):
            base = f"/ingredients/{m}"
            needed = [base + "/max_per_administration", base + "/min_interval_seconds"]
            needed += [f"{base}/{k}" for k in optional if ing.get(k) is not None]
            needed += [f"{base}/max_per_period/{k}" for k in range(len(_list(ing["max_per_period"])))]
            for field in needed:
                if field not in fields:
                    out.append(Finding("M-SRC", f"/dose_rules/{n}/provenance", f"{field} has no citation"))


def _check_line(
    t: Treatments, formulary: Mapping[str, FormularyProduct], condition: str, line: IndicationLine
) -> list[Finding]:
    """M-L1 and M-L2 for one indication line: rule, product and formulary agree, and every unit converts."""
    where = f"/indications/{condition}"
    out: list[Finding] = []
    if line.rule_id not in t.rules:
        out.append(Finding("M-L1", where, f"unknown rule {line.rule_id}"))
    if line.product_id not in t.products:
        return [*out, Finding("M-L1", where, f"{line.product_id} has no product entry")]
    product = formulary.get(line.product_id)
    if product is None:
        return [*out, Finding("M-L1", where, f"{line.product_id} not in the formulary")]
    rule = t.rules.get(line.rule_id)
    if rule is None:
        return out
    if {i.ingredient_id for i in rule.ingredients} != {i.ingredient_id for i in product.ingredients}:
        out.append(Finding("M-L1", rule.pointer, f"ingredients differ from {line.product_id}"))
    strengths = {i.ingredient_id: i.strength for i in product.ingredients}
    for m, ing_rule in enumerate(rule.ingredients):
        if ing_rule.ingredient_id in strengths:
            _units(ing_rule, strengths[ing_rule.ingredient_id], f"{rule.pointer}/ingredients/{m}", t, out)
    return out


def _check_treatments(
    t: Treatments,
    formulary: Mapping[str, FormularyProduct],
    condition_ids: frozenset[str],
    out: list[Finding],
) -> None:
    for condition, lines in t.indications.items():
        if condition not in condition_ids:
            out.append(Finding("M-L8", f"/indications/{condition}", "condition not in the kit map"))
        for line in lines:
            out.extend(_check_line(t, formulary, condition, line))
    for condition_id in (c for r in t.rules.values() for c in r.condition_cautions):
        if condition_id not in condition_ids:
            out.append(Finding("M-L8", "/dose_rules", f"caution condition {condition_id} not in the kit map"))
    for product_id, product_rule in t.products.items():
        if product_id not in formulary:
            out.append(Finding("M-L1", product_rule.pointer, f"{product_id} not in the formulary"))
        elif product_rule.deliverable_increment.ucum != formulary[product_id].per:
            words = "increment unit differs from the formulary strength unit"
            out.append(Finding("M-L2", product_rule.pointer, words))
    if any(i.dose_per_kg is not None for r in t.rules.values() for i in r.ingredients) and t.weight is None:
        out.append(Finding("M-L5", "/weight", "a per-kilogram rule needs sourced weight bounds"))


def _check_alert_policies(treatments_raw: Json, out: list[Finding]) -> None:
    """M-L8: exactly one policy per alert kind, and no unknown kind."""
    kinds = [str(x["kind"]) for x in _list(treatments_raw["alert_policies"])]
    out.extend(
        Finding("M-L8", "/alert_policies", f"{kind} needs exactly one policy")
        for kind in ALERT_KINDS
        if kinds.count(kind) != 1
    )
    out.extend(
        Finding("M-L8", "/alert_policies", f"unknown alert kind {kind}")
        for kind in kinds
        if kind not in ALERT_KINDS
    )


def _check_policy(
    t: Treatments,
    p: MedicinesPolicy,
    policy_raw: Json,
    formulary: Mapping[str, FormularyProduct],
    zone_ids: frozenset[str],
    out: list[Finding],
) -> None:
    """M-L4 (zones exist) and M-L9 (one live authorization per compensable product)."""
    for env, zones in p.environment_zones.items():
        out.extend(
            Finding("M-L4", f"/environment_zones/{env}", f"unknown zone {zone}")
            for zone in zones
            if zone not in zone_ids
        )
    seen: set[str] = set()
    for n, auth in enumerate(_list(policy_raw["live_compensation_authorizations"])):
        product_id = str(auth["product_id"])
        where = f"/live_compensation_authorizations/{n}"
        if product_id in seen:
            out.append(Finding("M-L9", where, "one entry per product"))
        seen.add(product_id)
        if product_id not in t.products or not formulary.get(product_id, _NO).potency_adjustment_allowed:
            out.append(Finding("M-L9", where, "product cannot be compensated"))


def check(
    treatments_raw: Json,
    policy_raw: Json,
    formulary: Mapping[str, FormularyProduct],
    condition_ids: frozenset[str],
    zone_ids: frozenset[str],
) -> tuple[Finding, ...]:
    """M-L1, M-L2, M-L4, M-L5, M-L8, M-L9 and source coverage. M-L3 (channels) passes while no channel is bound;
    M-L6 depends on the mission mode and is `activation_findings`."""
    out: list[Finding] = []
    t = parse_treatments(treatments_raw, "")
    p = parse_policy(policy_raw, "")
    _check_provenance(treatments_raw, out)
    _check_treatments(t, formulary, condition_ids, out)
    _check_alert_policies(treatments_raw, out)
    _check_policy(t, p, policy_raw, formulary, zone_ids, out)
    return tuple(out)


_NO = FormularyProduct("", "", "", (), False, False, False)


def _units(ing: IngredientRule, strength: Amount, pointer: str, t: Treatments, out: list[Finding]) -> None:
    amounts = {
        "max_per_administration": ing.max_per_administration,
        "dose": ing.dose,
        "dose_per_kg": ing.dose_per_kg,
        "max_cumulative": ing.max_cumulative,
        "min_effective": ing.min_effective,
        "catastrophic": t.catastrophic.get(ing.ingredient_id),
    }
    amounts.update({f"max_per_period/{k}": p.limit for k, p in enumerate(ing.max_per_period)})
    values: dict[str, Decimal] = {}
    for name, amount in amounts.items():
        if amount is None:
            continue
        if not known(amount.ucum):
            out.append(Finding("M-L2", f"{pointer}/{name}", f"unknown unit {amount.ucum}"))
            continue
        try:
            values[name] = convert(amount, strength.ucum).value
        except UnitIncompatible:
            out.append(
                Finding("M-L2", f"{pointer}/{name}", f"{amount.ucum} does not convert to {strength.ucum}")
            )
    cap = values.get("max_per_administration")
    if cap is None:
        return
    if "dose" in values and values["dose"] > cap:
        out.append(Finding("M-L5", f"{pointer}/dose", "dose above max_per_administration"))
    if "min_effective" in values and values["min_effective"] > cap:
        out.append(Finding("M-L5", f"{pointer}/min_effective", "min_effective above max_per_administration"))
    if "catastrophic" in values and cap > values["catastrophic"]:
        out.append(Finding("M-L5", f"{pointer}/max_per_administration", "above the catastrophic limit"))
    for k, period in enumerate(ing.max_per_period):
        name = f"max_per_period/{k}"
        if name in values and values[name] < cap:
            out.append(
                Finding(
                    "M-L5",
                    f"{pointer}/{name}",
                    f"limit below one administration over {period.period_seconds} s",
                )
            )


def activation_findings(treatments: Treatments, policy: MedicinesPolicy, mode: str) -> tuple[Finding, ...]:
    """M-L6: an example document cannot be active while the mission mode is LIVE."""
    if mode != "LIVE":
        return ()
    return tuple(
        Finding("M-L6", "/example", f"{name} is an example and the mission is LIVE")
        for name, example in (("treatments.json", treatments.example), ("medicines.json", policy.example))
        if example
    )
