# E21 build plan

The design is greenlit. Its cold review has zero blockers and four majors, settled by the implementation instructions below and in `tasks.json`; no second design review is requested (`docs/tdd/reviews/E21.astra-c1.md:1`). This is a build assignment, not implementation acceptance.

## Starting point and boundaries

At planning time, `hub/`, `ui/`, `eval/`, generated API contracts and hub configuration do not exist. Existing `contracts/`, `config/` and `fixtures/` contain pharmacy assets only. The root Python project belongs to that engine. Other build plans are being written concurrently. The ownership checks include the current E02, E03, E05, E07, E08, E09, E10, E14 and E15 task files; none intersects these task paths. Empty blocked plans confer no implementation ownership. Recheck ownership immediately before dispatch.

All task paths are exact files from `docs/tdd/E21.md:661`. E18 owns benchmark execution and `eval/harness/`; E21 owns `eval/runner/` and the demo fixture (`docs/SYSTEM.md:137`). No task edits the hub, UI, pharmacy, provider tests, shared scripts, design, requests or AI-use log. Provider failures return to their owners. E05 now has a design; use it without assuming its unresolved acquisition contracts are approved (`docs/tdd/E05.md:99`).

## Build order and connections

| Task | Concern | Starts after | Connection and completion evidence |
|---|---|---|---|
| T1 | Verdicts, clause registry and runner package | E01 protocols/fakes and R-E21-1 agreements for enabled interfaces | Owns schemas, pytest collection and all required provider bindings. T-E21-01/12 prove complete accounting and configuration-driven content. |
| T2 | Isolated runtime and HTTP/event evidence | T1; E01/E02/E20 composition and R-E21-2/3 | Owns launch, generated client, invocation recorder, orchestration and CLI dispatch. T-E21-03/05/06 prove fencing, contract fidelity and evidence completeness. |
| T3 | Frozen fixture and seed commands | T1; accepted bootstrap, freeze and provider schemas | Owns seed adapter and all fixture files. T-E21-02/04 prove unique executions, pinned inputs, full start state and reproducibility. Runs in parallel with T2. |
| T4 | Cross-module and provider acceptance | T2, T3; actual providers per clause | Owns SC-01 to SC-10, W1/W2 and real-stack V01 to V98 clauses. Runs in parallel with T5. |
| T5 | Six-act browser demonstration | T2, T3; actual screens/providers per clause | Owns UI driver and D01 to D44. Proves served UI, API state, source modes and fallback behavior agree. |
| T6 | Evidence, claims and presentation | T1 for pure/report work; accepted evidence store and provider exports for integration | Owns manifest, scanner, reporting and presentation sources. T-E21-07/08/09/12 prove compatible evidence, truthful claims and executable branch coverage. Can build alongside T2/T3; final acceptance waits for T4/T5 outputs. |

`depends_on` records code dependencies between tasks. External and evidence dependencies in each brief are equally binding. Start independent pure work as soon as its inputs exist; do not wait for a whole optional epic. Unavailable shared interfaces stay unimplemented. Use the provider protocol and E01 `kernel.testing` for unit tests once supplied, never create a replacement kernel fake or infer a missing DTO. Fakes inside the hub cannot earn end-to-end acceptance (`docs/SYSTEM.md:2388`).

Avoid a fixture/bootstrap cycle: E02's implemented identity/consent/vault and agreed provisioning boundary enable E21's isolated rig; E02's later acceptance task may consume the finished E21 fixture. Likewise use E03's implemented shell/checks/views before its fixture-dependent final acceptance. Provider implementation readiness and whole-epic acceptance are distinct. Do not require a provider's E21-dependent acceptance to finish before supplying its fixture.

The runtime reads T1's validated registry and T3's frozen inputs, drives the real stack, and hands clause outcomes to T6. T1 validates actual collected provider nodes; T4 owns additional HTTP/browser evidence. T5 drives six acts using the same runtime. T6 consumes their evidence without rerunning product logic. CLI ownership stays with T2: it wires T3 fixture verification and T6 scanning/report checking only after their callable contracts are agreed; those authors never edit `cli.py`.

## End-to-end evidence

Every acceptance run uses the shipped composition root `create_app(settings)`, a new temporary real SQLite data directory, real authentication/configuration/storage/domain modules and generated HTTP models. Browser cases use Playwright against that running hub and the actual served crew UI. No dependency overrides, fake repositories, direct SQL seeds or mocked hub HTTP responses count. Optional external workers are absent or use their documented adapters. Unit-test fakes are separate evidence.

| Tests | What the real stack must prove |
|---|---|
| SC-01, SC-10, W1.6 | Fixed help within 500 ms with model stopped and under load; remove hub/auth/storage and use signed cached/static entry and configured printed backup. Combined model/device/link loss retains capture, warnings and routine. |
| SC-02 | Model cannot raise, clear or lower safety warnings; acknowledgment and release withdrawal do not clear pinned warnings. |
| SC-03 | LIVE/SIM observation-time freshness and advancing REPLAY windows survive stop, loop, restart and clock correction; manual fallback preserves disagreements. |
| SC-04, W1.2/W1.3/W1.4 | Accepted commands survive process death, disk full, retries beyond the cache and paired restore; target/body conflicts, concurrent edits and current replay authorization remain enforced. |
| SC-05 | Cross-crew object/field grants govern HTTP, events, replay, cache, files, export, replication and ground; private deletion survives old-backup restore. Positive permitted reads accompany denials. |
| SC-06 | Unauthorized tools and fabricated measurements never execute; failed acquisition stays failed; missing equipment yields a gap/manual path, with Quantity validation and changed-lot recomputation. |
| SC-07 | Clock jumps, restart, concurrent callbacks and ownership transfer preserve routine facts and misses without invented credit. |
| SC-08 | Valid threshold, step and label changes take effect without code changes; corrupt candidates preserve last good content, open work pins and nonnegative stock. |
| SC-09 | Long outages retain approved snapshots; renewal, consent, duplicate receipts and file fallback produce a complete ground log without blocking care. |
| V01 to V98 | T1's provider/test matrix plus T4's real-stack clause for every scenario. Provider unit evidence alone cannot satisfy an alias. Hardware/model-specific evidence remains explicitly outstanding until executed. |
| D01 to D44 | T5 implements every assertion in VALIDATION section 3, including LIVE presenter separation, six-act start state, UI/API counts, negative event assertions and continued care during the link outage. |
| W1.1 to W2.4 | The exact checkpoint path in SYSTEM 8.8, including real model then stopped, actual camera success/refusal, private journal and daily completion. |

The registry retains every clause, including unavailable ones. Missing counterparts, lost evidence, failures and unrun hardware cases prevent full acceptance. No skip, xfail, empty selection or stub run stands in for the required clean live demo. Each scenario records its collected nodes and execution mode; each provider test remains in its owner's files.

Scoped execution and full acceptance are separate. Stub CI executes all applicable software clauses. Stopped CI executes SC-01/SC-10, W1.6 and the stopped/fixed-flow clauses of V01/V69/V97 and D05/D15/D23/D41. Live `table_min` executes real-model/hardware obligations and the full applicable live path; stub `table_min` also runs the full demo with actual LIVE presenter capture. Each command must execute every assigned clause successfully; out-of-scope clauses remain `not_selected` in the full denominator. Registry preconditions come from the source, never from whether a test passes. Final `report --check` requires all compatible mode evidence, including stub-only adversarial variants, and blocks on any unresolved required clause or executed failure. Recording additionally requires one complete clean live six-act run, not assembled successful fragments. Software CI success cannot close hardware acceptance.

## Review dispositions and gates

1. Major 1: T1 records required evidence per source clause and provider test ID, including unresolved bindings. T4 requires a real-stack counterpart for every V. Aggregation is an AND across all required nodes; two providers with the same V ID are valid, duplicate clause/provider evidence is not. Missing nodes cannot pass.
2. Major 2: T2 propagates explicit evidence loss on cursor expiry. T4/T5 mark affected ordering and negative assertions erroneous/incomplete even after views recover. An authorization-filtered sequence jump alone is not evidence loss.
3. Major 3: T6 makes the existing `chc-harness report --check` command enforce measured branch numerator/denominator for every runner domain/application file, rejecting absent/stale evidence and ratios below 95%. Its regression uses 100 covered statements and 5 of 10 branches. Combined coverage is insufficient.
4. Major 4: T6's same command enumerates bounded actual files and invokes E18's `lint claims FILE...` with them, including the current run's REPORT.md, generated panel/manifest and every presentation source. It rejects missing files and seeded prohibited claims in each output. No directory arguments reach E18.

Exact task commands are in `tasks.json`, using the standalone runner project of `docs/tdd/E21.md:768`, subject to R-E21-1. Commands explicitly select its pytest/mypy/import configuration so the pharmacy settings cannot apply. Missing commands/providers are blocked gates. Run the full runner/scenario suites after bug fixes, then E20's repository gates and independent build review. Local success alone is insufficient; CI, required hardware/live evidence and the AI-use log row must also be complete. No implementation gates were run while writing this plan.

## Requests

Addressed to the coordinator. The existing canonical requests remain in `docs/requests/E21.md:7`. This supervisor is restricted to `docs/build/E21/` and must not edit the design or requests. Sol/coordinator must record and resolve the following handoffs there before enabling dependent work; this section is not approval of a proposal.

| Handoff | Required decision or provider work | Interim |
|---|---|---|
| R-E21-1 | Settle runner workspace, evaluation schema/loading policy, missing shared DTOs, durable intents/artifact commits/model exclusion, all section 11 settings, E18 freeze import, E17 stub path and E20 invocation. Preserve `eval/runner/` ownership. | Pure tests only where complete interfaces exist; no local lock, file-transaction mechanism or invented DTO. |
| R-E21-1 provider execution handoff | E01/E20 must agree how E05/E10's hub evaluation environment consumes E21's runner/plugin and Python Playwright binding. Provider registration, including E15-core, must be available before fixture-dependent final acceptance. | No hub-project edit or second browser launcher in E21; no guessed callable interface. |
| R-E21-2 | Publish isolated provisioning and deterministic Clock/MissionClock/IdFactory/scheduler/fault composition, including private-store and evaluator fault phases. | Public API tests on independently provisioned isolated hubs only; unavailable phases cannot count as tested. |
| R-E21-3 | Publish complete invocation observer and process-epoch ordering at actual inference, including stub/stopped attempts. | No zero-call or cross-process-order verdict. |
| R-E21-4 | Update the stale missing-design list for E05; coordinate its remaining requests. Supply E06/E07/E08/E09/E14/E16/E29 contracts and real-stack test bindings. | Boundary/manual tests only; acquisition, transport and report clauses remain open. |
| R-E21-5/6/7 | Settle result-panel/API handoff, complete GroundLink read attribution and lunar surface-day-4 precondition. | No invented endpoint/read trace/phase origin; respective V80, D28/D32, D43 and frozen-demo clauses remain open. |
| Major 1 provider assignments | Confirm all unresolved provider test bindings in T1's matrix and publish actual collected nodes, especially complete V37/V38 and absent-provider clauses. Provider authors own missing tests. | Keep the required clause in the denominator as awaiting_request or missing_provider. |
| Reference evidence ownership | Reconcile `docs/tdd/E01.md:702` (E20 alone writes `eval/reference/`) with `docs/tdd/E20.md:670` and `docs/tdd/E21.md:738`. Assign the measurement output and exact registration before writing it. | No task owns reference output; consume compatible E20 evidence. T-E21-09 and reference acceptance stay open if required data are absent. |
| R-E21-8 extension | Append AI-use rows for this build-planning run and its read-only provider/ownership audits, dated 2026-11-14, tool Codex, outputs PLAN.md/tasks.json, scope and validation as recorded here. Future coder runs need their own rows. | No shared-log edit by these tasks; hand-in awaits coordinator attribution. |
| Gate-selection rule handoff | Add the lesson from this plan's mode-gate error to AGENTS section 6: a gate must state its source-derived execution scope; stopped/stub CI cannot require unavailable live hardware, and aggregate promotion must still require every live/hardware obligation. | The explicit rule already applies in all six briefs; AGENTS.md is outside this supervisor's edit scope. |

Planning checks passed: six tasks, 72 disjoint ownership entries, an acyclic dependency graph, existing read paths, 98 distinct V provider rows and all 44 D assignments. Ownership was checked against E21 section 13 and nine other current task files. The generated-run directory is limited to the five artifact names in E21 section 13. No forbidden dash forms or gate bypasses were found. The independent provider-matrix audit found no errors. These checks validate the assignment only, not the unbuilt software.

Independent planning review found one major: incompatible mode obligations made the initial stopped/CI gates impossible to satisfy honestly. Fixed by the scoped-execution rule above and the same explicit rule in every task brief, with separate live and stub reference-demo gates. Ownership, DAG, read paths and review-major implementation locations passed the review. This was a plan review, not another review of the greenlit design.
