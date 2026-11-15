"""SYSTEM 8.2 and 5.12: the bounded unknown-provider facade and its registration."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.guidance import module as guidance_module
from crew_health.guidance.application.facade import EligibilityFacade, FactProviders, GuidanceBounds
from crew_health.guidance.application.ports import (
    ContentFacts,
    ContentFactSet,
    ContentRef,
    EnvironmentFacts,
    EquipmentFacts,
    Fact,
    GuidanceEligibility,
    GuidanceRequest,
    Known,
    OperatorFact,
    Requirements,
    SkillFacts,
    StockFact,
    StockFacts,
)
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemoryRelease, MemorySettings, MemoryState, MemoryStorage, MemoryView
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot

pytestmark = [pytest.mark.trace("S8.2", "S5.12", "R07")]

STEP = ContentRef("plan_step", "plan_icl10.step_1", "a" * 64)
SUB = ContentRef("plan_step", "plan_icl10.step_1_alt", "b" * 64)
SKILLED = Requirements(frozenset(), {}, frozenset(), "skill", "pulse_check_wrist")
ECG = Requirements(frozenset({"ecg_single_lead"}), {}, frozenset(), "any_crew", None)
CREW = (
    OperatorFact("p_mara", 0, True, True, False, None, {}),
    OperatorFact("p_tomas", 1, True, True, False, None, {"pulse_check_wrist": "fresh"}),
)


def snapshot() -> ReadSnapshot:
    settings = MemorySettings([], {})
    storage = MemoryStorage(settings=settings, responses={}, results={}, readonly=True)
    return MemoryView(
        MemoryState(),
        connection=storage,
        release=MemoryRelease("rel_1", {}),
        now=datetime(2026, 11, 15, tzinfo=UTC),
    )


def request(requirements: Requirements = ECG, *, subject: str | None = "p_mara") -> GuidanceRequest:
    return GuidanceRequest(
        STEP, "patient_instruction", "rel_1", "LIVE", subject, None, requirements, False, None
    )


def good_content(
    *, substitute: ContentRef | None = None, requirements: Requirements | None = None
) -> ContentFactSet:
    return ContentFactSet(
        Known(True),
        Known(True),
        "not_clinically_reviewed",
        Known(False),
        Known(False),
        True,
        Known(frozenset({"lunar_surface"})),
        substitute,
        requirements,
    )


@dataclass
class FakeContent:
    sets: Mapping[ContentRef, ContentFactSet]
    calls: list[ContentRef] = field(default_factory=list)

    def content(
        self, snapshot: ReadSnapshot, content: ContentRef, *, purpose: str, release_id: str
    ) -> ContentFactSet:
        self.calls.append(content)
        return self.sets[content]


class FakeEnvironment:
    def applicable(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, environments: Collection[str]
    ) -> Fact[bool]:
        return Known("lunar_surface" in environments)


@dataclass
class FakeEquipment:
    here: frozenset[str]

    def instruments_here(
        self, snapshot: ReadSnapshot, patient_location_id: str | None
    ) -> Fact[frozenset[str]]:
        return Known(self.here)


class FakeSkills:
    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        return Known(CREW)


class FakeStock:
    def stock(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, supplies: Mapping[str, Quantity | None]
    ) -> Mapping[str, Fact[StockFact]]:
        return {key: Known(StockFact(True, None, None, False)) for key in supplies}


class Broken:
    def __getattr__(self, name: str) -> object:
        def fail(*args: object, **kwargs: object) -> object:
            raise RuntimeError("provider down")

        return fail


def facade(**providers: object) -> EligibilityFacade:
    built = EligibilityFacade(GuidanceBounds(max_requirement_items=8, max_operators=8))
    built.attach(FactProviders(**providers))  # type: ignore[arg-type]
    return built


def all_up(content: FakeContent, here: frozenset[str] = frozenset()) -> EligibilityFacade:
    return facade(
        content=content,
        environment=FakeEnvironment(),
        equipment=FakeEquipment(here),
        skills=FakeSkills(),
        stock=FakeStock(),
    )


def test_no_providers_fails_closed_naming_each_unknown() -> None:
    decision = facade().evaluate(snapshot(), request())
    assert decision.outcome == "not_supported" and not decision.executable
    assert decision.clinical_review == "not_clinically_reviewed"
    assert "source_and_hash_valid:content:no_provider" in decision.unknown
    assert "operators:skills:no_provider" in decision.unknown


def test_unattached_facade_fails_closed() -> None:
    unattached = EligibilityFacade(GuidanceBounds(max_requirement_items=8, max_operators=8))
    assert not unattached.evaluate(snapshot(), request()).executable


def test_only_content_known_still_never_executes() -> None:
    decision = facade(content=FakeContent({STEP: good_content()})).evaluate(snapshot(), request())
    assert not decision.executable and decision.outcome == "not_supported"
    assert decision.unknown == (
        "environment:environment:no_provider",
        "instruments:equipment:no_provider",
        "operators:skills:no_provider",
    )


def test_all_providers_known_and_aboard_executes() -> None:
    decision = all_up(FakeContent({STEP: good_content()}), frozenset({"ecg_single_lead"})).evaluate(
        snapshot(), request()
    )
    assert decision.executable and decision.who_can == ("p_mara", "p_tomas")


@pytest.mark.parametrize("broken", ["content", "environment", "equipment", "skills", "stock"])
def test_a_raising_provider_becomes_unknown_never_a_crash(broken: str) -> None:
    content = FakeContent({STEP: good_content()})
    providers: dict[str, object] = {
        "content": content,
        "environment": FakeEnvironment(),
        "equipment": FakeEquipment(frozenset({"ecg_single_lead"})),
        "skills": FakeSkills(),
        "stock": FakeStock(),
    }
    providers[broken] = Broken()
    supplies = Requirements(
        frozenset({"ecg_single_lead"}), {"eye_wash_set": None}, frozenset(), "any_crew", None
    )
    decision = facade(**providers).evaluate(snapshot(), request(supplies))
    assert not decision.executable
    assert any("provider_error" in entry for entry in decision.unknown)


def test_substitute_is_resolved_once_without_recursion() -> None:
    content = FakeContent(
        {
            STEP: good_content(substitute=SUB, requirements=SKILLED),
            SUB: good_content(substitute=STEP, requirements=ECG),
        }
    )
    decision = all_up(content).evaluate(snapshot(), request())
    assert decision.outcome == "show_with_substitute" and decision.availability == "substituted"
    assert decision.executable and decision.who_can == ("p_tomas",) and decision.substitute == SUB
    assert content.calls == [STEP, SUB]


def test_substitute_with_its_own_missing_instrument_stays_not_aboard() -> None:
    fundus = Requirements(frozenset({"fundoscope"}), {}, frozenset(), "any_crew", None)
    content = FakeContent({STEP: good_content(substitute=SUB, requirements=fundus), SUB: good_content()})
    decision = all_up(content).evaluate(snapshot(), request())
    assert decision.availability == "not_aboard" and not decision.executable and decision.substitute is None


def test_requirements_over_bound_fail_closed() -> None:
    many = Requirements(frozenset(f"i{n}" for n in range(9)), {}, frozenset(), "any_crew", None)
    decision = all_up(FakeContent({STEP: good_content()})).evaluate(snapshot(), request(many))
    assert (
        decision.outcome == "not_supported" and "source_and_hash_valid:content:over_bound" in decision.unknown
    )


def test_providers_attach_once() -> None:
    built = facade()
    with pytest.raises(ValueError):
        built.attach(FactProviders())


def test_module_registers_settings_and_binds_optional_providers() -> None:
    registry = DeclarationRegistry(max_ports=16, max_hooks=32)
    specs = guidance_module.setting_specs()
    settings = MemorySettings(specs, {spec.key: 8 for spec in specs})
    registry.provide(HubSettings, lambda r: settings)  # type: ignore[type-abstract]
    registry.provide(ContentFacts, lambda r: FakeContent({STEP: good_content()}))  # type: ignore[type-abstract]
    guidance_module.register(registry)
    resolver = registry.resolve()
    eligibility = resolver.require(GuidanceEligibility)  # type: ignore[type-abstract]
    decision = eligibility.evaluate(snapshot(), request())
    assert decision.outcome == "not_supported"
    assert "environment:environment:no_provider" in decision.unknown
    assert set(registry.declarations["settings"]) == {spec.key for spec in specs}
    for port in (EnvironmentFacts, EquipmentFacts, SkillFacts, StockFacts):
        assert resolver.optional(port) is None


def test_public_types_import_without_any_guidance_domain_module() -> None:
    """E26, E30 and E31 import these from crew_health.guidance.application; no guidance.domain module may load."""
    code = (
        "import sys\n"
        "from crew_health.guidance.application.ports import (Known, Unknown, Fact, OperatorFact, StockFact,"
        " SkillFacts, StockFacts, EquipmentFacts, EnvironmentFacts, ContentFacts, GuidanceEligibility, decide)\n"
        "from crew_health.guidance.application import model\n"
        "assert Known is model.Known and Unknown is model.Unknown\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('crew_health.guidance.domain'))\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
