# Exx: <epic title>

Technical design for epic Exx. Inputs: the brief (`tdd/Exx.md` in the planning package), PRD.md, USE-CASES.md,
CONTRACTS.md, `api/openapi.yaml`, the epic's work packages in `backlog.json`, `docs/BUILD-CHARTER.md`,
`docs/SYSTEM.md` and `docs/DEPLOYMENT.md` (when present). Every section is required; write "none" with a reason when a
section does not apply. Cite package sections and requirement IDs instead of restating them.

## 1. Scope

Work packages (story IDs), requirements (R), validation scenarios (V), demo assertions (D) and use cases (UC) this epic
delivers, and what it explicitly does not deliver (with the epic that does).

## 2. Dependencies and seams

Epics and modules it depends on and that depend on it; for each seam, the port, event or endpoint and who owns it.

## 3. Domain model

Entities, value objects, state machines and invariants (pure code, no I/O), mapped to CONTRACTS.md objects and
states. Every invariant gets a test ID in section 12.

## 4. Application layer

Use cases (inputs, outputs, errors, idempotency key), ports as Python protocols with exact signatures, events
published and consumed (CONTRACTS.md section 18 names and payloads).

## 5. Adapters

Storage (tables, columns, constraints, indexes, migrations), devices, model or transport clients, workers; timeouts,
retries and bounds on every external call.

## 6. API

Endpoints by `operationId` from `api/openapi.yaml`, request and response schemas, problem-detail error types, auth and
role checks, idempotency. Any endpoint or field the contract lacks is a request (section 14), not an invention.

## 7. User interface

Screens and components (or "none"), the endpoint each binds to (CONTRACTS.md section 19), every state (loading, empty,
error, stale, offline, LIVE, REPLAY, SIM, unavailable), accessibility, and proof that no content lives in client code.

## 8. Configuration

Configuration documents read (CONTRACTS.md section 11), their schemas and loader checks, the provenance fields, and
the test that a configuration edit changes behavior with no code change.

## 9. Deployment and fallbacks

Behavior on each deployment tier of DEPLOYMENT.md (minimum: one laptop and its webcam), and the degraded mode for each
missing optional part (charter section 5).

## 10. Safety, security and privacy

Hazards (what can harm a crew member or mislead them), causes, mitigations and the test for each; authorization rules;
private data (journal, reports) handling; what the assistant may and may not do in this epic.

## 11. Performance and resources

Latency and memory budgets on the 16 GB laptop, bounded queues, background work and how it yields to interactive
requests.

## 12. Test plan

Critical paths first. A table: test ID, level (unit, property, contract, integration, end to end), what it proves, the
R, V, D or invariant it traces to. Fixtures and how they are built from configuration, never hand-typed content.

## 13. File ownership

Exact paths this epic creates and owns in the repository (disjoint from every other epic), and the files it reads.

## 14. Open questions and requests

Numbered. Questions only the user can answer are marked "USER". Contract, configuration-shape or package changes are
written as exact proposals with the reason and the interim behavior.

## 15. Build plan

Ordered steps, each with its done criterion, and the gate commands (charter section 8) for this epic.
