# E16 build plan

Planning evidence only. No implementation gates or independent plan review have run.

## Basis and ownership

Use docs/product/tdd/E16.md:7 for E16-01 to E16-03 and R19/R26, with the mechanisms in
SYSTEM sections 4 to 10. As explicitly instructed, there is no new design document.
At inspection, docs/tdd/E16.md, docs/requests/E16.md and every E16 review were absent.
Also absent: hub/, ui/, eval/, contracts/api/ and the non-pharmacy runtime configuration.
contracts/ and config/ currently contain the potency engine's separate pharmacy artifacts.

SYSTEM 4.2/4.4 and 6.1 allocate the handoff module, its tests, ui/ground/, per-module crew
features and per-epic scenarios (docs/SYSTEM.md:103, docs/SYSTEM.md:1625). Since E16 has no
section 13, these allocations replace that missing ownership section for this plan:

| Task | Exclusive owned directories | Concern |
|---|---|---|
| T1 | hub/src/crew_health/handoff/, hub/tests/handoff/ | Approved snapshot and ground receipt/reply lifecycle, API and persistence |
| T2 | ui/crew/src/features/handoff/ | Crew selection, exact preview, explicit approval and history |
| T3 | ui/ground/ | Open MCT reader application, receipts and attributed advice |
| T4 | eval/scenarios/E16/ | Real-stack acceptance and fault evidence |

Directories instantiate SYSTEM 4.1/6.1's module layout, not new shared modules. Coders choose
private implementation basenames only inside their directory. Public module.py, application/ports.py
and replica.schema.json follow SYSTEM 4.1/5.7.1/5.12. No task owns root configuration,
package manifests outside ui/ground/, contract generation, E21 fixtures/runner, or another epic's code.
All available docs/build/*/tasks.json and other designs' section 13 allocations were checked.
Recheck them before dispatch because other planners are active.

## Build order and connections

1. Coordinator settles the shared prerequisites below; E01 provides effective generated contracts,
   kernel.testing, toolchains and the composition root. Independent pure/unit work can start against
   existing protocols and kernel fakes; a missing protocol is not permission to invent it.
2. Start T1, T2 and T3 together after their own contract/content prerequisites settle. UI unit work
   uses generated examples; live UI gates wait for T1 and the shell/hosting providers. No shared edits.
3. T4 follows T1/T2/T3. It drives create_app(settings), real temporary SQLite and authenticated HTTP,
   plus the real crew and ground apps with Playwright. E01/E02/E15 are runtime hard dependencies;
   E09/E29 remain optional (docs/SYSTEM.md:2368). Delivery acceptance needs real E29 integration;
   missing-E29 acceptance separately proves durable local queues with no invented delivery.
4. Coordinator runs every task gate, full hub/UI suites, shared contract/trace/safety/mutation gates,
   independent review and CI. Every V/D clause below must pass; missing providers are blocked evidence,
   never skips, xfails or successful reduced scenarios. Code review follows the build cap in AGENTS 5.

T1 owns only handoff_handoff and handoff_receipt (docs/SYSTEM.md:103). Use the shared UoW,
command ledger, outbox, audit and replication. E29 owns transport, contacts, queue dispatch,
BPv7, retries, GroundLogComposer/GroundLogView and getGroundLog. E26 owns GroundMessage,
its onboard lifecycle/log and preview/queue endpoints. E16 owns the ground receipt/reply endpoints
for both resource kinds (docs/tdd/E26.md:46). E15 owns episode association; E28 owns plan
association. Neither receipt nor reply invokes clinical transitions or recordReview.

## Design decisions

- **Seams and approval.** Snapshot selected authorized facts, source/time/origin labels, recipients,
  consent, attachments and catch_up before preview. Bind approval to the previewed revision and
  If-Match. Preserve those exact bytes and configuration pins; a later reading, plan edit or release
  requires a new preview and explicit approval. Journal/participation are never inputs. E29 enrichment
  cannot expand a previously approved payload (SYSTEM 5.7.1/8.1/8.3).
- **Time.** Retain the initial preview timeline instant as the visible creation time, even when the
  console reads it days later; do not add undeclared created_at JSON. Preserve underlying observation
  times and original dates. UTC, correction provenance and mission labels use SYSTEM 5.2/5.3.
  Contact and stated expiry instants are absolute; transit delay, retry and backoff durations are
  elapsed under SYSTEM 5.10, owned by E29. Unknown contact/delay stays unknown, never zero or now.
- **Durability and migration.** One local transaction commits snapshot/approval or ground action,
  command identity, reference-only audit, outbox and replication intent. No external call in that
  transaction. E29 transmits after commit. Use additive owned migrations and retain old readers;
  software rollback never rolls back records, consent or deduplication (SYSTEM 5.7/5.8/7.2).
- **Truth and fallback.** Distinguish preview, queue, in_transit, delivered and receipt_recorded using
  the exact Handoff enums. Local export is not delivery; simulator identity stays visible. No E29
  means local approved content remains queued and unavailable transport is visible. Primary delivery
  is E29's delayed queue; independent backup is its authorized file-drop transport. If both are absent,
  the safe state is retained local content and no delivery claim. Neither path gates onboard care.
- **Privacy and failures.** Recheck current recipient/object/field consent at preview, approval,
  materialization, send, import, read, event delivery and stored-result replay. Revocation fences unsent
  work and recipient streams; do not claim disconnected copies erased. Report attachments require
  separate owner-approved immutable versions via E09. Missing E09 disables attachments only. Ground
  sees only delivered approved projections, never live chart links. Fail storage/integrity errors
  loudly, retain accepted content after crash/disk full, and ignore old/duplicate lifecycle effects
  without dropping their history. An absent/incapacitated owner never blocks fixed help; it never
  authorizes extra sharing. Disagreeing readings remain distinct; missing samples remain missing.

Operational bounds reuse SYSTEM 5.11; C5 must settle the remaining complete-history response contract.
No new setting key or numerical default is allocated by this plan.

| Work | Existing settings, owned by E01 or E29 |
|---|---|
| Command/snapshot admission and repository reads | commands.max_bytes, storage.max_rows, storage.max_statement_bytes |
| Projection size/fields/dependencies | sync.max_bytes, sync.max_projection_fields, sync.max_dependencies |
| Event delivery and module discovery | events.page_size, event_bus.max_events, registry.max_modules, registry.max_ports, registry.max_hooks, registry.startup_timeout_ms |
| E29 transport/backlog/timeouts | bundle.max_bytes, bundle.max_inflight, bundle.retry_max, bundle.retry_backoff_ms, bundle.renewal_batch, sync.page_entries, sync.pending_max, sync.timeout_ms |

## End to end evidence

All scenarios live in T4's directory; T2/T3 additionally own browser specs in their directories.
Use fixtures/demo-mara-fd19 only through E21's explicit demo profile, with model/devices absent.
No mock replaces an internal hub service. External optional adapters may use only documented fakes.

| Trace | What the real stack must prove |
|---|---|
| E16-01, R19, V26, D13 | Create selected episode facts and a private journal canary through their APIs. Preview exact allowed fields, origins/times and separately consented attachments; approve unchanged revision. Canary, participation and excluded fields never reach payloads, ground HTTP/SSE, logs, caches or exports. Wrong subject/recipient, officer-only authority, revoked grants and station access fail. A configured non-safety label edit changes served/rendered content; invalid activation retains the last good release. |
| E16-02, R19, V29 | Queue during outage, restart before/after each commit and receipt, retry after cache expiry and concurrently. Exactly one approved snapshot and logical receiver record survive; same key on another target/body/parameter conflicts. Restore/contact recovery drains all approved content in order through E29, without recomposition. Local export, transmission, arrival and human receipt are distinct; every simulated event identifies its simulator. Missing E29 and missing bundle lifetime retain local content without claiming delivery. |
| E16-03, R19/R26, V95 | Authenticated ground reads immutable creation/source times, submits receipt and two distinct attributed replies. Close the actual plan before return delivery. Deliver replies out of order and duplicated, restart, then verify retained reply records attach to the original episode/handoff and alter no episode/plan/help step or warning. Wrong resource kind, source, recipient or hash cannot apply. Receipt is never a ClinicalReview. |
| R61, V68, D26, D42 | Real E26 help preview/press on Mars with link down; display delay/contact, keep guiding and log later ticks. Return contact sends exactly the approved text. E16 inbox/receipt/reply handles GroundMessage without taking E26's lifecycle ownership. Journal canary absent. |
| R48, EI1/EI2/EI5, V81, D40 | On the frozen demo's ICL10 run, prove stage order and ground stage last, preview/approve that particular run, queued leaves_at equals next contact, continue and close care. Compare real plan/help/warning/routine responses under up/delayed/down with the same clock and a planned gap or null silence threshold, allowing only exact contracted ground presentation leaves. Never mask care state, revisions, IDs or timestamps. Late reply changes no care state. Never infer a plan_run_id from episode_id. |
| AGENTS 1/6 invariants; R26 | Real fault controls exercise disk full/audit failure, corrupt/missing config, forward/backward clock jumps and mission boundary, concurrent approve/revoke, long backlog, missing E09/E29/model/devices and absent crew. No accepted record lost, no fabricated value, no cross-crew leakage. T4 supplies E21 verified capability evidence; E21 owns the nine-module manifest. |

V26 and D13 are handoff contributions; V68/D26/D42 and V81/D40 are complete cross-epic scenarios,
not passes inferred from isolated E16 unit tests. V95 also requires the real plan provider. No
threshold/step scan owned by another epic is claimed complete by E16's configuration-edit test.

## Design deviations

No E16 design sections or cold-review findings exist to override. Apply these known product/provider
conflicts in each brief; do not edit frozen designs:

| Conflicting source | Winning rule | What to build |
|---|---|---|
| Product brief dependency list (docs/product/tdd/E16.md:5) | SYSTEM 9 | E01/E02/E15 hard; E09/E29 optional with honest unavailable capability |
| CONTRACTS 5 cache-only identity (docs/product/CONTRACTS.md:210) | Part A D6, SYSTEM 5.8/8.6 | Mission-life full command identity and current result authorization |
| RFC 9457 versus closed errors | Part A D5, SYSTEM 5.5 | ErrorEnvelope on HTTP, sanitized RFC 9457 logging |
| Product timestamps/freshness, late delivery | Part A D3/D14, SYSTEM 5.2/8.3 | Preserve observed time/origin and snapshot time; arrival never makes old facts current |
| Old E26 computed/recomposed catch_up (docs/tdd/E26.md:194) | SYSTEM 8.1, docs/SYSTEM.md:1953 | Freeze preview/approval content; transport never alters approved text/hash |
| E28 ground run access (docs/tdd/E28.md:629) | Part A D9, SYSTEM 5.7.1/8.3 | Delivered approved snapshot fields only, no live mutable plan read |
| E15 ground review visibility (docs/tdd/E15.md:310) | SYSTEM 8.3 and D9 | No live-chart permission from ground role/consent alone; ground advice is a new record |
| Four product link readers versus import restriction | SYSTEM 6.4 plus coordinator C2 | No handoff import of groundlink.application.ports until the contradictory seam is settled |
| Local export and expired envelopes | CONTRACTS 4; Part A D11; DEPLOYMENT 4.6 | Export never means delivered; expiry never deletes approved local payload |
| HandoffCreate cannot identify a plan run | Closed request schema; E28 R28-5/24 | Do not infer or add fields locally; D40 remains blocked on C4 |

## Requests

Addressed to the coordinator. This run may edit only docs/build/E16/. Therefore C1 to C7 below
are concrete handoffs to file in docs/requests/E16.md, which is absent, not filed or accepted
requests. No task edits that canonical file. Consolidate C1/C2/C5/C6 with E29 P29-1/2/3/4 (docs/build/E29/PLAN.md), rather than defining competing protocols. Please file them before dispatch; pending proposals
must not enter contracts or the decision overlay without an accepted decision.

| ID / owners | Required resolution and acceptance-preserving interim |
|---|---|
| C1 / E01, E02, E15, E09, E26, E29 | Publish canonical, bounded, snapshot/session-bound HandoffQueries/GroundInboxView, EpisodeReader content projection, ReportQueries approved-attachment version reads, BundleOutbox/BundleInbox and E26 delivered-message reads. SYSTEM 4.2 names these but does not give their complete methods/types. E15's reader currently supplies subject/ref visibility only (docs/tdd/E15.md:413). Agree UoW ownership, typed delivery/receipt/reply callbacks, origin/subject/recipient/hash/revision checks and E15/E28 association, including current E02 field/purpose consent and result visibility. E16's /ground/messages receipt/reply must queue new ground records through E29; E29 invokes E26 GroundMessageLifecyclePort after return delay (docs/tdd/E26.md:470), never at button press. Specify minimal selected-facts/known-actions/unknown-watch-and-advice composition when E29 is absent. Clarify multiple/late reply ordering, receipt timestamps and ground-node replica storage within E16's two allocated tables. No invented adapters or direct table reads; dependent implementation/acceptance waits. |
| C2 / SYSTEM, E01, E29 | Resolve SYSTEM 4.2's E16 BundleOutbox consumption versus SYSTEM 6.4's prohibition on handoff importing groundlink.application.ports. Publish a separately importable command-only boundary and E29-owned presentation facade for leaves_at/contact/delay, or explicitly amend the import rule without permitting a fourth GroundLinkQuery reader. Pin catch-up/presentation inputs and preserve frozen content. E29 absent: durable local queue, unknown presentation, no delivery. No local simulator/retry scheduler in E16. |
| C3 / E03, content coordinator, E29 | Publish the sourced field-selection registry mapping HandoffCreate.fields to authorized episode/observation/gap content, labels, sensitive-default policy and purpose, using accepted configuration shapes. Supply lifecycle/transport/exclusion/button/ground-console words and title/text templates through ui-copy.json; its current schema has catch_up_words but no handoff vocabulary (docs/product/api/schemas/config-ui-copy.schema.json:1030). Agree E29 catch-up from selected facts only and unknown facts versus known empty activity. Do not copy example prose into code or introduce an unapproved document. Missing mapping leaves the affected preview unavailable, never an overbroad default selection. |
| C4 / package coordinator, E28, E15, E16 | Consolidate E28 R28-5/24 and P28-7 (docs/build/E28/PLAN.md:74): optional plan_run_id in HandoffCreate, exact typed HandoffGrantReader/GroundGrant, same-episode/subject/origin checks, immutable selected plan snapshot at preview/approval and E15 timeline/Records source. Add only after accepted contract propagation/codegen. Base request rejects undeclared plan_run_id; no association guessed from episode. D40 and full run catch-up wait. No grant exposes live plan data. |
| C5 / package coordinator, E01, E29 | Resolve bounded lifetime histories: Handoff.deliveries/timeline and GroundInbox.items are unpaged arrays with no maxima, while SYSTEM 5.11 requires bounded reads. Specify compatible paging/continuation and response byte limits with authorization; identify accepted bounds for fields/recipients/attachments/replies and immutable snapshot bytes. Do not silently truncate, drop accepted history or make an old handoff unreadable at a cap. Also resolve advertised cancel/retry actions with no corresponding handoff operations: no invented routes or misleading enabled button; unmet action acceptance stays blocked. Real transport names must not be inserted into the closed simulated_comms_queue/local_export enum locally. |
| C6 / E01, E03, E20, E21 | Provide generated effective contracts, kernel fakes and real SQLite fault controls; frozen shell feature/session/copy interfaces and Playwright discovery for feature-local specs; ui/ground workspace tooling and serving built assets at /ground/ through E20 appserve (docs/tdd/E20.md:615). Agree ground-node versus explicitly switched demo routing (SYSTEM 3; DEPLOYMENT 3.8/5.3). Register E16 scenario/requirement inventory, Schemathesis scope and safety/mutation thresholds in shared validate_hub/CI gates, without a new feature-owned runner. T3 supplies its own app build config, not root workspace edits. Gate commands below are implementation obligations, not currently runnable claims. |
| C7 / coordinator, E21 | Append this planning run to AI-USE-LOG.md using its existing columns: 2026-11-14; E16 build planner; Codex (model identifier not exposed); build planning; PLAN.md and four standalone task briefs; docs/build/E16/; independent senior review pending. Record subsequent coder/reviewer runs and attach E16 verified capability status to E21's R26 manifest. Root files are outside this run's ownership. |
