"""Finite deterministic device notification streams, independent of any adapter."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from . import gatt
from .ieee11073 import SFLOAT_NAN, WireValue
from .physiology import Physiology, quantize
from .scenarios import load_scenarios

type Family = Literal["oximeter", "cuff", "thermometer"]
MAX_EVENTS = 100_000


@dataclass(frozen=True)
class DeviceIdentity:
    name: str
    model: str
    serial: str
    manufacturer: str = field(default="crew-health simulator", init=False)
    simulated: bool = field(default=True, init=False)


@dataclass(frozen=True)
class Notification:
    characteristic: str
    uuid: str
    payload: bytes
    at: datetime


@dataclass(frozen=True)
class Disconnect:
    reason: str
    at: datetime


def _uuid(short: str) -> str:
    return f"0000{short}-0000-1000-8000-00805f9b34fb"


class Twin:
    def __init__(
        self,
        family: Family,
        scenario: str,
        seed: int,
        *,
        start: datetime,
        interval_s: Decimal,
    ) -> None:
        if family not in ("oximeter", "cuff", "thermometer"):
            raise ValueError("unknown family")
        if start.tzinfo is None or start.utcoffset() != timedelta(0):
            raise ValueError("start must be UTC aware")
        if not interval_s.is_finite() or interval_s < 0:
            raise ValueError("interval must be finite and nonnegative")
        if interval_s > timedelta.max.days * 86400:
            raise ValueError("interval outside datetime range")
        numerator, denominator = interval_s.as_integer_ratio()
        micros, remainder = divmod(numerator * 1_000_000, denominator)
        if remainder:
            raise ValueError("interval outside datetime resolution")
        scenarios = load_scenarios()
        if scenario not in scenarios or (scenario == "finger_out" and family != "oximeter"):
            raise ValueError("scenario not applicable")
        self.family, self.scenario, self.seed = family, scenarios[scenario], seed
        self.start, self.interval_s = start, interval_s
        self._step = timedelta(microseconds=micros)
        kind = {"oximeter": "plx", "cuff": "bpm", "thermometer": "htm"}[family]
        self.identity = DeviceIdentity(f"SIM-{kind.upper()}-{seed:04d}", f"sim-{kind}", f"{seed:04d}")
        self.characteristics = self._characteristics()

    def _characteristics(self) -> dict[str, str]:
        names = {
            "oximeter": {"plx_continuous": "2a5f", "plx_spot_check": "2a5e"},
            "cuff": {"blood_pressure_measurement": "2a35"},
            "thermometer": {"temperature_measurement": "2a1c"},
        }[self.family]
        return {name: _uuid(short) for name, short in names.items()} | {"battery_level": _uuid("2a19")}

    def iter_events(self, count: int) -> Iterator[Notification | Disconnect]:
        if not 0 <= count <= MAX_EVENTS:
            raise ValueError("event count outside bounded stream")
        physiology = Physiology(self.seed)
        parameters = self.scenario.parameters
        offset = int(self.scenario.name == "low_battery")
        last_payloads: dict[str, bytes] = {}
        for index in range(count):
            try:
                at = self.start + self._step * index
            except OverflowError as exc:
                raise ValueError("simulated clock overflow") from exc
            if offset and index == 0:
                yield self._notification("battery_level", gatt.battery_level(int(parameters["percent"])), at)
                continue
            sample = index - offset
            if (
                self.scenario.name in ("disconnect_mid_reading", "low_battery")
                and sample >= parameters["notifications"]
            ):
                yield Disconnect("power_off" if offset else "link_lost", at)
                return
            name, payload = self._measurement(physiology, sample, at)
            if self.scenario.name == "frozen_sensor":
                if sample <= parameters["from_notification"] or name not in last_payloads:
                    last_payloads[name] = payload
                payload = last_payloads[name]
            if self.scenario.name == "malformed_packet" and sample == parameters["notification_index"]:
                payload = payload[:-1]
            yield self._notification(name, payload, at)

    def _notification(self, name: str, payload: bytes, at: datetime) -> Notification:
        return Notification(name, self.characteristics[name], payload, at)

    def _measurement(self, model: Physiology, sample: int, at: datetime) -> tuple[str, bytes]:
        scenario, p = self.scenario.name, self.scenario.parameters
        multiplier = p.get("noise_multiplier", Decimal(1))
        pulse = model.heart_rate(self.interval_s * sample, multiplier)
        if scenario == "out_of_range":
            pulse = p["pulse_bpm"]
        if self.family == "oximeter":
            return self._plx(model, sample, at, pulse, multiplier)
        if self.family == "thermometer":
            value = model.temperature(at, multiplier) + p.get("offset_cel", Decimal(0))
            value = p.get("temperature_cel", value)
            return "temperature_measurement", gatt.temperature_measurement(
                quantize(value, "0.1"), timestamp=at
            )
        overrides = p if scenario == "hypertension" else None
        systolic, diastolic, mean = model.blood_pressure(multiplier, overrides)
        if scenario == "out_of_range":
            systolic, diastolic = p["systolic_mmhg"], p["diastolic_mmhg"]
            mean = quantize(diastolic + (systolic - diastolic) / 3)
        return "blood_pressure_measurement", gatt.blood_pressure_measurement(
            quantize(systolic),
            quantize(diastolic),
            mean,
            timestamp=at,
            pulse=pulse,
            measurement_status=int(gatt.BloodPressureStatus.body_movement)
            if scenario == "motion_low_perfusion"
            else 0,
        )

    def _plx(
        self,
        model: Physiology,
        sample: int,
        at: datetime,
        pulse: Decimal,
        multiplier: Decimal,
    ) -> tuple[str, bytes]:
        p, scenario = self.scenario.parameters, self.scenario.name
        spo2: WireValue = model.spo2(multiplier)
        rate: WireValue = pulse
        measurement_status = device_status = 0
        if scenario == "hypoxia_drop":
            mean = model.parameters["spo2"]["mean_percent"]
            fraction = (
                Decimal(1) if p["samples"] == 1 else min(Decimal(1), Decimal(sample) / (p["samples"] - 1))
            )
            spo2 = model.spo2(multiplier, mean + (p["target_percent"] - mean) * fraction)
        elif scenario == "finger_out":
            spo2 = rate = SFLOAT_NAN
            measurement_status = int(gatt.MeasurementStatus.measurement_unavailable)
            device_status = int(gatt.DeviceSensorStatus.sensor_unconnected_to_user)
        elif scenario == "motion_low_perfusion":
            measurement_status = int(gatt.MeasurementStatus.questionable_measurement)
            device_status = int(
                gatt.DeviceSensorStatus.low_perfusion | gatt.DeviceSensorStatus.erratic_signal
            )
        elif scenario == "out_of_range":
            spo2 = quantize(p["spo2_percent"])
        if sample % 2:
            return "plx_spot_check", gatt.plx_spot_check(
                spo2,
                rate,
                timestamp=at,
                measurement_status=measurement_status,
                device_status=device_status,
            )
        return "plx_continuous", gatt.plx_continuous(
            spo2,
            rate,
            measurement_status=measurement_status,
            device_status=device_status,
        )

    def events(self, count: int) -> tuple[Notification | Disconnect, ...]:
        """Return up to count events; a disconnect terminates the stream."""
        return tuple(self.iter_events(count))


def recording(twin: Twin, count: int) -> dict[str, object]:
    frames: list[dict[str, str]] = []
    for event in twin.events(count):
        if isinstance(event, Disconnect):
            frames.append({"event": "disconnect"})
        elif event.characteristic != "battery_level":
            frames.append({"characteristic": event.characteristic, "payload_hex": event.payload.hex()})
    identity = twin.identity
    return {
        "origin": "SIM",
        "provenance": f"{identity.name}, {identity.manufacturer}, {identity.model}, serial {identity.serial}; scenario {twin.scenario.name}; seed {twin.seed}; simulated twin, not a device recording",
        "frames": frames,
    }
