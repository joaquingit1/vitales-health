# SYSTEM and DEPLOYMENT response to cold review r2

The technical findings are resolved in the architecture; comment 36 remains partly fixed because implementing the
shared-document lint mode requires files outside the authorized scope. Existing section numbers, existing Part A
decisions and Part B are preserved. The product package was not edited. No git command was run.

This is design evidence. It does not claim implemented runtime protocols, clinical validation, mission acceptance,
CI success or an overall greenlight while the mandatory gate remains red.

## Review comments

Numbers match SYSTEM.astra-r2.md. Answered means the previously resolved rule is retained and verified, not reopened.

| Comment | Status | Evidence |
|---|---|---|
| 1 | answered | `docs/SYSTEM.md:2160`; `docs/SYSTEM.md:2183` retains both Q14 paths, compensation, trusted proposal context and no overrides. |
| 2 | answered | `docs/SYSTEM.md:2038`; `docs/SYSTEM.md:2072` keeps clinical review a visible state separate from source/purpose/applicability eligibility. |
| 3 | answered | `docs/SYSTEM.md:1944` retains both available readings; disagree stays retry and the plan step remains incomplete. |
| 4 | answered | `docs/SYSTEM.md:2128` retains default-off, prospective opt-in, both totals, immediate revocation and aggregate-only visibility. |
| 5 | answered | `docs/SYSTEM.md:1151`; `docs/SYSTEM.md:1162` retains the journal seal/reference/receipt barrier, keyed identity and crash recovery. The generalized coordinator also covers reports. |
| 6 | answered | `docs/SYSTEM.md:1775`; `docs/SYSTEM.md:1788`; `docs/DEPLOYMENT.md:419` retains independent wrapping keys, current revocations and selective restore behavior; no historical key backup claim. |
| 7 | answered | `docs/SYSTEM.md:2117`; `docs/SYSTEM.md:2124` retains task-only help access, limited summary, no officer chart grant and explicit trigger/scope/expiry/audit. |
| 8 | fixed | `docs/SYSTEM.md:737`; `docs/SYSTEM.md:788`; `docs/SYSTEM.md:813`; `docs/SYSTEM.md:871`; `docs/DEPLOYMENT.md:282` supplies the catalog, field/schema rules, export/apply registration and atomic cursors. Local audiences are distinct; revocation has a control lane; opaque origin tokens hide excluded sequence gaps. |
| 9 | answered | `docs/SYSTEM.md:2075`; `docs/SYSTEM.md:2076`; `docs/SYSTEM.md:2078`; `docs/SYSTEM.md:2079` preserves per-use quantities, fresh/due skills, operator filtering and full substitute checks. |
| 10 | answered | `docs/SYSTEM.md:970`; `docs/SYSTEM.md:977` retains complete identity and current result visibility. Receipt recognition is now explicit under comment 29. |
| 11 | answered | `docs/SYSTEM.md:614`; `docs/SYSTEM.md:728` retains the complete immutable event envelope, pin, audience and replay semantics. |
| 12 | answered | `docs/SYSTEM.md:1244`; `docs/SYSTEM.md:1251` retains canonical full-field hashing and independent head verification. |
| 13 | fixed | `docs/SYSTEM.md:263`; `docs/SYSTEM.md:291`; `docs/SYSTEM.md:312` separates capture-stream segments from global correction revisions and specifies repeated raw-time, restart and warning-clearing tests. |
| 14 | answered | `docs/SYSTEM.md:1262`; `docs/SYSTEM.md:1310` retains absolute versus elapsed behavior, keyed generations, cancellation and atomic callback completion. |
| 15 | answered | `docs/SYSTEM.md:1953` retains a complete schema-valid immutable approved snapshot, unknown-fact fallback and immediate queueing. |
| 16 | answered | `docs/SYSTEM.md:1895`; `docs/SYSTEM.md:1910`; `docs/SYSTEM.md:1918` retains the single E32-owned partition/batch/query seam, provenance, nullable quantities and bounded reads. |
| 17 | answered | `docs/SYSTEM.md:402`; `docs/SYSTEM.md:432` retains sourced wire mappings, explicit pressure, original quantities and one four-place half-even sensor rounding. |
| 18 | answered | `docs/DEPLOYMENT.md:635` preserves D8 ETags, editor/operator roles, open-work pins and D1 warning protection. |
| 19 | fixed | `docs/SYSTEM.md:149`; `docs/SYSTEM.md:2342`; `docs/SYSTEM.md:2366`; `docs/SYSTEM.md:2410` introduces E15-core, real mission dependencies, reverse late bindings and recomputed acceptance waves. E28 requires E26 for escalation. |
| 20 | fixed | `docs/SYSTEM.md:169`; `docs/SYSTEM.md:571`; `docs/SYSTEM.md:1677` keeps SQLite/framework/I/O implementations outside kernel. Convention discovery and project import-closure checks cover new modules without cross-owner edits. |
| 21 | answered | `docs/DEPLOYMENT.md:248`; `docs/DEPLOYMENT.md:253`; `docs/DEPLOYMENT.md:269` retains atomic replication/incarnation recovery and now prevents a stale backup from resurrecting revoked access. |
| 22 | fixed | `docs/SYSTEM.md:2258`; `docs/SYSTEM.md:2266`; `docs/DEPLOYMENT.md:326` makes only the current owner mutate occurrences. Old owners retain/forward facts; on-time completion reconciliation retains miss history and never uses HLC as write authority. |
| 23 | answered | `docs/DEPLOYMENT.md:559`; `docs/DEPLOYMENT.md:563`; `docs/DEPLOYMENT.md:570` retains authenticated native/container routes, private service DNS and tested native macOS fallback. |
| 24 | answered | `docs/DEPLOYMENT.md:589`; `docs/DEPLOYMENT.md:611` retains complete offline runtimes, platform prerequisites and clean install/upgrade/rollback gates. |
| 25 | answered | `docs/DEPLOYMENT.md:357`; `docs/DEPLOYMENT.md:361` retains sourced lifetime, stable approved identity, current consent checks, priority and bounded renewal. |
| 26 | fixed | `docs/requests/SYSTEM.md:8`; `docs/SYSTEM.md:2481`; `docs/SYSTEM.md:1316` keeps requests canonical and health wire output valid. Unfiled legacy references now point to explicit shared requests rather than nonexistent epic request entries. |
| 27 | fixed | `docs/SYSTEM.md:324`; `docs/DEPLOYMENT.md:681` defines the durable advancing-E receive reference, unchanged observation dates, original-date display and stop/loop/restart tests; LIVE/SIM retain D3. |
| 28 | fixed | `docs/SYSTEM.md:1015`; `docs/SYSTEM.md:1040`; `docs/SYSTEM.md:1019` classifies all effective mutations, forbids caller opt-out/local ledgers and preserves approvals after cache expiry. Multi-transaction work uses shared pending parent intents and separate child identities. |
| 29 | fixed | `docs/SYSTEM.md:982`; `docs/SYSTEM.md:989` defines the privacy-safe accepted_command receipt and exact matching rule; mismatches have null current_state and cannot remove the new queued command. |
| 30 | fixed | `docs/SYSTEM.md:1818`; `docs/SYSTEM.md:1852`; `docs/SYSTEM.md:1864`; `docs/SYSTEM.md:1872` settles the E26-owned port, occurrence/pin/actor context, separate commit boundaries, pending parent identity and crash reconciliation without resetting help. |
| 31 | fixed | `docs/SYSTEM.md:350`; `docs/SYSTEM.md:373` uses opaque string IDs and a separate durable generation, preserving package round trips. |
| 32 | fixed | `docs/SYSTEM.md:1111`; `docs/SYSTEM.md:1174`; `docs/SYSTEM.md:1166`; `docs/DEPLOYMENT.md:626` assigns E01 coordination, private report source/draft versions, atomic clinical confirmation, deletion/late-result behavior and an all-writer paired backup barrier under D18. |
| 33 | fixed | `docs/SYSTEM.md:115`; `docs/SYSTEM.md:1321`; `docs/SYSTEM.md:1375`; `docs/SYSTEM.md:1393` assigns shared job endpoints and task execution to E01, including leases, generations, pins, retry/restart/cancel, current result visibility and lawful public Job mapping. |
| 34 | fixed | `docs/SYSTEM.md:1443`; `docs/SYSTEM.md:1489`; `docs/SYSTEM.md:1493` defines the shared exception, pinned marker, evaluator registry, completion identity, fault audit and bounded recovery. Forbidden errors escape; old evaluations cannot replace newer safety state. |
| 35 | fixed | `docs/DEPLOYMENT.md:436`; `docs/DEPLOYMENT.md:456`; `docs/DEPLOYMENT.md:471` specifies per-owner creation/wrapping/unlocking, persisted wrapper only, lock disposal, credential/secret changes, revocation and restart/owner-switch/exactly-once tests. |
| 36 | partly fixed | `docs/requests/SYSTEM.md:239`; `docs/SYSTEM.md:2462`; `docs/DEPLOYMENT.md:667` files the exact shared-lint mode and regression requirements, removes all unfiled request errors and adds explicit internal/wire declarations. The script and its tests are outside the authorized five files. The current gate still has 18 template errors and 37 enum-declaration warnings; no waiver or greenlight is claimed. |

## Package issues

| Issue | Status | Evidence |
|---|---|---|
| P1 | answered | `docs/requests/COORDINATOR-DECISIONS.md:26`; `docs/requests/COORDINATOR-DECISIONS.md:65`; `docs/SYSTEM.md:1704` retains the signed effective overlay and individual superseded package lines. Generator implementation/tests are not claimed by this documentation change. |
| P2 | answered | `docs/requests/COORDINATOR-DECISIONS.md:22`; `docs/SYSTEM.md:540` retains strong response-header ETags without inventing ConfigRelease JSON fields. |
| P3 | fixed | `docs/requests/COORDINATOR-DECISIONS.md:23`; `docs/requests/COORDINATOR-DECISIONS.md:335`; `docs/SYSTEM.md:2117` retains station denial and explicit clinical object/field sharing; A3 now also covers the blanket officer plan-access line. |
| P4 | fixed | `docs/requests/COORDINATOR-DECISIONS.md:33`; `docs/requests/COORDINATOR-DECISIONS.md:331`; `docs/requests/COORDINATOR-DECISIONS.md:332`; `docs/SYSTEM.md:1321` separates internal tasks from public Jobs, preserves the three closed kinds and existing assistant turn/stream/cancel APIs. Public job.progress stays Job data. |
| P5, private-report transaction exception | fixed | `docs/requests/COORDINATOR-DECISIONS.md:34`; `docs/requests/COORDINATOR-DECISIONS.md:322`; `docs/SYSTEM.md:1174` centrally extends the private two-store rule to reports without changing report wire schemas or clinical confirmation checks. |
| P6, D14 supersession omissions | fixed | `docs/requests/COORDINATOR-DECISIONS.md:92`; `docs/requests/COORDINATOR-DECISIONS.md:325` gives the REPLAY exception in the existing unique A3 rows, preserving D3 for LIVE/SIM. |
| P7, durable replay error semantics | fixed | `docs/requests/COORDINATOR-DECISIONS.md:333`; `docs/requests/COORDINATOR-DECISIONS.md:334`; `docs/SYSTEM.md:982` applies existing D6 through the open current_state object while retaining the closed ErrorEnvelope and making rejected reuse unambiguous. |
| P8, context ownership | answered | `docs/requests/COORDINATOR-DECISIONS.md:31`; `docs/SYSTEM.md:150` aligns E23 ownership and inputs. The package declares no conflicting implementation owner, so no invented A3 override was added. |

## Independent review of this revision

A separate read-only agent first audited package lines, then reviewed the changed mechanisms cold, verified each fix,
and reviewed the whole artifact again. Its final verdict found no remaining technical blocker or major; it explicitly
withheld overall green status for R-SYS-22. These additional findings are retained here because this response is the
only authorized review-output file.

| Finding | Origin | Status | Evidence |
|---|---|---|---|
| IR1 parent command accepted before final plan commit | introduced by this revision | fixed | `docs/SYSTEM.md:1025`; `docs/SYSTEM.md:1864` |
| IR2 private publication callback could not signal false completion | introduced by this revision | fixed | `docs/SYSTEM.md:1112`; `docs/SYSTEM.md:1183` |
| IR3 public Job status lacked kind/result mapper | introduced by this revision | fixed | `docs/SYSTEM.md:1360`; `docs/SYSTEM.md:1393`; `docs/SYSTEM.md:1417` |
| IR4 ambiguous source-channel sequence and correction cursor | introduced by this revision | fixed | `docs/SYSTEM.md:293`; `docs/SYSTEM.md:295` |
| IR5 local audiences tied to export eligibility | introduced by this revision | fixed | `docs/SYSTEM.md:585`; `docs/SYSTEM.md:707` |
| IR6 revocation had no independent typed transport | present before, made explicit by the new closed catalog | fixed | `docs/SYSTEM.md:804`; `docs/SYSTEM.md:880` |
| IR7 raw origin sequence leaked excluded counts | present before | fixed | `docs/SYSTEM.md:865`; `docs/DEPLOYMENT.md:294` |
| IR8 stdlib import closure rejected its own dependencies | introduced by this revision | fixed | `docs/SYSTEM.md:172` |
| IR9 stale restore could resurrect access grants | present before | fixed | `docs/DEPLOYMENT.md:269`; `docs/SYSTEM.md:1743` |

## Verification and remaining ownership

- Parsed all 23 Python blocks and checked annotations on all 172 functions/methods. The combined temporary interface
  file passed `.venv/bin/python -m mypy --strict --cache-dir /tmp/system-r2-mypy-cache
  /tmp/system-r2-signatures.pyi`, with explicit generated-enum and framework-boundary stubs only. This verifies
  interface consistency; it is not an implemented import-linter or runtime test.
- Verified all 48 original SYSTEM section numbers and 39 DEPLOYMENT section numbers remain unique. Added only five
  SYSTEM and two DEPLOYMENT subsections. Recomputed all 35 dependency nodes and waves 0 through 6; the independent
  reviewer obtained the same result.
- Verified all 260 A3 package references exist and are unique. All 35 R-SYS entries contain target, exact proposal,
  reason and acceptance-preserving interim; every architecture R-SYS reference resolves. Existing decision rows and
  all of Part B compare byte for byte with the initial snapshot.
- Both architecture documents and the request file contain no local user absolute paths, em/en dashes or spaced
  hyphen punctuation. No code, package, tests, configuration or engine files were edited.
- Ran `.venv/bin/python -B scripts/design_lint.py docs/SYSTEM.md docs/DEPLOYMENT.md --json` after the changes:
  **18 errors and 37 warnings remain**. All errors demand epic-template sections. All warnings concern Literal sets
  whose internal/wire declaration markers the current linter cannot interpret. The 19 unfiled-request errors are
  removed. R-SYS-22 requires a correct shared mode with regression coverage, not suppressions. The gate owner must
  implement it in scripts/design_lint.py and its tests before overall greenlight.
- No application, hardware, container, crypto/vault, runtime fault or CI suite was run for these document changes.
  Their required acceptance cases are specified at each interface; implementations and their evidence remain with
  the named epic owners. The product owner must propagate accepted A3 changes through the D12 effective contract.
- Package hash monitoring observed a concurrent change to docs/product/README.md. It was neither edited nor restored
  by this run; the cited package sources remain unchanged. A temporary snapshot naming collision prevented a full
  byte diff of the original SYSTEM text, so its section inventory was verified against the initial read instead.
  Verification rule: scratch snapshots must preserve repository-relative paths, never basename-only names.

New decisions are D17 (internal tasks versus public Jobs) and D18 (shared private-report durability). No existing
D or Q decision was reversed, reworded or reopened. No new owner-only product choice remains. The outstanding work
requires the gate maintainer and implementation/package owners; pending extensions retain the explicit safe interims
in docs/requests/SYSTEM.md and are not counted as passed acceptance.

AI run record: 2026-11-14, Codex architecture author with one read-only independent reviewer, r2 resolution in the
five authorized documents, verification and limitations above. AI-USE-LOG.md was not edited because the explicit
five-file scope excludes it; its owner can copy this record into the repository log.
