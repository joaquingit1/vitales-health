# Crew Health Check

**Measures what it can. Shows what it can't. Teaches the crew the rest.**

Offline crew health software for spacecraft, stations, surface bases and rovers, built for the NASA Space Apps
Challenge 2026 challenge "Create Health Monitoring Software for Astronauts on Space Missions".

Everything runs on one laptop and its webcam, with no network. The crew does a short daily check; the kit map shows
which of the 119 condition assessments this ship's medical kit can actually run; when someone needs help, a crewmate
is guided through the checks and taught a second way to confirm each reading; and every plan lives on board, so nothing
waits for the ground. A local medical language model (HuatuoGPT-3-9B on llama.cpp) explains and drafts behind a typed
tool gateway. Every safety decision is deterministic code, and the system works the same with the model switched off.

> Not a medical device. A research prototype for the challenge.

## Status (interim)

The project is being built during the event (14 and 15 November 2026).

| Part | State |
|---|---|
| Product package (`docs/product/`) | Complete: PRD, pitch, use cases, UX, contracts, NASA sources |
| Shared architecture (`docs/SYSTEM.md`, `docs/DEPLOYMENT.md`) | Written; under independent review |
| Designs for the 33 epics (`docs/tdd/`) | Written; being brought in line with the architecture, then reviewed |
| Medicine potency engine (`pharmacy/`) | Built and validated (`docs/VALIDATION-REPORT.md`) |
| Design lint (`scripts/design_lint.py`) | Built, with tests |
| Hub, crew app, ground console | Being built epic by epic in the dependency order of `docs/SYSTEM.md` section 9, starting with E01 (platform) |

## System prerequisites

To run it (minimum tier T0, `docs/DEPLOYMENT.md` section 3.1):

| Need | Required | Notes |
|---|---|---|
| A laptop | Yes | Reference machine: Apple M3, 16 GB. macOS, Linux or Windows |
| A browser | Yes | The hub serves the crew app and the station view on `127.0.0.1:8080` |
| A webcam | No | Camera checks; manual entry when there is none |
| Model server | No | llama.cpp `llama-server` with HuatuoGPT-3-9B weights (GGUF, fetched by script, never committed); about 7.5 GB of memory while loaded |
| Bluetooth medical devices, habitat feeds, more hubs, a ground link | No | Tiers T1 to T7 in `docs/DEPLOYMENT.md` section 3 |

No cloud account, internet connection or container runtime is needed to run it. Containers (OCI compose profiles) are
for multi-node sites, CI and the ground node (`docs/DEPLOYMENT.md` section 7.3). Install, update and recovery work from
an offline bundle (section 7.4).

To develop: Python 3.14 with uv, Node.js with pnpm, and git (workspaces in `docs/SYSTEM.md` sections 6.2 and 6.3).
What runs today:

```sh
.venv/bin/python -m pytest tests                                 # potency engine and tooling tests
.venv/bin/python scripts/design_lint.py docs/tdd/E01.md          # check a design against the template and the contracts
```

E01 turns the repository into the uv and pnpm workspaces of `docs/SYSTEM.md` section 6 and adds `uv run crew-health`.

## Rules for everyone who works here, human or agent

`AGENTS.md` is the rulebook. Read it before any change; its section 6 lists the mistakes agents made in this
repository and the rule for each, so that new work gets them right the first time.

| Tool | How it loads the rules |
|---|---|
| Codex, Cursor, GitHub Copilot coding agent, Jules and other AGENTS.md readers | `AGENTS.md` directly |
| Claude Code | `CLAUDE.md`, which imports `AGENTS.md` |
| Gemini CLI | `GEMINI.md`, which imports `AGENTS.md` |
| GitHub Copilot chat | `.github/copilot-instructions.md` |
| Anything else | Point it at `AGENTS.md` first |

The process (`docs/BUILD-CHARTER.md` section 7): a design from `docs/tdd/TEMPLATE.md`; an independent review (the
reviewer is never the author, and a design is greenlit only with zero blockers and zero majors); tests first; code only
inside the paths the task owns; the gate commands of section 8; an independent review of the build. Reviews and the
authors' answers stay in `docs/tdd/reviews/` and `docs/build/<epic>/` as context for every later agent. Every AI run
that produced work gets a row in `AI-USE-LOG.md`.

Enforcement today is the rules, the design lint and the reviews. The CI gates of `docs/DEPLOYMENT.md` section 8 arrive
with E20; until then, run the gate commands of `docs/BUILD-CHARTER.md` section 8 before every push.

## Where things are

| Path | What |
|---|---|
| `docs/product/` | The product package; start with `README.md` and `PITCH.md` |
| `docs/product/LLM-ARCHITECTURE.md` | The model harness: tools, retrieval, verification |
| `docs/product/NASA-SOURCES.md` | The NASA sources the product and the model draw on |
| `docs/SYSTEM.md` | Shared architecture: kernel, modules, seams, dependency order |
| `docs/DEPLOYMENT.md` | Tiers, topology, security, packaging, CI |
| `docs/BUILD-CHARTER.md`, `docs/BUILD-PLAN.md` | How the build runs and its gates |
| `docs/tdd/` | One design per epic; reviews and answers in `docs/tdd/reviews/` |
| `docs/requests/` | Change requests between epics; decisions in `COORDINATOR-DECISIONS.md` |
| `docs/build/<epic>/` | Build plans, task briefs and build reviews |
| `contracts/`, `config/`, `data/`, `fixtures/` | Contracts, configuration documents, reference data, demo fixtures |
| `pharmacy/` | Medicine potency engine |
| `scripts/`, `tests/` | Tooling and tests |

The hub (`hub/`), the crew app and the ground console (`ui/`), the evaluation suites (`eval/`) and the evidence
bundles (`knowledge/`) are added by their epics, as laid out in `docs/SYSTEM.md` section 6.1.
