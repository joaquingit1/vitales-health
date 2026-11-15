"""`config/warning-rules.json`: what it is, what differs from the package example and why (E24 8).

Traces R36, R40, R43 and INV-E24-01, INV-E24-02, INV-E24-12, INV-E24-15. T-E24-090 to 092, 128 (pure part).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
import rules_harness as h

from crew_health.rules.domain.definitions import (
    MissionEventCondition,
    RulesDocument,
    definition_to_raw,
    parse_rules_document,
)
from crew_health.rules.domain.fingerprint import ReleaseDocuments, rule_fingerprint
from crew_health.rules.domain.rendering import sentences
from crew_health.rules.domain.vocabulary import EnvironmentId, Level, StepsFrom

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "R43", "INV-E24-12", "INV-E24-15")]

EXPECTED_DIFFERENCES = {
    # D2 and UC05 step 1: a storm shelter call is Emergency; the shelter has no hazard level index.
    ("radiation_storm_shelter", "level"),
    ("radiation_storm_shelter", "hazard_level_index"),
    # E24 8 and 3.14: the listed steps are the radiation `act` sentences; words name no absent source.
    ("radiation_storm_shelter", "content.steps"),
    ("radiation_storm_shelter", "content.what"),
    ("radiation_storm_shelter", "content.citation_template"),
    # E24 3.8: a rule warning ends by evidence or a resolution, never by a notification expiry.
    ("dizzy_after_landing", "clear.expires_after_seconds"),
}


def _differences(a: dict[str, Any], b: dict[str, Any]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    assert [d["id"] for d in a["definitions"]] == [d["id"] for d in b["definitions"]]
    for da, db in zip(a["definitions"], b["definitions"], strict=True):
        for key in da:
            if isinstance(da[key], dict):
                for sub in da[key]:
                    if da[key][sub] != db[key][sub]:
                        out.add((da["id"], f"{key}.{sub}"))
            elif da[key] != db[key]:
                out.add((da["id"], key))
    for key in a:
        if key != "definitions" and a[key] != b[key]:
            out.add(("document", key))
    return out


def test_deployment_document_parses_and_matches_the_example_except_where_decided() -> None:
    deployment, example = h.deployment_rules(), h.example_value("warning-rules")
    assert _differences(deployment, example) == EXPECTED_DIFFERENCES


def test_storm_is_emergency_with_the_radiation_act_sentences(deployment_doc: RulesDocument) -> None:
    storm = deployment_doc.by_id()["radiation_storm_shelter"]
    assert storm.level is Level.EMERGENCY
    assert storm.content.steps.source is StepsFrom.LISTED
    act_text = h.what_to_do_by_hazard()["radiation"]["act"]
    assert storm.content.steps.items == sentences(act_text)
    assert len(storm.content.steps.items) == 3


def test_storm_event_types_hold_no_vehicle_alarm_before_r_e24_9(deployment_doc: RulesDocument) -> None:
    storm = deployment_doc.by_id()["radiation_storm_shelter"]
    assert isinstance(storm.condition, MissionEventCondition)
    assert storm.condition.event_types == ("space_weather_alert",)
    assert storm.condition.clear_event_types == ("space_weather_all_clear",)
    names = {d.id for d in deployment_doc.definitions}
    assert not any("vehicle" in n for n in names)


def test_storm_words_name_no_absent_source(deployment_doc: RulesDocument) -> None:
    storm = deployment_doc.by_id()["radiation_storm_shelter"]
    text = " ".join([storm.content.what, storm.content.citation_template]).lower()
    assert "monitor" not in text
    assert "ground" not in text


def test_dizzy_has_no_expiry_and_stays_resolvable_by_the_person(deployment_doc: RulesDocument) -> None:
    dizzy = deployment_doc.by_id()["dizzy_after_landing"]
    assert dizzy.clear.expires_after_seconds is None
    assert dizzy.clear.person_may_resolve is True


def test_null_thresholds_stay_null(deployment_doc: RulesDocument) -> None:
    by_id = deployment_doc.by_id()
    assert by_id["ground_silence"].condition.after_next_contact_seconds is None  # type: ignore[union-attr]
    assert by_id["supply_cover_low"].condition.below_uses is None  # type: ignore[union-attr]


def test_every_definition_round_trips(deployment_doc: RulesDocument) -> None:
    raw = h.deployment_rules()
    assert [definition_to_raw(d) for d in deployment_doc.definitions] == raw["definitions"]


# --- T-E24-090 to 092: a configuration edit changes content with no code change ---------------------


def _edited(mutator: object) -> RulesDocument:
    raw = copy.deepcopy(h.deployment_rules())
    assert callable(mutator)
    mutator(raw)
    return parse_rules_document(raw)


def test_t090_a_threshold_edit_changes_the_rule_fingerprint(
    release_docs: ReleaseDocuments, deployment_doc: RulesDocument
) -> None:
    """Scratch copy edit of a sourced limit value (D15): the fingerprint of the definition moves."""
    limits = copy.deepcopy(dict(release_docs.sensor_limits))
    for entry in limits["limits"]:  # type: ignore[attr-defined]
        if entry["limit_id"] == "co2_1h_act":
            entry["value"] = 4
    edited = ReleaseDocuments(
        limits, release_docs.sensor_catalog, release_docs.questionnaires, release_docs.assistant_policy
    )
    d = deployment_doc.by_id()["co2_above_limit"]
    assert rule_fingerprint(d, edited) != rule_fingerprint(d, release_docs)
    other = deployment_doc.by_id()["oxygen_low_act"]
    assert rule_fingerprint(other, edited) == rule_fingerprint(other, release_docs)


def test_t091_a_listed_step_edit_reaches_the_next_warning(release_docs: ReleaseDocuments) -> None:
    def edit(raw: dict[str, object]) -> None:
        for d in raw["definitions"]:  # type: ignore[attr-defined]
            if d["id"] == "radiation_storm_shelter":
                d["content"]["steps"]["items"][0] = "Edited first step."

    doc = _edited(edit)
    world = h.World(doc, release_docs)
    from crew_health.rules.domain.inputs import MissionEventInput
    from crew_health.rules.domain.vocabulary import OriginMode

    world.feed(
        MissionEventInput(
            "evt_1",
            "space_weather_alert",
            "Space weather alert",
            h.T0,
            h.T0,
            h.T0,
            OriginMode.LIVE,
            "station_leo",
            (),
            None,
        )
    )
    (w,) = world.by_definition("radiation_storm_shelter")
    assert w.steps[0] == "Edited first step."


def test_t092_the_environment_switch_changes_the_evaluated_definitions(deployment_doc: RulesDocument) -> None:
    def evaluated(env: EnvironmentId) -> set[str]:
        return {d.id for d in deployment_doc.definitions if env in d.environments}

    assert "dust_lunar_act" in evaluated(EnvironmentId.LUNAR_SURFACE)
    assert "dust_lunar_act" not in evaluated(EnvironmentId.STATION_LEO)
    assert "dust_mars_act" in evaluated(EnvironmentId.MARS_SURFACE)


def test_no_definition_changes_a_safety_mapping_when_content_is_edited(deployment_doc: RulesDocument) -> None:
    """A content-only edit never touches level, raiser or kind (AGENTS.md 1)."""
    raw = copy.deepcopy(h.deployment_rules())
    for d in raw["definitions"]:
        d["content"]["title"] = d["content"]["title"] + " edited"
    edited = parse_rules_document(raw)
    assert [(d.id, d.level, d.raised_by, d.condition.kind) for d in edited.definitions] == [
        (d.id, d.level, d.raised_by, d.condition.kind) for d in deployment_doc.definitions
    ]


def test_json_file_is_valid_utf8_without_dashes() -> None:
    text = (h.ROOT / "config" / "warning-rules.json").read_text(encoding="utf-8")
    json.loads(text)
    assert chr(0x2014) not in text and chr(0x2013) not in text
