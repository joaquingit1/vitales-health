"""E27 4.3 read use cases: context capture, getKitMap, the kit gate, capabilities, kit queries, EquipmentFacts.

Every read uses one context (snapshot, release, environment, instant) and writes nothing (R44, KM-I7, KM-I8).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

from crew_health.guidance.application.ports import Fact, Known, Unknown
from crew_health.kernel.config import ConfigBinding
from crew_health.kernel.errors import FieldError
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.kitmap.application import mapping
from crew_health.kitmap.application.errors import InvalidEnvironment, KitGateInvalid, KitMapUnavailable
from crew_health.kitmap.application.ports import (
    GateOutcome,
    GateRow,
    Instrument,
    InstrumentReadinessPort,
    KitCapability,
    KitCheckFacts,
    KitMapContext,
    KitMapRelease,
    KitMapReleaseSource,
    MissionStatePort,
    PlanColumns,
    PlanColumnsPort,
    Snap,
)
from crew_health.kitmap.domain.model import (
    ConditionEvaluation,
    KitMapDoc,
    RegionSummary,
    StateCounts,
    UnknownCapabilityCode,
    UnknownConditionId,
)
from crew_health.kitmap.domain.model import (
    Instrument as DomainInstrument,
)
from crew_health.kitmap.domain.rules import (
    count_states,
    derive_capabilities,
    evaluate_kit,
    kit_gate,
    summarize_regions,
)

# CONTRACTS 21 joiners: "and" for a missing set, ", " for providers aboard, "or" for missing providers.
AND_JOINER = " and "
CAN_JOINER = ", "
OR_JOINER = " or "
# TemplateRenderer field keys are the template's document/pointer path (SYSTEM 5.6, config/domain/templates.py).
CANNOT_FIELD = "ui-copy/kit_check_templates/cannot"
CAN_FIELD = "ui-copy/kit_check_templates/can"
INSTRUMENTS_PLACEHOLDER = "instruments"


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Fixed vocabulary orders from the generated contract enums (common.schema)."""

    environments: tuple[str, ...]
    body_regions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ViewResult:
    ctx: KitMapContext[KitMapDoc]
    evaluations: tuple[ConditionEvaluation, ...]
    counts: StateCounts
    regions: tuple[RegionSummary, ...]
    missing_texts: tuple[str | None, ...]
    columns: tuple[PlanColumns, ...]


class KitMapReads:
    def __init__(
        self,
        *,
        snapshots: Callable[[], AbstractContextManager[ReadSnapshot]],
        releases: KitMapReleaseSource[KitMapDoc],
        config: ConfigBinding,
        vocabulary: Vocabulary,
        gate_max_condition_ids: int,
    ) -> None:
        self._snapshots = snapshots
        self._releases = releases
        self._config = config
        self.vocabulary = vocabulary
        self._gate_max = gate_max_condition_ids
        self.mission: MissionStatePort | None = None
        self.plans: PlanColumnsPort | None = None
        self.readiness: InstrumentReadinessPort | None = None

    # ---- context ----

    def context(self, view: Snap, environment_id: str | None) -> KitMapContext[KitMapDoc]:
        release = self._releases.for_release(view.release)
        if environment_id is None:
            active = self.mission.active_environment_id(view) if self.mission is not None else None
            if active is None:
                raise KitMapUnavailable("no active environment", retryable=False)
            environment_id = active
        if environment_id not in self.vocabulary.environments:
            raise InvalidEnvironment(environment_id)
        return KitMapContext[KitMapDoc](
            snapshot=view, release=release, environment_id=environment_id, at=view.now
        )

    def snapshot(self) -> AbstractContextManager[ReadSnapshot]:
        return self._snapshots()

    # ---- getKitMap ----

    def view(self, environment_id: str | None) -> ViewResult:
        with self._snapshots() as snap:
            ctx = self.context(snap, environment_id)
            return self.compute_view(ctx, ctx.release.doc.kits[ctx.environment_id])

    def compute_view(self, ctx: KitMapContext[KitMapDoc], kit: frozenset[str]) -> ViewResult:
        doc = ctx.release.doc
        evaluations = evaluate_kit(doc, kit)
        return ViewResult(
            ctx=ctx,
            evaluations=evaluations,
            counts=count_states(evaluations),
            regions=summarize_regions(evaluations, self.vocabulary.body_regions),
            missing_texts=tuple(self.missing_text(ctx, e.missing_instruments) for e in evaluations),
            columns=self.columns(ctx, kit, [e.row.plan_id for e in evaluations]),
        )

    def missing_text(self, ctx: KitMapContext[KitMapDoc], missing: Sequence[str]) -> str | None:
        release = ctx.release
        if not missing or release.templates is None:
            return None
        words = [_instrument(release, i).needs_text for i in missing]
        return self._config.bind(ctx.snapshot).render(
            CANNOT_FIELD, release.templates.cannot, {INSTRUMENTS_PLACEHOLDER: AND_JOINER.join(words)}
        )

    def columns(
        self, ctx: KitMapContext[KitMapDoc], kit: frozenset[str], plan_ids: Sequence[str | None]
    ) -> tuple[PlanColumns, ...]:
        if not ctx.release.has_onboard_plans:
            # No onboard-plans document in this release: no plan exists here, so none is aboard (not a default).
            return tuple(PlanColumns(plan_id, None, False) for plan_id in plan_ids)
        if self.plans is None:
            raise KitMapUnavailable("onboard plans present but no plan columns provider")
        columns = tuple(self.plans.row_columns(ctx, kit=kit, plan_ids=plan_ids))
        if len(columns) != len(plan_ids):
            raise KitMapUnavailable("plan columns provider answered a different row count")
        return columns

    # ---- kit gate (E05, inside its unit of work) ----

    def check(
        self, uow: UnitOfWork, *, capability_code: str, environment_id: str, condition_ids: Sequence[str]
    ) -> GateOutcome:
        if len(condition_ids) > self._gate_max:
            raise KitGateInvalid((FieldError("/condition_ids", "max_items", "too many condition IDs"),))
        ctx = self.context(uow, environment_id)
        doc = ctx.release.doc
        try:
            result = kit_gate(doc, capability_code, environment_id, condition_ids)
        except UnknownCapabilityCode:
            raise KitGateInvalid(
                (FieldError("/capability_code", "unknown", "unknown capability code"),)
            ) from None
        except UnknownConditionId as error:
            raise KitGateInvalid(
                (FieldError(f"/condition_ids/{error.index}", "unknown", "unknown condition ID"),)
            ) from None
        templates = ctx.release.templates
        if templates is None:
            raise KitMapUnavailable("ui-copy kit_check_templates missing from the release")
        template, field, joiner = (
            (templates.can, CAN_FIELD, CAN_JOINER)
            if result.can
            else (templates.cannot, CANNOT_FIELD, OR_JOINER)
        )
        text = self._config.bind(uow).render(
            field, template, {INSTRUMENTS_PLACEHOLDER: joiner.join(result.instrument_words)}
        )
        rows = {row.condition_id: row for row in doc.conditions}
        words = next(c for c in doc.capabilities if c.code == capability_code)
        return GateOutcome(
            kit_check=KitCheckFacts(
                can=result.can,
                instrument_ids=result.instrument_ids,
                missing_instrument_ids=result.missing_instrument_ids,
                condition_ids=result.condition_ids,
                cliff_urls=result.cliff_urls,
                text=text,
                kit_map_version=result.kit_map_version,
                environment_id=result.environment_id,
            ),
            capability=mapping.capability_words(words),
            rows=tuple(GateRow(c, rows[c].plan_id) for c in condition_ids),
        )

    # ---- capabilities (E05 serves them) and kit queries (E28, E32) ----

    def capabilities(self, snapshot: ReadSnapshot, environment_id: str) -> tuple[KitCapability, ...]:
        parsed = self._releases.for_release(snapshot.release)
        if environment_id not in self.vocabulary.environments:
            raise InvalidEnvironment(environment_id)
        return tuple(
            mapping.capability(value)
            for value in derive_capabilities(parsed.doc, environment_id, self.vocabulary.environments)
        )

    def kit_instruments(self, view: Snap, environment_id: str) -> frozenset[str]:
        return self.context(view, environment_id).release.doc.kits[environment_id]

    def instrument(self, view: Snap, instrument_id: str) -> Instrument | None:
        value = self._releases.for_release(view.release).doc.instrument(instrument_id)
        return None if value is None else mapping.instrument(value)

    # ---- EquipmentFacts for guidance (SYSTEM 8.2) ----

    def instruments_here(
        self, snapshot: ReadSnapshot, patient_location_id: str | None
    ) -> Fact[frozenset[str]]:
        if patient_location_id is not None:
            # SYSTEM 8.5 interim: one location per environment; a named location is not resolvable here yet.
            return Unknown("equipment", "location_not_resolvable")
        if self.mission is None:
            return Unknown("equipment", "no_mission_state")
        active = self.mission.active_environment_id(snapshot)
        if active is None or active not in self.vocabulary.environments:
            return Unknown("equipment", "no_active_environment")
        try:
            release = self._releases.for_release(snapshot.release)
        except KitMapUnavailable:
            return Unknown("equipment", "kit_map_unavailable")
        if self.readiness is None:
            # Packed is not usable now (E27 3.2, PLAN C3): without E05's readiness the answer is unknown.
            return Unknown("equipment", "readiness_unknown")
        return Known(release.doc.kits[active] & self.readiness.usable_now(snapshot, active))


def _instrument(release: KitMapRelease[KitMapDoc], instrument_id: str) -> DomainInstrument:
    found = release.doc.instrument(instrument_id)
    if found is None:
        raise KitMapUnavailable("instrument missing from a validated release")
    return found


def as_of(ctx: KitMapContext[KitMapDoc]) -> datetime:
    return ctx.at
