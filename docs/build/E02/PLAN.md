# E02 build plan

This is a conditional build plan, not a build greenlight. E02's cold-review response is
`docs/tdd/reviews/E02.response-c1.md`. No product code was written. At inspection, hub/, ui/, eval/,
contracts/api/, the shared runtime configuration and other docs/build/*/tasks.json files did not exist.
The only contracts/configuration present belonged to the pharmacy engine. The ownership audit found no collision
between E02 section 13 and the other 25 epic designs. Recheck ownership before dispatch because other supervisors
may publish plans after this snapshot.

## Order and release conditions

E01 first supplies SYSTEM 6/10 protocols, kernel.testing, generated contracts, the uv project and gate tools.
R-E02-5/9 must settle exact authentication and field-consent bindings before a coder writes their callers.
R-E02-22 records the missing bootstrap, CLI, UI and test-fixture interfaces. Never create those dependencies in E02.

1. T1 builds identity, authentication, roster and pure access/routing policy. Its frozen application ports unlock
   T2 consent and T3 vault work. It does not own composition registration.
2. T2 consent and T3 vault may then run in parallel in disjoint files. T2 requires the R-E02-21 expiry fence;
   T3 requires the R-E02-20 concrete backend and E01 barrier binding. Missing prerequisites keep that task blocked.
3. T5 wires only the two E02 module.py files after T1/T2/T3 and runs HTTP acceptance when the named provider
   modules are available. It does not depend on T4: its scenario tests use the real HTTP stack.
4. T4 then builds the lock integration against T5's running composed hub, E03's agreed lifecycle hook and shell,
   and E20's queue mechanism. Its browser tests finish the UI privacy and emergency-cache acceptance. This edge
   avoids a cycle in which the UI gate waits on a composition task that itself waits on the UI gate.

T1 and T2 own their application protocols. T3 implements the existing kernel PrivateKeyVault, so it adds no shared
port. T5 imports only provider application ports and the kernel through SYSTEM 5.12 registration; it cannot edit
T1/T2/T3 implementations. Domain/repository tests use kernel.testing while providers are absent. Only tests may use
fakes; production missing facts deny, never return sample data. A missing agreed function name is a coordinator
request, not a compatibility overload. No task may edit another task's paths, E01 assembly, E03 shell, config,
contracts, fixtures, the root toolchain or AI-USE-LOG.md. The coordinator records each run under R-E02-18.

## End-to-end proofs

T5 owns `eval/scenarios/E02/test_privacy_acceptance.py`; T4 owns `sessionLock.spec.ts`, including the browser
help-cache fallback. The E02 HTTP acceptance task does not require E02's lock component. Each test carries its
T-E02/R/V/D trace. Load the explicit E21 demo profile into a temporary real SQLite data directory and use
E01 `create_app(settings)` with normal discovery, middleware, generated models, repositories, audit and command
ledger. HTTP requests go through that app, or Playwright drives the real crew UI against a running hub.
No mocked router, fake repository, dependency override, fake consent, fake clock service or fake audience inside the
hub counts as end-to-end. Controlled clocks/faults use E01's documented platform testing boundary. Documented fakes
may replace optional external model/device/link processes only. Absent required provider modules fail acceptance.
Clinical fixture values come from configuration/fixture adapters, never inline invented medical content.

| Trace | Real-stack journey and required proof |
|---|---|
| R02, E02-01/03; T001/003/007/008/010/013 | Sign in as owner, other crew, officer and station; recordObservation/getObservation enforce object/field rights. Create and revoke a narrow grant through the agreed owner CLI, then prove HTTP, existing SSE and late replay deny after revocation, including a separate CLI process with no subsequent HTTP refresh. First read observes finite expiry, sweep is delayed, clock regresses or hub restarts: reauthentication cannot revive it. Test both permitted and denied projections. |
| R20, E02-02; T014 | writeJournal and listJournal plus E09 uploadReport/extraction use real PrivateRecordCoordinator and supported vault. Crash at each shared commit boundary, retry and restore paired data. Delete A, retain B, reject stale vault state and wait for an offline registered replica. Journal and report key allocation contend with backup barrier. Secure erasure needs real backend evidence in addition to hermetic tests. |
| V08, R29; T004/016 | Unpaired station acquisition through the real measurement API creates no patient Observation; an adapter cannot submit another request through recordAttempt. Remove E05/E26/model/devices/link in a separate minimal deployment: a verified officer's direct manual recordObservation for an incapacitated subject still commits. Quality/units remain real checks. |
| V09, D09; T005 | Real acquisition workflow using the documented external camera adapter fake, real E05/E01 and explicit presenter mapping. The accepted LIVE pulse belongs to demo_operator, keeps source/selector/disagreement, and never changes the SIM subject's baseline, streak or goal. E06/E14/E22 providers are required for these assertions; manual-only policy tests do not prove D09. |
| V25; T006 | Drive openConversation/sendMessage with the documented external model fake producing a forged cross-person tool request. Real E13/E12 gateway and E02 policy reject it despite the model's reason. No model mock inside the hub and no forged direct call substituted for the tool path. |
| V26, R20; T006 | Write the canary via writeJournal, verify legitimate owner response, reject another owner and inspect real prompt, retrieval, routine log, error, event, export and backup outputs. Exercise the actual providers; an absent output surface is not a passing negative assertion. No private screenshot is saved. |
| V64, D24; T002 | openHelpSession, authorized getHelpSubject, actOnHelpSession close, then the identical read returns UNAUTHORIZED_SCOPE. Verify source/age and names-only rendering plus each read's audit. Uninvolved crew, station, wrong subject and summary-ID chart access are denied. |
| D13; T006/017 | Actual act-5 ground preview and explicit approval queue the configured log, retain next-contact leaves_at and canary exclusion. Care/plan continues while link is down; the documented transport simulator later delivers with attribution. E16/E29 real modules and approved snapshot checks are required. |
| D26; T006/017 | previewGroundMessage, queueGroundMessage and later actOnHelpSession ticks prove help continues after queueing. getGroundMessage/getGroundLog show only approved delivered content, simulator attribution and no canary. Revoke an unsent report grant and verify cancelled transmission; a separate valid field-limited report snapshot reaches only its intended recipient. |
| Critical privacy fallback; T011 | T4 Playwright: sign in, render private content, disconnect/hide/suspend/expire/switch owner, then reject late responses and Back/bfcache repaint. Original-owner encrypted pending commands survive and cannot replay under the next owner. No token or private view persists. |
| Critical safety/configuration fallback; T016/021 | T4 browser test: warm real signed emergency cache, then stop hub/authentication/storage and saturate KDF: first help screen remains available within the charter budget; printed fallback is generated from the same configuration. Change a scratch ui-copy label through real release loading and observe changed text; invalid content retains last good release. |

The rows above cover every E02 V/D scenario and critical privacy, durability, configuration and emergency fallback
path. They preserve the remaining owners in design 12.1; E02 does not claim whole-epic clinical validation for them.
Model, devices and ground are absent except for the explicitly exercised external fake. Do not mark tests skipped,
xfail, not_run or a fail-closed placeholder as a completed acceptance gate. Missing providers are release blockers.

## Gates and handoff

Exact per-task commands are in tasks.json, from design 15 and SYSTEM 6. Run from repository root. Pytest explicitly
uses hub/pyproject.toml, including eval, so pharmacy settings cannot select/skip E02 tests. Each task's tests run
without -k filters. Import contracts are generated for all installed module directories, including unfinished ones;
E01 must provide that checking before task gate execution, or the gate remains blocked. UI uses pnpm and generated
@crew-health/contracts. No task repairs a shared tool configuration itself.

After integration, run every task gate, the full hub and crew suites, validate_hub, 95% domain/application branch
coverage, full access-decision condition coverage and the design's 90% mutation threshold. E01's documented mutation
and trace gate must enforce those thresholds, not merely run a mutation command. CI and independent build review
are required; cap build reviews at three and route unresolved blockers to the coordinator. Current evidence is
only design lint and plan/ownership checks. R-E02-20/21 remain unresolved cold-review integration findings.
