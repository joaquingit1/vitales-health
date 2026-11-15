"""Role engine and freshness: golden vectors from the package (checked against CONTRACTS 24 by hand) and properties."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health.roles.application.config import build_config
from crew_health.roles.domain.engine import (
    EngineInput,
    Person,
    Proposal,
    plan_needs,
    propose,
    propose_plan_run,
)
from crew_health.roles.domain.freshness import Practice, skill_record
from crew_health.roles.domain.rules import RolesConfig, render

from .harness import DAY_12, MISSION_START, NOW, WALK_END, documents, example, obj, objs, person_of

CFG: RolesConfig = build_config("rel_1", documents())


def _people(*, walking: tuple[str, ...] = (), asleep: tuple[str, ...] = ()) -> tuple[Person, ...]:
    base = (("mara", "Mara Okafor", 0, None), ("ilya", "Ilya Brandt", 1, 1), ("sofia", "Sofia Reyes", 2, 2))
    rows = (*base, ("tomas", "Tomas Lind", 3, None))
    return tuple(
        Person(pid, name, i, rank, pid in walking, pid in asleep, WALK_END if pid in walking else None)
        for pid, name, i, rank in rows
    )


def _label(at: datetime) -> str:
    return f"day {(at - MISSION_START).days + 1}"


def _inp(
    flow: str,
    subject: str | None,
    *,
    opener: str | None = "mara",
    holders: dict[str, dict[str, datetime]] | None = None,
    people: tuple[Person, ...] | None = None,
) -> EngineInput:
    return EngineInput(
        subject_person_id=subject,
        opener_person_id=opener,
        needs=CFG.rules.needs_for_flow(flow),
        people=_people() if people is None else people,
        holders=holders or {},
        decided=(),
        now=NOW,
        eva_word=CFG.eva_words.get("lunar_surface"),
        skill_short={s: d.short_name for s, d in CFG.skills.items()},
    )


def _rendered(proposal: Proposal) -> dict[str, tuple[str, str, list[str]]]:
    out: dict[str, tuple[str, str, list[str]]] = {}
    for p in proposal.proposed:
        text = render(CFG.rules.reason_phrases[p.reason.code], p.reason.values, CFG.words.unknown)
        context = [render(CFG.rules.reason_phrases[c.code], c.values, CFG.words.unknown) for c in p.context]
        out[p.need.role_id] = (p.person_id, text, context)
    return out


def _golden(name: str) -> dict[str, tuple[str, str, list[str]]]:
    value = example(name)
    rows = objs(value["assignments"]) if "assignments" in value else [value]
    return {
        str(r["role_id"]): (
            person_of(r),
            str(r["reason_text"]),
            [str(c["text"]) for c in objs(r["context_reasons"])],
        )
        for r in rows
    }


@pytest.mark.trace("V88", "R54", "EI9")
def test_station_cpr_golden_vector() -> None:
    cpr_at = DAY_12  # Mara and Tomas practiced CPR at the same instant: the non-opener goes first (Q2)
    proposal = propose(
        CFG.rules, _inp("not_breathing", "ilya", holders={"cpr": {"mara": cpr_at, "tomas": cpr_at}}), _label
    )
    assert _rendered(proposal) == _golden("roles/help-roles-cpr.json")
    unfilled = obj(example("roles/help-roles-cpr.json"))["unfilled"]
    assert [(n.role_id, n.required) for n in proposal.unfilled] == [
        (u["role_id"], u["required"]) for u in objs(unfilled)
    ]


@pytest.mark.trace("V88", "R54")
def test_eye_golden_vector_officer_back_from_walk() -> None:
    proposal = propose(CFG.rules, _inp("eye_injury", "tomas"), _label)
    assert _rendered(proposal) == _golden("roles/help-roles-eye.json")
    assert proposal.unfilled == ()


@pytest.mark.trace("V89", "D37")
def test_plan_helper_golden_vector() -> None:
    needs = plan_needs(
        CFG.rules, ["pulse_check_wrist", "pulse_check_wrist"], {s: d.name for s, d in CFG.skills.items()}
    )
    inp = _inp(
        "eye_injury",
        "mara",
        opener=None,
        people=_people(walking=("ilya", "sofia")),
        holders={"pulse_check_wrist": {"tomas": DAY_12}},
    )
    proposal = propose_plan_run(CFG.rules, replace(inp, needs=needs), _label)
    golden = _golden("roles/plan-helper-tomas.json")
    assert _rendered(proposal) == golden


@pytest.mark.trace("R54")
def test_freshness_boundaries_monthly() -> None:
    """Checked by hand: practice 2026-11-06T15:20Z, monthly (30 day month), due after 1, stale after 2 periods."""
    f = CFG.skills["cpr"].freshness
    at = datetime(2026, 11, 6, 15, 20, tzinfo=UTC)
    practice = Practice("prc_1", "tomas", "cpr", "drill", "drl_1", at, "SIM", "msn_moon_1")
    due, stale = datetime(2026, 12, 6, 15, 20, tzinfo=UTC), datetime(2027, 1, 5, 15, 20, tzinfo=UTC)
    tick = timedelta(microseconds=1)
    assert skill_record("tomas", "cpr", [practice], f, CFG.calendar, due - tick).freshness == "fresh"
    assert skill_record("tomas", "cpr", [practice], f, CFG.calendar, due).freshness == "due"
    assert skill_record("tomas", "cpr", [practice], f, CFG.calendar, stale - tick).freshness == "due"
    record = skill_record("tomas", "cpr", [practice], f, CFG.calendar, stale)
    assert (record.freshness, record.due_at, record.stale_at, record.is_holder) == (
        "stale",
        due,
        stale,
        False,
    )
    assert skill_record("tomas", "cpr", [], f, CFG.calendar, stale).freshness == "never"
    assert skill_record("tomas", "cpr", [practice], f, CFG.calendar, at - tick).freshness == "never"  # future


PEOPLE = st.permutations(list(_people()))
HOLDERS = st.dictionaries(
    st.sampled_from(["mara", "ilya", "sofia", "tomas"]),
    st.integers(min_value=0, max_value=20).map(lambda d: DAY_12 - timedelta(days=d)),
)
FLOWS = st.sampled_from([str(f) for f in CFG.rules.flow_roles])


@settings(max_examples=150, deadline=None)
@given(
    people=PEOPLE,
    holders=HOLDERS,
    flow=FLOWS,
    subject=st.sampled_from(["mara", "ilya", "sofia", "tomas", None]),
    walking=st.sets(st.sampled_from(["mara", "ilya", "sofia", "tomas"])),
    asleep=st.sets(st.sampled_from(["mara", "ilya", "sofia", "tomas"])),
)
@pytest.mark.trace("EI9", "R54")
def test_engine_invariants(
    people: list[Person],
    holders: dict[str, datetime],
    flow: str,
    subject: str | None,
    walking: set[str],
    asleep: set[str],
) -> None:
    crowd = tuple(
        Person(
            p.person_id,
            p.display_name,
            p.roster_index,
            p.officer_rank,
            p.person_id in walking,
            p.person_id in asleep,
            None,
        )
        for p in people
    )
    by_skill = {s: dict(holders) for s in CFG.skills}
    inp = _inp(flow, subject, people=crowd, holders=by_skill)
    proposal = propose(CFG.rules, inp, _label)
    for p in proposal.proposed:
        assert p.person_id != subject  # never the subject
        if CFG.rules.roles[p.need.role_id].on_site:
            assert p.person_id not in walking  # never a walker on site
        if p.need.skill_id is not None:
            assert p.person_id in holders  # an unqualified person never fills a skilled role
    filled = {(p.need.role_id, p.need.task_text) for p in proposal.proposed}
    assert len(filled) + len(proposal.unfilled) == len(inp.needs)
    reversed_inp = replace(inp, people=tuple(reversed(crowd)))
    assert propose(CFG.rules, reversed_inp, _label) == proposal  # input order independent


@settings(max_examples=100, deadline=None)
@given(
    days=st.lists(st.integers(min_value=0, max_value=90), min_size=1, max_size=6), later=st.integers(0, 120)
)
@pytest.mark.trace("R54")
def test_freshness_never_improves_with_time(days: list[int], later: int) -> None:
    f = CFG.skills["pulse_check_wrist"].freshness
    practices = [
        Practice(
            f"prc_{i}",
            "mara",
            "pulse_check_wrist",
            "preflight",
            f"p{i}",
            MISSION_START + timedelta(days=d),
            "SIM",
            "m",
        )
        for i, d in enumerate(days)
    ]
    order = ["fresh", "due", "stale"]
    now = MISSION_START + timedelta(days=max(days))
    first = skill_record("mara", "pulse_check_wrist", practices, f, CFG.calendar, now).freshness
    second = skill_record(
        "mara", "pulse_check_wrist", list(reversed(practices)), f, CFG.calendar, now + timedelta(days=later)
    ).freshness
    assert order.index(second) >= order.index(first)
