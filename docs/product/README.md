# Crew Health Check: planning package

**Measures what it can. Shows what it can't. Teaches the crew the rest.**

NASA Space Apps Challenge 2026 · Challenge: Create Health Monitoring Software for Astronauts on Space Missions · Event: 14 and 15 November 2026

On a long mission there is no doctor aboard, and Earth may be out of reach for a day or more. Crew Health Check is an offline, all-in-one health dashboard for the crew: the mission routine with plain how-to steps, the habitat's sensors read against their limits on a station, in deep space, on the Moon or on Mars, warnings that say who raised them, and a "Someone needs help" button on every screen. A short daily check, cameras that read pulse and breathing, a wrist band and the medical kit build each person's own baseline. When something feels wrong, a medical AI running on the same laptop asks one question, takes the next measurement and says plainly what this kit cannot test. Then it teaches a crewmate to check it another way, step by step on the body figure, with the cameras checking their work, and follows the plan aboard for that condition. The ground is a reader, not a step: it gets the log at the next contact, and nothing waits for it. It is built on NASA's list of 119 medical conditions modeled for exploration missions.

**The loop:** notice → ask → measure → check it another way → act on the plan aboard.

This package is the working plan for the event, covering the expanded assessment system, research tooling and evaluation ([PRD](PRD.md), single source of truth). HuatuoGPT-3-9B is the model, used with its existing medical adaptation and no additional weight training. If a document disagrees with [PITCH.md](PITCH.md) about the story, the demo or what we show, PITCH.md wins.

## Start here

1. [PITCH.md](PITCH.md): the front door. The pitch, what a judge sees, the demo story, the 30-second video (shot list and build priority in [DEMO-PLAN.md](DEMO-PLAN.md)), the seven slides, the project page fields and the rules that bind the demo.
2. [KICKOFF.md](KICKOFF.md): how the team starts on Saturday at 09:00.
3. [PRD.md](PRD.md): what we build. How it answers the challenge, the NASA data used, the experience, 62 requirements, scope and stages.
4. [USE-CASES.md](USE-CASES.md): the binding design for every flow: seven rules (the seventh: the ground is a reader, never a step), the actors (the Helper and the sensor monitor among them), the four environments, the warning levels, the routine rules and use cases UC01 to UC26.
5. [design/interaction-prototype.html](design/interaction-prototype.html): the visual direction as a working prototype, with the point-cloud body map, Today, Habitat, Routine, warnings and Someone needs help, in a six-act walkthrough.
6. [condition-browser.html](condition-browser.html): NASA's 119 modeled conditions, with what would confirm each one and whether this kit can.
7. [DEMO-CONDITIONS.md](DEMO-CONDITIONS.md): the worked condition entries the demo relies on.
8. [NASA-SOURCES.md](NASA-SOURCES.md): every NASA document and dataset from the research: the ones the product uses and what each does, the NASA data in [nasa-data/](nasa-data/eda_rr1_telemetry.csv), and the full catalog of 580 documents.

Every Markdown document also has a generated web version, linked from [the reading guide](index.html).

## Main documents

- [PRD](PRD.md): product behavior, how it answers the challenge, NASA data used, 62 requirements, scope and stages.
- [Use cases](USE-CASES.md): the seven rules, actors, environments, warning levels, routine rules and use cases UC01 to UC26 that every flow follows.
- [Environments](ENVIRONMENTS.md): each mission environment (space station in low Earth orbit, deep-space transit, Moon base, Mars base), its hazards, its sensors, their sourced limits and adverse effects.
- [Routine](ROUTINE.md): every routine measurement and task, how often and why, with plain steps and what makes the app ask more often.
- [Responder flows](RESPONDER-FLOWS.md): what Someone needs help shows, step by step, for each situation; every flow runs to its end with the ground link down, and its last row is the log for the ground.
- [LLM architecture](LLM-ARCHITECTURE.md): local reasoning, 23 tools, evidence database, orchestration and HealthBench evaluation.
- [UX design](UX-DESIGN.md): visual direction, screens, flows, copy and error states.
- [Contracts](CONTRACTS.md): identities, records, states, API intents and invariants, including configuration documents, environments, sensors, warnings, routine, Someone needs help, the kit map, the onboard plans, the skills catalog, role rules, the ground link and the screen to endpoint map. The endpoints are in `api/openapi.yaml`.
- [Research annex](RESEARCH-ANNEX.md): source of truth, source recovery, NASA inventories, clinical evidence, model research, the official 2026 rules and documentation maintenance.
- [Validation](VALIDATION.md): 98 integration scenarios (V01 to V98: Earth independence, the kit map, participation, the body map, environments, sensors, warnings, routine, Someone needs help and the no-hardcoded-data rule among them) and the demo fixture `demo-mara-fd19` with 44 assertions (D01 to D44) across six acts, with the ground link down from act 3, clinical review and benchmark reporting.
- [TDD briefs](tdd/README.md): 33 epic briefs for subsequent technical design work.

## Structured planning artifacts

- `backlog.csv` / `backlog.json`: 120 work packages across 33 epics (`epics.json` lists the epics, their dependencies and requirements).
- `environment-hazards.json`: the machine-readable environments and hazards behind ENVIRONMENTS.md, with effects, body regions, sourced levels and source IDs.
- `routine-schedule.json`: the machine-readable routine behind ROUTINE.md, with cadences by environment, triggers, source IDs and the skills each drill practices.
- `responder-flows.json`: the machine-readable responder flows behind RESPONDER-FLOWS.md.
- `research/`: the saved source texts for the environment, routine and responder research.
- `api/`: the endpoint contract: OpenAPI 3.1 in `api/openapi.yaml`, JSON Schemas in `api/schemas/` and examples in `api/examples/`. `api/examples/config/` holds an example of every configuration document, `onboard-plans`, `skills-catalog` and `role-rules` among them.
- `input-dictionary.csv` / `.json`: 156 logical optional input fields/groups.
- `condition-assessments-119.csv` / `.json`: indicative workups for 119 active NASA model entries.
- `cliff/`: the confirming test candidates for the 119 conditions, quoted verbatim from NASA's Clinical Findings Forms, with each form's saved text, URL and SHA-256 (`cliff/METHOD.md`).
- `expanded-inventory-assessments-792.csv`: broader overlapping inventories, provisionally mapped.
- `assessment-families.csv` / `.json`: 51 planning families, including a generic review fallback.
- `tool-catalog.json`: 23 tool interface sketches; nested schemas belong in the TDDs.
- `sources.csv`: the source registry, with what each source does in the product.
- `nasa-data/`: the NASA cabin telemetry (Rodent Research-1, 2014) and radiation sample, unchanged, with provenance.
- `diagrams/`: editable Mermaid and shareable SVG diagrams.

## Status and limitations

This is a design package, not implemented product code, completed TDDs, trained weights or a clinical validation report. The interaction prototype is a synthetic design prototype and does not capture, store or transmit health data. Unreviewed draft: needs clinician review before live guidance. This covers the clinical assessment entries, sensor limits, routine steps, responder flows, onboard plans and teach steps, which also require appropriate instruments/operators. Catalog membership is not diagnostic capability. 911 source rows include overlap and are not 911 unique pathologies.

