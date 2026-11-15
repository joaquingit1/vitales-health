"""The participation-rules document: it equals the package example, passes its schema and the loader checks, and a
configuration edit changes content with no code change (AGENTS hard rule 1; E22 section 8; T-E22-060, T-E22-121)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.participation.domain.errors import InvalidRules
from crew_health.participation.domain.rules import MAX_THRESHOLDS, MAX_WINDOW_DAYS, parse_rules

from .harness import CONFIG, PROP, config_document, example_value, rules, validator

pytestmark = pytest.mark.E22


@pytest.mark.trace("T-E22-060", "V75")
def test_document_equals_package_example_exactly() -> None:
    assert config_document() == example_value("config/participation-rules.example.json")
    assert CONFIG.read_text(encoding="utf-8").endswith("}\n")


@pytest.mark.trace("T-E22-121")
def test_document_passes_its_schema_and_provenance_is_kept() -> None:
    document = config_document()
    assert list(validator("config-participation-rules.schema.json").iter_errors(document)) == []
    assert document["basis"] == "team default"
    assert rules().basis == "team default"
    assert rules().practice is not None
    assert rules().practice.to_be_set_by is None  # type: ignore[union-attr]


@pytest.mark.trace("T-E22-060", "V75")
def test_configuration_edit_changes_content_with_no_code_change() -> None:
    document = config_document()
    base = rules()
    assert base.patch_thresholds == tuple(document["patch_thresholds"])  # type: ignore[arg-type]
    edited: dict[str, Any] = copy.deepcopy(document)
    edited["patch_thresholds"] = [5, 10]
    edited["patch_designs"] = [
        {**d, "threshold": t} for d, t in zip(edited["patch_designs"][:2], (5, 10), strict=True)
    ]
    edited["crew_goal_window_days"] = 3
    changed = parse_rules(edited)
    assert changed.patch_thresholds == (5, 10)
    assert changed.crew_goal_window_days == 3
    assert changed.design_for(5) is not None
    assert changed.design_for(7) is None
    assert changed.patch_designs[0].label == base.patch_designs[0].label


def _mutations() -> list[tuple[str, Any]]:
    def mutate(fn: Any) -> dict[str, Any]:
        doc: dict[str, Any] = copy.deepcopy(config_document())
        fn(doc)
        return doc

    return [
        ("thresholds descending", mutate(lambda d: d.update(patch_thresholds=[14, 7, 30]))),
        ("thresholds duplicate", mutate(lambda d: d.update(patch_thresholds=[7, 7, 30]))),
        ("threshold zero", mutate(lambda d: d.update(patch_thresholds=[0, 14, 30]))),
        ("threshold bool", mutate(lambda d: d.update(patch_thresholds=[True, 14, 30]))),
        ("thresholds empty", mutate(lambda d: d.update(patch_thresholds=[]))),
        (
            "thresholds too many",
            mutate(lambda d: d.update(patch_thresholds=list(range(1, MAX_THRESHOLDS + 2)))),
        ),
        ("design missing", mutate(lambda d: d["patch_designs"].pop())),
        ("designs empty", mutate(lambda d: d.update(patch_designs=[]))),
        ("design threshold differs", mutate(lambda d: d["patch_designs"][0].update(threshold=8))),
        ("design duplicate id", mutate(lambda d: d["patch_designs"][1].update(patch_design_id="patch_week"))),
        ("design bad id", mutate(lambda d: d["patch_designs"][0].update(patch_design_id="Patch Week"))),
        ("design empty label", mutate(lambda d: d["patch_designs"][0].update(label=""))),
        ("design extra field", mutate(lambda d: d["patch_designs"][0].update(extra=1))),
        ("window zero", mutate(lambda d: d.update(crew_goal_window_days=0))),
        ("window too long", mutate(lambda d: d.update(crew_goal_window_days=MAX_WINDOW_DAYS + 1))),
        ("window float", mutate(lambda d: d.update(crew_goal_window_days=7.0))),
        ("version empty", mutate(lambda d: d.update(derivation_version=""))),
        ("version missing", mutate(lambda d: d.pop("derivation_version"))),
        ("basis unknown", mutate(lambda d: d.update(basis="guess"))),
        ("unknown top field", mutate(lambda d: d.update(crew_goal_progress_bands=4))),
        (
            "practice id equals mission id",
            mutate(lambda d: d["practice"]["patch_designs"][0].update(patch_design_id="patch_week")),
        ),
        (
            "practice ids repeat",
            mutate(
                lambda d: d["practice"]["patch_designs"][1].update(patch_design_id="patch_practice_first")
            ),
        ),
        (
            "practice counts measurements",
            mutate(lambda d: d["practice"].update(drill_measurements_count=True)),
        ),
        ("practice counts other", mutate(lambda d: d["practice"].update(counts="drill_scored"))),
        (
            "practice kind unknown",
            mutate(lambda d: d["practice"]["patch_designs"][0].update(kind="best_score")),
        ),
        ("practice empty designs", mutate(lambda d: d["practice"].update(patch_designs=[]))),
        ("practice skills repeat", mutate(lambda d: d["practice"].update(core_skill_ids=["cpr", "cpr"]))),
        ("practice setter unknown", mutate(lambda d: d["practice"].update(to_be_set_by="the captain"))),
        ("practice missing field", mutate(lambda d: d["practice"].pop("basis"))),
        ("practice threshold zero", mutate(lambda d: d["practice"]["patch_designs"][1].update(threshold=0))),
        ("practice basis unknown", mutate(lambda d: d["practice"].update(basis="guess"))),
    ]


@pytest.mark.trace("T-E22-121", "T-E22-038")
@pytest.mark.parametrize(("name", "document"), _mutations(), ids=lambda v: v if isinstance(v, str) else "doc")
def test_invalid_document_is_rejected_whole(name: str, document: dict[str, Any]) -> None:
    with pytest.raises(InvalidRules):
        parse_rules(document)


@pytest.mark.trace("T-E22-121")
def test_not_a_mapping_is_rejected_and_round_trip_is_stable() -> None:
    with pytest.raises(InvalidRules):
        parse_rules([])  # type: ignore[arg-type]
    document = config_document()
    again = parse_rules(json.loads(json.dumps(document)))
    assert again == rules()


@pytest.mark.trace("T-E22-121")
@PROP
@given(
    thresholds=st.lists(
        st.integers(min_value=1, max_value=400), min_size=1, max_size=MAX_THRESHOLDS, unique=True
    ),
    window=st.integers(min_value=1, max_value=MAX_WINDOW_DAYS),
)
def test_any_sorted_threshold_set_with_one_design_each_is_accepted(
    thresholds: list[int], window: int
) -> None:
    ordered = sorted(thresholds)
    doc: dict[str, Any] = copy.deepcopy(config_document())
    doc["patch_thresholds"] = ordered
    doc["patch_designs"] = [
        {"threshold": t, "patch_design_id": f"patch_{i}", "label": f"Label {i}"}
        for i, t in enumerate(ordered)
    ]
    doc["crew_goal_window_days"] = window
    parsed = parse_rules(doc)
    assert parsed.patch_thresholds == tuple(ordered)
    assert parsed.crew_goal_window_days == window
