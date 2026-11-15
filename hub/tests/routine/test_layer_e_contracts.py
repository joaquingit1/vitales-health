"""Architecture, consumer DTO and generated response contracts for layer E."""

import ast
import json
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, cast, get_type_hints

import pytest
import schemathesis
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from schemathesis import Case
from schemathesis.checks import response_schema_conformance
from schemathesis.core.adapter import OperationParameter, ResponsesContainer
from schemathesis.schemas import APIOperation, BaseSchema

from crew_health.contracts_gen.schemas.routine_occurrence_schema import RoutineOccurrence
from crew_health.contracts_gen.schemas.routine_override_schema import RoutineOverride
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.uow import LocalProjectionRegistration
from crew_health.routine.application import ports
from crew_health.routine.domain.errors import ConfigInvalid
from crew_health.routine.domain.loader_checks import Bounds, check_release
from crew_health.routine.domain.release import parse_release
from crew_health.routine.domain.vocabulary import OccurrenceState, OriginMode

from .harness import RoutineHarness, session, station
from .http_harness import HttpHarness, headers
from .support import REPO, document, release


@pytest.mark.trace("T-E25-012", "INV-06", "07", "D43")
def test_routine_imports_no_ground_assistant_model_tools_or_participation() -> None:
    forbidden = {"groundlink", "assistant", "model", "tools", "participation"}
    checked = 0
    for path in sorted((REPO / "hub/src/crew_health/routine").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in imports:
                assert not (set(name.split(".")) & forbidden), (path, name)
                checked += 1
    assert checked > 100


class GroundPort:
    def __init__(self, state: str) -> None:
        self.state = state


class GroundHarness(RoutineHarness):
    def __init__(self, directory: Path, state: str | None) -> None:
        self.ground = None if state is None else GroundPort(state)
        super().__init__(directory)

    def _providers(self) -> None:
        super()._providers()
        ground = self.ground
        if ground is not None:
            self.registry.provide(GroundPort, lambda _: ground)


@pytest.mark.trace("T-E25-013", "D34", "V81")
def test_registered_ground_state_never_changes_routine_response_bytes(tmp_path: Path) -> None:
    def reads(hub: RoutineHarness) -> bytes:
        return canonical_json(
            {
                "today": hub.runtime.schedule(session(), "today"),
                "week": hub.runtime.schedule(session(), "week"),
                "tasks": hub.runtime.tasks(session(), None, 100),
                "occurrences": hub.runtime.occurrences(session(), "today", None, None, 100),
                "overrides": hub.runtime.overrides(session(), None, 100),
            }
        )

    expected: bytes | None = None
    for state in (None, "up", "delayed", "down"):
        directory = tmp_path / (state or "absent")
        directory.mkdir()
        hub = GroundHarness(directory, state)
        if state is not None:
            assert hub.resolver.require(GroundPort).state == state
        actual = reads(hub)
        if expected is None:
            expected = actual
        assert actual == expected


@pytest.mark.trace("T-E25-039", "INV-09")
def test_local_routine_projections_use_generated_models_and_registered_rules(tmp_path: Path) -> None:
    hub = station(tmp_path)
    registrations = hub.registry.declarations["local_projections"]
    for kind, model, identifier in (
        ("occurrence", RoutineOccurrence, "occurrence_id"),
        ("override", RoutineOverride, "override_id"),
    ):
        from crew_health.kernel.uow import AudienceProjectionKey

        key = AudienceProjectionKey("routine", "routine." + kind + "_changed", 1)
        registration = registrations[key]
        assert isinstance(registration, LocalProjectionRegistration)
        assert json.loads(registration.payload_schema_json) == model.model_json_schema()
        assert {"/" + identifier, "/revision", "/person_id", "/state"} <= set(
            registration.required_field_pointers
        )
        assert registration.rule is hub.registry.declarations["audiences"]["routine." + kind]


@pytest.mark.trace("T-E25-049", "E03 seams")
def test_provided_and_consumed_dtos_match_design_field_names_and_types() -> None:
    # Field expectations transcribed from E25 4.2/4.3, E03's seam table,
    # E23 4.2, E02's roster seam and E31's DrillOccurrence declaration.
    type Dto = (
        ports.AddedQuestion
        | ports.OccurrenceRef
        | ports.TodayRoutinePart
        | ports.DrillOccurrence
        | ports.MissionFacts
        | ports.RosterMember
        | ports.DailyCheckSubmitted
        | ports.DailyCheckAbandoned
        | ports.ReactionTestRecorded
    )
    expected: dict[type[Dto], dict[str, object]] = {
        ports.AddedQuestion: {
            "question_id": str,
            "cause": Literal["overlay", "miss", "warning"],
            "repeat_after_minutes": int | None,
        },
        ports.OccurrenceRef: {
            "occurrence_id": str,
            "item_id": str,
            "state": OccurrenceState,
            "due_from": datetime | None,
            "due_until": datetime | None,
        },
        ports.TodayRoutinePart: {
            "due": int,
            "later": int,
            "done": int,
            "open_today": int,
            "next_occurrence_ids": tuple[str, ...],
        },
        ports.DrillOccurrence: {
            "occurrence_id": str,
            "person_id": str,
            "item_id": str,
            "skill_ids": tuple[str, ...],
            "state": Literal["due", "later", "done", "missed", "skipped"],
        },
        ports.MissionFacts: {
            "mission_id": str,
            "mission_mode": OriginMode,
            "active_environment_id": str,
            "planned_length_days": int | None,
            "site_node_id": str,
        },
        ports.RosterMember: {
            "person_id": str,
            "display_name": str,
            "roster_index": int,
            "medical_officer_rank": int | None,
        },
        ports.DailyCheckSubmitted: {
            "daily_check_id": str,
            "person_id": str,
            "mission_day": int,
            "origin_mode": OriginMode,
            "submitted_at": datetime,
        },
        ports.DailyCheckAbandoned: {"daily_check_id": str, "person_id": str, "mission_day": int},
        ports.ReactionTestRecorded: {
            "reaction_test_id": str,
            "person_id": str,
            "state": str,
            "occurrence_id": str | None,
        },
    }
    for dto, declared in expected.items():
        assert {field.name: get_type_hints(dto)[field.name] for field in fields(dto)} == declared
    texts = {
        name: (REPO / f"docs/tdd/{name}.md").read_text(encoding="utf-8")
        for name in ("E02", "E03", "E23", "E25", "E31")
    }
    assert all(
        name in texts["E03"]
        for name in (
            "AddedQuestion",
            "OccurrenceRef",
            "TodayRoutinePart",
            "DailyCheckSubmitted",
            "DailyCheckAbandoned",
            "ReactionTestRecorded",
        )
    )
    assert "class MissionFacts:" in texts["E23"]
    assert "class DrillOccurrence:" in texts["E31"]
    assert "Sequence[RosterMember]" in texts["E02"]


malformed = st.one_of(st.none(), st.booleans(), st.integers(), st.text(), st.lists(st.integers(), max_size=3))


@pytest.mark.trace("T-E25-036", "CONTRACTS 11", "E25-03")
@settings(deadline=None)  # the first example loads and validates the release documents
@given(st.sampled_from(("on", "effect", "until", "binds")), malformed)
def test_malformed_binding_shapes_always_produce_typed_findings(part: str, value: object) -> None:
    rules = document("routine-rules")
    cast(list[dict[str, object]], rules["bindings"])[0][part] = value
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_malformed", document("routine-schedule"), rules, None, None)
    assert error.value.findings
    assert all(
        f.severity == "error" and f.pointer.startswith("/bindings/0/" + part) for f in error.value.findings
    )


@pytest.mark.trace("T-E25-036", "CONTRACTS 11", "E25-03")
@given(st.sampled_from(("trigger", "new_cadence", "source_ids", "basis")), malformed)
def test_malformed_trigger_shapes_always_produce_typed_findings(part: str, value: object) -> None:
    schedule = document("routine-schedule")
    trigger = cast(
        list[dict[str, object]], cast(list[dict[str, object]], schedule["items"])[0]["raise_frequency_when"]
    )[0]
    # Every generated value is malformed for the selected field.
    trigger[part] = {"unexpected": value}
    with pytest.raises(ConfigInvalid) as error:
        parse_release("rel_malformed", schedule, document("routine-rules"), None, None)
    assert error.value.findings
    assert all(f.pointer.startswith("/items/0/raise_frequency_when/0/" + part) for f in error.value.findings)


@pytest.mark.trace("T-E25-036", "CONTRACTS 11", "E25-03")
def test_guidance_checks_cover_all_eight_rules_and_trigger_drift() -> None:
    parsed = release()
    bad = replace(
        parsed.items[0],
        plain="Two sentences. More words.",
        steps=("Take 5 mg.",),
        instrument="",
        minutes=-1,
        body_region="unknown",
        source_ids=("missing",),
        by_environment={},
    )
    findings = check_release(
        replace(parsed, items=(bad, *parsed.items[1:])), Bounds(100, 100, 100), None, None
    )
    pointers = {f.pointer for f in findings if f.severity == "error"}
    assert {
        "/items/0/plain",
        "/items/0/instrument",
        "/items/0/minutes",
        "/items/0/body_region",
        "/items/0/steps/0",
        "/items/0/source_ids/0",
        "/items/0/cadence/by_environment",
    } <= pointers
    blank = replace(parsed.items[0], steps=(" ",))
    assert any(
        f.pointer == "/items/0/steps"
        for f in check_release(replace(parsed, items=(blank,)), Bounds(100, 100, 100), None, None)
    )
    assert any("G8" in f.message and f.severity == "warning" for f in findings)
    assert any("Misses window" in f.message for f in findings)


@pytest.mark.trace("T-E25-046", "INV-08", "R-E25-20")
def test_environment_trigger_is_a_loader_error() -> None:
    from crew_health.routine.domain.release import EnvironmentOn

    parsed = release()
    binding = replace(parsed.bindings[0], on=EnvironmentOn(("station_leo",)))
    findings = check_release(replace(parsed, bindings=(binding,)), Bounds(100, 100, 100), None, None)
    assert any(f.severity == "error" and f.pointer == "/bindings/0/on" for f in findings)


@pytest.mark.slow
@pytest.mark.trace("T-E25-037", "CONTRACTS 5")
def test_schemathesis_response_conformance_for_all_ten_operations(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    schema = schemathesis.openapi.from_path(REPO / "contracts/api/openapi.yaml").include(tag="Routine")
    schema.app = hub.app
    seen: set[str] = set()
    for result in schema.get_all_operations():
        from schemathesis.core.result import Ok

        assert isinstance(result, Ok), result
        operation = result.ok()
        assert operation is not None
        strategy = operation.as_strategy(headers=headers(key="schema_generated", revision=1))

        @settings(
            max_examples=3, deadline=None, suppress_health_check=[HealthCheck.too_slow], derandomize=True
        )
        @given(strategy)
        def check(
            case: Case[APIOperation[OperationParameter, ResponsesContainer[object], object, BaseSchema]],
        ) -> None:
            response = case.call()
            case.validate_response(response, checks=[response_schema_conformance])
            seen.add(case.operation.label)

        check()
    assert len(seen) == 10
