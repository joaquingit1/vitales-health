"""Parsed configuration the role engine reads (CONTRACTS 11 and 24); every word and number comes from here."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from crew_health.roles.domain.errors import RulesInvalid

type RoleId = Literal["lead_helper", "second_helper", "kit_fetcher", "medical_officer", "communicator"]
type ReasonCode = Literal[
    "first_officer",
    "officer_is_subject",
    "officer_on_eva",
    "officer_asleep",
    "second_officer",
    "freshest_skill",
    "free_to_help",
    "changed_by_helper",
]

ROLE_IDS: tuple[str, ...] = ("lead_helper", "second_helper", "kit_fetcher", "medical_officer", "communicator")
REASON_CODES: tuple[str, ...] = (
    "first_officer",
    "officer_is_subject",
    "officer_on_eva",
    "officer_asleep",
    "second_officer",
    "freshest_skill",
    "free_to_help",
    "changed_by_helper",
)
# CONTRACTS 11 placeholder table, row "role-rules reason_phrases" (loader check 13, R-L2).
REASON_PLACEHOLDERS: frozenset[str] = frozenset(
    {"officer_name", "eva_word", "time", "skill_short", "day_label", "person_name"}
)
READINESS_PLACEHOLDERS: frozenset[str] = frozenset({"skill_short", "n"})
CADENCES: frozenset[str] = frozenset({"daily", "weekly", "monthly", "every_n_days"})
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
SAFETY_EXCLUSIONS: frozenset[str] = frozenset({"never_subject", "not_on_eva_for_on_site"})


@dataclass(frozen=True, slots=True)
class RoleDef:
    role_id: str
    label: str
    on_site: bool


@dataclass(frozen=True, slots=True)
class EligibilityRule:
    rule_id: str
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class CascadeStep:
    kind: Literal["officer_rank", "freshest_skill_holder"]
    rank: int | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class RoleNeed:
    role_id: str
    skill_id: str | None
    task_text: str | None
    required: bool


@dataclass(frozen=True, slots=True)
class RoleRules:
    version: str
    notice: str | None
    roles: Mapping[str, RoleDef]
    role_order: tuple[str, ...]
    eligibility: Mapping[str, EligibilityRule]
    cascade: tuple[CascadeStep, ...]
    flow_roles: Mapping[str, tuple[RoleNeed, ...]]
    default_roles: tuple[RoleNeed, ...]
    plan_step_role: str
    reason_phrases: Mapping[str, str]

    def needs_for_flow(self, flow_id: str | None) -> tuple[RoleNeed, ...]:
        if flow_id is None:
            return ()
        return self.flow_roles.get(flow_id, self.default_roles)

    def prefers_awake(self) -> bool:
        rule = self.eligibility.get("awake_first")
        return rule is not None and rule.kind == "prefer"

    def one_role_each(self) -> bool:
        rule = self.eligibility.get("one_role_each")
        return rule is not None and rule.kind == "exclude"


@dataclass(frozen=True, slots=True)
class Freshness:
    cadence: str
    n: int | None
    due_after_periods: int
    stale_after_periods: int
    basis: str
    to_be_set_by: str | None


@dataclass(frozen=True, slots=True)
class DrillScenario:
    scenario_id: str
    needs_supplies: tuple[str, ...]
    practice_on: str | None


@dataclass(frozen=True, slots=True)
class SkillDef:
    skill_id: str
    name: str
    short_name: str
    freshness: Freshness
    drill_scenarios: Mapping[str, DrillScenario]


@dataclass(frozen=True, slots=True)
class Calendar:
    week_days: int
    month_days: int


@dataclass(frozen=True, slots=True)
class Words:
    unknown: str
    freshness_words: Mapping[str, str]
    readiness_words: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PracticeRules:
    patch_designs: tuple[tuple[str, int | None, str], ...]
    core_skill_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RolesConfig:
    """Everything the roles module reads from one release; built once per release version."""

    release_id: str
    rules: RoleRules
    skills: Mapping[str, SkillDef]
    skill_order: tuple[str, ...]
    calendar: Calendar
    words: Words
    eva_words: Mapping[str, str]
    practice: PracticeRules | None


def _obj(value: object, pointer: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise RulesInvalid(((pointer, "expected an object"),))
    return {str(k): v for k, v in value.items()}


def _list(value: object, pointer: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise RulesInvalid(((pointer, "expected an array"),))
    return value


def _str(value: object, pointer: str) -> str:
    if not isinstance(value, str) or not value:
        raise RulesInvalid(((pointer, "expected a non-empty string"),))
    return value


def _opt_str(value: object, pointer: str) -> str | None:
    return None if value is None else _str(value, pointer)


def _int(value: object, pointer: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RulesInvalid(((pointer, f"expected an integer >= {minimum}"),))
    return value


def _bool(value: object, pointer: str) -> bool:
    if not isinstance(value, bool):
        raise RulesInvalid(((pointer, "expected a boolean"),))
    return value


def placeholders(template: str) -> frozenset[str]:
    return frozenset(_PLACEHOLDER.findall(template))


def _need(raw: object, pointer: str, roles: Mapping[str, RoleDef]) -> RoleNeed:
    item = _obj(raw, pointer)
    role_id = _str(item.get("role_id"), pointer + "/role_id")
    if role_id not in roles:
        raise RulesInvalid(((pointer + "/role_id", "role not defined in roles[]"),))
    return RoleNeed(
        role_id=role_id,
        skill_id=_opt_str(item.get("skill_id"), pointer + "/skill_id"),
        task_text=_opt_str(item.get("task_text"), pointer + "/task_text"),
        required=_bool(item.get("required"), pointer + "/required"),
    )


def parse_role_rules(doc: Mapping[str, object]) -> RoleRules:
    """Parse role-rules.json and apply loader checks 13 and 16 for this document (R-L2, cascade, exclusions)."""
    roles: dict[str, RoleDef] = {}
    for i, raw in enumerate(_list(doc.get("roles"), "/roles")):
        item = _obj(raw, f"/roles/{i}")
        role_id = _str(item.get("role_id"), f"/roles/{i}/role_id")
        if role_id not in ROLE_IDS or role_id in roles:
            raise RulesInvalid(((f"/roles/{i}/role_id", "unknown or repeated role"),))
        roles[role_id] = RoleDef(
            role_id,
            _str(item.get("label"), f"/roles/{i}/label"),
            _bool(item.get("on_site"), f"/roles/{i}/on_site"),
        )
    eligibility: dict[str, EligibilityRule] = {}
    for i, raw in enumerate(_list(doc.get("eligibility"), "/eligibility")):
        item = _obj(raw, f"/eligibility/{i}")
        rule = EligibilityRule(
            _str(item.get("rule_id"), f"/eligibility/{i}/rule_id"),
            _str(item.get("kind"), f"/eligibility/{i}/kind"),
            _str(item.get("text"), f"/eligibility/{i}/text"),
        )
        eligibility[rule.rule_id] = rule
    findings: list[tuple[str, str]] = []
    for rule_id in sorted(SAFETY_EXCLUSIONS):
        found = eligibility.get(rule_id)
        if found is None or found.kind != "exclude":
            findings.append(("/eligibility", f"{rule_id} must be an exclusion (EI9)"))
    cascade = _cascade(doc.get("medical_officer_cascade"))
    if [(s.kind, s.rank, s.reason_code) for s in cascade] != [
        ("officer_rank", 1, "first_officer"),
        ("officer_rank", 2, "second_officer"),
        ("freshest_skill_holder", None, "freshest_skill"),
    ]:
        findings.append(
            ("/medical_officer_cascade", "must be first officer, second officer, freshest skill holder")
        )
    flow_roles: dict[str, tuple[RoleNeed, ...]] = {}
    for i, raw in enumerate(_list(doc.get("flow_roles"), "/flow_roles")):
        item = _obj(raw, f"/flow_roles/{i}")
        flow_id = _str(item.get("flow_id"), f"/flow_roles/{i}/flow_id")
        needs = tuple(
            _need(n, f"/flow_roles/{i}/roles/{j}", roles)
            for j, n in enumerate(_list(item.get("roles"), f"/flow_roles/{i}/roles"))
        )
        if flow_id in flow_roles or not needs:
            findings.append((f"/flow_roles/{i}", "repeated or empty flow"))
        flow_roles[flow_id] = needs
    defaults = tuple(
        _need(n, f"/default_roles/{j}", roles)
        for j, n in enumerate(_list(doc.get("default_roles"), "/default_roles"))
    )
    plan_step_role = _str(doc.get("plan_step_role"), "/plan_step_role")
    if plan_step_role not in roles or plan_step_role == "medical_officer":
        findings.append(("/plan_step_role", "must be a defined role other than medical_officer"))
    phrases_raw = _obj(doc.get("reason_phrases"), "/reason_phrases")
    phrases: dict[str, str] = {}
    for code in REASON_CODES:
        text = _str(phrases_raw.get(code), f"/reason_phrases/{code}")
        extra = placeholders(text) - REASON_PLACEHOLDERS
        if extra:
            findings.append(
                (f"/reason_phrases/{code}", "placeholder not allowed: " + ",".join(sorted(extra)))
            )
        phrases[code] = text
    if findings:
        raise RulesInvalid(tuple(findings))
    return RoleRules(
        version=_str(doc.get("version"), "/version"),
        notice=_opt_str(doc.get("notice"), "/notice"),
        roles=roles,
        role_order=tuple(roles),
        eligibility=eligibility,
        cascade=cascade,
        flow_roles=flow_roles,
        default_roles=defaults,
        plan_step_role=plan_step_role,
        reason_phrases=phrases,
    )


def _cascade(raw_cascade: object) -> tuple[CascadeStep, ...]:
    steps: list[CascadeStep] = []
    for i, raw in enumerate(_list(raw_cascade, "/medical_officer_cascade")):
        item = _obj(raw, f"/medical_officer_cascade/{i}")
        kind = _str(item.get("kind"), f"/medical_officer_cascade/{i}/kind")
        rank_raw = item.get("rank")
        rank = None if rank_raw is None else _int(rank_raw, f"/medical_officer_cascade/{i}/rank", 1)
        code = _str(item.get("reason_code"), f"/medical_officer_cascade/{i}/reason_code")
        if kind == "officer_rank" and rank is not None:
            steps.append(CascadeStep("officer_rank", rank, code))
        elif kind == "freshest_skill_holder" and rank is None:
            steps.append(CascadeStep("freshest_skill_holder", None, code))
        else:
            raise RulesInvalid(((f"/medical_officer_cascade/{i}", "kind and rank disagree"),))
    return tuple(steps)


def check_flows(rules: RoleRules, flow_ids: Sequence[str]) -> tuple[str, ...]:
    """R-L1: flows of responder-flows.json without a flow_roles entry (a warning; they use default_roles)."""
    return tuple(f for f in flow_ids if f not in rules.flow_roles)


def check_skills(rules: RoleRules, skill_ids: frozenset[str]) -> tuple[tuple[str, str], ...]:
    """Loader check 16: every skill a role names exists in the skills catalog."""
    found: list[tuple[str, str]] = []
    for flow_id, needs in sorted(rules.flow_roles.items()):
        found.extend(
            (f"/flow_roles/{flow_id}", f"unknown skill {n.skill_id}")
            for n in needs
            if n.skill_id is not None and n.skill_id not in skill_ids
        )
    found.extend(
        ("/default_roles", f"unknown skill {n.skill_id}")
        for n in rules.default_roles
        if n.skill_id is not None and n.skill_id not in skill_ids
    )
    return tuple(found)


def parse_skills(doc: Mapping[str, object]) -> tuple[dict[str, SkillDef], tuple[str, ...]]:
    """Fields of skills-catalog.json the roles module reads (E31 design 8), with check R-L3."""
    skills: dict[str, SkillDef] = {}
    order: list[str] = []
    for i, raw in enumerate(_list(doc.get("skills"), "/skills")):
        p = f"/skills/{i}"
        item = _obj(raw, p)
        skill_id = _str(item.get("skill_id"), p + "/skill_id")
        fresh = _obj(item.get("freshness"), p + "/freshness")
        cadence = _str(fresh.get("cadence"), p + "/freshness/cadence")
        n_raw = fresh.get("n")
        n = None if n_raw is None else _int(n_raw, p + "/freshness/n", 1)
        due = _int(fresh.get("due_after_periods"), p + "/freshness/due_after_periods", 1)
        stale = _int(fresh.get("stale_after_periods"), p + "/freshness/stale_after_periods", 1)
        if cadence not in CADENCES or (cadence == "every_n_days") != (n is not None) or stale < due:
            raise RulesInvalid(((p + "/freshness", "R-L3: cadence, n or periods invalid"),))
        scenarios: dict[str, DrillScenario] = {}
        for j, s_raw in enumerate(_list(item.get("drill_scenarios", []), p + "/drill_scenarios")):
            s = _obj(s_raw, f"{p}/drill_scenarios/{j}")
            sid = _str(s.get("scenario_id"), f"{p}/drill_scenarios/{j}/scenario_id")
            supplies = tuple(
                _str(x, f"{p}/drill_scenarios/{j}/needs_supplies")
                for x in _list(s.get("needs_supplies", []), f"{p}/drill_scenarios/{j}/needs_supplies")
            )
            scenarios[sid] = DrillScenario(sid, supplies, _opt_str(s.get("practice_on"), "/practice_on"))
        skills[skill_id] = SkillDef(
            skill_id=skill_id,
            name=_str(item.get("name"), p + "/name"),
            short_name=_str(item.get("short_name"), p + "/short_name"),
            freshness=Freshness(
                cadence,
                n,
                due,
                stale,
                _str(fresh.get("basis"), p + "/freshness/basis"),
                _opt_str(fresh.get("to_be_set_by"), p + "/freshness/to_be_set_by"),
            ),
            drill_scenarios=scenarios,
        )
        order.append(skill_id)
    _check_scenarios(skills)
    return skills, tuple(order)


def _check_scenarios(skills: Mapping[str, SkillDef]) -> None:
    """R-L4: a scenario listed under several skills has the same practice_on under each."""
    seen: dict[str, str | None] = {}
    for skill in skills.values():
        for scenario in skill.drill_scenarios.values():
            if scenario.scenario_id in seen and seen[scenario.scenario_id] != scenario.practice_on:
                raise RulesInvalid((("/skills", f"R-L4: {scenario.scenario_id} practice_on differs"),))
            seen[scenario.scenario_id] = scenario.practice_on


def parse_calendar(doc: Mapping[str, object]) -> Calendar:
    cal = _obj(doc.get("calendar"), "/calendar")
    return Calendar(
        _int(cal.get("week_days"), "/calendar/week_days", 1),
        _int(cal.get("month_days"), "/calendar/month_days", 1),
    )


def parse_words(doc: Mapping[str, object]) -> Words:
    states = _obj(doc.get("designed_states"), "/designed_states")
    fresh = _obj(doc.get("freshness_words"), "/freshness_words")
    ready = _obj(doc.get("readiness_words"), "/readiness_words")
    freshness_words = {
        k: _str(fresh.get(k), f"/freshness_words/{k}") for k in ("fresh", "due", "stale", "never")
    }
    readiness_words = {
        k: _str(ready.get(k), f"/readiness_words/{k}") for k in ("fresh_count", "nobody_fresh")
    }
    for key, text in readiness_words.items():
        if placeholders(text) - READINESS_PLACEHOLDERS:
            raise RulesInvalid(((f"/readiness_words/{key}", "placeholder not allowed"),))
    return Words(_str(states.get("unknown"), "/designed_states/unknown"), freshness_words, readiness_words)


def parse_eva_words(doc: Mapping[str, object]) -> dict[str, str]:
    words: dict[str, str] = {}
    for i, raw in enumerate(_list(doc.get("profiles"), "/profiles")):
        item = _obj(raw, f"/profiles/{i}")
        words[_str(item.get("environment_id"), f"/profiles/{i}/environment_id")] = _str(
            item.get("eva_word"), f"/profiles/{i}/eva_word"
        )
    return words


def parse_practice(doc: Mapping[str, object]) -> PracticeRules:
    practice = _obj(doc.get("practice"), "/practice")
    designs: list[tuple[str, int | None, str]] = []
    for i, raw in enumerate(_list(practice.get("patch_designs"), "/practice/patch_designs")):
        item = _obj(raw, f"/practice/patch_designs/{i}")
        threshold_raw = item.get("threshold")
        threshold = (
            None
            if threshold_raw is None
            else _int(threshold_raw, f"/practice/patch_designs/{i}/threshold", 1)
        )
        designs.append(
            (
                _str(item.get("kind"), f"/practice/patch_designs/{i}/kind"),
                threshold,
                _str(item.get("patch_design_id"), f"/practice/patch_designs/{i}/patch_design_id"),
            )
        )
    core = tuple(
        _str(x, "/practice/core_skill_ids")
        for x in _list(practice.get("core_skill_ids"), "/practice/core_skill_ids")
    )
    return PracticeRules(tuple(designs), core)


def render(template: str, values: Mapping[str, str | None], unknown: str) -> str:
    """Fill {placeholders}; a value with no source renders the designed unknown word (CONTRACTS 11)."""

    def fill(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return unknown if value is None else value

    return _PLACEHOLDER.sub(fill, template)
