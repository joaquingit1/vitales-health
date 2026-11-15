"""How the simulator is wired to the hub: devices, channel bindings, replay feeds and datasets.

Channel IDs, units and sensor types come from the hub's sensor catalog (E23 config/sensor-catalog.json)
and the channel registry entries (E23 design 5.3); nothing about a channel is hardcoded here.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from crew_health_sim_habitat.config import (
    JsonObject,
    load_json,
    obj,
    objs,
    opt_obj,
    opt_text,
    text,
    value,
)
from crew_health_sim_habitat.devicecfg import DeviceKind
from crew_health_sim_habitat.devices import DEVICE_STATUSES
from crew_health_sim_habitat.errors import ConfigError

# common.schema.json $defs/id and the contract's quality_status and origin_mode enums.
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
QUALITY_STATUSES = ("accepted", "rejected", "uncertain", "not_assessed")
ORIGIN_MODES = ("LIVE", "REPLAY", "SIM")
SIM_DEVICE_PREFIX = "sim_"
PRESSURE_CONVERSION = "ppm_to_mmhg"
# AdapterReadingBatch aggregation.kind; None sends no aggregation (the contract default, a sample).
AGGREGATIONS = (None, "sample", "mean", "time_weighted_mean", "energy_mean", "max")


@dataclass(frozen=True, slots=True)
class CatalogType:
    sensor_type_id: str
    unit: str
    conversions: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Channel:
    channel_id: str
    sensor_type_id: str
    input_unit: str
    adapter_id: str
    origin_mode: str
    environment_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeviceInstance:
    device_id: str
    kind: str
    location: str


@dataclass(frozen=True, slots=True)
class Binding:
    device_id: str
    output: str
    channel_id: str
    pressure_device: str | None
    pressure_output: str | None
    aggregation: str | None


@dataclass(frozen=True, slots=True)
class Dataset:
    dataset_id: str
    path: str
    provenance: str
    sha256: str
    columns: tuple[str, ...] | None  # names for a file with no header row, from its provenance file


@dataclass(frozen=True, slots=True)
class ReplayFeed:
    feed_id: str
    dataset: str
    time_column: str
    time_format: str
    value_column: str
    unit: str
    filter_column: str | None
    filter_equals: str | None
    channel_id: str | None
    dataset_label: str
    time_basis: str
    cabin_pressure_mmhg: float | None


@dataclass(frozen=True, slots=True)
class Adapter:
    adapter_id: str
    token_env: str


@dataclass(frozen=True, slots=True)
class Wiring:
    catalog_path: str
    registry_path: str
    adapters: Mapping[str, Adapter]
    hub_url: str
    quality_status: Mapping[str, str]
    devices: tuple[DeviceInstance, ...]
    bindings: tuple[Binding, ...]
    datasets: Mapping[str, Dataset]
    replay_feeds: tuple[ReplayFeed, ...]


def load_catalog(path: Path) -> dict[str, CatalogType]:
    doc = load_json(path)
    result: dict[str, CatalogType] = {}
    for raw in objs(doc, "sensor_types"):
        conversions = {text(u, "unit"): text(u, "conversion") for u in objs(raw, "accepted_units")}
        result[text(raw, "id")] = CatalogType(text(raw, "id"), text(raw, "unit"), conversions)
    return result


def load_registry(path: Path) -> dict[str, Channel]:
    doc = load_json(path)
    result: dict[str, Channel] = {}
    for raw in objs(doc, "channels"):
        envs = raw.get("environment_ids")
        if not isinstance(envs, list) or not envs or not all(isinstance(e, str) for e in envs):
            raise ConfigError("a channel needs a non-empty environment_ids list")
        channel = Channel(
            text(raw, "channel_id"),
            text(raw, "sensor_type_id"),
            text(raw, "input_unit"),
            text(raw, "adapter_id"),
            text(raw, "origin_mode"),
            tuple(str(e) for e in envs),
        )
        if channel.channel_id in result:
            raise ConfigError(f"duplicate channel {channel.channel_id!r}")
        result[channel.channel_id] = channel
    return result


def _binding(raw: JsonObject) -> Binding:
    pressure = opt_obj(raw, "cabin_pressure")
    return Binding(
        text(raw, "device_id"),
        text(raw, "output"),
        text(raw, "channel_id"),
        None if pressure is None else text(pressure, "device_id"),
        None if pressure is None else text(pressure, "output"),
        opt_text(raw, "aggregation"),
    )


def _replay_feed(raw: JsonObject) -> ReplayFeed:
    row_filter = opt_obj(raw, "row_filter")
    pressure = None if raw.get("cabin_pressure") is None else value(raw, "cabin_pressure", "mmHg")
    return ReplayFeed(
        feed_id=text(raw, "feed_id"),
        dataset=text(raw, "dataset"),
        time_column=text(raw, "time_column"),
        time_format=text(raw, "time_format"),
        value_column=text(raw, "value_column"),
        unit=text(raw, "unit"),
        filter_column=None if row_filter is None else text(row_filter, "column"),
        filter_equals=None if row_filter is None else text(row_filter, "equals"),
        channel_id=opt_text(raw, "channel_id"),
        dataset_label=text(raw, "dataset_label"),
        time_basis=text(raw, "time_basis"),
        cabin_pressure_mmhg=pressure,
    )


def _columns(raw: JsonObject) -> tuple[str, ...] | None:
    names = raw.get("columns")
    if names is None:
        return None
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
        raise ConfigError("dataset columns must be a nonempty list of names")
    if len(set(names)) != len(names):
        raise ConfigError("dataset columns must be unique")
    return tuple(names)


def load_wiring(path: Path) -> Wiring:
    doc = load_json(path)
    adapters_raw = obj(doc, "adapters")
    adapters = {
        m: Adapter(text(obj(adapters_raw, m), "adapter_id"), text(obj(adapters_raw, m), "token_env"))
        for m in sorted(adapters_raw)
    }
    quality_raw = obj(doc, "quality_status")
    datasets_raw = obj(doc, "datasets")
    datasets = {
        d: Dataset(
            d,
            text(obj(datasets_raw, d), "path"),
            text(obj(datasets_raw, d), "provenance"),
            text(obj(datasets_raw, d), "sha256"),
            _columns(obj(datasets_raw, d)),
        )
        for d in sorted(datasets_raw)
    }
    return Wiring(
        catalog_path=text(doc, "catalog_path"),
        registry_path=text(doc, "channel_registry_path"),
        adapters=adapters,
        hub_url=text(doc, "hub_url"),
        quality_status={k: text(quality_raw, k) for k in sorted(quality_raw)},
        devices=tuple(
            DeviceInstance(text(d, "device_id"), text(d, "kind"), text(d, "location"))
            for d in objs(doc, "devices")
        ),
        bindings=tuple(_binding(b) for b in objs(doc, "bindings")),
        datasets=datasets,
        replay_feeds=tuple(_replay_feed(f) for f in objs(doc, "replay_feeds")),
    )


def _check_channel(
    channel_id: str,
    unit: str,
    mode: str,
    wiring: Wiring,
    catalog: Mapping[str, CatalogType],
    registry: Mapping[str, Channel],
) -> str:
    """Check one channel use; returns the catalog conversion its input unit takes."""
    channel = registry.get(channel_id)
    if channel is None:
        raise ConfigError(f"channel {channel_id!r} is not in the channel registry")
    adapter = wiring.adapters.get(mode)
    if adapter is None or channel.adapter_id != adapter.adapter_id or channel.origin_mode != mode:
        raise ConfigError(f"channel {channel_id!r} must belong to the {mode} adapter with origin {mode}")
    sensor_type = catalog.get(channel.sensor_type_id)
    if sensor_type is None:
        raise ConfigError(
            f"channel {channel_id!r}: sensor type {channel.sensor_type_id!r} is not in the catalog"
        )
    conversion = sensor_type.conversions.get(channel.input_unit)
    if conversion is None:
        raise ConfigError(f"channel {channel_id!r}: {channel.input_unit!r} is not an accepted unit")
    if unit != channel.input_unit:
        raise ConfigError(f"channel {channel_id!r} takes {channel.input_unit!r}, the source gives {unit!r}")
    return conversion


def _check_devices(wiring: Wiring, kinds: Mapping[str, DeviceKind]) -> dict[str, DeviceKind]:
    by_id: dict[str, DeviceKind] = {}
    for device in wiring.devices:
        if not device.device_id.startswith(SIM_DEVICE_PREFIX) or not ID_PATTERN.match(device.device_id):
            raise ConfigError(
                f"device {device.device_id!r} must declare itself simulated ({SIM_DEVICE_PREFIX}...)"
            )
        if device.kind not in kinds or device.device_id in by_id:
            raise ConfigError(f"device {device.device_id!r}: unknown kind or duplicate id")
        by_id[device.device_id] = kinds[device.kind]
    return by_id


def validate_wiring(
    wiring: Wiring,
    kinds: Mapping[str, DeviceKind],
    catalog: Mapping[str, CatalogType],
    registry: Mapping[str, Channel],
) -> None:
    """Reject wiring that the hub would refuse, before any reading is produced."""
    for adapter in wiring.adapters.values():
        if not ID_PATTERN.match(adapter.adapter_id):
            raise ConfigError(f"adapter id {adapter.adapter_id!r} does not match the contract id pattern")
    if set(wiring.quality_status) != set(DEVICE_STATUSES) or not set(wiring.quality_status.values()) <= set(
        QUALITY_STATUSES
    ):
        raise ConfigError("quality_status must map every device status to a contract quality_status")
    devices = _check_devices(wiring, kinds)
    for binding in wiring.bindings:
        kind = devices.get(binding.device_id)
        if kind is None:
            raise ConfigError(f"binding names unknown device {binding.device_id!r}")
        unit = kind.output(binding.output).unit
        if binding.aggregation not in AGGREGATIONS:
            raise ConfigError(f"binding {binding.channel_id!r}: aggregation must be one of {AGGREGATIONS}")
        conversion = _check_channel(binding.channel_id, unit, "SIM", wiring, catalog, registry)
        if conversion == PRESSURE_CONVERSION:
            pressure_kind = devices.get(binding.pressure_device or "")
            if pressure_kind is None or pressure_kind.output(binding.pressure_output or "").unit != "kPa":
                raise ConfigError(f"{binding.channel_id!r} needs a measured cabin pressure in kPa")
    for feed in wiring.replay_feeds:
        if feed.dataset not in wiring.datasets:
            raise ConfigError(f"replay feed {feed.feed_id!r} names unknown dataset {feed.dataset!r}")
        if feed.channel_id is None:
            continue
        conversion = _check_channel(feed.channel_id, feed.unit, "REPLAY", wiring, catalog, registry)
        if conversion == PRESSURE_CONVERSION and feed.cabin_pressure_mmhg is None:
            raise ConfigError(f"replay feed {feed.feed_id!r} needs a cabin pressure for ppm")


def unmapped_outputs(wiring: Wiring, kinds: Mapping[str, DeviceKind]) -> list[tuple[str, str]]:
    """Device outputs with no hub channel: simulated, counted, never posted."""
    bound = {(b.device_id, b.output) for b in wiring.bindings}
    result: list[tuple[str, str]] = []
    for device in wiring.devices:
        for spec in kinds[device.kind].outputs:
            if (device.device_id, spec.name) not in bound:
                result.append((device.device_id, spec.name))
    return sorted(result)


def bindings_by_output(bindings: Sequence[Binding]) -> dict[tuple[str, str], Binding]:
    return {(b.device_id, b.output): b for b in bindings}
