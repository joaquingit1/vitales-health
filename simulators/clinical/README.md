# Clinical device twins

This independent Python 3.14 uv project simulates the device side of an oximeter,
blood pressure cuff and thermometer. Runtime dependencies are exclusively Python's
standard library. It imports no hub or adapter code. The byte encoders implement
Bluetooth SIG PLX Continuous Measurement (0x2A5F), PLX Spot-check Measurement
(0x2A5E), Blood Pressure Measurement (0x2A35), Temperature Measurement (0x2A1C)
and Battery Level (0x2A19), using IEEE 11073 decimal words.

Every identity is frozen and declares `simulated=True`, manufacturer
`crew-health simulator`, and a `SIM-` name. Recording documents declare `SIM`
origin and identify the scenario, seed and simulated provenance.

## Use

```sh
uv run --project simulators/clinical python -m crew_health_sim_clinical list
uv run --project simulators/clinical python -m crew_health_sim_clinical record --family oximeter --scenario hypoxia_drop --seed 1 --count 25 --start 2026-11-14T17:00:00Z --interval-s 1
```

Families are `oximeter`, `cuff` and `thermometer`. The default start is
2000-01-01T00:00:00Z, so repeated CLI runs reproduce the same bytes. The default
interval is one second. Python callers must provide a UTC aware start and a
nonnegative Decimal interval expressible in whole microseconds.

`Twin.events(count)` returns up to `count` total events, including battery and
disconnect events. The stream terminates at disconnect. Requests are bounded to
100,000 events. Each call replays from the seed and start, independently of previous
calls. Oximeters alternate continuous and timestamped spot-check notifications.
Cuffs and thermometers always include timestamps. The clock advances one interval
per event, including battery and disconnect events. No wall clock drives physiology.

`recording(twin, count)` returns the adapters' frame recording shape: `origin`,
`provenance` and `frames`. Each frame contains `characteristic` and lowercase
`payload_hex`, or `event: disconnect`. Battery frames are omitted because the
adapters do not subscribe to them. Counts still include omitted battery events.

## BLE transport connection

```python
from datetime import UTC, datetime
from decimal import Decimal

from crew_health_sim_clinical.ble import client_factory
from crew_health_sim_clinical.twin import Twin

twin = Twin(
    "oximeter",
    "disconnect_mid_reading",
    1,
    start=datetime(2026, 11, 14, 17, tzinfo=UTC),
    interval_s=Decimal("0.01"),
)
BleakClient = client_factory(twin)
client = BleakClient("SIM-PLX-0001", disconnected_callback=lambda client: None)
```

The returned factory accepts the constructor arguments used by the adapters'
`BleakFrameSource`, which takes it as its `client_factory`: the adapters' entry
points do this for `--source sim:<scenario>:<seed>` (through
`crew_health_device.adapters.twin_source`), and their `hardware` tests and the
E07 eval scenarios (`eval/scenarios/E07/test_twins.py`) do it by default. The
client's `simulated` identity makes the transport report SIM; nothing is
patched, and this package never imports the adapter, hub or bleak.

`connect`, `start_notify`, `stop_notify` and `disconnect` have the async client
shape used by that transport. `start_notify` accepts names or full UUIDs and
passes `(uuid, bytearray)` to callbacks. One bounded delivery task runs on the
caller's event loop, routes events to subscribed characteristics and sleeps for
the configured interval between events, or for `client_factory(twin, pace_s=...)`
when a caller delivers faster than the simulated clock (the E07 eval keeps a one
second device clock, so device timestamps advance like a real device's, and
delivers every 2 ms). Additional subscriptions share the same
clock and stream. Low battery is the first event at the connection's simulated
start, delivered once notification subscription is established. Manual disconnect
cancels delivery. Simulated link or power loss invokes the disconnected callback.
The client does not open a radio or network socket.

The adapters' BLE transport reads this client's `simulated` identity and reports
SIM for it, so readings are labeled through the normal source rule (AGENTS.md
section 4), and the adapter health manifest lists the twin as a connected SIM
device, not a file replay.

## Scenarios

Descriptions and numeric parameters live in `src/crew_health_sim_clinical/data/scenarios.json`.

| Name | Behavior | Applicability |
| --- | --- | --- |
| normal | Baseline variability and noise | All families |
| hypoxia_drop | Linear fall of the SpO2 mean to target by the Nth sample, then holds, with the same sensor noise as a resting reading | Oximeter; other families continue normally |
| fever | Temperature offset | Thermometer; other families continue normally |
| hypertension | Overrides BP means | Cuff; other families continue normally |
| motion_low_perfusion | Multiplies noise, PLX low perfusion, erratic signal and questionable flags, cuff body movement | All families |
| finger_out | SFLOAT NaN SpO2 and pulse, unattached sensor and measurement unavailable | Oximeter; others raise ValueError |
| disconnect_mid_reading | Link loss after K measurement notifications | All families |
| low_battery | Initial battery notification, power loss after K measurement notifications | All families |
| out_of_range | Wire-valid values beyond plausible physiological ranges | All families |
| malformed_packet | Truncates the configured notification by one byte, then resumes normal frames | All families |
| frozen_sensor | Repeats each characteristic's complete frozen measurement payload, including its device timestamp, from the configured index without disconnecting | All families |

Hypoxia's first sample is the mean and its Nth sample is the target. A one-sample
transition emits the target immediately. Scenario notification indices are zero
based. Noise is deterministic for a given seed, configuration and stream. HR uses
respiratory sinus arrhythmia, SpO2 uses sensor noise clamped to 0..100, temperature
uses the simulated hour's cosinor, and BP uses correlated Gaussian samples.
ROUND_HALF_EVEN quantizes SpO2, pulse and BP to integers and temperature to 0.1 Cel
before encoding. MAP is calculated from the unrounded BP sample and rounded.
Integer inputs to GATT numeric fields are explicit raw IEEE words, including the
special constants; use Decimal inputs for measured numeric quantities.

Device form encoding fixes the SFLOAT exponent at 0 for SpO2, pulse and blood
pressure, and the FLOAT exponent at -1 for temperature. Each numeric value is
quantized to that field's resolution before encoding with ROUND_HALF_EVEN.
For example, SpO2 98 encodes as `62 00`, temperature 36.6 Cel as `6e 01 00 ff`,
and a 120/80/93 mmHg panel as `78 00 50 00 5d 00`. Values outside the selected
wire representation raise ValueError; raw special words retain their exact bytes.
Battery remains uint8. Exponents are centralized in `gatt.py` with the Bluetooth
SIG GATT Specification Supplement field-resolution provenance.

`frozen_sensor` uses zero-based `from_notification: 2`. The measurement at index 2
is frozen, and every later notification reuses its characteristic's last payload.
For an oximeter, continuous freezes at index 2 and spot-check at index 1, including
the latter's timestamp. A characteristic first seen after the freeze retains its
first payload. Delivery times continue to advance, and battery events are
unaffected. The scenario fills the requested bounded event count without a
disconnect. Both `list` and `record --scenario frozen_sensor` use the scenario
document directly.

## Verification

With the existing venv available, run the five gates from the repository root.
Set `UV_NO_SYNC=1` for offline use and put tool caches inside this project:

```sh
export UV_NO_SYNC=1
export UV_CACHE_DIR="$PWD/simulators/clinical/.uv-cache"
export RUFF_CACHE_DIR="$PWD/simulators/clinical/.ruff_cache"
export MYPY_CACHE_DIR="$PWD/simulators/clinical/.mypy_cache"
export HYPOTHESIS_STORAGE_DIRECTORY="$PWD/simulators/clinical/.hypothesis"
uv run --project simulators/clinical pytest -c simulators/clinical/pyproject.toml -q simulators/clinical --cov-config=simulators/clinical/pyproject.toml --cov --cov-branch --cov-fail-under=95
uv run --project simulators/clinical ruff check simulators/clinical
uv run --project simulators/clinical ruff format --check simulators/clinical
uv run --project simulators/clinical mypy --config-file simulators/clinical/pyproject.toml --strict simulators/clinical/src simulators/clinical/tests
(cd simulators/clinical && uv run lint-imports --config pyproject.toml)
```

`uv run --project simulators/clinical python simulators/clinical/tools/check.py`
runs reversible mutation checks, the five gates and a real CLI recording, then
prints a source tree hash. It runs each subprocess with a timeout and keeps caches
in this project. The existing `uv.lock` records development tool versions.

Tests use hand-computed byte vectors, a test-only independent field decoder for
every flag combination, Hypothesis range round trips, fixed-seed statistics and
async transport tests without wall clock assertions. Import-linter forbids the
hub, shared device package, three adapter packages, httpx, fastapi and bleak.
A separate AST test enforces standard-library-only imports and the full
`crew_health_adapter_` prefix ban, since import-linter does not allow partial
module-name wildcards.

## Sources

The physiology parameters' provenance is in `data/physiology.json`, packaged at
`src/crew_health_sim_clinical/data/physiology.json`, one note per value. The resting
heart rate is N21 and the temperature acrophase N23 (docs/product/NASA-SOURCES.md);
the SpO2, blood pressure and temperature means are non-NASA exceptions listed in
that catalog. No source read gives the daily temperature amplitude, so it is `null`
and the twin leaves the daily term out. Noise and variability terms are labeled
modelling assumptions; no adapter check depends on their size. These tests establish protocol arithmetic and configured model
behavior; they do not establish clinical validity. The wire layouts follow the
Bluetooth SIG GATT Specification Supplement fields supplied in the task.
