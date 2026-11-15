"""Alerts of a proposal (E32 3.7). Levels, override roles and reason codes come only from `alert_policies`."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from crew_health.medicines.domain.model import (
    Alert,
    ContextPart,
    DoseContext,
    DoseRule,
    FormularyProduct,
    IngredientLine,
    Path,
    ProductRule,
)
from crew_health.medicines.domain.units import Amount, display


def _items(part: ContextPart | None) -> tuple[Mapping[str, object], ...]:
    return () if part is None or part.state not in ("documented",) else part.items


def _active(item: Mapping[str, object], now: datetime) -> bool:
    until = item.get("until")
    return until is None or (isinstance(until, datetime) and until > now)


type Found = list[tuple[str, str, str]]  # (kind, subject, text)


def _allergies(ctx: DoseContext, ingredients: set[str], classes: set[str]) -> Found:
    t = ctx.treatments
    found: Found = []
    for item in _items(ctx.context.allergies):
        kind, substance = item.get("substance_kind"), str(item.get("substance_id"))
        if kind == "ingredient" and substance in ingredients:
            found.append(("allergy_same_ingredient", substance, f"Documented allergy to {substance}"))
            continue
        if kind == "allergy_class" and substance in classes:
            found.append(("allergy_class", substance, f"Documented allergy to the {substance} class"))
            continue
        allergic = {substance}
        if kind == "ingredient":
            allergic |= {c for c, members in t.allergy_classes.items() if substance in members}
        else:
            allergic |= set(t.allergy_classes.get(substance, ()))
        if any(group & allergic and group & (ingredients | classes) for group in t.cross_sensitivity):
            found.append(("cross_sensitivity", substance, f"Cross-sensitivity with documented {substance}"))
    return found


def _other_medicines(ctx: DoseContext, ingredients: set[str]) -> Found:
    others = [i for i in _items(ctx.context.other_medicines) if _active(i, ctx.now)]
    other_ids = {str(i.get("ingredient_id")) for i in others}
    found: Found = []
    for interaction in ctx.treatments.interactions:
        a, b = interaction.pair
        if (a in ingredients and b in other_ids) or (b in ingredients and a in other_ids):
            other = b if a in ingredients else a
            found.append(
                ("interaction", other, f"{interaction.severity}: with {other}. {interaction.management_text}")
            )
    for ingredient in sorted(ingredients & other_ids):
        found.append(("duplicate_ingredient", ingredient, f"{ingredient} is also taken outside the hub"))
        found.append(
            (
                "outside_medicine_overlap",
                ingredient,
                f"amounts of {ingredient} taken outside the hub are unknown",
            )
        )
    return found


def _cautions(ctx: DoseContext, rule: DoseRule, product: ProductRule) -> Found:
    found: Found = []
    for organ in rule.organ_cautions:
        part = getattr(ctx.context, organ)
        if part is not None and part.state == "impaired":
            found.append(("organ_caution", organ, f"{organ} function impaired"))
    conditions = {str(i.get("condition_id")) for i in _items(ctx.context.conditions)}
    for condition in rule.condition_cautions:
        if condition in conditions:
            found.append(("condition_caution", condition, f"caution with condition {condition}"))
    for index, words in enumerate(product.spaceflight_cautions):
        found.append(("spaceflight_caution", str(index), words))
    if product.consult_ground_first:
        found.append(
            (
                "consult_ground_first",
                product.product_id,
                "Contact the ground when the link allows; never wait",
            )
        )
    return found


def _shortfall_text(line: IngredientLine, floor: Decimal | None, binding: str | None) -> str:
    if line.expected_median is None or line.expected_low is None:
        return ""
    short = max(line.target.value - line.expected_median.value, Decimal(0))
    if floor is None:
        low_words = "minimum effective amount not sourced"
    elif line.expected_low.value < floor:
        low_words = "lower end under the minimum effective amount"
    else:
        low_words = "lower end at or above the minimum effective amount"
    return (
        f"{line.ingredient_id}: target {display(line.target)}, expected {display(line.expected_median)} "
        f"(low {display(line.expected_low)}), short {display(Amount(short, line.target.ucum))}, "
        f"{low_words}, binding {binding}"
    )


def _shortfall(
    lines: tuple[IngredientLine, ...],
    capped: bool,
    min_effective: Mapping[str, Decimal | None],
    binding: Mapping[str, str],
) -> list[str]:
    """potency_shortfall: the expected median is under the target, or the amount was capped by a limit. It never
    calls the amount effective: without a sourced minimum effective amount it says so."""
    out = []
    for line in lines:
        if line.expected_median is None or line.expected_low is None:
            continue
        if line.target.value - line.expected_median.value <= 0 and not capped:
            continue
        out.append(
            _shortfall_text(line, min_effective.get(line.ingredient_id), binding.get(line.ingredient_id))
        )
    return out


def alerts_for(
    ctx: DoseContext,
    rule: DoseRule,
    product: ProductRule,
    formulary: FormularyProduct,
    path: Path,
    *,
    capped: bool,
    lines: tuple[IngredientLine, ...],
    min_effective: Mapping[str, Decimal | None],
    binding: Mapping[str, str],
) -> tuple[Alert, ...]:
    t = ctx.treatments
    ingredients = {i.ingredient_id for i in formulary.ingredients}
    classes = {c for c, members in t.allergy_classes.items() if members & ingredients}
    found = (
        _allergies(ctx, ingredients, classes)
        + _other_medicines(ctx, ingredients)
        + _cautions(ctx, rule, product)
    )
    if path in ("compensated", "labeled_with_estimate"):
        shortfall = _shortfall(lines, capped, min_effective, binding)
        if shortfall:
            found.append(("potency_shortfall", product.product_id, "; ".join(shortfall)))
    out = []
    for kind, subject, words in found:
        policy = t.policies[kind]
        out.append(
            Alert(
                alert_id=f"{kind}:{subject}",
                kind=kind,
                level=policy.level,
                text=words,
                reason_codes=policy.reason_codes,
                override_roles=policy.override_roles,
            )
        )
    unique = {a.alert_id: a for a in out}
    return tuple(unique[k] for k in sorted(unique))
