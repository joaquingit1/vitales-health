"""The domain vocabulary mirrors the contract enums (E24 3.1).

Traces R43 and INV-E24-12: a contract change cannot go unnoticed by the domain.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

import pytest

from crew_health.contracts_gen import enums as generated
from crew_health.rules.domain import vocabulary as v

pytestmark = [pytest.mark.E24, pytest.mark.trace("R43", "INV-E24-12")]

SCHEMAS = Path(__file__).resolve().parents[3] / "docs" / "product" / "api" / "schemas"


def schema(name: str) -> dict[str, object]:
    loaded = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def values(enum: type[StrEnum]) -> set[str]:
    return {m.value for m in enum}


def common(name: str) -> dict[str, object]:
    return schema("common.schema.json")["$defs"][name]  # type: ignore[index,return-value]


def warning_property(name: str) -> dict[str, object]:
    return schema("warning.schema.json")["properties"][name]  # type: ignore[index,return-value]


def test_level_is_ordered_info_watch_act_emergency_and_compares_by_rank() -> None:
    assert [lv.value for lv in v.LEVEL_ORDER] == common("level")["enum"]  # type: ignore[comparison-overlap]
    assert v.Level.EMERGENCY > v.Level.ACT > v.Level.WATCH > v.Level.INFO
    assert max(v.Level.WATCH, v.Level.EMERGENCY) is v.Level.EMERGENCY  # not alphabetical order


def test_raised_by_origin_environment_basis_presence() -> None:
    assert values(v.RaisedBy) == set(common("raised_by")["enum"])  # type: ignore[arg-type]
    assert values(v.OriginMode) == set(common("origin_mode")["enum"])  # type: ignore[arg-type]
    assert values(v.EnvironmentId) == set(common("environment_id")["enum"])  # type: ignore[arg-type]
    assert values(v.Basis) == set(common("basis")["enum"])  # type: ignore[arg-type]
    assert values(v.Presence) == set(common("presence")["enum"])  # type: ignore[arg-type]
    assert v.OVERLAYS == set(common("overlay")["enum"])  # type: ignore[arg-type]
    assert v.BODY_REGIONS == set(common("body_region")["enum"])  # type: ignore[arg-type]


def test_actor_kinds_and_evidence_kinds() -> None:
    actor = common("actor")["properties"]["kind"]["enum"]  # type: ignore[index]
    assert v.ACTOR_KINDS == set(actor)
    kinds = common("evidence_ref")["properties"]["kind"]["enum"]  # type: ignore[index]
    assert values(v.EvidenceKind) == set(kinds) == values(generated.EvidenceKind)  # type: ignore[arg-type]


def test_warning_state_and_clear_reason_and_scope() -> None:
    assert values(v.WarningState) == set(warning_property("state")["enum"])  # type: ignore[arg-type]
    reasons = warning_property("clear_reason")["anyOf"][0]["enum"]  # type: ignore[index]
    assert values(v.ClearReason) == set(reasons)
    assert values(v.Scope) == set(warning_property("scope")["enum"])  # type: ignore[arg-type]
    assert v.ACTIVE | v.FINAL == set(v.WarningState) and not v.ACTIVE & v.FINAL


def test_condition_kinds_steps_and_clear_by_come_from_the_rules_schema() -> None:
    rules = schema("config-warning-rules.schema.json")
    item = rules["properties"]["definitions"]["items"]["properties"]  # type: ignore[index]
    kinds = {branch["properties"]["kind"]["const"] for branch in item["condition"]["oneOf"]}
    assert values(v.ConditionKind) == kinds
    steps = {
        branch["properties"]["from"]["const"] for branch in item["content"]["properties"]["steps"]["oneOf"]
    }
    assert values(v.StepsFrom) == steps
    assert values(v.ClearBy) == set(item["clear"]["properties"]["by_engine"]["enum"])


def test_warning_actions_come_from_the_request_schema() -> None:
    action = schema("requests.schema.json")["$defs"]["WarningAction"]["properties"]["action"]["enum"]  # type: ignore[index]
    assert values(v.WarningAction) == set(action)


def test_spent_reasons_leave_the_reusable_ones_out() -> None:
    assert v.SPENT_CLEAR_REASONS == {
        v.ClearReason.CONDITION_FALSE,
        v.ClearReason.CLEAR_EVENT,
        v.ClearReason.EXPIRED,
    }
    assert v.ASSISTANT_LEVELS == {v.Level.INFO, v.Level.WATCH}


def test_the_ground_link_demo_source_and_sleep_overlay_are_contract_values() -> None:
    link = schema("ground-link.schema.json")
    assert v.LINK_SOURCE_DEMO_SWITCH in json.dumps(link)
    assert v.OVERLAY_SLEEP in v.OVERLAYS and v.OVERLAY_EVA in v.OVERLAYS
