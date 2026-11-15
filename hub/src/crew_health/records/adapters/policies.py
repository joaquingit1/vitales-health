"""Records' observation policy provider (E01 records `ObservationPolicies`, the docs/requests/E01.md R-E01-1
interim).

The criteria live in records/schemas/observation-policies.json, each with its basis and who sets it; this module
holds only the mechanism. The document is loaded and validated once at startup. Every policy is bound to the pinned
release: its canonical unit must be the release's unit mapping for the code, its accepted wire units are that
mapping's wire units (D12), and its version names the mapping version. A code without a row, or a release that maps
the code to another unit, has no policy (`PolicyMissing`), which records keeps as not assessed, never accepted."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_unit_mapping_schema import UnitMappingJson
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.units import UnitRegistry
from crew_health.records.domain.errors import PolicyMissing, RecordError
from crew_health.records.domain.observations import AcquisitionMethod, ObservationPolicy

DOCUMENT = "observation-policies.json"
MAX_DOCUMENT_BYTES = 256 * 1024


class _Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str | int
    basis: str = Field(min_length=1)
    to_be_set_by: str = Field(min_length=1)


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(pattern="^[a-z][a-z0-9_]{0,63}$")
    ucum_unit: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    methods: tuple[AcquisitionMethod, ...] = Field(min_length=1)
    minimum: _Criterion
    maximum: _Criterion
    saturation_minimum: _Criterion
    saturation_maximum: _Criterion
    stuck_count: _Criterion
    max_change_per_second: _Criterion


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(min_length=1)
    provenance: dict[str, str]
    policies: tuple[_Row, ...] = Field(max_length=200)


def _number(criterion: _Criterion) -> Decimal:
    try:
        value = Decimal(str(criterion.value))
    except InvalidOperation as error:
        raise RecordError(ErrorCode.CONFIG_INVALID) from error
    if not value.is_finite():
        raise RecordError(ErrorCode.CONFIG_INVALID)
    return value


@dataclass(frozen=True, slots=True)
class _Policy:
    row: _Row
    version: str

    def bind(
        self, units: UnitRegistry, wire_units: frozenset[str], mapping_version: str
    ) -> ObservationPolicy:
        row = self.row
        policy = ObservationPolicy(
            row.code,
            self.version + "+" + mapping_version,
            row.source_ref,
            units.parse(row.ucum_unit),
            wire_units,
            frozenset(row.methods),
            _number(row.minimum),
            _number(row.maximum),
            _number(row.saturation_minimum),
            _number(row.saturation_maximum),
            int(_number(row.stuck_count)),
            _number(row.max_change_per_second),
            True,
        )
        policy.validate()
        return policy


class ReleaseObservationPolicies:
    """`ObservationPolicies` over the records policy document, bound to the pinned release's unit mapping."""

    def __init__(self, units: UnitRegistry, raw: bytes) -> None:
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise RecordError(ErrorCode.CONFIG_INVALID)
        try:
            document = _Document.model_validate_json(raw)
        except ValidationError as error:
            raise RecordError(ErrorCode.CONFIG_INVALID) from error
        if len({row.code for row in document.policies}) != len(document.policies):
            raise RecordError(ErrorCode.CONFIG_INVALID)
        self.units = units
        self.policies = {row.code: _Policy(row, document.version) for row in document.policies}
        for row in document.policies:
            # Validate every row at startup against its own unit, so a bad document never starts.
            self.policies[row.code].bind(units, frozenset({row.ucum_unit}), "startup")

    @classmethod
    def packaged(cls, units: UnitRegistry) -> ReleaseObservationPolicies:
        raw = files("crew_health.records").joinpath("schemas", DOCUMENT).read_bytes()
        return cls(units, raw)

    def for_code(self, release: ReleaseHandle, code: str) -> ObservationPolicy:
        policy = self.policies.get(code)
        if policy is None:
            raise PolicyMissing()
        try:
            mapping = UnitMappingJson.model_validate_json(release.document(ConfigName.UNIT_MAPPING).raw)
        except (KeyError, ValidationError) as error:
            raise RecordError(ErrorCode.CONFIG_INVALID) from error
        entries = [item for item in mapping.mappings if item.indicator_code == code]
        if not entries or {item.ucum_unit for item in entries} != {policy.row.ucum_unit}:
            raise PolicyMissing()
        return policy.bind(self.units, frozenset(item.wire_unit for item in entries), mapping.version)
