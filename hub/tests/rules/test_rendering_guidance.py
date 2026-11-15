"""Rendering at raise, step derivation and guidance eligibility of the stored steps (E24 5.2, 3.16).

T-E24-011, 040, 041, 044 (pure part), 114, 130 (pure part); INV-E24-12, INV-E24-19; D20, Q1, Q7, Q18.
"""

from __future__ import annotations

import json
import string
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain import guidance, rendering
from crew_health.rules.domain.decisions import RenderValues, TimeLabelSpec
from crew_health.rules.domain.definitions import RulesDocument
from crew_health.rules.domain.errors import InvalidInput
from crew_health.rules.domain.guidance import GuidanceVerdict
from crew_health.rules.domain.loader_checks import CONTENT_PLACEHOLDERS
from crew_health.rules.domain.vocabulary import Basis, OriginMode
from crew_health.rules.domain.warning import DefinitionPin, LimitPin, Warning

from . import rules_harness as h

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "R43", "INV-E24-12", "INV-E24-19")]


def example_warning(name: str) -> dict[str, object]:
    path = h.ROOT / "docs" / "product" / "api" / "examples" / "warnings" / f"{name}.json"
    value: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))["value"]
    return value


@pytest.mark.trace("T-E24-040", "C13")
def test_t040_steps_follow_the_sentence_rule_and_match_the_example_warning() -> None:
    co2_act = h.what_to_do_by_hazard()["co2"]["act"]
    assert (
        list(rendering.sentences(co2_act)) == example_warning("co2-station")["steps"]
    )  # independent expected steps


def test_the_sentence_rule_removes_source_ids_collapses_space_and_splits_on_capitals() -> None:
    text = 'Do this (EV01).  Then   that (EV01, C25)! Is it ok? yes. "Quoted" next.'
    assert rendering.sentences(text) == ("Do this.", "Then that!", "Is it ok? yes.", '"Quoted" next.')
    assert rendering.sentences("") == ()
    assert rendering.sentences("   ") == ()


@pytest.mark.trace("T-E24-040")
def test_listed_assistant_and_hazard_steps_come_from_their_own_source(deployment_doc: RulesDocument) -> None:
    quiet = deployment_doc.by_id()["sensor_quiet"]
    values = RenderValues({}, (), TimeLabelSpec("mission", h.T0), (), None, False)
    assert rendering.steps_for(quiet, None, values) == quiet.content.steps.items
    assistant = deployment_doc.by_id()["assistant_pattern"]
    with pytest.raises(InvalidInput):
        rendering.steps_for(assistant, None, values)
    given_steps = replace(values, assistant_steps=("Rest.",))
    assert rendering.steps_for(assistant, None, given_steps) == ("Rest.",)
    co2 = deployment_doc.by_id()["co2_above_limit"]
    with pytest.raises(InvalidInput):
        rendering.steps_for(co2, {"info": "Nothing."}, values)  # no what_to_do at level act
    assert rendering.steps_for(co2, h.what_to_do_by_hazard()["co2"], values)


@pytest.mark.trace("T-E24-044", "T-E24-114")
def test_t044_the_replay_co2_warning_matches_the_package_example(world: h.World) -> None:
    from crew_health.rules.domain.inputs import LimitRenderFacts

    facts = LimitRenderFacts("mean_1h", "one-hour average", 3600, "3.5", "mmHg", "mmHg", "3")
    when = datetime(2014, 10, 7, 15, 36, tzinfo=UTC)
    world.feed(
        world.sensor(
            at=when,
            origin=OriginMode.REPLAY,
            results=[h.result("co2_1h_act", crossed=True, clear_holds=False, render=facts)],
        )
    )
    (w,) = world.by_definition("co2_above_limit")
    expected = example_warning("co2-station")
    for key in (
        "title",
        "what",
        "why",
        "steps",
        "body_regions",
        "level",
        "raised_by",
        "scope",
        "origin_mode",
        "hazard_id",
        "limit_id",
        "sensor_type_id",
        "evidence_time_label",
    ):
        got = getattr(w, key)
        got = got.value if hasattr(got, "value") else got
        assert (list(got) if isinstance(got, tuple) else got) == expected[key], key
    assert {"fragment": w.headline.fragment, "rank": w.headline.rank, "solo": w.headline.solo} == expected[
        "headline"
    ]
    assert w.evidence[0].kind.value == "reading" and w.evidence_time == when


@pytest.mark.trace("T-E24-114", "INV-E24-12")
def test_t114_the_citation_basis_is_the_weakest_used(world: h.World, deployment_doc: RulesDocument) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (w,) = world.by_definition("co2_above_limit")
    assert w.pin.limit is not None
    assert w.pin.limit.basis is Basis.SOURCED and w.pin.limit.clear_basis is Basis.TEAM_DEFAULT
    assert w.citation.basis is Basis.TEAM_DEFAULT  # the clear is a team default, so that is the weakest
    d = deployment_doc.by_id()["co2_above_limit"]
    strong = DefinitionPin(
        replace(d, clear=replace(d.clear, basis=Basis.SOURCED)),
        replace(w.pin.limit, clear_basis=Basis.SOURCED),
    )
    assert rendering.weakest_basis(strong) is Basis.SOURCED
    listed = deployment_doc.by_id()["radiation_storm_shelter"]
    assert rendering.weakest_basis(DefinitionPin(listed, None)) is Basis.TEAM_DEFAULT
    assert (
        rendering.weakest_basis(DefinitionPin(d, LimitPin("l", "f", 1, 1, None, None))) is Basis.TEAM_DEFAULT
    )


@given(
    st.dictionaries(
        st.sampled_from(sorted(CONTENT_PLACEHOLDERS)),
        st.text(alphabet=string.ascii_letters + string.digits + " .,", max_size=12),
        max_size=len(CONTENT_PLACEHOLDERS),
    )
)
@h.PROP
@pytest.mark.trace("T-E24-011", "T-E24-041", "C11")
def test_t011_rendering_leaves_no_braces_and_a_missing_value_renders_the_unknown_word(
    values: dict[str, str],
) -> None:
    from crew_health.rules.domain.definitions import parse_rules_document

    world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    for d in world.definitions:
        pin = DefinitionPin(d, None)
        rv = RenderValues(values, (), TimeLabelSpec("mission", h.T0), ("Step.",), None, False)
        rendered = rendering.render_content(pin, rv, world.renderer, world.what_to_do.get(d.hazard_id or ""))
        for text in (
            rendered.title,
            rendered.what,
            rendered.why,
            rendered.headline_fragment,
            rendered.citation.text,
        ):
            assert "{" not in text and "}" not in text
        if "{value}" in d.content.what and "value" not in values:
            assert "Unknown" in rendered.what


# --- guidance (T-E24-130, pure part) -----------------------------------------------------------------


def live_warning(world: h.World) -> Warning:
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (w,) = world.by_definition("co2_above_limit")
    return w


@pytest.mark.trace("T-E24-130", "INV-E24-19", "D20", "Q1")
def test_t130_eligible_unreviewed_steps_are_served_in_live_with_their_exact_label(world: h.World) -> None:
    w = live_warning(world)
    served = guidance.serve(w, GuidanceVerdict("show", "live", "not_clinically_reviewed"))
    assert (
        served.mode == "show" and served.steps == w.steps and served.review_label == "not_clinically_reviewed"
    )
    reviewed = guidance.serve(w, GuidanceVerdict("show", "live", "clinically_reviewed"))
    assert reviewed.review_label == "clinically_reviewed"


@pytest.mark.trace("T-E24-130", "INV-E24-19")
@pytest.mark.parametrize("outcome", ["show_with_substitute", "not_here", "not_supported"])
def test_t130_anything_but_an_explicit_show_is_unsupported_with_no_steps(
    world: h.World, outcome: Literal["show_with_substitute", "not_here", "not_supported"]
) -> None:
    w = live_warning(world)
    served = guidance.serve(w, GuidanceVerdict(outcome, "live", "not_clinically_reviewed"))
    assert served.mode == "unsupported" and served.steps == () and served.review_label is None
    assert w.steps  # the stored steps are never altered


@pytest.mark.trace("T-E24-130", "INV-E24-19")
def test_t130_an_absent_or_failing_provider_is_unsupported_and_sim_content_never_serves_live(
    world: h.World,
) -> None:
    w = live_warning(world)
    assert guidance.serve(w, None).mode == "unsupported"
    assert (
        guidance.serve(w, GuidanceVerdict("show", "simulation_only", "not_clinically_reviewed")).mode
        == "unsupported"
    )
    assert (
        guidance.serve(w, GuidanceVerdict("show", "replay", "not_clinically_reviewed")).mode == "unsupported"
    )
    sim = replace(w, origin_mode=OriginMode.SIM)
    labeled = guidance.serve(sim, GuidanceVerdict("show", "simulation_only", "not_clinically_reviewed"))
    assert labeled.mode == "simulation_only" and labeled.steps == sim.steps


def test_the_content_ref_carries_the_pinned_hash_release_and_purpose(world: h.World) -> None:
    w = live_warning(world)
    ref = guidance.content_ref(w)
    assert (ref.definition_id, ref.content_sha256, ref.release_id, ref.origin_mode, ref.purpose) == (
        w.definition_id,
        w.content_hash,
        w.config_release_id,
        w.origin_mode,
        "patient_instruction",
    )
    assert len(ref.content_sha256) == 64
