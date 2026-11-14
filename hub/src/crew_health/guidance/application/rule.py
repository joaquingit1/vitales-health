"""SYSTEM 8.2: the one deterministic eligibility rule for clinical guidance.

Pure: no I/O, no clock. Every failure and unknown is accumulated in the table order of 8.2 (content, origin,
environment, instruments and stock, skills, operator), then in sorted key order, so the result never depends on the
order in which providers answered. Unknown never authorizes execution; clinical review is only a label.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final, Literal

from crew_health.guidance.application.model import (
    ContentRef,
    Fact,
    GuidanceDecision,
    GuidanceFacts,
    GuidanceRequest,
    OperatorFact,
    StockFact,
    Unknown,
)

type Availability = Literal["aboard", "substituted", "not_aboard", "needs_skill_holder", "unknown"]
type Label = Literal["live", "replay", "simulation_only"]

_LABELS: Final[dict[str, Label]] = {"LIVE": "live", "REPLAY": "replay", "SIM": "simulation_only"}
_HOLDER_FRESHNESS: Final = frozenset({"fresh", "due"})


@dataclass(slots=True)
class _Trail:
    reasons: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def unknown_fact(self, dimension: str, fact: Unknown) -> None:
        entry = f"{dimension}:{fact.provider}:{fact.reason}"
        if entry not in self.unknown:
            self.unknown.append(entry)


def _known_bool(trail: _Trail, dimension: str, fact: Fact[bool], *, bad: bool, reason: str) -> bool:
    """True when the fact is known and not the bad value; an unknown counts as a failure."""
    if isinstance(fact, Unknown):
        trail.unknown_fact(dimension, fact)
        return False
    if fact.value is bad:
        trail.fail(reason)
        return False
    return True


def _content_ok(request: GuidanceRequest, facts: GuidanceFacts, trail: _Trail) -> bool:
    checks = (
        _known_bool(
            trail,
            "source_and_hash_valid",
            facts.source_and_hash_valid,
            bad=False,
            reason="source_or_hash_invalid",
        ),
        _known_bool(trail, "purpose_allowed", facts.purpose_allowed, bad=False, reason="purpose_not_allowed"),
        _known_bool(trail, "withdrawn", facts.withdrawn, bad=True, reason="withdrawn"),
    )
    pinned = facts.pinned_release_matches
    if not pinned:
        trail.fail("pinned_release_mismatch")
    origin = True
    if request.origin_mode in ("LIVE", "REPLAY"):
        origin = _known_bool(
            trail, "simulation_only", facts.simulation_only, bad=True, reason="simulation_only_content"
        )
    shape = True
    if request.requirements.operator == "skill" and request.requirements.operator_skill_id is None:
        trail.fail("operator_skill_unset")
        shape = False
    return all(checks) and pinned and origin and shape


def _environment(
    request: GuidanceRequest, facts: GuidanceFacts, trail: _Trail
) -> Literal["yes", "no", "unknown"]:
    fact = facts.environment_applicable
    if isinstance(fact, Unknown):
        trail.unknown_fact("environment", fact)
        trail.fail("not_confirmed_here" if request.fixed_first_screen else "environment_unknown")
        return "unknown"
    if not fact.value:
        trail.fail("environment_not_applicable")
        return "no"
    return "yes"


def _stock_missing(key: str, fact: Fact[StockFact] | None, trail: _Trail) -> tuple[bool, bool]:
    """Returns (missing, unknown) for one required supply."""
    if fact is None:
        trail.fail(f"supply_missing:{key}")
        return True, False
    if isinstance(fact, Unknown):
        trail.unknown_fact(f"stock:{key}", fact)
        return False, True
    stock = fact.value
    if not stock.aboard or (stock.quantity_now is not None and stock.quantity_now.value <= 0):
        trail.fail(f"supply_missing:{key}")
        return True, False
    if stock.consumable and stock.per_use is not None and stock.quantity_now is not None:
        if stock.per_use.unit != stock.quantity_now.unit:
            trail.unknown_fact(f"stock:{key}", Unknown("stock", "unit_mismatch"))
            return False, True
        if stock.quantity_now.value < stock.per_use.value:
            trail.fail(f"supply_insufficient:{key}")
            return True, False
    return False, False


def _equipment(request: GuidanceRequest, facts: GuidanceFacts, trail: _Trail) -> tuple[bool, bool]:
    """Returns (some equipment missing, some equipment unknown)."""
    missing = unknown = False
    needed = request.requirements.instruments
    if needed:
        here = facts.instruments_here
        if isinstance(here, Unknown):
            trail.unknown_fact("instruments", here)
            unknown = True
        else:
            for instrument in sorted(needed - here.value):
                trail.fail(f"instrument_missing:{instrument}")
                missing = True
    for key in sorted(request.requirements.supplies):
        supply_missing, supply_unknown = _stock_missing(key, facts.stock.get(key), trail)
        missing = missing or supply_missing
        unknown = unknown or supply_unknown
    return missing, unknown


def _required_skills(request: GuidanceRequest) -> tuple[str, ...]:
    skills = set(request.requirements.skills)
    if request.requirements.operator == "skill" and request.requirements.operator_skill_id is not None:
        skills.add(request.requirements.operator_skill_id)
    return tuple(sorted(skills))


def _holds(person: OperatorFact, skill: str) -> bool:
    return person.skill_freshness.get(skill) in _HOLDER_FRESHNESS


def _skills(request: GuidanceRequest, facts: GuidanceFacts, trail: _Trail) -> tuple[bool, bool]:
    """Returns (some skill has no fresh or due non-subject holder, operators unknown)."""
    required = _required_skills(request)
    if not required:
        return False, False
    if isinstance(facts.operators, Unknown):
        trail.unknown_fact("operators", facts.operators)
        return False, True
    missing = False
    subject = request.subject_person_id
    for skill in required:
        if not any(p.person_id != subject and _holds(p, skill) for p in facts.operators.value):
            trail.fail(f"skill_holder_missing:{skill}")
            missing = True
    return missing, False


def _eligible(request: GuidanceRequest, person: OperatorFact) -> bool:
    if not (person.awake and person.on_site and not person.on_eva):
        return False
    requirements = request.requirements
    subject = request.subject_person_id
    if requirements.operator == "any_crew":
        return True
    if person.person_id == subject:
        return False
    if requirements.operator == "skill":
        return requirements.operator_skill_id is not None and _holds(person, requirements.operator_skill_id)
    return person.officer_rank is not None


def _operators(request: GuidanceRequest, facts: GuidanceFacts, trail: _Trail) -> tuple[tuple[str, ...], bool]:
    """Returns (who_can in roster order, operator denied or unknown)."""
    if isinstance(facts.operators, Unknown):
        trail.unknown_fact("operators", facts.operators)
        return (), True
    ordered = sorted(facts.operators.value, key=lambda p: (p.roster_order, p.person_id))
    who_can = tuple(p.person_id for p in ordered if _eligible(request, p))
    denied = False
    if not who_can:
        trail.fail("no_operator_available")
        denied = True
    proposed = request.proposed_operator_id
    if proposed is not None and proposed not in who_can:
        trail.fail("proposed_operator_not_eligible")
        denied = True
    return who_can, denied


def _availability(equipment: tuple[bool, bool], skills: tuple[bool, bool]) -> Availability:
    if equipment[0]:
        return "not_aboard"
    if skills[0]:
        return "needs_skill_holder"
    if equipment[1] or skills[1]:
        return "unknown"
    return "aboard"


def decide(request: GuidanceRequest, facts: GuidanceFacts) -> GuidanceDecision:
    trail = _Trail()
    content_ok = _content_ok(request, facts, trail)
    environment = _environment(request, facts, trail)
    equipment = _equipment(request, facts, trail)
    if request.fixed_first_screen and equipment[1]:
        trail.fail("not_confirmed_here")
    skills = _skills(request, facts, trail)
    who_can, operator_blocked = _operators(request, facts, trail)
    availability = _availability(equipment, skills)
    if not content_ok:
        outcome: Literal["show", "not_here", "not_supported"] = "not_supported"
    elif environment == "no":
        outcome = "not_here"
    elif environment == "unknown" and not request.fixed_first_screen:
        outcome = "not_supported"
    else:
        outcome = "show"
    executable = outcome == "show" and availability == "aboard" and not operator_blocked and not trail.unknown
    return GuidanceDecision(
        outcome=outcome,
        availability=availability,
        executable=executable,
        who_can=who_can,
        reasons=tuple(trail.reasons),
        unknown=tuple(trail.unknown),
        label=_LABELS[request.origin_mode],
        clinical_review=facts.clinical_review,
        substitute=None,
    )


def apply_substitute(
    primary: GuidanceDecision, substitute: ContentRef, substitute_decision: GuidanceDecision
) -> GuidanceDecision:
    """Combine a primary result whose equipment is missing with the configured substitute's own result.

    The substitute is shown as the package's equipment substitute only when its own content checks pass and its
    own instruments and supplies are aboard; it is executable only when its own decision is executable.
    """
    if primary.outcome != "show" or primary.availability != "not_aboard":
        return primary
    prefixed_reasons = tuple("substitute/" + r for r in substitute_decision.reasons)
    prefixed_unknown = tuple("substitute/" + u for u in substitute_decision.unknown)
    has_equipment = substitute_decision.outcome == "show" and substitute_decision.availability in (
        "aboard",
        "needs_skill_holder",
    )
    if not has_equipment:
        return replace(
            primary,
            reasons=(*primary.reasons, "substitute_unavailable", *prefixed_reasons),
            unknown=(*primary.unknown, *prefixed_unknown),
        )
    return replace(
        primary,
        outcome="show_with_substitute",
        availability="substituted",
        executable=substitute_decision.executable,
        who_can=substitute_decision.who_can,
        reasons=(*primary.reasons, *prefixed_reasons),
        unknown=(*primary.unknown, *prefixed_unknown),
        substitute=substitute,
    )
