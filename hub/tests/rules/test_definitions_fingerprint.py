"""Definition parser and the two fingerprints (E24 3.2).

T-E24-010 (parser property), 013, 126 (pure parts); INV-E24-02, INV-E24-12, INV-E24-15.
"""

from __future__ import annotations

import copy
import json
import random
from functools import lru_cache
from typing import Any

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.rules.domain.definitions import (
    DefinitionInvalid,
    RulesDocument,
    definition_to_raw,
    parse_definition,
    parse_rules_document,
)
from crew_health.rules.domain.fingerprint import (
    ReleaseDocuments,
    content_hash,
    limit_fingerprint,
    rule_fingerprint,
)

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "R43", "INV-E24-12", "INV-E24-15")]

SCHEMAS = h.ROOT / "docs" / "product" / "api" / "schemas"


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    registry: Registry[Any] = Registry()
    for path in sorted(SCHEMAS.glob("*.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(contents["$id"], Resource.from_contents(contents))
    schema = json.loads((SCHEMAS / "config-warning-rules.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=registry)


def schema_valid(definition: object) -> bool:
    document = {
        "notice": "n",
        "headline": {"max_fragments": 1, "separator": ",", "none_text": "none"},
        "definitions": [definition],
    }
    return validator().is_valid(document)


def test_every_shipped_and_example_definition_is_schema_valid_and_parses() -> None:
    for raw in (h.deployment_rules(), h.example_value("warning-rules")):
        assert validator().is_valid(raw)
        document = parse_rules_document(raw)
        assert len(document.definitions) == len(raw["definitions"])  # type: ignore[arg-type]


POOL: list[object] = [
    None,
    "",
    "x",
    "act",
    "assistant",
    "rule",
    0,
    -1,
    1,
    3.5,
    True,
    False,
    [],
    ["a"],
    {},
    {"k": 1},
    "{a}",
    "station_leo",
]


@st.composite
def mutated(draw: st.DrawFn) -> object:
    definitions = [definition_to_raw(d) for d in parse_rules_document(h.deployment_rules()).definitions]
    raw = copy.deepcopy(draw(st.sampled_from(definitions)))
    for _ in range(draw(st.integers(0, 3))):
        container: Any = raw
        path: list[str] = []
        while (
            isinstance(container, dict)
            and draw(st.booleans())
            and any(isinstance(x, dict) for x in container.values())
        ):
            key = draw(st.sampled_from(sorted(k for k, x in container.items() if isinstance(x, dict))))
            container = container[key]
            path.append(key)
        if not isinstance(container, dict) or not container:
            continue
        key = draw(st.sampled_from(sorted(container)))
        action = draw(st.sampled_from(["set", "delete", "extra"]))
        if action == "set":
            container[key] = draw(st.sampled_from(POOL))
        elif action == "delete":
            del container[key]
        else:
            container["unexpected"] = draw(st.sampled_from(POOL))
    return raw


@given(mutated())
@h.PROP
@pytest.mark.trace("T-E24-010", "R43")
def test_t010_valid_definitions_parse_and_round_trip_and_invalid_ones_raise_only_definition_invalid(
    raw: object,
) -> None:
    try:
        parsed = parse_definition(raw)
    except DefinitionInvalid:
        return  # a typed rejection is the only allowed failure
    assert definition_to_raw(parsed) == raw  # round trip
    assert schema_valid(raw)  # the parser is never looser than the schema


@given(mutated())
@h.PROP
@pytest.mark.trace("T-E24-010")
def test_t010_a_schema_valid_definition_is_rejected_only_for_the_documented_extra_rules(raw: object) -> None:
    if not schema_valid(raw):
        return
    try:
        parse_definition(raw)
    except DefinitionInvalid as error:
        assert any(
            text in error.detail
            for text in (
                "needs scope person",
                "need a hazard_id",
                "steps from the assistant",
                "go together",
                "raised by rule",
                "info or watch only",
            )
        ), error.detail


# --- fingerprints ------------------------------------------------------------------------------------


def edited_docs(release_docs: ReleaseDocuments, document: str, mutate: Any) -> ReleaseDocuments:
    fields = {
        "sensor_limits": copy.deepcopy(dict(release_docs.sensor_limits)),
        "sensor_catalog": copy.deepcopy(dict(release_docs.sensor_catalog)),
        "questionnaires": copy.deepcopy(dict(release_docs.questionnaires)),
        "assistant_policy": copy.deepcopy(dict(release_docs.assistant_policy)),
    }
    mutate(fields[document])
    return ReleaseDocuments(**fields)


@pytest.mark.trace("T-E24-013")
def test_t013_a_content_edit_changes_the_content_hash_only(
    deployment_doc: RulesDocument, release_docs: ReleaseDocuments
) -> None:
    raw = copy.deepcopy(h.deployment_rules())
    for d in raw["definitions"]:  # type: ignore[attr-defined]
        d["content"]["title"] = d["content"]["title"] + " edited"
        d["content"]["source_ids"] = ["C25"]
    edited = parse_rules_document(raw)
    for before, after in zip(deployment_doc.definitions, edited.definitions, strict=True):
        assert content_hash(before) != content_hash(after)
        assert rule_fingerprint(before, release_docs) == rule_fingerprint(
            after, release_docs
        )  # words are not the rule


@pytest.mark.trace("INV-E24-12")
def test_provenance_edits_never_change_the_rule_fingerprint(
    deployment_doc: RulesDocument, release_docs: ReleaseDocuments
) -> None:
    raw = copy.deepcopy(h.deployment_rules())
    for d in raw["definitions"]:  # type: ignore[attr-defined]
        d["clear"]["basis"] = "sourced" if d["clear"]["basis"] == "team default" else "team default"
        cond = d["condition"]
        for key in ("basis", "to_be_set_by"):
            if key in cond:
                cond[key] = "flight rules" if key == "to_be_set_by" else "sourced"
        if "within_days_after_event" in cond:
            cond["within_days_after_event"]["basis"] = "sourced"
    edited = parse_rules_document(raw)
    for before, after in zip(deployment_doc.definitions, edited.definitions, strict=True):
        assert rule_fingerprint(before, release_docs) == rule_fingerprint(after, release_docs)


@pytest.mark.trace("T-E24-126", "INV-E24-15")
def test_t126_editing_only_the_referenced_limit_changes_the_fingerprint_of_its_definitions(
    deployment_doc: RulesDocument, release_docs: ReleaseDocuments
) -> None:
    def bump(doc: dict[str, Any]) -> None:
        for entry in doc["limits"]:
            if entry["limit_id"] == "o2_low_act":
                entry["value"] = entry["value"] - 1

    docs = edited_docs(release_docs, "sensor_limits", bump)
    changed = {
        d.id
        for d in deployment_doc.definitions
        if rule_fingerprint(d, docs) != rule_fingerprint(d, release_docs)
    }
    assert changed == {"oxygen_low_act"}
    assert limit_fingerprint("o2_low_act", docs) != limit_fingerprint("o2_low_act", release_docs)
    assert limit_fingerprint("o2_low_emergency", docs) == limit_fingerprint("o2_low_emergency", release_docs)


@pytest.mark.parametrize(
    ("document", "mutate", "expected"),
    [
        (
            "sensor_limits",
            lambda d: d["limits"][0]["clear"].__setitem__("dwell_seconds", 1),
            {"co2_above_limit"},
        ),
        ("sensor_limits", lambda d: d["limits"][0].__setitem__("min_coverage", 0.5), {"co2_above_limit"}),
        ("sensor_limits", lambda d: d["limits"][0].__setitem__("label", "other words"), set()),
        ("sensor_catalog", lambda d: d["sensor_types"][0].__setitem__("name", "words only"), set()),
    ],
)
def test_t126_dependencies_that_decide_clearing_are_in_the_fingerprint_and_words_are_not(
    deployment_doc: RulesDocument,
    release_docs: ReleaseDocuments,
    document: str,
    mutate: Any,
    expected: set[str],
) -> None:
    docs = edited_docs(release_docs, document, mutate)
    changed = {
        d.id
        for d in deployment_doc.definitions
        if rule_fingerprint(d, docs) != rule_fingerprint(d, release_docs)
    }
    assert changed == expected


def test_catalog_stale_bound_aggregation_questionnaire_and_policy_dependencies(
    deployment_doc: RulesDocument, release_docs: ReleaseDocuments
) -> None:
    def changed(document: str, mutate: Any) -> set[str]:
        docs = edited_docs(release_docs, document, mutate)
        return {
            d.id
            for d in deployment_doc.definitions
            if rule_fingerprint(d, docs) != rule_fingerprint(d, release_docs)
        }

    co2 = lambda d: next(t for t in d["sensor_types"] if t["id"] == "co2")  # noqa: E731
    assert {"co2_above_limit", "co2_week_high"} <= changed(
        "sensor_catalog", lambda d: co2(d).__setitem__("stale_after_seconds", 1)
    )
    quiet = changed("sensor_catalog", lambda d: co2(d).__setitem__("stale_after_seconds", 1))
    assert "sensor_quiet" in quiet  # the quiet rule covers every sensor type
    assert "co2_above_limit" in changed(
        "sensor_catalog", lambda d: co2(d)["aggregations"][1].__setitem__("window_seconds", 7200)
    )
    question = lambda d: next(q for q in d["questions"] if q["id"] == "dizzy_standing")  # noqa: E731
    assert changed(
        "questionnaires", lambda d: question(d)["choices"][0].__setitem__("presence", "unknown")
    ) == {"dizzy_after_landing"}
    assert changed(
        "assistant_policy", lambda d: d["insight_topics"][0].__setitem__("evidence_codes", ["x"])
    ) == {"assistant_pattern"}
    assert changed("assistant_policy", lambda d: d.__setitem__("allowed_warning_levels", ["info"])) == {
        "assistant_pattern"
    }


@given(st.randoms(use_true_random=False))
@h.PROP
@pytest.mark.trace("T-E24-010")
def test_fingerprints_do_not_depend_on_key_order(rng: random.Random) -> None:
    raw = h.deployment_rules()
    shuffled = copy.deepcopy(raw)
    for d in shuffled["definitions"]:  # type: ignore[attr-defined]
        for obj in (d, d["clear"], d["content"], d["condition"]):
            items = list(obj.items())
            rng.shuffle(items)
            obj.clear()
            obj.update(items)
    docs = h.release_documents()
    a, b = parse_rules_document(raw), parse_rules_document(shuffled)
    for x, y in zip(a.definitions, b.definitions, strict=True):
        assert content_hash(x) == content_hash(y) and rule_fingerprint(x, docs) == rule_fingerprint(y, docs)
