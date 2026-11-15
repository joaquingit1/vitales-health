"""FindingPolicies and the extraction vocabulary, both read from the pinned configuration release (SYSTEM 5.6).

Accepted units per indicator are the UCUM units of unit-mapping.json (units travel as UCUM, AGENTS 1.8); labels
come from indicator-catalog.json. A printed wire unit (for example bpm) is recognised in a report only where the
mapping is the identity, and is then proposed as its UCUM unit; for any other mapping no unit is proposed.
A code with no mapping has no policy, so no draft for it can be confirmed (fail closed). Textual finding policies
have no configuration home yet (PLAN P3): none are served, so a textual draft cannot be confirmed here."""

from __future__ import annotations

from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName
from crew_health.contracts_gen.schemas.config_indicator_catalog_schema import IndicatorCatalogJson
from crew_health.contracts_gen.schemas.config_unit_mapping_schema import UnitMappingJson
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.reports.domain.findings import FindingPolicy


def _config_invalid() -> ProblemError:
    return ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)


def _mapping(release: ReleaseHandle) -> UnitMappingJson:
    try:
        return UnitMappingJson.model_validate_json(release.document(ConfigName.UNIT_MAPPING).raw)
    except (KeyError, ValidationError) as error:
        raise _config_invalid() from error


class ConfigFindingPolicies:
    def for_code(self, release: ReleaseHandle, code: str) -> FindingPolicy | None:
        entries = [m for m in _mapping(release).mappings if m.indicator_code == code]
        if not entries:
            return None
        units = frozenset(m.ucum_unit for m in entries)
        ref = entries[0].source_refs[0]
        return FindingPolicy(code, "numeric", units, f"{ref.path}:{ref.line}")


class ConfigVocabulary:
    """label (lower case) to {code, accepted units} for the contained parser, built from one release."""

    def of(self, release: ReleaseHandle) -> dict[str, dict[str, object]]:
        try:
            catalog = IndicatorCatalogJson.model_validate_json(
                release.document(ConfigName.INDICATOR_CATALOG).raw
            )
        except (KeyError, ValidationError) as error:
            raise _config_invalid() from error
        policies = ConfigFindingPolicies()
        vocabulary: dict[str, dict[str, object]] = {}
        for indicator in catalog.indicators:
            code = indicator.code.root
            policy = policies.for_code(release, code)
            if policy is None:
                continue
            vocabulary[indicator.label.lower()] = {"code": code, "units": _tokens(release, code)}
        return vocabulary


def _tokens(release: ReleaseHandle, code: str) -> dict[str, str]:
    """Printed unit token to the UCUM unit it proposes: the UCUM unit itself, and the wire unit when the mapping
    leaves the value unchanged (identity)."""
    tokens: dict[str, str] = {}
    for m in _mapping(release).mappings:
        if m.indicator_code != code:
            continue
        tokens[m.ucum_unit] = m.ucum_unit
        if m.conversion == "identity":
            tokens[m.wire_unit] = m.ucum_unit
    return tokens
