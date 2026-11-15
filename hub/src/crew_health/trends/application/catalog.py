"""Parse pinned indicator content without substituting missing configuration."""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from crew_health.contracts_gen.schemas.config_indicator_catalog_schema import Indicator, IndicatorCatalogJson
from crew_health.kernel.config import ConfigName, ReleaseHandle
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.trends.application.ports import Unavailable
from crew_health.trends.domain.model import Comparison


@dataclass(frozen=True, slots=True)
class Catalog:
    indicators: tuple[Indicator, ...]
    catalog_version: str

    def indicator(self, code: str) -> Indicator | None:
        return next((item for item in self.indicators if item.code.root == code), None)


def load_catalog(release: ReleaseHandle) -> Catalog | Unavailable:
    try:
        document = release.document(ConfigName.INDICATOR_CATALOG)
    except ProblemError as error:
        if error.code != ErrorCode.CONFIG_INVALID:
            raise
        return Unavailable("trends", "indicator_catalog_absent")
    try:
        parsed = IndicatorCatalogJson.model_validate_json(document.raw)
    except ValidationError as error:
        raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value) from error
    return Catalog(tuple(parsed.indicators), release.version_of(ConfigName.INDICATOR_CATALOG))


def comparison_of(indicator: Indicator) -> Comparison:
    """The indicator's within_usual band and provenance, as the catalog release states them."""
    within = indicator.baseline.within_usual
    to_be_set_by = None if within.to_be_set_by is None else within.to_be_set_by.root
    return Comparison(
        Decimal(within.band),
        within.basis.root,
        tuple(item.root for item in within.source_ids.root),
        tuple(item.root for item in within.nasa_checked.root),
        to_be_set_by,
    )
