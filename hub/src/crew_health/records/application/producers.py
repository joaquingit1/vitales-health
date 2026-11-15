"""The records provider of `ObservationCommands` (E30 teach, E05 measurement) and the fail-closed `AnswerCommands`.

Producers append in their caller's unit of work, so a rollback of the caller leaves nothing. Every append checks the
quantity against the canonical unit the pinned release maps the code to, stores it canonical, writes one immutable
row (its replication change comes with it), one `observation.recorded` audit entry and nothing else: CONTRACTS 18
has no observation event, so none is invented (SYSTEM 4.2).

Idempotency is the records row itself. The observation ID derives from the producer's key (teach's
idempotency_key; measurement's attempt, code and observation time), so a replay finds the stored row and returns
its ID without a second write. A replay whose draft differs from the stored row is DUPLICATE_COMMAND (SYSTEM 5.8).
No digest of the clinical draft is kept anywhere else.

Neither draft carries the capture clock segment or a trusted quality proof (R-E01-17 is pending), so the time is
recorded unknown (SYSTEM 5.2: a capture with no proven segment mapping is unknown) and the value is never stored
accepted: a declared or policy-assessed accepted value becomes uncertain with reason time_unknown.

Appending a record for another module through `AnswerCommands` still needs the consolidated answer interface of
R-E01-17; until it lands every answer append fails closed with CAPABILITY_UNAVAILABLE (AGENTS 1.9)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, DecimalException, localcontext
from typing import cast, get_args

from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_sensor_catalog_schema import SensorCatalogJson
from crew_health.contracts_gen.schemas.config_unit_mapping_schema import UnitMappingJson
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import NormalizedTime, TimeInput, require_utc
from crew_health.kernel.config import ConfigReleaseReader, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.units import ConvertedQuantity, Quantity, Unit, UnitRegistry, WireQuantity
from crew_health.kernel.uow import UnitOfWork
from crew_health.records.application.ports import AnswerDraft, AttemptObservationDraft, ObservationDraft
from crew_health.records.application.seams import MissionStatePort, ObservationPolicies, RecordsStore
from crew_health.records.domain.errors import RecordError
from crew_health.records.domain.lineage import check_observation_lineage
from crew_health.records.domain.observations import (
    AcquisitionMethod,
    ObservationRecord,
    OriginMode,
    QualityAssessment,
    assess_quality,
)

# Clock provenance of a producer capture: no segment mapping is proven, so normalization is unknown (SYSTEM 5.2).
UNMAPPED_CLOCK = "unmapped"
# Quality rule version of a value no policy assessed and no producer declared.
NO_POLICY = "none"
_METHODS: frozenset[str] = frozenset(get_args(AcquisitionMethod.__value__))
_ORIGINS: frozenset[str] = frozenset(get_args(OriginMode.__value__))


def _unavailable() -> ProblemError:
    return ProblemError(
        ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value, retryable=False
    )


def _document[M: BaseModel](release: ReleaseHandle, name: ConfigName, model: type[M]) -> M:
    try:
        return model.model_validate_json(release.document(name).raw)
    except (KeyError, ValidationError) as error:
        raise RecordError(ErrorCode.CONFIG_INVALID) from error


@dataclass(frozen=True, slots=True)
class CanonicalUnit:
    unit: Unit
    mapping_version: str
    decimal_places: int


def release_canonical_unit(release: ReleaseHandle, code: str, units: UnitRegistry) -> CanonicalUnit:
    """The UCUM unit the release's unit mapping gives `code`, with the mapping version and the rounding a
    converted value takes (D12: half even, the mapping's decimal places). A code with mappings to
    several units takes the one whose wire unit is the code's sensor-catalog unit. No mapping, or no single
    answer, is INVALID_UNIT (SYSTEM 5.4); no unit is ever defaulted."""
    mapping = _document(release, ConfigName.UNIT_MAPPING, UnitMappingJson)
    entries = [item for item in mapping.mappings if item.indicator_code == code]
    if len({item.ucum_unit for item in entries}) > 1:
        catalog = _document(release, ConfigName.SENSOR_CATALOG, SensorCatalogJson)
        sensors = [sensor.unit for sensor in catalog.sensor_types if sensor.id.root == code]
        entries = [item for item in entries if len(sensors) == 1 and item.wire_unit == sensors[0]]
    if len({(item.ucum_unit, item.rounding.decimal_places) for item in entries}) != 1:
        raise RecordError(ErrorCode.INVALID_UNIT)
    return CanonicalUnit(
        units.parse(entries[0].ucum_unit), mapping.version, entries[0].rounding.decimal_places
    )


@dataclass(frozen=True, slots=True)
class _Capture:
    """One producer draft in records' terms, before the release, mission and quality are applied."""

    observation_id: str
    patient_id: str
    operator_id: str
    episode_id: str | None
    code: str
    quantity: Quantity
    observed_at: datetime
    entered_at: datetime | None  # None: entered when the caller's unit of work appends it
    origin_mode: OriginMode
    method: AcquisitionMethod
    device_id: str | None
    context: tuple[tuple[str, str], ...]
    body_region: str | None
    supersedes: str | None
    measurement_request_id: str | None
    care_basis_ref: str
    proof_ref: str | None
    declared: QualityAssessment
    declared_version: str

    def content(self) -> tuple[object, ...]:
        return (
            self.patient_id,
            self.operator_id,
            self.episode_id,
            self.code,
            WireQuantity(self.quantity.value, self.quantity.unit.code),
            self.observed_at,
            self.entered_at,
            self.origin_mode,
            self.method,
            self.device_id,
            self.context,
            self.body_region,
            self.supersedes,
            self.measurement_request_id,
            self.care_basis_ref,
            self.proof_ref,
        )

    def stored(self, record: ObservationRecord) -> tuple[object, ...]:
        """The same draft fields read back from a stored row, for the replay comparison."""
        return (
            record.patient_id,
            record.operator_id,
            record.episode_id,
            record.code,
            record.conversion.original,
            record.observed.original_at,
            None if self.entered_at is None else record.entered_at,
            record.origin_mode,
            record.acquisition_method,
            record.device_id,
            record.context,
            record.body_region,
            record.supersedes,
            record.measurement_request_id,
            record.care_basis_ref,
            record.proof_ref,
        )


class ObservationProducer:
    """`ObservationCommands` for in-process producers. The caller is trusted to have authorized the capture
    (teach's guided check, measurement's assigned acquisition); records owns units, partition, idempotency,
    lineage, audit and storage."""

    def __init__(
        self,
        *,
        repository: RecordsStore,
        ids: IdFactory,
        units: UnitRegistry,
        releases: ConfigReleaseReader,
        mission: MissionStatePort | None = None,
        policies: ObservationPolicies | None = None,
    ) -> None:
        self.repository, self.ids, self.units, self.releases = repository, ids, units, releases
        self.mission, self.policies = mission, policies

    def bind(self, *, mission: MissionStatePort | None, policies: ObservationPolicies | None) -> None:
        """Late composition: the optional mission and policy providers, absent until their epics land."""
        self.mission, self.policies = mission, policies

    def canonical_unit(self, code: str) -> Unit:
        return release_canonical_unit(self.releases.active(), code, self.units).unit

    def append_observation(self, uow: UnitOfWork, draft: ObservationDraft, *, idempotency_key: str) -> str:
        require_id(idempotency_key)
        require_utc(draft.entered_at)
        if (
            draft.acquisition_method != "crewmate_palpation"
            or draft.quality_status != "accepted"
            or not draft.quality_rule_version
        ):
            raise RecordError(ErrorCode.INVALID_VALUE)
        capture = _Capture(
            self.ids.derived("obs", "observation", idempotency_key),
            draft.patient_id,
            draft.operator_id,
            draft.episode_id,
            draft.code,
            draft.value,
            draft.observed_at,
            draft.entered_at,
            draft.origin_mode,
            draft.acquisition_method,
            None,
            tuple(sorted(draft.context.items())),
            draft.body_region,
            None,
            None,
            idempotency_key,
            None,
            QualityAssessment(draft.quality_status, ()),
            draft.quality_rule_version,
        )
        return self._append(uow, capture)

    def append_for_attempt(self, uow: UnitOfWork, draft: AttemptObservationDraft) -> str:
        require_id(draft.attempt_id)
        require_id(draft.measurement_request_id)
        require_utc(draft.observed_at)
        if draft.operator_id is None:
            raise RecordError(ErrorCode.IDENTITY_MISMATCH)
        if draft.acquisition_method not in _METHODS:
            raise RecordError(ErrorCode.INVALID_VALUE)
        capture = _Capture(
            self.ids.derived("obs", "attempt", draft.attempt_id, draft.code, draft.observed_at.isoformat()),
            draft.patient_id,
            draft.operator_id,
            draft.episode_id,
            draft.code,
            draft.quantity,
            draft.observed_at,
            None,
            draft.origin_mode,
            cast(AcquisitionMethod, draft.acquisition_method),
            draft.device_id,
            tuple(sorted(draft.context.items())),
            draft.body_region,
            draft.supersedes,
            draft.measurement_request_id,
            draft.measurement_request_id,
            draft.attempt_id,
            QualityAssessment("not_assessed", ("no_policy",)),
            NO_POLICY,
        )
        return self._append(uow, capture)

    def _append(self, uow: UnitOfWork, capture: _Capture) -> str:
        _require_capture(capture)
        existing = self.repository.observation(uow, capture.observation_id)
        if existing is not None:
            if capture.stored(existing) != capture.content():
                raise RecordError(ErrorCode.DUPLICATE_COMMAND)
            return existing.observation_id
        mission_id = self._mission(uow)
        canonical = release_canonical_unit(uow.release, capture.code, self.units)
        conversion = self._convert(capture.quantity, canonical)
        quality, rule_version = self._quality(uow.release, capture, conversion.canonical)
        record = ObservationRecord(
            capture.observation_id,
            capture.patient_id,
            capture.operator_id,
            mission_id,
            capture.episode_id,
            capture.code,
            conversion.canonical,
            conversion,
            TimeInput(
                capture.observed_at, UNMAPPED_CLOCK, UNMAPPED_CLOCK, 0, "source_raw", capture.origin_mode
            ),
            NormalizedTime(capture.observed_at, None, None, None, "unknown"),
            capture.entered_at or uow.now,
            uow.now,
            capture.origin_mode,
            capture.method,
            quality.status,
            rule_version,
            quality.reasons,
            capture.device_id,
            capture.context,
            capture.body_region,
            capture.supersedes,
            uow.release.release_id,
            capture.proof_ref,
            capture.measurement_request_id,
            capture.care_basis_ref,
        )
        previous = None if record.supersedes is None else self.repository.observation(uow, record.supersedes)
        check_observation_lineage(record, previous)
        self.repository.add_observation(uow, record)
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                "observation.recorded",
                1,
                uow.actor,
                {"record": record.observation_id},
                uow.correlation_id,
                None,
                {},
            )
        )
        return record.observation_id

    def _mission(self, uow: UnitOfWork) -> str:
        facts = None if self.mission is None else self.mission.current(uow)
        if facts is None:
            raise _unavailable()
        return require_id(facts.mission_id)

    def _convert(self, quantity: Quantity, canonical: CanonicalUnit) -> ConvertedQuantity:
        """A value already in the canonical unit is kept exact; any other commensurable UCUM unit is converted
        and rounded once as the mapping says (SYSTEM 5.4), with the original kept."""
        original, version = WireQuantity(quantity.value, quantity.unit.code), canonical.mapping_version
        if quantity.unit == canonical.unit:
            return ConvertedQuantity(original, quantity, "identity", version, None, "none")
        if not self.units.commensurable(quantity.unit, canonical.unit):
            raise RecordError(ErrorCode.INVALID_UNIT)
        try:
            with localcontext() as context:
                context.prec = 64
                value = self.units.convert(quantity, canonical.unit).value.quantize(
                    Decimal(1).scaleb(-canonical.decimal_places), rounding=ROUND_HALF_EVEN
                )
        except DecimalException as error:
            raise RecordError(ErrorCode.INVALID_VALUE) from error
        rounding = "half_even:" + str(canonical.decimal_places)
        return ConvertedQuantity(original, Quantity(value, canonical.unit), "ucum", version, None, rounding)

    def _quality(
        self, release: ReleaseHandle, capture: _Capture, quantity: Quantity
    ) -> tuple[QualityAssessment, str]:
        """The policy's assessment where a policy provider exists, else what the producer declared. The time is
        unknown, so the history checks have no corrected history to run on and an accepted value is held
        uncertain, never stored accepted."""
        base, version = capture.declared, capture.declared_version
        if self.policies is not None:
            policy = self.policies.for_code(release, capture.code)
            policy.validate()
            if policy.code != capture.code or capture.method not in policy.methods:
                raise RecordError(ErrorCode.CONTEXT_INAPPLICABLE)
            if policy.canonical_unit != quantity.unit:
                raise RecordError(ErrorCode.INVALID_UNIT)
            base, version = assess_quality(policy, quantity, capture.observed_at, ()), policy.version
        status = "uncertain" if base.status == "accepted" else base.status
        return QualityAssessment(status, base.reasons + ("time_unknown",)), version


def _require_capture(capture: _Capture) -> None:
    for identifier in (capture.patient_id, capture.operator_id, capture.care_basis_ref):
        require_id(identifier)
    for optional in (capture.episode_id, capture.device_id, capture.supersedes, capture.proof_ref):
        if optional is not None:
            require_id(optional)
    require_utc(capture.observed_at)
    if capture.origin_mode not in _ORIGINS:
        raise RecordError(ErrorCode.INVALID_VALUE)


class PendingAnswerCommands:
    def append_answer(self, uow: UnitOfWork, draft: AnswerDraft, *, idempotency_key: str) -> str:
        raise _unavailable()
