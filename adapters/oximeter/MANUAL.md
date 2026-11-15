# PLX oximeter adapter (E07-03)

This process uses the standard Bluetooth SIG Pulse Oximeter Service (PLX), service `0x1822`, spot-check characteristic `0x2A5E` and continuous characteristic `0x2A5F`. References: [Bluetooth SIG adopted specifications](https://www.bluetooth.com/specifications/specs/), Pulse Oximeter Service and GATT Specification Supplement; IEEE 11073-20601 SFLOAT numerical representation. Protocol constants come from those specifications. Device ranges, units, quality settings, labels and provenance come from the supplied validated device profile.

The alternatives are a standard PLX profile, a documented vendor variant, and unavailable instrument with manual entry. This implementation selects standard PLX. Vendor-specific formats require a separate sourced decoder and evidence. An unavailable instrument routes to hub-owned manual entry, preserving device attribution there. This process does not invent a measurement or implement manual entry.

Calibration is **unknown** until a paired model and its sourced calibration statement are recorded in the profile. Decoder verification against specification golden cases is numerical verification. It does not establish hardware compatibility or calibration: both remain **unmeasured** until recorded evidence exists. It establishes no clinical validation or mission acceptance.

## Operation

Install with Python 3.14 using `uv sync --project adapters/oximeter`. Add `--extra ble` only for physical BLE operation. Settings, profile, token and recording files are supplied externally. There is no built-in sample profile or recording.

Run `uv run --project adapters/oximeter python -m crew_health_adapter_oximeter --settings <settings.json> --profile <profile.json> --token-file <token> --hub-url <url> --adapter-id <id> --source <source> --journal <directory> --port <port>`.

`ble:<address>` is LIVE. `recording:<path>` is explicitly REPLAY or SIM as declared in its validated recording. A recording can never be LIVE. `sim:<scenario>:<seed>` runs the simulated twin from `simulators/clinical` (the `sim` extra) behind the same BLE transport, one notification a second; the twin's device identity declares it simulated, so its readings are SIM through the normal source rule. Each acquisition reads the next seed. Binding origin must match source origin. The worker binds `127.0.0.1`, authenticates acquisition controls with a Bearer token, and reports connection and evidence through `/health`. Manual LIVE operation belongs to the hub. No optional model or ground link is required.

POST a binding to `/acquisitions`, query `/acquisitions/{id}?generation=N`, or POST `{"generation":N}` to `/acquisitions/{id}/cancel`. A started capture runs in the background and journals its exact attempt before delivery. One request emits only its requested capability, never an extra pulse observation. Reception time comes from the injected UTC clock; device timestamps never establish freshness.

## Failure modes

| Failure | Adapter behavior |
| --- | --- |
| Radio absent | Failed `radio_absent`, no invented observation. |
| Device absent | Failed `device_absent`, no invented observation. |
| Disconnect | Connection becomes disconnected. The profile completion is `window`: a link lost before the window or frame budget is used makes the attempt failed `disconnected` with no value, whatever arrived (E07-01). A link that drops only while closing after a complete window keeps the reading. |
| Timeout | Same rule as disconnect: a silent device before the window ends is failed `timeout`. Calls and acquisition windows are bounded. |
| Malformed frame | Typed frame reason, frame skipped with a quality note; no parsed substitute. |
| Special values | Missing component with the IEEE special reason, never zero or infinity. |
| Status flags | RFU flags 5 to 7, measurement status bits 0 to 4 and sensor status bits 16 to 23 are ignored with status `reserved_bits`. Profiles list it in `noted_status` to retain `device_status:reserved_bits`. Known rejecting statuses still skip the frame. Truncation and unexplained trailing bytes remain errors. |
| Stored data | Skipped with `stored_measurement_untrusted_time`, never presented as current. |
| Demonstration or testing data | Rejected under LIVE, independent of configuration. |
| Wrong origin | Rejected `origin_mismatch`; REPLAY and SIM cannot cross a LIVE binding. |
| Wrong subject | Rejected `wrong_subject` when supplied device and expected user indexes disagree. |
| Unsupported unit | Rejected with the protocol unit code; no guessed conversion. |
| Out of device range | Rejected `out_of_device_range`, no clipping. A profile without a sourced range leaves the check unset (`range_min` and `range_max` null) and every reading carries `unchecked:range:<code>`; likewise `unchecked:rate:<code>` for an unset rate limit. |
| Saturation | Rejected `saturated` at configured inclusive saturation bounds. |
| Stuck | Decided from evidence, never from equal values (a steady value is a good reading): a device-timestamped measurement that arrives again identical, timestamp included, is rejected `stuck` with note `frame:repeated_measurement`; a device sensor status listed as rejecting rejects the frame. |
| Not steady | With `steady_reading` (C08: the reading counts once it "stops changing and displays one steady number"), the reported sample is the last one equal to its predecessor; a window that never settles is rejected `not_steady`. |
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

## Clinical file import

E07-02 provides a library input in `crew_health_adapter_oximeter.fhir_import.service.ImportService`
for HL7 FHIR R4 4.0.1 JSON: a single Observation or a Bundle containing `entry[].resource`.
It produces typed local draft findings. Every result has status `awaiting_confirmation`.
P-E07-2 remains open: the generated typed finding/confirmation boundary is unpublished, and
there is no approved hub endpoint for imports. The service never posts anything, accepts a
clinical record, interprets findings or resolves a CapabilityGap. Confirmation and authorized
hub acceptance remain unavailable.

Load a sourced mapping using `fhir_import.mapping.load_mapping_file(path, max_bytes)` or pass
a mapping document to `ImportService(mapping, settings, clock)`. Both paths validate against
the packaged Draft 2020-12 `schemas/clinical-import-mapping.schema.json`, including provenance.
Duplicate `(system, code)` identities across numeric and interpretation mappings raise
`ImportMappingError`. No clinical code table is built into the importer. Invalid configuration
prevents construction; an existing service retains its immutable validated mapping.

Import settings use the same required entry document as the shared runtime:
each key maps to `value`, `unit`, `basis` and `to_be_set_by`. Load them through
`fhir_import.settings.load_settings(document)`; there are no defaults. Missing/unknown keys,
wrong types, wrong units, empty attribution/basis or out of range values raise the shared
`SettingsError`. Bounds are:

| Key | Unit | Inclusive bounds | Purpose |
|---|---|---|---|
| `import.max_bytes` | `By` | 1 to 10485760 | Raw file admission, also usable for bounded mapping reads. |
| `import.max_entries` | null | 1 to 10000 | Bundle resource count and distinct cached validation results per service. |
| `import.max_depth` | null | 1 to 64 | JSON container nesting; the root container has depth 1. |
| `import.max_text_bytes` | `By` | 1 to 65536 | UTF-8 qualitative narrative, including retained coding displays. |

Supply a clock with `now() -> datetime`, returning a timezone aware instant. Read a clinical
file with an explicit bound before passing its bytes to the service:

```python
from crew_health_adapter_oximeter.fhir_import.mapping import load_mapping_file
from crew_health_adapter_oximeter.fhir_import.service import ImportService

mapping = load_mapping_file(mapping_path, settings.max_bytes)
importer = ImportService(mapping, settings, clock)
with clinical_path.open("rb") as stream:
    raw = stream.read(settings.max_bytes + 1)
result = importer.import_file(
    raw,
    expected_patient_reference=selected_patient_reference,
    expected_examination_date=selected_examination_date,
)
```

Patient references must match exactly. The optional examination date compares against the
source timestamp's date in its own local offset. Partial dates, missing timezones, future times,
nonfinal status, missing/wrong subjects, unknown codes and unit mismatches are held with a reason and JSON
pointer. Bundle resources other than Observation are held individually. Nothing fills missing
values, converts units, averages conflicting codes or replaces a historical examination time
with import time. A `valueQuantity` carrying any `comparator` is held with
`comparator_unsupported`, never imported as an exact value. Numeric values without a comparator
stay exact finite Decimal quantities with their UCUM code.
ECG and other qualitative findings retain the source codings/text and performer attribution as
`interpreter`, with `confirmed=False`. They never become numeric values or context entries.

Each finding keeps `examined_at` and `imported_at` separately, plus a raw-file SHA-256 and
source JSON pointer. The empty pointer means a standalone root Observation. `import_id` is
`imp_` followed by the first 32 hex digits of SHA-256 of the raw-file hash concatenated with
the expected patient reference. All output tuples follow JSON pointer order with array indexes
compared numerically, so `/entry/2/resource` precedes `/entry/10/resource`.

A service retains each first validation result in protected process memory, keyed by import
identity and expected examination date. Identical retries return that result, including its
original `imported_at`, even if the clock moves. Changed examination-date constraints are
validated separately. At `import.max_entries` distinct results, a new result evicts the oldest
cached result. Cache hits do not change eviction order. An evicted import is revalidated using
the current clock, retaining its deterministic identity and source values with a new import
timestamp. This cache is not a durable clinical store or an authorization grant. A new process
can reproduce the import identity but needs a new
validation and import timestamp. Keep input files and draft results in station-protected
storage; nothing private is logged.

Oversized files, malformed JSON/UTF-8, duplicate keys, nonfinite JSON constants, excessive
nesting and excessive resource counts raise `ImportRejected` with the corresponding reason.
Nesting is checked with an explicit stack before decoding, so deeply nested hostile input
does not reach the JSON decoder's recursive descent. No drafts from a rejected file are
returned. Source device references are text attribution only. Import never creates hardware,
changes device availability, or updates the manifest's connected operation. The shared manifest
is owned by `crew_health_device`, which this input does not edit, so no `import` operation is
added to that manifest. Hardware compatibility remains unmeasured until independently tested.

## Sources

Cited by catalog ID following docs/product/NASA-SOURCES.md section 4.

- Why the kit carries a pulse oximeter and why its reading is kept as attributed device data: RT17 (NASA CHP-IDSP Project Updates, HRP IWS 2025, NTRS 20250000330), quoted at docs/product/ROUTINE.md:172: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file". RT18 (NTRS 20230015627) and RT17 place blood oxygen in the pre and post spacewalk exam (docs/product/ROUTINE.md section 5). The instrument is the "Kit pulse oximeter (finger)" of that section.
- What an oximeter reading cannot tell, and why it is read with the symptoms (circulation, skin pigmentation, skin temperature, tobacco use, nail polish): C08 (FDA, Pulse Oximeter Basics), via docs/product/DEMO-CONDITIONS.md, kit table. Non-NASA: the NASA catalog has no oximeter accuracy source. The adapter reports these as the profile's calibration `unknown` and hardware evidence `unmeasured`, never as a corrected value.
- Byte layout, flags, SFLOAT encoding and measurement status bits: Bluetooth SIG Pulse Oximeter Service and GATT Specification Supplement. Non-NASA, used because no document in the NASA catalog defines a device protocol. BLE on the habitat network follows docs/DEPLOYMENT.md:128 (ICSIS, vehicle-computing.md 3.3 [VC40]).
- Clinical import codes: docs/SYSTEM.md:1760 (SYSTEM 7.3, LOINC first, UCUM units, health-data-standards.md 6); LOINC 8601-7 EKG impression from loinc.org (non-NASA: the NASA catalog defines no terminology codes). The scenario mapping document is eval/scenarios/E07/assets/clinical-import-mapping.json.
- A profile's device range is a data check, not a clinical threshold (contracts/reference/input-dictionary.json, fields `spo2` and `pulse`, validation text). No NASA source in the catalog sets an oximeter device range, so each profile states the basis of its own range in `provenance`.
