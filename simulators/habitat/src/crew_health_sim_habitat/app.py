"""Load and validate every document the simulator needs, once, at startup."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from crew_health_sim_habitat.ble_ess import EssConfig, check_sources, load_ess_config
from crew_health_sim_habitat.devicecfg import DeviceKind, load_device_kinds
from crew_health_sim_habitat.errors import ConfigError
from crew_health_sim_habitat.scenariocfg import Scenario, load_scenarios
from crew_health_sim_habitat.wiring import (
    CatalogType,
    Channel,
    ReplayFeed,
    Wiring,
    load_catalog,
    load_registry,
    load_wiring,
    validate_wiring,
)
from crew_health_sim_habitat.world import A_WEIGHTING_DB

CONFIG_DIR = Path("simulators/habitat/config")


@dataclass(frozen=True, slots=True)
class Loaded:
    root: Path
    kinds: Mapping[str, DeviceKind]
    scenarios: Mapping[str, Scenario]
    wiring: Wiring
    catalog: Mapping[str, CatalogType]
    registry: Mapping[str, Channel]
    ess: EssConfig

    def scenario(self, scenario_id: str) -> Scenario:
        if scenario_id not in self.scenarios:
            raise ConfigError(f"unknown scenario {scenario_id!r}; known: {sorted(self.scenarios)}")
        return self.scenarios[scenario_id]

    def replay_feed(self, feed_id: str) -> ReplayFeed:
        for feed in self.wiring.replay_feeds:
            if feed.feed_id == feed_id:
                return feed
        raise ConfigError(f"unknown replay feed {feed_id!r}")


def load_all(root: Path, config_dir: Path | None = None, catalog_path: Path | None = None) -> Loaded:
    """Load the simulator documents under root; catalog_path overrides the wiring's catalog path."""
    directory = config_dir if config_dir is not None else root / CONFIG_DIR
    kinds = load_device_kinds(directory / "devices.json")
    scenarios = load_scenarios(directory / "scenarios.json")
    wiring = load_wiring(directory / "feeds.json")
    catalog = load_catalog(catalog_path if catalog_path is not None else root / wiring.catalog_path)
    registry = load_registry(root / wiring.registry_path)
    validate_wiring(wiring, kinds, catalog, registry)
    for scenario in scenarios.values():
        unknown = sorted(set(scenario.environment.noise_bands_db) - set(A_WEIGHTING_DB))
        if unknown:
            raise ConfigError(f"{scenario.scenario_id}: noise bands without an A-weighting: {unknown}")
        devices = {d.device_id for d in wiring.devices}
        missing = sorted({f.device_id for f in scenario.faults} - devices)
        if missing:
            raise ConfigError(f"{scenario.scenario_id}: faults name unknown devices {missing}")
        _check_fault_units(scenario, wiring, kinds)
    ess = load_ess_config(directory / "ble-ess.json")
    _check_ess(ess, scenarios, wiring, kinds)
    return Loaded(root, kinds, scenarios, wiring, catalog, registry, ess)


def _check_fault_units(scenario: Scenario, wiring: Wiring, kinds: Mapping[str, DeviceKind]) -> None:
    """An offset or drift fault must act on at least one output of its device, or it does nothing."""
    kind_of = {d.device_id: d.kind for d in wiring.devices}
    for fault in scenario.faults:
        if fault.unit is None:
            continue
        outputs = kinds[kind_of[fault.device_id]].outputs
        if not any(fault.acts_on(o.unit) for o in outputs):
            raise ConfigError(
                f"{scenario.scenario_id}: the {fault.fault} fault on {fault.device_id} is in {fault.unit!r}, "
                "which matches none of its outputs"
            )


def _check_ess(
    ess: EssConfig, scenarios: Mapping[str, Scenario], wiring: Wiring, kinds: Mapping[str, DeviceKind]
) -> None:
    units = {(d.device_id, o.name): o.unit for d in wiring.devices for o in kinds[d.kind].outputs}
    check_sources(ess, units)
    for scenario in ess.scenarios.values():
        if scenario.base_scenario not in scenarios:
            base = scenario.base_scenario
            raise ConfigError(f"ESS scenario {scenario.scenario_id}: unknown base scenario {base!r}")
