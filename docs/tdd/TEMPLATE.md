# Exx: <epic title>

Technical design for epic Exx. Read first: `AGENTS.md`, `docs/SYSTEM.md`, `docs/DEPLOYMENT.md` and
`docs/requests/COORDINATOR-DECISIONS.md`. Then the brief (`docs/product/tdd/Exx.md`), PRD.md, USE-CASES.md,
CONTRACTS.md, `api/openapi.yaml`, the epic's work packages in `backlog.json` (all under `docs/product/`) and
`docs/BUILD-CHARTER.md`. Use SYSTEM.md's shared mechanisms by section number and never define your own. Cite package
sections and requirement IDs instead of restating them. Write "None" under a section that fixes no decision. Before
hand-in, run `scripts/design_lint.py docs/tdd/Exx.md` (it must pass) and check the design against every item of
`docs/tdd/REVIEW-CHECKLIST.md`, the list the reviewer will use: anticipate the review instead of waiting for it.

## 1. Scope

Work packages (story IDs), requirements (R), validation scenarios (V), demo assertions (D) and use cases (UC) this epic
delivers, and what it explicitly does not deliver (with the epic that does).

### 1.1 Rule trace

Every sentence containing must, never, only, always or "writes no" in the CONTRACTS.md sections this epic owns, and
the PRD requirements it delivers: one row each with the source line, the design section that implements it and the
test ID. A rule the design cannot meet is a request (section 14), never silence.

## 2. Seams

A table, one row per port, method or event this epic provides or consumes: name, provider epic, consumer epics,
signature or payload with types (units as Quantity), and the SYSTEM.md section that defines it. Names match SYSTEM.md
and the provider's table exactly. A seam SYSTEM.md does not cover goes to the coordinator before you design around it.

## 3. Domain model

Entities, value objects, state machines and invariants (pure code, no I/O), mapped to CONTRACTS.md objects and
states. Every invariant gets a test ID in section 12.

### 3.1 Time

The clock used (SYSTEM.md 5.2), mission time (5.3), ordering of delayed, duplicate and out of order events, and the
behavior on a clock correction. Freshness is computed from when a value was observed, never from when it arrived.

## 4. Application layer

Use cases (inputs, outputs, errors, idempotency key), ports as Python protocols with exact signatures, events
published and consumed (CONTRACTS.md section 18 names and payloads). For each command: its unit of work and what
survives a crash at each step (SYSTEM.md 5.7, 5.8).

## 5. Adapters

Storage (tables, columns, constraints, indexes, migrations), devices, model or transport clients, workers; timeouts,
retries and bounds on every external call, as named hub settings (SYSTEM.md 5.11).

## 6. API

Endpoints by `operationId` from `api/openapi.yaml`, request and response schemas, errors (SYSTEM.md 5.5), auth and
role checks, idempotency. Any endpoint or field the contract lacks is a request (section 14), not an invention.

## 7. User interface

Screens and components (or "None"), the endpoint each binds to (CONTRACTS.md section 19), every state (loading,
empty, error, stale, offline, LIVE, REPLAY, SIM, unavailable), accessibility, and proof that no content lives in
client code.

## 8. Configuration

Configuration documents read (CONTRACTS.md section 11), their schemas and loader checks, the provenance fields, and
the test that a configuration edit changes content with no code change (never a safety mapping).

### 8.1 Activation effects

For each document read: what happens to open sessions, active warnings, stock and scheduled work when a new release
activates or a definition disappears. An activation never clears or lowers an active warning.

## 9. Deployment and fallbacks

Behavior on each deployment tier of DEPLOYMENT.md (minimum: one laptop and its webcam), and the degraded mode for each
missing optional part (charter section 5).

## 10. Safety, security and privacy

Hazards (what can harm a crew member or mislead them), causes, mitigations and the test for each; authorization rules
(SYSTEM.md 8.3, 8.4); private data handling; what the assistant may and may not do in this epic.

### 10.1 Failure and edge cases

A table with one row per item of the "Failure and edge cases" list in `docs/tdd/REVIEW-CHECKLIST.md`: the behavior of
this epic in that case and the test ID, or "None" with the reason it cannot happen here.

## 11. Performance and resources

A bounds table: every queue, cache, batch, retry and timeout, with its hub setting name and limit. Latency and memory
budgets on the 16 GB laptop; how background work yields to interactive requests.

## 12. Test plan

Critical paths first. A table: test ID, level (unit, property, contract, integration, end to end), what it proves,
the R, V, D or invariant it traces to. Fixtures are built from configuration, never hand-typed content.

### 12.1 Acceptance matrix

Each V and D scenario, backlog work package and R requirement of this epic, with the test ID that covers it.

## 13. File ownership

Exact paths this epic creates and owns in the repository (disjoint from every other epic), and the files it reads.

## 14. Requests and open questions

Requests are filed in `docs/requests/Exx.md` (one entry each: target, exact proposal, reason, interim) and only
referenced here. Each interim lists the safety checks and validations it keeps; an interim that would drop one leaves
the feature off instead. Questions already decided in COORDINATOR-DECISIONS.md are not asked again.

## 15. Build plan

Ordered steps, each with its done criterion, and the gate commands (charter section 8) for this epic.
