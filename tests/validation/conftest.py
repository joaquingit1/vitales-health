"""Fixtures of tests/validation (B3): the test bundle holding the validation products, and
the session-end writer of tests/validation/report.json."""

from collections.abc import Iterator

import pytest

from pharmacy.config import ConfigBundle, load_bundle
from tests.scenarios.bundles import write_test_bundle
from tests.validation.products import declared_station_zone, v1_cases, v6_cases, validation_zones
from tests.validation.runner import REPORT


@pytest.fixture(scope="session")
def validation_bundle(tmp_path_factory: pytest.TempPathFactory) -> ConfigBundle:
    control = v1_cases("control", all_rows=True)
    zones = validation_zones(load_bundle())
    flight = v1_cases("flight", all_rows=True)
    v6 = v6_cases()
    products = {c.product["product_id"]: c.product for c in [*control, *v6]}
    lots = [c.lot for c in [*control, *flight, *v6]]
    return write_test_bundle(
        tmp_path_factory.mktemp("validation-bundle"),
        products=list(products.values()),
        extra_lots=lots,
        zones=zones,
        zone_overrides=[declared_station_zone()],
    )


@pytest.fixture(scope="session", autouse=True)
def _write_validation_report() -> Iterator[None]:
    yield
    if REPORT.datasets:
        REPORT.write()
