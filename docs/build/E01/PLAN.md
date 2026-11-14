# E01 build plan

Ready for senior review, not implementation greenlight. Only docs/build/E01/ is changed; the design stays frozen.
The six standalone briefs in tasks.json contain the exact owned files, source sections, tests, prerequisites and
gates. The path groups below abbreviate that authority. No product code was written.

## Build order and connections

| Task | Owned concern and path groups | Starts after |
|---|---|---|
| T1, foundation | hub/pyproject.toml; root package.json/pnpm-workspace.yaml; kernel/testing; app registry/import generator; guarded base contracts/reference copies; contracts_gen/contracts-ts generation; kernel/SHA tests | Accepted generation/trust allocations below |
| T2 | platform durability, private coordination, replication/ownership, scheduler/tasks/evaluation/health; events; their tests | T1 |
| T3 | config loader/activation/API and tests | T1 |
| T4 | clock segments/corrections/mission time and tests | T1 |
| T5 | records observations/answers/API and tests | T1; approved policy/record/auth seams for positive acceptance |
| T6 | app assembly/middleware/errors/server entry; platform CLI/Jobs boundary; validate_hub.py; app/contracts tests; two E01 scenario files | T2, T3, T4, T5 and the real counterparts required by each scenario |

T1 contains no persistence, loader, live clock, feature scaffold or server. Its guarded generation is the necessary
input to kernel's contracts_gen.enums import, using one permanent pipeline rather than bootstrap copies. Trust and
D12 artifact prerequisites must be supplied before its gates; E20's completed supervisor is not a prerequisite.
Other epics start coding against the foundation immediately when T1 passes, using SYSTEM 10 kernel fakes.

T2 through T5 code concurrently against those protocols. T6 supplies concrete cross-task integration; each owner
fixes its own implementation. E02 supplies identity/consent/vault; E03/E09 own private writers; T2 alone coordinates
their transactions. T3 supplies release pins, T4 corrected time, T5 records and same-UoW AnswerAccepted, T2 committed
events. T6 assembles them through SYSTEM 5.12. Component fakes never count as production fallbacks or E2E evidence.

Sorted module.py discovery and generated import contracts extend without E01 edits (docs/SYSTEM.md:1552,
docs/SYSTEM.md:1677). Module-owned architecture.toml, replica.schema.json, checks and settings add constraints.
Handwritten paths stay within docs/tdd/E01.md:666; generated roots allow only manifest-derived leaves and the
explicitly listed build/package files. No other epic edits hub/pyproject.toml to register a module.

## End-to-end proofs

T6 owns eval/scenarios/E01/test_recovery.py and test_acceptance.py. Each journey starts the real composed hub process
on temporary real SQLite, drives loopback HTTP and uses Playwright for UI clauses. No mocked hub service, dependency
override, direct SQL seed or fake hub clock earns E2E. Optional external workers may be absent or their documented
provider adapters; LIVE clauses still require LIVE acquisition. Inputs use E21's explicit fixture/profile. Every
call, process and wait is bounded. Missing providers/clauses fail acceptance, never skip or xfail.

| Trace / critical path | Owning file and required journey |
|---|---|
| E01-01/R25/V16; T-E01-03/16/17/20 | test_recovery.py: authenticated manual capture, kill at each write/ACK boundary, restart/read, identical late retry once and mismatched target conflict. Separate page-limit/ENOSPC, supported upgrade/new acceptance/previous-code rollback, paired interrupted replacement restore, current revocations and new incarnation. E02/E20 and sourced manual policy required. |
| E01-02/R01/V09; T-E01-05/20 | test_acceptance.py: separate LIVE presenter/guest from SIM persona; positive narrow sharing and cross-crew/partial/revoked/station/ungiven-officer denial. Verified incapacitated-subject manual capture remains independent of E05/model/devices/ground. E02/E23; baseline proof needs E14. |
| R06/V15; T-E01-06/20 | test_acceptance.py: unsupported/ambiguous unit cannot become accepted data; corrected valid entry succeeds. Forged producer quality fails; conflicting real readings remain separate. E02/E05 and approved R-E01-1/17/18 policy/proof. |
| E01-03/V16/V35; T-E01-03/11/20 | test_recovery.py: defined config/job command has one result/event after lost ACK; restart/UI cursor crosses invisible pages to authorized immutable event. Revoked replay denied; subscriber repair cannot skip. E03/E20. Observation-event clause remains open under R-E01-22. |
| R25/V35; T-E01-19 | test_recovery.py: actual E03 incomplete check and E05 request/attempt/manual-link survive commit/refresh faults and retry with original identities, no unlinked accepted reading or repeated unknown acquisition. Task/evaluation recovery preserves generation. |
| V46; T-E01-12/19 | test_recovery.py: real checks at 23:58/00:03 count consecutive days once; duplicate day counts once. Authorized mission-clock correction and E22 rebuild audit moved days without double count. Relative deadlines move, absolute ones do not. Requires real E03/E22/E23 and agreed local time controls. |
| V47; T-E01-05/19 | test_acceptance.py: LIVE demo-operator reading/guest check leave SIM history/views unchanged, with Sim swatch; REPLAY contributes no completion. Real E03/E22/E23/E21, not origin-tag-only assertions. |
| V75/configuration; T-E01-08/09/20 | test_acceptance.py: sourced scratch threshold plus step edit, activate, switch environment, observe all changes in real UI, run actual E21 client scan. Invalid/missing contributor rejects in every mode; D8 race, D1/pins/stock preserved. E03/E21/E23/E24/E26 and every loader contributor. |
| D02/R06; T-E01-07/20 | test_acceptance.py: E08 estimate and E03 confirmation/correction are separate linked Observation/Answer records; immutable estimate survives retry. Unknown/declined are distinct, completion independent. Missing E08 design cannot be replaced by manually seeded success. |
| D09/V09/V47; T-E01-05/20 | test_acceptance.py: accepted LIVE selected camera pulse records source/choice/disagreement on demo-operator; Mara baseline/streak/goal unchanged. Actual E05/E06/E14/E22/E21 required; SIM camera cannot earn LIVE. |
| Private durability/privacy; T-E01-04/14/17/20 | test_recovery.py: owner journal/report, denied other readers; seal/reference/receipt/delete/backup faults; old restore keeps deleted A unreadable and retained B readable. Check actual logs/errors/events/jobs/replica/export canary exclusions. E02/E03/E09/E20 and exit owners; an absent surface proves nothing. |
| Capture/evaluation; T-E01-02/07/10/20 | test_acceptance.py: actual E03/E04 answer append into E11/E24/E15; typed computation fault preserves input/pending/warning, forbidden storage/I/O/auth/config/input faults abort all. Older recovery cannot overwrite newer warning. Model/devices/link absent. |
| Emergency/manual fallback; T-E01-15/20 | test_acceptance.py: minimal laptop capture/help with optional parts absent; then storage fault, Python writer stall and queue/SSE saturation. No false acceptance; actual cached/printed help meets charter budget. E03/E20/E26/E27-guidance, verified cache and R-E01-21 watchdog. |
| Sync/ownership; T-E01-18 | test_recovery.py: outage/backlog, current consent and independent revocation lane, no excluded IDs/hashes/counts; every transfer-phase crash permits at most one writer/timer owner. Current owner alone applies remote routine fact. Agreed file/CLI exchange plus E20/E25/E29/E32, never invented sync HTTP. |

Every E01 critical path and V/D has a collected journey. Counterpart clauses in docs/tdd/E01.md:648 remain mandatory.
Hardware/secure-erasure/offline-install evidence is separate from hermetic software verification. Numerical
verification never implies clinical validation or mission acceptance.

## Design deviations

SYSTEM/Part A win, then unresolved cold-review blockers/majors. Previously fixed findings remain regressions.

| Design / open review point | Winning rule and build instruction |
|---|---|
| E01 13/15 order | User's early foundation plus SYSTEM 6.2: T1 includes required guarded type inputs; standalone hub until engine owner applies R-SYS-10. No root edit. |
| E01 13; C1 major 7 | D12/SYSTEM 6.5 require accepted signed artifacts; R-E01-20/28 allocation remains pending. T1/T3 cannot silently own proposed overlay/unit-map/signature paths. |
| E01 2/4; C1 blocker 2/major 4, R1 major 12 | SYSTEM 4.2/5.7/5.10.2: records publishes AnswerAccepted in caller UoW for every producer. R-E01-17 settles complete DTO/query/proof; T6 exercises real consumers. |
| E01 2/8; C1 major 3 | SYSTEM 6.4: release-bound TextRulesPort agreement/content under R-E01-27 precedes generated prose. Consumers cannot import config.domain or guess no-match. |
| E01 3/6; R1 blockers 5/6 | SYSTEM 8.3/8.4/D9 plus E02/E05 policy: full quality/partition/episode/field validation, no role-based chart grant or body trust flag. R-E01-1/17/18/25 block unsupported acceptance. |
| E01 4/6; C1 blocker 1 | Effective operation security preserves public health/sign-in; protected operations remain gated. Multipart and both ledger exceptions must pass assembled middleware. |
| E01 5/9; R1 blocker 3, majors 14/15/17 | SYSTEM 5.8.1/7 and DEPLOYMENT 4.2.1/7 govern recovery. Missing R-E01-19/20/21/24 archive/maintenance/trust/watchdog/compatibility bindings cannot be replaced locally. |
| E01 4/6/8 event/API gaps | Closed CONTRACTS 18 forbids invented observation event; R-E01-22 remains open. R-E01-10's explicit rejected-proposal interim and shared Config-Release rejection under R-E01-23 retain checks, not final package approval. |
| E01 15; C1 majors 5/6 | Exact per-file coverage and complete artifact/contributor corpus, measured before validation. T6 trace/safety/mutation must fail missing counterpart clauses; adapters cannot dilute coverage. |
| E01 12 provider assumptions | Actual tree wins: E05 design exists; detailed E06/E08/E09/E14/E16/E29 designs are absent. Product briefs/fakes are not provider agreement. Return readiness remains E33. |
| E01 13 unallocated script/manifests/locks | AGENTS 1.9: request allocation, never claim unowned paths. scripts/gates.sh remains a mandatory final gate. E20 retains shell/CI ownership. |

## Requests

Addressed to the coordinator. Existing R-E01 requests are canonical in docs/requests/E01.md. New proposals below
must be filed there by its owner: this assignment permits edits only under docs/build/E01/. This plan is not an
accepted decision or a claim of canonical filing. Disabled features never satisfy acceptance.

| Request / owner | Action and safe interim |
|---|---|
| R-E01-1/17/18/25; record/auth/producer consumers | Publish sourced observation policy and exact answer/query/proof/field/routing/revocation bindings, propagate consumers and recovery handoffs. Unsupported acceptance denies; preserve manual/emergency paths. |
| R-E01-19/20/21/24/28; coordinator/E20/E02 | Settle paired envelope, exclusive maintenance/pristine identity, signer manifests, callback watchdog and schema compatibility. Allocate proposed decision-overlay/unit-mapping outputs. Preserve verified last-good/current keys/receipts; unverified enablement stays off. D12 is settled, pending proposals excluded. |
| R-E01-22/10/23; package/skeleton owners | Decide observation event/assertion, rejected-proposal response and configuration-mismatch propagation. Keep defined events and durable validated capture without invented wire fields. |
| R-E01-27/2/3/15; config/E03/schema owners | Resolve text port/terms, basis copy, MET extension and existing R-SYS-9 health propagation. Unsupported prose/fields/wording stay unavailable; no guessed unit/time/content. |
| R-SYS-10/22, R-E01-26; engine/gate owner | Add root workspace and fix shared-document/decision-versus-demo lint with regressions. Standalone hub is the workspace interim; no lint waiver or document renumbering. |
| Provider/fixture owners | Deliver actual missing designs/modules/configuration, every loader contributor, approved external adapters/LIVE acquisition and E21 fixture. Missing provider work remains a prerequisite, not an E01 copy. |
| New allocation; coordinator/engine/E20/gate owner | Allocate scripts/gates.sh to T6: bash scripts/gates.sh is the single fail-fast entry later epics/CI call for generated/import checks, discovered suites, strict types/lints, corpus/trace/coverage/safety/mutation/E2E. Invoke underlying commands once, never recursively execute its task gate list. Allocate hub/uv.lock, pnpm-lock.yaml and signed build-catalog/output-manifest paths/verification bindings before creation. Root uv.lock/pyproject remain engine-owned; E20 edits CI. Until allocation, validate_hub.py can proceed but full script/workspace delivery cannot pass. |
| New shared interfaces; coordinator/E20/E23/E10/E21/E01 | Publish exact CLI extension (consolidate R-E20-3), create_app settings/bootstrap/local clock/fault controls, installed verified source/condition/form mapping and signed build-manifest binding (consolidate R-E01-19/20/28). Define module-owned validator coverage/trace/threshold registration for other epics without E01 edits. No guessed endpoint, fake hub clock or runtime product-tree source fallback; dependent acceptance stays blocked. |

Coordinator AI-use row: 2026-11-14; Codex; E01 build planning; docs/build/E01/PLAN.md and tasks.json; verification
below. AI-USE-LOG.md and canonical new-request filing are outside this assignment's write scope.

## Gates and verification

Exact commands are in tasks.json, using uv --project hub and pnpm. Each task runs its tests, ruff/format,
mypy --strict and generated import-linter contracts. T6 requires T-E01-01 through T-E01-20 and every work-package/R/V/D
clause, full safety condition coverage, complexity <=15, >=95 percent per executable domain/application file with
explicit branchless handling, and 100 percent killed non-equivalent mutants. Independent evidence disposes of
equivalence. Fixes rerun full owned/integrated suites. Missing CI/review/providers/tests is not success; build review
caps at three rounds, then the coordinator decides blockers.

Planning checks cover JSON/DAG, exact design ownership, pairwise/cross-epic overlap, existing reads, citations,
selected tests and punctuation. Runtime/product code and implementation/browser/fault/coverage/mutation/CI gates
were not run. Recheck concurrent epic task files before dispatch.

Read-only checks run: .venv/bin/python -B scripts/design_lint.py docs/tdd/E01.md --json passed with []. The same
command for docs/SYSTEM.md docs/DEPLOYMENT.md failed with 18 errors and 37 warnings, matching pending R-SYS-22's
template/internal-enum defect. That failure is not waived and prevents greenlight. No git command was run.
Plan audit rule: validate reads as actual path tokens before dispatch; an absent provider design is a blocked input,
never evidence its proposed interface was read or agreed.
