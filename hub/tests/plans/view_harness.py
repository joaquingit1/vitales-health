"""View fixtures and D22 validation; no production content is changed."""

from __future__ import annotations

from dataclasses import replace

import jsonschema
from referencing import Registry, Resource

from crew_health.guidance.application.ports import Fact, Known, OperatorFact
from crew_health.kernel.uow import ReadSnapshot

from .plans_stack import ROOT, CrewFacts, PlansStack
from .test_plans_authoring import d22_schema


class GoldenCrew(CrewFacts):
    def __init__(self) -> None:
        super().__init__()
        self.members = tuple(
            replace(m, medical_officer_rank=None)
            if m.person_id in ("mara", "tomas")
            else replace(m, medical_officer_rank=i + 1)
            for i, m in enumerate(self.members)
        )

    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        return Known(
            tuple(
                OperatorFact(
                    m.person_id,
                    m.roster_index,
                    True,
                    True,
                    m.person_id not in ("mara", "tomas"),
                    m.medical_officer_rank,
                    self.skills if m.person_id == "tomas" else {},
                )
                for m in self.members
            )
        )


def install_golden_crew(s: PlansStack) -> None:
    crew = GoldenCrew()
    s.crew.members = crew.members
    # The guidance facade retains the original provider instance; bind its behavior via a fresh facade.
    from crew_health.guidance.application.facade import EligibilityFacade, FactProviders, GuidanceBounds

    from .plans_stack import EnvironmentFacts, GuidanceStock

    guidance = EligibilityFacade(GuidanceBounds(200, 32))
    guidance.attach(
        FactProviders(s.content, EnvironmentFacts(s.mission), s.equipment, crew, GuidanceStock(s.supplies))
    )
    s.rebind(roster=crew, skills=crew, guidance=guidance)


def validate_view(value: object) -> None:
    schemas = ROOT / "contracts/api/schemas"
    registry: Registry = Registry().with_resources(
        (p.name, Resource.from_contents(d22_schema(p))) for p in schemas.glob("*.schema.json")
    )
    jsonschema.Draft202012Validator(
        d22_schema(schemas / "plan-view.schema.json"), registry=registry
    ).validate(value)
