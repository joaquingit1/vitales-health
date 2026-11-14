# Shared architecture requests

Binding decisions are in COORDINATOR-DECISIONS.md Part A; this file records exact propagation work and pending
extensions. Settled decisions are not questions. A pending schema extension never appears as an undeclared wire field.
Every interim below retains the named checks and acceptance criteria. A disabled extension is not a passed acceptance
scenario. SYSTEM section 13 references this file only.

## R-SYS-1

- Target: BUILD-CHARTER 3.6 and AGENTS code error rule.
- Exact proposal: propagate D5: "Typed module errors use the closed ErrorEnvelope on the wire; a sanitized RFC 9457
  view is available to logs." Remove the former proposal to add problem-details fields to ErrorEnvelope.
- Reason: settled error-format conflict, not a new wire design.
- Interim: implement D5 exactly; retain error-code/status/retryability validation, correlation IDs and privacy-safe
  logging. Contract tests use ErrorEnvelope; no alternate error body is enabled.

## R-SYS-2

- Target: CONTRACTS 11, release ConfigName and E29 export-mapping configuration/schema.
- Exact proposal: add signed release document `export-mapping.json` with `codes[]` entries `{indicator_code, system,
  code, display, unit_ucum, basis, source_ids, to_be_set_by}`, `local_codes[]` of the same shape, and `origin_labels`
  for LIVE/REPLAY/SIM. Validate unique indicator/system/code tuples, known UCUM units and every exported indicator.
  E29 owns the mapper; E01 registers its check. Export is FHIR R4 4.0.1 as already specified in BUILD-CHARTER 2.
- Reason: mappings are domain content and cannot be a loose unversioned file. The former R5 charter request is obsolete.
- Interim: FHIR export is off with mapping-unavailable status until the signed document is supported. Onboard records,
  approved handoffs and catch-up still work. Preserve consent, original units/lineage and R4 validator acceptance;
  do not claim FHIR acceptance while off.

## R-SYS-3

- Target: API Sync tag, node enrollment and replication projection schemas.
- Exact proposal: add mutually authenticated node-only endpoints `GET /v1/sync/status`,
  `GET /v1/sync/entries?stream_id=&after=&limit=` and `POST /v1/sync/entries`. Status returns node/incarnation,
  software version, release ID, recipient stream vector, visible queue counts and signed audit checkpoint. Each
  projection envelope has stream ID, policy epoch, delivery sequence, recipient, purpose, record projection type,
  origin triple, canonical payload/hash and authorized causal refs. POST returns applied/duplicate/quarantined counts
  and the contiguous delivery cursor. Bounds come from SYSTEM 5.11 and declared schema maxima. Errors use existing
  MALFORMED_REQUEST, UNAUTHENTICATED, UNAUTHORIZED_SCOPE, PAYLOAD_TOO_LARGE and INVALID_VALUE codes.
- Reason: raw-origin streams cannot enforce consent or advance safely through exclusions (DEPLOYMENT 4.3).
- Interim: no node session or HTTP sync route is exposed. Authenticated CLI `.chsync` uses the exact recipient policy,
  schema, signature, encryption, incarnation, deduplication and cursor checks. Retain multi-node privacy, crash and
  outage acceptance; no file-transport exception to consent.

## R-SYS-4

- Target: device enrollment endpoints and Session device binding.
- Exact proposal: operator `POST /v1/devices/enrollments` returns `{code, expires_at}`; code-authenticated
  `POST /v1/devices` accepts `{code, kind, name}` and returns `{device_id, ca_certificate}`; operator
  `DELETE /v1/devices/{device_id}` revokes it. Add optional `device_id` to SessionCreate/Session with server validation.
- Reason: enrollment and selective revocation are required for LAN clients and adapter hosts.
- Interim: local authenticated CLI enrollment/revocation; revocation ends all sessions and rotates affected credentials.
  Keep TLS trust, one-time expiry, rate bounds, audit, owner-only queued-command access and lost-device privacy tests.
  Selective HTTP revocation stays off until its schema is applied.

## R-SYS-5

- Target: E31 authority port, E32 formulary/proposal/confirmation schemas and optional Session acting view.
- Exact proposal: propagate Q4/Q11/Q14/Q16 and SYSTEM 8.4. Product `confirmation_authority` is
  `any_trained_crew` or `crew_medical_officer`, with sourced required training; the second independent acting path is a
  medicine named by the subject's active plan step for the condition. Confirmation carries proposal ID/revision,
  recommended and label quantities, engine context fingerprint, acting flag and accepted assignment reference.
  Optional session display is `{role_assignment_id, for_subject_person_id, since}` or null; it grants no authority.
- Reason: remove obsolete label-dose-only and pending-owner restrictions. No allergy/interaction override is allowed.
- Interim: authorization and audit already follow the settled decisions through typed internal context. Wire views
  use accepted effective contracts, otherwise display acting through the existing assignment view, without adding
  Session fields. Keep engine range/coverage/degradant/device/product/dose-limit checks, stock transaction, recomputation
  on changed inputs, identity checks and Q16 resolution restriction. Never reintroduce a ground/pharmacist gate.

## R-SYS-6

- Target: environment profiles, HelpSession/Episode, supply stock/use and mission-event shapes.
- Exact proposal: per profile add `locations[]` with `{location_id, label, kind, node_id}`; kind is habitat_module,
  medical_bay, pressurized_rover, transit_vehicle or eva_site. Add nullable patient location to help/episode,
  custody location to stock/use, mission phase, and typed crew_departed/crew_arrived/custody_transferred events with
  subject IDs, locations and transfer ID/epoch. SYSTEM 8.5.1 is the only ownership-transfer protocol.
- Reason: clinical applicability and stock ownership must follow physical location without inventing cross-site stock.
- Interim: one location/custody per environment at an independent hub. Cross-site custody/routine transfer is off until
  location contracts and fenced-transfer acceptance pass. Each site's help, warnings, routine, manual records and own
  kit remain usable. Preserve source conflicts, nonnegative stock, no duplicate timer effects, privacy, crash recovery
  and T6 tests; do not count a disabled handover as passed T6 acceptance.

## R-SYS-7

- Target: CONTRACTS 18 event audiences and E26/E01 delivery tests.
- Exact proposal: help-session events go only to an authenticated reader with the matching object/field grant; open
  helper membership grants only the session's authorized projection. Other crew/station receive broadcast only.
  Officer status is not a general grant. Ground copies appear only after authorized delivery, never at queue time.
- Reason: audience metadata and current grants must match HTTP privacy and PRD 144 to 147.
- Interim: existing role-filtering and owning-module audience rules enforce this without new public fields. Retain
  zero cross-patient leakage, after-close denial, cache invalidation and ground-delivery-only tests.

## R-SYS-8

- Target: CONTRACTS 5 idempotency text and durable-command acceptance.
- Exact proposal: propagate D6 and SYSTEM 5.8. Mission-life ledger binds actor/key to operation, normalized target,
  semantic parameters and body fingerprint. A mismatched use conflicts. Matching replay after the response-cache
  window returns DUPLICATE_COMMAND with a currently authorized result reference/state only; execution never repeats.
- Reason: retries after long outages and identical bodies on different resources must not lose or misattribute care.
- Interim: existing code/envelope can express the result. Preserve current authentication, per-object/field visibility,
  private HMAC fingerprints, no private response cache, permanent deduplication and restore reconciliation tests.

## R-SYS-9

- Target: Health, TodayView and HabitatView schemas and E03 attention layer.
- Exact proposal: add optional Health `evaluators[]` entries `{component, state, code, since}` with component rules or
  pathways, state ok/degraded, allowlisted code EVALUATION_UNAVAILABLE or null and nullable UTC since; add nullable
  `evaluation_notice` `{since}` to the two views. No private identifiers or error text is exposed.
- Reason: the current closed Health cannot carry evaluator detail or the original failure time.
- Interim: emit only declared Health fields with overall status degraded; station renders a configured general
  evaluation-unavailable warning. Local diagnostics show actual evaluator/since from internal health. Detailed wire
  notice stays off. Keep valid-input capture, durable retry, unchanged active warnings, storage-fault propagation and
  visible failure acceptance. Never fabricate since from the time the browser first saw the fault.

## R-SYS-10

- Target: root pyproject.toml (engine owner), BUILD-CHARTER gate commands.
- Exact proposal: uv workspace members hub, workers/*, adapters/*, eval/bench and eval/harness; add hub-specific
  validation and `--project hub` gates. E01's registry/import-contract generators discover owned module files by
  convention; no epic edits central assembly or import-linter entries to register itself.
- Reason: the engine project owns the root; architecture integration must not require cross-owner file edits.
- Interim: standalone hub uv project and locked path sources. Keep strict typing/lint, dependency direction, generated
  schema guards and hermetic test selection. Do not change engine code, test configuration or tolerances.

## R-SYS-11

- Target: E22 consent state/API and participation-rules display policy.
- Exact proposal: implement D10's GET/PUT owner-only consent resource and Q15 default exclusion. Add optional sourced
  publication policy `{update: "live" or "day_boundary"}`; no contributor-count field is public. Consent revocation
  removes the member immediately even under delayed publication.
- Reason: delayed aggregation is not consent and small crews can infer participation.
- Interim: default every absent consent to off, publish zero/no-activity aggregate and keep personal streaks working.
  No implicit opt-in in fixtures or migration. Retain per-origin separation, no value-dependent rewards, aggregate-only
  output, revocation/cache tests and consented goal acceptance through the D10 effective contract.

## R-SYS-12

- Target: E24 evaluator isolation and all input-producing modules.
- Exact proposal: propagate D13 and SYSTEM 8.6 savepoint isolation only for typed computation failures. Capture accepted input,
  pending evaluation marker and durable retry together; preserve prior warnings. Storage/I/O/integrity errors abort
  the whole command and remain visible. Missing content or invalid input is never downgraded to a retry warning.
- Reason: downstream computation must neither lose real input nor swallow storage failures.
- Interim: use this shared mechanism; no local retry transaction. Keep capture durability, safety-state invariants,
  bounded retry, fault visibility and kill/disk-full/corrupt-configuration tests.

## R-SYS-13

- Target: E26/E11 red-flag seam and HelpSessionReader.
- Exact proposal: E26 owns opening/joining help; E11 owns red-flag urgency and consumes help changes after commit.
  `open_from_red_flag` uses episode ID as ref, joins without resetting an open flow; `context` and `readable_by` are
  E26 ports with SYSTEM 8.3 object grants.
- Reason: transaction and port ownership differed between provider and consumer designs.
- Interim: follow SYSTEM 8.1 with fake ports only in tests. Preserve cached first-screen latency, idempotent help
  entry, no model/ground dependency, no urgency lowering by help closure and privacy tests.

## R-SYS-14

- Target: ConfigRelease schema.
- Exact proposal: optional `provenance` object `{source: "local" or "ground_bundle", bundle_sha256: sha256 or null,
  signer_key_id: string or null, carried_by_message_id: id or null}`; validate source-dependent required values.
- Reason: release transport provenance should be inspectable without reading internal audit.
- Interim: reference-only audit holds bundle hash, signer and carrier with each activation; return existing closed
  ConfigRelease plus D8 ETag. Preserve signature verification, all loader checks, atomic activation, last-good rollback,
  active warning/session pins and activation-role tests.

## R-SYS-15

- Target: E27 guidance deliverable and E11/E12/E13/E26/E28/E30/E33 dependency declarations.
- Exact proposal: adopt SYSTEM 8.2 types and rule as `E27-guidance`, hard prerequisite, with unknown-provider facade
  and Q1/Q7/Q18 review labels. Keep the rest of E27 dependent on E02/E23; early guidance needs only E01 kernel.
- Reason: guidance was mandatory in runtime prose but missing in the graph and startup resolution.
- Interim: build against bounded fakes, then gate consumers on the real rule; a fake cannot pass runtime acceptance.
  Preserve source/purpose/hash checks, per-use stock, fresh-or-due skills, operator authority, substitute validation,
  fixed emergency entry, clinical-review labels and unknown-fact tests. No skip of loader checks when E10 is absent.

## R-SYS-16

- Target: BUILD-CHARTER ground-link row and E29 packaging.
- Exact proposal: none outstanding. The charter already selects µD3TN or dtn7-rs and excludes archived HDTN.
- Reason: the original request cited an obsolete charter version.
- Interim: retain that choice with file transport fallback; keep bounded calls, signed/recipient-encrypted bundles,
  consent filtering, offline queue and link-independence acceptance.

## R-SYS-17

- Target: ConfigRelease response headers, activation If-Match and configuration examples.
- Exact proposal: apply D8's ETag to create/detail/activate headers and document its release/object/activation
  generation components. Detail examples supply the current token; activation examples send it. No JSON revision field.
- Reason: package issue 2; activation currently requires a token clients cannot obtain.
- Interim: D8 effective OpenAPI exposes the header; keep base copies unchanged. Retain 428 missing-token and 409 stale
  tests, cross-target activation races, idempotent retry, editor/operator roles and open-session pin acceptance.

## R-SYS-18

- Target: listPlanRuns, getPlanRun, related events and station UI authorization.
- Exact proposal: implement D9. Station returns UNAUTHORIZED_SCOPE for clinical runs; authenticated person access is
  own record or explicit per-object/per-field grant. Station renders fixed/public plan content and broadcasts only.
- Reason: package issue 3; all-run station visibility conflicts with PRD privacy.
- Interim: narrower access uses the existing error envelope. Retain help before authentication, accepted-task access,
  authorized clinical work, privacy tests on reads/events/cache/files/ground and no ground dependency.

## R-SYS-19

- Target: effective unit-mapping document/schema, ConfigName and contextual conversion adapter.
- Exact proposal: apply D12 with mappings `{indicator_code, wire_unit, ucum, conversion, rounding_places,
  rounding_mode, basis, source_ids, to_be_set_by}`; conversion is identity, dimensional or ppm_to_mmhg. Reject duplicate
  indicator/wire pairs, unknown UCUM, unsupported conversion and missing provenance. Contextual conversion requires
  PressureContext; sensor rounding uses places 4 and half_even.
- Reason: wire bpm/mmHg/ppm are not UCUM spellings and gas conversion needs measured or sourced assumed pressure.
- Interim: D12 signed effective release supplies mappings; no heuristic default, hardcoded alias or pressure fallback.
  Keep originals, canonical units, provenance, independent golden conversions, property tests and no-nonfinite checks.

## R-SYS-20

- Target: environment-profile comms_queue, ground envelope lifecycle and examples.
- Exact proposal: apply D11's optional sourced bundle_lifetime object; validate its positive bounded seconds and
  provenance. Implement re-enveloping of unacknowledged approved snapshots with stable application identity and
  current grant check, preserving priority and original approval order.
- Reason: no field supports the old prose lifetimes and expiry could strand unacknowledged content.
- Interim: missing lifetime disables BPv7 transmission visibly, preserves local approved queue and existing simulated
  delivery; no magic duration. Retain link independence, consent, no lost approved records, duplicate ACK and bounded
  outage/retry/expiry tests. BPv7 acceptance waits for the configured field.

## R-SYS-21

- Target: E01/E03/E20 recovery and private-key implementation evidence.
- Exact proposal: implement SYSTEM 5.8.1 two-store barriers, 7.4 per-entry vault revocation and DEPLOYMENT 4.2.1
  incarnation/deduplication recovery. Backup manifest pins both stores, reference watermark, audit head, key-vault
  generation and receipt checkpoint. Never snapshot wrapping keys into a data backup. Restore cannot reuse origin
  sequences or infer nonexecution from an old ledger.
- Reason: architecture review 5, 6 and 21 require explicit power-loss, deletion and stale-backup semantics.
- Interim: one local hub still uses all three protocols. Multi-node ownership remains fenced until reconciled; old
  queued commands stay pending when deduplication is incomplete. Preserve emergency entry/new manual capture,
  append-only audit, accepted-write durability, owner privacy and all crash/delete/restore tests. No claim of total
  disk-loss recovery without current recovery material, or complete erasure while a key replica remains outstanding.
