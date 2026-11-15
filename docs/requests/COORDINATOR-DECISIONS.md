# Coordinator decisions on the open requests

Binding for every builder, designer and reviewer. Where a decision below names a value, field or rule, use it exactly;
where it leaves a choice to the implementer, the implementer documents the choice in the spec and in the report. A
decided point is not raised again in a design or a review. Part A applies to every epic; Part B is the medicine potency
engine. The coordinator is Joaquin Castellano: every decision here is Joaquin Castellano's, and the agents that draft
and land them are tools.

# Part A. System and product decisions

## A1. Package conflicts, settled

| ID | Conflict | Decision |
|---|---|---|
| D1 | CONTRACTS.md section 11 lets a warning whose definition disappears clear with `config_changed`. | Unsafe; overridden. An active warning keeps the definition version it was raised under. A release that edits or removes the definition never clears or lowers it: it clears only by its pinned clearing rule or by a medical officer's resolution with a reason, and it shows "definition withdrawn" when its definition is gone. New evaluations use the new release. |
| D2 | Radiation storm shelter: USE-CASES.md says Emergency, the example warning rule sets `act`, and V74 holds everything below Emergency during sleep. | Emergency. A storm shelter call wakes everyone. The example rule is corrected to `emergency`. |
| D3 | Freshness: CONTRACTS.md computes it from `last_received_at`, yet says a late arrival is never fresh. | Freshness is computed from the time the value was observed (`observed_at` on the hub's corrected clock), never from when it arrived. A late reading is shown with its observation time and is fresh only if that time is inside the window. Only readings observed after a warning was raised, and fresh by that rule, can count toward clearing it. |
| D4 | One command is one transaction, yet the journal lives in `private.sqlite`. | Two-store protocol: the sealed entry is written to `private.sqlite` with its idempotency key and a keyed fingerprint, then the hub record that references it commits in one hub transaction. The command is acknowledged only after both. Startup recovery completes or discards an orphan private entry by its idempotency key. |
| D5 | Error format: the charter asks for RFC 9457, the package defines a closed ErrorEnvelope. | As SYSTEM.md 5.5 and R-SYS-1: the ErrorEnvelope on the wire, an RFC 9457 view in logs. |
| D6 | Idempotency keys kept 24 hours versus long ground outages. | As SYSTEM.md 5.8 and 8.6: the durable command ledger. |
| D7 | "Every number in configuration" versus ordinary operational constants. | Domain content is configuration; queue sizes, timeouts, batch sizes and retries are hub settings (SYSTEM.md 5.11). |

| D8 | Config activation requires an object-revision If-Match but ConfigRelease has no revision field or response ETag. | Keep the closed ConfigRelease JSON. Expose a strong ETag header on create, detail read and activate responses: quoted `<release_id>:<object_revision>:<activation_generation>`, with positive monotonic object revision and hub-wide activation generation stored by E01. Detail reads provide the token; collection readers fetch the detail before activating. If-Match compares both revisions in the activation transaction; missing is VERSION_REQUIRED and mismatch is STALE_VERSION. Increment the activation generation on every activation/rollback and object revisions on every release-state change. Editor and operator remain the only activation roles; open work keeps its pins. Apply the header contract through the decision overlay, then propagate it centrally to the package. |
| D9 | listPlanRuns grants station and medical officers every clinical run, contrary to PRD object-sharing rules. | Station has no clinical plan-run list or detail access: return UNAUTHORIZED_SCOPE for those operations. Serve public plan content and broadcasts through existing nonclinical views. A person sees their own runs or an explicit object/field grant; crew and acting medical officer status alone grants no chart. Task helpers see only their accepted task projection. Apply the same rule to events, caches, exports, sync and ground snapshots. No new general break-glass access is authorized. |
| D10 | Crew-goal derivation includes every roster member without Q15 consent state or an opt-in operation. | E22 owns durable per-person, mission and origin-mode goal consent, default off. Add GET and PUT /v1/me/crew-goal-consent (crew only); the PUT body is {enabled: boolean}, uses Idempotency-Key and If-Match, and returns {mission_id, origin_mode, enabled, effective_at, revision}. A missing row is revision 0 and disabled. Only the authenticated owner changes it. Enabling includes contributions prospectively; revocation immediately excludes that member from the rolling aggregate and invalidates caches. Both completed and due totals use consent. Personal completion records never become public. Zero opted-in due contributions is no goal activity and reached false, never fabricated success. No ground role is needed. |
| D11 | BPv7 lifetime is absent from the closed environment-profile comms_queue schema. | Add optional comms_queue.bundle_lifetime with {value: positive integer, unit: "s", basis, source_ids, to_be_set_by}; maximum 315360000 seconds is a serialization/operational ceiling, not a mission default. Mission authors choose a sourced value; no implicit value. Missing field leaves BPv7 transmission unavailable while local approved queues and the existing simulated contact queue work. Expired envelopes are re-created at a later contact with the same approved snapshot ID/hash and priority, new transport identity and current configured lifetime. Recheck consent, bound per-contact retries and never delete unacknowledged approved content on expiry. |
| D12 | The package is frozen during architecture revision, but accepted Part A changes and UCUM wire mappings need executable contract support. | Keep package and verbatim base copies intact. E01 generates an effective contract from the base plus a separately versioned, signed decision overlay; every overlay change traces to an A3 line and accepted Part A decision. Pending requests cannot enter it. Preserve base and effective hashes and test both, labeling superseded assertions explicitly. Add unit-mapping.json to the effective release: versioned mappings by indicator and wire unit to UCUM, conversion ID, decimal rounding and provenance. Seed bpm to /min, mmHg to mm[Hg], ppm to [ppm] from CONTRACTS 86 and 659; contextual ppm_to_mmhg requires explicit sourced pressure. Sensor rounding is four decimal places, decimal half even. Every document loads atomically and validates before activation. This implements existing decisions without weakening any unrelated schema or source, privacy, unit, safety or acceptance check. |

| D13 | CONTRACTS 748 requires rule evaluation in the input transaction but an evaluator failure can prevent durable capture of a valid reading. | Run evaluation in a savepoint inside the producer transaction. Only typed computation failures may roll back evaluation while the validated input, evaluation_pending marker, durable retry and reference-only fault audit commit together. Existing warning/urgency state is never cleared or lowered; show degraded evaluation. Storage, I/O, integrity, authorization, configuration-validation and input-validation errors abort the whole command and are never swallowed. Recovery re-evaluates pending inputs under their original observation time and release without overwriting newer state. |
| D14 | Freshness of REPLAY channels: decision D3 uses the observation time, but a replayed reading's `observed_at` is another mission's date (R-E23-1). | A REPLAY channel's freshness reference is the hub receive time of the latest accepted reading that advanced the channel's window end E. A reading that does not advance E (a replay restarted from the top, a late or repeated row) never refreshes it, so a stopped or looping replay goes stale. Windows and evaluation still follow `observed_at` (CONTRACTS 13). LIVE and SIM keep D3 unchanged. |
| D15 | AGENTS.md rule 1 says no configuration edit test edits a safety mapping (E23 open question 2). | A test that changes a sourced threshold value in a scratch copy of the configuration, consistently in `environment-hazards.json` and `sensor-limits.json` so loader check 4 passes, is not a safety mapping edit. A safety mapping is which level a limit or rule raises and who may raise or lower a level; tests never edit those. The shipped documents are never edited by a test. |
| D16 | Owner of `getContext` (Q-E02-13). | E23 owns `getContext`, built in the `mission` module, which alone may read the ground link for views (SYSTEM.md `ground-link-readers`). `person` and `roles` come from E02's `SessionContext`, `config_release_id` and `config_versions` from E01's `ConfigReleaseReader`, `assistant_state` from E13's `AssistantStatusPort` when installed (`unavailable` when absent), `model_release_id` from the same port once request R-E23-17 adds it. E02 does not build it. |

| D17 | CONTRACTS calls model turns Jobs, but the closed public Job schema has no model-turn kind. | E01 owns one durable internal worker-task mechanism. Only assessment, report_extraction and participation_rebuild tasks expose public Job resources and job.progress events. Assistant turns use their existing turn_id, conversation stream and cancellation operation; forecasts use internal tasks and existing forecast records. Refresh never starts duplicate work. All kinds use bounded execution, pinned inputs, durable identity, cancellation generations and current object/field authorization. No onboard capability depends on a ground role. |
| D18 | Private reports and extraction versions live outside hub.sqlite, while CONTRACTS 1108 requires one transaction for commands and D4 covers only journals. | Extend D4 through E01's PrivateRecordCoordinator to report uploads and immutable extraction versions. Seal private content first, atomically commit hub references and command/task identity with audit, then acknowledge only after the paired-store barrier. E09 confirms only validated source spans and values into clinical observations in a hub transaction. All private writers and deletions join the paired-backup barrier. Deletion revokes source/draft version keys and prevents late publication; confirmed clinical records remain append only with deleted-source provenance. Preserve current owner/grant checks and every report identity, unit, upload and confirmation validation. No ground role is required. |
| D19 | E01's foundation task waited on the D12 overlay, signatures (R-E01-20, R-E01-28), CLI registration (R-E20-3) and lockfile allocation, so no code could start. | 1. The foundation generates contract types from the verbatim base copies verified against SHA256SUMS; until the D12 overlay exists the effective contract equals the base and the generation manifest records no overlay. E01 T3 builds the D12 overlay (`contracts/decision-overlay/`) and `config/unit-mapping.json`, and generation reruns when they land. 2. Overlay, build catalog and configuration bundles are checked by SHA-256 inventories. Signatures and trust keys (R-E01-20) are E20 release packaging and gate no code task; intake of external configuration bundles stays disabled until that verifier exists, so no unverified external input is accepted. 3. hub/pyproject.toml declares no console scripts at first; E20 adds the `crew-health` entry when it builds the shell, and the R-E20-3 interim holds until then. 4. E01 T1 creates hub/uv.lock and the root pnpm-lock.yaml; afterwards any coder may add a dependency its design names to hub/pyproject.toml or ui/crew/package.json and update the lockfiles, and change nothing else in those files. 5. A pending request never blocks a code task: the coder builds everything that does not need it, keeps the request's interim and lists the gap in its report. |
| D20 | Q1 ships content labeled "not clinically reviewed" while AGENTS.md keeps unreviewed content from the crew (R-E24-23 b). | AGENTS.md's ban covers draft content: content not yet sourced, validated and released. Released, sourced content awaiting clinician review is not draft. An emergency warning's listed steps (the storm shelter steps) are served in LIVE as its fixed first screen, labeled "not clinically reviewed" under Q1. Draft content stays off. |
| D21 | Who resolves a warning whose definition was edited or withdrawn (D1) when no crew medical officer is present. | A crew medical officer or the acting medical officer resolves it, with a reason; the record names the role (`acting_medical_officer`, as SYSTEM.md 8 records urgency dispositions). A warning never waits for an officer who is not aboard. |
| D22 | `docs/product/condition-assessments-119.json` has ICL36A and ICL36B, which the condition ID pattern `^ICL[1-9][0-9]{0,2}$` of the kit map, onboard plan and related schemas rejects. | They are valid IDs: two variants of row 36. The pattern becomes `^ICL[1-9][0-9]{0,2}[A-Z]?$` in every package schema that has it; the coordinator lands that contract change once E01 is built. Until then, code accepts the suffix and lists the contract change as a request. |
| D23 | `docs/product/NASA-SOURCES.md` row N02 links two documents (the OSDR Environmental Data Application guide and the RR-1 dashboard). | A source row may list several documents. All of them belong to the one source ID; that is not a conflict, and none is dropped. |
| D24 | tblite (GFN2-xTB for the potency engine's BDE layer) publishes no Windows build, so neither the engine nor the hub, which depends on it, installed on Windows; the engine also put the installed tblite version into every xtb cache key and run pin. | tblite is a dependency except on Windows. Where it is not installed, cache keys and `Pins.tblite_version` use `xtb_cache_tblite_version` of `config/pharmacy/engine-settings.json`, the version that computed the committed cache: committed entries are read and a miss raises `XtbError`. New entries are computed on Linux or macOS. |
| D25 | SYSTEM 5.12 declares `Resolver.require[P](port: type[P])` and the ports are Protocols; mypy's `type-abstract` check rejects a Protocol class for `type[P]`, so E01 T1 silenced it with 31 `# type: ignore[type-abstract]`. | `hub/pyproject.toml` disables mypy's `type-abstract` error code for the hub and the 31 ignores are removed. The SYSTEM 5.12 signature stays, and every other mypy check stays strict. |
| D26 | The generation manifest `hub/src/crew_health/contracts_gen/manifest.json` pins the hash of every installed `module.py`, so the import-contract gate fails whenever an epic adds or edits one. | The epic that adds or edits a `module.py` reruns `uv run --project hub python -m crew_health.contracts_gen.build` and keeps the regenerated manifest, a shared dependency any epic may change. While the contracts are unchanged the command rewrites no other file. |
| D27 | The E01 fix pass (fix/e01-composition) weakened or changed shared gates and SYSTEM.md without a decision on record. Joaquin Castellano, 2026-11-14. | Approved waivers, recorded here: `design_lint` is dropped from docs/build/E01/E01.gates (AGENTS section 8 skips design lint loops); branch coverage is advisory for E01 (scripts/validate_hub.py `--coverage-report-only`, which reports the figure and never fails the gate), until the domain and application coverage of AGENTS section 4 is measured; the SYSTEM.md 5.12 edit adding `ModuleRegistry.audit_policy` and `replicated_table` and the `<module>/adapters/migrations/NNNN_<module>.sql` convention stands as ratified; removing the 93 `# type: ignore[type-abstract]` of D25 is deferred to a later pass, and D25 is otherwise unchanged. Nothing else in the E01 gates is waived. |

## A2. Product decisions

The owner delegated open product questions to the coordinator on 14 November; owner answers are marked. "Applies to"
names the designs that must follow the decision.

| ID | Question | Decision | Applies to |
|---|---|---|---|
| Q1 | Clinician review of evidence and pathway content. | No clinician this weekend. Content ships labeled "not clinically reviewed"; the review step stays in the workflow and blocks nothing at runtime. Nothing on board waits for a ground clinician. | E10, E19, E27, E28 |
| Q2 | Redistribution of non-NASA sources. | NASA documents ship in full; other sources ship metadata only (title, citation, link, content hash) until permission is documented. | E10, E19 |
| Q3 | Exa for the research workbench. | **Owner: yes**, when `EXA_API_KEY` is set in the environment (never in the repository); only generalized public research questions, never crew data; without a key, local sources only. | E19 |
| Q4 | Acting medical officer and urgent cases. | **Owner: yes.** The crew member E31 assigns and who accepts the medical officer duty may disposition urgent cases, shown as acting and audited with the assignment. | E11, E15, E26, E31 |
| Q5 | CPR manikin and volunteers. | Team members only, as consenting adults. Without a manikin, CPR teaching runs in rehearsal mode labeled SIM and no CPR quality result is claimed. | E30 |
| Q6 | Consent wording. | Written into configuration with provenance by the coordinator; a team member reads it to each volunteer, who agrees before any trial. | E30 |
| Q7 | Authorship of plans and kit map rows. | One model drafts from the package sources, the other model checks against the sources; each carries provenance and the visible label "not clinically reviewed"; runtime use is not blocked on sign off. | E27, E28 |
| Q8 | Checking camera vitals. | Laptop webcam; reference is a manual 30 second pulse count by a second person, plus a pulse oximeter when available. The camera never vetoes a manual reading: both are recorded and a disagreement is flagged without blame. No accuracy claim beyond what we measure. | E06, E27, E30 |
| Q9 | "Act now" while the person sleeps. | Goes at once to someone awake (medical officer or crew member on duty); the sleeper is woken only if nobody awake acknowledges within a configured time. Lower levels wait for wake. Emergency wakes everyone. | E24, E26 |
| Q11 | Potency compensation. | **Owner:** on in every mode, LIVE included, with no outside authorization (space missions have no pharmacist). When a lot has lost potency, the proposal warns that the medicine has degraded (estimate and interval) and gives the new recommended dose next to the label dose, for the medical officer to confirm. The engine's gates still apply: never for `do_not_use` lots, estimates outside the validated range or with low sensor coverage, toxic degradants or fixed dose devices; a per product opt out in the formulary; every dose limit caps the amount with the shortfall stated. The `live_compensation_authorizations` gate is removed. | E32 |
| Q14 | Acting medical officer and doses. | For products marked `any_trained_crew`, or a medicine the active onboard plan step names for that condition, the acting officer may confirm the recommended dose (compensated when the engine recommends it). Never an allergy or interaction override. The record shows acting, with the assignment. | E28, E32 |
| Q15 | Crew goal and privacy. | Opt in per member, off until chosen; the crew view shows only progress toward the goal, never per person counts. | E22 |
| Q16 | Acting officer, clinical review and resolution. | A clinical review, yes, shown as acting. A `clinician_assessed` resolution waits for a crew medical officer; the episode stays open and visible meanwhile. | E15, E31 |
| Q17 | NASA-STD-3001 contrast and clock. | The reference design system stays the default look. A flight readability theme (ink-3 at 6:1 or better, a short clock kept on narrow screens) is selectable in configuration, off by default; the deviation is stated where we describe what we claim. | E03, E20, E21 |
| Q18 | `patient_instruction` evidence passages without review. | Ship labeled "not clinically reviewed", like the plans. | E10 |
| Q19 | Supply low threshold default. | None in the release: the alert is inactive until set. The demo fixture sets a SIM value labeled team default. | E32, E18 |
| Q21 | Next engine fix round. | After the walking skeleton runs end to end: TV-H-5, the two packaged fits, performance. | pharmacy |
| Q23 | Where the product package lives. | `docs/product/` is the living source of truth; accepted requests are applied there. | all |
| Q24 | Local copies of NASA source documents. | **Owner:** later, never in bulk: one request at a time with a pause between requests. | E10 |
| Q25 | A quality bar for the assistant's clinical reasoning beyond HealthBench. | An internal OSCE track: structured clinical exams on ankle injury, ear pain and flank pain (body map regions `ankle`, `ear`, `flank`), the format NASA used to quantify CMO-DA ("Validation of performance using OSCE's with 77-88%", AsMA 2025, cited in `docs/product/NASA-SOURCES.md`). An independent grader scores each answer against a physician-style rubric. The cases and rubric are ours and say so ("our cases, not NASA's"); the score is an internal bar, never a published accuracy claim. The pitch keeps "beside CMO-DA, not competing on accuracy"; the claims lint applies. | E18 |
| Q26 | Which tests from NASA's Clinical Findings Forms become a kit map row's confirming tests. | The candidates, quoted verbatim with page and form SHA-256, are `docs/product/cliff/cliff-confirming-tests.json`. A confirming test is one the form's diagnostic text names as diagnosing this condition by itself (CONTRACTS: any one of `confirming_tests[]` confirms); criteria the form applies together (ICL84 SIRS, ICL100 SANS) are one test whose `instruments` are all needed. Not confirming: tests named only for the differential, a complication or the worst case; items the form marks optional, unavailable or set aside; a consultant's suggestions; history. An examination by a trained crew member is a test with `instruments: []`. Where the form says no test confirms (ICL8), the row has one test, the clinical examination it describes, with `instruments: []`, and quotes that sentence. ICL88 has no form in NASA's 119 set: the row cites the 2020 Skin Infection CliFF, says so in its provenance, and its test is the examination by description. Regions: a form region maps to a `body_region` value only when it lies within it (mouth, nose, brain: `head`; throat, cervical spine: `neck`; lumbar spine: `back`; kidney: `flank`; pelvis: `abdomen`; lower extremity: `legs`; chest: `heart` or `lungs` when the form names the organ; upper extremity: the part it names, `shoulder`, `wrist` or `hand`); any other region, skin included, is `[]`. Quotes stay verbatim, the form's own dashes included. The form fetcher follows Q24 (one request at a time with a pause) and reuses a saved text in `docs/product/cliff/forms/` whose SHA-256 matches. | E27, E28 |

## A3. Package lines superseded by decisions

The package is unchanged in this revision. Each row identifies the exact original line whose requirement, example or
closed extension point is superseded. Only the stated part changes; all other safety checks and acceptance criteria
remain. D12 makes these changes executable through a separately hashed effective-contract overlay until the package
owner propagates them. It is not permission for an epic to edit the package or to skip validation. Paths below are
relative to `docs/product/`; numbers refer to the unchanged files. Repeated constraints are listed individually.

| Package file and line | Decision | What designs and builders do instead |
|---|---|---|
| `CONTRACTS.md:43` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `CONTRACTS.md:86` | D12 | Preserve contracted wire units and original quantities; use sourced UCUM mapping and explicit pressure context; round sensor canonical output once, half even, to four decimals. |
| `CONTRACTS.md:127` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `CONTRACTS.md:129` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `CONTRACTS.md:248` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `CONTRACTS.md:362` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `CONTRACTS.md:363` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `CONTRACTS.md:364` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `CONTRACTS.md:388` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `CONTRACTS.md:396` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `CONTRACTS.md:403` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `CONTRACTS.md:489` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `CONTRACTS.md:490` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `CONTRACTS.md:521` | D1 | Retain active warning definition pins; definition removal cannot clear/lower. Only pinned clearing rules or an authorized medical-officer resolution with reason may end it. |
| `CONTRACTS.md:531` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `CONTRACTS.md:659` | D12 | Preserve contracted wire units and original quantities; use sourced UCUM mapping and explicit pressure context; round sensor canonical output once, half even, to four decimals. |
| `CONTRACTS.md:661` | D3 | Keep original observation time immutable and add corrected-clock provenance internally; never substitute arrival time; REPLAY retains original date. |
| `CONTRACTS.md:668` | D3, D14 | LIVE/SIM freshness uses corrected observed_at. REPLAY uses hub receive time of the latest accepted reading advancing E, retained across restart/loops; windows keep original observation dates. Only eligible observations after raising may clear within their origin partition. |
| `CONTRACTS.md:782` | D1 | Retain active warning definition pins; definition removal cannot clear/lower. Only pinned clearing rules or an authorized medical-officer resolution with reason may end it. |
| `CONTRACTS.md:1134` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `CONTRACTS.md:1155` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `CONTRACTS.md:1208` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `CONTRACTS.md:1333` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `CONTRACTS.md:1460` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `CONTRACTS.md:1462` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `KICKOFF.md:77` | Q15, D10 | CP2 retains goal-rebuild tests and adds opt-in/default-off/revocation evidence; no implicit all-crew contribution. |
| `PRD.md:114` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `PRD.md:137` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:159` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:226` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `PRD.md:233` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `PRD.md:249` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:263` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:278` | Q9, D2 | Act goes to an awake responder immediately; wake the sleeper only after configured unacknowledged interval. Emergency wakes everyone; routine suppression and EVA policy otherwise remain. |
| `PRD.md:286` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:289` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:313` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:336` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `PRD.md:359` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `PRD.md:382` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `PRD.md:401` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `PRD.md:403` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `PRD.md:417` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:419` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:426` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:433` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:528` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `PRD.md:542` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:548` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:550` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:552` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `PRD.md:562` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `PRD.md:620` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `PRD.md:663` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `USE-CASES.md:11` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `USE-CASES.md:35` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `USE-CASES.md:46` | Q9, D2 | Apply the awake-responder cascade for Act during sleep; Emergency wakes everyone. Preserve routine suppression. |
| `USE-CASES.md:148` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `USE-CASES.md:176` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `USE-CASES.md:187` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `USE-CASES.md:250` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `VALIDATION.md:94` | Q9, D2 | V74 tests awake responder routing, configured sleep escalation and unconditional Emergency interruption; other suppression checks remain. |
| `VALIDATION.md:106` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `VALIDATION.md:124` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `VALIDATION.md:150` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `VALIDATION.md:165` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `VALIDATION.md:176` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `api/README.md:74` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `api/examples/config/assistant-policy.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/environment-profiles.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/help-presentation.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/indicator-catalog.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/questionnaires.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/routine-rules.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/sensor-catalog.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/sensor-limits.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/warning-rules.example.json:6` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/config/warning-rules.example.json:594` | D2 | Effective storm-shelter definition is emergency; wake everyone. |
| `api/examples/configuration/document-sensor-catalog.json:12` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/configuration/document-sensor-catalog.json:20` | D12 | Preserve these wire spellings; effective unit-mapping resolves UCUM and requires pressure provenance for ppm_to_mmhg. |
| `api/examples/configuration/document-sensor-catalog.json:29` | D12 | Preserve these wire spellings; effective unit-mapping resolves UCUM and requires pressure provenance for ppm_to_mmhg. |
| `api/examples/configuration/document-sensor-catalog.json:30` | D12 | Preserve these wire spellings; effective unit-mapping resolves UCUM and requires pressure provenance for ppm_to_mmhg. |
| `api/examples/kit-map.example.json:4` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/plans/guided-check-disagree.json:3` | Q8 | Effective fixture includes its retained manual observation ID and independently checked count; keep camera reference, correction and retry. |
| `api/examples/plans/guided-check-disagree.json:145` | Q8 | Effective fixture includes its retained manual observation ID and independently checked count; keep camera reference, correction and retry. |
| `api/examples/requests/config-release.json:10` | Q1, Q7, Q18 | Effective displayed notice is "not clinically reviewed"; source/purpose/applicability validation still applies. |
| `api/examples/views/habitat-transit.json:605` | D2 | Effective storm-shelter warning is emergency, with matching configured level words. |
| `api/examples/views/today-transit.json:32` | D2 | Effective storm-shelter warning is emergency, with matching configured level words. |
| `api/examples/warnings/list-transit.json:12` | D2 | Effective storm-shelter warning is emergency, with matching configured level words. |
| `api/openapi.yaml:885` | D8 | Expose the strong release/object/activation ETag in create/detail/activation response headers and compare it transactionally; keep editor/operator roles and open pins. |
| `api/openapi.yaml:893` | D8 | Expose the strong release/object/activation ETag in create/detail/activation response headers and compare it transactionally; keep editor/operator roles and open pins. |
| `api/openapi.yaml:901` | D8 | Expose the strong release/object/activation ETag in create/detail/activation response headers and compare it transactionally; keep editor/operator roles and open pins. |
| `api/openapi.yaml:6593` | D9 | Deny station clinical runs; officer lists are filtered by explicit object/field sharing, with ground receiving approved snapshots only. |
| `api/openapi.yaml:6597` | D9 | Deny station clinical runs; officer lists are filtered by explicit object/field sharing, with ground receiving approved snapshots only. |
| `api/openapi.yaml:11666` | D8 | Expose the strong release/object/activation ETag in create/detail/activation response headers and compare it transactionally; keep editor/operator roles and open pins. |
| `api/schemas/common.schema.json:482` | D12 | Add unit-mapping.json to the effective ConfigName enum and signed release/check inventory; base enum remains unchanged. |
| `api/schemas/config-environment-profiles.schema.json:233` | D11 | Extend effective comms_queue with optional sourced bundle_lifetime object; require a value for BPv7, never assume one. |
| `api/schemas/config-environment-profiles.schema.json:262` | D11 | Extend effective comms_queue with optional sourced bundle_lifetime object; require a value for BPv7, never assume one. |
| `api/schemas/config-index.schema.json:53` | D12 | The effective config index lists 19 documents, the 18 base documents plus unit-mapping.json, and releases pin unit-mapping.json; the base bounds remain unchanged. |
| `api/schemas/config-index.schema.json:54` | D12 | The effective config index lists 19 documents, the 18 base documents plus unit-mapping.json, and releases pin unit-mapping.json; the base bounds remain unchanged. |
| `api/schemas/config-onboard-plans.schema.json:398` | Q11, Q14 | Keep plan text sourced and link the named medicine to E32 proposals; no model-generated amount or global ban on the medicine engine schema. |
| `api/schemas/config-onboard-plans.schema.json:774` | Q11, Q14 | Keep plan text sourced and link the named medicine to E32 proposals; no model-generated amount or global ban on the medicine engine schema. |
| `api/schemas/config-release.schema.json:139` | D8 | Keep closed JSON; concurrency token is an exposed HTTP ETag, not an undeclared revision property. |
| `api/schemas/evidence-passage.schema.json:5` | Q1, Q18 | Separate source/purpose approval from clinical_review; effective model adds required clinical_review with clinically_reviewed/not_clinically_reviewed values and visible label; unreviewed patient_instruction is allowed with source and purpose checks. |
| `api/schemas/evidence-passage.schema.json:35` | Q1, Q18 | Separate source/purpose approval from clinical_review; effective model adds required clinical_review with clinically_reviewed/not_clinically_reviewed values and visible label; unreviewed patient_instruction is allowed with source and purpose checks. |
| `api/schemas/evidence-passage.schema.json:49` | Q1, Q18 | Separate source/purpose approval from clinical_review; effective model adds required clinical_review with clinically_reviewed/not_clinically_reviewed values and visible label; unreviewed patient_instruction is allowed with source and purpose checks. |
| `api/schemas/guided-check.schema.json:5` | Q8 | Replace disagreement no-observation constraint with a manual observation reference; camera reference remains separate; keep retry state and all other checks. |
| `api/schemas/guided-check.schema.json:406` | Q8 | Replace disagreement no-observation constraint with a manual observation reference; camera reference remains separate; keep retry state and all other checks. |
| `api/schemas/guided-check.schema.json:413` | Q8 | Replace disagreement no-observation constraint with a manual observation reference; camera reference remains separate; keep retry state and all other checks. |
| `api/schemas/warning.schema.json:222` | D1 | Do not emit config_changed to clear an active warning; keep legacy value parseable for historical records only. |
| `backlog.json:14` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:28` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:42` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:56` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:70` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:84` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:98` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:112` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:126` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:140` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:154` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:168` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:182` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:196` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:210` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:224` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:238` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:252` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:266` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:280` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:294` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:308` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:322` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:336` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:350` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:364` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:378` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:392` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:406` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:420` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:434` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:448` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:459` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `backlog.json:462` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:476` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:490` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:502` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `backlog.json:504` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:518` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:532` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:546` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:560` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:574` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:588` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:602` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:616` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:630` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:644` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:658` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:672` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:686` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:700` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:714` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:728` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:742` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:756` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:770` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:784` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:798` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:812` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:826` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:840` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:854` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:868` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:882` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:896` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:910` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:924` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:936` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `backlog.json:938` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:950` | Q15, D10 | Filter both goal totals by explicit per-member opt-in, default off; revocation removes contributions immediately, never publish member counts or identities. |
| `backlog.json:952` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:966` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:980` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:994` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1008` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1022` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1036` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1050` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1064` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1078` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1092` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1104` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `backlog.json:1106` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1120` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1134` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1145` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `backlog.json:1146` | Q1, Q7, Q18 | Sourced, hash-pinned, purpose-eligible applicable content may run labeled "not clinically reviewed"; review workflow remains and cannot imply clinical validation. |
| `backlog.json:1148` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1162` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1176` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1190` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1204` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1218` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1232` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1246` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1260` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1272` | Q11, Q14 | Permit only deterministic E32 dose proposals/confirmations under Q11/Q14; keep assistant-invented doses prohibited and the names-only SubjectSummary unchanged. |
| `backlog.json:1274` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1288` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1302` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1316` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1330` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1344` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1358` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1372` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1386` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1400` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1414` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1428` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1440` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `backlog.json:1442` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1456` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1470` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1484` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1498` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1512` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1526` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1540` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1554` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1568` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1582` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1596` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1610` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1624` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1638` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1652` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1666` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `backlog.json:1680` | Q1, Q7, Q18 | Clinical review is a visible status, not a runtime gate; keep trained-operator, source, applicability and simulation checks. |
| `tdd/E30.md:15` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `tdd/E30.md:25` | Q8 | Preserve manual and available camera observations with method/origin and disagreement; retry stays incomplete; never close a gap or infer a diagnosis. |
| `api/openapi.yaml:6764` | D9 | The plan detail role declaration is only a coarse gate; station is denied and ground sees approved snapshots only. Person reads need the matching object/field grant. |
| `api/openapi.yaml:6768` | D9 | Remove station from effective clinical plan detail roles; fixed plan content and broadcasts remain available. |
| `api/openapi.yaml:11635` | D9 | A subject query parameter never grants chart access; station cannot use it to read clinical runs and officer reads still require explicit sharing. |
| `api/examples/configuration/release-active.json:1` | D8 | Effective HTTP example includes its response ETag alongside unchanged ConfigRelease JSON. |
| `VALIDATION.md:225` | Q5, Q6, Q8 | Venue reference is a consenting adult team member's independent 30-second manual pulse count; an oximeter adds a reference when present. Freeze consent/protocol and report every refusal and disagreement. |
| `VALIDATION.md:227` | Q5, Q6, Q8 | Use the Q8 manual reference for camera validation and preserve separately configured teach timing. Without a manikin CPR teaching is SIM rehearsal and earns no CPR quality claim. |
| `CONTRACTS.md:210` | D6 | Cache for 24 hours, retain durable identity for mission life, bind target/semantic parameters as well as body, and authorize stored-result visibility before replay. |
| `api/openapi.yaml:11657` | D6 | Effective Idempotency-Key description includes resource/parameter identity, mission-life durable receipt and current access checks. |
| `CONTRACTS.md:1108` | D4, D18 | Hub changes still commit atomically; journal/report uploads and extraction versions use sealed private writes, hub-reference transactions and acknowledgment only after both stores. Confirmations remain atomic hub records. |
| `CONTRACTS.md:1114` | D4 | Separate journal storage obeys the two-store recovery and paired-backup barrier, never independent uncoordinated commits. |
| `CONTRACTS.md:783` | D1 | An active rule/sensor warning's manual resolution requires a medical officer and reason; assistant cannot resolve it. |
| `api/schemas/sensor-channel.schema.json:5` | D3, D14 | LIVE/SIM use corrected observed_at; REPLAY uses the durable receive reference of the latest accepted reading advancing E. last_received_at alone cannot make a repeated/late row fresh. |
| `CONTRACTS.md:519` | D12 | Effective releases pin the 18 base documents plus the accepted unit-mapping document and decision-overlay version. |
| `CONTRACTS.md:748` | D13 | Attempt rule evaluation inside the transaction savepoint; a typed computation failure preserves valid capture with durable pending evaluation and a visible fault, never clears a warning. |

| `CONTRACTS.md:203` | D17 | Public Job IDs apply to the three public kinds; assistant work reconnects with existing turn identity and internal durable tasks. |
| `CONTRACTS.md:954` | D17 | A model-turn job is an internal worker task; keep existing conversation stream/cancellation and no duplicate work on refresh. |
| `CONTRACTS.md:1112` | D17 | Long work uses the shared task runner; only assessment, report_extraction and participation_rebuild expose public Jobs. |
| `api/openapi.yaml:10168` | D17 | Interpret model-turn job as the internal task bound to turn_id; retain AssistantMessage response and conversation stream/cancel endpoints. |
| `CONTRACTS.md:234` | D6 | DUPLICATE_COMMAND with null current_state means rejected identity reuse; an authorized identical accepted replay carries the closed accepted_command receipt of SYSTEM 5.8. |
| `api/schemas/error.schema.json:90` | D6 | Allow the closed accepted_command receipt in current_state for an authorized matching durable replay. Mismatched identities always return null; no clinical values or body fingerprints are exposed. |
| `CONTRACTS.md:1085` | D9 | Medical-officer status does not grant every plan run; require current object/field sharing for clinical reads. |

# Part B. Medicine potency engine

## 0. Why the independent tests fail: root causes found

The independent tests (V1, V6, V2 and the virtual studies) fail because the engine misrepresents reality in five
places. The fixes are engine fixes; the tests are corrected only where a test compared the wrong quantities (section
8 and the V2 count in section 4).

1. **Calibration and prediction conditions disagree** (V1). The class priors were derived from Du 2011 ground
   controls stored in a JSC chamber at 36 to 44 %RH, then treated as if measured at 25 C / 40 %RH, and V1 validation
   placed the same samples at 25 C / 60 %RH: repackaged rows were predicted 2.2 times too fast. Section 4.
2. **The class priors are overconfident** (V1, V6). Each dose-form family's spread came from 4 to 11 products with no
   allowance for estimating it (the semisolid sd of 0.20 rests on four rows). Measured offline on the held-out rows:
   the current construction covers 9 of 16 even at the right condition; pooled between-product variance with a
   Student-t predictive covers 12 of 16. Section 4.
3. **The spaceflight factor contradicts the data** (V2, V1 flight). Du 2011 flight samples degraded 1.3 to 1.8 times
   faster than identical ground kits (median ratio 1.47 over 34 rows) although the flight kit was drier; the engine's
   factor has median 1. Predicted failures at day 880: 5, documented 23 to 24. Section 4.
4. **Study fits ignore the package** (VS-1, VS-3, VS-5). Packaged ICH samples experience the package's internal
   humidity (the aspirin bottle's moisture time constant is 2449 days at 25 C, so a 6-month 40 C / 75 %RH pull sees
   about 41 %RH), but the fit is told the chamber's 60 to 75 %RH; the engine then applies the fitted humidity term to
   the internal humidity again, counting the package twice and predicting too little loss (VS-3 medians 0.8 to 3.7
   points above the truth). Section 6.
5. **Anchors measured in flight count the spaceflight factor twice.** Two anchors were measured on the ISS
   (`aspirin_tablet_iss_550d`, MDP05; `acetaminophen_tablet_iss_6mo`, MDP16), so their rates already contain the
   factor of root cause 3, and the engine multiplies a station lot by the factor again. Once the factor is calibrated
   (median about 1.47), aspirin and acetaminophen in a station kit would be predicted about 1.5 times too fast.
   Section 11.

## 1. Requests accepted as proposed

Every request in `docs/requests/B1.md` (R-01 to R-21, R-FIT-1 to R-FIT-8, R-STORE-1, the R-19 list and the "Spec
findings"), `docs/requests/B2.md` (R1 to R24) and `docs/requests/B3.md` (R1 to R31) is accepted as proposed, except
where a section below decides otherwise. Where B1 and B3 proposed different shapes for the same point, the B1 shape
(already implemented) wins and the oracle adapts: B3-R4 uses B1 R-08 (molar basis in `evaluate_draws`, mass
conversion in the pipeline); B3-R7 uses B1 R-12 (`Forecast.quantile_probability`); B3-R20 uses B1 R-20 (AICc `None`
when n <= p + 1, `select_model` raises `InputError` on a candidate without AICc). B3-R1: the virtual-study fixture
stays at `tests/virtual/virtual-studies.json`; spec 15.8 and the build plan say so. B3-R28: the location is the module
docstring of `pharmacy.decision`. 15.7 one batch: `min_batches = max_batches = 1` means adaptive stopping off.

## 2. Performance (B1 R-21)

The engine must run the demo end to end in a normal test session. Exactness of the reference path is not negotiable;
speed comes from engineering and from approximations whose error is bounded and checked on every estimate.

1. Threads: `evaluate_draws` runs its fixed segment chunks on a thread pool; partial sums are reduced in chunk order,
   so results are bit identical for any thread count. Thread count comes from engine-settings (0 means the CPU count).
2. Compressed history: a fast evaluation of Theta over a compressed exposure history (for example blocks of
   consecutive segments replaced by quadrature nodes on each block's empirical distribution of the rate drivers),
   designed by the implementer and written into the spec with its error analysis. Rule: on every estimate the
   compressed and the exact evaluator are compared on a fixed probe set of draws that spans the drawn parameter
   support (smallest and largest Ea, B, zone offsets and multipliers of the batch, plus fixed quantile points); if the
   largest relative difference of any pathway's Theta exceeds `numerics.compression_relative_tolerance` (team default
   1e-7) the estimate uses the exact evaluator. The trace records the probe error and which evaluator ran. The exact
   evaluator stays public and is what the oracle checks at 1e-9.
3. Forecasts reuse the estimate's per-draw state at as_of; nothing up to as_of is recomputed.
4. Targets on this machine, one process, reported every time (not asserted in unit tests): the demo at day 100 for
   all lots through `assess_inventory` within 120 s; day 400 within 300 s; the end-to-end test of 15.6 within 15
   minutes.

## 3. Degradants: identified before they condemn a lot (B1 finding, demo day 100)

A degradant estimate is only as good as the evidence that the pathway forming it really runs at that rate. Class
priors and anchors from assay loss measure total loss, not which pathway caused it, so attributing that loss to a
toxic degradant is a worst case, not an estimate.

* A degradant prediction is identified when its forming pathway's rate comes from evidence specific to that pathway:
  a measured model that lists the degradant, or an anchor that measured the degradant's formation (a new anchor field
  naming the degradants it measured, empty by default). Otherwise it is unidentified.
* An identified degradant above its limit makes the lot `do_not_use` (as now).
* An unidentified degradant whose probability above its limit reaches the decision threshold gives the warning reason
  `degradant_unidentified_above_limit`. It forbids potency adjustment for the lot, the lot state follows potency, and
  the decision says to inspect the lot or assay the degradant. It never makes the lot `do_not_use` by itself.
* The trace states, per degradant, whether it is identified and by what.

## 4. Class-prior reference conditions (validation finding V1)

The total-rate priors were used as if measured at (T*, RH*) = (298.15 K, 40 %RH) while V1 validation placed the same
Du 2011 ground controls at 25 C and 60 %RH: calibration and validation disagreed by about exp(0.04 x 20), a factor of
2.2. Fix the inconsistency at its source:

* Each `total_rate_priors` entry gains the storage condition of its calibration data, with the same fields and
  semantics as an anchor (`temperature_k`, `temperature_sd_k`, `rh_percent`), and converts to (T*, RH*) by spec 9.3,
  adding the variance terms of 9.3.
* Du 2011 ground controls (MDP01, MDP02): "ground controls in a JSC environmental chamber", RH 36 to 44 % (read as
  40 +- 2 %RH, sourced). The chamber temperature is not in the research notes: 295.15 K (22 C) +- 1.5 K as a team
  default matching the ISS cabin, to be set by the chemistry lead from MDP03.
* V1 ground-control validation uses a declared zone built from these same fields (in tests), so calibration and
  validation can never disagree again. V1 flight and V6 keep the declared station zone (B3-R27).

### 4.1 Calibration is code, not hand arithmetic

A new offline module `pharmacy/calibration.py` (like `fit.py`: never called by an estimate) derives the class priors
and the spaceflight factor from the calibration rows of the Du 2011 dataset (odd rows plus the solution rows the
config names; `tests/data/vectors/validation-du2011.json` is its input file, passed as an argument) and writes the
config entries with full provenance: method, rows, statistics and the engine version. A unit test reruns it and
asserts the committed config values equal its output exactly, so no prior can drift from its derivation.

* **Total-rate priors.** Per row, the two-point first-order ln k from day 13 to day 880 of the ground control, then
  converted to (T*, RH*) through the engine's own physics at the row's calibration condition (section 4) and package
  (repackaged rows in `repackaged_bag`, others in their original container), using the family's Ea and B prior means.
  Rows whose loss is not positive are kept through a censored likelihood or an equivalent documented rule, never
  dropped silently. Family means; one between-product variance pooled across families (a random-effects model fitted
  by REML or the pooled within-family variance, documented); predictive spread s sqrt(1 + 1/n_f) with Student-t
  degrees of freedom N - F. Because the engine draws ln k as normal, the stored sd is the normal sd whose central
  interval at the decision coverage (90 %) equals the Student-t predictive interval; the note says so.
* **Spaceflight factor.** Per calibration row, ln(k_flight / k_ground) minus the ratio the engine's physics predicts
  for the two arms (flight 23 to 25 %RH, ISS cabin temperature; ground as above; the row's package): this residual is
  the unexplained spaceflight factor. Its median and Student-t predictive spread become the lognormal rate multiplier of
  `station_kit` (basis derived, MDP01 and MDP02). `transit_kit`, `moon_base_pharmacy` and `mars_base_pharmacy` take the
  same prior with basis team_default and a note that no surface data exist, to be set by the pharmacist and flight
  surgeon.
* **V2 failure counts** compare each row with its own USP lower limit (the dataset's `usp_range_percent`), one count
  per formulation, flight arm; they are reported against the documented counts.

## 5. Validated range below the lowest study temperature (B3-R24)

A measured model fitted from long-term data at 25 C and accelerated data above it is routinely applied to storage a
few degrees cooler. Engine-settings gains `validated_range.temperature_below_min_margin_k` (team default 10 K, to be
set by the chemistry lead): temperatures down to the model's minimum minus this margin are inside the validated range;
the 8.2 widening distance below the range is measured from the extended limit. Above the maximum nothing changes. VS-3
and VS-4 use B3-R24's wording.

## 6. Study fits use what the product experienced (B3-R15, root cause 4)

The humidity term of a rate law is a function of the humidity at the product (spec 5.1), so a measured model's B and
its validated RH range refer to the product's internal RH. `fit_stability_study` accepts, per observation, the logged
piecewise-constant chamber history from the start of the condition to the pull (constant setpoints remain allowed as
a degenerate log) and the package the samples were stored in (none for open dish). It computes the internal RH trace
with the engine's own microclimate functions and Theta with the engine's own humidity integral, so a fit and an
estimate use identical physics. The virtual studies hand the fits the simulator's chamber sensor readings and the
package, as a laboratory would. "The selected model's 95%
prediction interval" is the 2.5 and 97.5 % quantiles of potency over 4000 draws from the multivariate normal of the fit,
with a seed derived from the fit's inputs.

## 7. Model selection when kinds cannot be told apart (B3-R29)

Among the kinds within 2 AICc units of the lowest, `select_model` chooses the one that predicts the lowest potency at
the longest time and highest temperature of the study's long-term condition (the conservative choice, in the spirit of
ICH Q1E); ties go to fewer parameters, then kind order. VS-2 becomes one sided: over misselections, the truth's potency
at 25 C / 60 %RH and 24 months lies below the selected model's 2.5 % prediction bound in a fraction no larger than
the binomial allowance for 0.025 (3 standard errors); two-sided coverage and the confusion matrix are reported.

## 8. Covering a noisy measurement (B3-R31)

Wherever a published or simulated measured value is compared with the engine, the comparison is against the
predictive distribution of that measurement: the engine's potency draws, each plus a normal assay error with the
measurement's own sd, before quantiles are taken.

## 9. Tests never write the committed BDE cache (B3-R25)

The cache directory can be set by the environment variable `PHARMACY_XTB_CACHE_DIR` (location only; entries are
content addressed and verified on read). A session-scoped autouse fixture in `tests/conftest.py` copies the committed
cache to a temporary directory and points the variable at it, and a session-end check fails the run if the committed
cache's bytes changed.

## 10. Defects to fix

* `placards[].share_nominal` can exceed 1 by rounding (B3 finding 3); shares are clamped to [0, 1] after
  normalization, with a property test.
* Oracle D2 `rate_function` must use `log1p` near alpha = 0 (B1 finding for B3).
* Simulator OU variance self-test uses the sampling-distribution band of B3-R2, and the three strict xfails go.

## 11. Anchors carry where they were measured (root cause 5)

An anchor states the storage zone and package its rate was measured in, with the same meaning as the calibration
storage condition of a total-rate prior (section 4):

* `storage_zone_id`: a zone of `storage-zones.json` (contract validation checks it exists). Ground and laboratory
  data name the ground zone, whose multiplier is fixed at 1; the two ISS anchors name `station_kit`.
* `package_id`: the package the product was stored in, or null when the rate refers to the product itself (solution
  kinetics, open dish). When it is set, the anchor's `rh_percent` is the ambient RH and the conversion to (T*, RH*)
  uses the internal RH that the engine's microclimate gives for that package at the anchor's constant condition over
  the anchor's duration, so an anchor and an estimate share identical physics (the reasoning of section 6). The anchor
  therefore also states `duration_days`; the start of the internal RH is the package's configured
  `initial_internal_rh_percent` (spec 4.3), exactly as for a lot. The anchor's ln k is matched to the mean rate over
  the duration (Theta divided by the duration), which is what a two-point first-order rate measures.

Conversion to the ground-equivalent rate at (T*, RH*) subtracts the zone multiplier's ln mean. Its uncertainty is
correlated with the lot's own zone factor, not independent of it: the factor that sped up the measured sample speeds
up a lot stored in the same zone. Adding its variance independently (the first draft of this section) would count it
twice for exactly the lots the anchor describes best. Spec 9.3 gives the exact rule: the anchor enters the 9.4
combination with the factor's variance included, and the pathway's ln k draw subtracts lambda times the zone
multiplier's drawn deviation through `Pathway.zone_correction`, sharing the zone's variate column (spec 9.7). The
multiplier is read from configuration at conversion time, so recalibrating the factor (section 4.1) updates every
flight anchor without editing it.

Packages (research notes, space-pharmacy.md): Wotring 2016 (MDP05) stored its nine products repackaged, so
`aspirin_tablet_iss_550d` uses `repackaged_bag`, and the V6 rows (the same study) use `repackaged_bag`, not
`original_bottle`. Bogomolov 2015 (MDP16) does not state the package: `original_bottle` stays a team assumption
recorded in `storage_context`. The GCRSim amoxicillin control arm (MDP11, MDP12) was in sealed manufacturer
containers: `original_bottle`.

## 12. Calibration choices (CX-CAL requests)

* **CAL-01 accepted.** Family Ea and B for calibration are equal-weight mixtures of the applicable destructive
  thermal class priors (mean of locations; sd from the mixture variance), unclassified thermal included.
* **CAL-02 accepted.** The stored sd is sqrt(sigma_t^2 + v_conversion), where sigma_t matches the Student-t
  predictive interval and v_conversion is the family's mean 9.3 conversion variance (shared, not divided by n).
* **CAL-03 accepted.** The V1 flight zone comes from the four flight condition statistics; spec 15.5 says so.
* **CAL-04 decided differently.** The flight factor's ln location is the mean ln residual, not the median: with the
  sample sd and sqrt(1 + 1/N) on N - 1 degrees of freedom it is the exact Student-t predictive for a normal sample,
  and "median" in section 4.1 meant the multiplier's median, exp(mean ln). The factor's median is 2.02 (ln sd 0.40).
* **CX-C R1 accepted.** For a packaged anchor or calibration row the 9.3 humidity variance term uses the
  rate-weighted internal RH (the derivative of the log mean rate with respect to B), not a rate-equivalent RH. Both
  chemistry and calibration use it; spec 9.3 gives the closed form.
* **CX-C R2 accepted.** `chemistry.parameters.anchor_at_reference` takes a required keyword-only `bundle`.

