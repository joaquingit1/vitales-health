"""Composition providers for guidance's consumer-owned fact ports (SYSTEM 8.2, 5.12) whose owning epics are not on
main: content facts (E10, R-E10-17), environment facts (E23) and stock facts (E32), plus plans' own stock reader.

Each answers only from what the snapshot's active configuration release and the mission state actually hold, with
their provenance, and fails closed (Unknown, or a raise the facade turns into Unknown) when that source is missing.

- Content: a plan or prepare step of the release's onboard-plans document (E28's validated parse), its content hash,
  its cited sources resolved against the hub's hash-verified source registry (the same identifiers loader check 2
  resolved before the release activated: sources.csv, NASA-SOURCES.md, DEMO-CONDITIONS.md and the documents' own
  source lists, config/adapters/verification.py), its review state and the document's `example`
  flag. The document's steps are patient instructions, so `patient_instruction` is the one purpose it serves; any
  other purpose is not allowed. A teach step is a skill of the release's skills catalog at its pinned version, cited
  sources resolved the same way, serving the `guided_check` purpose. Other content kinds (flows, passages) stay
  Unknown until E10.
- Environment: the mission's active environment (MissionStatePort, the demo switch on the demo hub) when it names a
  profile of the release's environment-profiles document; no mission facts is Unknown.
- Stock: the release's kit-map supplies and per environment stock (E32's packed stock), mapped as E28's own
  `packed_stock` maps them. A supply the kit map has no row for is left out, which the rule counts as missing.

A port another composer already bound (the owning epic, once on main) keeps its provider."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import cast

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.contracts_gen.enums import ConfigName
from crew_health.guidance.application.ports import (
    ContentFacts,
    ContentFactSet,
    ContentRef,
    EnvironmentFacts,
    Fact,
    Known,
    Requirements,
    StockFact,
    StockFacts,
    Unknown,
)
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot
from crew_health.plans.application.ports import StockLevel, StockReader
from crew_health.plans.application.release import (
    PlansCatalog,
    array_value,
    kit_supplies,
    object_value,
    packed_stock,
    read_document,
)
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.domain.model import Plan, PlansDocument, Step, all_steps
from crew_health.roles.application.ports import MissionStatePort

PATIENT_INSTRUCTION = "patient_instruction"
GUIDED_CHECK = "guided_check"
PLAN_KINDS = frozenset({"plan_step", "prepare_step"})


def _kind(step: Step) -> str:
    # The same mapping plans' StepGate uses for its requests (plans/application/eligibility.py).
    return "prepare_step" if step.stage_id == "return" else "plan_step"


def _requirements(step: Step) -> Requirements:
    return Requirements(
        instruments=frozenset(step.needs.instruments),
        supplies={need.supply_id: need.per_use for need in step.needs.supplies},
        skills=frozenset(step.needs.skills),
        operator=step.who.kind,
        operator_skill_id=step.who.skill_id,
    )


@dataclass(frozen=True, slots=True)
class PlanContentFacts:
    """guidance ContentFacts for plan and prepare steps, from the pinned onboard-plans document."""

    catalog: PlansCatalog
    sources: frozenset[str]

    def content(
        self, snapshot: ReadSnapshot, content: ContentRef, *, purpose: str, release_id: str
    ) -> ContentFactSet:
        if content.kind == "teach_step":
            return self._teach(snapshot, content, purpose=purpose, release_id=release_id)
        if content.kind not in PLAN_KINDS:
            raise LookupError("no content provider for " + content.kind)
        document = self.catalog.document(snapshot.release)
        if document is None:
            raise LookupError("onboard plans unavailable")
        found = _find(document, content.id)
        if found is None:
            raise LookupError("plan step not in the release")
        plan, step = found
        if _kind(step) != content.kind:
            raise LookupError("plan step kind mismatch")
        sources = self.sources
        substitute = None if step.substitute_step_id is None else plan.step(step.substitute_step_id)
        environments = step.environments if step.environments is not None else plan.environments
        return ContentFactSet(
            source_and_hash_valid=Known(
                step.content_sha256 == content.content_sha256 and all(s in sources for s in step.source_ids)
            ),
            purpose_allowed=Known(purpose == PATIENT_INSTRUCTION),
            clinical_review="clinically_reviewed"
            if step.review_state == "reviewed"
            else "not_clinically_reviewed",
            # Present in the active, validated release: a withdrawn step is one a later release leaves out.
            withdrawn=Known(False),
            simulation_only=Known(document.example),
            pinned_release_matches=release_id == snapshot.release.release_id,
            environments=Known(frozenset(environments)),
            substitute=None
            if substitute is None
            else ContentRef(_kind(substitute), substitute.step_id, substitute.content_sha256),  # type: ignore[arg-type]
            substitute_requirements=None if substitute is None else _requirements(substitute),
        )

    def _teach(
        self, snapshot: ReadSnapshot, content: ContentRef, *, purpose: str, release_id: str
    ) -> ContentFactSet:
        """A teach step is a skill of the release's skills catalog, pinned by the catalog's version. The catalog
        names no environment restriction for a skill, so it applies in every environment profile of the release;
        teach itself refuses an environment the skill has no steps for."""
        catalog = read_document(snapshot.release, ConfigName.SKILLS_CATALOG)
        profiles = read_document(snapshot.release, ConfigName.ENVIRONMENT_PROFILES)
        if catalog is None or profiles is None:
            raise LookupError("skills catalog or environment profiles unavailable")
        skills = [
            object_value(v)
            for v in array_value(catalog.get("skills", []))
            if object_value(v).get("skill_id") == content.id
        ]
        if len(skills) != 1:
            raise LookupError("skill not in the release")
        skill = skills[0]
        cited = [str(v) for v in array_value(skill.get("source_ids", []))]
        pinned = snapshot.release.version_of(ConfigName.SKILLS_CATALOG)
        return ContentFactSet(
            source_and_hash_valid=Known(
                pinned == content.content_sha256 and bool(cited) and all(s in self.sources for s in cited)
            ),
            purpose_allowed=Known(purpose == GUIDED_CHECK),
            clinical_review="clinically_reviewed"
            if skill.get("review_state") == "reviewed"
            else "not_clinically_reviewed",
            withdrawn=Known(False),
            simulation_only=Known(catalog.get("example") is not False),
            pinned_release_matches=release_id == snapshot.release.release_id,
            environments=Known(
                frozenset(
                    str(object_value(v).get("environment_id"))
                    for v in array_value(profiles.get("profiles", []))
                )
            ),
            substitute=None,
            substitute_requirements=None,
        )


def _find(document: PlansDocument, step_id: str) -> tuple[Plan, Step] | None:
    matches = [(plan, step) for plan in document.plans for step in all_steps(plan) if step.step_id == step_id]
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class MissionEnvironmentFacts:
    """guidance EnvironmentFacts from the mission's active environment, checked against the release's profiles."""

    mission: MissionStatePort

    def active(self, snapshot: ReadSnapshot) -> str | None:
        facts = self.mission.current(snapshot)
        if facts is None:
            return None
        profiles = read_document(snapshot.release, ConfigName.ENVIRONMENT_PROFILES)
        known = (
            ()
            if profiles is None
            else (object_value(v).get("environment_id") for v in array_value(profiles.get("profiles", [])))
        )
        return facts.active_environment_id if facts.active_environment_id in known else None

    def applicable(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, environments: Collection[str]
    ) -> Fact[bool]:
        environment_id = self.active(snapshot)
        if environment_id is None:
            return Unknown("environment", "no_active_environment")
        return Known(environment_id in environments)


@dataclass(frozen=True, slots=True)
class KitStockFacts:
    """guidance StockFacts from the release's kit-map stock for the active environment."""

    environment: MissionEnvironmentFacts

    def stock(
        self,
        snapshot: ReadSnapshot,
        patient_location_id: str | None,
        supplies: Mapping[str, Quantity | None],
    ) -> Mapping[str, Fact[StockFact]]:
        environment_id = self.environment.active(snapshot)
        if environment_id is None:
            return {key: Unknown("stock", "no_active_environment") for key in sorted(supplies)}
        info = kit_supplies(snapshot.release)
        here = packed_stock(snapshot.release).get(environment_id, {})
        answer: dict[str, Fact[StockFact]] = {}
        for key, per_use in sorted(supplies.items()):
            if key not in info:
                continue
            level = here.get(key)
            answer[key] = Known(
                StockFact(
                    aboard=level is not None,
                    quantity_now=None if level is None else level.quantity_now,
                    per_use=per_use,
                    consumable=info[key].consumable,
                )
            )
        return answer


@dataclass(frozen=True, slots=True)
class KitStockReader:
    """plans StockReader: the kit-map stock of one environment, every kit-map supply with whether it is aboard."""

    snapshots: object

    def stock(self, environment_id: str) -> Mapping[str, StockLevel]:
        with self.snapshots.snapshot() as view:  # type: ignore[attr-defined]
            info = kit_supplies(view.release)
            here = packed_stock(view.release).get(environment_id, {})
        return {
            key: StockLevel(
                key,
                key in here,
                None if key not in here else here[key].quantity_now,
                info[key].unit,
            )
            for key in sorted(info)
        }


def compose(registry: ModuleRegistry) -> None:
    """Call from assembly right after `configure`: a port another composer bound keeps its provider, and a missing
    source (no mission state port) leaves guidance with its typed Unknown."""
    from crew_health.kernel.uow import ReadSnapshotFactory

    providers: Mapping[type[object], object] = getattr(registry, "providers", {})
    settings, mission = cast(type[object], HubSettings), cast(type[object], MissionStatePort)
    snapshots = cast(type[object], ReadSnapshotFactory)

    def content(resolver: Resolver) -> PlanContentFacts:
        return PlanContentFacts(
            PlansCatalog(PlansSettings.read(resolver.require(HubSettings))),  # type: ignore[type-abstract]
            resolver.require(ConfigArtifacts).sources.source_ids,
        )

    def environment(resolver: Resolver) -> MissionEnvironmentFacts:
        return MissionEnvironmentFacts(resolver.require(MissionStatePort))  # type: ignore[type-abstract]

    def stock(resolver: Resolver) -> KitStockFacts:
        return KitStockFacts(environment(resolver))

    def reader(resolver: Resolver) -> KitStockReader:
        return KitStockReader(resolver.require(ReadSnapshotFactory))  # type: ignore[type-abstract]

    entries = (
        (cast(type[object], ContentFacts), content, (settings, ConfigArtifacts)),
        (cast(type[object], EnvironmentFacts), environment, (mission,)),
        (cast(type[object], StockFacts), stock, (mission,)),
        (cast(type[object], StockReader), reader, (snapshots,)),
    )
    for port, factory, requires in entries:
        if port not in providers and all(dependency in providers for dependency in requires):
            registry.provide(port, factory, requires=requires)
