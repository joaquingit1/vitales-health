from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.app.import_contracts import check_features
from crew_health.routine.domain.cadence import period_days
from crew_health.routine.domain.errors import ConfigInvalid
from crew_health.routine.domain.loader_checks import Bounds, check_release
from crew_health.routine.domain.release import (
    AnswerOn,
    EnvironmentOn,
    ObservationOn,
    OverlayOn,
    ReplaceCadence,
    UntilMissionEvent,
    parse_release,
)
from crew_health.routine.domain.windows import policy_window

from .support import NOW, REPO, SCHEMAS, document, example, release


@pytest.mark.trace("R37", "INV-E25-12")
def test_configuration_documents_validate_against_the_package_schemas() -> None:
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8"))))
        for p in sorted(SCHEMAS.glob("*.schema.json"))
    ]
    registry: Registry = Registry().with_resources(resources)
    for name in ("routine-schedule", "routine-rules"):
        schema = json.loads((SCHEMAS / f"config-{name}.schema.json").read_text(encoding="utf-8"))
        assert list(Draft202012Validator(schema, registry=registry).iter_errors(document(name))) == []


@pytest.mark.trace("INV-E25-12")
def test_configuration_edit_changes_content_without_code() -> None:
    schedule, rules = document("routine-schedule"), document("routine-rules")
    before = parse_release("rel_before", schedule, rules, example("ui-copy"), example("questionnaires"))
    items = cast(list[dict[str, object]], schedule["items"])
    steps = cast(list[str], items[0]["steps"])
    steps[0] = steps[0] + " Updated from configuration."
    after = parse_release("rel_after", schedule, rules, example("ui-copy"), example("questionnaires"))
    assert before.items[0].steps != after.items[0].steps
    assert before.item_content_sha256 != after.item_content_sha256


@pytest.mark.trace("R37", "T-E25-025")
def test_release_has_no_loader_errors_and_copy_has_only_authorized_edits() -> None:
    r = release()
    findings = check_release(r, Bounds(100, 100, 100), None, None)
    assert [f for f in findings if f.severity == "error"] == []
    assert list(findings) == sorted(findings, key=lambda f: (f.pointer, f.severity, f.message))
    assert (REPO / "config/routine-schedule.json").read_bytes() == (
        REPO / "docs/product/routine-schedule.json"
    ).read_bytes()
    expected = example("routine-rules")
    cast(dict[str, object], expected["calendar"])["anchor"] = "mission_start"
    expected["notice"] = "Not clinically reviewed."
    assert document("routine-rules") == expected
    assert r.question_carrier_item_id() == r.items[0].item_id
    assert r.item("absent") is None and r.policy("absent") is None


@pytest.mark.parametrize(
    "mutation,pointer",
    [
        ("text", "/bindings/0/binds/trigger_text"),
        ("item", "/bindings/0/binds/item_id"),
        ("index", "/bindings/0/binds/trigger_index"),
        ("duplicate", "/bindings/10/binding_id"),
        ("cadence", "/bindings/3/effect/cadence"),
        ("amount", "/items/0/steps/0"),
        ("sentences", "/items/0/plain"),
        ("environment", "/bindings/0/on"),
    ],
)
@pytest.mark.trace("R37", "T-E25-025")
@pytest.mark.trace("T-E25-036", "CONTRACTS 11", "E25-03")
def test_loader_rejects_golden_configuration_failures(mutation: str, pointer: str) -> None:
    # CONTRACTS 15: "the loader rejects any drift". Amounts are forbidden by E25 3.12.3 G5.
    schedule, rules = document("routine-schedule"), document("routine-rules")
    bindings = cast(list[dict[str, object]], rules["bindings"])
    binds = cast(dict[str, object], bindings[0]["binds"])
    items = cast(list[dict[str, object]], schedule["items"])
    if mutation == "text":
        binds["trigger_text"] = str(binds["trigger_text"]) + " Changed"
    elif mutation == "item":
        binds["item_id"] = "absent"
    elif mutation == "index":
        binds["trigger_index"] = 999
    elif mutation == "duplicate":
        bindings.append(deepcopy(bindings[0]))
    elif mutation == "cadence":
        effect = cast(dict[str, object], bindings[3]["effect"])
        cadence = cast(dict[str, object], effect["cadence"])
        cadence["note"] = "Changed"
    elif mutation == "amount":
        cast(list[str], items[0]["steps"])[0] += " 5 mg"
    elif mutation == "sentences":
        items[0]["plain"] = str(items[0]["plain"]) + " Another sentence."
    elif mutation == "environment":
        bindings[0]["on"] = {"kind": "environment_activated", "environments": ["station_leo"]}
    r = parse_release("rel_bad", schedule, rules, example("ui-copy"), example("questionnaires"))
    findings = check_release(r, Bounds(100, 100, 100), None, None)
    assert any(f.severity == "error" and f.pointer == pointer for f in findings)


@pytest.mark.trace("INV-E25-12", "T-E25-035")
def test_policy_and_calendar_edits_change_windows_and_periods() -> None:
    r = release()
    rules = document("routine-rules")
    policies = cast(list[dict[str, object]], rules["item_policies"])
    policies[0]["preferred_time"] = "09:15"
    cast(dict[str, object], rules["calendar"])["week_days"] = 9
    changed = parse_release("rel_changed", document("routine-schedule"), rules, None, None)
    before = policy_window(r.policy(r.items[0].item_id), NOW, NOW + timedelta(days=1))
    after = policy_window(changed.policy(r.items[0].item_id), NOW, NOW + timedelta(days=1))
    assert before is not None and after is not None
    assert after.due_from - before.due_from == timedelta(hours=2, minutes=15)
    weekly = next(
        c
        for item in r.items
        for c in (item.default_cadence, *item.by_environment.values())
        if c.word == "weekly"
    )
    assert period_days(weekly, r.calendar) == 7
    assert period_days(weekly, changed.calendar) == 9


@pytest.mark.parametrize("value", [{}, {"items": "wrong"}, {"items": [None]}, {"items": []}])
@pytest.mark.trace("R37")
def test_corrupt_schedule_raises_only_configuration_error(value: dict[str, object]) -> None:
    with pytest.raises(ConfigInvalid):
        parse_release("rel_bad", value, document("routine-rules"), None, None)


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(),
    lambda children: st.lists(children, max_size=5) | st.dictionaries(st.text(), children, max_size=5),
    max_leaves=20,
)


@given(json_values)
@pytest.mark.trace("R37")
def test_random_json_shapes_raise_only_configuration_error(value: object) -> None:
    with pytest.raises(ConfigInvalid):
        parse_release("rel_bad", cast(Mapping[str, object], value), document("routine-rules"), None, None)


@pytest.mark.trace("R37")
@pytest.mark.trace("T-E25-036", "CONTRACTS 11", "E25-03")
def test_reference_bounds_and_guidance_checks_reject_invalid_release() -> None:
    r = release()
    checks = check_release(r, Bounds(0, 0, 0), frozenset(), frozenset())
    pointers = {f.pointer for f in checks if f.severity == "error"}
    assert {"/items", "/bindings", "/bindings/0/effect"} <= pointers
    assert any("/definition_ids/" in p for p in pointers)
    assert any("/event_types/" in p for p in pointers)
    bad_item = replace(r.items[0], instrument="", steps=(), body_region="unknown", source_ids=("missing",))
    bad = replace(
        r, items=(bad_item, *r.items[1:]), misses=replace(r.misses, question_id="missing"), question_sets={}
    )
    checks = check_release(bad, Bounds(100, 100, 100), None, None)
    pointers = {f.pointer for f in checks if f.severity == "error"}
    assert {
        "/items/0/instrument",
        "/items/0/steps",
        "/items/0/body_region",
        "/items/0/source_ids/0",
        "/misses/question_id",
        "/bindings/0/effect/question_set_id",
    } <= pointers


@pytest.mark.trace("R37")
def test_duplicate_policy_is_not_silently_overwritten() -> None:
    rules = document("routine-rules")
    policies = cast(list[dict[str, object]], rules["item_policies"])
    policies.append(deepcopy(policies[0]))
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_bad", document("routine-schedule"), rules, None, None)
    assert error.value.findings[0].pointer == "/item_policies/8/item_id"


@pytest.mark.parametrize("bad_minutes", [2**53, -(2**53)])
@pytest.mark.trace("R37")
def test_noncanonical_content_numbers_raise_configuration_error(bad_minutes: int) -> None:
    schedule = document("routine-schedule")
    cast(list[dict[str, object]], schedule["items"])[0]["minutes"] = bad_minutes
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_bad", schedule, document("routine-rules"), None, None)
    assert error.value.findings[0].pointer.startswith("/items/0")


@pytest.mark.trace("R37")
def test_noncanonical_unicode_content_raises_configuration_error() -> None:
    schedule = document("routine-schedule")
    cast(list[dict[str, object]], schedule["items"])[0]["plain"] = "\ud800"
    with pytest.raises(ConfigInvalid):
        parse_release("rel_bad", schedule, document("routine-rules"), None, None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("minutes", "three"),
        ("minutes", True),
        ("plain", []),
        ("steps", [None]),
        ("who", {}),
        ("cadence", {"default": None}),
    ],
)
@pytest.mark.trace("R37")
def test_nested_item_shape_surprises_have_pointer_findings(field: str, value: object) -> None:
    schedule = document("routine-schedule")
    cast(list[dict[str, object]], schedule["items"])[0][field] = value
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_bad", schedule, document("routine-rules"), None, None)
    assert error.value.findings[0].pointer.startswith("/items/0/" + field)


@pytest.mark.parametrize(
    "part,value",
    [
        ("on", {"kind": "missing"}),
        ("effect", {"kind": "missing"}),
        ("until", {"kind": "missing"}),
        ("on", {"kind": "observation", "value": float("nan")}),
        ("on", {"kind": "observation", "value": "six"}),
        (
            "effect",
            {
                "kind": "add_occurrences",
                "at_trigger": 1,
                "times": [],
                "every_minutes": None,
                "at_clear": False,
                "basis": "team default",
            },
        ),
    ],
)
@pytest.mark.trace("R37")
def test_unknown_tags_and_invalid_binding_values_raise_configuration_error(part: str, value: object) -> None:
    rules = document("routine-rules")
    cast(list[dict[str, object]], rules["bindings"])[0][part] = value
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_bad", document("routine-schedule"), rules, None, None)
    assert error.value.findings[0].pointer.startswith("/bindings/0/" + part)


@pytest.mark.parametrize("bad_time", ["24:00", "07:60", "7:00", "bad"])
@pytest.mark.trace("R37")
def test_policy_time_requires_valid_hours_and_minutes(bad_time: str) -> None:
    rules = document("routine-rules")
    cast(list[dict[str, object]], rules["item_policies"])[0]["preferred_time"] = bad_time
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_bad", document("routine-schedule"), rules, None, None)
    assert error.value.findings[0].pointer == "/item_policies/0/preferred_time"


@pytest.mark.trace("R37")
def test_suppressed_overlay_object_form_matches_list_form() -> None:
    rules = document("routine-rules")
    rules["suppressed_overlays"] = {"overlays": rules["suppressed_overlays"]}
    r = parse_release("rel_object", document("routine-schedule"), rules, None, None)
    assert r.suppressed_overlays == release().suppressed_overlays


@pytest.mark.trace("R37")
def test_schema_tag_variants_are_typed_and_decimal_value_preserves_input() -> None:
    rules = document("routine-rules")
    bindings = cast(list[dict[str, object]], rules["bindings"])
    bindings[0]["on"] = {
        "kind": "answer",
        "question_id": release().misses.question_id,
        "presence": ["present"],
    }
    bindings[1]["on"] = {"kind": "environment_activated", "environments": ["station_leo"]}
    bindings[2]["on"] = {"kind": "overlay_planned", "overlay": "eva"}
    bindings[3]["until"] = {"kind": "mission_event", "event_types": ["landing"]}
    cast(dict[str, object], bindings[4]["on"])["value"] = 6.1
    r = parse_release("rel_tags", document("routine-schedule"), rules, None, None)
    assert isinstance(r.bindings[0].on, AnswerOn)
    assert isinstance(r.bindings[1].on, EnvironmentOn)
    assert isinstance(r.bindings[2].on, OverlayOn) and r.bindings[2].on.edge == "planned"
    assert isinstance(r.bindings[3].until, UntilMissionEvent)
    assert isinstance(r.bindings[4].on, ObservationOn) and r.bindings[4].on.value == Decimal("6.1")


@pytest.mark.trace("R37")
def test_loader_detects_cross_document_and_display_group_conflicts() -> None:
    r = release()
    carrier = r.bindings[0]
    invalid = replace(
        r,
        bindings=(replace(carrier, item_id=r.items[1].item_id), *r.bindings),
        display_groups=(*r.display_groups, r.display_groups[0]),
        policies={
            **r.policies,
            "unknown": replace(next(iter(r.policies.values())), item_id="unknown"),
            "duplicate": next(iter(r.policies.values())),
        },
    )
    findings = check_release(invalid, Bounds(100, 100, 100), None, None)
    assert any(f.message == "Question bindings require one carrier item" for f in findings)
    assert any(f.message == "Duplicate item policy" for f in findings)
    assert any(f.message == "Cadence word listed twice" for f in findings)
    assert any(f.pointer.startswith("/item_policies/") and f.message == "Unknown item" for f in findings)
    assert invalid.question_carrier_item_id() is None
    no_groups = replace(r, display_groups=())
    findings = check_release(no_groups, Bounds(100, 100, 100), None, None)
    assert any(f.pointer == "/bindings/3/effect/cadence/word" for f in findings)
    assert any(f.pointer == "/items/0/cadence" and f.severity == "error" for f in findings)


@pytest.mark.trace("R37")
def test_loader_warns_about_missing_walk_policy_and_looser_cadence_and_checks_person_sources() -> None:
    r = release()
    walk = next(i for i in r.items if i.default_cadence.word == "per_eva")
    periodic = next(i for i in r.items if i.default_cadence.word == "daily")
    monthly = next(i.default_cadence for i in r.items if i.default_cadence.word == "monthly")
    binding = replace(r.bindings[0], effect=ReplaceCadence(monthly))
    event = replace(r.bindings[7], audience="person")
    answer = replace(r.bindings[0], on=AnswerOn("unknown", ("present",)))
    item = replace(
        periodic, triggers=(replace(periodic.triggers[0], source_ids=("missing",)), *periodic.triggers[1:])
    )
    invalid = replace(
        r,
        items=tuple(item if i.item_id == item.item_id else i for i in r.items),
        policies={k: v for k, v in r.policies.items() if k != walk.item_id},
        bindings=(binding, event, answer),
    )
    findings = check_release(invalid, Bounds(100, 100, 100), None, None)
    assert any(f.message == "per_eva has no item policy" for f in findings)
    assert any(f.message == "Looser periodic cadence is additive" for f in findings)
    assert any(f.pointer == "/bindings/1/audience" for f in findings)
    assert any(f.pointer == "/bindings/2/on/question_id" for f in findings)
    assert any("/raise_frequency_when/0/source_ids/0" in f.pointer for f in findings)


@pytest.mark.parametrize(
    "target,pointer",
    [
        ("schedule", "/items/0/body_region"),
        ("schedule", "/items/0/cadence/default/n"),
        ("rules", "/item_policies/0/preferred_time"),
        ("rules", "/item_policies/0/window_minutes"),
        ("rules", "/item_policies/0/per_eva"),
        ("rules", "/bindings/4/effect/every_minutes"),
        ("rules", "/bindings/8/binds/environment_id"),
    ],
)
@pytest.mark.trace("R37")
def test_missing_required_nullable_field_is_a_shape_error(target: str, pointer: str) -> None:
    schedule, rules = document("routine-schedule"), document("routine-rules")
    current: object = schedule if target == "schedule" else rules
    parts = pointer.strip("/").split("/")
    for part in parts[:-1]:
        current = (
            cast(list[object], current)[int(part)]
            if isinstance(current, list)
            else cast(dict[str, object], current)[part]
        )
    del cast(dict[str, object], current)[parts[-1]]
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_bad", schedule, rules, None, None)
    assert error.value.findings[0].pointer == pointer


@pytest.mark.trace("INV-E25-07")
def test_domain_obeys_repository_import_purity_rules() -> None:
    check_features(REPO / "hub/src/crew_health", ("routine",))


@given(st.sampled_from(("minutes", "plain", "steps", "body_region", "cadence", "who")), json_values)
@pytest.mark.trace("R37")
def test_random_nested_item_values_never_leak_untyped_parser_errors(field: str, value: object) -> None:
    schedule = document("routine-schedule")
    cast(list[dict[str, object]], schedule["items"])[0][field] = value
    try:
        parsed = parse_release("rel_fuzz", schedule, document("routine-rules"), None, None)
    except ConfigInvalid as error:
        assert error.findings and all(f.severity == "error" for f in error.findings)
    else:
        assert parsed.release_id == "rel_fuzz"
