"""Validate an optional indicator catalog before activating its release."""

from pydantic import ValidationError

from crew_health.contracts_gen.schemas.config_indicator_catalog_schema import IndicatorCatalogJson
from crew_health.kernel.config import CandidateRelease, ConfigName, Finding
from crew_health.kernel.errors import ProblemError
from crew_health.trends.application.catalog import comparison_of
from crew_health.trends.domain.baseline import validate_comparison


class IndicatorCatalogCheck:
    @property
    def check_id(self) -> str:
        return "trends.indicator_catalog"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        document = candidate.documents.get(ConfigName.INDICATOR_CATALOG)
        if document is None:
            return ()
        try:
            catalog = IndicatorCatalogJson.model_validate_json(document.raw)
        except ValidationError:
            return (Finding(self.check_id, ConfigName.INDICATOR_CATALOG, "", "indicator_catalog_invalid"),)
        findings: list[Finding] = []
        for index, indicator in enumerate(catalog.indicators):
            try:
                validate_comparison(comparison_of(indicator), indicator.decimals)
            except ProblemError:
                pointer = f"/indicators/{index}/baseline/within_usual/band"
                findings.append(
                    Finding(self.check_id, ConfigName.INDICATOR_CATALOG, pointer, "comparison_band_invalid")
                )
        return tuple(findings)
