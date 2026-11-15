"""Seeded, provisional physiology. All model parameters come from package data."""

import json
import math
import random
from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from importlib.resources import files
from pathlib import Path


def object_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("expected a JSON object")
    return {str(key): item for key, item in value.items()}


def read_document(name: str, path: Path | None = None) -> dict[str, object]:
    text = (
        files(__package__).joinpath("data", name).read_text(encoding="utf-8")
        if path is None
        else path.read_text(encoding="utf-8")
    )
    value: object = json.loads(text)
    return object_map(value)


# Parameters no source gives yet: null in the document, and the model leaves that term out (nothing invented).
NULLABLE = frozenset(("amplitude_cel",))


def decimal_map(value: object) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for key, item in object_map(value).items():
        if item is None and key in NULLABLE:
            continue
        if not isinstance(item, str):
            raise ValueError("parameters must be decimal strings")
        try:
            number = Decimal(item)
        except ArithmeticError as exc:
            raise ValueError("invalid decimal parameter") from exc
        if not number.is_finite():
            raise ValueError("nonfinite parameter")
        result[key] = number
    return result


def quantize(value: Decimal, resolution: str = "1") -> Decimal:
    return value.quantize(Decimal(resolution), rounding=ROUND_HALF_EVEN)


class Physiology:
    def __init__(self, seed: int, *, path: Path | None = None) -> None:
        document = read_document("physiology.json", path)
        self.parameters = {
            name: decimal_map(document[name])
            for name in ("heart_rate", "spo2", "temperature", "blood_pressure")
        }
        for group in self.parameters.values():
            if any(value < 0 for key, value in group.items() if "sd_" in key):
                raise ValueError("negative standard deviation")
        if abs(self.parameters["blood_pressure"]["correlation"]) > 1:
            raise ValueError("correlation outside unit interval")
        self._rng = random.Random(seed)

    def _noise(self, sd: Decimal, multiplier: Decimal) -> Decimal:
        return Decimal(str(self._rng.gauss(0, float(sd * multiplier))))

    def heart_rate(self, elapsed_s: Decimal, noise_multiplier: Decimal = Decimal(1)) -> Decimal:
        p = self.parameters["heart_rate"]
        phase = math.tau * float(elapsed_s * p["respiration_per_min"] / 60)
        value = p["mean_bpm"] + p["rsa_amplitude_bpm"] * Decimal(str(math.sin(phase)))
        return quantize(value + self._noise(p["noise_sd_bpm"], noise_multiplier))

    def spo2(self, noise_multiplier: Decimal = Decimal(1), mean_percent: Decimal | None = None) -> Decimal:
        """Sensor noise around the sourced resting mean, or around a scenario's trajectory (hypoxia)."""
        p = self.parameters["spo2"]
        centre = p["mean_percent"] if mean_percent is None else mean_percent
        value = centre + self._noise(p["sensor_noise_sd_percent"], noise_multiplier)
        return quantize(min(Decimal(100), max(Decimal(0), value)))

    def temperature(self, at: datetime, noise_multiplier: Decimal = Decimal(1)) -> Decimal:
        p = self.parameters["temperature"]
        hour = (
            Decimal(at.hour)
            + Decimal(at.minute) / 60
            + Decimal(at.second) / 3600
            + Decimal(at.microsecond) / 3600000000
        )
        value = p["mesor_cel"]
        if "amplitude_cel" in p:
            phase = math.tau * float((hour - p["acrophase_hour"]) / 24)
            value += p["amplitude_cel"] * Decimal(str(math.cos(phase)))
        return quantize(value + self._noise(p["noise_sd_cel"], noise_multiplier), "0.1")

    def blood_pressure(
        self,
        noise_multiplier: Decimal = Decimal(1),
        overrides: Mapping[str, Decimal] | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        p = self.parameters["blood_pressure"] | dict(overrides or {})
        z1, z2 = self._rng.gauss(0, 1), self._rng.gauss(0, 1)
        rho = float(p["correlation"])
        systolic = p["systolic_mean_mmhg"] + p["systolic_sd_mmhg"] * noise_multiplier * Decimal(str(z1))
        diastolic = p["diastolic_mean_mmhg"] + p["diastolic_sd_mmhg"] * noise_multiplier * Decimal(
            str(rho * z1 + math.sqrt(1 - rho * rho) * z2)
        )
        return quantize(systolic), quantize(diastolic), quantize(diastolic + (systolic - diastolic) / 3)
