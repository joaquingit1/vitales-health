"""Validate kitmap's exported facts as the immutable kit check stored by measurement."""

from crew_health.kitmap.application.ports import KitCheckFacts
from crew_health.measurement.domain.model import KitCheck


def kit_check(value: KitCheckFacts) -> KitCheck:
    return KitCheck(
        can=value.can,
        instrument_ids=value.instrument_ids,
        missing_instrument_ids=value.missing_instrument_ids,
        condition_ids=value.condition_ids,
        cliff_urls=value.cliff_urls,
        text=value.text,
        kit_map_version=value.kit_map_version,
        environment_id=value.environment_id,
    )
