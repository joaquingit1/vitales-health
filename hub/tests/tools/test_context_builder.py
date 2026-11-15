from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from crew_health.kernel.clock import NormalizedTime, TimeInput
from crew_health.kernel.testing import MemorySettings
from crew_health.kernel.units import Quantity, Unit
from crew_health.tools.application.context_builder import ContextBuilder
from crew_health.tools.domain.context import ContextBudget, ContextSection
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.support import SupportItem
from crew_health.tools.settings import setting_specs
from tests.tools.fakes import identity
from tests.tools.test_bounds import settings_values
from tests.tools.test_gateway import Authority, View, scope


def observation() -> SupportItem:
    return SupportItem(
        "obs_a",
        "observation",
        "person_a",
        "LIVE",
        "accepted",
        "current",
        "manual_instrument",
        View.now,
        View.now,
        Quantity(Decimal(0), Unit("1")),
        None,
        None,
        None,
        None,
        code="test_indicator",
        source_revision=1,
        observed=TimeInput(View.now, "clock_a", "segment_a", 1, "hub_corrected", "LIVE"),
        normalized=NormalizedTime(View.now, View.now, "correction_a", 0, "known"),
        freshness_policy_ref="freshness_a",
        question_id="question_a",
        questionnaire_version="questions_a",
        question_text="configured question",
    )


class Projection:
    def __init__(self, sections: tuple[ContextSection, ...]) -> None:
        self.result = sections

    def sections(self, uow: object, scope: object, phase: object) -> tuple[ContextSection, ...]:
        return self.result


class Tokens:
    def count(self, prompt: bytes, *, model_release_id: str) -> int:
        return len(prompt)


class Prompt:
    def serialize(self, scope: object, phase: object, sections: bytes) -> bytes:
        return b"complete template wrapper:" + sections


@pytest.mark.trace("T-E12-020", "T-E12-022", "R16", "V23", "D11", "I3")
def test_failed_values_absent_zero_and_conflicts_retained() -> None:
    zero = observation()
    failed = replace(zero, ref_id="obs_failed", quality="rejected", exclusion_reason="signal")
    conflict = replace(zero, ref_id="obs_b", quantity=Quantity(Decimal(1), Unit("1")))
    settings = MemorySettings(setting_specs(), settings_values())
    section = ContextSection("observations", (zero, failed, conflict), True)
    builder = ContextBuilder(
        Projection((section,)), Tokens(), Prompt(), Authority(), settings, identity=identity()
    )
    result = builder.build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("observations",)))
    assert result.included_ids == ("obs_a", "obs_failed", "obs_b")
    assert b'"value":"0"' in result.sections
    assert b'"value":"1"' in result.sections
    assert b'"ref_id":"obs_failed"' in result.sections
    assert b'"quantity":null' in result.sections


@pytest.mark.trace("T-E12-037", "T-E12-043", "V28", "I4")
def test_required_overflow_refuses_inference_and_counts_wrappers() -> None:
    settings = MemorySettings(setting_specs(), settings_values())
    section = ContextSection("observations", (observation(),), True)
    builder = ContextBuilder(
        Projection((section,)), Tokens(), Prompt(), Authority(), settings, identity=identity()
    )
    with pytest.raises(ToolsInvalid, match="PAYLOAD_TOO_LARGE"):
        builder.build(View(), scope(), scope().phase, ContextBudget(10, 0, ("observations",)))


@pytest.mark.trace("T-E12-021", "T-E12-040", "R20", "V10", "V26", "I1")
@pytest.mark.parametrize("mutation", [{"person_id": "person_b"}, {"origin_mode": "SIM"}, {"kind": "journal"}])
def test_projection_cannot_widen_scope(mutation: dict[str, Any]) -> None:
    settings = MemorySettings(setting_specs(), settings_values())
    with pytest.raises(ToolsInvalid):
        section = ContextSection("observations", (replace(observation(), **mutation),), True)
        ContextBuilder(
            Projection((section,)), Tokens(), Prompt(), Authority(), settings, identity=identity()
        ).build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("observations",)))


@pytest.mark.trace("T-E12-034", "I7")
def test_missing_tokenizer_cannot_use_a_byte_estimate() -> None:
    settings = MemorySettings(setting_specs(), settings_values())
    section = ContextSection("observations", (observation(),), True)
    with pytest.raises(ToolsInvalid, match="CAPABILITY_UNAVAILABLE"):
        ContextBuilder(
            Projection((section,)), None, Prompt(), Authority(), settings, identity=identity()
        ).build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("observations",)))


@pytest.mark.trace("T-E12-043", "V28", "I4")
def test_provider_cannot_make_accepted_facts_optional_to_fit_budget() -> None:
    settings = MemorySettings(setting_specs(), settings_values())
    section = ContextSection("observations", (observation(),), False)
    builder = ContextBuilder(
        Projection((section,)), Tokens(), Prompt(), Authority(), settings, identity=identity()
    )
    with pytest.raises(ToolsInvalid, match="PAYLOAD_TOO_LARGE"):
        builder.build(View(), scope(), scope().phase, ContextBudget(100, 0, ("observations",)))


@pytest.mark.trace("T-E12-023", "T-E12-020", "I3", "I6")
def test_future_observation_cannot_be_current_even_if_provider_says_current() -> None:
    item = replace(observation(), corrected_at=View.now + timedelta(seconds=1))
    result = ContextBuilder(
        Projection((ContextSection("observations", (item,), True),)),
        Tokens(),
        Prompt(),
        Authority(),
        MemorySettings(setting_specs(), settings_values()),
        identity=identity(),
    ).build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("observations",)))
    assert b'"freshness":"future"' in result.sections
    assert b'"quantity":null' in result.sections
    assert b'"permitted_uses":[]' in result.sections


@pytest.mark.trace("T-E12-021", "T-E12-043", "V10", "I3", "I4")
def test_optional_place_metadata_can_be_omitted_with_explicit_reason() -> None:
    item = replace(observation(), kind="reading", person_id=None, origin_mode="REPLAY")
    builder = ContextBuilder(
        Projection((ContextSection("place_facts", (item,), False),)),
        Tokens(),
        Prompt(),
        Authority(),
        MemorySettings(setting_specs(), settings_values()),
        identity=identity(),
    )
    result = builder.build(View(), scope(), scope().phase, ContextBudget(100, 0, ("place_facts",)))
    assert result.omitted_sections == ("place_facts",)
    assert result.included_ids == ()
    complete = builder.build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("place_facts",)))
    assert b'"origin_mode":"REPLAY"' in complete.sections
    assert b'"permitted_uses":[]' in complete.sections


@pytest.mark.trace("T-E12-020", "T-E12-021", "I1", "I3")
@pytest.mark.parametrize(
    "mutation",
    [
        {"observed_at": None},
        {"corrected_at": None},
        {"kind": "answer", "presence": "unknown"},
        {"kind": "answer", "presence": "declined"},
    ],
)
def test_unmeasured_and_declined_inputs_remain_metadata(mutation: dict[str, Any]) -> None:
    item = replace(observation(), **mutation)
    builder = ContextBuilder(
        Projection((ContextSection("facts", (item,), True),)),
        Tokens(),
        Prompt(),
        Authority(),
        MemorySettings(setting_specs(), settings_values()),
        identity=identity(),
    )
    result = builder.build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("facts",)))
    assert b'"quantity":null' in result.sections
    assert b'"permitted_uses":[]' in result.sections


@pytest.mark.trace("T-E12-003", "T-E12-043", "I1", "I4")
@pytest.mark.parametrize(
    "fault", ["pin", "phase", "revoked", "duplicate", "names", "sections", "items", "personal_habitat"]
)
def test_context_refuses_inconsistent_scope_and_projection_bounds(fault: str) -> None:
    item = observation()
    sections: tuple[ContextSection, ...] = (ContextSection("facts", (item,), True),)
    authorization = Authority()
    target = scope()
    phase = target.phase
    if fault == "pin":
        target = replace(target, config_release_id="different")
    elif fault == "phase":
        from crew_health.tools.domain.phase import Phase

        phase = Phase.ASSESSMENT
    elif fault == "revoked":
        authorization.live = False
    elif fault == "duplicate":
        sections = sections * 2
    elif fault == "names":
        sections = (ContextSection("other", (item,), True),)
    elif fault == "sections":
        sections = sections * 17
    elif fault == "items":
        sections = (ContextSection("facts", (item,) * 201, True),)
    elif fault == "personal_habitat":
        sections = (ContextSection("facts", (replace(item, kind="reading", person_id=None),), True),)
    builder = ContextBuilder(
        Projection(sections),
        Tokens(),
        Prompt(),
        authorization,
        MemorySettings(setting_specs(), settings_values()),
        identity=identity(),
    )
    with pytest.raises(ToolsInvalid):
        builder.build(View(), target, phase, ContextBudget(32768, 0, ("facts",)))


@pytest.mark.trace("T-E12-034", "T-E12-037", "T-E12-040", "I4", "I7")
@pytest.mark.parametrize("fault", ["bytes", "bad_tokens", "revoked_after_read"])
def test_context_rejects_byte_overflow_bad_tokenizer_and_late_revocation(fault: str) -> None:
    settings_data = settings_values()
    authorization = Authority()
    item = replace(observation(), kind="reading", person_id=None)

    class ReadingProjection(Projection):
        def sections(self, uow: object, scope: object, phase: object) -> tuple[ContextSection, ...]:
            if fault == "revoked_after_read":
                authorization.live = False
            return self.result

    class Counting(Tokens):
        def count(self, prompt: bytes, *, model_release_id: str) -> int:
            return -1 if fault == "bad_tokens" and b'"ref_id"' in prompt else len(prompt)

    if fault == "bytes":
        settings_data["tools.context.max_bytes"] = 1
    builder = ContextBuilder(
        ReadingProjection((ContextSection("place_facts", (item,), False),)),
        Counting(),
        Prompt(),
        authorization,
        MemorySettings(setting_specs(), settings_data),
        identity=identity(),
    )
    with pytest.raises(ToolsInvalid):
        builder.build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("place_facts",)))


@pytest.mark.trace("T-E12-005", "T-E12-043", "I4")
@pytest.mark.parametrize(
    "budget", [(0, 0, ("facts",)), (1, -1, ("facts",)), (1, 0, ()), (1, 0, ("facts", "facts"))]
)
def test_invalid_context_budget_rejects_startup(budget: tuple[int, int, tuple[str, ...]]) -> None:
    with pytest.raises(ToolsInvalid):
        ContextBudget(*budget)


@pytest.mark.trace("T-E12-020", "T-E12-022", "R16", "I3", "I4")
def test_context_retains_indicator_question_and_conflict_identity() -> None:
    first = replace(observation(), code="first_indicator", source_revision=3, conflicting_refs=("obs_b",))
    second = replace(
        observation(), ref_id="obs_b", code="second_indicator", source_revision=4, conflicting_refs=("obs_a",)
    )
    answer = replace(
        observation(),
        kind="answer",
        ref_id="answer_a",
        quantity=None,
        question_id="question_a",
        question_text="configured question",
        value="choice_a",
        questionnaire_version="questions_a",
        presence="absent",
    )
    result = ContextBuilder(
        Projection((ContextSection("facts", (first, second, answer), True),)),
        Tokens(),
        Prompt(),
        Authority(),
        MemorySettings(setting_specs(), settings_values()),
        identity=identity(),
    ).build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("facts",)))
    import json

    rows = json.loads(result.sections)[0]["items"]
    assert rows[0]["code"] == "first_indicator"
    assert rows[1]["code"] == "second_indicator"
    assert rows[0]["conflicting_refs"] == ["obs_b"]
    assert rows[2]["question_id"] == "question_a"
    assert rows[2]["value"] == "choice_a"


@pytest.mark.trace("T-E12-043", "I4")
def test_optional_routine_explicit_negative_cannot_be_dropped() -> None:
    negative = replace(observation(), kind="routine_occurrence", presence="absent")
    with pytest.raises(ToolsInvalid, match="PAYLOAD_TOO_LARGE"):
        ContextBuilder(
            Projection((ContextSection("routine", (negative,), False),)),
            Tokens(),
            Prompt(),
            Authority(),
            MemorySettings(setting_specs(), settings_values()),
            identity=identity(),
        ).build(View(), scope(), scope().phase, ContextBudget(100, 0, ("routine",)))


@pytest.mark.trace("T-E12-020", "T-E12-023", "I3", "I6")
@pytest.mark.parametrize("fault", ["original", "origin", "correction", "uncertainty", "segment"])
def test_inconsistent_time_lineage_cannot_support_a_value(fault: str) -> None:
    item = observation()
    observed, normalized = item.observed, item.normalized
    assert observed is not None and normalized is not None
    if fault == "original":
        item = replace(item, normalized=replace(normalized, original_at=View.now - timedelta(seconds=1)))
    elif fault == "origin":
        item = replace(item, observed=replace(observed, origin_mode="REPLAY"))
    elif fault == "correction":
        item = replace(item, normalized=replace(normalized, correction_id=None))
    elif fault == "uncertainty":
        item = replace(item, normalized=replace(normalized, uncertainty_ms=-1))
    else:
        item = replace(item, observed=replace(observed, segment_id=""))
    result = item.for_context(person_id="person_a", origin_mode="LIVE", at=View.now)
    assert result.quantity is None
    assert result.freshness == "unknown"
    assert not result.permitted_uses(mission_origin="LIVE")


@pytest.mark.trace("T-E12-020", "T-E12-022", "I3", "I4")
def test_material_conflict_cannot_reference_an_omitted_fact() -> None:
    item = replace(observation(), conflicting_refs=("obs_missing",))
    builder = ContextBuilder(
        Projection((ContextSection("observations", (item,), True),)),
        Tokens(),
        Prompt(),
        Authority(),
        MemorySettings(setting_specs(), settings_values()),
        identity=identity(),
    )
    with pytest.raises(ToolsInvalid, match="MODEL_OUTPUT_REJECTED"):
        builder.build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("observations",)))
