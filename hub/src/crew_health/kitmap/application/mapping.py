"""Map pure domain results to the application values exported across module boundaries."""

from crew_health.kitmap.application import ports
from crew_health.kitmap.domain import model


def capability_words(value: model.CapabilityWords) -> ports.CapabilityWords:
    return ports.CapabilityWords(
        code=value.code,
        label=value.label,
        body_region=value.body_region,
        operator_requirement=value.operator_requirement,
        expected_duration_seconds=value.expected_duration_seconds,
        basis=value.basis,
        components=value.components,
    )


def capability(value: model.KitCapability) -> ports.KitCapability:
    return ports.KitCapability(
        code=value.code,
        label=value.label,
        body_region=value.body_region,
        operator_requirement=value.operator_requirement,
        expected_duration_seconds=value.expected_duration_seconds,
        methods=value.methods,
        instrument_ids=value.instrument_ids,
        missing_instrument_ids=value.missing_instrument_ids,
        applicable_environments=value.applicable_environments,
        kit_status=value.kit_status,
        kit_reason=value.kit_reason,
        components=value.components,
    )


def instrument(value: model.Instrument) -> ports.Instrument:
    return ports.Instrument(value.id, value.name, value.needs_text, value.capability_codes, value.method)
