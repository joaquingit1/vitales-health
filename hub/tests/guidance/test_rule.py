"""SYSTEM 8.2: the pure guidance rule, its exact public API and its properties."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.guidance.application import ports
from crew_health.guidance.application.ports import (
    ContentRef,
    Fact,
    GuidanceFacts,
    GuidanceRequest,
    Known,
    OperatorFact,
    Requirements,
    StockFact,
    Unknown,
    apply_substitute,
    decide,
)
from crew_health.kernel.units import Quantity, Unit

pytestmark = [pytest.mark.trace("S8.2", "R07")]
ROOT = Path(__file__).resolve().parents[3]

DOSE = Unit("{rinse}")
STEP = ContentRef("plan_step", "plan_icl10.step_1", "a" * 64)
SUB = ContentRef("plan_step", "plan_icl10.step_1_alt", "b" * 64)


def person(
    pid: str,
    order: int,
    *,
    awake: bool = True,
    on_site: bool = True,
    on_eva: bool = False,
    rank: int | None = None,
    skills: dict[str, Literal["fresh", "due", "stale"]] | None = None,
) -> OperatorFact:
    return OperatorFact(pid, order, awake, on_site, on_eva, rank, skills or {})


def request(
    *,
    instruments: frozenset[str] = frozenset(),
    supplies: dict[str, Quantity | None] | None = None,
    skills: frozenset[str] = frozenset(),
    operator: Literal["any_crew", "skill", "crew_medical_officer"] = "any_crew",
    operator_skill_id: str | None = None,
    origin: Literal["LIVE", "REPLAY", "SIM"] = "LIVE",
    subject: str | None = "p_mara",
    fixed: bool = False,
    proposed: str | None = None,
) -> GuidanceRequest:
    return GuidanceRequest(
        content=STEP,
        purpose="patient_instruction",
        release_id="rel_1",
        origin_mode=origin,
        subject_person_id=subject,
        patient_location_id=None,
        requirements=Requirements(instruments, supplies or {}, skills, operator, operator_skill_id),
        fixed_first_screen=fixed,
        proposed_operator_id=proposed,
    )


CREW = (
    person("p_mara", 1),
    person("p_tomas", 2, skills={"pulse_check_wrist": "fresh"}),
    person("p_ilya", 0, rank=1),
)


def facts(**changes: object) -> GuidanceFacts:
    base = GuidanceFacts(
        source_and_hash_valid=Known(True),
        purpose_allowed=Known(True),
        clinical_review="not_clinically_reviewed",
        withdrawn=Known(False),
        simulation_only=Known(False),
        pinned_release_matches=True,
        environment_applicable=Known(True),
        instruments_here=Known(frozenset({"pulse_oximeter", "thermometer"})),
        stock={},
        operators=Known(CREW),
        substitute=None,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_all_known_and_aboard_shows_executable_in_roster_order() -> None:
    decision = decide(request(instruments=frozenset({"pulse_oximeter"})), facts())
    assert decision.outcome == "show" and decision.availability == "aboard" and decision.executable
    assert decision.who_can == ("p_ilya", "p_mara", "p_tomas")
    assert decision.reasons == () and decision.unknown == ()
    assert decision.label == "live" and decision.clinical_review == "not_clinically_reviewed"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"source_and_hash_valid": Known(False)}, "source_or_hash_invalid"),
        ({"purpose_allowed": Known(False)}, "purpose_not_allowed"),
        ({"withdrawn": Known(True)}, "withdrawn"),
        ({"pinned_release_matches": False}, "pinned_release_mismatch"),
        ({"simulation_only": Known(True)}, "simulation_only_content"),
    ],
)
def test_content_rejection_is_not_supported(change: dict[str, object], reason: str) -> None:
    decision = decide(request(), facts(**change))
    assert decision.outcome == "not_supported" and not decision.executable and reason in decision.reasons


@pytest.mark.parametrize(
    "field", ["source_and_hash_valid", "purpose_allowed", "withdrawn", "simulation_only"]
)
def test_unknown_content_fact_never_authorizes(field: str) -> None:
    decision = decide(request(), facts(**{field: Unknown("content", "no_provider")}))
    assert decision.outcome == "not_supported" and not decision.executable
    assert decision.unknown == (f"{field}:content:no_provider",)


def test_missing_clinical_review_alone_never_blocks_and_label_is_kept() -> None:
    unreviewed = decide(request(), facts(clinical_review="not_clinically_reviewed"))
    reviewed = decide(request(), facts(clinical_review="clinically_reviewed"))
    assert unreviewed.executable and reviewed.executable
    assert unreviewed.clinical_review == "not_clinically_reviewed"
    assert replace(unreviewed, clinical_review="clinically_reviewed") == reviewed


def test_simulation_only_content_is_labeled_practice_in_sim() -> None:
    decision = decide(request(origin="SIM"), facts(simulation_only=Known(True)))
    assert decision.outcome == "show" and decision.executable and decision.label == "simulation_only"
    assert decide(request(origin="REPLAY"), facts()).label == "replay"


def test_environment_known_inapplicable_is_left_out_and_unknown_is_unavailable() -> None:
    assert decide(request(), facts(environment_applicable=Known(False))).outcome == "not_here"
    unknown = decide(request(), facts(environment_applicable=Unknown("environment", "no_data")))
    assert unknown.outcome == "not_supported" and "environment_unknown" in unknown.reasons
    assert not unknown.executable


def test_content_rejection_dominates_environment() -> None:
    decision = decide(request(), facts(withdrawn=Known(True), environment_applicable=Known(False)))
    assert decision.outcome == "not_supported"
    assert decision.reasons == ("withdrawn", "environment_not_applicable")


def test_fixed_first_screen_stays_visible_but_not_confirmed_here() -> None:
    unknown = Unknown("environment", "no_profile")
    decision = decide(
        request(fixed=True, instruments=frozenset({"bp_cuff"})),
        facts(environment_applicable=unknown, instruments_here=Unknown("equipment", "no_provider")),
    )
    assert decision.outcome == "show" and not decision.executable
    assert "not_confirmed_here" in decision.reasons and decision.availability == "unknown"
    rejected = decide(request(fixed=True), facts(source_and_hash_valid=Unknown("content", "no_provider")))
    assert rejected.outcome == "not_supported"


def test_missing_instrument_is_not_aboard() -> None:
    decision = decide(request(instruments=frozenset({"ecg_single_lead", "bp_cuff"})), facts())
    assert decision.availability == "not_aboard" and not decision.executable
    assert decision.reasons == ("instrument_missing:bp_cuff", "instrument_missing:ecg_single_lead")


def stock(
    *, aboard: bool = True, now: str | None = "4", per_use: str | None = "1", consumable: bool = True
) -> Fact[StockFact]:
    q_now = None if now is None else Quantity(Decimal(now), DOSE)
    q_use = None if per_use is None else Quantity(Decimal(per_use), DOSE)
    return Known(StockFact(aboard, q_now, q_use, consumable))


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (stock(), "aboard"),
        (stock(now="1", per_use="1"), "aboard"),
        (stock(now="0.5", per_use="1"), "not_aboard"),
        (stock(now="0"), "not_aboard"),
        (stock(aboard=False), "not_aboard"),
        (stock(now=None, per_use=None), "aboard"),
        (stock(now="0.5", per_use="1", consumable=False), "aboard"),
        (Unknown("stock", "no_provider"), "unknown"),
    ],
)
def test_stock_compares_quantity_against_per_use(fact: Fact[StockFact], expected: str) -> None:
    decision = decide(request(supplies={"eye_rinse_water": None}), facts(stock={"eye_rinse_water": fact}))
    assert decision.availability == expected
    assert decision.executable is (expected == "aboard")


def test_absent_stock_row_is_missing_and_unit_mismatch_is_unknown() -> None:
    assert decide(request(supplies={"eye_rinse_water": None}), facts()).availability == "not_aboard"
    mismatch = Known(StockFact(True, Quantity(Decimal(4), DOSE), Quantity(Decimal(1), Unit("mL")), True))
    decision = decide(request(supplies={"eye_rinse_water": None}), facts(stock={"eye_rinse_water": mismatch}))
    assert decision.availability == "unknown" and not decision.executable


def test_skill_needs_a_fresh_or_due_holder_who_is_not_the_subject() -> None:
    skilled = request(operator="skill", operator_skill_id="pulse_check_wrist")
    decision = decide(skilled, facts())
    assert decision.availability == "aboard" and decision.who_can == ("p_tomas",) and decision.executable
    stale = (
        person("p_mara", 1, skills={"pulse_check_wrist": "fresh"}),
        person("p_tomas", 2, skills={"pulse_check_wrist": "stale"}),
    )
    blocked = decide(skilled, facts(operators=Known(stale)))
    assert blocked.availability == "needs_skill_holder" and blocked.who_can == () and not blocked.executable
    assert blocked.reasons == ("skill_holder_missing:pulse_check_wrist", "no_operator_available")


def test_missing_equipment_takes_precedence_over_missing_skill() -> None:
    decision = decide(request(instruments=frozenset({"ecg_single_lead"}), skills=frozenset({"cpr"})), facts())
    assert decision.availability == "not_aboard"


def test_operator_rules_exclude_asleep_eva_offsite_and_subject_where_required() -> None:
    roster = (
        person("p_mara", 3, rank=2),
        person("p_ilya", 1, rank=1, on_eva=True),
        person("p_tomas", 2),
        person("p_ana", 4, awake=False, rank=3),
        person("p_li", 5, on_site=False, rank=4),
    )
    any_crew = decide(request(), facts(operators=Known(roster)))
    officer = decide(request(operator="crew_medical_officer"), facts(operators=Known(roster)))
    assert any_crew.who_can == ("p_tomas", "p_mara")
    assert officer.who_can == () and not officer.executable and "no_operator_available" in officer.reasons


def test_proposed_operator_must_be_in_who_can() -> None:
    assert decide(request(proposed="p_tomas"), facts()).executable
    denied = decide(request(proposed="p_ground"), facts())
    assert not denied.executable and "proposed_operator_not_eligible" in denied.reasons


def test_skill_operator_without_skill_id_is_not_supported() -> None:
    assert decide(request(operator="skill"), facts()).outcome == "not_supported"


def test_substitute_is_shown_only_with_its_own_equipment() -> None:
    primary = decide(request(instruments=frozenset({"ecg_single_lead"})), facts())
    sub_ok = decide(request(operator="skill", operator_skill_id="pulse_check_wrist"), facts())
    combined = apply_substitute(primary, SUB, sub_ok)
    assert combined.outcome == "show_with_substitute" and combined.availability == "substituted"
    assert combined.executable and combined.who_can == ("p_tomas",) and combined.substitute == SUB
    sub_missing = decide(request(instruments=frozenset({"fundoscope"})), facts())
    kept = apply_substitute(primary, SUB, sub_missing)
    assert kept.availability == "not_aboard" and not kept.executable and kept.substitute is None
    sub_withdrawn = decide(request(), facts(withdrawn=Known(True)))
    assert apply_substitute(primary, SUB, sub_withdrawn).availability == "not_aboard"
    sub_no_skill = decide(request(skills=frozenset({"cpr"})), facts())
    shown = apply_substitute(primary, SUB, sub_no_skill)
    assert shown.availability == "substituted" and not shown.executable


facts_strategy = st.builds(
    GuidanceFacts,
    source_and_hash_valid=st.sampled_from([Known(True), Known(False), Unknown("content", "x")]),
    purpose_allowed=st.sampled_from([Known(True), Known(False), Unknown("content", "x")]),
    clinical_review=st.sampled_from(["clinically_reviewed", "not_clinically_reviewed"]),
    withdrawn=st.sampled_from([Known(True), Known(False), Unknown("content", "x")]),
    simulation_only=st.sampled_from([Known(True), Known(False), Unknown("content", "x")]),
    pinned_release_matches=st.booleans(),
    environment_applicable=st.sampled_from([Known(True), Known(False), Unknown("environment", "x")]),
    instruments_here=st.one_of(
        st.just(Unknown("equipment", "x")),
        st.frozensets(st.sampled_from(["a", "b", "c"])).map(Known),
    ),
    stock=st.dictionaries(
        st.sampled_from(["s1", "s2"]), st.sampled_from([stock(), stock(now="0"), Unknown("stock", "x")])
    ),
    operators=st.one_of(
        st.just(Unknown("skills", "x")),
        st.lists(
            st.builds(
                person,
                st.sampled_from(["p1", "p2", "p3"]),
                st.integers(0, 3),
                awake=st.booleans(),
                on_eva=st.booleans(),
                rank=st.none() | st.just(1),
                skills=st.dictionaries(st.just("k"), st.sampled_from(["fresh", "due", "stale"])),
            ),
            max_size=4,
            unique_by=lambda p: p.person_id,
        ).map(lambda xs: Known(tuple(xs))),
    ),
    substitute=st.none(),
)
request_strategy = st.builds(
    request,
    instruments=st.frozensets(st.sampled_from(["a", "b", "c"])),
    supplies=st.dictionaries(st.sampled_from(["s1", "s2"]), st.none()),
    skills=st.frozensets(st.just("k")),
    operator=st.sampled_from(["any_crew", "skill", "crew_medical_officer"]),
    operator_skill_id=st.sampled_from([None, "k"]),
    origin=st.sampled_from(["LIVE", "REPLAY", "SIM"]),
    subject=st.sampled_from([None, "p1"]),
    fixed=st.booleans(),
    proposed=st.sampled_from([None, "p1", "p2"]),
)


@given(request_strategy, facts_strategy)
def test_property_unknown_never_enables_execution(req: GuidanceRequest, f: GuidanceFacts) -> None:
    decision = decide(req, f)
    if decision.unknown:
        assert not decision.executable
    if decision.executable:
        assert decision.outcome == "show" and decision.availability == "aboard" and decision.who_can
        if req.proposed_operator_id is not None:
            assert req.proposed_operator_id in decision.who_can


@given(request_strategy, facts_strategy)
def test_property_independent_of_provider_arrival_order(req: GuidanceRequest, f: GuidanceFacts) -> None:
    reordered_stock = dict(reversed(list(f.stock.items())))
    operators = f.operators
    if isinstance(operators, Known):
        operators = Known(tuple(reversed(operators.value)))
    assert decide(req, f) == decide(req, replace(f, stock=reordered_stock, operators=operators))


@given(request_strategy, facts_strategy)
def test_property_clinical_review_is_only_a_label(req: GuidanceRequest, f: GuidanceFacts) -> None:
    a = decide(req, replace(f, clinical_review="clinically_reviewed"))
    b = decide(req, replace(f, clinical_review="not_clinically_reviewed"))
    assert replace(a, clinical_review="not_clinically_reviewed") == b


def _system_block() -> ast.Module:
    text = (ROOT / "docs/SYSTEM.md").read_text(encoding="utf-8")
    blocks: list[str] = re.findall(
        r"```python\n(# crew_health/guidance/application/ports\.py.*?)```", text, re.S
    )
    assert len(blocks) == 1
    return ast.parse(blocks[0])


def test_public_api_is_exactly_system_8_2() -> None:
    for node in _system_block().body:
        if isinstance(node, ast.ClassDef):
            actual = getattr(ports, node.name)
            expected = {
                n.target.id: ast.unparse(n.annotation)
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }
            if expected:
                assert {f.name: f.type for f in dataclasses.fields(actual)} == expected, node.name
            for fn in (n for n in node.body if isinstance(n, ast.FunctionDef)):
                params = [a.arg for a in fn.args.args]
                assert list(inspect.signature(getattr(actual, fn.name)).parameters) == params
        elif isinstance(node, ast.FunctionDef):
            assert list(inspect.signature(getattr(ports, node.name)).parameters) == [
                a.arg for a in node.args.args
            ]
        elif isinstance(node, ast.TypeAlias):
            assert getattr(ports, node.name.id).__name__ == node.name.id
