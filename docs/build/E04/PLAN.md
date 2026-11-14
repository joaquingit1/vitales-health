# E04 build plan

Planning only. No product code, git command, implementation gate or clinical-validation claim. The frozen design is
`docs/tdd/E04.md:1`; this plan resolves its pending delivery points without editing it. `tasks.json` contains the
standalone coder briefs and exact commands. A senior reviewer must accept the plan before coding.

## Build order and connections

The checkout has no `hub/`, `ui/`, `packages/contracts-ts/` or `eval/`. `contracts/` and `config/` contain pharmacy
artifacts only. Every assigned implementation file is absent. E01's kernel, generated contracts, toolchain and
`kernel.testing` are prerequisites, not work assigned to E04 (`docs/SYSTEM.md:1623`, `docs/SYSTEM.md:2388`).
E04 acceptance requires real E01, E02, E03, E23 and E15-core (`docs/SYSTEM.md:2338`). E15-core builds first without
reverse intake/urgency/participation imports, then binds consumers late (`docs/SYSTEM.md:141`).

| Task | Builds | Starts after | Handoff |
|---|---|---|---|
| T1 | Pure policies, catalog loading/checks, family capture manifest and independent goldens | E01 kernel/types; accepted configuration/reference shapes for positive catalog acceptance | Immutable IntakeCatalog, Route, QuestionPlan and policy results for T2; reviewed content/examples for T4 and E03 |
| T2 | Commands, authorized queries, intake tables and causal index recovery | T1; canonical record/episode/consent bindings | StartConcern/AnswerConcern, IntakeQueries and E15 reverse protocols for T3 |
| T3 | Generated HTTP boundary, module registration and contract tests | T2; approved effective operations/errors | Normal composition-root routes, settings/check/task registration |
| T4 | Configured Vue screens and E03/E20/E26 integration | T1; generated view/examples and actual shell/offline/help agreements | Feature routes and component tests; can run alongside T2/T3 |
| T5 | Real-stack HTTP/browser, fallback, privacy and load acceptance | T2, T3, T4 and each scenario's real providers | Complete traced evidence, final gates and review handoff |

All tasks own exact disjoint files from design section 13 (`docs/tdd/E04.md:582`). No task owns shared configuration,
contracts, generated clients, shell, offline storage, provider tables or the harness. Other designs' section-13
inventories and every current `docs/build/*/tasks.json` were checked: no other epic claims these 49 files. E03 owns
`config/questionnaires.json` and `config/ui-copy.json`; E21 owns runner/provisioning and the demo fixture. T1's
`hub/tests/intake/golden/family-capture.json` is the reviewed R-E04-5 manifest and golden handoff, not a production
configuration substitute. E03 incorporates the authored intake definitions through the existing request. T1 never
self-certifies independent checking. E04's listed `cli.py` stays unassigned because no intake CLI behavior is specified.

The connection is UI/generated client to T3 to T2 to T1 and provider ports. One caller UoW appends the E01 Answer,
prepares E04 chain/E15 route, then permits E01's once-only AnswerAccepted dispatch with mandatory E15 attachment before
isolated computations. State, receipt, audit and replication commit together. E15 owns public concern/onset/revision
and completion; E11 owns urgency and engine help; E04 owns only intake metadata/index and its application behavior
(`docs/tdd/E04.md:172`, `docs/SYSTEM.md:1950`). No internal model/device/ground call blocks capture.

## End-to-end evidence

T5 owns all three scenario files below, plus `hub/tests/intake/test_load.py`. Each journey uses the normal
`create_app(settings)` composition root, real temporary SQLite and actual hub services, or Python Playwright against
that running hub and built UI. No mocks, dependency overrides, direct clinical SQL seeding or HTTP stubs inside the hub.
The explicit E21 demo fixture/provisioner and documented fault/clock controls supply inputs. Optional external parts
are absent or use documented external fakes. Kernel fakes establish component behavior only (`docs/SYSTEM.md:2388`).
A missing provider blocks its positive journey; a denial-only interim cannot satisfy it. The original unit/integration
IDs below are also exercised at end-to-end level to meet every V and D obligation (`docs/tdd/E04.md:505`).

| Owned file | Required journey and proof | Trace |
|---|---|---|
| `eval/scenarios/e04/test_intake.py` | Narrative and area-only entry, unknown onset, urinary route with relevant restricted purpose/decline and unrelated fields hidden; full deterministic capture with model stopped, devices absent and ground down; explicit origin and presenter separation, urgent SIM sends no real external message | 011/040/050/080/091; E04-01/02, R04/R07/R15, V09/V40, D05 |
| Same | Decline/unknown survive HTTP, reload and rendered history without a negative finding; blank creates no Answer; supersession retains the original | 002/003/020/061; E04-03, R05/R06, V12 |
| Same | Old/future/skewed capture and onset retain provenance/precision/age across clock corrections and mission boundary; unsupported or ambiguous UCUM remains held with no guessed accepted value | 030/031/032; V14/V15 |
| Same | Actual microgravity context excludes standing provocation; unknown environment blocks contextual new questions, fixed help remains | 041; R07, V17 |
| Same | Keyboard and screen-reader completion, non-color unknown/failure interpretation; present symptom is a hollow ring before and beside a real measurement dot, other presence states have no ring | 095/098; R24, V36/V50, D07 |
| Same | Concern/update without daily check counts the original day once through real E15/E22, including retry/boundary/correction; outcome/private content does not change counts; no participation or celebration on concern/history/shared care | 094/099; R31, V43, D14 |
| Same | Missing red flag/pending evaluator prevents E13 work; accepted correction invalidates older model snapshot; E12/E13 cannot select hidden/history-only IDs, answer or mutate safety | 051/071; R28, D05; D17 intake contribution only |
| `eval/scenarios/e04/test_offline.py` | Cached red flags precede saving/auth/network/model; unknown visibly offers help; yes opens fixed help within 500 ms with hub/model down. Remove client follow-through and real E11/E26 supplies the independent engine backup | 004/091/092; R15, V61, D05; critical red-flag path |
| Same | Crash at command phases and accepted-response loss; restart/refetch, duplicate and weeks-late receipt, lost-create dependency recovery, same body on another target and changed pin/body rejection; retained owner ciphertext survives disk/quota/failure | 043/044/066/097; R25, V16/V35; critical durable-capture path |
| Same | Permanent typed computation failure preserves input/pending marker/safety state; storage/marker/audit/commit faults abort. Older recovery cannot lower newer safety state; help remains available | 044/066/092; D13, I8, V35/V61 |
| `eval/scenarios/e04/test_privacy.py` | Owner/narrow field grants work; other crew/officer/acting/helper/station and expired/revoked grants fail across HTTP, errors, receipts, events, caches, sync/export and restored access. No proxy self-report for incapacitated subject; fixed help/limited E26 summary remains | 042/070; R02/R20, V25; critical privacy path |
| Same | A queues sensitive input; restart and B sign-in cannot decrypt/replay it; lock clears late callbacks before another screen; A later unlocks with current authorization and gets one acceptance | 096/097; R02/R25, V16/V35; independent privacy backup |
| Same | Actual E03 journal canary absent from prompts/retrieval/exports/routine logs and private screenshots; intake text and reconstructable symptom metadata absent from general audit/metrics | 070; R20, V26 |
| `hub/tests/intake/test_load.py` | Real release activation changes configured label/purpose without code or safety-mapping change; invalid activation retains last good; edit/removal/rollback retain historical pins. Maximum combined sizes, bounded rebuild yielding and execution deadlines under external load; cached help budget and measured reference latency/memory | 064/100/101; R43, I3/I8/I9, charter section 6 |

Numbers in the table are T-E04 suffixes. `test_intake.py` needs E01/E02/E03/E23/E15-core; its completion, dot and
assistant cases additionally need E22, E05 and E11/E12/E13 respectively. Offline cases need E20/E26/E27-guidance;
the engine backup additionally needs E11. V26 needs the real E03 private-store/E02 vault and the actual
prompt/retrieval/export observers. E21 supplies bounded provisioning, external model stub/stopped control and browser
launch. These are acceptance dependencies, never permission to add production model/ground dependencies to intake.
E13 owns full D17 insight behavior; E04 proves its intake contribution only (`docs/tdd/E04.md:580`).

## Design deviations

The current conformance report is an author response, not a new independent greenlight
(`docs/tdd/reviews/E04.conformance.md:3`). No fixed review defect is reopened. Each remaining partly fixed blocker or
major is settled below as a concrete implementation hold or winning behavior. Review numbers refer to the cold review
`docs/tdd/reviews/E04.astra-r1.md:1`; current design sections are cited instead of that review's obsolete line numbers.

| Design section / review | Winning rule | What to build |
|---|---|---|
| 2, 4, 5; 2/5/16/17 | SYSTEM 4.2/4.4/8.1/9, D13; R-E04-7/11 | T2 imports canonical EpisodePort/DTOs and E01 records ports only once published. E15-core precedes E04. E04 owns intake_concern/intake_chain only; no EpisodeKernel alias, early dispatch, local record API or unbounded AnswerStore. Mandatory attachment then isolated evaluators; causal staged rebuild and conflicting branches remain blocked until exact checkpoint agreement. |
| 3.1, 8.1; 1/4 | SYSTEM 5.2/5.6, D1/D3; R-E04-10 | Preserve future/uncertain original capture and causal order. Open work stays pinned; base active-header mismatch remains a rejection until historical exception is accepted. T2/T4 never relabel/re-key it. T5 must test positive retained-pin acceptance after approval, not declare mismatch-only behavior complete. |
| 3, 8; 7/11/12 | AGENTS 1, SYSTEM 5.6/5.12, Part A Q1/Q7/Q18; R-E04-1/5 | T1 validates explicit classification, all family components, unsuppressible red flags, total routes and every kind's unknown/declined. E03 publishes actual authored forms and retained references. Missing sign-off is a label; missing source/purpose/rights/applicability or rejected content stays blocked. No generic/example form masquerades as family delivery. |
| 6; 13/15 | Generated contract and SYSTEM 5.5/5.8, D5/D6; R-E04-4/12 | T3 requires accepted/generated error and area-only changes. No fabricated narrative or undocumented code; /supersedes uses array JSON pointers and null current_state. D6's exact authorized accepted receipt is the only duplicate exception. No invented start ETag or answer If-Match. |
| 6/7; 18 | CONTRACTS 19, SYSTEM 8.3; R-E04-2 | T2/T3 compute approved server projection/history; T4 renders it. No client-routed or full-history interim. Delivery stays blocked until closed view schemas, pagination, words and operations exist. |
| 7/9; 3 | SYSTEM 5.8/8.6, DEPLOYMENT 5.5/5.5.1; R-E04-9 | T4 consumes E20 owner-encrypted queue and published create-ID/receipt recovery. Lock discards plaintext and callbacks, not ciphertext. No second queue/crypto, fabricated help ref, owner transfer or choice-only assistant write. T5 exercises weeks-late lost-create response. |
| 10; 6/8/9/10 | SYSTEM 8.3/8.4, D9 | T2/T3/T4 enforce current object/field/purpose grants even for officers/helpers; fixed help and limited open-session summary remain. Historical corrections are separate from current questions. Relevant urinary reproductive fields are permitted with purpose/decline. General telemetry contains only opaque refs/codes. |
| 11/12/15; 14/19 | AGENTS 6, SYSTEM 5.11/12 and charter 4/6/8 | T5 adds real-stack journeys to every V/D, including V12/V14/V15/V17/V26/V43 rather than stopping at unit/component proofs. Require actual execution deadlines, branch-per-file/condition/complexity/mutation/trace gates and nonzero test inventory. Do not reuse the engine validator. |
| 15 gate syntax | SYSTEM 6.2, E01 section 15 (`docs/tdd/E01.md:779`) | Commands use uv --project hub and explicit -c hub/pyproject.toml for pytest isolation from the engine. Generated import rules cover E04 automatically. E01 must bind coverage/safety/mutations to E04 before T5 can pass; the existing proposed coverage inventory covers E01 only. |

## Requests

Addressed to the coordinator. Pending is not approved. This planner may edit only `docs/build/E04/`, so it cannot
append to `docs/requests/E04.md` or `AI-USE-LOG.md`. The canonical existing requests already record the runtime gaps;
the additional handoffs below must be recorded there by the coordinator before dependent dispatch. Nothing here
claims an unfiled handoff is a decided request (`AGENTS.md:53`).

| Canonical request / owner | Concrete delivery needed | Hold |
|---|---|---|
| R-E04-1/3/5; E01/E03, with E04 authorship | Accepted intake schema and complete sourced definitions/copy; per-family optional component manifest and independent goldens; retained family/dictionary raw-byte references | T1 positive catalog and T4 complete forms; full E04-02 remains required |
| R-E04-2/4/12; E01/E15/E03 | Closed generated IntakeView/history contracts and error tables; area-only null narrative/explicit selection/original entered time and atomic initial answer | T3 view/area delivery and T4 complete UI; full E04-01 remains required |
| R-E04-7/11; E15/E01/E02/E11/E13 | Provider-owned EpisodePort/DTO exports and nullable route clearing; field/purpose grants; typed snapshot-bound answer append/query/payload, producer preparation/publication order, completion readiness and causal checkpoint/rebuild/resolution binding | T2 production integration/rebuild; no consumer-local replacement |
| R-E04-9; E20/E03/E13/E26 | Published owner-queue dependency resolution, immutable first-send identity, lost-create/post-cache authorized resource recovery and ordinary human answer binding | T4 dependent offline/reply integration and T5 positive restart/receipt paths |
| R-E04-10; coordinator/E01/E11/E15 | Explicit accepted historical answer/correction Config-Release exception with exact retained definition and response header semantics | Post-activation positive submission, never cached fixed help |
| R-E04-13; E01 | Pending seam lint support; manually verify canonical renamed declarations meanwhile | Syntax lint cannot establish provider agreement |
| Extend R-E04-11 with R-E01-21; E01/E20 | Publish how E04 query/command/parse deadlines use shared cancellation/watchdog and bounded historical checkpoint work. Include stalled Python callback and writer release, not only SQLite lock wait | T2/T3 deadline binding and T5 101; no local deadline mechanism |
| Additional assurance handoff to record under E04; E01/E21 | Supply exact runner/fault/clock/Playwright and observed prompt/retrieval/export bindings. Extend validate_hub.py coverage/safety/mutations/trace registration to all files under intake/domain and intake/application, I1/I3/I4/I6/I8/I9, and every listed T/R/V/D. Per-file branch ratio >=95%, full safety conditions, complexity <=15, 100% killed non-equivalent critical mutants; fail missing report/scope/provider or zero collection. Preserve E01's existing ten-root check independently. Use E01 15's explicit JSON branch-report/--report pattern with the isolated .cache/e04-coverage.json test artifact; the E04 contributor binding is not yet published | T5 assurance and real-stack evidence; no invented local runner, coverage format or mutation settings |
| Run attribution and review; coordinator | Append AI-use rows for this 2026-11-14 Codex planning run and later coder runs; record senior plan review, independent content/golden checking and build review in the owners' existing locations | Required before hand-in; no task edits the shared log/review files |

R-E04-6/8 ownership is settled; do not re-request it. No new user product decision is needed. All affected requirements
remain in scope while their gates are blocked (`docs/requests/E04.md:1`).

## Gates and hand-in

Each task's exact commands select its owned tests and Python or TS files. T5 additionally runs contracts, all E04
scenarios, complete hub tests, configuration/interfaces/generated/trace/coverage/safety/mutation gates and pnpm checks.
Commands come from `docs/tdd/E04.md:632`, `docs/tdd/E01.md:779` and SYSTEM section 6; unavailable tools are blocked gates.
No implementation commands were run in this planning session. Fixes rerun the full suite. Independent build review is
capped at three rounds; the coordinator decides at the cap (`AGENTS.md:181`). CI and AI attribution remain required.
