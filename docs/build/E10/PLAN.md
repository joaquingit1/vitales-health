# E10 build plan

Cold review: `docs/tdd/reviews/E10.astra-c1.md`, GREENLIGHT with zero blockers and three majors. The briefs settle the implementation rules for those majors. Pending shared contracts still prevent complete E10 acceptance. No product implementation or runtime gate is claimed complete.

## Build order

| Task | Concern and owned area | Dependencies |
|---|---|---|
| T1 | Pure domain models, E10 schemas and public port definitions | E01 kernel, generated contracts and documented fakes; agreed external shapes before declaring them implemented |
| T2 | Registry, source copies, condition resolution and pinned form adapter | T1 |
| T3 | Catalog capture/review application and deterministic bundle construction | T1; agreed catalog/artifact provider for mutations |
| T4 | Durable release installation, history, indexes, activation, rollback and recovery | T1, T3; shared artifact/secondary-store protocol |
| T5 | Pinned passage/search readers and passage HTTP boundary | T1, T2, T4; accepted selectors/labels for successful HTTP delivery |
| T6 | Module assembly, CLI, validation and real-stack acceptance | T2, T3, T4, T5; real consumer modules for their scenarios |

`tasks.json` is the exact file allocation. T2 and T3 can run concurrently after T1 publishes their contracts. T4 can overlap T2. Provider requests are hard dependencies for the affected work, even when not task IDs. Do not start an integration against an invented substitute. Kernel fakes support unit work; they are never production fallbacks or real-stack acceptance. Currently the hub, UI, knowledge and eval trees are absent, and the sole other build plan is E02. Recheck ownership before launching each fresh session.

T1 fixes E10's own types once. T2 supplies registry/source/form facts. T3 produces immutable, validated artifacts from the E19 export. T4 builds and validates indexes before publishing verified state, and retains artifacts using E01 mechanisms. Its index build needs no query-embedding service from T5. T5 reads exact pins and purposes. T6 registers the completed providers through SYSTEM 5.12, without changing E01 assembly or another task's files. Missing services retain the design's explicit unavailable states.

T6 exposes the runnable core composition before running the consumer scenario gates, so E12/E13/E19/E27/E28 can integrate without waiting for their own full scenarios to pass first. This checkpoint is usable provider code, not completed E10 acceptance. Optional consumers are late bindings and cannot become factory dependencies of the core.

## End-to-end proofs

T6 owns all three `eval/scenarios/e10/` files. Load the explicit E21 demo fixture, use `create_app(settings)` from SYSTEM 5.12 with a real temporary SQLite data directory, and enter through authenticated HTTP. Administrative setup may use the real authorized E10 CLI once its binding is settled. No mocked repository, fake authorization, fake registry, dependency override or replacement hub service counts. Controlled time and storage faults use the documented E01 testing boundary. Only optional external model/device/link processes may be absent or use documented fakes. A missing required provider is failed/unavailable acceptance, never a skip or successful negative assertion.

| File and trace | What the real journey proves |
|---|---|
| `test_evidence.py`: E10-02/03, R21, I2/I3/I5/I6; T-E10-13/14/16/37/38/47/49/50/52/53/54 | Capture, completeness assessment, extraction, technical check, per-use approval, build, install, verify and activate through the real authorized boundaries. Read by HTTP. Reject summary-only/incorrect-purpose/tampered content. Crash before and after each shared publication/commit boundary; retry after 24 hours, race two activations, roll A to B back to A, branch C, prune/rebuild indexes and restore. No accepted record or citation disappears and no late result changes a newer command. |
| `test_evidence.py`: V23, D10, T-E10-11/48/59 | Real E12/E13 output validation accepts a valid pinned citation and rejects a fabricated, wrong-purpose or history-only citation. The documented external model fake supplies candidate output. `getEvidencePassage` and `getAssessment` preserve exact release/provenance/review label. Switching active release during the assessment never substitutes a new citation. E13 owns the failed-observation half of D10; the full assertion requires its real implementation. |
| `test_evidence.py`: V24, T-E10-10/35/52/57 | Build and activate a current release with an explicitly reviewed contradiction relation. Superseded text stays out of current retrieval, historical membership remains inspectable, and the discrepancy reaches E19's real review surface. A port-only export check is component evidence, not the completed scenario. |
| `test_evidence.py`: V22, T-E10-22; I8, T-E10-44/58 | Embedded source instructions remain inert through retrieval and the actual tool boundary. Stop the optional embedding process: lexical retrieval still succeeds with the configured limitation. Remove/corrupt FTS: exact reads survive and searches report unavailable, not no match. Model/devices/ground absent never prevent local supported reads. |
| `test_registry.py`: R13, E10-01, D28/D32; T-E10-01/28/29/30/31/33/39/45/62 | Validate the pinned registry and candidate source/condition/form checks, change a sourced label in a scratch release without code or safety-mapping changes, and observe the HTTP view change. Real E27 `getKitMap` and E28 `getConditionPlan` consume exactly the active memberships and pinned plan identities. This test also uses Playwright through E21's agreed runner binding to compare E03/E27/E28 rendered card, kit view and result panel against these responses. Registry totals alone never claim full D28/D32. |
| `test_registry.py`: I6, T-E10-03/30/54/61 | Reject corrupt/missing candidate input while retaining last good state. Remove optional E10/title integration in a separate minimal composition: real `openHelpSession` and `recordObservation` still work with model/devices/ground absent. Activation changes no warning level, stock or open-work pins. Real safety owners are required for these assertions. |
| `test_privacy.py`: R20, V26/V33, I7; T-E10-17/18/19/60 | Write a canary via real `writeJournal`, query through the real tool/read paths, and inspect actual catalog, bundles, indexes, logs, audit, exports and backup projections. Other crew cannot read it and no query-derived fingerprint persists. Place a holdout canary adjacent to an allowed source and behind a symlink: imports/tool retrieval reject it. Exercise positive allowed retrieval too. No screenshot of private content is stored. |

Each test records its complete T/R/V/D trace using the existing runner's registration. T6 runs the complete E10 test and scenario directories with no filtering. E21/E03/E27/E28 retain ownership of their runner and UI implementations; T6 owns the real-stack assertions above, including rendered D28/D32. The Python-to-Playwright test-entry binding must be agreed before coding, not invented or added to another epic's files by E10.

## Requests

Addressed to the coordinator:

1. Resolve R-E10-14/16/17/19/21 with E01/E02/E19/E20 and the fact consumers. Before T1 freezes dependent types, have the E10 author supply the missing exact DTO fields, catalog table names/constraints, internal operation identifiers and task handler/version registrations. Comment 2 fixes the mechanism: SYSTEM 5.8 intents and 5.10.1 tasks, no `durable=True`, no feature queue, no new public Job kind. These names cannot be guessed in the briefs. Keep the affected commands unavailable until the agreed declarations exist.
2. Resolve R-E10-01/12/04 for successful passage HTTP selection and review words; R-E10-03 remains optional HTTP administration, with CLI as the documented interim. E01 owns effective contract generation, E03 owns ui-copy. E10 edits neither.
3. Complete R-E10-18 with a manifest of existing local inputs and exact E10 destination ownership. Reuse `docs/product/cliff/forms/` and its manifest under Q24/Q26; original PDFs and the recovered catalog are separate missing inputs. No bulk fetch, copied private archive, fabricated hash or sample replacement.
4. Align E13's nullable evidence reader with E10's complete result union, and confirm the E19 and E27/E28/E03/E21 scenario entry bindings above. Required test consumers may delay acceptance but cannot be replaced with internal mocks.
5. Deliver E01's real kernel/testing, composition root, generated models, task/command infrastructure and import-contract generator before implementation gates. R-E10-10 covers dependencies, CLI packaging and shared validation. R-E10-20 covers the remaining gate-recognition defects; the real clock mismatch is fixed by comment 1, not waived.
6. Record the supervisor and supporting audit runs in `AI-USE-LOG.md`, which is outside this edit scope. Require independent build review and green CI before declaring E10 done.

These requests extend or route the existing entries; this supervisor does not edit the design or its request file. Majors are handled in T1/T4/T6 and their prerequisites. Any newly missing declaration is returned to the coordinator before dependent code, not silently invented. Task gates in `tasks.json` are future completion gates and were not run against the absent implementation.

## Plan verification

The supporting agent independently checked the seams and drafted allocations. Its two substantive findings are fixed: T5 calls the canonical no-argument `embedding_pin()`, and T4 owns `adapters/release_index.py` so verification can build indexes without depending on downstream retrieval. The conformance citation was also corrected. Document checks confirm six tasks, 83 distinct owned files, an acyclic dependency graph, existing read inputs, ownership within E10 section 13 and no overlap with the existing E02 plan. These checks are planning evidence only; implementation gates and CI remain outstanding.
