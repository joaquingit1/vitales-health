# SYSTEM and DEPLOYMENT response to cold review r1

The architecture text is revised for all 26 comments. Existing section numbers and every existing Part A decision are
preserved; Part B is unchanged. Only the five authorized files were written. No git command was run. This response
records design evidence; it does not claim implementation, clinical validation, mission acceptance or a new independent
greenlight.

## Review comments

| Comment | Status | Evidence |
|---|---|---|
| 1 | fixed | `docs/SYSTEM.md:1478` supplies proposal, lot, dose, plan step, condition, pin and fingerprint. `docs/SYSTEM.md:1501` applies both Q14 paths, compensated recommendations, accepted-assignment audit and no allergy/interaction overrides. |
| 2 | fixed | `docs/SYSTEM.md:1353` separates source/purpose facts from clinical_review. `docs/SYSTEM.md:1390` makes unreviewed status a visible label while retaining eligibility checks; Q1/Q7/Q18 never wait for a clinician. |
| 3 | fixed | `docs/SYSTEM.md:1261` preserves manual and available camera observations on disagreement, with methods and provenance; retry leaves the plan step incomplete. `docs/requests/COORDINATOR-DECISIONS.md:173` supersedes the null-reference constraint. |
| 4 | fixed | `docs/SYSTEM.md:1446` defines E22-owned default-off consent, filtering, prospective inclusion, immediate revocation and cache invalidation. `docs/requests/COORDINATOR-DECISIONS.md:24` supplies the owner-only resource. |
| 5 | fixed | `docs/SYSTEM.md:708` defines private ports, HMAC identity, sealing, hub commit and acknowledgment barrier; the crash table covers each boundary. `docs/SYSTEM.md:785` binds both stores and deduplication. `docs/SYSTEM.md:1576` excludes private text from client command persistence. |
| 6 | fixed | `docs/SYSTEM.md:1156` defines independent DEKs and wrapping keys, with no wrapping keys in backups. `docs/SYSTEM.md:1169` restores a pre-deletion backup: deleted A cannot unwrap, retained B can, stale vault restore is refused. `docs/DEPLOYMENT.md:403` uses the same design. This is the required implementation test, not a claim that a vault implementation was exercised in this documentation task. |
| 7 | fixed | `docs/SYSTEM.md:1435` limits helpers to task fields and the configured recent-reading/name-only summary; officers need explicit sharing. `docs/SYSTEM.md:1442` defines trigger, scope, expiry and audit with no general break-glass chart grant. Matches package PRD 144 to 147 and CONTRACTS 924. |
| 8 | fixed | `docs/DEPLOYMENT.md:271` applies recipient/consent/field policy across transports. `docs/DEPLOYMENT.md:275` gives authorized projections their own contiguous delivery sequence; excluded rows do not leak or stall ACKs. `docs/DEPLOYMENT.md:295` limits ground payloads to approved snapshots/catch-up. |
| 9 | fixed | `docs/SYSTEM.md:1305` and `docs/SYSTEM.md:1336` define quantities, each skill and operator facts. `docs/SYSTEM.md:1393` implements per_use; the following rows implement fresh-or-due holders, package availability precedence, who_can and fully checked substitutes. Unknown never enables execution. |
| 10 | fixed | `docs/SYSTEM.md:663` includes target and parameter digest. `docs/SYSTEM.md:693` defines normalization and mismatches across cache/ledger; `docs/SYSTEM.md:700` requires current authentication and result visibility before replay. |
| 11 | fixed | `docs/SYSTEM.md:552` persists ID, times, full scope, pinned release, correlation, payload and audience. `docs/SYSTEM.md:649` defines exact generated Event mapping and immutable retry behavior; no reconstruction from current state. |
| 12 | fixed | `docs/SYSTEM.md:808` and `docs/SYSTEM.md:830` cover every stored authoritative field, sequence and timestamp, with canonical UTF-8 and domain-separated hash. Verification checks contiguous links, indexed-column equality, trusted head and restore incarnation. |
| 13 | fixed | `docs/SYSTEM.md:248` defines apply/history/normalize/elapsed; `docs/SYSTEM.md:284` defines tick/observe, bounds and atomic high water. `docs/SYSTEM.md:292` defines version selection, epoch/day boundaries and audited retrospective rebuilds; 5.2 covers both jumps, uncertainty, future input and REPLAY. |
| 14 | fixed | `docs/SYSTEM.md:848` and `docs/SYSTEM.md:852` distinguish timer classes. `docs/SYSTEM.md:896` leaves contact/expiry instants fixed; `docs/SYSTEM.md:888` defines replacement, cancellation and atomic effects/completion with crash-safe generations. |
| 15 | fixed | `docs/SYSTEM.md:1271` returns schema-valid queued GroundMessage in the press transaction, with immutable text/hash/revision, required catch_up/leaves_at and snapshot fallback. Missing watch/advice facts are configured unknown lines; no composition_pending wire property. Preserves E26 snapshot behavior without allowing unapproved content enrichment. |
| 16 | fixed | `docs/SYSTEM.md:1206` through `docs/SYSTEM.md:1236` define the single E32-owned public batch seam: partition, cause, source refs, quantities, nullable cover, display and last-use fields, revision, pin and bounded snapshot paging. E24 imports it; local variants are retired. |
| 17 | fixed | `docs/SYSTEM.md:366` accepts explicit PressureContext and preserves conversion provenance. `docs/SYSTEM.md:376` gives formula and rounding; `docs/requests/COORDINATOR-DECISIONS.md:26` and `docs/requests/SYSTEM.md:201` define the sourced mapping release and effective schema. |
| 18 | fixed | `docs/DEPLOYMENT.md:580` preserves endpoint roles and D8 token checking. Open sessions/runs do not block activation and keep pins; officer status alone gives no activation permission. Package openapi.yaml 885 remains the role authority. |
| 19 | fixed | `docs/SYSTEM.md:951` specifies declarations before resolution, missing-port/cycle checks and complete loader inventory. `docs/SYSTEM.md:1676` makes the early rule explicit; section 9 includes all consumers and section 10 waves were recomputed and checked for all 34 deliverables. |
| 20 | fixed | `docs/SYSTEM.md:515` replaces sqlite3.Connection. `docs/SYSTEM.md:161` restricts pure imports; assembly/FastAPI stays outside kernel. `docs/SYSTEM.md:1067` makes import contracts convention-based, with no shared-file edit for a new epic. Extracted signatures pass strict mypy; a runtime module-template gate remains implementation work. |
| 21 | fixed | `docs/SYSTEM.md:643` makes local replication append and sequence allocation atomic. `docs/DEPLOYMENT.md:246` defines new restore incarnations, writer/custody fencing and ledger reconciliation before old-command retries; the sequence-80/100 recovery cannot reuse identity. Unknown post-backup history stays visible. |
| 22 | fixed | `docs/SYSTEM.md:1548` defines transfer records, epochs and prepared/fenced/accepted/completed phases. It forbids timeout promotion, requires destination acceptance before departure, checks each mutation/timer epoch and names manual/printed backups for the interrupted handover gap, with fault tests at every phase. |
| 23 | fixed | `docs/DEPLOYMENT.md:490` defines native loopback, private container DNS/listeners, credentials, Linux policy and the macOS socket gateway with native fallback. Each profile has authenticated connectivity and external-port isolation checks. `docs/SYSTEM.md:48` now points to that distinction. |
| 24 | fixed | `docs/DEPLOYMENT.md:534` enumerates bootstrap, Python, uv and native dependencies; following bullets specify per-platform libraries, native Metal/llama and forecast runtimes. Clean install, upgrade and rollback run offline with empty caches; absent optional models leave the hub usable. |
| 25 | fixed | `docs/requests/COORDINATOR-DECISIONS.md:25` supplies sourced bundle_lifetime. `docs/DEPLOYMENT.md:345` retains approved content, re-envelopes with stable application identity, rechecks consent, preserves priority and bounds retries; absent lifetime never becomes a code default. |
| 26 | fixed | `docs/requests/SYSTEM.md:1` files R-SYS-1 through R-SYS-21 with target/proposal/reason/interim. `docs/SYSTEM.md:1758` only references them. `docs/SYSTEM.md:902` uses existing Health.status for a general station fault notice and keeps evaluator/since in diagnostics until the extension, without extra JSON fields. |

## Package issues

| Issue | Status | Evidence |
|---|---|---|
| P1 | fixed | `docs/requests/COORDINATOR-DECISIONS.md:57` records each superseded package line separately, including CONTRACTS 531/1333/1460/1462, PRD 528, GuidedCheck schema 413, V86, the disagree example and backlog acceptance. `docs/requests/COORDINATOR-DECISIONS.md:26` requires an effective decision overlay and base/effective guards; Q8 preserves both readings and Q11/Q14 retain engine gates. Source package files are intentionally not patched by this author. |
| P2 | fixed | `docs/requests/COORDINATOR-DECISIONS.md:22` defines a strong response-header ETag with object revision and activation generation. `docs/SYSTEM.md:484` gives exact transactional interfaces; `docs/requests/SYSTEM.md:183` specifies create/detail/activate headers and examples. Closed ConfigRelease JSON is retained, so no undeclared revision property is needed. |
| P3 | fixed | `docs/requests/COORDINATOR-DECISIONS.md:23` centrally denies station clinical runs and narrows officers to explicit grants. `docs/SYSTEM.md:1435` applies this to reads/events/caches; A3 names openapi.yaml 6593/6597 and detail/query lines. `docs/requests/SYSTEM.md:192` carries propagation and privacy acceptance. |

The package owner still needs to propagate accepted changes into the living package, effective generators, examples
and assertions. That is implementation/ownership follow-through, not an unresolved policy decision. No builder may
silently relax a schema to match a local design. Unchanged assistant dose-generation prohibitions still apply; the
exception is the deterministic medicine engine and authorized human confirmation. Names-only SubjectSummary remains
names only. Review labels do not create clinical validation.

## New decisions and remaining ownership

D8 settles release ETags; D9 station and officer clinical-plan access; D10 crew-goal consent; D11 bundle lifetime and
renewal; D12 effective decision overlays and sourced UCUM mappings; D13 durable capture under typed evaluator failure.
No existing decision was reversed, reworded or reopened. No new owner-only product choice is required. Pending
extensions and their disabled/interim behavior are explicit in the request file, notably FHIR mappings, HTTP sync,
location contracts, detailed health notices and release provenance. Their owners must implement and validate them.

## Verification

- Compared original and revised heading inventories: all existing SYSTEM and DEPLOYMENT section numbers survive;
  added subsections only, no duplicate numbers.
- Parsed all 16 Python blocks and checked annotations on all 112 functions/methods. Extracted them into a temporary
  `.pyi` with standard-library imports and stubs for external generated enums/router/envelope. Ran
  `.venv/bin/python -m mypy --strict --cache-dir /tmp/system-r1-mypy-cache /tmp/system-r1-signatures.pyi`: passed.
  This checks signature consistency, not runtime implementations or a generated production module template.
- Computed the dependency DAG and earliest waves independently: all 34 deliverables (33 epics plus E27-guidance)
  match the published table; no cycle. Guidance is a prerequisite for every required consumer.
- Checked all 253 A3 package-line references exist and are unique; every R-SYS entry has its required five fields.
  Existing decision rows and the complete Part B compare byte for byte with the initial snapshot.
- Scanned all written documents: no local user absolute paths or em/en dashes. The two spaced subtraction expressions
  in untouched Part B are mathematics, not punctuation dashes.
- Ran `.venv/bin/python scripts/design_lint.py docs/SYSTEM.md docs/DEPLOYMENT.md --json`: **not green**, 37 errors and
  29 warnings. Eighteen errors demand epic-template sections/titles that these shared documents do not use; nineteen
  reference preexisting unfiled cross-epic request IDs. The warnings are internal Literal sets without a wire-schema
  binding. The linter was not modified and section numbers were not distorted to satisfy its epic template. Owners
  must file those legacy requests; a shared-document lint mode needs its own authorized change. No error is hidden.
- The first lint attempt with system Python could not import PyYAML; the repository virtual environment supplied the
  installed dependencies. No dependency was downloaded. No application, hardware, container, vault, CI or fault suite
  was run for this documentation-only change; their required checks are specified at the corrected seams.
- Read-only package hashes detected concurrent changes to KICKOFF.md, PITCH.md and README.md outside this author's
  edits. Those files were neither written nor restored here; cited package lines were checked against the available
  files. The architecture package changes remain documented in A3, not applied to package files by this run.
- AI-USE-LOG.md was not written because the user's explicit scope permits only five files. This response records the
  run: 2026-11-14, Codex, shared architecture review resolution, five authorized documents, checks and limitations above.
  The owning coordinator can copy that record into the repository log.
