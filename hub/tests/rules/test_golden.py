"""T-E24-131: independently sourced golden cases agree with the configured release (AGENTS.md 1).

The cases live in `golden/cases.yaml`, each with its document, section and a quote that must occur
verbatim in that document, and the second reviewer's recorded check against that source. This runner executes the
pure domain against the cases; it never edits them.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from crew_health.rules.domain import clear_evidence
from crew_health.rules.domain.definitions import RulesDocument
from crew_health.rules.domain.headline import headline
from crew_health.rules.domain.hold import route_act
from crew_health.rules.domain.vocabulary import Level
from crew_health.rules.domain.warning import HeadlineFields, Warning

from . import rules_harness as h

pytestmark = [pytest.mark.E24, pytest.mark.trace("T-E24-131", "R36", "R43")]

CASES = yaml.safe_load((Path(__file__).parent / "golden" / "cases.yaml").read_text(encoding="utf-8"))
BY_KIND: dict[str, list[dict[str, Any]]] = {}
for case in CASES["cases"]:
    BY_KIND.setdefault(case["kind"], []).append(case)


def normal(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_golden_file_declares_its_review_state_and_every_quote_is_in_its_source() -> None:
    assert CASES["checked_by"]  # a second reviewer, not the author, checked every case against its source
    outcomes = ("agrees:", "agrees_with_assumption:", "corrected:", "needs_decision:")
    for case in CASES["cases"]:
        assert str(case.get("check", "")).startswith(outcomes), case["id"]
        source = case["source"]
        text = normal((h.ROOT / source["document"]).read_text(encoding="utf-8"))
        assert normal(source["quote"]) in text, case["id"]


def test_every_case_kind_has_a_runner() -> None:
    assert set(BY_KIND) == {
        "headline",
        "storm_level",
        "dwell",
        "escalation",
        "occurrences",
        "act_routing",
        "evidence_after_raise",
    }


def raised(world: h.World, limit: str = "co2_1h_act") -> Warning:
    world.feed(world.sensor(results=[h.result(limit, crossed=True, clear_holds=False)]))
    (w,) = world.by_definition("co2_above_limit")
    return w


@pytest.mark.parametrize("case", BY_KIND["headline"], ids=lambda c: c["id"])
def test_headline_golden(case: dict[str, Any], world: h.World, example_doc: RulesDocument) -> None:
    path = (
        h.ROOT / "docs" / "product" / "api" / "examples" / "warnings" / f"list-{case['input']['list']}.json"
    )
    items = yaml.safe_load(path.read_text(encoding="utf-8"))["value"]["items"]
    base = raised(world)
    warnings = [
        replace(
            base,
            warning_id=f"wrn_{i}",
            headline=HeadlineFields(x["headline"]["fragment"], x["headline"]["rank"], x["headline"]["solo"]),
            raised_at=datetime.fromisoformat(str(x["raised_at"]).replace("Z", "+00:00")),
        )
        for i, x in enumerate(items)
    ]
    assert headline(warnings, example_doc.headline).text == case["expected"]["text"]


@pytest.mark.parametrize("case", BY_KIND["storm_level"], ids=lambda c: c["id"])
def test_storm_level_golden(case: dict[str, Any], deployment_doc: RulesDocument) -> None:
    assert deployment_doc.by_id()["radiation_storm_shelter"].level is Level(case["expected"]["level"])


@pytest.mark.parametrize("case", BY_KIND["dwell"], ids=lambda c: c["id"])
def test_dwell_golden(case: dict[str, Any], world: h.World) -> None:
    raised(world)
    (w,) = world.by_definition("co2_above_limit")
    step, count = case["input"]["step_seconds"], case["input"]["evaluations"]
    cleared_at: int | None = None
    for index in range(count):
        world.now = h.T0 + timedelta(seconds=step * (index + 1))
        world.feed(world.sensor(results=[h.result(case["input"]["limit"], crossed=False, clear_holds=True)]))
        if not world.warnings[w.warning_id].active:
            cleared_at = index + 1
            break
    expected = case["expected"]["cleared_at_evaluation"]
    assert cleared_at == expected
    reason = world.warnings[w.warning_id].clear_reason
    assert (reason.value if reason else None) == case["expected"]["clear_reason"]


@pytest.mark.parametrize("case", BY_KIND["escalation"], ids=lambda c: c["id"])
def test_escalation_golden(case: dict[str, Any], world: h.World) -> None:
    kw: h.SensorKw = {"sensor_type": "o2", "channel": "ch_o2"}
    for limit in case["input"].get("active", []):
        world.feed(world.sensor(results=[h.result(limit, crossed=True, clear_holds=False)], **kw))
        world.advance(5)
    before = {w.warning_id for w in world.warnings.values()}
    results = [h.result(limit, crossed=True, clear_holds=False) for limit in case["input"]["crossed"]]
    world.feed(world.sensor(results=results, **kw))
    expected = case["expected"]
    if "active_after" in expected:
        active = sorted(w.definition_id for w in world.warnings.values() if w.active)
        assert active == sorted(expected["active_after"])
    if "raised" in expected:
        new = [w for w in world.warnings.values() if w.warning_id not in before]
        assert sorted(w.definition_id for w in new) == sorted(expected["raised"])
    if "superseded" in expected:
        superseded = [
            w.definition_id
            for w in world.warnings.values()
            if w.clear_reason and w.clear_reason.value == "superseded"
        ]
        assert sorted(superseded) == sorted(expected["superseded"])


@pytest.mark.parametrize("case", BY_KIND["occurrences"], ids=lambda c: c["id"])
def test_occurrences_golden(case: dict[str, Any], world: h.World) -> None:
    results: list[bool] = []
    for index, (kind, minute) in enumerate(case["input"]["delivery"]):
        when = h.T0 + timedelta(minutes=minute)
        world.now = h.T0 + timedelta(minutes=30 + index)  # delivery order, all after the occurrences
        world.now = max(world.now, when)
        event_type = "space_weather_alert" if kind == "alert" else "space_weather_all_clear"
        world.feed(world.event(f"evt_{index}", event_type, when))
        results.append(bool(world.by_definition("radiation_storm_shelter")))
    assert results == case["expected"]["active_after"]


@pytest.mark.parametrize("case", BY_KIND["act_routing"], ids=lambda c: c["id"])
def test_act_routing_golden(case: dict[str, Any]) -> None:
    given = case["input"]
    routing = route_act(
        sleepers=given["sleepers"],
        awake_responders=given["awake"],
        acknowledged_by=given["acknowledged_by"],
        elapsed_seconds=given["elapsed"],
        wake_after_seconds=given["wake_after"],
        responders_may_receive=given["may_receive"],
    )
    assert list(routing.notify_now) == case["expected"]["notify_now"]
    assert list(routing.wake_now) == case["expected"]["wake_now"]
    assert routing.wake_after_seconds == case["expected"]["wake_after"]


@pytest.mark.parametrize("case", BY_KIND["evidence_after_raise"], ids=lambda c: c["id"])
def test_evidence_after_raise_golden(case: dict[str, Any], world: h.World) -> None:
    w = raised(world)
    flags: list[bool] = []
    for observed_offset, hub_offset in case["input"]["cases"]:
        now = h.T0 + timedelta(seconds=hub_offset)
        when = h.T0 + timedelta(seconds=observed_offset)
        inp = world.sensor(at=when, results=[h.result("co2_1h_act", crossed=False, clear_holds=True)])
        flags.append(clear_evidence.eligible(inp, w, now).eligible)
    assert flags == case["expected"]["eligible"]
