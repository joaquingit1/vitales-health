# E12 build plan

Cold review: docs/tdd/reviews/E12.astra-c1.md. Zero blockers; comments 1 to 4 are settled in the briefs below.
This is a conditional build plan, not permission to implement proposed interfaces or a claim of completed acceptance.
The implementation roots were absent at inspection. All code ownership is expanded from docs/tdd/E12.md:590.
No E12 table, migration, HTTP route, UI component, worker, shared validator or replacement provider is planned.

## Build order

| Task | One concern | Dependencies | Connection |
|---|---|---|---|
| T1 | Pure E12 values, invariants, port declarations and settings | E01 kernel, generated contracts and approved E12 interface bindings | Freezes the domain/application contracts used by the other tasks |
| T2 | Validated tool dispatch and atomic proposal application | T1 | Uses static handlers, current grants and the caller's E13 apply UOW |
| T3 | Pinned evidence retrieval and its development comparison | T1 | E10 search and E27 eligibility; E17 artifacts; E18 dev files |
| T4 | Authorized context assembly and read handlers | T1 | Owner projections, support classification and pinned token budget |
| T5 | Output-policy enforcement | T1 | Shared text rules and owner guards before E13 accepts or publishes text |
| T6 | Composition and assembled acceptance | T2, T3, T4, T5 | Registers the module and tests the actual HTTP/SSE stack with SQLite |

Start T2 to T5 in parallel after T1 freezes its types. Each can use kernel fakes at agreed ports while its real
provider is unfinished. Resolve a newly exposed shared seam with the coordinator before implementing it. A pending
provider binding is a real dependency, not a reason to copy the provider. T6 accepts no mocks inside the hub.
Follow each task's explicit prerequisite list in tasks.json; task dependencies alone do not imply provider readiness.

Every brief is standalone and owns exact disjoint files. T1 owns shared test scaffolding; later tasks request changes
from its coder instead of editing it. T3 owns the two E12 fixture documents. Other tasks use validated provider
fixtures/configuration and read those documents when available. E21 owns the shared demo and stub scripts.

## Assembled acceptance

T6 runs the hub composition root create_app(settings) from docs/SYSTEM.md:119 against a real temporary SQLite
database. It seeds through authenticated owner operations or the agreed E21 seed adapter. E17's documented stub is
the optional model boundary; no in-hub service replacement is permitted. Stopped-model cases start with that worker
absent. All records retain LIVE, REPLAY or SIM independently of model mode. Test bodies belong to the four E12
scenario files below; no generated scenario names or guessed API are required.

| Owned file | What the end-to-end cases prove |
|---|---|
| eval/scenarios/e12/test_tools.py | Unknown/stage/schema/cross-person actions are denied and audited before execution (R17, V25). Cancel and urgent entry remain usable during delayed/malformed inference, with incomplete state visible (V27). Changed facts make the assessment stale; crash, disk-full, retry, changed target and generation fencing preserve atomic receipts/effects (V30, I5/I6). Positive and unavailable measurement proposals retain reason, decision, capability, heart body region and prior red-flag ordering; unavailable kit produces the persistent gap/form link and handoff without acquisition (R45, D06, D30, V78). Invalid warning actions leave warnings unchanged; a valid evidenced one-action insight dismisses only itself (V65). |
| eval/scenarios/e12/test_retrieval.py | PDF instructions cannot execute actions (V22). Every displayed support reference resolves to an eligible pinned passage or accepted usable observation; failed attempts never support any assessment section (R16/R21, V23, D10/D11). Superseded contradictions remain visible but cannot supply current support (V24). Exhausted available tests yield honest gaps and a supported next step (V28). A missing benchmark lab is never invented (V34). Dense loss falls back to eligible lexical results with configured notice; missing/corrupt pins or tokenizer fail visibly. |
| eval/scenarios/e12/test_privacy.py | Cross-person, journal, private-report and participation canaries stay out of prompts, queries, logs, audits, SSE and authorized export/backup/sync projections, including revocation during inference (R20, V25/V26). A hidden target adjacent to allowed dev files and symlink/traversal attempts never reach the retrieval/model process (R23, V33). Use the owners' inspection/export interfaces; no public tool-trace endpoint. |
| eval/scenarios/e12/test_fallbacks.py | With model, gateway, devices, dense worker and ground link absent, fixed help, rules, routine misses, manual capture and cached/printed backups still work (T-E12-070). REPLAY habitat readings never become personal exposure (V10). Help assistant output follows fixed steps, one question at a time, with pinned step/kit references; medicine/dose attempts are rejected and logged (R56, V63, D25). Invalid plan IDs, answers, non-runnable steps and model dose text leave authored steps/order unchanged (V84). Validated copy edits change content while invalid activation preserves the last good release and active safety state (I8). |

All fifteen V scenarios and five D assertions claimed in E12 section 1 have an E12 HTTP/SSE case above. D25/D30's
screen assertions also require E21's existing test_D25/test_D30 and the E13/E03 renderers. T6 runs the published E21
e2e command after that owner binds the runner. It must inspect actual rendered ordering, labels and citations; an
API-only check cannot close a visual clause. E21 owns those test files and fixture scripts, not E12.

## Requests

Addressed to the coordinator. These are prerequisites or changes in unowned files, never E12 task ownership grants.

1. Supply E01's SYSTEM section 10 kernel/testing, generated effective contracts, standalone hub toolchain, real
   composition/storage and mandatory loader checks. At inspection no hub implementation existed. E02 authentication
   and grants, E10 evidence and E27-guidance are hard dependencies under SYSTEM section 9.
2. Resolve the already filed R-E12-1/2/4/5/7/9/11/12 with their named owners before the affected task implements or
   enables the seam. T1 requires the complete tool/result/effect and stage types; T2 requires atomic owner commands;
   T3 requires citation, query, artifact and snapshot bindings; T4 requires authorized projections/freshness and E14
   calculations; T5 requires canonical text rules and copy. No pending schema goes into a decision overlay. Existing
   safe disabled behavior is required but does not complete the feature. R-E12-13 remains with the design-lint owner.
3. For review comment 2, have E01 extend scripts/validate_hub.py's existing coverage, safety, mutations and trace
   gates to consume E12 evidence. Its current documented inventory is E01-only. Include every executable tools
   domain/application file, independent branch percentage at least 95, full safety-condition coverage, complexity at
   most 15 and mutation checks for authorization, support and forbidden-output decisions. Missing tests/inventory or
   surviving non-equivalent critical mutants must fail. Preserve the published commands; T6 produces the combined
   branch JSON report specified in tasks.json. E12 does not edit the validator or invent a parallel one.
4. E13/E17/E21 must supply the real controller, documented stub, frozen demo fixture, accepted bounded private trace
   inspection and runner binding, including current E21 request R-E21-1 and E18 request R-E18-12. E03/E13/E21 own
   UI ordering evidence; E05/E16 own unavailable-kit handoff; E24/E26/E28 own safety guards and fixed paths;
   E20/E02 own backup/export/replication evidence. T6 cannot claim these scenarios with local substitutes. E18 supplies
   dev labels and a verified manifest; E12 never edits its corpus, sealed vault, scoring registry or published scores.
5. Record this supervisor run and later coder/reviewer runs in AI-USE-LOG.md. That file is outside the authorized
   edit scope. Sol owns any correction of stale E05/E28 references in E12's design/request documents.

## Verification and completion

Design lint passed with no findings. This plan partitions all 64 E12 implementation/fixture files, assigns all 38 test IDs and checks disjoint ownership,
dependency order, existing read paths, scenario coverage and JSON validity. Current and future provider files must be rechecked before
dispatch because other epics are working concurrently.

Task gates select hub configuration explicitly and use uv. The final task runs all hub tests, E12 scenarios, shared
contract/interface/corpus/trace/safety/mutation gates, import rules, strict typing and formatting. Its E01 validator
extension is a prerequisite, not a silently passing E01-only check. Reference measurements are separately labeled.
No runtime gates or CI have run for this documentation-only work. Implementation completion additionally requires
resolved independent review, all required acceptance evidence, green CI and the AI-use log entries. Missing providers,
unrun reference measurements and failing gates must remain visible; they cannot be replaced by skips or placeholders.
