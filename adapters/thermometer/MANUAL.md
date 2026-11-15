# HTM thermometer adapter (E07-03)

This process uses the standard Bluetooth SIG Health Thermometer Service (HTM), service `0x1809`, Temperature Measurement characteristic `0x2A1C` (`00002a1c-0000-1000-8000-00805f9b34fb`). References: [Bluetooth SIG adopted specifications](https://www.bluetooth.com/specifications/specs/), Health Thermometer Service and GATT Specification Supplement; IEEE 11073-20601 FLOAT numerical representation. Protocol constants come from those specifications. Device ranges, units, quality settings, labels and provenance come from the supplied validated device profile.

The alternatives are a standard HTM profile, a documented vendor variant, and unavailable instrument with manual entry. This implementation selects standard HTM. Vendor-specific formats require a separate sourced decoder and evidence. An unavailable instrument routes to hub-owned manual entry, preserving device attribution there. This process does not invent a measurement or implement manual entry.

Calibration is **unknown** until a paired model and its sourced calibration statement are recorded in the profile. Decoder verification against specification golden cases is numerical verification. It does not establish hardware compatibility or calibration: both remain **unmeasured** until recorded evidence exists. It establishes no clinical validation or mission acceptance.

## Operation

Install with Python 3.14 using `uv sync --project adapters/thermometer`. Add `--extra ble` only for physical BLE operation. Settings, profile, token and recording files are supplied externally. There is no built-in sample profile or recording.

Run `uv run --project adapters/thermometer python -m crew_health_adapter_thermometer --settings <settings.json> --profile <profile.json> --token-file <token> --hub-url <url> --adapter-id <id> --source <source> --journal <directory> --port <port>`.

`ble:<address>` is LIVE. `recording:<path>` is explicitly REPLAY or SIM as declared in its validated recording. A recording can never be LIVE. `sim:<scenario>:<seed>` runs the simulated twin from `simulators/clinical` (the `sim` extra) behind the same BLE transport, one notification a second; the twin's device identity declares it simulated, so its readings are SIM through the normal source rule. Each acquisition reads the next seed. Binding origin must match source origin. The worker binds `127.0.0.1`, authenticates acquisition controls with a Bearer token, and reports connection and evidence through `/health`. Manual LIVE operation belongs to the hub. No optional model or ground link is required.

POST a binding to `/acquisitions`, query `/acquisitions/{id}?generation=N`, or POST `{"generation":N}` to `/acquisitions/{id}/cancel`. A started capture runs in the background and journals its exact attempt before delivery. One request emits only the requested temperature capability. Temperature type codes 1 through 9 are carried as `device_site_code`; reserved types are frame errors. HTM has no subject index or status word. The operator must bind the correct patient and device; this protocol cannot verify their identity. Reception time comes from the injected UTC clock; device timestamps never establish freshness.

## Failure modes

| Failure | Adapter behavior |
| --- | --- |
| Radio absent | Failed `radio_absent`, no invented observation. |
| Device absent | Failed `device_absent`, no invented observation. |
| Disconnect | Connection becomes disconnected. The profile completion is `measurement`: each indication is a whole measurement, so a link that ends after one keeps it; with no measurement received the attempt is failed `disconnected`. |
| Timeout | Same rule as disconnect: failed `timeout` only when no measurement arrived. Calls and acquisition windows are bounded. |
| Malformed frame | Typed frame reason, frame skipped with a quality note; no parsed substitute. |
| Special values | Missing component with the IEEE special reason, never zero or infinity. |
| Reserved fields | RFU flag bits 3 through 7 are ignored with status `reserved_bits`. Profiles list it in `noted_status` to retain `device_status:reserved_bits`. Temperature types 0 or 10 through 255 remain frame errors. HTM has no status word; truncation and unexplained trailing bytes remain errors. |
| Stored data | Shared runtime skips `data_from_storage` with `stored_measurement_untrusted_time` when a protocol supplies that status. HTM cannot identify stored data from this characteristic; it has no status word. |
| Demonstration or testing data | Shared runtime rejects demonstration/testing statuses under LIVE when supplied by a protocol. HTM cannot identify them from this characteristic. Recorded sources remain explicitly REPLAY or SIM. |
| Wrong origin | Rejected `origin_mismatch`; REPLAY and SIM cannot cross a LIVE binding. |
| Wrong subject | HTM does not transmit patient identity. Keep the correct patient and device binding at the hub. The shared runtime rejects `wrong_subject` if another protocol supplies a conflicting user index; HTM never invents one. |
| Unsupported unit | A Fahrenheit frame under a Celsius-only profile is rejected `unsupported_unit:degree_fahrenheit`. The decoder never performs affine conversion; profile mappings carry UCUM units. |
| Out of device range | Rejected `out_of_device_range`, no clipping. A profile without a sourced range leaves the check unset (`range_min` and `range_max` null) and every reading carries `unchecked:range:<code>`; likewise `unchecked:rate:<code>` for an unset rate limit. |
| Saturation | Rejected `saturated` at configured inclusive saturation bounds. |
| Stuck | Decided from evidence, never from equal values (a steady value is a good reading): a device-timestamped measurement that arrives again identical, timestamp included, is rejected `stuck` with note `frame:repeated_measurement`; a device sensor status listed as rejecting rejects the frame. |
| Rate | Rejected `implausible_rate`; nonpositive time deltas are `unknown_time_order`. |
| Cancel during capture | Frames already read are kept: a qualified value is sent with note `acquisition_cancelled` and the hub quarantines it as late evidence; otherwise failed `cancelled`. |
| Unit change inside one window | Rejected `unit_changed`; samples in two units are never compared or converted. |
| Insufficient samples | Rejected `insufficient_samples`, no averaging or gap filling. |
| Notification queue full | Capture disconnects with an overflow quality note; no silent sample drop. |
| Journal full or disk full | `JournalFull` or `JournalUnavailable` propagates; nothing is claimed sent. |
| Hub refusal | Nonretryable refusal held in journal, never automatically resent. A retryable refusal stays pending with identical body bytes until it has been refused `retry_max` times in all (the count is journaled, so restarts do not reset it), then it is held `retry_exhausted:<code>`. Transport failures stay pending. Held sends are listed by `GET /deliveries` and an operator sends one again with `POST /deliveries/release`; nothing is deleted. |
| Receipt mismatch | Held `receipt_mismatch`; only the exact accepted command, operation and request target allow removal. |
| Restart with unknown physical state | Existing acquisition with the same binding is duplicate, never recaptured; the same acquisition id for another request, patient, capability or device is a conflict. In-flight physical state is unknown. Pending attempts are delivered once at startup. |
| Invalid profile reload | Last good profile stays active; open work remains pinned to its starting profile. |

`observed_at` is the adapter's arrival time on its UTC clock. When a frame carries the device's own timestamp, the observation keeps it as context `device_time`, with `device_clock_set` when the device reports it; the device clock is evidence, never the record time.

An acquisition marker stays in the journal while its attempt is unacknowledged, so a restart never recaptures it. When the hub acknowledges the attempt (200 or the exact accepted_command receipt), the send record and a finished marker are removed, keeping the journal bounded; a marker still in `started` (unknown physical state) is never removed automatically. Journal capacity is finite and exhaustion fails loudly. Journal files contain clinical attempt data and require the station's protected local storage and access controls. No request bodies are logged.

## Verification

The suite covers specification golden FLOAT frames (including `0xFF00016E = 36.6`), every special value, all optional fields and truncation boundaries, RFU flags, all temperature type bytes, arbitrary bytes and FLOAT values, capture failures, durable restart duplicates, journal delivery, reception timestamps, and scratch profile edits. Tests use schema-validated in-memory SIM documents and temporary journals. These are numerical checks, not physical calibration or clinical validation.

The `hardware` notification test runs against the simulated thermometer twin by default (AGENTS.md section 4); supplying `CHC_E07_THERMOMETER_ADDRESS`, with the BLE extra installed, runs it against a real thermometer as an extra run. A passing physical test alone does not change the profile's hardware evidence. Record the exact model, calibration source and measured compatibility evidence separately before declaring measured hardware compatibility. Manual temperature entry from a functioning instrument belongs to the hub and retains its device attribution; an absent instrument has no temperature value.

## Sources

Cited by catalog ID following docs/product/NASA-SOURCES.md section 4.

- Why the kit carries a thermometer and why its reading is kept as attributed device data: RT17 (NASA CHP-IDSP Project Updates, HRP IWS 2025, NTRS 20250000330), quoted at docs/product/ROUTINE.md:172: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file". The instrument is the "Kit digital thermometer" of docs/product/ROUTINE.md section 6.
- Byte layout, flags, FLOAT encoding and temperature type codes: Bluetooth SIG Health Thermometer Service and GATT Specification Supplement. This is a non-NASA source, used because no document in the NASA catalog defines a device protocol. BLE on the habitat network follows docs/DEPLOYMENT.md:128 (ICSIS, vehicle-computing.md 3.3 [VC40]).
- A profile's device range is a data check, not a clinical threshold (contracts/reference/input-dictionary.json, field `temperature`, validation text). No NASA source in the catalog sets a thermometer device range, so each profile states the basis of its own range in `provenance`.
