"""Device models: what each low-cost sensor reports when it samples the cabin.

The measurement chain of every output: true input, kind-specific physics (cross-sensitivity,
humidity growth, baseline learning, counting statistics), sensitivity loss and offset drift with
age, noise, clamping to the sensor range and rounding to its resolution. A value clamped at either
end of the range reports `saturated`. Faults from the scenario (stuck, dropout, offset, drift,
disconnect) act on the device, never on the truth. A device that cannot measure (no truth for its
quantity, a dropout, a lost link) emits nothing: a gap stays a gap.
"""

import math
import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from crew_health_sim_habitat.devicecfg import DeviceKind, OutputSpec
from crew_health_sim_habitat.scenariocfg import DeviceFault
from crew_health_sim_habitat.units import Quantity, quantize
from crew_health_sim_habitat.world import Truth

STATUS_OK = "ok"
STATUS_WARMING_UP = "warming_up"
STATUS_SATURATED = "saturated"
DEVICE_STATUSES = (STATUS_OK, STATUS_WARMING_UP, STATUS_SATURATED)
_DAY_S = 86400.0
# Above this mean, a Poisson count is drawn from its normal approximation (error below 1%).
_POISSON_NORMAL_ABOVE = 50.0


@dataclass(frozen=True, slots=True)
class DeviceReading:
    """One output register value at one sample instant (seconds since scenario start)."""

    device_id: str
    output: str
    quantity: Quantity
    t_s: float
    status: str


def poisson(rng: random.Random, mean: float) -> int:
    """A Poisson draw with a bounded loop (inversion up to a fixed mean, normal above)."""
    if mean <= 0:
        return 0
    if mean > _POISSON_NORMAL_ABOVE:
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    limit, product, count = math.exp(-mean), rng.random(), 0
    while product > limit and count < 10 * int(_POISSON_NORMAL_ABOVE):
        product *= rng.random()
        count += 1
    return count


class Device:
    """A simulated device of one kind.

    The device_id names the device inside the simulator only and never reaches the wire. The hub labels
    a reading SIM from the channel's registry entry (origin_mode SIM in config/sensor-channels.sim.json)
    and the reading's own origin_mode, which E23 checks against the channel (ORIGIN_MISMATCH).
    """

    def __init__(
        self, device_id: str, kind: DeviceKind, faults: Sequence[DeviceFault], rng: random.Random
    ) -> None:
        self.device_id = device_id
        self.kind = kind
        self._faults = tuple(f for f in faults if f.device_id == device_id)
        self._rng = rng
        self._drift_sign = {o.name: rng.choice((-1.0, 1.0)) for o in kind.outputs}
        self._last: list[DeviceReading] = []
        self._voc_baseline: float | None = None
        window = kind.extras.get("rate_window", 0.0)
        self._history: deque[tuple[float, float]] = deque(maxlen=int(window / kind.interval_s) + 1)
        self._powered_since_s = 0.0
        self.next_due_s = 0.0

    def due(self, t_s: float) -> bool:
        return t_s + 1e-9 >= self.next_due_s

    def _fault(self, t_s: float, fault: str) -> DeviceFault | None:
        for item in self._faults:
            if item.fault == fault and item.active(t_s):
                return item
        return None

    def sample(self, t_s: float, truth: Truth) -> list[DeviceReading]:
        """Sample at t; schedules the next sample one interval later."""
        self.next_due_s = t_s + self.kind.interval_s
        link = self._fault(t_s, "disconnect")
        if link is not None:
            self._lose_link(link.start_s + link.duration_s)
            return []
        if self._fault(t_s, "dropout") is not None:
            return []
        if self._fault(t_s, "stuck") is not None and self._last:
            return [DeviceReading(r.device_id, r.output, r.quantity, t_s, r.status) for r in self._last]
        readings: list[DeviceReading] = []
        measured: dict[str, float] = {}
        for spec in self.kind.outputs:
            raw = self._input(spec, truth, measured, t_s)
            if raw is None:
                continue
            reading = self._finish(spec, raw, t_s)
            measured[spec.name] = reading.quantity.value
            readings.append(reading)
        readings.extend(self._derived(t_s, truth, measured))
        self._last = readings
        return readings

    def _lose_link(self, back_s: float) -> None:
        """A lost link power cycles the device: it warms up again, relearns its baselines and has no
        last value to repeat."""
        self._powered_since_s = back_s
        self._last = []
        self._voc_baseline = None
        self._history.clear()

    def _input(self, spec: OutputSpec, truth: Truth, measured: dict[str, float], t_s: float) -> float | None:
        quantity = spec.quantity
        if quantity == "pressure_rate":
            return None
        if quantity == "voc_signal":
            return self._voc_index(truth)
        if quantity == "geiger_counts":
            return self._geiger_cpm(truth)
        if quantity in ("geiger_dose_estimate", "o2_percent"):
            return None
        true_value = truth.get(quantity)
        if true_value is None:
            return None
        raw = true_value + self._cross(truth)
        if self.kind.kind == "optical_pm":
            raw *= self._humidity_factor(truth)
        return self._age(spec, raw, t_s)

    def _cross(self, truth: Truth) -> float:
        total = 0.0
        for quantity, coefficient in sorted(self.kind.cross_sensitivity.items()):
            other = truth.get(quantity)
            if other is not None:
                total += coefficient * other
        return total

    def _age(self, spec: OutputSpec, raw: float, t_s: float) -> float:
        days = t_s / _DAY_S
        gain = max(0.0, 1.0 - spec.sensitivity_loss_per_day * days)
        return raw * gain + self._drift_sign[spec.name] * spec.offset_drift_per_day * days

    def _finish(self, spec: OutputSpec, raw: float, t_s: float) -> DeviceReading:
        """Faults, noise, range and resolution. The reading is saturated when the value the device is
        measuring lies beyond either end of its range: the register then shows the end it reached.
        Noise around an in-range value is clamped to the range and stays a valid reading."""
        offset = self._fault(t_s, "offset")
        if offset is not None and offset.offset is not None and offset.acts_on(spec.unit):
            raw += offset.offset
        drift = self._fault(t_s, "drift")
        if drift is not None and drift.offset is not None and drift.acts_on(spec.unit):
            raw += drift.offset * (t_s - drift.start_s) / 3600.0
        noisy = raw
        sd = spec.noise_abs + spec.noise_rel * abs(raw)
        if sd > 0:
            noisy += self._rng.gauss(0.0, sd)
        warming = t_s < self._powered_since_s + self.kind.warmup_s
        status = STATUS_WARMING_UP if warming else STATUS_OK
        if raw > spec.range_max or raw < spec.range_min:
            status = STATUS_SATURATED
        noisy = min(spec.range_max, max(spec.range_min, noisy))
        if status == STATUS_SATURATED:
            noisy = spec.range_max if raw > spec.range_max else spec.range_min
        value = quantize(noisy, spec.resolution)
        return DeviceReading(self.device_id, spec.name, Quantity(value, spec.unit), t_s, status)

    def _humidity_factor(self, truth: Truth) -> float:
        rh = truth.get("rh_percent")
        threshold = self.kind.extra("humidity_threshold")
        if rh is None or rh <= threshold:
            return 1.0
        rh = min(rh, 99.0)
        return 1.0 + self.kind.extra("humidity_growth") * (rh - threshold) / (100.0 - rh)

    def _voc_index(self, truth: Truth) -> float | None:
        tvoc = truth.get("tvoc_ugm3")
        if tvoc is None:
            return None
        signal = max(1e-6, tvoc + self._cross(truth))
        if self._voc_baseline is None:
            self._voc_baseline = signal
        alpha = min(1.0, self.kind.interval_s / self.kind.extra("baseline_tau"))
        self._voc_baseline += (signal - self._voc_baseline) * alpha
        return 100.0 * signal / self._voc_baseline

    def _geiger_cpm(self, truth: Truth) -> float | None:
        dose = truth.get("dose_rate_ugy_h")
        if dose is None:
            return None
        true_cps = dose * self.kind.extra("cps_per_ugy_h")
        observed_cps = true_cps / (1.0 + true_cps * self.kind.extra("dead_time"))
        window = self.kind.interval_s
        return poisson(self._rng, observed_cps * window) * 60.0 / window

    def _o2_percent(self, truth: Truth, ppo2_kpa: float) -> float | None:
        """% O2 from the cell's own ppO2 and its own pressure sensor, as such a device reports it."""
        pressure = truth.get("pressure_kpa")
        if pressure is None:
            return None
        internal = pressure + self._rng.gauss(0.0, self.kind.extra("internal_pressure_noise"))
        return 100.0 * ppo2_kpa / internal if internal > 0 else None

    def _derived(self, t_s: float, truth: Truth, measured: dict[str, float]) -> list[DeviceReading]:
        """Outputs computed by the device from its own measured registers."""
        names = {o.name for o in self.kind.outputs}
        result: list[DeviceReading] = []
        if "pressure_rate" in names and "pressure" in measured:
            self._history.append((t_s, measured["pressure"]))
            first_t, first_p = self._history[0]
            if len(self._history) == self._history.maxlen and t_s > first_t:
                rate = (measured["pressure"] - first_p) / (t_s - first_t) * 60.0
                spec = self.kind.output("pressure_rate")
                result.append(self._finish(spec, rate, t_s))
        if "dose_rate_estimate" in names and "counts" in measured:
            spec = self.kind.output("dose_rate_estimate")
            result.append(self._finish(spec, measured["counts"] / self.kind.extra("cpm_per_usv_h"), t_s))
        if "o2_percent" in names and "ppo2" in measured:
            percent = self._o2_percent(truth, measured["ppo2"])
            if percent is not None:
                result.append(self._finish(self.kind.output("o2_percent"), percent, t_s))
        return result
