# Build charter

Binding for every agent and person building Crew Health Check. The planning package at
`docs/product/` is the product source of truth (PITCH.md wins on story and demo,
PRD.md on product behavior, CONTRACTS.md and `api/openapi.yaml` on shared objects and endpoints, USE-CASES.md on flows).
This charter decides how it is built. Where the two disagree on a product question, the package wins and the
disagreement is filed as a request (section 7).

## 1. What we are building

A real crew health system, not a demo shell: it must be deployable on a spacecraft, station, surface base or rover,
and it must be fully usable with the minimum setup of **one laptop and its webcam**. Everything beyond that (more
cameras, crew tablets, a pulse oximeter, a blood pressure cuff, a thermometer, a wrist band, habitat sensor feeds, the
local medical model, the forecasting model, a ground link) adds capability; none of it is required for the system to
work. The deployment architecture (where the hub runs, which clients exist, how nodes sync, how the ground link works)
is specified in `docs/DEPLOYMENT.md`, written from the research in
`crew-health-build-2026-11-14/research/`.

Non-negotiables:

1. **No hardcoded data.** Every label, threshold, cadence, step, effect, citation, environment, sensor, plan, skill,
   role rule, supply, unit and model setting comes from versioned configuration documents with provenance (CONTRACTS.md
   section 11) or from backend state served through the API. Code holds mechanisms, never content. A configuration
   edit changes behavior with no code change; a test proves it for every module.
2. **Safety functions never depend on optional parts.** Someone needs help, rule and sensor warnings, routine misses
   and record entry work with the language model stopped, every device absent and the ground link down (PRD section
   11 targets).
3. **The assistant can never raise or lower safety state.** It cannot raise "Act now" or "Emergency", and it cannot
   lower or clear a rule or sensor warning. Enforced in the domain layer and asserted by tests, never by prompts.
4. **Nothing fabricated.** Missing, stale, unavailable and simulated data are shown as such (LIVE, REPLAY, SIM);
   insufficient samples stay insufficient; no gap is interpolated silently.
5. **Records are durable.** An accepted record survives crash, restart and retry; commands are idempotent; the audit
   trail is append-only and hash chained.

## 2. Stack, by component

Chosen for reliability, offline operation, auditability and alignment with what NASA uses for the same job.

| Component | Stack | Why |
|---|---|---|
| Hub (records, rules, workflows, adapters) | Python 3.14, FastAPI, Pydantic v2 models generated from the contracts, SQLite (WAL, STRICT tables, foreign keys on) through typed repositories, `uv` workspace | One process, no server to administer; SQLite is tested to aviation-grade branch coverage and runs anywhere a crew laptop runs; Python matches NASA ground and science tooling and the existing potency engine |
| Medicine potency engine (E32) | The existing `pharmacy` package in this repository | Built to its specification and checked against published flight and stability data; open findings are listed in docs/VALIDATION-REPORT.md. Its numbers are experimental estimates, not clinical authorization |
| Crew app | TypeScript (strict), Vue 3, Vite, installable as an offline web app; the visual system of `docs/product/design/interaction-prototype.html` (its tokens, type and point-cloud body renderer) reproduced exactly | Runs in any browser on the laptop or a crew tablet with nothing installed; Vue is the framework of NASA Open MCT, so components and telemetry views share one framework |
| Habitat telemetry views | NASA Open MCT (Apache 2.0) plots and tables fed by the hub's telemetry endpoints (historical and realtime) | Open MCT is NASA's mission operations framework for exactly this job |
| Ground console | An Open MCT application with Crew Health Check plugins, read only | The ground reads; it is never a step (USE-CASES rule 7) |
| Ground link | Store-and-forward bundles following DTN Bundle Protocol version 7 (RFC 9171, CBOR); transports are adapters: file drop (removable media), a local DTN node (µD3TN or dtn7-rs; never HDTN, which is archived) when present, as decided in docs/DEPLOYMENT.md, plain HTTPS on Earth | DTN is NASA's protocol for delayed and disrupted links and runs on the ISS; the app never waits for delivery |
| Medical devices | Bluetooth SIG health profiles (Pulse Oximeter, Blood Pressure, Health Thermometer, Heart Rate) through `bleak`, with IEEE 11073 semantics; manual entry for every device as the always-available fallback | Standard profiles mean any compliant kit device works without custom drivers |
| Cameras | A Python worker on the device that holds the camera: OpenCV capture, face and torso landmarks, rPPG (POS and CHROM) and motion-based breathing, with a signal-quality gate that refuses rather than guesses | Frames never leave the device that captured them |
| Clinical data interchange | HL7 FHIR R4 4.0.1 resources (R4 because the device and patient summary guides build on it; docs/SYSTEM.md) (Observation, Device, Condition, CarePlan, MedicationStatement) with LOINC codes and UCUM units, as an export and import format; internal records follow CONTRACTS.md | The standard health data model; lets a real medical data architecture ingest our records |
| Local medical model | HuatuoGPT-3-9B served by `llama.cpp` (`llama-server`, OpenAI-compatible API) in its own process, behind the typed tool gateway; quantization and the "weights unchanged" rule are decided and documented in the E17 TDD | Portable C++ runtime that runs on any laptop CPU or GPU, isolated so it can be stopped without affecting anything else |
| Forecasting | Chronos-2 in an isolated worker (PyTorch, CPU or Apple GPU), experimental view only | Never in a safety path |
| Knowledge and evidence | SQLite FTS5 plus a local vector index (`sqlite-vec`), files addressed by content hash | Same storage engine as the hub, nothing extra to run |
| Packaging | One native command for the minimum setup (`uv run crew-health`, serving the built crew app); OCI images (Docker or Podman) with compose profiles `minimal`, `crew`, `full` and `ground` for multi-node and CI | The minimum setup must not require containers |
| CI | GitHub Actions: lint, types, tests, coverage, contract tests, configuration validation, requirement traceability report, SBOM (CycloneDX), dependency audit, container builds, end-to-end on the demo fixture | Evidence for every gate is produced by the pipeline, not by hand |

## 3. Architecture rules

1. **Clean architecture per module.** Each bounded context (CONTRACTS.md section 20 "Modules") is a package with four
   layers: `domain` (pure types and rules, no I/O, no framework imports), `application` (use cases and ports, which are
   Python protocols), `adapters` (SQLite repositories, devices, model and transport clients) and `api` (FastAPI routers
   and schemas). Dependencies point inward only. `import-linter` contracts enforce it in CI.
2. **Modules talk through ports and events.** A module never reads another module's tables or imports another
   module's adapters. Cross-module calls go through the other module's application ports; facts are announced on the
   event stream (CONTRACTS.md section 18).
3. **Contracts first.** `contracts/` holds the OpenAPI document and JSON Schemas copied verbatim from the package, with a
   SHA-256 guard test. Pydantic and TypeScript types are generated from them; nobody hand-writes a shared type. A
   change to a contract is a request (section 7), approved by the coordinator, then regenerated.
4. **Determinism.** Arithmetic, units, baselines, aggregations, thresholds and decisions are deterministic code with
   tests. The language model only drafts wording, asks the next question and calls typed tools; its outputs are data,
   checked by the domain before anything is recorded.
5. **Time.** UTC everywhere, an injected clock, mission elapsed time derived from the mission clock document; no wall
   clock read inside domain code.
6. **Errors.** Typed errors per module mapped to RFC 9457 problem details at the API; no bare exceptions crossing a
   layer; no NaN, infinity or silent default returned.
7. **Every optional part has a declared fallback** (section 5), exercised by tests.

## 4. Quality bar (NASA practice, adapted to a hackathon codebase)

The intent is NPR 7150.2D practice for software that informs crew health decisions (treated as safety relevant),
with NASA-STD-8739.8B assurance and NASA-STD-7009B credibility evidence for models. Concretely:

- Bidirectional traceability: every requirement (R01 to R62), validation scenario (V01 to V98) and demo assertion (D01
  to D44) maps to tests through pytest markers or test IDs; CI produces the matrix and fails on an untested
  requirement in a built epic.
- `mypy --strict` and TypeScript `strict`; `ruff` check and format; `eslint` and `prettier` for the UI.
- Branch coverage at least 95% on `domain` and `application` code; mutation testing on the critical paths of section 6.
- Property-based tests (Hypothesis, fast-check) for every rule engine and parser.
- Contract tests: the running hub against `contracts/openapi.yaml` (Schemathesis); the UI against recorded API
  examples.
- Static rules in the spirit of the JPL Power of Ten: bounded loops and queues, timeouts on every external call and
  subprocess, no unchecked return values, no recursion in safety paths, assertions on preconditions.
- Every finding in review gets a verdict; nothing is waved through.

## 5. Fallback ladder

| Missing part | What still works | How it shows |
|---|---|---|
| Language model | Every guided flow, Someone needs help, warnings, routine, kit map, onboard plans, teach and check, records | The assistant panel says it is off; questions come from the configured question sets |
| Camera or poor signal | Manual entry of pulse and breathing; the rest of the check | "Not measured: signal quality too low" with the reason; never a guessed value |
| Pulse oximeter, cuff, thermometer, band | Manual entry, or the kit map shows the measurement cannot be made here | The kit map counts change from configuration and attached devices, live |
| Habitat sensor feed | Manual readings, REPLAY of NASA telemetry or SIM, each labeled | Stale and offline channels are never shown as current or safe |
| Ground link | Everything; the ground log queues | The queue size and last contact are shown; nothing waits |
| Crew tablets | The laptop is a complete station | |
| Forecasting model | Trends with deterministic baselines | The second view is absent, labeled |

## 6. Critical paths (tests are intentional, not decorative)

Someone needs help first screen in 500 ms with the model stopped and under load; warning levels never raised or
lowered by the assistant; stale or offline readings never shown as current; accepted records never lost across crash
and retry, idempotent commands; no cross-crew, journal or unauthorized report leakage; no executed unauthorized tool
action and no fabricated accepted measurement; routine misses never filled in; configuration edits change behavior with
no code change; the ground log is complete after any outage. Each has named tests in the epic that owns it and an
end-to-end scenario in `eval/`.

## 7. Process

1. **Technical design first.** Each epic gets a TDD in `docs/tdd/Exx.md` from its brief
   (`tdd/Exx.md` in the package), the PRD, CONTRACTS.md and this charter, using `docs/tdd/TEMPLATE.md`.
2. **Cross-model review.** The model that did not write the TDD reviews it (Opus writes, GPT-6 Astra reviews, or the
   reverse) and returns either "greenlight" or numbered comments. The author resolves every comment or answers it with
   evidence. Questions only the user can answer go to the coordinator, who asks the user.
3. **Build.** The builder implements the greenlit TDD inside its owned files only, test first on the critical paths.
4. **Cross-model build review.** The other model reviews the code cold (artifact and evidence, not the builder's
   rationale), default to reject, then the builder fixes.
5. **Greenlight** by the coordinator when the epic's gate commands pass (section 8), the review is resolved and the
   backlog is updated.
6. **Requests.** Anyone who needs a contract, configuration shape, charter or package change writes the exact proposal
   and the reason in `docs/requests/<epic or role>.md`, picks the least surprising interim behavior inside the existing
   contract, and reports it. Nobody edits files they do not own.
7. **AI use log.** Every agent run that produces work appends one row to `AI-USE-LOG.md` (KICKOFF.md section 8 format).

## 8. Gate commands (per epic, run by the coordinator before greenlight)

```
uv run python scripts/validate_contracts.py          # all configuration documents and contract copies
uv run mypy --strict <owned packages>
uv run ruff check . && uv run ruff format --check .
uv run lint-imports                                  # layering contracts
uv run pytest -q <owned tests> --cov --cov-branch    # 95% branch on domain and application
pnpm -C ui/crew typecheck && pnpm -C ui/crew test    # when the epic owns UI
uv run pytest -q eval -m "<epic scenarios>"          # its V and D scenarios
```

## 9. Writing rules

No em or en dashes and no spaced hyphen used as a dash, anywhere (code comments, docs, UI copy). No revision history
inside documents. English everywhere.
