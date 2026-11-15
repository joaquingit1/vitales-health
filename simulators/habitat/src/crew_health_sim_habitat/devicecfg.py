"""Device kinds from config/devices.json."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from crew_health_sim_habitat.config import (
    JsonObject,
    load_json,
    obj,
    seconds,
    text,
    value,
)
from crew_health_sim_habitat.errors import ConfigError


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """One output register of a device, with its measurement chain."""

    name: str
    quantity: str
    unit: str
    resolution: float
    range_min: float
    range_max: float
    noise_abs: float
    noise_rel: float
    offset_drift_per_day: float
    sensitivity_loss_per_day: float


@dataclass(frozen=True, slots=True)
class DeviceKind:
    kind: str
    label: str
    interval_s: float
    warmup_s: float
    outputs: tuple[OutputSpec, ...]
    extras: Mapping[str, float]
    cross_sensitivity: Mapping[str, float]

    def output(self, name: str) -> OutputSpec:
        for spec in self.outputs:
            if spec.name == name:
                return spec
        raise ConfigError(f"device kind {self.kind!r} has no output {name!r}")

    def extra(self, key: str) -> float:
        if key not in self.extras:
            raise ConfigError(f"device kind {self.kind!r} needs the extra {key!r}")
        return self.extras[key]


def _output(name: str, raw: JsonObject) -> OutputSpec:
    unit = text(raw, "unit")
    spec = OutputSpec(
        name=name,
        quantity=text(raw, "quantity"),
        unit=unit,
        resolution=value(raw, "resolution", unit),
        range_min=value(raw, "range_min", unit),
        range_max=value(raw, "range_max", unit),
        noise_abs=value(raw, "noise_abs", unit),
        noise_rel=value(raw, "noise_rel", "1"),
        offset_drift_per_day=value(raw, "offset_drift_per_day", unit + "/d"),
        sensitivity_loss_per_day=value(raw, "sensitivity_loss_per_day", "1/d"),
    )
    if not spec.range_min < spec.range_max:
        raise ConfigError(f"output {name!r}: range_min must be below range_max")
    if min(spec.resolution, spec.noise_abs, spec.noise_rel, spec.sensitivity_loss_per_day) < 0:
        raise ConfigError(f"output {name!r}: resolution, noise and loss must not be negative")
    return spec


def _extras(raw: JsonObject) -> tuple[dict[str, float], dict[str, float]]:
    extras: dict[str, float] = {}
    cross: dict[str, float] = {}
    for key in sorted(raw):
        if key == "cross_sensitivity":
            table = obj(raw, key)
            for quantity in sorted(table):
                cross[quantity] = value(table, quantity, text(obj(table, quantity), "unit"))
        else:
            unit = text(obj(raw, key), "unit")
            extras[key] = seconds(raw, key) if unit in ("s", "min", "h") else value(raw, key, unit)
    return extras, cross


def load_device_kinds(path: Path) -> dict[str, DeviceKind]:
    doc = load_json(path)
    kinds: dict[str, DeviceKind] = {}
    table = obj(doc, "device_kinds")
    for kind in sorted(table):
        raw = obj(table, kind)
        outputs_raw = obj(raw, "outputs")
        outputs = tuple(_output(name, obj(outputs_raw, name)) for name in sorted(outputs_raw))
        if not outputs:
            raise ConfigError(f"device kind {kind!r} has no outputs")
        extras, cross = _extras(obj(raw, "extras"))
        interval = seconds(raw, "interval")
        if interval <= 0:
            raise ConfigError(f"device kind {kind!r}: interval must be positive")
        kinds[kind] = DeviceKind(
            kind, text(raw, "label"), interval, seconds(raw, "warmup"), outputs, extras, cross
        )
    return kinds
