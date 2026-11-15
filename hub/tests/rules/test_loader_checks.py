"""Loader checks of `warning-rules.json` (E24 8, R-E24-10, CONTRACTS 11 checks 5 and 13).

T-E24-016, 127, 128 (loader part), 090 to 093 (pure); they only reject.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
import rules_harness as h

from crew_health.rules.domain import loader_checks as lc
from crew_health.rules.domain.definitions import parse_rules_document

pytestmark = [pytest.mark.E24, pytest.mark.trace("R43", "INV-E24-12", "INV-E24-15")]


def context() -> dict[str, Any]:
    limits = {e["limit_id"]: e for e in h.example_value("sensor-limits")["limits"]}
    return {
        "max_definitions": 500,
        "limits": limits,
        "what_to_do": h.what_to_do_by_hazard(),
        "topics": h.example_value("assistant-policy")["insight_topics"],
        "prohibited_phrases": None,
    }


def run(raw: object, **override: Any) -> tuple[str, ...]:
    ctx = {**context(), **override}
    return tuple(sorted({f.check_id for f in lc.check_document(raw, **ctx)}))


def edit(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    raw: dict[str, Any] = copy.deepcopy(h.deployment_rules())
    mutate(raw)
    return raw


def definition(raw: dict[str, Any], definition_id: str) -> dict[str, Any]:
    return next(d for d in raw["definitions"] if d["id"] == definition_id)


def test_the_shipped_deployment_document_passes_every_check() -> None:
    assert lc.check_document(h.deployment_rules(), **context()) == ()
    # The package example keeps a notification expiry on the dizziness rule; E24's rules.expiry_scope refuses it
    # (E24 3.8, R-E24-20), which is why the deployment document differs from the example there.
    findings = lc.check_document(h.example_value("warning-rules"), **context())
    assert [(f.check_id, f.pointer) for f in findings] == [
        ("rules.expiry_scope", "/definitions/14/clear/expires_after_seconds")
    ]


def test_findings_carry_no_document_name_so_the_application_stamps_it() -> None:
    raw = edit(lambda r: definition(r, "co2_above_limit")["content"].__setitem__("title", "{nope}"))
    findings = lc.check_document(raw, **context())
    assert findings and all(f.document is None and f.pointer.startswith("/definitions/") for f in findings)


@pytest.mark.trace("T-E24-128", "INV-E24-15")
def test_t128_a_non_null_expiry_on_a_rule_definition_is_rejected() -> None:
    raw = edit(
        lambda r: definition(r, "dizzy_after_landing")["clear"].__setitem__("expires_after_seconds", 100)
    )
    assert run(raw) == ("rules.expiry_scope",)
    ok = edit(lambda r: definition(r, "assistant_pattern")["clear"].__setitem__("expires_after_seconds", 100))
    assert run(ok) == ()


def test_check_13_placeholders_and_value_placeholders_on_non_sensor_definitions() -> None:
    unknown = edit(
        lambda r: definition(r, "co2_above_limit")["content"].__setitem__("what", "{not_listed} here")
    )
    assert run(unknown) == ("13",)
    value_on_rule = edit(
        lambda r: definition(r, "ground_silence")["content"].__setitem__("what", "{value} now")
    )
    assert run(value_on_rule) == ("rules.value_placeholder",)
    on_why = edit(lambda r: definition(r, "dizzy_after_landing")["content"].__setitem__("why", "{bad_name}"))
    assert run(on_why) == ("13",)


def test_level_and_sensor_type_must_equal_the_limits() -> None:
    level = edit(lambda r: definition(r, "oxygen_low_act").__setitem__("level", "watch"))
    assert run(level) == ("rules.limit_level",)
    sensor = edit(lambda r: definition(r, "oxygen_low_act").__setitem__("sensor_type_id", "co2"))
    assert run(sensor) == ("rules.limit_sensor",)


def test_check_5_every_active_warning_limit_has_exactly_one_definition() -> None:
    missing = edit(lambda r: r["definitions"].remove(definition(r, "oxygen_low_act")))
    assert run(missing) == ("5",)
    twice = edit(
        lambda r: r["definitions"].append(
            {**copy.deepcopy(definition(r, "oxygen_low_act")), "id": "oxygen_low_act_2"}
        )
    )
    assert run(twice) == ("5",)
    unknown_limit = edit(
        lambda r: definition(r, "oxygen_low_act")["condition"].__setitem__("limit_id", "o2_missing")
    )
    assert "5" in run(unknown_limit)


@pytest.mark.parametrize(
    ("raise_cmp", "raise_v", "clear_cmp", "clear_v", "safe"),
    [
        ("above", "3", "at_or_below", "3", True),
        ("above", "3", "below", "3", True),
        ("above", "3", "below", "2", True),
        ("above", "3", "at_or_below", "4", False),
        ("above", "3", "at_or_above", "2", False),
        ("at_or_above", "2.5", "below", "2.5", True),
        ("at_or_above", "2.5", "at_or_below", "2.5", False),  # overlaps at the threshold
        ("below", "145", "at_or_above", "145", True),
        ("below", "145", "above", "150", True),
        ("below", "145", "at_or_above", "140", False),
        ("at_or_below", "10", "above", "10", True),
        ("at_or_below", "10", "at_or_above", "10", False),
    ],
)
def test_a_clear_is_the_complement_or_stricter(
    raise_cmp: str, raise_v: str, clear_cmp: str, clear_v: str, safe: bool
) -> None:
    assert lc.clear_is_safe(raise_cmp, Decimal(raise_v), clear_cmp, Decimal(clear_v)) is safe


def test_a_weak_clear_in_the_limits_is_rejected() -> None:
    limits = copy.deepcopy(context()["limits"])
    limits["co2_1h_act"]["clear"]["value"] = 5
    assert run(h.deployment_rules(), limits=limits) == ("rules.limit_clear",)


def test_hazard_steps_need_what_to_do_at_the_level() -> None:
    what = copy.deepcopy(h.what_to_do_by_hazard())
    del what["co2"]["act"]
    assert run(h.deployment_rules(), what_to_do=what) == ("rules.hazard_steps",)


def test_every_topic_names_an_assistant_insight_definition_listing_it() -> None:
    topics = copy.deepcopy(context()["topics"])
    topics[0]["warning_definition_id"] = "co2_above_limit"
    assert run(h.deployment_rules(), topics=topics) == ("rules.topic_definition",)
    topics[0]["warning_definition_id"] = "nonexistent"
    assert run(h.deployment_rules(), topics=topics) == ("rules.topic_definition",)
    topics[0]["warning_definition_id"] = None  # a topic with no definition is allowed
    assert run(h.deployment_rules(), topics=topics) == ()


def test_the_prohibited_phrase_check_runs_only_with_a_configured_list() -> None:
    raw = edit(
        lambda r: definition(r, "co2_above_limit")["content"].__setitem__("why", "The air is safe here.")
    )
    assert run(raw) == ()  # no list is declared: the check does not run and invents no list
    assert run(raw, prohibited_phrases=["the air is safe"]) == ("rules.prohibited_phrase",)
    assert run(raw, prohibited_phrases=[]) == ()


def test_too_many_definitions_and_an_unparsable_document_are_rejected() -> None:
    assert run(h.deployment_rules(), max_definitions=3) == ("rules.max_definitions",)
    assert run({"notice": "n"}) == ("1",)
    assert run("not a document") == ("1",)
    broken = edit(lambda r: definition(r, "oxygen_low_act").__setitem__("level", "assistant"))
    assert run(broken) == ("1",)
    assert parse_rules_document(h.deployment_rules())  # a valid candidate is untouched by the failing ones
