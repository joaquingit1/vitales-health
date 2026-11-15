# Blood pressure cuff adapter (E07-03)

This process uses the standard Bluetooth SIG Blood Pressure Service, service `0x1810`, Blood Pressure Measurement characteristic `0x2A35` (`00002a35-0000-1000-8000-00805f9b34fb`). References: [Bluetooth SIG adopted specifications](https://www.bluetooth.com/specifications/specs/), Blood Pressure Service and GATT Specification Supplement; IEEE 11073-20601 SFLOAT numerical representation. Protocol constants come from those specifications and the binding E07 spec section 3. Device ranges, UCUM mappings, quality settings, labels and provenance come from the supplied validated device profile. Capture, journaling, delivery and the API reuse `crew_health_device` from the oximeter project.

The alternatives are a standard Blood Pressure Service profile, a documented vendor variant, and an unavailable instrument with manual entry. This implementation selects the standard service. Vendor-specific formats require a separate sourced decoder and evidence. An unavailable instrument routes to hub-owned manual entry, preserving device attribution there. This process does not invent a measurement or implement manual entry.

Calibration is **unknown** until a paired model and its sourced calibration statement are recorded in the profile. Follow that model's documented calibration procedure and retain the evidence before recording a calibration claim. Decoder verification against specification golden cases is numerical verification. It does not establish hardware compatibility or calibration: hardware compatibility remains **unmeasured** until recorded evidence exists. It establishes no clinical validation or mission acceptance. An optional hardware test alone does not change the profile's evidence declaration.

## Operation

Install with Python 3.14 using `uv sync --project adapters/cuff`. The editable path dependencies are `crew-health-adapter-oximeter` at `../oximeter` and `crew-health` at `../../hub`. Add `--extra ble` only for physical BLE operation. Settings, profile, token and recording files are supplied externally. There is no built-in sample profile or recording.

Run `uv run --project adapters/cuff python -m crew_health_adapter_cuff --settings <settings.json> --profile <profile.json> --token-file <token> --hub-url <url> --adapter-id <id> --source <source> --journal <directory> --port <port>`.

`ble:<address>` is LIVE. `recording:<path>` is explicitly REPLAY or SIM as declared in its validated recording. A recording can never be LIVE. `sim:<scenario>:<seed>` runs the simulated twin from `simulators/clinical` (the `sim` extra) behind the same BLE transport, one notification a second; the twin's device identity declares it simulated, so its readings are SIM through the normal source rule. Each acquisition reads the next seed. Binding origin must match source origin. The worker binds `127.0.0.1`, authenticates acquisition controls with a Bearer token, and reports connection and evidence through `/health`. Before any acquisition health is quiet. A normal capture end leaves health connected, becoming quiet after the configured interval; a lost connection reports disconnected. Manual LIVE operation belongs to the hub. No optional model or ground link is required.

POST a binding to `/acquisitions`, query `/acquisitions/{id}?generation=N`, or POST `{"generation":N}` to `/acquisitions/{id}/cancel`. A started capture runs in the background and journals its exact attempt before delivery. Reception time comes from the injected UTC clock; device timestamps never establish freshness.

The frame contains `systolic`, `diastolic`, `mean_arterial_pressure` and optionally `pulse_rate`. The profile maps `systolic` to `systolic_bp` and `diastolic` to `diastolic_bp`. Each request produces only its own observation. A profile `panels` entry binds the hub capability `blood_pressure` to `systolic_bp` and `diastolic_bp` (SYSTEM 7.3: LOINC 85354-9 panel with 8480-6 and 8462-4): one attempt carries both observations from the same frame, all or none, so a half panel is never sent. Without a panel entry a `blood_pressure` request is rejected `capability_not_in_profile`. E05 on main still accepts only observations whose code equals the requested capability, so it refuses a panel attempt and the adapter holds it (follow-up for the E05 owner under **P-E07-2**; eval/scenarios/E07/test_cuff.py pins the current behavior). MAP or pulse are not emitted as unsolicited observations. A missing MAP does not block a qualified systolic result.

Flag bit 0 selects `millimetre_of_mercury` or `kilopascal`. These are protocol unit tokens, mapped to UCUM only by the profile. A profile mapping only `millimetre_of_mercury` rejects kPa as `unsupported_unit:kilopascal`. The decoder and runtime never convert kPa to mmHg.

A present user ID must match `binding.expected_user_index` when both are known, otherwise the entire attempt is rejected `wrong_subject`. Wire user ID `0xFF` means unknown and becomes `user_index=None`, as does an absent user field. Even if the binding specifies an expected index, an unknown wire user does not cause rejection: the hub binding names the subject, and qualified frame values are accepted under that binding. Unknown is never treated as user 0 and does not confirm identity. The operator must ensure the hub binding identifies the measured crew member.

Measurement status names are `body_movement`, `cuff_too_loose`, `irregular_pulse`, `improper_measurement_position`, `pulse_rate_exceeds_upper` and `pulse_rate_below_lower`. The profile may list each as rejecting or noted. A rejecting status skips the frame; a noted status preserves a qualified value with a quality note. RFU bits and the reserved pulse range encoding are ignored with status `reserved_bits`; profiles list it in `noted_status` to retain the quality note.

The `hardware` test runs against the simulated cuff twin by default (AGENTS.md section 4). Set `CHC_E07_CUFF_ADDRESS` to a paired device address, with the BLE extra installed and a measurement notification available within the configured timeouts, to run the same test against the real cuff as an extra run. Specification frames and SIM recordings do not demonstrate physical cuff accuracy or compatibility.

## Failure modes

| Failure | Adapter behavior |
| --- | --- |
| Radio absent | Failed `radio_absent`, no invented observation. |
| Device absent | Failed `device_absent`, no invented observation. |
| Disconnect | Connection becomes disconnected. The profile completion is `measurement`: each indication is a whole measurement, so a link that ends after one keeps it; with no measurement received the attempt is failed `disconnected`. |
| Timeout | Same rule as disconnect: failed `timeout` only when no measurement arrived. Calls and acquisition windows are bounded. |
| Malformed frame | Typed frame reason, frame skipped with a quality note; no parsed substitute. Truncation and trailing bytes are rejected. |
| Special values | Missing component with the IEEE special reason, never zero or infinity. Other independently qualified components remain usable. |
| Status flags | RFU flags 5 to 7, status bits 6 to 15 and pulse range value 3 are ignored with status `reserved_bits` and profile note `device_status:reserved_bits`. Known rejecting statuses still skip the frame. Reserved pulse range does not imply an upper or lower range. Truncation and unexplained trailing bytes remain errors. |
| Wrong origin | Rejected `origin_mismatch`; REPLAY and SIM cannot cross a LIVE binding. |
| Wrong subject | Rejected `wrong_subject` when known device and expected user indexes disagree. Unknown `0xFF` relies on the hub subject binding as described above. |
| Unsupported unit | Rejected with the protocol unit code; no guessed conversion. |
| Unsupported capability | Rejected `capability_not_in_profile`, including `blood_pressure` when the profile has no panel for it. |
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

## Sources

Cited by catalog ID following docs/product/NASA-SOURCES.md section 4.

- Why the kit carries a blood pressure cuff and why its reading is kept as attributed device data: RT17 (NASA CHP-IDSP Project Updates, HRP IWS 2025, NTRS 20250000330), quoted at docs/product/ROUTINE.md:172 and :277: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file". RT16 (NTRS 20060051792) places blood pressure in the ISS periodic health status exam (docs/product/ROUTINE.md section 4, note). The instrument is the "Kit blood pressure cuff (upper arm)" of that section.
- Indicator codes `systolic_bp` and `diastolic_bp` and their UCUM unit `mm[Hg]`: config/unit-mapping.json, sourced there to docs/product/CONTRACTS.md:86 (D12).
- Byte layout, flags, SFLOAT encoding, user ID and measurement status bits: Bluetooth SIG Blood Pressure Service and GATT Specification Supplement. This is a non-NASA source, used because no document in the NASA catalog defines a device protocol. BLE on the habitat network follows docs/DEPLOYMENT.md:128 (ICSIS, vehicle-computing.md 3.3 [VC40]).
- A profile's device range is a data check, not a clinical threshold (contracts/reference/input-dictionary.json, fields `systolic_bp` and `diastolic_bp`, validation text). No NASA source in the catalog sets a cuff device range, so each profile states the basis of its own range in `provenance`. How an automated cuff behaves during an irregular rhythm is not sourced (docs/product/DEMO-CONDITIONS.md, kit table, C04 and C05): the `irregular_pulse` status is kept as a quality note or rejection per profile, never interpreted.
