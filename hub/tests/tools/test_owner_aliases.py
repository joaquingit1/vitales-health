"""E12 edits outside its own package, pinned to their owners' definitions (docs/build/E05 and E01
BUILT-BY-OTHERS.md). A consumer-facing copy that drifts from the owner's definition fails here."""

from __future__ import annotations

from typing import get_args

import pytest

from crew_health.config.application import loader
from crew_health.config.application import ports as config_ports
from crew_health.measurement.application import ports as measurement_ports
from crew_health.measurement.domain import model


@pytest.mark.parametrize(
    "name", ["OriginMode", "OperatorRequirement", "GapReason", "CapabilityStatus", "UnavailableReason"]
)
def test_measurement_port_vocabularies_equal_the_domain_definitions(name: str) -> None:
    published = getattr(measurement_ports, name).__value__
    owned = getattr(model, name).__value__
    assert set(get_args(published)) == set(get_args(owned))


def test_the_config_port_validation_result_names_the_loaders_result_structurally() -> None:
    members = {"errors", "warnings", "executed"}
    assert members <= set(dir(config_ports.ValidationResult))
    assert members <= set(loader.ValidationResult.__dataclass_fields__)
