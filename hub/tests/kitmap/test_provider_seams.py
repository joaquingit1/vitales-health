"""Real kit results pass through measurement's boundary without losing fields or validation."""

import json
from collections.abc import Callable
from dataclasses import asdict

import pytest

from crew_health.kitmap.application.ports import CapabilityWords, KitCapability, KitCheckFacts, KitMapQueries
from crew_health.measurement.application.mapping import kit_check
from crew_health.measurement.application.ports import KitCapabilitiesPort, KitGate

from .conftest import Hub, release, ui_copy_raw


@pytest.mark.trace("R07", "V78", "KM-I9", "SYSTEM-6.4")
def test_registered_kit_results_preserve_measurements_stored_kit_check(
    make_hub: Callable[..., Hub], config_raw: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()))
    document = json.loads(config_raw)
    gate = hub.resolver.require(KitGate)  # type: ignore[type-abstract]
    capabilities = hub.resolver.require(KitCapabilitiesPort)  # type: ignore[type-abstract]
    instruments = hub.resolver.require(KitMapQueries)  # type: ignore[type-abstract]
    seen: set[bool] = set()
    for environment_id in document["kits"]:
        with hub.snapshot() as view:
            packed = capabilities.capabilities(view, environment_id)
        assert packed and all(type(c) is KitCapability for c in packed)
        for capability in packed:
            configured_words = next(c for c in document["capabilities"] if c["code"] == capability.code)
            # Check against the source: two boundary outputs can share the same mapping defect.
            assert capability.label == configured_words["label"]
            outcome = gate.check(
                hub.as_uow(),
                capability_code=capability.code,
                environment_id=environment_id,
                condition_ids=(document["conditions"][0]["condition_id"],),
            )
            assert type(outcome.capability) is CapabilityWords
            # A panel lists its indicator codes; a single value has none (config-kit-map `components`).
            components = tuple(configured_words.get("components", ()))
            assert asdict(outcome.capability) == {**configured_words, "components": components}
            assert type(outcome.kit_check) is KitCheckFacts
            assert outcome.capability.label == capability.label
            stored = kit_check(outcome.kit_check)
            assert asdict(stored) == asdict(outcome.kit_check)
            assert outcome.rows[0].condition_id == stored.condition_ids[0]
            seen.add(stored.can)
    assert seen == {True, False}
    with hub.snapshot() as view:
        for configured in document["instruments"]:
            actual = instruments.instrument(view, configured["id"])
            assert actual is not None
            assert actual.name == configured["name"]
        assert instruments.instrument(view, "missing") is None
