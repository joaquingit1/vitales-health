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
  permitted node/incarnation, recipient-bound opaque origin token, canonical payload/hash and authorized causal refs. POST returns applied/duplicate/quarantined counts
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
  window returns DUPLICATE_COMMAND with the closed accepted_command receipt specified in SYSTEM 5.8; mismatch uses
  current_state null and is never acceptance. Execution never repeats.
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
- Exact proposal: propagate D13 and SYSTEM 8.6 savepoint isolation only for EvaluatorComputationFailure through SYSTEM 5.10.2. Capture accepted input,
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
  sequences or infer nonexecution from an old ledger. E02 fences old credentials/sessions and reconciles current
  consent/revocation checkpoints before any read/export, not only writes; missing history denies sharing until the
  owner grants anew locally.
- Reason: architecture review 5, 6 and 21 require explicit power-loss, deletion and stale-backup semantics.
- Interim: one local hub still uses all three protocols. Multi-node ownership remains fenced until reconciled; old
  queued commands stay pending when deduplication is incomplete. Preserve emergency entry/new manual capture,
  append-only audit, accepted-write durability, owner privacy and all crash/delete/restore tests. No claim of total
  disk-loss recovery without current recovery material, or complete erasure while a key replica remains outstanding.

## R-SYS-22

- Target: scripts/design_lint.py and its tests, owned by the gate maintainer.
- Exact proposal: add a shared-document mode selected for docs/SYSTEM.md and docs/DEPLOYMENT.md. Preserve existing
  section numbering and validate their actual sections, request references, typed signatures, internal/wire declarations,
  decision/A3 links and dependency waves. Keep epic-template checks unchanged for docs/tdd/Exx.md. Recognize an explicit
  `# internal enum` annotation only for non-wire types; validate `# wire: Schema.field` against the effective D12 schema.
  Regression fixtures must catch missing shared requests, forbidden kernel imports, mismatched wire enums, a missing
  provider/cyclic graph, duplicate or renumbered sections, and unchanged epic-template failures. A shared mode is not
  a blanket skip of section or enum checks.
- Reason: the current gate incorrectly requires epic sections in shared architecture and cannot classify internal
  Literal declarations. The review requires a regression-tested fix, not a waiver or document renumbering.
- Interim: run the existing command and report its failures; no greenlight until the gate owner implements and passes
  this change. Keep independent review, exact signature/type checks, request existence, decision traceability,
  section stability, dependency checks and all charter acceptance gates. No safety or validation criterion is waived.

## R-SYS-23

- Target: E01 exchange/registry and each producer's owned replica.schema.json, including R-SYS-3 transport work.
- Exact proposal: implement SYSTEM 5.7.1's versioned ProjectionKey catalog, exact effective-schema field projections,
  typed export/apply ports and per-module registration. Advance source scan/delivery rows atomically; import effects
  and contiguous cursor commit together. Required authorized dependencies wait; omitted dependencies reveal no ID/hash. Separate local audience schemas
  from replica eligibility; revocation uses a signed independent control lane with atomic epoch fencing and receipt.
  Raw origin sequences stay internal, using recipient/policy-bound opaque tokens to avoid excluded-count leakage.
- Reason: audience admission alone did not define how typed replica views are generated or applied.
- Interim: unregistered projections remain off with an unavailable reason; local records and every onboard safety
  capability remain usable. CLI and eventual HTTP use the same schema/signature/consent/grant/epoch checks. Preserve
  cross-patient privacy, no raw clinical ground sync, crash/cursor/deduplication and revocation acceptance tests.

## R-SYS-24

- Target: E01 clock ports, E23 ingest and all time/freshness consumers.
- Exact proposal: adopt SYSTEM 5.2 segment IDs independent of correction revisions, durable adapter-sequence mapping,
  same-segment normalization and unknown-time failure behavior. Implement D14's durable advancing-E receive reference
  for REPLAY. Preserve opaque string mission clock IDs with a separate persisted generation under 5.3.
- Reason: repeated raw timestamps after backward jumps could become fresh; REPLAY lacked its binding D14 branch;
  integer mission clock IDs could not round-trip through the package.
- Interim: unmapped/uncertain time stays unknown and cannot clear safety state. Preserve original times and origin,
  live manual entry, replay labels, coverage checks, D3 LIVE/SIM, D14 stop/loop/restart tests and clock-jump acceptance.
  No timestamp/clock-version wire field is fabricated.

## R-SYS-25

- Target: E01 command ledger, effective error descriptions, every mutating API and E20 queue replay.
- Exact proposal: implement SYSTEM 5.8's generated complete mutation policy, mission-life receipts and closed
  accepted_command receipt in ErrorEnvelope.current_state under D6. Mismatch always has null current_state. Client
  accepts a 409 only on valid receipt kind plus matching immutable key, operation and normalized target.
- Reason: incomplete lists lost approvals after 24 hours, and an error code alone could acknowledge the wrong target.
- Interim: preserve the accepted action and pending client intent until a verifiable receipt is available. Never
  re-execute a ledger hit, mint a replacement key, or substitute a local ledger. Retain identity/body/parameter checks,
  current role/object/field authorization, private HMACs, restore reconciliation and late-retry acceptance tests.

## R-SYS-26

- Target: E26 help/application/ports.py and E28 escalation use case.
- Exact proposal: use SYSTEM 8.1's PlanHelpEntry and PlanEscalation. E28 reserves the public command as a stable pending escalation intent; E26 owns
  its help transaction and derived internal child receipt; only E28's final plan transaction accepts the parent. Both recovery runner and durable help event
  reconcile by escalation ID. Retire incompatible caller-UoW and unkeyed opener variants.
- Reason: two designs assigned opposite commit ownership and could reset help or lose the plan record after a crash.
- Interim: cached help enters immediately; unrecorded escalation is visible and remains pending. Preserve source/flow
  pin, subject/origin, actor/grant, expected revision, occurrence identity, no flow reset and no model/ground gate.
  Consumer acceptance waits for the real E26 port and tests each commit boundary and duplicate event.

## R-SYS-27

- Target: E15/E04/E11/E22/E23 seam ownership and epic dependency tables.
- Exact proposal: build E15-core (create, facts, record_route, view and answer attachment) before E04/E11; E15 owns
  its files throughout. E04/E22 require E23 mission facts. Bind reverse intake/urgency/completion consumers after
  registration. E03 owns ProfileFactsPort; E15 owns EpisodeFacts; E05 owns GapProposalPort. D16 assigns getContext
  to E23, with E02 session and E01 release inputs and an optional assistant status provider.
- Reason: required providers were called optional and reverse dependencies hid a build cycle.
- Interim: fakes only in tests, never runtime acceptance. Keep source/condition and loader checks, real mission/origin
  routing, accepted-record durability, privacy, complete warning/manual entry and no optional worker dependency.
  Acceptance follows the recomputed SYSTEM 9/10 graph; ordinary help does not wait for plan-intent bindings.

## R-SYS-28

- Target: E01 PrivateRecordCoordinator, E09 reports, E03 journal and E20 backup/restore.
- Exact proposal: implement D18 and SYSTEM 5.8.1's common private-store protocol, report source/draft storage split,
  staged version publication, live-source confirmation, deletion cancellation and all-writer backup barrier.
- Reason: report writes crossed two stores without an acknowledgment or orphan protocol.
- Interim: report operations cannot acknowledge acceptance until sealed data and hub refs are durable; extraction
  can remain visibly pending while manual records work. Keep upload/media/size, identity, source-span, unit, consent,
  confirmation, current-key revocation, immutable clinical records and power-loss/deletion/backup acceptance tests.

## R-SYS-29

- Target: E01 task runner and generic Job endpoints; E09/E13/E14/E22 task handlers.
- Exact proposal: implement D17 and SYSTEM 5.10.1's shared submission/lease/generation/completion/cancellation ports.
  Public Job schema and job.progress keep their three kinds; assistant and forecast use internal tasks and existing
  turn/forecast views. Task payloads contain only immutable pinned references and keyed private fingerprints.
- Reason: long work lacked a shared owner and public model-turn Job could not validate.
- Interim: absent workers show unavailable or pending with deterministic/manual alternatives; no fake Job kind or
  synchronous request-path model call. Preserve current grants, cancellation, no late publication, bounded retries,
  duplicate/restart/timeout tests, no assistant safety mutation and every model-off acceptance scenario.

## R-SYS-30

- Target: E01 evaluation/scheduler/health registration and all D13 input producers.
- Exact proposal: implement SYSTEM 5.10.2 EvaluatorComputationFailure, versioned evaluator registry, EvaluationInput,
  isolation/pending/retry APIs and atomic fault audit. Reuse 5.10 timers and clock hooks; no local retry/scheduler.
- Reason: policy alone made each module invent its catch list, pending shape and recovery transaction.
- Interim: only the named computation error is isolated; every storage/I/O/integrity/auth/config/input error aborts.
  Preserve accepted valid capture, active warnings, original time/release, bounded durable retry, visible Health.status
  degradation and tests for marker/audit failure and delayed old evaluations. Detailed health remains R-SYS-9.

## R-SYS-31

- Target: E20 offline queue crypto/store and E02 owner/device revocation integration.
- Exact proposal: implement DEPLOYMENT 5.5.1's per-owner random queue key, independently retained high-entropy unlock
  secret, persisted wrapped key only, memory-only unlocked key, lock disposal, rewrap/rotation and quarantine lifecycle.
- Reason: a non-extractable key alone defined neither persistence nor privacy across browser/owner changes.
- Interim: a locked/unrecoverable queue remains visibly pending under its original owner; sensitive plaintext is never
  stored as a fallback and no officer gets recovery access. Keep cached emergency entry/manual human path, bounded
  capture, owner separation, current replay authorization, exact identity and A/restart/B/A acceptance tests.

## R-SYS-32

- Target: package event catalog and E15/E01 episode notification integration.
- Exact proposal: add episode.changed with Episode data and person-only owning-module audience, delivered after commit
  with current object/field grants, matching the existing Event envelope and release pin. E15 owns emission.
- Reason: the legacy episode-change reference had no canonical filed request.
- Interim: no undeclared event is emitted. Consumers use authorized bounded EpisodeReader queries and internal
  in-transaction domain events. Preserve record durability, no general chart access, urgent/manual flows and response
  validation; event-specific acceptance remains pending until the effective contract is approved.

## R-SYS-33

- Target: E11/E26/E31 urgency entry, attribution and role seams.
- Exact proposal: E11 owns actOnEpisodeUrgency and durable red-flag classification; E26 opens/joins help by episode ref
  without resetting flow, as SYSTEM 8.1/R-SYS-13. Use E02 roster ranks and E31 ClinicalAuthorityQuery for Q4/Q16 acting
  authority and the accepted-assignment audit. Add POST /v1/episodes/{episode_id}/urgency, operationId
  actOnEpisodeUrgency, crew/crew_medical_officer roles, Idempotency-Key and Episode If-Match. Closed request is either
  {action: "start_response"} or {action: "record_disposition", cycle: positive integer, cycle_revision: positive
  integer, disposition: existing HelpAction close disposition enum, rationale: nonempty string}; return 200 Episode.
  Use existing errors MALFORMED_REQUEST, UNAUTHENTICATED, ACTION_NOT_ALLOWED, UNAUTHORIZED_SCOPE, NOT_FOUND,
  INVALID_TRANSITION, STALE_VERSION, DUPLICATE_COMMAND, INVALID_VALUE and VERSION_REQUIRED at their contracted statuses.
  Add Episode allowed_actions start_urgent_response/record_urgent_disposition and optional urgency object with cycle,
  revision, since, signals_count, help_session_ids and nullable disposition {disposition, rationale, by: Actor,
  authority: crew_medical_officer or acting_medical_officer, role_assignment_id: id or null, at: instant}.
  Validate Q4/Q16 authority and assignment inside the command transaction; no extra session role is invented.
- Reason: several in-design legacy references were never filed, despite shared ownership being settled.
- Interim: retain fixed help before auth/storage and existing valid record entry; an uncontracted disposition endpoint
  stays off with the case open/visible until its exact schema is accepted. Preserve current authority, reason, no
  assistant lowering, Q16 clinician_assessed restriction, object privacy and urgent-entry/idempotent-recovery tests.

## R-SYS-34

- Target: E03 questionnaires.json ownership and E04 intake content.
- Exact proposal: E03 remains the sole file owner; E04 submits sourced intake section changes to E03 for integration,
  including E04's registered semantic checks. No simultaneous cross-owner file edits.
- Reason: the legacy intake-content ownership reference was unfiled.
- Interim: existing validated intake content remains active; missing content is visibly unavailable, never a sample.
  Preserve provenance, mandatory semantic/schema checks, last-good activation, pinned sessions and configuration-edit
  acceptance tests. No production fake supplies clinical content.

## R-SYS-35

- Target: E23 mission-event ingest and authenticated habitat adapters.
- Exact proposal: add an explicit adapter-authorized mission-event ingest operation whose body is the existing
  MissionEvent input with attributable adapter/channel origin, accepted event-type allowlist and Idempotency-Key;
  POST /v1/adapters/{adapter_id}/mission-events, operationId ingestAdapterMissionEvent, x-roles adapter, request
  requests.schema.json#/$defs/MissionEventCreate, response 201 mission-event.schema.json. Adapter ID and event type
  must belong to its authenticated configured allowlist; preserve the requested observed time and origin. Use existing
  errors MALFORMED_REQUEST, UNAUTHENTICATED, UNAUTHORIZED_SCOPE, ADAPTER_NOT_TRUSTED, ORIGIN_MISMATCH,
  DUPLICATE_COMMAND and INVALID_VALUE with their contracted statuses. Never infer vehicle alarms from model output.
- Reason: vehicle-event ingestion was referenced by an unfiled legacy request; current adapter permissions cover readings.
- Interim: no adapter event endpoint is enabled. Authorized crew record the configured mission event through the
  existing API; retain emergency storm routing, source attribution, unit/reading validation, durable identity and
  ground-independent safety acceptance. Unknown telemetry never becomes an inferred alarm.
