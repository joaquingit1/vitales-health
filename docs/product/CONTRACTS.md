# Shared data and workflow contracts

These contracts are the planning baseline for all epic TDDs and for the hub's API. They specify product semantics; implementation choices such as the UI framework and adapter transport remain in the relevant TDD. Sections 11 to 21 specify the configuration documents, the deterministic engines and the API in enough detail to build and test the hub from this package alone. The machine readable form is in [api/](api/README.md): `openapi.yaml` (OpenAPI 3.1), one JSON Schema per object and per configuration document, and illustrative examples marked SIM or REPLAY. Examples are not seed data.

## 1. Identity, time and provenance

Every clinical object has an immutable ID, patient ID, mission ID, schema version, creation time, creating actor and revision lineage. A device operator is not necessarily the person measured. A synthetic character is never the same patient as the live presenter. Do not join records by display name or facial similarity.

Use UTC instants plus the original offset where known. Preserve `observed_at`, `entered_at` and `received_at`; a late arrival does not become a fresh measurement. Approximate symptom onset has an explicit precision field. Mission-relative time and a device monotonic sequence can supplement UTC but cannot silently replace it. Unknown time zone, device drift and clock corrections are recorded.

`origin_mode` is LIVE, REPLAY or SIM. `acquisition_method` is self_report, manual_instrument, device, camera_estimate, imported_report, clinician_entry or derived. `quality_status` is accepted, rejected, uncertain or not_assessed. These are independent dimensions. LIVE means acquired from a real source; it does not mean clinically accurate. Accepted means it passed the specified data-quality checks, not that a diagnosis is confirmed.

Observations never contain a fake numerical sentinel for missingness. Zero is a value. An absent, declined, unavailable or failed input is a request/answer state, not a zero observation. A corrected record links to the superseded record; it does not erase it.

## 2. Core objects

| Object | Required contents and invariants |
|---|---|
| Person | ID, preferred label, identity namespace, optional relevant profile, consent references |
| Mission | ID, phase, gravity, locations, schedule/time configuration (including versioned mission clock configurations and the crew roster by mission day), policy release |
| ConsentGrant | Actor, subject, recipient/purpose, allowed fields/actions, validity, revocation |
| Device | ID, type/model, adapter/version, units, calibration metadata if known, availability |
| Capability | Code, supported methods, device/operator requirements, gravity/context applicability, current status, expected duration, evidence/pathway references |
| Episode | Patient, concern, onset/precision, symptom answers, state, urgency state, owner, follow-up tasks |
| Answer | Question code/version, presence state (present, absent, unknown, declined), typed value if applicable, actor, time, sensitivity and supersession. Symptom answers carry an optional `body_region` and `laterality` (section 7) |
| DailyCheck | Patient, mission day, origin mode, references to the symptom, sleep and mood answers, optional parts (reaction test, camera and kit readings, band confirmation, whether a journal entry exists), state, and the device estimates its confirm questions show (section 11) |
| Observation | Code, value, original/canonical unit, times, patient/operator, device/method, origin, quality, context, evidence lineage, optional `body_region` |
| MeasurementRequest | Episode, capability, reason, the decision the result would change, source/pathway, requester, operator assignment, consent, state, attempts and results, optional `body_region` |
| CapabilityGap | Episode, the needed examination or capability code, reason (`no_capability`, `device_absent`, `operator_absent`, `context_inapplicable`), the consequence (what stays unresolved), alternatives offered (import an existing report, medical review summary), optional `body_region`, state (`open`, `addressed`). Created by the controller when a request ends `unavailable` or a needed test has no capability; the model can only propose one |
| Report | Immutable file hash, original name/type, patient, source/author, examination and report dates, access scope |
| ExtractionDraft | Report source span, proposed code/value/unit/date, extraction version, review status; cannot act as confirmed finding |
| ConfirmedFinding | Accepted extraction or entered finding, source span, confirmer/time; clinician interpretation separately attributed |
| Assessment | Model/reviewer origin, state snapshot, evidence release, findings, hypotheses, contradictions, missing evidence, next step, validation status. Each finding, hypothesis and missing-evidence entry carries an optional `body_region` |
| ClinicalReview | Reviewer identity/role, assessment/findings reviewed, decisions, signature/attribution, unresolved items |
| FollowupTask | Episode, action, due time/policy, owner, state, completion evidence or cancellation reason |
| Baseline | Patient/code/context cohort, included observation IDs, method/version, sample count, exclusions and value/range |
| Forecast | Model version, input IDs, transformations, horizon, intervals, origin inheritance, experimental status |
| HandoffSnapshot | Immutable approved field selection, episode snapshot time, consent reference, recipients and attachments |
| DeliveryAttempt | Snapshot ID, transport/simulator identity, scheduled time, attempt/receipt states, idempotency key |
| AuditEvent | Event ID/type/version, actor, object references, timestamp, correlation/causation IDs, safe metadata |
| CheckCompletion | Immutable participation event: patient, mission, origin mode, basis (daily check or concern), mission day, rule version, status. No value or outcome field (section 8) |
| StreakView | Derived, private to the person: current and longest streak, completed days, own day history (section 8) |
| CrewGoalView | Derived crew total: completed checks and checks due in the seven mission days ending today. No per-person data (section 8) |
| PatchAward | Derived: the 7, 14 and 30 completed-check patches a person has earned (section 8) |
| EnvironmentProfile | One record of the mission's environment (`station_leo`, `transit`, `lunar_surface` or `mars_surface`): label, place, walk word, the hazard, limit, warning definition, sensor type, sensor channel and routine item IDs it switches on, and its ground delay words. Exactly one record is active; a change is a new record that starts `proposed` and becomes `active` only after crew confirmation (section 12) |
| ActivityOverlay | Per person: `eva`, `post_eva`, `exercise` or `sleep`, with planned start, start, end and source (section 12) |
| MissionEvent | Something the engines react to: a walk starting or ending, a landing, a space weather alert, a local water source. Types, labels and who may record them come from environment-profiles.json (section 12) |
| GroundLink | The link to the ground: `status` (`up`, `delayed`, `down`), `one_way_delay_s`, `next_contact_at`, `last_contact_at`, `source` (`schedule`, `measured`, `demo_switch`) and `environment_id`, with the delay words from `earth_delay` in environment-hazards.json and their source IDs. Nothing on board waits on it (section 12) |
| SensorChannel | One habitat or wearable sensor feed: sensor type, hazard, location, adapter, origin mode, input unit, expected reporting interval, stale time and derived freshness (section 13) |
| SensorReading | Immutable reading from a channel in the sensor type's canonical unit, with its original value and unit, conversion, aggregation, times, origin mode, adapter sequence and replay block. It describes the place, never a person's observation or exposure (section 13) |
| SensorLimit | One row of sensor-limits.json: the sourced hazard level it copies, environments, role, level, comparator, value (or pending), aggregation, minimum coverage, clear condition with dwell, card mark, and source IDs or "team default" (section 13) |
| SensorEvaluation | The sensor monitor's result for one sensor type: status, level, the limit crossed, aggregated value, window and coverage (section 13) |
| SensorStatus | What a sensor card shows: display value, context words, evaluation, shown level, limit marks, effects, what to do and citation (section 13) |
| Warning | A warning raised by a rule, a sensor limit or the assistant: definition, level, `raised_by`, evidence, body regions, plain steps, headline fragment, state and clear reason (section 14) |
| RoutineTask | One routine item of routine-schedule.json resolved for the active environment: base and effective cadence, minutes, instrument, steps, region, why this often and its basis, and which triggers run automatically (section 15) |
| RoutineOccurrence | One due instance of an item for one person: mission day, display group, due window, state, cause and outcome (section 15) |
| RoutineOverride | A temporary change to one item's cadence from a trigger, with its binding, reason, source and end (section 15) |
| RoutineSchedule | Derived per person and environment: grouped occurrences, counts, the week, active overrides and misses. Never written through an API (section 15) |
| HelpSession | One "Someone needs help" session: subject, opener, helpers, place, body region, the current node of the responder flows, path, fixed steps with tick times, log, crew call, assistant line, pinned configuration and state (section 16) |
| GroundMessage | A log of a help session sent to the ground: `queued`, `sent` or `acknowledged`. Sent when the link allows; nothing on board waits for it to be sent, acknowledged or answered (section 16) |
| CrewBroadcast | The emergency line on every crew screen: who needs help, who is helping, where (section 16) |
| SubjectSummary | The readings panel of a help session: the subject's latest readings, and allergies and medicines by name only. There is no dose field (section 16) |
| Conversation, AssistantMessage | An assistant conversation pinned to a model release and a configuration release; each assistant message carries text or one validated ModelAction (sections 6 and 17) |
| Insight | A pattern the assistant found in the person's own records. Evidence is the person's own records only; habitat readings appear only as separate habitat facts (section 17) |
| AssistantStatus | The local model service: `running`, `stopped`, `loading` or `unavailable`, with its model release (section 17) |
| Event | One entry of the event stream: sequence number, type, scope, configuration release and data (section 18) |
| ConfigDocument, ConfigRelease | A versioned configuration document, and the pinned set of document versions the hub runs under (section 11) |
| Session | A signed-in session: actor, person and roles. Identity and roles come from the session only (section 20) |
| KitMapView, KitCheck | The kit map computed for one environment, and the kit gate result every measurement request carries (section 21) |
| TodayView, HowYouAreView, HabitatView, RecordsView, GroundInbox | Screen projections joined by the hub from state and the active configuration (section 19) |

Clinical observations and documents require access control. Audit logs store references and necessary state changes rather than full private symptoms, journal text, raw prompts or document contents by default. Privileged diagnostic logs have explicit retention and access limits.

## 3. Example observation

```json
{
  "schema_version": "1.0",
  "observation_id": "obs-example-001",
  "patient_id": "demo-person-a",
  "operator_id": "demo-person-a",
  "mission_id": "demo-mission",
  "episode_id": "episode-example-001",
  "code": "pulse",
  "value": 76,
  "unit": "bpm",
  "original_value": 76,
  "original_unit": "bpm",
  "observed_at": "2026-11-13T18:00:00Z",
  "entered_at": "2026-11-13T18:00:05Z",
  "origin_mode": "SIM",
  "acquisition_method": "manual_instrument",
  "quality_status": "accepted",
  "quality_rule_version": "demo-input-1",
  "device_id": "simulated-oximeter",
  "context": {"activity": "rest", "gravity": "Earth"},
  "supersedes": null
}
```

This is a simulated example, not a collected measurement. A device adapter uses the same contract but must supply its actual method, subject and device. A failed reading creates an attempt event and reason, not this object with a made-up value.

## 4. States and transitions

### Measurement request

`proposed → awaiting_acceptance → accepted → in_progress → completed`

From an appropriate nonterminal state it may end as `unavailable`, `declined`, `cancelled` or `failed`. Only `completed` with one or more accepted observations counts as a usable result. An attempt can capture a rejected observation for technical analysis without promoting it to the accepted chart context. A retry creates a new attempt with the same logical request and a new attempt ID; it cannot change failed history into success retrospectively.

An assigned crewmate must accept the task before viewing the necessary measurement details. Cancellation stops acquisition where possible, marks any late result as arriving after cancellation, and requires explicit review before use. Device-side inability to cancel must be visible.

### Episode

`draft → open → awaiting_information / awaiting_review / followup → resolved`

`acknowledged` and `reviewed` are events, not substitutes for resolved. An episode can return to open when symptoms recur or important evidence changes. Resolution requires actor, reason and a clear distinction between “measurement anomaly not reproduced,” “symptoms reported resolved,” and “clinician assessed.” The app cannot infer medical resolution solely from a normal value. `awaiting_review` waits for a reviewer on board: the crew medical officer or the person the crew designates. A ground review may be recorded when it arrives, but no episode state needs one.

Urgency is a separate state machine: `none → needs_urgent_review → active_response → disposition_recorded`. The designated authorized person on board records disposition and rationale; no ground acknowledgement is needed. Model output cannot close it. A narrative model flag can request review but cannot generate its own threshold or provide proof of safety.

### Daily check

`started → submitted`, or `started → abandoned` when the mission day ends without a submission. Submission requires an accepted answer, in any presence state, to the symptom question, sleep and mood. A submitted check can still receive its optional parts (reaction test, camera and kit readings, journal); they never change its completion. Check completions, the streak, patches and the crew goal are specified in section 8.

### Evidence and model releases

`discovered → captured → extracted → technically_checked → clinically_reviewed → approved → superseded/withdrawn`.

Review status can branch: a modeling document may be approved for risk-context use while disallowed for patient instructions. Approval belongs to a version and purpose. A release pins all passage, pathway, model, prompt and tool versions; publication does not overwrite earlier release contents.

### Sharing

`draft → previewed → approved → queued → in_transit → delivered → receipt_recorded`.

`delivery_failed`, `cancelled` and `expired` remain explicit. The simulator declares itself in every delivery event. A locally generated file is exported, not delivered. A transport receipt is not a clinical review. A reviewer reply records its own actor, time and scope. A handoff is optional and is a side effect of the onboard path: nothing on board waits for it to be delivered, received or answered, and it leaves when the ground link allows (section 12). Its `leaves_at` and `catch_up` are in section 26.

### Environment, sensors, warnings, routine and help

Environment changes (section 12), sensor freshness and evaluation (section 13), warnings (section 14), routine occurrences and overrides (section 15), and help sessions with their ground messages (section 16) have their own state rules in those sections. Assistant turns and insights are in section 17.

Nothing on board waits for the ground. No responder flow, help session, episode, warning, measurement request or onboard plan blocks on a ground reply or acknowledgement; every transition that matters on board happens without one. Ground messages (section 16) and handoffs (Sharing, above) are logs sent when the link allows (section 12).

## 5. API intents

The table lists every intent. [api/openapi.yaml](api/openapi.yaml) holds each operation with its request body, responses, roles, headers and examples; section 19 maps screens to these endpoints. All patient-scoped endpoints enforce session identity and permissions server-side. Client/model-supplied IDs are not authorization.

| Intent | Suggested endpoint | Semantics |
|---|---|---|
| Read own mission/profile | `GET /v1/context` | Scoped projection, no journal by default |
| Start daily check | `POST /v1/daily-checks` | Opens today's check for the session person; one per person and mission day |
| Submit daily check | `POST /v1/daily-checks/{id}/submit` | Validates the three required answers; the controller emits the CheckCompletion; returns the person's own StreakView |
| Start concern | `POST /v1/episodes` | Creates episode with idempotency key |
| Add/correct answer | `POST /v1/episodes/{id}/answers` | Immutable answer revision |
| Record observation | `POST /v1/observations` | Validates units, identity, mode and context |
| Read capabilities | `GET /v1/capabilities` | Actual availability plus applicability |
| Propose request | `POST /v1/measurement-requests` | Draft request; no acquisition yet |
| Accept/decline/cancel request | `POST /v1/measurement-requests/{id}/actions` | Validated state transition and actor |
| Adapter outcome | `POST /v1/measurement-requests/{id}/attempts` | Trusted adapter identity and unique attempt sequence |
| Upload report | `POST /v1/reports` | Size/type limits, immutable content, no automatic accepted findings |
| Confirm extraction | `POST /v1/reports/{id}/confirmations` | Field-level confirmation with source spans |
| Begin assessment | `POST /v1/episodes/{id}/assessments`, `GET /v1/assessments/{id}` | Asynchronous job over pinned state snapshot |
| Read or cancel job | `GET /v1/jobs/{id}`, `POST /v1/jobs/{id}/cancel` | Progress and result; cancelling does not erase completed events |
| Urgent entry | `POST /v1/urgent-events` | Alias of `POST /v1/help-sessions`: same body, behavior and response, with `entry.kind` `urgent_event_alias`. Minimal local path; UI available without inference (section 16) |
| Record review | `POST /v1/episodes/{id}/reviews` | Authorized reviewer on board or on the ground, attribution and unresolved tasks. No episode state needs a ground review |
| Preview/share handoff | `POST /v1/handoffs` then `POST /v1/handoffs/{id}/approve` | Optional. No send without explicit user action; nothing on board waits for delivery, receipt or reply |
| Read evidence | `GET /v1/evidence/passages/{id}` | Only allowed release and use |
| Read own streak | `GET /v1/me/streak` | The session person's StreakView; takes no person ID |
| Read own patches | `GET /v1/me/patches` | The session person's PatchAwards |
| Read crew goal | `GET /v1/crew-goal` | CrewGoalView totals only; any breakdown parameter returns `AGGREGATE_ONLY` |
| Rebuild participation | `POST /v1/participation/rebuild` | Operator role; rebuilds every participation view from events; returns event counts and a result hash, never per-person views |
| Subscribe to status | `GET /v1/events` | Scoped event stream with resumable cursor; never carries another person's participation events |
| Research/import | `GET`, `POST /v1/research/sources` | Separate editor privileges; no patient context |
| Health, sign in and out | `GET /v1/health`, `POST /v1/sessions`, `GET`, `DELETE /v1/sessions/current` | Health needs no session and reveals no person data. Sign in exchanges a local credential for a bearer token (section 20) |
| Read the roster | `GET /v1/crew` | Names and roles only; no health values, no participation |
| Read configuration | `GET /v1/config`, `GET /v1/config/{name}` | The active release and each document with its version as ETag (section 11) |
| Manage releases | `GET`, `POST /v1/config/releases`, `GET /v1/config/releases/{id}`, `POST /v1/config/releases/{id}/activate` | Editor proposes; the loader validates; editor or operator activates atomically (section 11) |
| Read mission and environment | `GET /v1/mission`, `GET /v1/environment` | Mission clock, active profile, pending change, crew and ground link (section 12) |
| Change environment | `GET`, `POST /v1/environment/changes`, `POST /v1/environment/changes/{id}/actions` | Proposed, then confirmed or rejected by the crew (section 12) |
| Overlays | `GET`, `POST /v1/overlays`, `POST /v1/overlays/{id}/end` | The session person's walk, after-walk, exercise and sleep (section 12) |
| Mission events | `GET`, `POST /v1/mission-events` | Recorded by the roles in environment-profiles.json (section 12) |
| Ground link | `GET /v1/ground-link`, `POST /v1/demo/ground-link` | Every client shows status and next contact. The POST is demo only (served only in demo mode) and flips the link; nothing else changes when it is down (section 12) |
| Sensor cards and data | `GET /v1/sensors`, `GET /v1/sensors/{sensor_type_id}`, `GET /v1/sensors/{sensor_type_id}/series`, `GET /v1/sensor-channels`, `GET /v1/sensor-readings/{id}` | Computed by the sensor monitor (section 13) |
| Adapter ingest | `POST /v1/adapters/{adapter_id}/readings` | Adapter token; per reading accept, duplicate or reject (section 13) |
| Warnings | `GET /v1/warnings`, `GET /v1/warnings/{id}`, `POST /v1/warnings/{id}/actions` | Acknowledge, resolve, dismiss or note, as `allowed_actions` permits (section 14) |
| Routine | `GET /v1/routine/today`, `GET /v1/routine/week`, `GET /v1/routine/occurrences`, `GET /v1/routine/occurrences/{id}`, `POST /v1/routine/occurrences/{id}/actions`, `GET /v1/routine/tasks`, `GET /v1/routine/tasks/{item_id}` | Derived by the routine scheduler; a person completes, skips or moves an occurrence (section 15) |
| Routine overrides | `GET`, `POST /v1/routine/overrides`, `POST /v1/routine/overrides/{id}/actions` | Bound triggers start automatically; a crew medical officer or the ground starts and ends manual ones (section 15) |
| Daily check parts | `GET /v1/daily-checks/{id}`, `POST /v1/daily-checks/{id}/answers` | One answer revision per call |
| Journal | `GET`, `POST /v1/journal` | Author only; never in prompts, indexes, exports or other people's events |
| Kit map | `GET /v1/kit-map`, `POST /v1/kit-map/what-if` | Computed per environment; the what-if stores nothing (section 21) |
| Someone needs help | `POST /v1/help-sessions`, `GET /v1/help-sessions`, `GET /v1/help-sessions/{id}`, `POST /v1/help-sessions/{id}/actions`, `GET /v1/help-sessions/{id}/subject`, `GET /v1/help/broadcasts` | The help state machine (section 16) |
| Message for the ground | `GET /v1/help-sessions/{id}/ground-message-preview`, `POST /v1/help-sessions/{id}/ground-messages`, `GET /v1/ground-messages/{id}` | The For the ground card shows the draft; pressing Tell the ground approves and queues it; it leaves when the link allows and nothing waits for it (sections 16 and 26) |
| Ground console | `GET /v1/ground/inbox`, `POST /v1/ground/messages/{id}/receipt`, `POST /v1/ground/messages/{id}/reply`, `POST /v1/ground/handoffs/{id}/receipt`, `POST /v1/ground/handoffs/{id}/reply` | Ground role; a receipt is not a clinical review, and nothing on board waits for a receipt or reply |
| Assistant | `GET /v1/assistant/status`, `POST /v1/assistant/runtime`, `POST /v1/assistant/conversations`, `GET /v1/assistant/conversations/{id}`, `POST /v1/assistant/conversations/{id}/messages`, `GET /v1/assistant/conversations/{id}/stream`, `POST /v1/assistant/conversations/{id}/cancel` | One turn at a time per conversation; stopping the model stops nothing else (section 17) |
| Insights | `GET /v1/insights`, `GET /v1/insights/{id}`, `POST /v1/insights/{id}/actions` | The session person's own insights (section 17) |
| Screens | `GET /v1/views/today`, `GET /v1/views/how-you-are`, `GET /v1/views/habitat`, `GET /v1/views/records` | Projections (section 19) |
| Onboard plans | `GET /v1/conditions/{condition_id}/plan`, `GET /v1/plans/{plan_id}`, `GET`, `POST /v1/plan-runs`, `GET /v1/plan-runs/{id}`, `POST /v1/plan-runs/{id}/actions` | Plans computed for the environment; a plan run per person, which never waits for the ground (section 22) |
| Teach and check | `POST /v1/guided-checks`, `GET /v1/guided-checks/{id}`, `POST /v1/guided-checks/{id}/beats`, `POST /v1/guided-checks/{id}/actions`, `GET /v1/help-sessions/{id}/coaching` | A crewmate taught step by step, checked by the cameras; CPR coaching (section 23) |
| Roles, skills and drills | `GET`, `POST /v1/help-sessions/{id}/roles`, `GET`, `POST /v1/plan-runs/{id}/roles`, `GET /v1/me/skills`, `GET /v1/crew/readiness`, `POST /v1/drills`, `GET /v1/drills/{id}`, `POST /v1/drills/{id}/complete` | Who does what, with reasons; skill freshness; drills (section 24) |
| Supplies | `GET /v1/supplies`, `POST /v1/supplies/{supply_id}/uses` | Stock, uses and computed cover (section 25) |
| Ground log | `GET /v1/ground/log` | Ground role: the catch-up a late reader sees (section 26) |
| Stay or return | `GET /v1/return-readiness` | Criteria, trip and who decides, where a return is possible; it never decides (section 27) |

Every mutating command accepts `idempotency_key` and expected object version. Use optimistic concurrency: conflicting updates return a conflict with current revision rather than silently overwriting. Long-running operations return a job ID and progress events. A UI refresh reconnects to the job; it does not create a second camera or model request.

On the wire these are headers:

| Header | Direction | Rule |
|---|---|---|
| `Authorization: Bearer <token>` | Request | A session token from `POST /v1/sessions`, or an adapter token configured on the hub for adapter operations |
| `Idempotency-Key` | Request, every mutating operation | Required. The same key with the same body returns the first result; with a different body, `409 DUPLICATE_COMMAND`. Keys are kept 24 hours. `POST /v1/kit-map/what-if` and `POST /v1/sessions` do not take one |
| `If-Match` | Request, every action on an object with a `revision` | Required: the revision as an ETag, for example `"3"`. Missing: `428 VERSION_REQUIRED`. Older than the current revision: `409 STALE_VERSION` with the current object in `current_state`. Section 16 lists the help actions the hub merges instead |
| `ETag` | Response | The object's revision, or a configuration document's version |
| `If-None-Match` | Request, `GET /v1/config/{name}` | Equal to the current version: `304` with no body |
| `Config-Release` | Request and response | Response: the active release ID on every response. Request, optional: the release the client rendered from; a mutating request that depends on configuration under a different active release returns `409 CONFIG_VERSION_MISMATCH`. Help session actions ignore it (section 16) |
| `X-Correlation-Id` | Request and response | Echoed, and copied into any error envelope; generated by the hub when absent |
| `Last-Event-ID` | Request, `GET /v1/events` | Resume after this sequence number (section 18) |

Lists return `{ "items": [...], "page": { "next_cursor": ..., "total": ... } }` and accept `cursor` and `limit` (default 50, at most 200). Times are UTC instants with a `Z`. IDs are opaque strings; configuration IDs (`config_id`) are lowercase snake case and come from the configuration documents.

Standard error envelope: `code`, `message`, `retryable`, `field_errors`, `correlation_id`, `current_state` and optionally `supported_alternative` ([schemas/error.schema.json](api/schemas/error.schema.json)). Every non-2xx response except 304 carries it, and every operation in openapi.yaml documents the codes it can return. The client shows its own words for a code from ui-copy `error_messages`, never `message`.

| Code | HTTP | Meaning |
|---|---|---|
| `MALFORMED_REQUEST` | 400 | The body is not valid JSON or a required header is malformed. |
| `UNAUTHENTICATED` | 401 | No valid session or adapter token. |
| `UNAUTHORIZED_SCOPE` | 403 | The session is valid but may not read or change this object (another person's data, a closed help session, a role the session lacks). |
| `CONSENT_REQUIRED` | 403 | A consent grant is missing for this recipient, purpose or field. |
| `ACTION_NOT_ALLOWED` | 403 | This actor may not take this action on this object, for example the assistant acknowledging a rule warning. |
| `ADAPTER_NOT_TRUSTED` | 403 | The adapter token is unknown, revoked, or not registered for this channel. |
| `AGGREGATE_ONLY` | 403 | A request for a per-person or per-day breakdown of a crew total. |
| `NOT_FOUND` | 404 | No object with this ID is visible to this session. |
| `STALE_VERSION` | 409 | If-Match does not equal the current revision. current_state carries the current object. |
| `INVALID_TRANSITION` | 409 | The action is not allowed from the object's current state. current_state carries the current object. |
| `DUPLICATE_COMMAND` | 409 | The Idempotency-Key was already used with a different request body. |
| `SESSION_CLOSED` | 409 | The help session or conversation is closed. |
| `CONFIG_VERSION_MISMATCH` | 409 | The request names a configuration entity or release that is not in the active release. |
| `CONFIRMATION_REQUIRED` | 409 | The change waits for a crew confirmation before it takes effect. |
| `CURSOR_EXPIRED` | 410 | The event cursor is older than the retained history; reload the views and resubscribe without a cursor. |
| `PAYLOAD_TOO_LARGE` | 413 | The upload or batch is larger than the limit in the operation description. |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | The upload type is not accepted. |
| `INVALID_UNIT` | 422 | The unit is not an accepted unit for this code or sensor type. |
| `INVALID_VALUE` | 422 | A field fails validation; field_errors lists each one. |
| `IDENTITY_MISMATCH` | 422 | The subject, operator or adapter identity does not match the session or the channel. |
| `ORIGIN_MISMATCH` | 422 | The origin mode does not match the channel, the mission mode or the object it attaches to. |
| `KIT_CANNOT_PERFORM` | 422 | No instrument in the active environment's kit provides the requested capability (CONTRACTS section 21). current_state carries the recorded MeasurementRequest in state unavailable with its kit_check. |
| `CAPABILITY_UNAVAILABLE` | 422 | The capability is not available now; supported_alternative names what is. |
| `CONTEXT_INAPPLICABLE` | 422 | The request does not apply in the active environment or overlay. |
| `SOURCE_NOT_APPROVED` | 422 | The evidence source is not approved for this release and use. |
| `REPORT_UNCONFIRMED` | 422 | A report extraction is used before a person confirmed it. |
| `LEVEL_NOT_ALLOWED` | 422 | The level is not allowed for this raiser, for example act or emergency from the assistant. |
| `APPROVAL_REQUIRED` | 422 | A handoff is sent before a person approved it. For a ground message, pressing Tell the ground is the approval: it queues the previewed draft (section 26). |
| `CONFIG_INVALID` | 422 | A configuration document fails its schema or the loader's cross checks. |
| `STEP_NOT_AVAILABLE` | 422 | The plan step cannot run here: an instrument, supply or skill it needs is not aboard or not held by an available crewmate (section 22). current_state carries the step's view with its availability and substitute. |
| `SUPPLY_BELOW_ZERO` | 422 | The use would take the stock below zero (section 25). Nothing is written; the step shows Not aboard with its substitute. |
| `ROLE_NOT_ELIGIBLE` | 422 | The person may not take this role or this check: the subject never gets one, and nobody in an eva overlay gets an on-site role (section 24). |
| `VERSION_REQUIRED` | 428 | The operation requires If-Match and the header is missing. |
| `INTERNAL_ERROR` | 500 | An unexpected hub fault. The help path never returns this for a cached step. |
| `MALFORMED_MODEL_OUTPUT` | 502 | The model output failed the schema after the one allowed repair. |
| `MODEL_OUTPUT_REJECTED` | 502 | The model output was valid JSON but broke a rule (a dose, a level above watch, an unknown source ID) and was discarded. |
| `MODEL_UNAVAILABLE` | 503 | The local model service is stopped, loading or crashed. |
| `MODEL_TIMEOUT` | 504 | The model did not answer within the turn timeout in assistant-policy. |

## 6. Model action contract

The model proposes one structured action per turn: `ask`, `request_measurement`, `retrieve_evidence`, `assessment`, `request_review` or `stop_incomplete`. Read-only tool calls can be grouped by the controller when independent; acquisition and human answers remain sequential. A tool proposal carries its reason and what decision the result would affect. `request_review` asks for review on board (the crew medical officer or the designated person); a ground message may be added as a side effect, and nothing waits for it.

The controller injects authorized patient and episode scopes. It validates capability codes against the current registry, source IDs against the pinned evidence release, observations against current quality/access rules, and every `body_region` on a proposed request or assessment line against the fixed set (section 7). The model cannot alter urgency state, write diagnoses into the chart, accept its own report extraction, or approve a handoff.

For concurrent updates, pin `state_snapshot_version`; if a material new answer arrives during inference, mark output stale and regenerate or require review. Do not silently apply an assessment to evidence it never saw.

## 7. Body regions

The body map ([UX-DESIGN.md](UX-DESIGN.md), section 1.6) lights the place on the figure that an object is about. The place is a `body_region` from one fixed set, identical to the keys of `BodyRender.REGIONS` in `design/body-render.js`. Set version: `body-regions-1`.

| `body_region` | Display label |
|---|---|
| `head` | Head |
| `eyes` | Eyes |
| `ear` | Ear |
| `neck` | Neck |
| `shoulder` | Shoulder |
| `heart` | Heart |
| `lungs` | Lungs |
| `abdomen` | Abdomen |
| `flank` | Flank |
| `back` | Back |
| `wrist` | Wrist band |
| `hand` | Hand |
| `legs` | Legs |
| `knee` | Knee |
| `ankle` | Ankle |

A contract test asserts that this set equals the renderer's region keys, so the two cannot drift. Adding a region changes both, under a new set version.

**Objects that carry an optional `body_region`:**

| Object | Field | Set by |
|---|---|---|
| Answer (symptom answers only) | `body_region`, plus optional `laterality` | The question's default, or the person's choice on the figure or in the region list |
| MeasurementRequest | `body_region` of what the result is about | The capability registry's default for the indicator; a model proposal is validated by the controller |
| Observation | `body_region` | The indicator's registry default: pulse `heart`, breathing rate `lungs`, SpO2 `hand`, sleep `wrist` (the band made the estimate), mood `head`, reaction time `eyes`, activity `legs` |
| Assessment | `body_region` on each `findings[]`, `hypotheses[]` and `missing_evidence[]` entry | The model proposes; the controller validates |
| CapabilityGap | `body_region` the missing test would examine (an ECG: `heart`) | The capability registry |

Rules:

- `null` means the object is not about one place (fatigue, fever, cabin air, a whole-body finding). The UI shows the whole figure.
- A value outside the set is rejected with `INVALID_VALUE`. In model output an invalid region is removed from its line (the line is shown without a place) and the repair is logged. A region is never guessed from free text.
- `band` is a renderer anchor on the face of the wrist band, used for the band's own light. It is never stored as a `body_region`.
- `laterality` (`left`, `right`, `both`, `unknown`) is a separate optional field on symptom answers and findings, shown in words. The figure marks the region, not the side.
- The mark is derived from the object and its state. It is never stored and never chosen by the model.

| Object and state | Mark on the figure |
|---|---|
| Observation with `quality_status` accepted | Filled dot: a measurement |
| Symptom Answer with presence `present` | Hollow ring: reported, not measured |
| CapabilityGap | Dashed ring: not available here |
| Rejected observation, failed attempt, declined or cancelled request, symptom answer absent, unknown or declined | No mark; words only |

## 8. Participation: streak, patches and crew goal

Participation rewards showing up and never the numbers (requirements R31 to R33). Everything here is derived from one immutable event, the CheckCompletion, and the mission clock. Nothing in this section reads a value.

### Objects

**CheckCompletion** (immutable event, written only by the controller)

| Field | Rule |
|---|---|
| `completion_id` | UUID |
| `patient_id`, `mission_id` | The person whose check or concern it is |
| `origin_mode` | `LIVE` or `SIM`, taken from the basis. A REPLAY basis creates nothing |
| `basis` | `daily_check`, `concern_reported` or `episode_updated` |
| `basis_ref` | The DailyCheck or Episode ID. A reference only, never its content |
| `occurred_at` | UTC instant, with the original offset, at which the basis was accepted |
| `mission_clock_version` | The clock configuration in force at `occurred_at` |
| `mission_day` | Integer derived from `occurred_at` under that clock version; stored for audit and recomputed on rebuild |
| `completion_rule_version` | Version of the rule that accepted it |
| `status` | `accepted` or `rejected`, with `status_reason` (`ORIGIN_MISMATCH`, `IDENTITY_MISMATCH`, `REQUIRED_ANSWER_MISSING`) |
| `idempotency_key`, `created_at` | One event per basis |

The schema has no field for a value, a state, answer content, quality, a symptom count, a consult outcome or journal content, and it rejects extra fields.

**StreakView** (derived, private to the person)

| Field | Rule |
|---|---|
| `patient_id`, `mission_id`, `origin_mode` | One view per person, mission and origin mode |
| `as_of`, `current_mission_day` | When it was computed |
| `today` | `checked` or `due` |
| `current_streak_days`, `longest_streak_days`, `completed_days_total` | See the derivation rule |
| `first_mission_day` | The person's first day on this mission's roster; earlier days are not shown |
| `days[]` | `{ mission_day, status }` with status `checked`, `missed`, or `due` (today only) |
| `derivation_version`, `mission_clock_version`, `event_cursor` | What it was computed from |

**CrewGoalView** (derived, the only crew-visible participation object)

| Field | Rule |
|---|---|
| `mission_id`, `origin_mode` | One view per mission and origin mode |
| `window_first_mission_day`, `window_last_mission_day` | The seven mission days ending today |
| `completed_total` | Count of (crew member, mission day) pairs in the window with an accepted completion |
| `due_total` | For each day in the window, the crew members on the roster that day, summed (28 for four people over seven days) |
| `reached` | `completed_total >= due_total` |
| `as_of`, `derivation_version`, `mission_clock_version` | What it was computed from |

It contains no patient ID, name, per-person count or per-day count.

**PatchAward** (derived, private to the person)

| Field | Rule |
|---|---|
| `award_id` | Deterministic from patient, mission, origin mode and threshold, so a rebuild reproduces it |
| `patient_id`, `mission_id`, `origin_mode` | Whose patch |
| `threshold` | 7, 14 or 30 completed days |
| `patch_design_id` | One of our own patch designs |
| `reached_by_completion_id`, `reached_on_mission_day` | The completion that reached the threshold |
| `derivation_version` | Rule version |

### When the controller writes a CheckCompletion

- `daily_check`: on submission of a DailyCheck, when the symptom question, sleep and mood each have an accepted Answer from this person in any presence state: present, absent ("nothing new"), unknown ("not sure") or declined ("prefer not to say"). A blank is not an answer. The camera, kit readings, the reaction test, the band and the journal are never consulted.
- `concern_reported`: when the person starts an episode through **I feel unwell**.
- `episode_updated`: when the person adds or corrects an answer on their own open episode, or an urgent event is recorded on it.

### Derivation rule

1. **Inputs.** The accepted, non-voided CheckCompletion events for one mission and one origin mode, the versioned mission clock configuration and the crew roster. Nothing else. The derivation module has no read access to observations, answer values, assessments, episodes, handoffs or the journal.
2. **Mission day.** Each event's day comes from `occurred_at` under the clock version in force at that instant, with the day boundary at 00:00 mission time. A check at 23:58 belongs to day N and one at 00:03 to day N+1. A later clock change never moves an earlier event. A correction of a wrong clock is a new clock version with an explicit effective range; a rebuild under it can move a day, and the rebuild records every changed day assignment as an AuditEvent.
3. **Checked day.** A day is checked when at least one event falls on it. Several events on one day count once.
4. **Current streak.** Consecutive checked days ending today if today is checked, otherwise ending yesterday. It is 0 when today and yesterday are both unchecked.
5. **Longest streak.** The longest run of consecutive checked days.
6. **Missed day.** A past day from `first_mission_day` onward with no event. Today is `due` until it ends, never `missed`.
7. **Patches.** A threshold is reached when `completed_days_total` reaches 7, 14 or 30.
8. **Late arrival.** An event synced late keeps its `occurred_at`, so a day can move from `missed` to `checked`. It never counts for the day it synced.
9. **Determinism.** The same events, clock configuration and roster always give the same result (identical serialization apart from `as_of`). Views may be cached, but they are never the source of truth, never written through an API and never edited by hand. A rebuild is allowed at any time.

### State rules

- **CheckCompletion:** created `accepted` or `rejected`. An accepted completion is voided only by a later void event that references it, when an identity correction reassigns its basis to another person. The original record is never edited. Nothing else voids it: not a corrected value, a changed answer, an episode outcome or a review.
- **Day status:** `due → checked` when an event falls on the day; `due → missed` when the day ends without one; `missed → checked` only through a late-arriving event from that day or a rebuild under a clock correction.
- **PatchAward:** `earned` is the only state. A rebuild after a void that drops the total below a threshold removes that award and records why. Nothing else removes one.
- **CrewGoalView:** no state beyond `reached`. Its total moves when the seven-day window moves at the day boundary; the UI never presents that as a loss.

### Invariants

- No value, state, in-range result, quality result, consult outcome, symptom count, declined or absent answer, or journal content is an input to a completion or to any view.
- No screen, export, log view, event stream or API returns one person's completions, streak or patches to anyone but that person. The crew goal returns totals only; a breakdown request returns `AGGREGATE_ONLY`.
- SIM is never mixed with LIVE: each view is computed per origin mode, a SIM view counts only SIM events and is labeled SIM wherever shown, and REPLAY never creates events.
- Participation data never enters a model prompt, a retrieval index, a HandoffSnapshot or the ground console, and never appears on urgent, assessment or shared-care screens.

## 9. Invariants for every implementation stream

- No simulated patient answer or result enters LIVE mode.
- No unavailable, declined, failed or stale-as-current result becomes a normal finding.
- No private journal enters prompts, search indexes, screenshots for evaluation or exports.
- No experimental forecast or camera estimate substitutes for a required clinical instrument.
- No reviewed warning is implicitly medically resolved.
- No urgent response waits for LLM completion, another sample or a ground acknowledgment.
- Nothing on board waits for the ground: no responder flow, help session, episode, warning, measurement request or onboard plan blocks on a ground reply or acknowledgement. Ground messages and handoffs are logs sent when the link allows.
- No historical cabin series becomes current exposure without an explicit actual association.
- No source correction or record correction erases lineage.
- No benchmark answer/rubric is accessible to the evaluated model or its retrieval tools.
- No inferred diagnostic probability is derived from retrieval similarity or sensor confidence.
- No value, outcome, declined answer or journal content changes a check completion, a streak, a patch or the crew goal.
- No per-person participation data reaches anyone but that person; the crew sees only the crew-goal total.
- No `body_region` outside the fixed set is stored or drawn, and no reported symptom is drawn as a measurement.

## 10. Data retention and recovery choices for the TDD

The prototype should default to no raw camera retention. Sensor validation recordings, if collected with consent, live in a separate evaluation dataset with explicit retention and deletion. Band raw epochs are retained for reproducibility under a stated policy. Journal deletion includes its separate store and defined backups; the UI must not promise immediate erasure from immutable external exports.

Encrypt sensitive local files and backups with an explicit key-management design. Binding to localhost is the default. Any ground console on the local network requires authenticated, encrypted access and explicit enabling. “Offline” is not an access-control strategy. Export and restore tests must cover schema version, patient namespaces and evidence-release consistency.

## 11. Configuration documents and the no-hardcoded-data rule

### The rule

No client hardcodes data. Every label, threshold, limit, cadence, due time, step, question, headline, template, display range, decimal count, unit word, day label, delay word, instrument and kit comes from a configuration document or from hub state served through the API. The hub follows the same rule: its engines read numbers and words from the active configuration release, never from code.

A client may hardcode only:

- the fixed vocabulary below, as keys and never as display words;
- layout, and design tokens keyed by vocabulary keys (for example one color per level);
- the offline copy of the help start screen, which it builds from the cached responder-flows.json and help-presentation.json (section 16);
- question texts from questionnaires.json, putting the hub's `DailyCheck.estimates[].value_text` into `{value}`.

A value that has no source and no team decision stays visibly open: a limit with `value: null` and `status: threshold_pending` ("threshold to be set by clinical review"), a routine cadence the research file marks "frequency to be set by the flight surgeon", or a value labeled `"basis": "team default"`. Sourced values are copied from the research files unchanged, and the loader checks every copy.

### Fixed vocabulary

These sets are code. They are defined once in [schemas/common.schema.json](api/schemas/common.schema.json) and every schema refers to them. Changing one is a contract change.

| Set | Values |
|---|---|
| `environment_id` | `station_leo`, `transit`, `lunar_surface`, `mars_surface`. The research files also use the key `eva` in `by_environment` for text that applies outside |
| `overlay` | `eva`, `post_eva`, `exercise`, `sleep` |
| `level` | `info`, `watch`, `act`, `emergency`, in that order |
| `raised_by` | `rule`, `sensor_limit`, `assistant` |
| Cadence word | `continuous`, `daily`, `every_n_days`, `weekly`, `monthly`, `per_eva`, `after_event`, `mission_phase`, `as_needed` |
| `body_region` | The 15 regions of section 7 |
| `origin_mode` | `LIVE`, `REPLAY`, `SIM` |
| `basis` | `sourced`, `team default` |
| Source ID | Two letters or one (`EV`, `RT`, `RS`, `SK`, `S`, `C`, `N`, `L`) and two digits, for example `EV01` |
| `gap_kind` | `planned_line_of_sight`, `relay_outage`, `conjunction`, `equipment`, `unknown` (section 26) |
| Plan stage | `check_another_way`, `do_now`, `watch`, `if_worse`, `medical_officer`, `return`, `ground_log`, in that order (section 22) |
| Plan mode | `resolve`, `talk_through`, `stabilize`, `prepare_return` |
| `who` | `any_crew`, `crew_medical_officer`, or `skill:` followed by a skills-catalog skill ID |

### The documents

| Document | Holds | Edited by | Schema |
|---|---|---|---|
| `environment-hazards.json` | Environments with their hazard IDs and `earth_delay` words; hazards with levels, effects, what to do per level and words per environment | Research owner | `config-environment-hazards.schema.json` |
| `routine-schedule.json` | Routine items: steps, instrument, minutes, region, cadence per environment and the triggers that raise frequency | Research owner | `config-routine-schedule.schema.json` |
| `responder-flows.json` | Triage questions and responder flows: fixed steps, questions, measurements, treatment steps, the log for the ground (last, never blocking) | Responder flows owner | `config-responder-flows.schema.json` |
| `sensor-catalog.json` | Sensor types (below) | Team | `config-sensor-catalog.schema.json` |
| `sensor-limits.json` | Machine readable limits bound to hazard levels (section 13) | Team; values copied from environment-hazards.json | `config-sensor-limits.schema.json` |
| `warning-rules.json` | Warning definitions, including `ground_silence` and `supply_cover_low`, and the headline rule (section 14) | Team | `config-warning-rules.schema.json` |
| `routine-rules.json` | Calendar, suppressed overlays, display groups, due windows, the miss rule and trigger bindings (section 15) | Team | `config-routine-rules.schema.json` |
| `environment-profiles.json` | Day labels, the comms queue (simulated one-way delay and scheduled link status), the link's default gap kind and simulated contact windows, the return option, walk word, sensor types per environment, effect filters, mission event types, state facts, the confirmation rule (sections 12 and 27) | Team | `config-environment-profiles.schema.json` |
| `questionnaires.json` | Daily check, red flags, question sets added by bindings, the miss question | Team | `config-questionnaires.schema.json` |
| `indicator-catalog.json` | Personal indicators: label, unit, decimals, region, baseline method and window; the reaction test lapse line | Team | `config-indicator-catalog.schema.json` |
| `help-presentation.json` | Start node, fixed buttons, log templates, per flow level, broadcast and ground message templates (with the waiting template for a link that is down), readings panel, red flag flows (sections 16 and 26) | Team | `config-help-presentation.schema.json` |
| `assistant-policy.json` | Allowed warning levels, loop budgets, timeouts, insight topics, forbidden outputs (section 17) | Team | `config-assistant-policy.schema.json` |
| `ui-copy.json` | Display words for the fixed vocabulary, designed states, occurrence end texts, skip reasons, error messages, kit map words, ground link and gap kind words, duration words, plan stage and mode words, plan lines, guided check, freshness, supply and readiness words, catch-up words | Team | `config-ui-copy.schema.json` |
| `participation-rules.json` | Patch thresholds and designs, crew goal window (section 8); practice patches (section 24) | Team | `config-participation-rules.schema.json` |
| `kit-map.json` | Instruments, kits per environment, supplies and stock per environment, candidate instruments, supplies and skills, ICL conditions with confirming tests, the plan aboard and what NASA's form assumed of the ground (sections 21 and 25) | Team; each row reviewed clinically | `config-kit-map.schema.json` |
| `onboard-plans.json` | The plan aboard for each kit-map row and responder flow: stages of steps with what each needs, ending with the log for the ground (section 22) | Team; each plan reviewed clinically | `config-onboard-plans.schema.json` |
| `skills-catalog.json` | Skills a crewmate can be taught on the body figure: teach steps, landmarks, camera checks, freshness, drills (sections 23 and 24) | Team; each skill reviewed clinically | `config-skills-catalog.schema.json` |
| `role-rules.json` | Roles, eligibility, the medical officer cascade, the roles each flow needs, reason phrases (section 24) | Team | `config-role-rules.schema.json` |

The three research files are in the package root. The hub reads the team documents from its configuration directory as `config/<name>.json`. [api/examples/config/](api/examples/config/) holds an example of each team document and [api/examples/kit-map.example.json](api/examples/kit-map.example.json) the example kit map. They show the shape and reproduce the demo; they are not seed data, and a deployment writes its own documents and runs them through the loader.

### The sensor catalog

`sensor-catalog.json` is a configuration document, not code. One entry per sensor type:

| Field | Meaning |
|---|---|
| `id`, `hazard_id` | Sensor type ID (for example `co2`) and the hazard in environment-hazards.json it reports on |
| `name`, `long_name` | Card name (CO2) and detail name (Carbon dioxide) |
| `body_region` | Region the card lights, or `null` |
| `unit`, `unit_display` | Canonical unit the hub stores and evaluates in; the unit as shown (empty for an index) |
| `decimals`, `trim_trailing_zeros` | Display rounding. With `trim_trailing_zeros`, 48.0 shows as 48 |
| `accepted_units[]` | `{unit, conversion, factor}`: `identity`, `scale` (canonical = value x factor) or `ppm_to_mmhg` (mmHg = ppm x cabin pressure in mmHg / 1,000,000) |
| `aggregations[]` | `{id, kind, window_seconds, label}`; kind is `latest`, `mean`, `time_weighted_mean`, `energy_mean`, `max` or `sum` (section 13) |
| `display_aggregation_id` | The aggregation the card shows |
| `display` | `{range, by_environment, basis}`: the card's scale |
| `expected_interval_seconds`, `interval_basis`, `interval_source_ids` | How often a channel should report; `null` when the design decides |
| `stale_after_seconds`, `stale_basis` | When a channel counts as quiet |
| `no_reading_label` | Word shown with no reading (No reading, Untested) |
| `manual_alternative_item_id` | Routine item that measures the same thing by hand, or `null` |
| `citation_text` | How the card cites the sensor type |

### Versions and releases

- A document's `version` is the SHA-256 of its raw file bytes, the same value `design/tools/bundle-config.py` writes. A label inside a document (for example the kit map's `version`) is display only.
- A ConfigRelease pins one version of each of the 18 documents. `POST /v1/config/releases` proposes one (documents not listed keep their active version). The loader validates it and stores it as `draft` or `rejected` with its findings. `POST /v1/config/releases/{id}/activate` makes a draft active in one transaction and sends `config.release_activated`.
- Every warning, routine occurrence and override, help session and conversation records the `config_release_id` it was computed under.
- On activation the engines re-evaluate: sensor evaluations, warnings (a warning whose definition is gone clears with `clear_reason` `config_changed`), and routine schedules. An open help session keeps the responder-flows, help-presentation and ui-copy versions it pinned at open until it closes, and an open plan run keeps its onboard-plans, skills-catalog, role-rules, kit-map and ui-copy versions (section 22).
- Clients call `GET /v1/config` at start, cache each document by version (`If-None-Match`), and refetch after `config.release_activated`.
- A kit map with `example: true` cannot be activated while the mission mode is `LIVE` (`CONFIG_INVALID`).

### Loader checks

The loader rejects a release with `CONFIG_INVALID` and lists each finding in `validation.errors` (JSON pointer and message) when any check fails. All 16 checks also run in the package validator (api/README.md), against the three research files and the example documents.

1. Each document validates against its schema.
2. Every source ID resolves: the research files' own source lists, `sources.csv`, or a `| Cxx |` row in NASA-SOURCES.md, DEMO-CONDITIONS.md or RESEARCH-ANNEX.md.
3. Responder flows: every triage answer goes to a triage node or a flow; a treatment step that names a medicine (medicine, inhaler, adrenaline, glucose, glucagon, pain relief) says "as in the onboard procedure"; no step or kit text names an amount (a number with mg, mcg, µg, g, mL, IU or units). No schema and no document has a dose field.
4. Sensor limits: `hazard_level` equals `environment-hazards.json` `hazards[hazard_id].levels[index]` in `level`, `value`, `unit` and `window`; `value_from: level_value` means `value` equals the level value; `rule_text` means the number appears in that level's rule text; `pending` means `value` is `null`; at most one `card_mark` per sensor type and environment.
5. Warning rules: every active limit with role `warning` has exactly one definition whose condition names it; `raised_by: assistant` definitions are `info` or `watch`.
6. Routine rules: each binding's `trigger_text` equals `routine-schedule.json` `items[item_id].raise_frequency_when[trigger_index].trigger`; each `cadence_event` binding's `event_text` equals the item's cadence event or phase words. Triggers with no binding are reported in `validation.warnings` and stay manual.
7. Environment profiles: one profile per environment; every sensor type exists in the catalog.
8. Questionnaires: every referenced question exists; every flow a choice opens exists.
9. Indicator catalog: every code is a `field_id` in input-dictionary.json.
10. Help presentation: the start node, every `flow_levels` flow, every `red_flag_flows` flow and every `answer_log` node and answer index exist in responder-flows.json.
11. Kit map: every kit and candidate instrument and every test instrument exists in `instruments`; every capability code an instrument provides has exactly one `capabilities` entry and every entry is provided by some instrument; every `condition_id` exists in condition-assessments-119.csv and appears once.
12. Each sensor limit's `aggregation_id` exists on its sensor type and its `unit` is the catalog unit; each effect filter's `effect_starts_with` matches the start of the hazard effect at `effect_index`; a `reviewed` kit map row names `checked_by`.
13. Every template uses only the placeholders listed for its field below.
14. Onboard plans (section 22): stages in the fixed order, each at most once, `ground_log` last with `blocking` false (EI4); a `return` stage only for environments whose `return_option` is not `null` and that the plan covers (EI11); every instrument, supply, skill, landmark, flow and substitute a step names exists, and a substitute is a `substitute_only` step of the same plan; `crew_medical_officer` steps only in `medical_officer` or `return`; guided checks only in `check_another_way`; only `if_worse` steps open help; a non-consumable supply has `per_use` `null`; the `medicine` object holds `named_as` only and any step text that names a medicine says "as in the onboard procedure"; no step or teach text names an amount. Form quotes (`ground_needs`, `treatment_scope`, `replaces_ground_task`) are found on the cited page of the saved form text, and `rtdc` comes from a saved form with a Removal to Definitive Care table.
15. Plan aboard (EI3): every kit-map row names a `plan_id` written for its condition, and in every environment that plan has an `any_crew` step needing no skill, in `check_another_way`, `do_now` or `watch`, that runs with that environment's kit and stock, and a `watch` or `check_another_way` step. A failure names the row and environment. With mission mode LIVE it rejects the release; with SIM or REPLAY it is listed in `validation.warnings` and the This kit count shows the gap.
16. Skills and roles (sections 23 and 24): teach steps numbered from 1, with exactly one of `text` or `from_flow_step` (which must exist), count steps and only they with `count_seconds`, question steps and only they with `choices`, landmarks in the step's region; a `count_agreement` check has a tolerance and a reference capability that exists, a `compression_rate` check a sourced target band; every drill supply and routine `skill_ids` entry exists; role-rules flows, roles and skills exist, the cascade is first officer, second officer, then the freshest skill holder, and `never_subject` and `not_on_eva_for_on_site` are exclusions (EI9).

### Templates

A template is text with placeholders in braces. The hub renders every template that needs hub state and serves the result in the object (for example `Warning.what`, `RoutineOccurrence.end_text`, `SensorStatus.display_value.text`, `KitCheck.text`). Clients render only the ui-copy words for vocabulary keys, the designed states and the question texts above. A designed state's placeholders take their values from the object being drawn (`{time}` in `sensor_quiet` is `SensorStatus.stale_since`; in `ground_next_contact` it is `GroundLink.next_contact_at`), formatted as below. If a placeholder has no value at render time, the renderer uses ui-copy `designed_states.unknown` in its place and the hub logs it.

| Placeholder | Value | Allowed in |
|---|---|---|
| `{day}`, `{total}` | Day number by the profile's counter; planned mission length | environment-profiles `day_label` templates |
| `{n}`, `{eva_word}`, `{event}`, `{phase}` | Cadence count; the profile's walk word; cadence event words; mission phase words | ui-copy `cadence_words` |
| `{day_label}`, `{time}`, `{overlay}`, `{reason}` | Inline day label of the occurrence's day; mission time HH:MM; overlay word; skip reason words | ui-copy `occurrence_end_text` |
| `{time}`, `{in_time}`, `{dataset}`, `{sensor_name}`, `{value}`, `{unit_display}`, `{source_label}` | Mission time HH:MM; time to the next contact (section 26); replay dataset name; sensor name; formatted value; unit; channel source words | ui-copy `designed_states` |
| `{instruments}` | Instrument names (section 21) | ui-copy `kit_check_templates` |
| `{n}` | A count | ui-copy `duration_words` |
| `{aggregation_label}`, `{value}`, `{unit}`, `{unit_display}`, `{limit_value}`, `{location}`, `{sensor_name}`, `{source_label}`, `{last_received_time}` | From the reading, limit, channel and catalog | warning-rules `content` (`title`, `what`, `why`, `citation_template`, `headline.fragment`) |
| `{answer_day_label}`, `{answer_time}`, `{day_short}`, `{eva_word}` | Day label and time of the answer that raised it; short label of today; the profile's walk word | The same warning-rules `content` fields |
| `{insight_title}`, `{insight_text}`, `{insight_why}`, `{insight_fragment}` | From the insight | The same warning-rules `content` fields |
| `{silence_text}`, `{next_contact_time}` | Time past the planned contact; its mission time HH:MM (section 26) | The same warning-rules `content` fields |
| `{supply_name}`, `{uses}`, `{quantity}`, `{per_use}` | From the SupplyCoverView row (section 25) | The same warning-rules `content` fields |
| `{subject_name}`, `{opener_name}`, `{helper_name}`, `{helper_names}`, `{place}`, `{flow_title}`, `{step}`, `{note}`, `{text}`, `{disposition}`, `{answers}`, `{steps}`, `{helping}`, `{time}`, `{delay}`, `{in_time}` | From the help session and its ground message (sections 16 and 26) | help-presentation `answer_log[].log_template`, `broadcast`, `log_templates`, `ground_message` templates |
| `{in_time}`, `{origin_word}`, `{step}`, `{skill_short}`, `{count}`, `{total}`, `{missing}`, `{plan_label}`, `{to_be_set_by}`, `{time}` | Time to the next contact; origin words of the link; a step text; a skill's `short_name`; counts of the This kit line; the plan's tag; who sets a pending value; a recheck time (section 22) | ui-copy `plan_lines` |
| `{time}`, `{step}`, `{recheck}`, `{topic}` | A step's done time HH:MM; its text; its recheck words; an advice topic (section 26) | ui-copy `catch_up_words` |
| `{n}` | Seconds left in a count | ui-copy `guided_check_words` |
| `{n}`, `{name}`, `{quantity}` | Cover in uses; supply name; quantity with its unit (section 25) | ui-copy `supply_words` |
| `{skill_short}`, `{n}` | A skill's `short_name`; the count of fresh holders (section 24) | ui-copy `readiness_words` |
| `{subject_name}`, `{helper_name}` | The guided check's subject and helper (section 23) | skills-catalog `card_title`, `teach_steps[].text`, `teach_steps[].text_first_time` |
| `{count}`, `{camera}`, `{difference}`, `{rate}`, `{reason}` | The count per minute; the camera value; the difference as a positive number; a compression rate; the no-reading reason (section 23) | skills-catalog `checks[].phrases` |
| `{officer_name}`, `{eva_word}`, `{time}`, `{skill_short}`, `{day_label}`, `{person_name}` | From the role engine (section 24) | role-rules `reason_phrases` |
| `{reason}` | The trigger's reason words | questionnaires `question_sets[].reason_template` |
| `{value}` | `DailyCheck.estimates[].value_text`: the device estimate, formatted by the hub with the indicator catalog decimals and `unit_display` (5.2 h) | questionnaires `questions[].text` of `confirm_estimate` questions |

A placeholder in any other field, or a placeholder not listed for its field, fails loader check 13.

Formatting: `{value}` uses the sensor catalog `decimals` (and `trim_trailing_zeros`). `{limit_value}` is the limit number in its shortest form (3, 0.3, 2.5). `{time}` is mission time HH:MM of the relevant instant, except in `replay_time`, where it is the replay reading's original `observed_at` in UTC. `{delay}` is ui-copy `duration_words` in the largest unit that gives a whole number (10 seconds, 5 minutes, 20 minutes). `{in_time}` is rounded down instead (section 26).

## 12. Environments, hazards and mission context

### Mission clock

The mission clock configuration has an `epoch` and a `clock_version`. Mission day = floor((t − epoch) / 86,400 s) + 1, with the day boundary at 00:00 mission time (section 8). Mission elapsed time (`met`) is `DDD/HH:MM:SS` since the epoch, for example `018/09:10:16` on mission day 19. Mission time is the time of day within the mission day, (t − epoch) mod 86,400 s, shown as HH:MM; with the demo epoch at 00:00 UTC it equals UTC (09:10:16Z is 09:10). `GET /v1/mission` returns the clock, today's day label, the active profile, any pending change, the crew and the ground link.

### Environment profiles

Exactly one EnvironmentProfile is active. A change is a new record:

`proposed → active → superseded`, or `proposed → rejected`.

| Step | Who | Rule |
|---|---|---|
| Propose | `operator`, `ground`, `crew_medical_officer` | `POST /v1/environment/changes`. Only one proposal can be open; a second returns `INVALID_TRANSITION`. Sends `environment.change_proposed` |
| Confirm | Any `crew` session | Adds the person to `confirmations`. When the count reaches `change_confirmation.confirmations_required` in environment-profiles.json, the record activates at that instant, or at `effective_at` if that is later. Until then any action that depends on the change returns `CONFIRMATION_REQUIRED` |
| Reject | Any `crew` session | The record becomes `rejected`; nothing changes. Sends `environment.change_rejected` |

Activation runs in one transaction, in this order:

1. The active record becomes `superseded`; the new record becomes `active` with `activated_at` and `environment_day_start` set to the activation instant.
2. The record lists what it switches on: `hazard_ids` from `environment-hazards.json` `environments[environment_id].hazard_ids`; `limit_ids` and `warning_definition_ids` whose `environments` include it; `sensor_type_ids` from the profile; `sensor_channel_ids` of channels whose `environment_ids` include it; every routine item.
3. Warnings whose definition does not apply to the new environment clear with `clear_reason` `environment_changed`. Overrides whose source warning cleared end with `end_reason` `environment_changed`.
4. The routine scheduler recomputes every person's schedule from the new cadences (section 15). Done occurrences stay.
5. The sensor monitor re-evaluates the new set of sensor types; bindings on `environment_activated` fire.
6. Events: `environment.activated`, then the events the steps above cause.

Day labels come from the profile's `day_label`:

- `counter` is `mission_day` or `environment_day`. Environment day = floor((t − `environment_day_start`) / `day_length_seconds`) + 1.
- `template` gives the full label (`flight day {day} of {total}`, `surface day {day}`, `sol {day} on Mars`), `short_template` the short one (`Day 19`, `Sol 3`), and `inline_template` the one used inside sentences (`day 21`, `sol 7`).

`return_option` is the trip home from this environment (`trip_text`, `source_ids`, `to_be_set_by`), or `null` where return is not an option (Mars, transit). Only plans for environments with a return option may have a return stage (section 27).

### Hazards of the environment

The hazards of the active environment are `environment-hazards.json` `environments[id].hazard_ids`. Sensed hazards appear on sensor cards (section 13). Hazards of kind `condition` (microgravity, partial gravity, isolation, circadian light, distance) appear on the Habitat screen as `living_here`: name without its source IDs, body regions of its effects, effects after `effect_filters` (an effect at `effect_index` is shown only in the environments in `only_in`), what to do (the sentences of `what_to_do.info` and `what_to_do.watch`, skipping "Nothing to do" sentences) and the words for this environment from `by_environment`.

### Activity overlays

An overlay belongs to one person: `planned` (with `planned_start`), `active` (with `started_at`) or `ended`.

- A mission event whose type has `starts_overlay` starts that overlay for the event's `person_ids`; `ends_overlay` ends it. `eva_ended` ends `eva` and starts `post_eva`. A person or operator can also start, plan or end one through the API.
- A person has at most one active overlay of each kind. `post_eva` ends when a person ends it, when the next `eva` starts, or at the end of the mission day.
- While an overlay listed in routine-rules `suppressed_overlays` is active (always `sleep` and `eva`), nothing routine is due for that person (section 15). Bindings on `overlay_ended` and `overlay_planned` fire on those changes.

### Mission events and state facts

Mission event types, their labels, the overlays they start or end and the roles that may record them are in environment-profiles.json `mission_event_types`. Recording one is `POST /v1/mission-events`; a role not in `who_may_record` gets `UNAUTHORIZED_SCOPE`. State facts are booleans about the place (for example `local_water_untested`), set and cleared only by the event types listed in `state_facts`, and only in the listed environments. Warning rules read them (section 14).

### Ground link

The whole health loop runs on board. The ground is never a step anything waits on: no responder flow, help session, episode, warning, measurement request or onboard plan blocks on a ground reply or acknowledgement. Ground messages (section 16) and handoffs (section 4, Sharing) are logs that leave when the link allows. Section 26 names the only four readers of the link.

GroundLink is hub state, read with `GET /v1/ground-link` and inside `GET /v1/mission` and the screen views:

| Field | Rule |
|---|---|
| `status` | `up`: available for a near real time exchange. `delayed`: available, each message takes `one_way_delay_s` one way. `down`: no link; ground messages and handoffs wait in the queue |
| `one_way_delay_s` | The one-way delay when the link is available; kept while it is down; `null` when unknown |
| `next_contact_at` | The next contact when the link is down and it is known; otherwise `null` |
| `last_contact_at` | The last time a message, handoff, receipt or reply crossed the link; `null` before the first |
| `gap_kind` | Why the link is down: `planned_line_of_sight`, `relay_outage`, `conjunction`, `equipment` or `unknown`. Set when `status` is `down`; `null` otherwise |
| `gap_planned` | True when the gap is in the contact schedule or was set as planned by the demo switch. A planned gap raises nothing (section 26). Always false when `status` is `up` or `delayed` |
| `source` | `schedule`: from the active profile's `comms_queue`. `measured`: reported by a comms adapter (this package defines no such adapter). `demo_switch`: set with the demo route below |
| `environment_id` | The active environment |
| `delay_text`, `delay_source_ids` | `earth_delay` of the active environment in environment-hazards.json, with the parenthesised source IDs moved to `delay_source_ids`. For the Moon: "Seconds. NASA gives 6 to 10 seconds on the surface of the Moon as an example of a small delay." (EV01). Never from hub state, environment-profiles.json or code |

- With source `schedule`, `one_way_delay_s` is `comms_queue.simulated_one_way_delay_seconds`. When a window of the profile's `link.sim_schedule` covers the hub clock, `status`, `gap_kind` and `gap_planned` (the window's `planned`) come from that window, and for a `down` window `next_contact_at` is its `ends_at`. Otherwise `status` is `comms_queue.scheduled_status` (`up` or `delayed`, a team choice) and `next_contact_at` is `null`. The example profiles have no windows. Environment activation resets the link to the new profile's schedule.
- `POST /v1/demo/ground-link` is demo only. The hub serves it only in demo mode (mission mode SIM or REPLAY) and answers `404 NOT_FOUND` otherwise. The body is either `{"use_schedule": true}` or a `status` with an optional `one_way_delay_s`, `next_contact_at`, `gap_kind` (default: the profile's `link.default_gap_kind`) and `gap_planned` (default false); it sets `source` `demo_switch`. In the demo, act 3 takes the Moon base link down for a planned 26 hour gap ([environment/ground-link-lunar.json](api/examples/environment/ground-link-lunar.json)).
- The simulated comms queue sends queued items in order as soon as `status` is `up` or `delayed`, and each arrives after `one_way_delay_s`. While `status` is `down` they wait; nothing else waits.
- Clients show ui-copy `ground_link_words[status]`, and `designed_states.ground_next_contact` when `next_contact_at` is set. Nothing else on any screen changes when the link is down. `ground_link.changed` reports every change.

## 13. Sensors and readings

### Channels and ingest

A SensorChannel is one feed: sensor type, hazard, location, adapter, origin mode, input unit, expected interval and stale time (both from the catalog unless the channel sets its own, for example 300 s for the 5-minute replay file). Adapters send batches of at most 1,000 readings to `POST /v1/adapters/{adapter_id}/readings` with their adapter token. For each reading, in order:

1. The channel must belong to the adapter (`ADAPTER_NOT_TRUSTED`) and the reading's `origin_mode` must equal the channel's (`ORIGIN_MISMATCH`).
2. The unit must be the canonical unit or one of `accepted_units` (`INVALID_UNIT`). The hub converts to the canonical unit and records `original_value`, `original_unit` and `conversion`. For `ppm_to_mmhg` it uses `cabin_pressure_mmhg` from the reading, measured or assumed, and records the basis. Example: 4,646 ppm at an assumed 760 mmHg is 3.531 mmHg. Canonical values are stored rounded to 4 decimal places.
3. `(channel_id, adapter_sequence)` is unique; a repeat is counted as a duplicate and ignored.
4. `received_at` is the hub clock. `observed_at` is kept as sent. A REPLAY reading keeps its original date and carries `replay` (dataset and time basis).
5. A reading with `quality_status` `rejected` is stored but never evaluated.

A batch is never rejected as a whole for one bad reading; `IngestResult` reports accepted, duplicate and rejected readings by index.

### Freshness

A channel is `current` when now − `last_received_at` ≤ `stale_after_seconds`, `stale` when it is longer, and `never_reported` with no reading. Becoming stale sends `sensor.quiet` and raises the `sensor_quiet` warning definition where one applies. The next reading sends `sensor.resumed` and clears that warning.

### Aggregation

For an aggregation with `window_seconds` W, the window is the half-open interval (E − W, E], where E is the latest `observed_at` of an accepted reading on the channel. E follows the data and not the wall clock, so a REPLAY channel is evaluated on its original dates. A reading older than E is stored and appears in series but does not move E.

| Kind | Value |
|---|---|
| `latest` | The reading at E |
| `mean` | Arithmetic mean of the readings in the window |
| `time_weighted_mean` | Each reading holds from its `observed_at` until the next reading (the last until E); the integral over the covered time divided by the covered time. No value is assumed before the first reading in the window |
| `energy_mean` | 10 log10 of the time-weighted mean of 10^(L/10), for decibels (Leq) |
| `max`, `sum` | Maximum, sum of the readings in the window |

Coverage: with an expected interval I, coverage = min(1, n x I / W) for n readings in the window. With no expected interval, coverage = (last − first `observed_at` in the window) / W, and 0 with fewer than two readings. `latest` has no coverage. A limit is evaluated only when coverage is at least its `min_coverage`.

Worked example (examples/sensors/reading-co2-replay.json): the replay file nasa-data/eda_rr1_telemetry.csv has CO2 every 5 minutes. On 7 Oct 2014 the window (14:36, 15:36] holds the 12 samples from 14:41 to 15:36. Their mean is 4,605.67 ppm, which is 3.5003 mmHg at the assumed 760 mmHg, with coverage 12 x 300 / 3,600 = 1. The DosTel1 file has no expected interval; the two-hour window ending 01:04:40 on 2 Apr 2022 holds 80 samples with mean 6.0819 µGy/h, shown as 6.1.

### Evaluation

The sensor monitor evaluates every sensor type of the active environment after each accepted reading and on every freshness change. Only limits with role `warning` and status `active` whose `environments` include the active environment are evaluated; each on its own `aggregation_id`. The status is the first that applies:

| Status | When | `level` |
|---|---|---|
| `no_reading` | The channel has no accepted reading | `null` |
| `stale` | The channel is stale | `null` |
| `beyond_limit` | At least one evaluated limit is crossed (its comparator against its value) | The highest crossed level; ties go to the earlier limit in sensor-limits.json. `limit_id` names it |
| `within_limit` | At least one limit was evaluated and none is crossed | `null` |
| `insufficient_data` | Active limits exist but none had enough coverage | `null` |
| `threshold_pending` | Only limits with status `threshold_pending` exist | `null` |
| `no_limit_on_file` | No limit applies | `null` |

`aggregated_value`, `window_start`, `window_end` and `coverage` report the display aggregation. Limits with role `display_only` (every `info` level) never change the status; they are drawn as marks. A pending limit never raises anything.

### Raising and clearing sensor warnings

- When a limit is crossed, the rule engine raises the warning definition whose condition is `{kind: sensor_limit, limit_id}`, once per definition and channel, unless that warning is already active.
- Escalation: when a definition of a higher level raises for the same sensor type and channel, active lower-level warnings for that sensor type and channel clear with `clear_reason` `superseded`, and the new warning's `previous_warning_id` names the one it replaced.
- Clearing: the limit's `clear` condition (`comparator` and `value`, on the same aggregation) must hold continuously for `dwell_seconds` of `observed_at` time. Then the warning clears with `clear_reason` `condition_false`. Stale data never clears a warning.

### The sensor card

`SensorStatus` is what a card draws, computed by the hub:

| Field | Rule |
|---|---|
| `display_value.text` | The display aggregation value rounded to `decimals` (trimmed when `trim_trailing_zeros`); with no reading, `no_reading_label` |
| `context_label` | The channel's `display_note` when set; otherwise the aggregation label and the channel's `location_label`, joined by ", " (one-hour average; two-hour mean, Columbus module) |
| `shown_level` | The highest of `evaluation.level` and the levels of active warnings (`raised` or `acknowledged`) linked to this sensor type. `null` renders as ui-copy `within_limit_word` when the status is `within_limit`, otherwise as ui-copy `designed_states` for the status (`threshold_pending`, `no_limit_on_file`, `insufficient_data`, `sensor_quiet` for stale, `no_reading`) |
| `limit_marks` | Limits with `card_mark` true in this environment |
| `display_range` | Catalog `display.by_environment[environment]`, or `display.range` |
| `effects` | The hazard's effects after effect filters, source IDs removed, with their body regions |
| `todo` | The sentences of the hazard's `what_to_do` at `shown_level`, or at `info` when it is `null` |
| `here` | The hazard's `by_environment` text for this environment |
| `citation` | "Limit: " and the card-mark limits' source IDs, then "What it can do: " and the effects' source IDs |

Sentence rule, used wherever the contracts say "the sentences of": remove parenthesised source IDs such as `(EV01)` or `(EV01, C25)`, collapse white space, and split after `.`, `!` or `?` followed by white space and an uppercase letter or a quotation mark.

### REPLAY readings

A REPLAY reading describes another mission's cabin at its original date. It is never a person's exposure and never evidence about a person. A warning it raises carries `origin_mode` `REPLAY` and its `evidence_time_label` is ui-copy `designed_states.replay_time` with the original time. Series keep the original dates. An insight may mention it only as a habitat fact (section 17).

### Events

`sensor.status_changed` is sent when a card changes (value text, evaluation, shown level or reading state), at most once per sensor type per expected interval, or per reading when the interval is `null`. `GET /v1/sensors/{sensor_type_id}/series` returns points with their original `observed_at`, the chosen aggregation and the card-mark limits.

## 14. Warnings

### Definitions

Every warning comes from one definition in `warning-rules.json`: `id`, `raised_by`, `level`, `hazard_id` and `hazard_level_index` (when it maps to a hazard level), `sensor_type_id`, `environments`, `scope`, `condition`, `clear` and `content`. Only definitions whose `environments` include the active environment are evaluated.

| `scope` | One active warning per | Visible to |
|---|---|---|
| `habitat` | Definition and channel (or location when there is no channel) | Everyone in the environment |
| `person` | Definition and person | That person only |
| `crew` | Definition | The whole crew |

### The rule engine

The rule engine is deterministic. It runs after each input below, in the same transaction:

| Condition `kind` | Raised when | Cleared when (`clear.by_engine`) |
|---|---|---|
| `sensor_limit` | The sensor monitor finds `limit_id` crossed (section 13) | `condition_false`: the limit's clear condition held for its dwell |
| `sensor_quiet` | A channel of one of `sensor_type_ids` becomes stale (an empty list: any sensor type of the environment) | `condition_false`: the channel reports again |
| `mission_event` | An event of `event_types` is recorded | `clear_event`: an event of `clear_event_types`; or after `expires_after_seconds` |
| `answer` | A person's answer to `question_id` has a presence in `presence`, and, with `within_days_after_event`, the answer is within `days` after the latest event of that type | `never` unless the person resolves it (`person_may_resolve`) or `expires_after_seconds` passes |
| `state_fact` | The fact equals `equals` | `condition_false`: it no longer does |
| `assistant_insight` | An insight of one of `topic_ids` is created for a person (section 17) | The person dismisses it, or the insight expires |
| `ground_silence` | The link is `down`, `next_contact_at` is set and the hub clock passes it by `after_next_contact_seconds` (section 26). The only rule that reads the link; with the threshold `null` it raises nothing | `condition_false`: the link is back, or `next_contact_at` moved later |
| `supply_cover_low` | A supply's computed `cover_uses` is below `below_uses` (section 25); with the threshold `null` it raises nothing | `condition_false`: the cover is no longer below it |

`raised_by` is `sensor_limit` for the two sensor kinds, `assistant` for `assistant_insight`, and `rule` otherwise. A sensor `act` level that has no number in the research (radiation) is handled by a rule on a mission event instead (the space weather alert), never by an invented number.

### Content

The hub renders the warning's words when it raises it:

- `title`, `what` and `why` from the definition's templates (section 11).
- `steps`: with `from: hazard_what_to_do`, the sentences of the hazard's `what_to_do` at the definition's level; with `listed`, the listed items; with `assistant`, the insight's steps.
- `body_regions` from the definition (an assistant warning may narrow them to the insight topic's region).
- `headline` `{fragment, rank, solo}` from the definition.
- `citation` from `citation_template` and `source_ids`.
- `evidence[]`: the readings, mission events, answers, state facts or the person's own records that raised it, with `evidence_time` and `evidence_time_label` (replay time for REPLAY).

### States

`raised → acknowledged`; `raised` or `acknowledged` → `cleared`, `resolved` or `dismissed`. Cleared, resolved and dismissed are final; a new occurrence of the condition raises a new warning.

| Transition | By | Rule |
|---|---|---|
| `acknowledge` | A person who can see it | Adds `{person_id, at}`; the warning stays |
| `add_note` | A person who can see it | Adds a note; state unchanged |
| cleared | The engine | `clear_reason`: `condition_false`, `clear_event`, `expired`, `superseded`, `environment_changed` or `config_changed` |
| `resolve` | A person, only when `clear.person_may_resolve` | Needs a `reason` |
| `dismiss` | The person, only for `raised_by: assistant` | Rule and sensor warnings can never be dismissed |

The assistant can never acknowledge, resolve, clear or lower a rule or sensor warning (`ACTION_NOT_ALLOWED`) and can raise only `info` or `watch` (`LEVEL_NOT_ALLOWED`). The hub computes `allowed_actions` for the session; the client offers only those.

### Headline

The Today headline is computed by the hub from the active (`raised` or `acknowledged`) warnings visible to the person, sorted by `headline.rank` and then `raised_at`:

1. If the first warning's headline is `solo`, the headline is its fragment alone.
2. Otherwise take up to `headline.max_fragments` fragments of non-solo warnings, join them with `headline.separator` and capitalise the first letter.
3. With no active warning, the headline is `headline.none_text`.

With the example documents this gives "Short sleep, CO2 above the limit" on the station, "Solar storm: go to the shelter" on the transit, "Dust after the surface walk" on the Moon base and "Sol 3: getting used to gravity" on the Mars base.

### Effects on the routine

A raised warning starts the routine bindings whose `on` is `warning_raised` with its definition (section 15). The warning lists the overrides it started in `routine_override_ids`.

## 15. Routine

### Inputs

- `routine-schedule.json` items: cadence (`default` and `by_environment`), minutes, instrument, steps, region, and `raise_frequency_when[]` triggers, each with a `new_cadence`, source IDs and basis.
- `routine-rules.json`: `calendar`, `suppressed_overlays`, `display_groups`, `item_policies`, `misses` and `bindings`.

The effective cadence of an item for a person is the `new_cadence` of the most recent active override for that item and person; otherwise `cadence.by_environment[environment]`, or `cadence.default`.

### Occurrences

The routine scheduler creates occurrences per person and mission day. It runs at the start of each mission day, on environment activation, when an override starts or ends, when an overlay changes, and after a mission event. Occurrence IDs are deterministic from person, item, mission day and cause, so a rerun reproduces them.

| Cadence word | Occurrences |
|---|---|
| `daily` | One every day |
| `every_n_days`, `weekly`, `monthly` | Every n days, where n is the cadence's `n`, `calendar.week_days` or `calendar.month_days`. The next occurrence falls n days after the day of the item's last occurrence for that person that ended (`done`, `skipped` or `missed`) since the anchor. With none, the first falls on day 1 counted from `calendar.anchor` (`environment_start`: environment day 1; `mission_start`: mission day 1), or today when that day has passed. Items therefore stay staggered by their own history |
| `per_eva` | Per `item_policies.per_eva`: one before each planned walk (`before_start`), one after each walk ends (`after_end`), or both |
| `after_event`, `mission_phase` | Created by a `cadence_event` binding when the event or phase happens; without a binding the item stays manual |
| `continuous` | One per day, done automatically when an observation with the item's code arrives |
| `as_needed` | None scheduled; created only by triggers, bindings or a person |

Due window: with an item policy `preferred_time`, `due_from` is that time on the day and `due_until` is `due_from` + `window_minutes` (`null`: the end of the day). An occurrence created at a trigger is due from the trigger instant; an `after_end` walk occurrence is due from the end of the walk. With no time it is due all day.

States: `not_due` (a future day) → `due` (at `due_from`) → `done`, `skipped` or `missed` (when `due_until` passes). `due` → `later` when a person picks a later time, then `due` again at that time. While an overlay in `suppressed_overlays` is active at `due_from`, the occurrence waits, `suppressed_by_overlay` names the overlay, and it becomes due when the overlay ends; its `due_until` moves by the same amount.

Group: `today` when the occurrence is on today's mission day and not `not_due`; otherwise the display group whose `cadence_words` contain the effective cadence word.

`end_text` is rendered from ui-copy `occurrence_end_text`, first match:

| Case | Key |
|---|---|
| Done today; done on an earlier day | `done_today` with `{time}`; `done_earlier` with `{day_label}` |
| Skipped; missed | `skipped` with `{reason}`; `missed` |
| Waiting for an overlay | `suppressed` with `{overlay}` |
| Later | `later` with `{time}` of the new `due_from` |
| Due, with a specific `due_from` (preferred time, trigger or end of walk) | `due_now` |
| Due all day | `due_today` |
| A walk item, no walk planned | `no_walk_planned` |
| A walk item, a walk planned; no walk yet in this environment | `next_walk` with `{day_label}`; `first_walk` with `{day_label}` |
| Any other future occurrence | `not_due` with `{day_label}` |

`{day_label}` is the profile's `inline_template` for the occurrence's day (day 21, sol 7).

A person completes, skips (with a reason from ui-copy `skip_reasons`) or moves an occurrence with `POST /v1/routine/occurrences/{id}/actions`. Completing may link observation and answer IDs.

### Misses

When a person has missed `misses.ask_barrier_after_misses` occurrences of the same item within `misses.window_days`, the next daily check adds `misses.question_id` (what got in the way). `RoutineSchedule.misses` reports the count and whether the question is due. A miss never changes participation (section 8).

### Bindings and overrides

A binding makes one trigger of routine-schedule.json run automatically. `binds` names the trigger by `item_id` and `trigger_index` and copies its words in `trigger_text` (or names a cadence event with `event_text`); the loader rejects any drift. When its `on` happens, its `effect` applies until its `until`:

| `on` | Fires when |
|---|---|
| `warning_raised` | A warning of one of `definition_ids` is raised |
| `mission_event` | An event of one of `event_types` is recorded |
| `overlay_ended`, `overlay_planned` | An overlay of that kind ends or is planned |
| `answer` | An answer to `question_id` has a listed presence |
| `observation` | An accepted observation with `code` satisfies `comparator` and `value` in `unit` |
| `environment_activated` | One of `environments` becomes active |

| `effect` | Result |
|---|---|
| `replace_cadence` | A RoutineOverride with the trigger's `new_cadence` |
| `add_occurrences` | Occurrences at the trigger (`at_trigger`), at listed day offsets and times, every `every_minutes`, and one when the trigger's warning clears (`at_clear`), each with `cause.binding_id` |
| `add_questions` | The question set is added to the next (or each) daily check; `repeat_after_minutes` asks again |

| `until` | The effect ends when |
|---|---|
| `warning_cleared` | The warning that fired it clears |
| `effect_complete` | The added occurrences or questions are done |
| `days` | `days` have passed |
| `person_stops` | A person with `role` ends it |
| `mission_event` | An event of one of `event_types` is recorded |

`audience` is `person` (the person concerned), `everyone_in_location` or `all_crew`. A RoutineOverride records the binding, trigger index and text, `started_by` (`rule`, `sensor_limit` or `person`), its source, the new cadence and its words, the reason, the end rule and the release. `routine.override_changed` is sent when one starts or ends.

Triggers with no binding stay manual: a crew medical officer or the ground starts one with `POST /v1/routine/overrides` (the trigger index must exist on the item) and ends it with an `end` action. The assistant may add one occurrence of an existing item for a person (cause `assistant`, with the insight ID) and nothing else.

## 16. Someone needs help

### What never happens

The help module is a state machine over `responder-flows.json`. Nothing in it waits for the model, the network, another sample or the ground. Every step, question and treatment line comes from responder-flows.json; every other word comes from help-presentation.json and ui-copy.json. Medicines appear only as "as in the onboard procedure": no object, request or response in this contract has a dose field, and the assistant may not name one (section 17).

### Opening

`POST /v1/help-sessions` (or its alias `POST /v1/urgent-events`) opens a session for `self`, a `crewmate` or an `unknown` person, from the button, a red flag answer (`entry.kind` `red_flag`, opening straight on the flow from help-presentation `red_flag_flows`), or a warning. The hub:

1. pins the versions of responder-flows, help-presentation and ui-copy (`config_pins`) for the life of the session;
2. puts the session at `start_node_id` and replays `offline_path` (answers the client took on its cached first screen before the hub answered) in order, each at its own client time; a replayed answer that does not match the current node stops the replay and is logged;
3. starts the crew broadcast and sends `help.session_changed` and `help.broadcast_changed`.

A client that cannot reach the hub shows the start node from its cached responder-flows.json and help-presentation.json and sends the answers as `offline_path` when it can.

### Nodes and level

The session is at a triage node (a question with answers) or at a flow (title, plain words, fixed immediate steps that can be ticked, questions, measurements, treatment steps, the log for the ground (last, never blocking), and the note for the active environment). The session level is the flow's level in help-presentation `flow_levels` (or `default_flow_level`) at a flow, and `default_flow_level` at a triage node. Flow body regions are a list; an empty list means the flow is not about one place. The session's `body_region` is the flow's region when it has exactly one and no person set one; otherwise `null` until a person sets it.

### Actions

`POST /v1/help-sessions/{id}/actions` with `If-Match`. Each action is one transition, adds a log line from help-presentation `log_templates` (or `answer_log` for triage answers) and sends `help.session_changed`.

| Action | Allowed when | Effect |
|---|---|---|
| `answer` (`node_id`, `answer_index`) | At a triage node equal to `node_id` | Path entry; the node becomes the answer's `goes_to` (a triage node or a flow) |
| `switch_flow` (`flow_id`) | Open | Goes straight to a flow |
| `tick_step`, `untick_step` (`step_index`) | At a flow | Sets or clears `ticked_at` and `ticked_by` |
| `call_crew` | Open | Sets `crew_called_at` the first time; the broadcast shows it |
| `join`, `leave` | Open | Adds or removes the session person as a helper |
| `start_over` | Open | Back to the start node; the path and log keep everything |
| `set_body_region`, `set_place` | Open | Sets them; `null` region means not about one place |
| `add_note` (`note`) | Open | Log only |
| `ask_assistant` (`text`) | Open | Opens or continues the session's conversation. The session's `assistant.status` shows `off` (never asked), `waiting`, `answering`, `stopped` or `unavailable`; with the model stopped the log shows `assistant_line.stopped_text`. The action returns at once |
| `close` (`disposition`, `note`) | Open | `closed` with `closed_at`; the broadcast ends; no further actions (`SESSION_CLOSED`) |

Concurrency: `tick_step`, `untick_step`, `call_crew`, `join`, `leave` and `add_note` commute. When their `If-Match` is stale, the hub applies them to the current revision and returns 200 instead of `STALE_VERSION`. Every other action needs the current revision. The client sends `client_time`; the log records both client and hub times.

### Crew broadcast and readings panel

The broadcast text is help-presentation `broadcast.template` followed by `helping_template` with the helpers' names, or `nobody_helping`. Its level is the session level. `GET /v1/help-sessions/{id}/subject` returns the subject's latest observations for `readings_panel.codes` no older than `readings_panel.max_age_minutes`, with origin and age, and allergies and medicines by name only; it is readable by the opener, helpers and subject while the session is open.

When the session reaches a flow, the role engine proposes who does what (section 24); the roles show below the steps and the steps never wait for them. During CPR the camera coaching samples are appended to the session (section 23). A plan run's `escalate` opens a session with `entry.kind` `plan_step` (section 22).

### Message for the ground

The help session is complete on board: its steps, crew call, readings panel, level and close never need the ground. A message for the ground is an optional log of the session, a side effect.

The For the ground card shows the draft first: `GET /v1/help-sessions/{id}/ground-message-preview` (section 26). `POST /v1/help-sessions/{id}/ground-messages` is the Tell the ground button. Pressing it approves the draft as it stands and is the explicit action, so the message is created `queued` at once, with `session_revision`, `text_sha256`, `leaves_at` and `catch_up` (section 26), and the call returns. The lines follow help-presentation `ground_message`, in order: `header_template`; `answers_template` with the answer log lines joined by ". "; `steps_template` with the ticked steps (only when a step is ticked); `crew_template` with the crew call time and the helpers (only when the crew was called); then `extra_note` when the request has one. The session log gets `log_templates.ground_message_queued`.

`queued → sent → acknowledged`

| State | When | `status_text` |
|---|---|---|
| `queued` | Created | `queued_template` with `{time}` of `queued_at` while the link is `up` or `delayed`; `waiting_template` with `{in_time}` while it is `down`, when `leaves_at` is the next contact |
| `sent` | The simulated comms queue sends it, at once when the link is `up` or `delayed`, otherwise when it comes back (section 12). `expected_arrival_at` is `sent_at` + `one_way_delay_s` | `sent_template` with `{time}` and `{delay}` |
| `acknowledged` | The ground console records a receipt; it reaches the crew after the one-way delay and sets `acknowledged_at` | `acknowledged_template` with `{time}` |

- The ground may reply (`reply`); the reply reaches the crew after the same delay and is added to the help log with `log_templates.ground_reply`. A reply never changes the session's node, level or steps by itself.
- A receipt is not a clinical review. The session may close before the message is sent; the message still leaves when the link allows.
- No action of the session, and no other object, waits for `sent`, `acknowledged` or a reply.

## 17. Assistant conversation

### Runtime

The model runs locally as one model release (model ID, revision, SHA-256 of the weights, prompt version and tool catalog version). `GET /v1/assistant/status` reports `running`, `stopped`, `loading` or `unavailable`; an operator starts or stops it with `POST /v1/assistant/runtime`. Stopping the model stops nothing else: help, warnings, routine, records, measurement cards and the kit map are deterministic.

### Turns

A conversation is pinned to a model release and a configuration release, and has a context (`general`, `episode`, `help_session`, `warning`, `reading` or `insight`). `POST /v1/assistant/conversations/{id}/messages` returns 202 at once; the turn runs as a job and streams on `GET /v1/assistant/conversations/{id}/stream` (`turn.started`, `token`, `tool`, `message.completed`, `turn.failed`). One turn runs at a time per conversation; a second send returns `INVALID_TRANSITION`. `POST .../cancel` stops a running turn.

The controller builds the model's context from the session person's own records and the pinned evidence release, never from another person's records, the journal or participation data. Each turn the model proposes one action from section 6. The controller then:

1. validates it against the ModelAction schema; one repair is allowed (`max_schema_repairs`), then `MALFORMED_MODEL_OUTPUT`;
2. checks capability codes, source IDs, body regions, question choices and plan step IDs (`choose_plan_step`, section 22) against the active configuration;
3. for `request_measurement` (the tool `propose_measurement` maps to it), runs the kit gate of section 21. When the kit cannot do it, the request is recorded as `unavailable` with its `kit_check`, a CapabilityGap is opened, the assistant message lists the kit text in `limitations`, and nothing is shown as a task;
4. checks the text against every `forbidden_output` rule in assistant-policy.json (no dose, no level above watch, no habitat reading as the person's exposure, no invented threshold, never delaying or hiding the help steps). A breach discards the output (`MODEL_OUTPUT_REJECTED`) and the conversation gets a `notice` message;
5. stops the loop after `max_iterations` or `turn_timeout_seconds` (`MODEL_TIMEOUT`). With the model down, the turn fails with `MODEL_UNAVAILABLE` and a notice that names what still works.

### Insights

An Insight is a pattern in the session person's own records for one topic of assistant-policy `insight_topics`. It may raise one `info` or `watch` warning through the topic's `warning_definition_id` and add one occurrence of an existing routine item (section 15). Nothing else.

Validation rule for every insight (VALIDATION V10). The hub rejects the insight and stores nothing when any of these fails:

1. Every entry of `evidence[]` has kind `observation`, `answer`, `daily_check` or `routine_occurrence`, and the record it names belongs to `person_id` (its `patient_id` or `person_id` equals the insight's).
2. No evidence entry names a sensor reading, a sensor channel, any record with `origin_mode` `REPLAY`, or another person's record. A habitat reading is never evidence about a person.
3. A habitat reading may appear only in `habitat_facts[]`, as a separate fact about the place. The model names only the `reading_id`; the hub writes the `text` from ui-copy: `designed_states.replay_note` for a REPLAY reading ("Replay of OSDR Rodent Research-1 ISS environmental telemetry, 2014. A record of another mission's cabin, not your exposure."), `designed_states.habitat_fact` otherwise. The insight's `title`, `text` and `why` must not describe a habitat reading as the person's exposure (forbidden output `replay_as_exposure`).
4. `level` is `info` or `watch`.

The person may dismiss an insight; dismissing it dismisses its warning. `insight.changed` is sent to that person only.

## 18. Event stream

`GET /v1/events` is a server-sent event stream by default (`id` is `seq`, `event` is the type, `data` is the Event as JSON) and returns one EventPage with `Accept: application/json`. The stream sends a comment line every 15 seconds to keep the connection open.

- `seq` is a hub-wide, strictly increasing integer. Clients resume with `Last-Event-ID` or `after`. A cursor older than the retained history returns `410 CURSOR_EXPIRED`; the client reloads its views and resubscribes without a cursor.
- Events are emitted after the change commits, in commit order, at least once. Clients ignore a `seq` they have already applied.
- Each event has a `scope`. A session receives `habitat`, `crew` and `ground` scope events its roles allow, and `person` scope events only for its own person. Participation events for one person never go to anyone else (section 8).

| Type | Data | Scope |
|---|---|---|
| `config.release_activated` | ConfigRelease | crew, ground, operator |
| `environment.change_proposed`, `environment.change_rejected` | EnvironmentProfile | crew, operator |
| `environment.activated` | EnvironmentProfile | crew, ground, operator |
| `overlay.changed` | ActivityOverlay | person, crew |
| `mission.event_recorded` | MissionEvent | crew, operator |
| `ground_link.changed` | GroundLink | crew, ground, operator |
| `sensor.status_changed` | SensorStatus | habitat |
| `sensor.quiet`, `sensor.resumed` | SensorChannel | habitat, operator |
| `warning.raised`, `warning.updated`, `warning.cleared` | Warning | person, habitat or crew, as the warning's scope |
| `routine.occurrence_changed` | RoutineOccurrence | person |
| `routine.override_changed` | RoutineOverride | person, habitat, crew |
| `help.session_changed`, `help.broadcast_changed` | HelpSession, CrewBroadcast | crew |
| `ground_message.changed` | GroundMessage | crew, ground |
| `assistant.status_changed` | AssistantStatus | crew, operator |
| `insight.changed` | Insight | person |
| `job.progress` | Job | person, operator |
| `measurement_request.changed` | MeasurementRequest | person |
| `handoff.changed` | Handoff | person, ground |
| `participation.streak_changed` | StreakView | person |
| `participation.crew_goal_changed` | CrewGoalView | crew |
| `plan_run.changed`, `plan_run.step_done`, `plan_run.escalated` | PlanRun | person, crew |
| `guided_check.beat` | `{guided_check_id, beat_count, at, seconds_left}` | person |
| `guided_check.checked` | GuidedCheck | person |
| `coaching.sample` | CoachingSample | crew |
| `role.proposed`, `role.changed` | RoleAssignment | crew |
| `skill.practiced` | SkillRecord | person, and crew for the crew_medical_officer role only |
| `supply.used` | SupplyUse | crew |
| `drill.changed` | Drill | crew |

## 19. Screen to endpoint map

Every screen loads with `GET` calls, acts with the listed commands and stays current with the event stream (section 18). A screen never computes a level, a state, a due time or a label: it draws what the hub returns, with words from the cached configuration (section 11). On `config.release_activated` a client refetches the configuration and then the views.

| Screen or element | Loads | Acts | Updates on |
|---|---|---|---|
| Start and sign in | `GET /v1/health`, `POST /v1/sessions`, `GET /v1/context`, `GET /v1/config`, `GET /v1/config/{name}` | `DELETE /v1/sessions/current` | `config.release_activated` |
| Ground link indicator, on every screen | `GET /v1/ground-link` (also inside `GET /v1/context`, `GET /v1/mission` and the Habitat view) | None. Operators use `POST /v1/demo/ground-link` in demo mode only | `ground_link.changed` |
| Crew broadcast line, on every crew screen | `GET /v1/help/broadcasts` | Opens the help session | `help.broadcast_changed` |
| Today | `GET /v1/views/today` | `POST /v1/warnings/{id}/actions`, `POST /v1/routine/occurrences/{id}/actions`, `POST /v1/insights/{id}/actions` | `warning.*`, `routine.occurrence_changed`, `routine.override_changed`, `sensor.status_changed`, `insight.changed`, `participation.*`, `environment.activated` |
| Daily check | `POST /v1/daily-checks` (start or get today's), `GET /v1/daily-checks/{id}`, `GET /v1/config/questionnaires` | `POST /v1/daily-checks/{id}/answers`, `POST /v1/daily-checks/{id}/submit`, `POST /v1/journal` | The submit response carries the new StreakView |
| How you are | `GET /v1/views/how-you-are`, `GET /v1/me/streak`, `GET /v1/me/patches`, `GET /v1/crew-goal` | None | `participation.streak_changed`, `participation.crew_goal_changed` |
| Habitat | `GET /v1/views/habitat`, `GET /v1/sensors/{sensor_type_id}/series` for a card's detail | `POST /v1/warnings/{id}/actions` | `sensor.status_changed`, `sensor.quiet`, `sensor.resumed`, `warning.*`, `environment.activated`, `ground_link.changed` |
| Routine | `GET /v1/routine/today`, `GET /v1/routine/week`, `GET /v1/routine/tasks/{item_id}` | `POST /v1/routine/occurrences/{id}/actions`; a crew medical officer: `POST /v1/routine/overrides`, `POST /v1/routine/overrides/{id}/actions` | `routine.occurrence_changed`, `routine.override_changed` |
| Records | `GET /v1/views/records`, `GET /v1/observations/{id}`, `GET /v1/episodes/{id}`, `GET /v1/reports/{id}`, `GET /v1/handoffs/{id}` | `POST /v1/episodes`, `POST /v1/observations`, `POST /v1/reports`, `POST /v1/handoffs` and `.../approve` (optional) | `measurement_request.changed`, `handoff.changed` |
| Measurement card | `GET /v1/capabilities`, `GET /v1/measurement-requests/{id}` | `POST /v1/measurement-requests`, `POST /v1/measurement-requests/{id}/actions` | `measurement_request.changed`, `job.progress` |
| Kit map (This kit) | `GET /v1/kit-map?environment_id=`, `GET /v1/supplies` | `POST /v1/kit-map/what-if` with instruments, supplies or skills (stores nothing) | `config.release_activated`, `environment.activated`, `supply.used` |
| Plan card (Assessment, gap card) | `GET /v1/conditions/{condition_id}/plan`, `GET /v1/plan-runs/{id}`, `GET /v1/plan-runs/{id}/roles` | `POST /v1/plan-runs`, `POST /v1/plan-runs/{id}/actions`, `POST /v1/plan-runs/{id}/roles` | `plan_run.*`, `role.*`, `ground_link.changed` |
| A crewmate checks (guided check) | `GET /v1/guided-checks/{id}` | `POST /v1/guided-checks`, `POST /v1/guided-checks/{id}/beats`, `POST /v1/guided-checks/{id}/actions` | `guided_check.beat`, `guided_check.checked` |
| Skills and drills | `GET /v1/me/skills`; the medical officer: `GET /v1/crew/readiness` | `POST /v1/drills`, `POST /v1/drills/{id}/complete` | `skill.practiced`, `drill.changed` |
| Stay or return (Moon base) | `GET /v1/return-readiness?subject=` | Prepare steps through `POST /v1/plan-runs/{id}/actions` | `plan_run.*` |
| Someone needs help | Cached responder-flows.json and help-presentation.json first, then `POST /v1/help-sessions` (or `GET /v1/help-sessions/{id}` to join), `GET /v1/help-sessions/{id}/subject`, `GET /v1/help-sessions/{id}/roles`, `GET /v1/help-sessions/{id}/coaching`; the For the ground card: `GET /v1/help-sessions/{id}/ground-message-preview` | `POST /v1/help-sessions/{id}/actions`, `POST /v1/help-sessions/{id}/roles`, `POST /v1/supplies/{supply_id}/uses`; Tell the ground: `POST /v1/help-sessions/{id}/ground-messages` | `help.session_changed`, `help.broadcast_changed`, `ground_message.changed`, `role.*`, `coaching.sample` |
| Assistant | `GET /v1/assistant/status`, `POST /v1/assistant/conversations`, `GET /v1/assistant/conversations/{id}`, `GET /v1/assistant/conversations/{id}/stream` | `POST /v1/assistant/conversations/{id}/messages`, `POST /v1/assistant/conversations/{id}/cancel` | `assistant.status_changed`, `insight.changed` |
| Environment switch | `GET /v1/mission`, `GET /v1/environment/changes` | `POST /v1/environment/changes`, `POST /v1/environment/changes/{id}/actions`, `POST /v1/mission-events`, `POST /v1/overlays`, `POST /v1/overlays/{id}/end` | `environment.*`, `overlay.changed`, `mission.event_recorded` |
| Ground console (ground role) | `GET /v1/ground/inbox`, `GET /v1/ground/log?since=`, `GET /v1/ground-messages/{id}`, `GET /v1/handoffs/{id}` | `POST /v1/ground/messages/{id}/receipt`, `.../reply`, `POST /v1/ground/handoffs/{id}/receipt`, `.../reply` | `ground_message.changed`, `handoff.changed` |
| Operator panel | `GET /v1/assistant/status`, `GET /v1/sensor-channels`, `GET /v1/config/releases` | `POST /v1/assistant/runtime`, `POST /v1/config/releases`, `POST /v1/config/releases/{id}/activate`, `POST /v1/participation/rebuild`, demo only `POST /v1/demo/ground-link` | `assistant.status_changed`, `sensor.quiet`, `config.release_activated` |

Two rules hold on every screen:

- The help screen must draw its first question from the cached documents without any network call; the hub accepts the answers afterwards as `offline_path` (section 16).
- Nothing on any screen depends on the model or the ground link. With the model stopped the assistant panels show ui-copy `designed_states.assistant_stopped`; with the link down the indicator shows `ground_link_words.down` and `designed_states.ground_next_contact`, and the four readers of section 26 show their words; everything else is unchanged.

## 20. Backend implementation guide

### Modules

Build the hub as these modules. Each engine is a deterministic function of the active configuration release, the stored state and the clock; none calls the model or the network.

| Module | Owns | Reads | Sections |
|---|---|---|---|
| Configuration loader | ConfigDocument, ConfigRelease; the 16 loader checks | The configuration directory and the three research files | 11 |
| Clock | Mission day, MET, day labels; an injectable clock for tests and replay | Mission clock configuration, environment profiles | 12 |
| Identity | Sessions, roles, adapter tokens | Local credentials | 5, 20 |
| Event log | `seq`, scopes, the outbox, SSE | Every committed change | 18 |
| Mission and environment | EnvironmentProfile records, overlays, mission events, state facts | environment-profiles.json, environment-hazards.json | 12 |
| Ground link and comms queue | GroundLink, the simulated queue for ground messages and handoffs | Profile `comms_queue`, `earth_delay` | 12, 16 |
| Sensor ingest and monitor | SensorChannel, SensorReading, SensorEvaluation, SensorStatus | sensor-catalog.json, sensor-limits.json | 13 |
| Rule engine | Warning | warning-rules.json and its inputs | 14 |
| Routine scheduler | RoutineOccurrence, RoutineOverride, RoutineSchedule | routine-schedule.json, routine-rules.json, overlays | 15 |
| Records | Daily checks, answers, journal, observations, episodes, measurement requests, reports, assessments, reviews, handoffs | questionnaires.json, indicator-catalog.json | 2 to 4 |
| Kit map | KitMapView, KitMapWhatIf, KitCheck, capabilities | kit-map.json, onboard-plans.json | 21 |
| Plan engine | PlanView, PlanRun, ReturnReadiness | onboard-plans.json, kit-map.json, environment-profiles.json, the roster, overlays, SkillRecords, SupplyStock | 22, 27 |
| Teach and check | GuidedCheck, CoachingSample, the crewmate_palpation Observation | skills-catalog.json, the camera adapter | 23 |
| Role engine, skills and drills | RoleAssignment, SkillRecord, Drill, practice patches | role-rules.json, skills-catalog.json, participation-rules.json, the roster and overlays | 24 |
| Supplies ledger | SupplyStock, SupplyUse, SupplyCoverView | kit-map.json, onboard-plans.json | 25 |
| Catch-up composer | GroundMessagePreview, `catch_up`, `leaves_at`, GroundLogView | help-presentation.json, ui-copy.json, the ground link | 26 |
| Help | HelpSession, CrewBroadcast, GroundMessage, SubjectSummary | responder-flows.json, help-presentation.json, ui-copy.json | 16 |
| Assistant controller | Conversations, turns, insights, model runtime | assistant-policy.json, tool-catalog.json, the pinned evidence release | 6, 17 |
| Participation | StreakView, PatchAward, CrewGoalView | CheckCompletion events, participation-rules.json | 8 |
| Views | TodayView, HowYouAreView, HabitatView, RecordsView, GroundInbox | The modules above | 19 |
| HTTP API | The operations in api/openapi.yaml | api/schemas | 5 |

### Sessions and roles

`POST /v1/sessions` exchanges a local credential (a PIN or passphrase configured on the hub, never in a configuration document) for a bearer token, returned once. `kind` is `crew` (with `person_id`), `station`, `ground`, `operator` or `editor`; adapters use per adapter tokens configured on the hub. A crew session carries the person's roles from the roster (`crew`, and `crew_medical_officer` for the person the crew designates). Identity and roles come only from the session, never from a body or a model output.

| Role | May |
|---|---|
| `crew` | Read and act on their own records, routine, warnings, insights and conversations; open, join and act on help sessions; confirm or reject environment changes |
| `crew_medical_officer` | Everything `crew` may, plus start and end manual routine overrides, record reviews, propose environment changes, read crew readiness and every plan run |
| `station` | A shared habitat screen with no person: read habitat views, broadcasts and the crew goal; open a help session |
| `ground` | The ground console: read what reached the ground and the catch-up log, record receipts and replies, start and end manual overrides. Nothing on board waits for this role |
| `operator` | Run the demo: model runtime, mission events, releases, participation rebuild, demo only `POST /v1/demo/ground-link` |
| `editor` | Propose configuration releases; the research workspace |
| `adapter` | Ingest sensor readings and measurement attempts for its own channels |

Each operation lists its roles in `x-roles` in api/openapi.yaml; any other role gets `403 UNAUTHORIZED_SCOPE`.

### Build order

1. Configuration loader with all 16 checks, run against the research files and api/examples/config. Nothing else starts until a release validates.
2. Clock, identity, the event log and the error envelope. Every handler validates its body against api/schemas/requests.schema.json and its response against the response schema in tests.
3. Mission and environment, then the ground link. From here `GET /v1/mission` and `GET /v1/ground-link` work.
4. Help (section 16). It depends only on steps 1 to 3 and must work with every later module absent, the model stopped and the link down.
5. Sensor ingest and monitor, then the rule engine, then the routine scheduler.
6. Records, the kit map and the kit gate; then the supplies ledger, the plan engine, teach and check, the role engine with skills and drills, and the catch-up composer (sections 22 to 27). Plans and roles depend only on configuration, the roster and the clock, and must work with the model stopped and the link down.
7. Views and the event scopes.
8. The assistant controller, last: nothing above depends on it.
9. Participation (section 8), which reads only CheckCompletion events.

### Rules for every module

- One command is one transaction: the state change, the audit event and the outbox entry commit together, and events go out only after the commit.
- Engines take time from the injectable clock, never from the wall clock. Sensor windows follow `observed_at` (section 13), so a REPLAY file evaluates the same way on any day.
- Rendering is deterministic: the same release, state and clock give byte-identical views. IDs that the contracts call deterministic (routine occurrences) are derived, not random.
- Every object with a `revision` increments it on each change and serves it as the `ETag`.
- Nothing waits on the model, the network or the ground. Long work (an assessment, a model turn) runs as a Job; the command returns at once.
- Nothing is hardcoded: when a module needs a word or a number that no document holds, add it to a document and its schema, then to the loader checks. Do not put it in code.
- Storage is the implementer's choice. It needs transactions, a strictly increasing sequence for events and a separate store for journal entries.

### Test vectors

These values come from the example documents and examples in api/examples. An implementation run with the same documents, readings and clock must reproduce them exactly.

| Input | Expected |
|---|---|
| CO2 replay, nasa-data/eda_rr1_telemetry.csv, window (14:36, 15:36] on 7 Oct 2014, assumed 760 mmHg | 12 samples, mean 4,605.67 ppm = 3.5003 mmHg, coverage 1.0; limit `co2_1h_act` (above 3 mmHg, act) crossed; card text 3.5; warning `act` raised by `sensor_limit` |
| DosTel1 replay, two hours ending 01:04:40 on 2 Apr 2022 | 80 samples, mean 6.0819 µGy/h, card text 6.1, status `threshold_pending` |
| Today headline per environment | Station "Short sleep, CO2 above the limit"; transit "Solar storm: go to the shelter"; Moon base "Dust after the surface walk"; Mars base "Sol 3: getting used to gravity" |
| Day labels | Station "flight day 19 of 30", MET `018/09:10:16`; transit "mission day 96"; Moon base "surface day 4"; Mars base "sol 3 on Mars" |
| Routine counts (due, later, done) | Station 2, 3, 1; transit 2, 1, 0; Moon base 3, 1, 1; Mars base 3, 1, 0 |
| Occurrence end texts, with the completion history of design/crew-data.js (for example body mass last done on day 14) | Station blood pressure "due today", vision check "13:00", reaction test "done 07:38", body mass "day 21"; Moon base pre-walk check "next walk day 6" (walk planned on surface day 6); Mars base pre-walk check "first walk sol 8" (walk planned on sol 8) |
| Kit map, example document | Station 0 measured here, 3 report only (ICL14, ICL95, ICL18), 4 need a test not aboard (ICL10, ICL3, ICL64, ICL100); Mars base 1, 3, 3 (ICL100 measured by the ultrasound); station what-if with `ecg_single_lead`: ICL10 moves to measured here, 1, 3, 3 |
| Kit gate | `request_measurement` for `ecg_rhythm` on the station: `can` false, `missing_instrument_ids` [`ecg_single_lead`], text "This kit can't: needs an ECG", error `KIT_CANNOT_PERFORM` |
| Streak after submitting day 19 with days 1 to 18 checked | `current_streak_days` 19, `longest_streak_days` 19, `completed_days_total` 19 |
| Help path | Answers `air_safe` 0, `responding` 0, `severe_bleeding_check` 1, `what_happened` 6 reach the eye injury flow; `air_safe` 0, `responding` 1, `breathing` 1 reach `not_breathing` in three answers |
| Plans aboard, example documents | 7 of 7 kit-map rows have a plan aboard in all four environments; station KitMapView `plans_aboard` 7, `plans_to_write` empty |
| ICL10's plan on the Moon base at 09:36, link down since 09:20:40, next contact 11:20:40 the next day, Ilya Brandt and Sofia Reyes on the walk | `check_another_way` step `who_can` [Tomas Lind]; medical officer step `who_can` empty; `ground_log` `link_text` "Ground link down · next contact in 25 hours (Sim). The log goes then. Nothing here waits for it."; with the link up only `link_text` ("Ground link up · the log goes now") and `leaves_at` (`null`) differ |
| Guided check, pulse at the wrist | 92 beats in 60 seconds, camera 96 bpm: `count_per_minute` 92, `difference` `-4`, team default tolerance 10, `agree`, "Your count 92 · cameras 96 · they agree", a `crewmate_palpation` Observation; 65 against 96: `disagree`, "Your count is 31 lower than the cameras. ...", no Observation |
| Roles, station CPR preset with Ilya Brandt the patient | Sofia Reyes medical officer (`second_officer`, context "Ilya Brandt is the patient"), Tomas Lind compressions ("CPR practiced day 12"), Mara Okafor brings the AED and the restraint board; second helper and communicator unfilled |
| Supplies, Moon base at 12:10 | Eye rinse: 6 packed, 1 used, `quantity_now` 5, `cover_uses` 5; eye shield not aboard, so the shield step is `not_aboard` |
| For the ground, act 6 at 12:05 | Preview `status_text` "Waiting for the next contact in 23 hours. Nothing here waits for it."; the queued GroundMessage's `text_sha256` equals the preview's; `leaves_at` 2026-11-14T11:20:40Z |

Each example file names the call it answers in its `summary`. A backend test can load an example's `value`, run the call against the same inputs and compare.

### Mocking and validation

[api/README.md](api/README.md) explains how to validate the package (the Deno validator and the OpenAPI meta-schema check) and how to run a mock of the whole API with Prism from api/openapi.yaml, so client work can start before the hub exists.

## 21. Kit map

The kit map answers "what can this kit confirm here?" for the conditions of NASA's ICL 1.0 v3.2 list. It is computed by the hub from `kit-map.json` and the active environment's kit; no state is stored.

### The document

| Field | Meaning |
|---|---|
| `version` | The authors' label. The served version is the file's SHA-256 (section 11) |
| `example` | `true` for an illustrative document; the hub refuses to activate it while the mission mode is LIVE |
| `notice` | Shown on every page that renders the document's clinical content (for example "Unreviewed draft: needs clinician review before live guidance.") |
| `instruments[]` | `id`, `name`, `needs_text` (how it reads after "needs", default `name`), `capability_codes`, `method` (acquisition method, default `manual_instrument`) |
| `capabilities[]` | One per capability code any instrument provides: `code`, `label`, `body_region`, `operator_requirement` (`self`, `crewmate` or `crew_medical_officer`: always someone on board), `expected_duration_seconds`, `basis`. `GET /v1/capabilities` serves these words |
| `kits` | Instrument IDs aboard per environment |
| `candidates` | Instrument IDs the planning view offers in a what-if |
| `conditions[]` | `condition_id` (an ICL row of condition-assessments-119.csv), `name`, `body_regions` (`[]`: not about one place), `confirming_tests[]`, `cliff_url`, optional `report_only_findings` (display only), optional `source_ids`, `review_state` (`unreviewed`, `reviewed`, `rejected`), `written_by`, `checked_by` (required when `reviewed`), `plan_id` (the onboard-plans.json plan, or `null` while none is written, which loader check 15 reports), `ground_needs` (the form's Communications and Support Needs: `diagnosis`, `treatment`, `follow_up` booleans with `quote`, `source_id`, `page`), `treatment_scope` (`quote`, `source_id`, `page`) and `rtdc` (the form's Table 4, section 27), each copied from the saved form or `null` |
| `supplies[]`, `stock`, `candidate_supplies`, `candidate_skills` | Supplies, the packed stock per environment, and what the planning view offers to add (section 25) |
| `confirming_tests[]` | Alternatives: any one test confirms the condition. Each has `name`, optional `test_id`, and `instruments`: all of them are needed; `[]` means no instrument (history, interview or an examination by a trained person) |

The example document is [api/examples/kit-map.example.json](api/examples/kit-map.example.json): seven ICL rows, `example: true`, every row `unreviewed` with `written_by` and `checked_by` `null`.

### State of a condition

For a kit K (the instrument IDs aboard), first match:

1. `measured_here`: some test has a non-empty `instruments` list that is entirely in K.
2. `report_only`: some test has an empty `instruments` list.
3. `needs_test_not_aboard`: otherwise.

`missing_instruments`: empty for `measured_here`. Otherwise, among the tests with a non-empty `instruments` list, the instruments of each test that are not in K; the smallest such set, ties to the first test in document order; empty when no test needs an instrument. Rows with `review_state` `rejected` are left out of every view and count.

### View and what-if

`GET /v1/kit-map?environment_id=` (default: the active environment) returns a KitMapView:

- `counts` of the three states and `total`;
- `regions[]`: for each body region in the fixed order, and `whole_body` for conditions with `[]`, the counts of the conditions that list it and `marks`, the states present, in the order `measured_here`, `report_only`, `needs_test_not_aboard`;
- `conditions[]` with `state`, each test's `fully_aboard` and `missing`, `missing_instruments`, `missing_text` (ui-copy `kit_check_templates.cannot` with the `needs_text` of the missing instruments joined by " and "), `review_state`, `plan_id`, `plan_written_for`, `plan_aboard` (section 22), `ground_needs`, `treatment_scope` and `rtdc`;
- `plans_aboard`, `plans_written_for_condition`, `plans_from_family` and `plans_to_write` (the rows without a plan aboard here);
- `kit`, `candidates` (with `aboard`), `example`, `kit_map_version` and `kit_map_label`.

`POST /v1/kit-map/what-if` with any of `add_instruments`, `add_supplies` (`supply_id`, `quantity`) and `add_skills` (`skill_id`, `person_ids`), and optional `environment_id`, returns KitMapWhatIf: the same computation with those instruments added, `base_counts`, `what_if_counts`, `regions` and `changed` (each condition whose state moved). Instruments already aboard are listed in `ignored_instrument_ids`. Rings move only with instruments; supplies and skills (counted as practiced now) recompute `plans_changed` (each plan whose `plan_aboard` or `waits_for_medical_officer` changes) and `cover_changed` (each added supply's cover in uses, before and after). It stores nothing, sends no event and takes no `Idempotency-Key`; `persisted` is always `false`.

### The kit gate

Every measurement request, from a person or from the assistant's `request_measurement`, passes the kit gate before it exists as a task. For capability code c in environment E:

- providers: the instruments whose `capability_codes` include c;
- `instrument_ids`: providers in E's kit; `can` is true when there is at least one;
- `missing_instrument_ids`: when `can` is false, the providers not in the kit; otherwise `[]`;
- `condition_ids` and `cliff_urls`: the ICL conditions the request names and their CliFF links;
- `text`: when `can`, ui-copy `kit_check_templates.can` with the provider names joined by ", "; otherwise `kit_check_templates.cannot` with their `needs_text` joined by " or ".

The result is the request's `kit_check` (KitCheck: `can`, `instrument_ids`, `missing_instrument_ids`, `condition_ids`, `cliff_urls`, `text`, `kit_map_version`, `environment_id`). When `can` is false the hub records the request in state `unavailable` with its `kit_check`, opens a CapabilityGap and answers `422 KIT_CANNOT_PERFORM` with the recorded request in `current_state`. The assistant's turn continues and lists the kit text in its `limitations` (section 17).

`GET /v1/capabilities` derives each Capability from the same document: `label`, `body_region`, `operator_requirement` and `expected_duration_seconds` from `capabilities[]`, `methods` from the providers' `method`, `instrument_ids` and `missing_instrument_ids` as in the gate, `applicable_environments` the environments whose kit holds a provider, and `status` `unavailable` with reason `not_in_kit` when no provider is aboard.

### Onboard plans

Each row's `plan_id` names its plan in onboard-plans.json. Plans, plan runs and the plan columns of the kit map are specified in section 22; supplies and stock in section 25.

## 22. Onboard plans

Every kit-map row and every responder flow has a plan the crew runs with what is aboard. A plan never has a step that waits for the ground: the log for the ground is its last stage and nothing waits for it. Plans are configuration, written at the event and unreviewed until a clinician checks them; the hub computes what each step needs and who can do it, and the model never writes a step.

### The document

`onboard-plans.json` ([schemas/config-onboard-plans.schema.json](api/schemas/config-onboard-plans.schema.json)) holds `version`, `example`, `notice`, `sources` (sources not yet in the package registry, each with its verbatim `quote` or a `note` on how it was read) and `plans[]`.

| Field of a plan | Meaning |
|---|---|
| `plan_id` | `plan_` followed by lowercase snake case |
| `title`, `mode` | The plan's name; `resolve`, `talk_through`, `stabilize` or `prepare_return` (ui-copy `plan_mode_words`) |
| `scope`, `written_for` | Exactly one of `condition_id` (a kit-map row) or `flow_id` (a responder flow). `written_for` is `condition` (written for this condition), `family` (a family plan, labeled so) or `flow` |
| `environments` | The environments the plan covers |
| `stages[]` | In the fixed order `check_another_way`, `do_now`, `watch`, `if_worse`, `medical_officer`, `return`, `ground_log`; each at most once; `ground_log` is required, last and `blocking: false` (EI4) |
| `source_ids`, `review_state`, `written_by`, `checked_by` | As for kit-map rows |

| Field of a step | Meaning |
|---|---|
| `step_id`, `text`, `text_first_time` | The step as the crew reads it, rendered verbatim, and the longer version for someone who never did it (`null` until written) |
| `who` | `any_crew`, `crew_medical_officer`, or `skill:<skill_id>` for a crewmate holding that skill (section 24) |
| `needs` | `instruments[]` (kit-map instrument IDs, all needed), `supplies[]` (`supply_id`, `per_use` or `null`, `basis`, `to_be_set_by`) and `skills[]` |
| `substitute_step_id`, `substitute_only` | The step that runs instead when this one is not aboard; a `substitute_only` step is shown only in place of the step that names it |
| `environments` | Only in these environments; `null`: every environment of the plan |
| `body_region`, `landmark_id` | The region the figure lights and the skills-catalog landmark (section 23) |
| `guided_check_skill_id` | The skill whose guided check this step is; only in `check_another_way`, and `who` is `skill:` of the same skill |
| `cannot_replace_text` | What the step cannot replace, shown beside it ("It cannot replace an ECG.") |
| `recheck` | `cadence` and `n`, or `interval_seconds`, with `basis`; or `to_be_set_by` with neither, and then the plan shows the bracketed words and no due time |
| `opens_help`, `opens_flow_id` | Only in `if_worse`: the step opens Someone needs help, on that flow when set |
| `medicine` | `{"named_as": "as in the onboard procedure"}` when the step involves a medicine. The object has no other property; the schema has no dose, amount, route or frequency anywhere, and the step text says "as in the onboard procedure" |
| `replaces_ground_task` | `{quote, source_id, page}`: what a NASA form assumed the ground would do, which this step does aboard |
| `source_ids`, `basis`, `review_state` | Citations; `sourced` or `team default` |

A `return` stage has `environments` (each must have a non-null `return_option` in environment-profiles.json, EI11), `criteria[]` (`text`, `source_ids`, `to_be_set_by`), `prepare[]` steps, `decided_by` (`commander`, `crew_medical_officer`) and `rtdc` (NASA's modeled chance from the form's Table 4, section 27). The `ground_log` stage has `blocking: false` and optional `items[]` (`text`, `source_ids`), extra lines for the catch-up, never a precondition.

A kit-map row names its plan in `plan_id` (section 21). A flow plan is found by its `scope.flow_id`. The example document ([api/examples/config/onboard-plans.example.json](api/examples/config/onboard-plans.example.json)) has ICL10's plan written for the condition, the `eye_injury` flow's plan, and plans for the other six example rows built only from their DEMO-CONDITIONS.md lines.

### What a step needs here

For environment E, the hub computes from the active release, E's kit and stock (section 25), the roster, overlays and SkillRecords (section 24), at the hub clock:

1. A step applies in E when the plan covers E and the step's `environments` is `null` or includes E. Steps that do not apply are left out.
2. Missing instruments: `needs.instruments` not in E's kit. Missing supplies: `needs.supplies` whose stock is not aboard (no stock row, or `quantity_now` 0), or, for a consumable with `per_use` and `quantity_now` set, `quantity_now` below `per_use`.
3. Holders of a skill: people with a SkillRecord for it whose freshness is `fresh` or `due`, other than the subject. Missing skills: the skills in `needs.skills`, and the skill in `who`, that have no holder.
4. `availability`: with a missing instrument or supply, `substituted` when the substitute step's own instruments and supplies are all here, otherwise `not_aboard`; else with a missing skill, `needs_skill_holder`; else `aboard`. `availability_text` is ui-copy `plan_lines` `not_aboard`, `substituted` (with `{step}` the substitute's text) or `needs_skill_holder` (with `{skill_short}`), and `null` when aboard. A step that is not aboard is never shown as doable (EI7).
5. `who_can`, in roster order, among people awake and not outside on a walk: for `any_crew`, everyone, the subject included (they can note their own symptoms); for `skill:<k>`, the holders of k other than the subject; for `crew_medical_officer`, the available officers by `medical_officer_rank` (`GET /v1/crew`), other than the subject. Each entry has a `reason_code` (`any_crew`, `skill_holder`, `medical_officer`). An empty list means nobody can do it now; the step still shows.
6. `supply_cover_after`: for each consumable the step uses, the cover in uses after one more run (section 25), `null` when the stock or `per_use` is not set.
7. `recheck.text`: ui-copy `plan_lines.recheck_pending` with `{to_be_set_by}` while the interval is to be set, otherwise the cadence words.

### PlanView

`GET /v1/conditions/{condition_id}/plan` (the row's plan in the active environment) and `GET /v1/plans/{plan_id}?environment_id=` (any plan, any environment: the planning view) return a PlanView ([schemas/plan-view.schema.json](api/schemas/plan-view.schema.json)). It is computed on read and never stored. A row without `plan_id` returns `CONTEXT_INAPPLICABLE`.

- `stages[]` in the fixed order, each with its step views; a stage with no applicable step is left out. The `return` stage appears only when the environment's `return_option` is not `null` and the stage lists the environment; it carries `criteria`, `criteria_text` (ui-copy `plan_lines.return_criteria_pending` while any criterion is to be set by flight rules), `prepare` step views, `decided_by`, `who_decides_text`, `trip_text` and `trip_source_ids` from the `return_option`, and `rtdc`. Where `return_option` is `null`, `no_return_text` is ui-copy `plan_lines.no_return` (EI11).
- The `ground_log` stage is last with `blocking` false, `row_text` (`plan_lines.ground_log_row`), its `items`, `link_text` and `leaves_at`. With the link `down` and `next_contact_at` set, `link_text` is `plan_lines.ground_log_down` with `{in_time}` (section 26) and `{origin_word}` (ui-copy `origin_words` of the link's origin) and `leaves_at` is `next_contact_at`; otherwise `link_text` is `plan_lines.ground_log_up` and `leaves_at` is `null`. These two fields are the only part of a PlanView that changes with the link (EI2): [plans/view-icl10-lunar.json](api/examples/plans/view-icl10-lunar.json) and [plans/view-icl10-lunar-link-up.json](api/examples/plans/view-icl10-lunar-link-up.json) differ only there.
- `plan_aboard` (EI3): true when an `any_crew` step that needs no skill, in `check_another_way`, `do_now` or `watch`, is aboard here or substituted by an `any_crew` step that is aboard, and the plan has a `watch` or `check_another_way` step here.
- `waits_for_medical_officer`: true when every runnable step outside `if_worse` needs the medical officer or a skill only a medical officer holds. The planning view reports changes to it.
- `tag_text` is `plan_lines.plan_tag` with `{plan_label}` the condition ID (or the flow ID in words) and the review state ("Onboard plan ICL10 · unreviewed draft"); `mode_text` from `plan_mode_words`; `notice` from the document; `release_id` and `plan_version` (the document's SHA-256).

The kit map rows carry `plan_id`, `plan_written_for` and `plan_aboard`, and KitMapView counts `plans_aboard`, `plans_written_for_condition`, `plans_from_family` and lists `plans_to_write` (section 21). The This kit line is `plan_lines.plans_aboard` ("{count} of {total} have a plan aboard"), with `{total}` the rows of the kit map.

### PlanRun

A PlanRun ([schemas/plan-run.schema.json](api/schemas/plan-run.schema.json)) is one plan being run for one person. `POST /v1/plan-runs` (`plan_id`, `subject_person_id`, exactly one of `episode_id` or `help_session_id`) opens it in the active environment, pins onboard-plans, skills-catalog, role-rules, kit-map and ui-copy (`config_pins`) for its life, copies the link state into `link_state_at_open` (audit only: no rule, step or state reads it) and returns at once. Every applicable step starts `open`.

`active → watching → closed`, with `escalated` while an escalation's help session is open.

| Action (`POST /v1/plan-runs/{id}/actions`, If-Match) | Allowed when | Effect |
|---|---|---|
| `step_done` (`step_id`, optional `observation_ids`, `guided_check_id`) | The step is `open` and its availability is `aboard` or `substituted` | The step becomes `done` with `by` and `at`. A consumable with `per_use` set is written as a SupplyUse in the same transaction (section 25); a use that would go below zero refuses the whole action with `SUPPLY_BELOW_ZERO`. A step that is not aboard or has no skill holder returns `STEP_NOT_AVAILABLE` with the step view in `current_state` |
| `not_available` (`step_id`, `reason`) | The step is `open` | A person found it cannot run here (for example the instrument is broken). The step becomes `not_available`; its substitute, if any, is offered |
| `use_substitute` (`step_id`) | The step has a `substitute_step_id` | The step becomes `substituted` with `substitute_step_id`; the substitute step stays `open` |
| `escalate` (`step_id`) | An `if_worse` step with `opens_help` | Opens Someone needs help for the subject with `entry.kind` `plan_step` and `ref_id` the run, straight on `opens_flow_id` when set, otherwise at the start node (section 16). Adds the session to `escalations`; the run is `escalated` until that session closes. Returns at once |
| `close` (`disposition`, `note`) | Not closed | `closed` with `close` {`disposition`: `resolved_on_board`, `handed_to_medical_officer`, `watch_ended`, `preparing_return`, `other`, `note`, `by`, `at`} |

- The run is `active` while a `check_another_way` or `do_now` step is `open`, and `watching` once none is: only watch steps, rechecks, `if_worse` and the medical officer remain. When an escalation's session closes the run returns to `active` or `watching` by the same rule.
- A step with `guided_check_skill_id` is marked `done` by its guided check reaching `checked` (section 23): the hub sets `guided_check_id` and the check's Observation in `observation_ids`.
- `rechecks[]`: one per `watch` step with a `recheck`. `due_at` is the last done time plus the interval when the interval is set; otherwise `null` and `due_text` is `plan_lines.recheck_pending` with who sets it.
- `step_done`, `not_available` and `use_substitute` commute like help ticks: a stale If-Match on them is merged and returns 200.
- `handoff_id` links the run's log for the ground (section 26). No state waits for it, its delivery, a receipt or a reply (EI5).
- Events: `plan_run.changed` (opened, watching, closed), `plan_run.step_done`, `plan_run.escalated`.
- Roles for a plan step: `GET /v1/plan-runs/{id}/roles` and `POST /v1/plan-runs/{id}/roles` (accept or reassign), with the rules of section 24.

### The assistant and plans

Five tools in tool-catalog.json serve plans: `get_plan` (read the PlanView), `choose_plan_step` (propose a step ID with the answer that led to it), `explain_step` (read a step's `text_first_time` and the passages its `source_ids` cite), `propose_guided_check` (propose a guided check for a `check_another_way` step; the role engine names the helper, never the model) and `get_crew_readiness` (who can do a step now, crew_medical_officer sessions only). The controller injects the active release and the roster. A `choose_plan_step` naming a step outside the active plan, or a step that is not aboard, is discarded with `MODEL_OUTPUT_REJECTED` and logged; the person always sees step text from the release, never model text (EI6). A proposal never marks a step; a person does.

## 23. Teach and check

A crewmate who never did a check is taught it step by step on the body figure, and the cameras check the result where they can. A guided check records what it is ("pulse felt by a crewmate"); it never confirms or excludes a condition, and it never closes a capability gap (EI8).

### The document

`skills-catalog.json` ([schemas/config-skills-catalog.schema.json](api/schemas/config-skills-catalog.schema.json)) holds `landmarks[]` (`landmark_id`, `body_region`, `label`, `offset` on the region's point or `null` until the design team sets it) and `skills[]`:

| Field | Meaning |
|---|---|
| `skill_id`, `name`, `short_name`, `who_may`, `card_title` | Words for cards, role rows and reasons; `card_title` takes `{helper_name}` and `{subject_name}` |
| `freshness` | `cadence`, `n`, `due_after_periods`, `stale_after_periods`, `basis`, `to_be_set_by` (section 24) |
| `teach_steps[]` | `step_no` from 1, `kind` (`instruction`, `count`, `question`), `actor` (`helper`, `subject`, `both`), exactly one of `text` or `from_flow_step` (a responder flow immediate step rendered instead, so the help flow and the teaching never drift apart), `text_first_time`, `landmark_id`, `body_region`, `environments`, `count_seconds` (count steps only), `choices[]` (question steps only), `source_ids` |
| `checks[]` | `check_id`, `kind` (`count_agreement`, `compression_rate`, `hand_position`), `experimental` (true until measured at the venue; the screen shows ui-copy `guided_check_words.experimental`), `reference` (`capability_code`, `what`), `target` (`min`, `max`, `unit`, `source_ids`), `tolerance` (`value`, `unit`, `basis`, `to_be_set_by`), `update_interval_seconds`, `phrases` (`ok`, `too_slow`, `too_fast`, `mismatch`, `cannot_see`, `no_reference`) |
| `what_it_shows` | `does`, `does_not`, `source_ids`: shown with the result |
| `drill_scenarios[]` | `scenario_id`, `title`, `practice_on` (`crewmate`, `manikin`), `needs_supplies` |

The example has the pulse check at the wrist (British Heart Foundation, SK01 and SK02; the 10 beats per minute tolerance is a labeled team default, to be set by clinical review) and CPR (teach steps rendered from the `not_breathing` flow; the 100 to 120 a minute band from RS01, RS02 and SK03).

### GuidedCheck

`POST /v1/guided-checks` (`skill_id`, `helper_person_id`, `subject_person_id`, optional `plan_run_id` or `drill_id`, `role_assignment_id`) starts a GuidedCheck ([schemas/guided-check.schema.json](api/schemas/guided-check.schema.json)) at teach step 1. The helper is never the subject (`ROLE_NOT_ELIGIBLE`). It works with the model stopped and the link down.

`teaching → counting → checked`, `counting → retry → counting`, and any state before `checked` `→ cancelled`.

| Action | Allowed when | Effect |
|---|---|---|
| `step_done` (`step_no`) | `teaching`, the step on screen | Adds to `steps_done`; the next step shows and lights its landmark |
| `start_count` | The step on screen has `kind` `count` | `counting`; `count_started_at` is the hub time and `count_ends_at` adds `count_seconds`. The hub asks the cameras for the check's reference capability over the same window |
| `POST .../beats` (`sequence`, `client_time`) | `counting`, hub time before `count_ends_at` | Appends the hub receive time to `beats`; a repeated `sequence` changes nothing. Sends `guided_check.beat` with the count and seconds left |
| `answer` (`answer`) | The question step | Records `helper_answer` (`regular`, `irregular`, `not_sure`) as an Answer about the subject (`answer_id`) |
| `finish` | Counting ended | Computes the result below |
| `retry` | `retry` | Clears the count and starts again from the count step |
| `cancel` | Before `checked` | `cancelled`; nothing is written |

The result of a `count_agreement` check:

1. `count_per_minute` = `beat_count` × 60 / `count_seconds`.
2. `camera_reference`: the camera Observation of the reference capability over the window, or `no_reading_reason` when the cameras gave none.
3. With no camera value, or with `tolerance.value` not set: `no_reference`, `result_text` the `no_reference` phrase with `{reason}`. Otherwise `difference` = `count_per_minute` minus the camera value; within `tolerance.value` (inclusive) it is `agree` and `result_text` the `ok` phrase with `{count}` and `{camera}`; outside it, `disagree` with the `too_slow` (lower) or `too_fast` (higher) phrase with `{difference}` as a positive number.
4. `agree` and `no_reference` write one Observation ([plans/observation-pulse-felt.json](api/examples/plans/observation-pulse-felt.json)): the reference capability's code, `value` `count_per_minute`, `acquisition_method` `crewmate_palpation`, `operator_id` the helper, `body_region` the landmark's region, `context` with `guided_check_id`, `count_seconds` and `camera_agreement`. The check becomes `checked` and, in a plan run, marks its step done. `disagree` writes no value and the check becomes `retry` (EI8).
5. The Observation is what it is: a pulse felt by a crewmate. It says nothing about the rhythm, it never closes a CapabilityGap, and the kit gate and the body figure's dashed ring ignore it. Sends `guided_check.checked`.

In the live demo the beats and the camera reading are the presenter's and go to the demo-operator record; the persona stays Sim.

### CPR coaching

While a help session is at a flow whose role rules give the lead helper a skill with a `compression_rate` check, and during a drill of that skill, the camera adapter sends at most one CoachingSample ([schemas/coaching-sample.schema.json](api/schemas/coaching-sample.schema.json)) per `update_interval_seconds`: `rate_per_min` when its quality gate passes, otherwise `rate_per_min` `null` with `reason` `cannot_see_hands` or `quality_gate_failed`, never a guessed number. `in_band` and `direction` compare the rate with the check's `target`; `text` is the matching phrase with `{rate}`. Help session samples are appended to the session (`GET /v1/help-sessions/{id}/coaching`) and sent as `coaching.sample`; drill samples are shown live, have `persisted` false and are dropped when the drill ends (EI10). Every sample is labeled Experimental until the venue check.

## 24. Who helps: roles, skills and drills

### The document

`role-rules.json` ([schemas/config-role-rules.schema.json](api/schemas/config-role-rules.schema.json)) holds `roles[]` (`role_id`: `lead_helper`, `second_helper`, `kit_fetcher`, `medical_officer`, `communicator`; `label`; `on_site`), `eligibility[]` (`never_subject`, `not_on_eva_for_on_site` and `one_role_each` exclude; `awake_first` prefers), `medical_officer_cascade[]` (first officer, second officer, then the freshest holder of the needed skill), `flow_roles[]` (per flow: `role_id`, `skill_id`, `task_text`, `required`), `default_roles[]`, `plan_step_role` and `reason_phrases` (one template per reason code).

Officer ranks are on the roster: `CrewMember.medical_officer_rank` is 1 for the crew medical officer and 2 for the second (`GET /v1/crew`). The fixture: Ilya Brandt rank 1, Sofia Reyes rank 2, Mara Okafor and Tomas Lind crew.

### The role engine

When a help session reaches a flow, the engine proposes RoleAssignments ([schemas/role-assignment.schema.json](api/schemas/role-assignment.schema.json)) for the flow's `flow_roles` (or `default_roles`). Eligible for a role: on the roster, not the subject, not in an active `eva` overlay when the role is `on_site`, and without another role while someone else is free; people awake come before people asleep. In this order:

1. `medical_officer`, by the cascade: rank 1 when eligible (`first_officer`); else rank 2 (`second_officer`); else the eligible holder of the flow's needed skill with the latest `last_practiced_at` (`freshest_skill`). Each officer passed over adds a `context_reasons` entry: `officer_is_subject`, `officer_on_eva` (with `{eva_word}` and the overlay's planned end as `{time}`) or `officer_asleep`.
2. The required roles in flow order. A role with a `skill_id` goes to the eligible holder with the latest practice (`freshest_skill`, "CPR practiced day 12"); a role without one to the first eligible person in roster order (`free_to_help`).
3. The optional roles, while anyone is left. A role nobody can fill is listed in `unfilled`.

For a plan step whose `who` is `skill:<k>`, the engine proposes `plan_step_role` for the freshest eligible holder of k, with `context_reasons` naming each medical officer who is unavailable ([roles/plan-helper-tomas.json](api/examples/roles/plan-helper-tomas.json)).

- `reason_text` is the reason phrase rendered with `{officer_name}`, `{eva_word}`, `{time}`, `{skill_short}`, `{day_label}` (the inline day label of the last practice) and `{person_name}`. Every assignment states its reason (EI9).
- `accept` sets `accepted`. `reassign` (`person_id`) changes the assignment in place: the new person, `state` `changed`, `reason_code` `changed_by_helper`, `changed_by` and `changed_at`. Any helper may reassign any role to any eligible person; an ineligible one returns `ROLE_NOT_ELIGIBLE`.
- The engine proposes again when an overlay starts or ends, a person joins or leaves, or the subject changes. A new proposal sets `replaced_by_id` on the one it replaces. It never moves a role a helper changed.
- Roles are shown below the steps, never before them, and the steps never wait for roles. `GET /v1/help-sessions/{id}/roles` returns a HelpRolesView; the crew broadcast carries the roles (`CrewBroadcast.roles`). Events: `role.proposed`, `role.changed`, `help.broadcast_changed`.

The fixture: in act 6 Ilya Brandt's walk ended at 11:40, so he is the medical officer and Mara Okafor helps ([roles/help-roles-eye.json](api/examples/roles/help-roles-eye.json)). When Ilya is the patient (the station CPR preset), Sofia Reyes is the medical officer, Tomas Lind does compressions and Mara Okafor brings the AED and the restraint board ([roles/help-roles-cpr.json](api/examples/roles/help-roles-cpr.json)).

### SkillRecords and freshness

A SkillRecord ([schemas/skill-record.schema.json](api/schemas/skill-record.schema.json)) is computed from `skill.practiced` events: `source` `preflight` (training before launch, Sim in the demo), `drill` (a completed drill) or `event` (the person did the skill in a help session or a checked guided check). Freshness at the hub clock, from the skill's `freshness`: the period is the cadence (`monthly` is routine-rules `calendar.month_days` days, `weekly` 7, `daily` 1, `every_n_days` n); `due` from `due_after_periods` periods after the last practice, `stale` from `stale_after_periods`, `fresh` before, `never` without a record. The cadence is a team default, to be set by the flight surgeon. `freshness_text` is ui-copy `freshness_words`.

`GET /v1/me/skills` returns the session person's own records, one per skill. `GET /v1/crew/readiness` (crew_medical_officer only) returns, per skill, the holders in roster order with freshness, rank and availability now, and `text` from ui-copy `readiness_words`. Skill data is operational: no health data, no ranking, never in participation views.

### Drills

`POST /v1/drills` starts a Drill ([schemas/drill.schema.json](api/schemas/drill.schema.json)) for a scenario of `skills-catalog.json`, its skills and participants, optionally linked to the routine occurrence of the drill task (routine-schedule items may carry `skill_ids`). A scenario whose `needs_supplies` are not aboard returns `CONTEXT_INAPPLICABLE`.

`running → completed`, or `running → abandoned`.

`POST /v1/drills/{id}/complete` writes a `skill.practiced` event for each participant (`source` `drill`, `last_practiced_at` the completion) and counts toward the practice patches of participation-rules `practice` (`first_drill`, `drill_count`, `skill_fresh`). `stores_measurements` is always false: a drill's camera numbers are shown live and never stored, ranked or rewarded, and drills never touch health records, the streak or the crew goal (EI10). The fixture: on day 12 on the station Tomas Lind practiced the pulse check, and Mara Okafor and Tomas Lind practiced CPR on the practice manikin.

## 25. Supplies and cover

### The document

kit-map.json `supplies[]` (`supply_id`, `name`, `unit`, `consumable`) and per environment `stock[]` (`supply_id`, `quantity` or `null`, `basis`, `to_be_set_by`) beside `kits`. A supply with no stock row in an environment is not aboard there. `candidate_supplies` and `candidate_skills` are what the planning view offers. Quantities in the example are team defaults; per-use amounts live on plan steps (section 22).

### Stock, uses and cover

- SupplyStock ([schemas/supply-stock.schema.json](api/schemas/supply-stock.schema.json)): `quantity_packed` from the stock row, `used` the sum of SupplyUses in that environment, `quantity_now` = packed minus used (`null` when packed is `null`), `aboard` when a stock row exists and `quantity_now` is `null` or above 0. Never typed (EI12).
- SupplyUse ([schemas/supply-use.schema.json](api/schemas/supply-use.schema.json)): append-only, in the active environment. `POST /v1/supplies/{supply_id}/uses` records one with the help session or plan run and step it served; a plan run's `step_done` writes one per consumable with `per_use` set. A use that would take `quantity_now` below zero is refused with `SUPPLY_BELOW_ZERO` and nothing is written; the step then shows Not aboard with its substitute. Sends `supply.used`.
- Cover: `cover_uses` = floor(`quantity_now` / the largest `per_use` among the applicable plan steps that use the supply), `null` when the supply is not consumable, not aboard, its quantity or every `per_use` is not set. `cover_text` is ui-copy `supply_words` `cover`, `cover_unknown`, `not_used`, `not_aboard` or `not_consumable`.
- `GET /v1/supplies` returns a SupplyCoverView ([schemas/supply-cover-view.schema.json](api/schemas/supply-cover-view.schema.json)) of every supply in the environment, with `used_by` (plan, step, `per_use`) and `low`.
- `supply_cover_low` (warning-rules, `watch`, raised by `rule`, crew scope, one per supply): raised when `below_uses` is set and `cover_uses` is below it; cleared when it no longer is. Its content takes `{supply_name}`, `{uses}` (`cover_uses`), `{quantity}` (`quantity_now` with the supply's unit) and `{per_use}` (the largest per-use amount). `below_uses` is to be set by the flight surgeon; while it is `null` the rule raises nothing.

The fixture: in act 6 Mara Okafor used one eye rinse for Tomas Lind; on the Moon base 6 were packed, so 5 are left and the cover is 5 uses ([supplies/cover-lunar.json](api/examples/supplies/cover-lunar.json)).

## 26. The ground as a reader

The ground reads what happened when the link allows. It is never a step: nothing on board reads the link except the four places below (EI1), and every plan, flow, warning list and routine list is the same with the link up, delayed or down except those places (EI2).

| Reader | What it does with the link |
|---|---|
| The ground link line (every screen, Habitat) | Shows `status`, the delay and `next_contact_at` with its origin (section 12) |
| The `ground_log` stage of a PlanView | `link_text` and `leaves_at` (section 22) |
| Handoff and GroundMessage | `leaves_at` and `status_text` |
| The `ground_silence` warning rule | Below |

### GroundLink gaps

GroundLink (section 12) carries `gap_kind` (`planned_line_of_sight`, `relay_outage`, `conjunction`, `equipment`, `unknown`; ui-copy `gap_kind_words`) and `gap_planned`. With `status` `down`, `gap_kind` is set; with `up` or `delayed`, `gap_kind` is `null` and `gap_planned` false. A planned gap raises nothing.

`ground_silence` (warning-rules, `watch`, raised by `rule`, crew scope) is the only rule that reads the link: raised when `status` is `down`, `next_contact_at` is set and the hub clock passes `next_contact_at` plus `after_next_contact_seconds`; cleared when the link comes back or `next_contact_at` moves later. It shows with the watch level words ("Keep an eye on it") and its definition's title ("Ground out of contact longer than planned"); `{silence_text}` is the time past `next_contact_at` in the `{in_time}` rounding and `{next_contact_time}` its mission time HH:MM. `after_next_contact_seconds` is to be set by clinical review; while it is `null` the rule raises nothing.

`{in_time}` is the time from the hub clock to `next_contact_at`, in whole hours rounded down when it is at least one hour, otherwise in whole minutes rounded down, rendered with ui-copy `duration_words`. With the demo link down at 09:20:40 and the next contact at 11:20:40 the next day: "25 hours" at 09:36 and "23 hours" at 12:05.

### The log for the ground

A handoff (from an episode or a plan run, section 4, Sharing) and a ground message (from a help session, section 16) are the log for the ground. Both carry:

- `leaves_at`: while queued with the link `down`, the link's `next_contact_at`; `null` while the link is `up` or `delayed` (it leaves now) and once it left.
- `catch_up` (common `catch_up`), composed by the hub with ui-copy `catch_up_words`:
  - `what_happened`: for a ground message, its header and answers lines; for a handoff, the concern and the observations it carries;
  - `what_was_done`: `step_done_line` (`{time}` HH:MM, `{step}` the step text) for each ticked help step, or each done step of the plan run's `check_another_way` and `do_now`, then the crew call line when the crew was called;
  - `being_watched`: `watch_line` (`{step}`, `{recheck}`) for each watch step of the plan run or of the flow's plan that is not `substitute_only`; when there is none, `nothing_watched`;
  - `advice_welcome`: `advice_line` (`{topic}`) for each open CapabilityGap consequence of the episode, then each plan step with `replaces_ground_task` (the steps NASA's forms expected the ground to do), then the `ground_log` stage's `items`.
- Ground message `status_text`: help-presentation `ground_message` `queued_template` with the link up or delayed, `waiting_template` ("Waiting for the next contact in {in_time}. Nothing here waits for it.") with it down, then `sent_template` and `acknowledged_template`.

### Preview, then Tell the ground

The For the ground card shows the drafted text before the press. `GET /v1/help-sessions/{id}/ground-message-preview` returns a GroundMessagePreview ([schemas/ground-message-preview.schema.json](api/schemas/ground-message-preview.schema.json)): `lines`, `text`, `text_sha256`, `catch_up`, the link, `leaves_at`, `status_text` and the `session_revision` it was drafted from. It is computed on read and never stored (`persisted` false).

Pressing Tell the ground (`POST /v1/help-sessions/{id}/ground-messages`) approves the draft and queues it in one step: the GroundMessage records `session_revision` and `text_sha256`, which equal the preview at that revision ([help/ground-message-preview.json](api/examples/help/ground-message-preview.json) and [help/ground-message-queued.json](api/examples/help/ground-message-queued.json)). The optional `previewed_sha256` in the request is recorded only: the hub queues the draft as it stands and never refuses the press. There is no separate approve call for a ground message. A handoff keeps its preview (`POST /v1/handoffs`, `previewed`) and its approve call, which is the same Tell the ground press on the act 5 card. Requirement R61's "previewed and approved" means exactly this.

The simulated comms queue sends queued items in order at the next contact. A receipt, a reply or a review from the ground is a new record that changes no step, plan run, episode or help session (EI5). `GET /v1/ground/log?since=` (ground role) returns a GroundLogView: the gap and every handoff and ground message with its catch-up, oldest first ([ground/log-catch-up.json](api/examples/ground/log-catch-up.json)). Reading it changes nothing aboard.

## 27. Stay or return

A plan may have a `return` stage only for environments whose `return_option` (environment-profiles.json) is not `null`: the Moon base ("A three-day lunar trip", EV25), and the station per flight rules. Mars and transit plans have none and say so with `plan_lines.no_return` (EI11).

`GET /v1/return-readiness?subject=` returns a ReturnReadiness ([schemas/return-readiness.schema.json](api/schemas/return-readiness.schema.json)) for the subject's plan run whose plan has a return stage in the active environment; elsewhere it answers `CONTEXT_INAPPLICABLE`. It is computed on read:

- `state`: `closed` (no such run is active), `open` (one is: its criteria are listed), `preparing` (a `prepare` step is done), `stood_down` (the run closed with a disposition other than `preparing_return` after preparing started).
- `criteria[]`: the stage's criteria with `status` `met`, `not_met` or `unknown`, `evidence_ids`, `source_ids` and `to_be_set_by`. No rule evaluates a criterion in this release: every criterion is to be set by flight rules, so its status is `unknown` and `criteria_text` says so.
- `prepare[]`: the stage's prepare steps with their state in the run (marked with the plan run's `step_done`).
- `decided_by`, `who_decides_text` (ui-copy `plan_lines.who_decides`), `trip_text` and `trip_source_ids`, and `rtdc`: NASA's modeled chance of removal to definitive care from the form's Table 4 (treated and untreated, best and worst case), copied from the saved form with its page, never typed from anywhere else.

There is no decision field and no decide action: the app lists the criteria, the trip and who decides; the commander and the medical officer decide.

## 28. Earth-independence invariants

Every implementation keeps these. The package validator checks the configuration and examples (api/README.md); the hub's tests check the running system.

| ID | Invariant | How it is checked |
|---|---|---|
| EI1 | No plan step, flow step, warning rule, routine rule or assistant action has a precondition on the ground link or a ground reply | Validator `rule.ground`: no warning condition kind other than `ground_silence` reads the link, and no schema state waits on the ground. Hub: a scan of the code for readers of GroundLink outside the four readers of section 26 |
| EI2 | With the link up, delayed and down, every PlanView, help flow, warning list and routine list is identical except the ground stage text and the ground line | Validator `ei.link_diff`: the two ICL10 PlanView examples differ only in the `ground_log` stage's `link_text` and `leaves_at`. Hub: byte comparison of those responses in the three states with the same clock |
| EI3 | Every kit-map row has, in every environment, a plan with an `any_crew` step that can run with that environment's kit and stock, and a `watch` or `check_another_way` step | Loader check 15 and validator `plans.aboard`, naming each failing row and environment; in LIVE a failing release is rejected, in SIM the This kit count shows the gap |
| EI4 | The ground stage is last in every plan and `blocking: false` | Schema (`blocking` is the constant false) and validator `plans.order` |
| EI5 | No PlanRun, HelpSession, Handoff or GuidedCheck has a state that waits for the ground | Validator `rule.ground` (no state named after waiting for the ground in any schema) and `ei.states` (the four state sets are exactly those of sections 4, 16, 22 and 23) |
| EI6 | Step text, medicine mentions and thresholds come from the active release; medicine steps have no dose field; the assistant can only name step IDs of the active plan | Validator `rule.no_dose` over every schema, document and plan step; controller validation of `choose_plan_step` (`MODEL_OUTPUT_REJECTED`) |
| EI7 | A step whose instrument, supply or skill is missing is never shown as doable; it shows Not aboard and its substitute when one exists | Validator `ei.availability`: every step view whose `missing` lists something has availability `not_aboard`, `substituted` or `needs_skill_holder` and its words. Hub: PlanView tests per environment |
| EI8 | A guided check never confirms or excludes a condition; disagreement writes no value; a dashed ring is never closed by a substitute check | Schema (`disagree` forces `observation_id` null) and validator `ei.guided` over the examples; hub: Assessment and CapabilityGap validation ignore `crewmate_palpation` |
| EI9 | Role assignment never assigns the subject; never assigns someone in an `eva` overlay to an on-site role; always states a reason; the helper can change it | Validator `roles.cross` (the exclusions exist) and `ei.roles` over the role examples; hub: role engine tests |
| EI10 | Drill measurements are never stored as scores, ranked or rewarded; drills never touch health records, the streak or the crew goal | Schema (`stores_measurements` false; drill CoachingSamples `persisted` false; participation `practice.drill_measurements_count` false); hub: participation rebuild and a storage scan |
| EI11 | A return stage exists only where `return_option` is not null; Mars and transit plans have none and say so | Validator `plans.return` and `ei.return` over the PlanView examples |
| EI12 | Supply cover is computed, never typed; a use that would go below zero is refused and the step shows Not available with its substitute | No request body carries `quantity_now` or `cover_uses`; validator `ei.cover` recomputes the cover examples; hub: ledger tests |

## 29. Requirements R47 to R62

| ID | Requirement | Contract | Example or check |
|---|---|---|---|
| R47 | No dead ends: a plan aboard for every kit-map row in every environment | Sections 21 and 22: `plan_id`, `plan_aboard`, `plans_aboard`, `plans_to_write`; loader check 15 | `plans.aboard`; [kit-map/view-station.json](api/examples/kit-map/view-station.json) |
| R48 | The ground is never a step | Sections 12, 22 and 26; EI1, EI2, EI5 | `rule.ground`, `ei.link_diff` |
| R49 | Show the link and the next contact; silence past plan is a watch warning | GroundLink `gap_kind`, `gap_planned`, `next_contact_at`; `ground_silence` (section 26) | [environment/ground-link-lunar.json](api/examples/environment/ground-link-lunar.json) |
| R50 | Check it another way | `check_another_way` steps, the gap card's `plan_id` (CapabilityGap), GuidedCheck and the `crewmate_palpation` Observation (sections 22 and 23) | [plans/guided-check-agree.json](api/examples/plans/guided-check-agree.json) |
| R51 | Teach a manual check or step on the body | skills-catalog `teach_steps` with landmarks (section 23) | [plans/guided-check-teaching.json](api/examples/plans/guided-check-teaching.json) |
| R52 | The cameras check the helper | The result rule and CPR coaching (section 23); `experimental` | [plans/guided-check-disagree.json](api/examples/plans/guided-check-disagree.json), [help/coaching-cpr.json](api/examples/help/coaching-cpr.json) |
| R53 | Know who can help | SkillRecord, `GET /v1/me/skills`, `GET /v1/crew/readiness` (section 24) | [crew/readiness-lunar.json](api/examples/crew/readiness-lunar.json) |
| R54 | Assign emergency roles, including when the medical officer is the patient or away | The role engine (section 24); EI9 | [roles/help-roles-cpr.json](api/examples/roles/help-roles-cpr.json) |
| R55 | Drills keep skills fresh and earn practice patches, never scores | Drill, `skill.practiced`, participation-rules `practice` (section 24); EI10 | [drills/drill-pulse-completed.json](api/examples/drills/drill-pulse-completed.json) |
| R56 | Decisions packed before launch | Plans, skills and roles are configuration with `review_state` and a notice; EI6 | `rule.no_dose`, `plans.cross` |
| R57 | Treat here: each step declares what it needs | Step `needs`, availability and substitutes (section 22); EI7 | `ei.availability` |
| R58 | Supplies and cover | Section 25; `supply_cover_low`; EI12 | [supplies/cover-lunar.json](api/examples/supplies/cover-lunar.json), `ei.cover` |
| R59 | The planning view covers instruments, supplies and training | KitMapWhatIf `add_supplies`, `add_skills`, `plans_changed`, `cover_changed` (section 21) | [kit-map/what-if-supplies-skills.json](api/examples/kit-map/what-if-supplies-skills.json) |
| R60 | Stay or return, Moon base only | Return stage and ReturnReadiness (section 27); EI11 | [plans/return-readiness-tomas.json](api/examples/plans/return-readiness-tomas.json) |
| R61 | A log for a late reader | `leaves_at`, `catch_up`, the preview and the press, `GET /v1/ground/log` (section 26) | [help/ground-message-preview.json](api/examples/help/ground-message-preview.json), [ground/log-catch-up.json](api/examples/ground/log-catch-up.json) |
| R62 | Plans survive the laptop | Plans, skills and roles are configuration documents, cached by version on every crew device like responder-flows.json (section 11); a client renders the first help screen and the plan steps from cache with their release version when the hub is stopped | `GET /v1/config/{name}` with `If-None-Match` |
