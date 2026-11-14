# E32 build plan

For independent review. No implementation is authorized by this planning run. The frozen design is unchanged.
`tasks.json` contains four standalone assignments with disjoint ownership from design section 13
(`docs/tdd/E32.md:857`). All execution and acceptance evidence remains planned.

The inspected tree has the engine, its schemas, configuration and fixture, but no `hub/`, `ui/`, `eval/`,
`contracts/api/`, hub configuration documents or `docs/requests/E32.md`. The medicines research report named by
the design is also absent. E29's design is also absent in the current tree; its exporter remains a provider prerequisite. Existing epic task inventories were inspected; none claims the paths assigned here.
Recheck inventories before dispatch because other planners are writing concurrently.

## Build order and connections

| Task | Concern | Start and acceptance dependencies |
|---|---|---|
| T1 | Supplies ledger, cover and pure planning contribution, with backend acceptance | Start on E01 protocols/fakes after requests B01 to B04 settle declarations. Real acceptance requires E01/E02/E23/E27, plus real E24/E26/E28 for their scenarios. |
| T2 | Medicines decision support and durable assessment adapter, with backend acceptance | Independent of T1. One author owns this safety concern. E01 protocols permit pure work; B02/B04/B06/B07 block affected integration. Never enable a proposed contract. |
| T3 | Supplies components and assembled E32-05 acceptance | Dispatch after T1; uses generated contracts and provider UI from E03/E20 and B05. |
| T4 | Medicines views and browser acceptance | Dispatch after T2 and B06; uses generated contracts and provider UI from E03/E20. |

T1 and T2 do not import each other's implementation. T1 publishes the canonical E24 facts, implements E27's
planning protocol and exposes E28's stock/ledger ports. E27 owns the what-if endpoint, card shell and planning
toggles; E28 owns plan execution; E31 owns trained-hand counts and authority. T3 tests the assembled result without
editing those providers. T2 calls the existing public pharmacy pipeline only from its adapters. It uses E01 tasks,
transactions, command receipts and clock corrections. T4 renders the resulting authorized views.

The minimal code excludes another queue, transaction wrapper, clock detector, deduplication store, rule engine,
ground transport, formulary framework or assistant tool. Provider absence is unknown or unavailable, never sample
data. Missing prerequisite evidence blocks acceptance rather than removing a requirement. Dependency lists in JSON
are internal task edges; provider and request dependencies above and in each brief also apply.

## End to end evidence

Backend tests stay in `hub/tests/supplies/` and `hub/tests/medicines/`; browser tests stay inside each owned feature
test directory. Use the real HTTP API through `create_app(settings)`, real authentication/configuration/composition
and temporary real SQLite, or Playwright against that running hub. No mocks, dependency overrides or fake repositories
inside the hub. Kernel fakes are for unit/port development only. External optional parts are absent or use their
documented adapter fakes. Use E21's explicit demo profile and fixture once available, without copying its runner.

| Owner and trace | What the real-stack test proves |
|---|---|
| T1, V91, R57/R58, UC25, E32-T-07/08/09/15/17/19 | A real plan action consumes pinned needs, lowers cover and raises E24's warning. Insufficient stock refuses the entire action, preserves the step and displays the substitute. Activation, concurrent last uses, crash, disk full and retries past 24 hours preserve state/audit/events/receipts together. |
| T1, V92, R59, E32-T-20/24/25 | E27's endpoint combines an instrument, supply and training change in one snapshot. Counts, officer dependencies and cover change; database, command ledger and event state do not. Concurrent activation/use cannot mix snapshots. |
| T1/T3, V75/V98, E32-T-30/40 | Scratch configuration edits to content, a sourced threshold and step applicability reach the actual API and screen without code edits; environment changes update the view. Scan the owned client files as well. E21 retains the complete cross-product scan. |
| T1/T3, V81/D34, V83 supply contribution, E32-T-12/13/42 | With model/devices/link absent, supply reads and accepted uses work, unavailable needs show substitutes and cached help remains accessible. Offline refused help uses retain one reconciliation record. B08 is required for approved ground-log completeness. Other epics retain the full V81/V83/D34 assertions. |
| T3, D32, E32-T-43/46/47/48 | Plain crew sees API-derived supplies and trained-hand counts, plan summaries and ground-purpose text. Card, kit view and E21's result panel agree on the frozen count. Events during fetch, reconnect and expired cursors cannot restore stale numbers. |
| T2/T4, proposed V99, E32-T-50/52/54/58 | A human diagnosis leads to a traced proposal and authorized confirmation; exactly one administration and movement survives retry/crash. Changing the lot requires a new proposal; competing proposals recheck current allowance atomically. |
| T2/T4, proposed V100/V101, E32-T-55/56/69/74 | Compensation caps at every remaining ingredient limit and states shortfall. Recalled, adversely inspected, expired and known do_not_use lots remain excluded with the worker stopped. Declared coverage differs from missing sensed history. |
| T2/T4, proposed V102, E32-T-61/62/75 | Current authority and object/field grants both apply. Acting confirmation follows Q14, with assignment recorded; acting allergy/interaction overrides fail. Unauthorized IDs, events, saved results and browser caches reveal no clinical fields. |
| T2/T4, proposed V103, E32-T-59/63/64/70/71/76 | Model/link/device absence preserves the human flow. Worker timeout/cancellation/restart cannot publish a stale generation. Stored artifacts replay through the real pipeline. Clock corrections cannot revive proposals or satisfy intervals early; load never blocks emergency entry or manual capture. |
| T2/T4, proposed V104, E32-T-51/78 as superseded | The same eligible adjust lot compensates in LIVE, REPLAY and SIM without external authorization, unless the person explicitly requests the labeled path or a retained engine/product gate refuses. No authorization-entry fixture is required. |

V99 to V104 and R63 are proposals in `docs/tdd/E32.md:928`, absent from the living package. Preserve their intended
proofs with Q11/Q14 replacements, but do not report those IDs as accepted until B06 propagates them. E21 owns scenario
registration and demo fixture changes, not these test bodies (B08). Every test carries its requirement/invariant and
E32-T identifier; no missing counterpart is a skip or xfail.

## Design deviations

| Frozen design section | Winning rule | What to build |
|---|---|---|
| 2, 4.1, 5.1/5.2, 6.1 | SYSTEM 5.1 to 5.12, 6.2/6.4; D5/D6/D7/D12 | Exact injected kernel interfaces, generated effective models, module discovery and generated import rules. No `begin(immediate=True)`, `HubSettings.get`, feature command ledger, SQL transaction control or raw connection. Full operation/target/parameters/body identity precedes mutable business checks. |
| 3.1/4.1, test 16 | SYSTEM 5.8; D6 | No per-mission command-key namespace. Reusing an actor/key with a changed mission/origin parameter conflicts; a new mission action needs a new intent/key. Authorized identical late retry returns the original result or receipt. |
| 2, 3.3, 4.3/4.5, tests 08/16 | SYSTEM 8.1 at docs/SYSTEM.md:1881; D3/D13 | `SupplyPartition`, extended `CoverFact`, paged `SupplyCoverFacts.rows` and `SupplyCoverChanged`, not retired `SupplyUseRecorded`. Revisions, corrected observation time, all changed rows and provenance survive activation/recovery. Typed computation failure commits capture plus pending evaluation; storage/integrity failure aborts. |
| 2 and 4.3 | SYSTEM 4.4/6.4/8.1; docs/tdd/E27.md:256 | Import E27's application protocol and implement `cover_changed(ctx, *, add_supplies)`. Use its snapshot/release/stock/time with `with_added`. No local copy of the old environment-only protocol. |
| 2, 7, 9, 14 | SYSTEM 4.4/8.4/9 | `MissionStatePort` from E23, help context from E26, profile facts from E03, `ClinicalAuthorityQuery` from E31. E01/E02/E23/E27 are hard E32 dependencies; other integrations remain optional except for their named acceptance tests. |
| Header, DD10, 3.6, 8.4/8.5, HZ17, test 78 | Part A Q11, docs/requests/COORDINATOR-DECISIONS.md:52 | Compensation in all origins, no live authorization document field/check M-L9, no external approval gate. Preserve validated range, coverage, degradants, device/product exclusions and every final-amount limit. Replace the old LIVE authorization test. |
| Header, 3.7, M-L8, tests 61/62 | Part A Q14; SYSTEM 8.4 | Acting officer can confirm the recommended compensated dose when trusted current product/training or active plan/condition context permits. No acting allergy/interaction override. Do not force a new labeled proposal solely because the officer is acting. |
| 3.5, M-L6, test 60 | SYSTEM 5.6/8.2; Part A Q1/Q7/Q18 | Missing clinical sign-off remains a visible review status, not a general runtime review prerequisite. Retain source, purpose, applicability, simulation restrictions and all dose gates. B06 must make the exact medicines replacement executable before enabling it. |
| 3.8, 5.2/5.3, tests 56/70/71 | SYSTEM 5.2/5.10/5.10.1 | Shared correction-aware TimeInput, elapsed/absolute deadlines and task generations. Proposal terminal expiry on restart remains; no private clock history or restart queue. Unknown time refuses time-dependent proposals; never automatic clearing by time catch-up. |
| 3.4, 4.4, 5.2 | SYSTEM 4.1/4.2/5.7/5.8 | Module-prefixed tables and shared receipts replace unprefixed SQL and feature dedup columns. Exact schema mapping is B02, not a coder guess. Domain assessment history is not a second task runner. |
| 2/5.3, review finding 16 | Existing public engine boundary, pharmacy/config.py:73 and pharmacy/pipeline.py:1734 | canonical_json_bytes comes from pharmacy.config, not pharmacy.types. Map active-measurand estimates by ingredient and retain coverage/degradant provenance; do not copy the engine. |
| 3.4, 4.4, test 68 | SYSTEM 7.3; docs/BUILD-CHARTER.md:48 | FHIR R4 4.0.1 through E29's exporter and sourced export mapping. No R5 export or parallel E32 exporter. Keep the export unavailable until B07 supplies the mapping and port. |
| 10.2 and test 75 | SYSTEM 8.3/8.4; D9 | Roles and accepted duties never grant a chart. Every clinical operation, event, task result, replay and export checks current object and field grants; use UNAUTHORIZED_SCOPE for denied access. No officer-wide events or station clinical view. |
| 9/14 | SYSTEM 8.5/8.5.1; DEPLOYMENT 4.1/4.4 | Local site custody and ownership fencing, not environment-only multi-writer stock. One local location until R-SYS-6 lands; transfer remains unavailable without its complete registered snapshot and epoch checks. |
| 13/15 | SYSTEM 5.12; review r1 findings 8/20/21/23 | Registration, complete owned acceptance and all gates are mandatory. Missing supplies registration ownership, schemas and research sources block affected work through B01/B05/B06/B09, never an invented file or reduced acceptance. |

Review r1's 24 findings have answers in `docs/tdd/reviews/E32.response-r1.md:3`; those answers are claims to verify,
not a greenlight. Carry all their regression tests. Findings 4/23 still need executable seam/schema propagation;
8 needs assembled providers; 20/21 need shared bounds, fault evidence and ground reconciliation. The table above
also replaces superseded answers 4/17/18/24 and the old decision paragraph. Do not start another design review loop.

## Requests

Addressed to the coordinator. These are handoffs, not filed requests: `docs/requests/E32.md` is absent, and this run
may edit only `docs/build/E32/`. File them there before dependent code, preserving the existing R-E32-01 to R-E32-28
proposals from design section 14 and applying settled Part A decisions rather than asking them again.

| ID | Owner and concrete action | Blocks |
|---|---|---|
| B01 | Coordinator/E01: allocate `hub/src/crew_health/supplies/module.py` under the frozen ownership exception. SYSTEM 5.12 requires it but E32 section 13 omits it. Approve the exact registration/setting/projection files needed before revising task ownership. No task currently claims that file. | T1 real composition |
| B02 | Coordinator/E01: publish exact module-prefixed replacements for E32 section 5 SQL, unit registration and immutable artifact/prune tables, including timestamps, custody revision and replication declarations. Bind step-use identity to E28 occurrence and the shared parent receipt. The design's supply_unit and med_* tables and per-mission API dedup key cannot be implemented literally. | T1/T2 storage |
| B03 | E27/E28/E24/E32: settle R28-20's occurrence-bearing ledger call and SupplyCoverPort against E32's older signature; publish canonical SupplyNeed/StockLevel ownership and snapshot binding. E24 adopts SYSTEM 8.1 exactly. Define the remaining StockFacts provider protocol for SYSTEM 8.2 and the bound for changed-row batches. No duplicated type or newly guessed protocol. | T1 seams, V91/V92 |
| B04 | E01/E02/E23/E26/E28/E31: provide kernel.testing and current typed boundary DTOs, snapshot-bound stock/context/weight/history readers and role checks. Resolve HelpContext, RunContext, ObservationReader.accepted and SeriesSlice mission/time/quality provenance without opening a second snapshot in confirmation. Publish settings limits for every E32 section 11 descriptor and transactional refused-use evidence under SYSTEM 5.8/5.9, retaining client time without clinical log values. | T1/T2 provider integration |
| B05 | E27/E31/E03/E20: implement R-E32-04, R27-3, R-E32-09/28; E27 applies the four supply keys, retaining unreferenced inventory and provenance. Allocate authoring/supplies/kit-map-supplies.json before anyone writes it. Supply toggle quantity comes from configuration or explicit input. Bind feature slots, generated copy/client APIs, owner queue and Playwright discovery of owned feature test directories. No E32 task edits provider components or shell. | T3, E32-01/05, D32 |
| B06 | Coordinator/package/E01: publish complete medicines schemas/operations/documents/source registry and guarded generation for R-E32-10 to -14/-27, with Q11/Q14 corrections and review-status behavior above. Complete revision/ETag/errors/role/nullability bodies, active-plan medicine/condition linkage, ClinicalAuthorityQuery binding and clinical operation grants. Explicitly settle manual-administration safety checks so it cannot bypass dose/stock/exclusion invariants. No custom wire models, invented rules or mounted proposed routes. | T2/T4 wire/configuration |
| B07 | E29/E01: register sourced R4 medication mapping and an exact exporter port under SYSTEM 7.3. Medicines stay locally usable with export unavailable. No medicine replica projection or ground access before an explicit approved projection/grant. | T2 export acceptance |
| B08 | E21/E29/E26: register V91/V92 and later accepted V99 to V104 against E32-owned test bodies; supply the explicit SIM threshold fixture (Q19). Implement R-E32-17/21/26 for durable refused-use reconciliation and consent-filtered approved ground lines after outages, without blocking help or relabeling a replay into a different environment. | Full scenario/ground evidence |
| B09 | Coordinator: recover the missing medicines DESIGN-BRIEF.md and research/dosing-safety.md named at docs/tdd/E32.md:7, or supply reviewed source replacements and independent golden expectations. Extend E01's existing coverage --report validator to select the complete E32 per-file measured-source inventory and wire condition/complexity/mutation inventory to both E32 modules, and feature test discovery to actual toolchain. scripts/validate_contracts.py currently validates the engine only; it cannot stand for hub validation. Record AI-use rows for this run and later coders in AI-USE-LOG.md. | Golden medicine tests, final gates |

All task gates are exact prospective commands from SYSTEM 6 and the opened E01/E03/E32 gate inventories. Their
projects/providers do not exist yet. At hand-in run task suites, the full hub/UI suites after fixes, effective contract
validation, per-file 95% branch and full safety condition/complexity/mutation gates, then independent build review
and CI. Missing tooling, prerequisites, failing tests and open findings remain failures. Planning verification is JSON,
ownership, source-reference and writing-rule inspection only; no product gate was run or claimed green.
