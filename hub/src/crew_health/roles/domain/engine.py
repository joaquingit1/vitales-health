"""The role engine (E31 design 3.3, CONTRACTS 24): deterministic, input-order independent, never guesses.

Order of work: hard exclusions (EI9) on every path, then kept human decisions, then the medical officer cascade,
then required needs in flow order, then optional needs. A role with a skill goes only to a holder (fresh or due);
with no eligible holder it stays unfilled, required or not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from crew_health.roles.domain.rules import RoleNeed, RoleRules

type ContextKind = Literal["help_session", "plan_run"]
type RetireReason = Literal["ineligible_subject", "ineligible_eva", "need_changed", "not_on_roster"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICRO = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class Person:
    """One roster member as the engine sees them now (roster, overlays and location facts already merged)."""

    person_id: str
    display_name: str
    roster_index: int
    officer_rank: int | None
    on_eva: bool
    asleep: bool
    eva_until: datetime | None = None
    location_conflict: bool = False


@dataclass(frozen=True, slots=True)
class Decided:
    """A current assignment a human decided (accepted, or set by a helper): the engine never moves it."""

    role_assignment_id: str
    role_id: str
    skill_id: str | None
    task_text: str | None
    person_id: str


@dataclass(frozen=True, slots=True)
class ReasonDraft:
    code: str
    values: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Proposed:
    need: RoleNeed
    person_id: str
    reason: ReasonDraft
    context: tuple[ReasonDraft, ...]


@dataclass(frozen=True, slots=True)
class Proposal:
    proposed: tuple[Proposed, ...]
    unfilled: tuple[RoleNeed, ...]
    kept: tuple[Decided, ...]
    retired: tuple[tuple[Decided, RetireReason], ...]


@dataclass(frozen=True, slots=True)
class EngineInput:
    subject_person_id: str | None
    opener_person_id: str | None
    needs: tuple[RoleNeed, ...]
    people: tuple[Person, ...]
    holders: Mapping[str, Mapping[str, datetime]]  # skill_id -> person_id -> last_practiced_at (holders only)
    decided: tuple[Decided, ...]
    now: datetime
    eva_word: str | None
    skill_short: Mapping[str, str]


def need_key(need: RoleNeed | Decided) -> tuple[str, str, str]:
    return (need.role_id, need.skill_id or "", need.task_text or "")


def exclusion(rules: RoleRules, role_id: str, person: Person, subject_person_id: str | None) -> str | None:
    """The hard exclusions, in contract order; they are code, never a setting (AGENTS rule 1)."""
    if person.person_id == subject_person_id:
        return "never_subject"
    role = rules.roles.get(role_id)
    if (role is None or role.on_site) and person.on_eva:
        return "not_on_eva_for_on_site"
    return None


def clock_text(instant: datetime | None, now: datetime) -> str | None:
    """Mission time HH:MM of a planned end; a planned end already past is not shown as current (unknown)."""
    if instant is None or instant < now:
        return None
    return instant.astimezone(UTC).strftime("%H:%M")


def _officer_reason(code: str, person: Person, inp: EngineInput) -> ReasonDraft:
    return ReasonDraft(
        code,
        {
            "officer_name": person.display_name,
            "eva_word": inp.eva_word,
            "time": clock_text(person.eva_until, inp.now),
        },
    )


def _skilled_key(
    person: Person, last: datetime, inp: EngineInput, rules: RoleRules
) -> tuple[int, int, int, int]:
    asleep = 1 if (person.asleep and rules.prefers_awake()) else 0
    opener = 1 if person.person_id == inp.opener_person_id else 0
    return (asleep, -((last - _EPOCH) // _MICRO), opener, person.roster_index)


def _free_key(person: Person, rules: RoleRules) -> tuple[int, int]:
    return (1 if (person.asleep and rules.prefers_awake()) else 0, person.roster_index)


class _State:
    def __init__(self, rules: RoleRules, inp: EngineInput) -> None:
        self.rules = rules
        self.inp = inp
        self.people = tuple(sorted(inp.people, key=lambda p: (p.roster_index, p.person_id)))
        self.roles_held: dict[str, int] = {}

    def eligible(self, role_id: str) -> list[Person]:
        return [
            p for p in self.people if exclusion(self.rules, role_id, p, self.inp.subject_person_id) is None
        ]

    def take(self, person_id: str) -> None:
        self.roles_held[person_id] = self.roles_held.get(person_id, 0) + 1

    def pool(self, people: Sequence[Person], required: bool) -> list[Person]:
        if not self.rules.one_role_each():
            return list(people)
        free = [p for p in people if self.roles_held.get(p.person_id, 0) == 0]
        if free or not required:
            return free
        return list(people)

    def skilled(self, need: RoleNeed, skill_id: str) -> tuple[Person, datetime] | None:
        holders = self.inp.holders.get(skill_id, {})
        candidates = [p for p in self.eligible(need.role_id) if p.person_id in holders]
        pool = self.pool(candidates, need.required)
        if not pool:
            return None
        best = min(pool, key=lambda p: _skilled_key(p, holders[p.person_id], self.inp, self.rules))
        return best, holders[best.person_id]

    def skill_reason(
        self, skill_id: str, last: datetime, label_at: Callable[[datetime], str | None]
    ) -> ReasonDraft:
        return ReasonDraft(
            "freshest_skill", {"skill_short": self.inp.skill_short.get(skill_id), "day_label": label_at(last)}
        )


def _split_decided(
    state: _State, needs: list[RoleNeed]
) -> tuple[list[Decided], list[tuple[Decided, RetireReason]]]:
    by_id = {p.person_id: p for p in state.people}
    kept: list[Decided] = []
    retired: list[tuple[Decided, RetireReason]] = []
    for d in sorted(state.inp.decided, key=lambda d: (need_key(d), d.person_id, d.role_assignment_id)):
        person = by_id.get(d.person_id)
        if person is None:
            retired.append((d, "not_on_roster"))
            continue
        rule = exclusion(state.rules, d.role_id, person, state.inp.subject_person_id)
        if rule is not None:
            retired.append((d, "ineligible_subject" if rule == "never_subject" else "ineligible_eva"))
            continue
        match = next((n for n in needs if need_key(n) == need_key(d)), None)
        if match is None:
            retired.append((d, "need_changed"))
            continue
        needs.remove(match)
        kept.append(d)
        state.take(d.person_id)
    return kept, retired


def _medical_officer(
    state: _State, need: RoleNeed, all_needs: tuple[RoleNeed, ...], label_at: Callable[[datetime], str | None]
) -> tuple[Proposed | None, tuple[ReasonDraft, ...]]:
    passed: list[ReasonDraft] = []
    asleep_first: tuple[Person, str, int] | None = None
    for step in state.rules.cascade:
        if step.kind == "officer_rank":
            officer = next((p for p in state.people if p.officer_rank == step.rank), None)
            if officer is None:
                continue
            rule = exclusion(state.rules, need.role_id, officer, state.inp.subject_person_id)
            if rule == "never_subject":
                passed.append(_officer_reason("officer_is_subject", officer, state.inp))
                continue
            if rule is not None:
                passed.append(_officer_reason("officer_on_eva", officer, state.inp))
                continue
            if officer.asleep and state.rules.prefers_awake():
                if asleep_first is None:
                    asleep_first = (officer, step.reason_code, len(passed))
                passed.append(_officer_reason("officer_asleep", officer, state.inp))
                continue
            return Proposed(need, officer.person_id, ReasonDraft(step.reason_code), tuple(passed)), ()
        skill_id = need.skill_id or next((n.skill_id for n in all_needs if n.skill_id is not None), None)
        if skill_id is None:
            break
        found = state.skilled(need, skill_id)
        if found is not None and (not found[0].asleep or asleep_first is None):
            person, last = found
            return Proposed(
                need, person.person_id, state.skill_reason(skill_id, last, label_at), tuple(passed)
            ), ()
    if asleep_first is not None:
        officer, code, index = asleep_first
        context = tuple(passed[:index] + passed[index + 1 :])
        return Proposed(need, officer.person_id, ReasonDraft(code), context), ()
    return None, tuple(passed)


def propose(rules: RoleRules, inp: EngineInput, label_at: Callable[[datetime], str | None]) -> Proposal:
    """Proposal for a help session's needs (CONTRACTS 24 steps 1 to 3)."""
    state = _State(rules, inp)
    open_needs = list(inp.needs)
    kept, retired = _split_decided(state, open_needs)
    proposed: list[Proposed] = []
    unfilled: list[RoleNeed] = []
    mo_need = next((n for n in open_needs if n.role_id == "medical_officer"), None)
    if mo_need is not None:
        open_needs.remove(mo_need)
        chosen, _ = _medical_officer(state, mo_need, inp.needs, label_at)
        if chosen is None:
            unfilled.append(mo_need)
        else:
            proposed.append(chosen)
            state.take(chosen.person_id)
    ordered = [n for n in open_needs if n.required] + [n for n in open_needs if not n.required]
    for need in ordered:
        chosen = _fill(state, need, label_at)
        if chosen is None:
            unfilled.append(need)
        else:
            proposed.append(chosen)
            state.take(chosen.person_id)
    return Proposal(tuple(proposed), tuple(unfilled), tuple(kept), tuple(retired))


def _fill(state: _State, need: RoleNeed, label_at: Callable[[datetime], str | None]) -> Proposed | None:
    if need.skill_id is not None:
        found = state.skilled(need, need.skill_id)
        if found is None:
            return None
        person, last = found
        return Proposed(need, person.person_id, state.skill_reason(need.skill_id, last, label_at), ())
    pool = state.pool(state.eligible(need.role_id), need.required)
    if not pool:
        return None
    best = min(pool, key=lambda p: _free_key(p, state.rules))
    return Proposed(need, best.person_id, ReasonDraft("free_to_help"), ())


def plan_needs(
    rules: RoleRules, skill_ids: Sequence[str], skill_names: Mapping[str, str]
) -> tuple[RoleNeed, ...]:
    """One need per distinct skill among a run's skill steps, in step order (E31 design 3.3)."""
    seen: list[str] = []
    for skill_id in skill_ids:
        if skill_id not in seen:
            seen.append(skill_id)
    return tuple(RoleNeed(rules.plan_step_role, s, skill_names.get(s), True) for s in seen)


def unavailable_officers(state_rules: RoleRules, inp: EngineInput, role_id: str) -> tuple[ReasonDraft, ...]:
    people = sorted(inp.people, key=lambda p: (p.roster_index, p.person_id))
    reasons: list[ReasonDraft] = []
    for step in state_rules.cascade:
        if step.kind != "officer_rank":
            continue
        officer = next((p for p in people if p.officer_rank == step.rank), None)
        if officer is None:
            continue
        rule = exclusion(state_rules, role_id, officer, inp.subject_person_id)
        if rule == "never_subject":
            reasons.append(_officer_reason("officer_is_subject", officer, inp))
        elif rule is not None:
            reasons.append(_officer_reason("officer_on_eva", officer, inp))
        elif officer.asleep:
            reasons.append(_officer_reason("officer_asleep", officer, inp))
    return tuple(reasons)


def propose_plan_run(
    rules: RoleRules, inp: EngineInput, label_at: Callable[[datetime], str | None]
) -> Proposal:
    """`plan_step_role` for the freshest eligible holder of each skill, with the officers who are unavailable."""
    state = _State(rules, inp)
    open_needs = list(inp.needs)
    kept, retired = _split_decided(state, open_needs)
    context = unavailable_officers(rules, inp, rules.plan_step_role)
    proposed: list[Proposed] = []
    unfilled: list[RoleNeed] = []
    for need in open_needs:
        chosen = _fill(state, need, label_at)
        if chosen is None:
            unfilled.append(need)
            continue
        proposed.append(Proposed(chosen.need, chosen.person_id, chosen.reason, context))
        state.take(chosen.person_id)
    return Proposal(tuple(proposed), tuple(unfilled), tuple(kept), tuple(retired))
