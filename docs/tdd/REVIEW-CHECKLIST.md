# Design checklist

One list, used twice: the author checks a design against every item before hand-in, and the cross-model reviewer
checks it again with evidence. A defect the reviewer finds under an item below is one the author was expected to
foresee. The review exists to catch what the author could not foresee, not what the author did not look for.

For each item, the design either answers it (section and test ID) or says "None" with the reason.

## Coverage

- Every work package of the epic in `docs/product/backlog.json`, every requirement (R), validation scenario (V) and
  demo assertion (D) it owns is delivered, with a test ID in the acceptance matrix (12.1), or explicitly assigned to
  another epic that the package agrees owns it.
- Every must, never, only, always and "writes no" sentence of the CONTRACTS.md sections the epic owns is in the rule
  trace (1.1) and actually implemented.

## Contract fidelity

- Every endpoint (operationId), field, enum value, state, event name (CONTRACTS.md section 18), role and configuration
  document (section 11) exists exactly as written in `docs/product/`.
- Anything the contract lacks is a request filed in `docs/requests/<epic>.md`, never silently used.
- Nullable and optional fields are handled as nullable; required fields are always written.

## Shared mechanisms

- Identifiers, clock, mission time, units, errors, configuration loading, unit of work, events, idempotency, audit,
  scheduler and settings follow SYSTEM.md by section; the design defines none of its own.
- Both sides of every seam agree: names, signatures, payloads and which side owns the transaction match SYSTEM.md
  section 8 and the other epic's current design.
- Every decision in `docs/requests/COORDINATOR-DECISIONS.md` Part A that applies to the epic is applied.

## No hardcoded data

- Labels, thresholds, cadences, steps, units, citations and model settings come from configuration documents or
  backend state; queue sizes, timeouts, batch sizes and retries are named hub settings (SYSTEM.md 5.11).
- No client code holds content.

## Safety

- Safety functions work with the model stopped, devices absent and the ground link down.
- The assistant never raises Act now or Emergency and never lowers or clears a rule or sensor warning, enforced in
  the domain layer, not only in prompts or UI.
- Stale, offline, REPLAY and SIM data are never shown as current; insufficient samples stay insufficient; nothing is
  fabricated or interpolated silently.
- No interim drops a safety check, a validation or an acceptance criterion; if nothing safe is allowed, the feature
  stays off and says so.
- Nothing on board waits for a ground role (flight surgeon, pharmacist, clinician).

## Failure and edge cases

Each has a defined behavior and a test:

- Crash or power loss at each step of every command: what survives, what is retried, what the user sees.
- Duplicate and retried submissions, and the same key sent to a different resource.
- Clock jumps forward and backward, mission elapsed time, delayed and out of order events.
- Disk full, corrupt or missing configuration, a configuration release that removes a definition in use.
- Partial, implausible or disagreeing device readings.
- Concurrent edits from two clients or two crew members.
- A long ground link outage and a backlog arriving at once.
- A crew member who is incapacitated, absent or cannot use the screen.
- Privacy between crew members: who can read what, including helpers, station sessions, exports, logs and backups.

## Architecture and tests

- Clean layering; cross-module access only through ports and events (SYSTEM.md 6.4); bounded queues and timeouts.
- Tests are intentional, critical paths first, traced to R, V, D or an invariant; fixtures come from configuration;
  property tests for rule engines and parsers.

## Ownership and buildability

- Section 13 paths do not overlap any other epic's.
- A builder could implement the design without guessing: exact types, states, errors and done criteria.
