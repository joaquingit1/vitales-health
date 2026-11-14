# E19 build plan

For independent plan review, not implementation greenlight. The design is frozen. No product code or runtime gates were run. The hub, UI, knowledge and eval trees are absent; contracts/config contain pharmacy artifacts only. E01, E02 and E10 are hard providers (docs/SYSTEM.md:2371). Component work uses their documented ports and kernel fakes after delivery; acceptance requires real providers.

## Build order and connections

| Task | Concern | Dependencies | Owned area |
|---|---|---|---|
| T1 | Pure rules and validated content | E01 kernel/enums; request 2 for shared shapes | Domain, ports, policy loader, knowledge/workbench, domain tests |
| T2 | Append-only persistence and recovery binding | T1; request 1 | Command adapters, repository, migration, reconciliation, storage tests |
| T3 | Bounded public-evidence ingestion | T1, T2; requests 1 to 3 | Discovery/capture/recheck services, boundary adapters, worker, ingestion tests |
| T4 | Independent review and promotion | T1, T2; requests 1 to 3 | Review/promotion services and tests |
| T5 | Honest documentation releases | T1, T2, T4; requests 1 and 4 | Documentation service/publisher, kit adapter, generated research output, documentation tests |
| T6 | Authenticated surfaces and acceptance | T1 to T5; requests 1 to 6 | Module registration, API/CLI, feature UI, acceptance tests and E19 scenarios |

Run T3 and T4 together after T2. T5 can follow T4 while T3 finishes. T1 defines local rules/ports once; T2 binds shared storage; T3 stages public input; T4 exports complete reviews to E10; T5 renders pinned facts; T6 assembles through SYSTEM 5.12. New test subdirectories are allocations within the design's wildcard grant (docs/tdd/E19.md:971). Exact disjoint paths and standalone briefs are in tasks.json.

External prerequisites are additional hard dependencies. Settle shared declarations before dependent code, never invent them. Phase A's two contracted source operations and CLI can precede the full workspace, but CLI tests cannot replace HTTP/UI acceptance. Phase B waits for accepted generated contracts/copy. Disabled features and previews do not complete the epic.

All available build plans and other designs' ownership sections were checked. E10 keeps catalog/artifacts/releases and knowledge outside knowledge/workbench; E03 keeps shell/copy; E21 keeps runner/demo fixture (docs/SYSTEM.md:155, docs/tdd/E19.md:959). Recheck concurrent plans before dispatch. No task edits another task's paths.

## End-to-end proofs

T6 owns eval/scenarios/e19. Each journey uses authenticated HTTP through real create_app(settings), temporary real SQLite stores, or Playwright against that hub. CLI may prepare state. No mocked hub ports, dependency overrides, fake E10/identity, skips or xfails. Model/devices/ground are absent; documented external fakes and E01 time/fault hooks are allowed. Missing hard providers block acceptance. The E21 demo fixture requires its explicit profile.

| Journey and trace | What it proves |
|---|---|
| Discovery/capture; E19-01, R13, INV-01/02/03/12/13; T-E19-01 to 08, 26 to 32 | Positive lexicon query through the real boundary and documented Exa fake; archive request/response before use, capture/assess/extract admitted public evidence. No key means zero sockets with saved-import fallback; network off disables fetch/recheck/link checks. A refusal alone cannot pass. |
| V22; T-E19-26 | Actual hostile PDF extraction treats instructions as data, bounds the child/output/bomb and causes no execution or safety mutation. Full model/tool V22 remains receiving-owner evidence. |
| V26/V33; T-E19-02/04/06/07 | Real writeJournal canary and documented E18 sealed/adjacent target fixture remain absent from actual outbound/archive/release/audit/log exits during positive ingestion. Actual adapters reject path/adjacent-file escape, links/special files, denied roots, private destinations, redirects and rebinding. Workbench never opens the vault for its oracle. Other prompt/retrieval/export contributions remain with their owners. |
| Review/rollback; E19-02, R21, V24, INV-04 to 08; T-E19-09 to 21, 33 | Self-review fails; a distinct editor approves eligible content with its honest review label. Changed hashes, failed/revoked heads, forged audit and scalar status edits cannot promote. Build A/B with directed contradiction, show discrepancy, respect purpose/applicability, retain history, roll back and compare exact passage IDs through successful pinned-purpose HTTP. |
| Durability/privacy; INV-05/06/14; T-E19-08/09/10/22 | Kill at every artifact/store/audit/child/build/activation/pointer/ack boundary; inject full disk/EIO/corruption. No lost accepted record, false receipt or partial docs; retries after restart/24 hours have one effect. Different targets/parameters conflict, revoked replay fails, revision/generation races have one winner, clock jumps/mission boundaries never reopen completed work. |
| Documentation/V80; E19-03, R27, INV-09/10/11/14; T-E19-14/23/24/25/29/34 | Offline/incomplete inputs yield explicit preview/null counts; measured zero remains zero. Pin formulas/denominators, exclude failed/partial/incompatible/revoked runs, render qualified sourced claims and rights-safe content, publish complete checked outputs atomically. Compare actual E27 How we know UI. External link fakes support hermetic tests; a real publishable release additionally needs actual online evidence and delivered documentation items. |
| Containment; charter help/record critical paths; T-E19-28, D41 contribution | Real worker saturation, kill and restart leave actual cached help within 500 ms, manual record entry available and warning/urgency/pins unchanged with optional processes absent. Require actual E26/E01/E03, not health stubs. |

E19 assigns no D assertion directly (docs/tdd/E19.md:21, docs/product/tdd/E19.md:7); D41 is additional containment evidence. All five V contributions and T-E19-01 to 34 are allocated in the briefs. Cross-epic partial evidence never claims the whole scenario.

## Design deviations

SYSTEM and Part A win, then the cold review. Preserve all r1 regression obligations despite the author's answers (docs/tdd/reviews/E19.response-r1.md:3); no later greenlight exists.

| Frozen design point | Winning rule | What to build |
|---|---|---|
| Sections 1/14.14, T-E19-15: clinician gates patient_instruction | Q1/Q18, docs/requests/COORDINATOR-DECISIONS.md:43 and docs/requests/COORDINATOR-DECISIONS.md:57; SYSTEM 8.1/8.2 | Allow sourced, technically checked, purpose/rights/applicability-eligible unreviewed instructions with not_clinically_reviewed. Preserve distinct-editor checks and actual draft/source negatives. No new clinician question. |
| Sections 2/4.3/5.1/14.2: shared ResearchTx/E10 store | SYSTEM 4.4/8.1, docs/SYSTEM.md:156 | Separate E19/E10 stores under deployment research root; no joins/caller transaction. Shared completion provider is request 1, not a local two-store implementation. |
| Sections 4.1/4.2/5.1: IdGenerator/ActorRef, workbench_command, AuditForwarder | SYSTEM 5.1/5.7/5.8/5.9, D5/D6 | Canonical IdFactory/Actor/SessionContext, actor-wide durable CommandIdentity, intents, current ResultVisibility and full-envelope audit. No 24-hour-only ledger or feature chain. Secondary-store audit proof awaits request 1. |
| Sections 4.4/5.1/14.1: feature queue/workbench_job/public research kinds | D17, SYSTEM 5.10.1 | E01 internal tasks, public_kind=None, pinned reference inputs, lease/generation completion. No research public Job/event/editor scope or cancelJob expansion. Transport is request 5. |
| Section 4.3: unconditional interruption of running jobs | SYSTEM 5.2/5.10.1 | Recover kernel leases/generations. Possibly sent Exa queries become OUTCOME_UNKNOWN once under the agreed hook and never auto-resend; late cancelled results cannot publish. |
| Section 8: operational limits inside content policy | D7, SYSTEM 5.11/5.12 | Bounded HubSettings/SettingSpec for named operational limits, versioned content for public words/source-purpose policy. Pin both; retain last good; no independent settings loader. |
| Sections 3.5/14.2: legacy builder arguments/rights enum | SYSTEM 8.1; current E10, docs/tdd/E10.md:195 and docs/tdd/E10.md:224 | E10-owned complete ReviewExport and build(review_export); exact heads/extraction/page maps/rights/directed discrepancy. E10 rights: nasa_public_domain, permission_documented, metadata_only. No permission by enum rename. Request 2 settles serialization. |
| Sections 2/4.1/6: E01 roster/role-name varargs | SYSTEM 4.4/5.7/8.3; docs/tdd/E02.md:100 | E02 snapshot roster and generated operation-role boundary with trusted session. No patient imports. Auth/mission/MET binding awaits request 3. |
| Section 4.3: separate pointer/index replacement is atomic | INV-E19-14; r1 comment 4, docs/tdd/reviews/E19.astra-r1.md:9; AGENTS 6 | One authoritative pointer snapshot into immutable output. Shared provider must settle publication/index/receipt recovery before implementation, request 1. |
| Section 6: nonretryable storage and generic 422 capability code | SYSTEM 5.5/8.6/D5; docs/product/api/openapi.yaml:6103 | StorageUnavailable propagates retryable INTERNAL_ERROR. Source POST 422 permits INVALID_VALUE only; unavailable uses contracted default INTERNAL_ERROR until accepted extension. No undeclared envelope/code. |
| T-E19-07: remove loopback denial for a positive test | AGENTS 1/6; INV-E19-03 | Non-public denial is an invariant floor. Exercise a logical public endpoint through injected external transport, without weakening production policy. |
| Sections 9/11: worker on flight hub | SYSTEM 3/11, docs/SYSTEM.md:66 and docs/SYSTEM.md:2440 | Worker on ground/editor hosts only; flight network off/no Exa key. E10 owns offline install/activation. No runtime safety dependency on workbench. |
| Sections 3.7/14.8: trust simplified complete run manifests | Current E18 public/revocation contract, docs/tdd/E18.md:785 | Agreed current nonrevoked public projection only; keep denominators/engineering qualification. Missing binding gives null/not_measured_yet; no vault access/schema copy. |
| Section 15: engine validator and integration-ready Phase A | SYSTEM 6.1/6.2/6.4, docs/SYSTEM.md:1655 | E01 scripts/validate_hub.py, uv --project hub and generated import contracts. Never edit engine validator/root project. Missing shared providers still block Phase A integration. |

Review coverage: r1 1/2/13 in T1/T3/T6; 3/4/5 in T2/T6; 6/7/8/9 in T1/T4/T6; 10/15 in T2/T6; 11/12 in T5/T6; 14 in T1/T5; 16 in ownership/T6. Shared durability, exact export and transport remain prerequisite blockers under the newer architecture; obsolete fixes are not implementation authority.

## Requests

Addressed to the coordinator. docs/requests/E19.md is absent and outside this run's edit scope. Create it and file these plus the still-valid design section 14 proposals before dependent implementation. These are handoff requests, not canonical filings; settled policy is not reopened.

1. **E01/E20 durability, blocking T2 to T6.** Extend existing R-E10-16 (docs/requests/E10.md:188) to E19 store/audit proof/docs publication. Publish exact storage/snapshot/migration/artifact/admission/completion/audit ports, internal operations/handler versions, receipt/generation boundaries, backup/restore and cross-platform crash/cancellation matrix. SYSTEM intents/tasks are mandatory building blocks but lack this complete shared protocol. No invented local substitute; dependent mutations remain unavailable.
2. **E10 shapes, blocking dependent T1/T3/T4 work.** Close R-E10-14 with owned export schema, complete command DTOs (docs/tdd/E10.md:358), artifact/extraction/page-map/source enumeration, all review heads, rights, per-use events, directed discrepancies and shared fixtures. Current ResearchCatalogPort/ReleaseAdminPort/build(review_export) only; no legacy BlobStore/ApprovalInput copy.
3. **E02/E23/E01 authentication/context.** Join R-E02-9/R-E10-21: exact HTTP/CLI/internal entry, snapshot roster, current mission ID and sourced MET matching, editor revocation/replay and editor/operator release administration. No invented mission-clock schema field or patient access.
4. **Evidence/content owners.** Confirm E19 3.6 item owners/paths; deliver actual versioned docs, saved inputs/hashes (R-E10-18). Agree E27 kit snapshot and E18 public run/revocation projection; E04 intake may remain optional unknown. E03 supplies research/review copy. Missing inputs remain preview/null. No bulk NASA fetch (Q24).
5. **Contract owner/E01/E03, blocking full T6.** Route E19 14.1 operations with complete schemas/errors/revisions/roles/copy. Replace proposed public research Jobs/editor event scope with approved D17-compatible task status/cancellation transport. Settle ETags/unavailable/pin-mismatch responses and E10 R-E10-01/12 successful pinned-purpose passage HTTP. Until accepted/generated, ship only existing source operations/CLI and withhold full acceptance.
6. **Gates/runtime/evidence owners.** E01 supplies hub project/kernel.testing/composition/import generator/validate_hub integration, finite operational maxima and documented safety branch/condition/complexity/mutation gates. E20 supplies supervised worker, platform extraction containment and offline packaging; E21 runner/profile/traces; E03/E18/E26/E27 actual canary/help/V80/UI evidence. Coordinator records planner/coder/reviewer AI-USE-LOG rows outside this scope. Require independent build review and green CI.

Task gates are exact future commands. JUnit checks reject empty/skipped/failed task suites; coverage checks domain/application branch counters separately. T6 runs all E19 scenarios without filters, complete workbench tests, applicable UI gates and the full hub suite after fixes. No unavailable prerequisite, offline preview or missing source counts as completed acceptance.
