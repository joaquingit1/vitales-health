# Kickoff

How the team starts on Saturday 14 November at 09:00 and gets to a submitted project by Sunday evening. [PITCH.md](PITCH.md) says what we show; [PRD.md](PRD.md) says what we build; this page says who does what, in what order, and how we know it works.

**The rule that comes first.** Teams may not begin working on the challenge before the hackathon starts. Code, content files, fixtures, tests and designs for the product are created from 09:00 Saturday. This package is the plan, and nothing in it is copied into the repository as product code.

## 1. Roles

Four streams own the 33 epics, as in [PRD.md](PRD.md) section 12 and [backlog.csv](backlog.csv). An epic's owner may hand single work packages to another stream; backlog.csv names the owner of each. Write a name next to each role before 09:00. One person can hold two roles; every role has exactly one owner.

| Role | Owns | Epics | Name |
|---|---|---|---|
| Stream A, platform and data, and integration owner | Repository, local hub, records, events, permissions, serving the configuration documents through the API, the kit map engine and what-if, the plan schema and PlanRun engine, the ground link as a reader (GroundLink, the queued log with its `leaves_at`, link independence, the device cache), SkillRecords and the skills views, the GuidedCheck engine, supplies and cover, ReturnReadiness, ground console; merges to main and runs every checkpoint | E01, E02, E05, E09, E16, E29, E31, E32, E27-02, E28-01, E28-04, E30-02, E33-02 | |
| Stream B, clinical content and knowledge | The kit map's 119 rows from NASA's Clinical Findings Forms (each checked by a second person), the 119-condition registry, evidence passages, pathways, intake, episodes, the warnings engine, the responder flows and help session of Someone needs help, routine task guidance, the onboard plan for every kit map row (each checked by a second person), the skills catalog with its teach steps, the role rules, the supply list, the ground-silence and supply cover rules, the lunar return stages | E04, E10, E11, E15, E19, E24, E26 (with D), E28, E33, E25-03, E27-01, E29-03, E30-01, E31-02, E32-01, E32-03 | |
| Stream C, sensing and intelligence | Camera station, oximeter, band and habitat sensor adapters, the environment profile and its limit table, the local model and its tools with the kit check on every request and the plan tools, teach and check (the camera pulse as the reference for a crewmate's count; the compression rate from the helper's motion, experimental), trends and Chronos-2 (experimental, under Records) | E06, E07, E08, E12, E13, E14, E17, E23, E30, E27-03, E28-06 | |
| Stream D, experience and evaluation | The crew app on the reference design system (Today, This kit with the kit aboard card, Habitat with its ground line, Routine with drills, warnings, the Assessment with the plan aboard, A crewmate checks, Return and Who decides, and the help screens with their role rows and For the ground card), the routine scheduler, participation and practice patches, the demo fixture, scenarios, the venue study and How we know, HealthBench, accessibility | E03, E18, E20, E21, E22, E25 (with B), E23-04, E24-04, E26-03, E26-04, E27-04, E27-05, E28-05, E29-05, E30-05, E30-06, E31-03, E31-04, E32-05, E33-03 | |
| Presenter and video | The 30-second video, the recorded run, captions | | |
| Submission owner | The project page, every field in section 7, the AI-use log, the final link check | | |

## 2. Saturday 09:00 to 10:30: S0, freeze the contracts

Everyone works on this together. Nothing else starts until the freeze is written down.

1. Stream A creates the public repository with the layout in section 6 and adds `AI-USE-LOG.md` (section 8). From this minute every AI use is logged.
2. The submission owner registers the team and the challenge, creates the project page with the name "Crew Health Check" and saves it. Submission opens at 09:00; only the last saved version is judged.
3. The submission owner asks the Local Lead in writing, and keeps the answer: whether running HuatuoGPT-3-9B, open weights from a research group at The Chinese University of Hong Kong, Shenzhen, downloaded and run offline with no collaboration, funding or data exchange, raises a concern under the Judging and Awards Guide's review for "prohibited bilateral activity with China, or a Chinese-owned entity". The team follows the answer.
4. Stream D writes the venue study protocol and consent wording ([VALIDATION.md](VALIDATION.md) section 6) and freezes it before the first volunteer. Stream B splits the 119 kit map rows among writers and names a second checker for each, and splits the onboard plans the same way: one plan for every kit map row, each with its writer and a second checker.
5. The team reads these contracts aloud, agrees them and commits them as `contracts/` in the repository, in our own words and code:

| Frozen at S0 | Source in this package |
|---|---|
| Identity, time and provenance (`origin_mode` Live, Replay or Sim) | [CONTRACTS.md](CONTRACTS.md) section 1 |
| Core objects and the example observation | CONTRACTS.md sections 2 and 3 |
| States and transitions | CONTRACTS.md section 4 |
| API intents and error codes | CONTRACTS.md section 5 |
| The model action contract and the 23 tools | CONTRACTS.md section 6, [tool-catalog.json](tool-catalog.json), [LLM-ARCHITECTURE.md](LLM-ARCHITECTURE.md) section 4 |
| The 15 body regions | CONTRACTS.md section 7 |
| Participation: CheckCompletion, StreakView, CrewGoalView, PatchAward, `AGGREGATE_ONLY` | CONTRACTS.md section 8 |
| Configuration documents and the no-hardcoded-data rule: every label, threshold, cadence, step, effect, citation, environment, sensor list, help step, plan step, teach step, role rule and supply is served by the API | CONTRACTS.md section 11 “Configuration documents and the no-hardcoded-data rule” |
| Environments, hazards and mission context: EnvironmentProfile, `station_leo`, `transit`, `lunar_surface`, `mars_surface`, the overlays `eva`, `post_eva`, `exercise`, `sleep` | CONTRACTS.md section 12, [ENVIRONMENTS.md](ENVIRONMENTS.md), [environment-hazards.json](environment-hazards.json) |
| Sensors and readings: SensorReading, SensorLimit, Live, Replay and Sim, the stale state, and the limit table with the level each limit raises | CONTRACTS.md section 13, [ENVIRONMENTS.md](ENVIRONMENTS.md) |
| Warnings: Warning, its four levels, `raised_by` rule, sensor_limit or assistant, raised, acknowledged and cleared with a reason | CONTRACTS.md section 14 |
| Routine: RoutineTask, RoutineSchedule, the cadence words, triggers with reason, source and end, missed and not available | CONTRACTS.md section 15, [ROUTINE.md](ROUTINE.md), [routine-schedule.json](routine-schedule.json) |
| Someone needs help: HelpSession, responder checks, fixed steps, assistant turns, readings shown, the role rows, the log for the ground (queued for the next contact, never waited for), the crew broadcast | CONTRACTS.md section 16, [RESPONDER-FLOWS.md](RESPONDER-FLOWS.md) |
| The kit map: KitMap, the precedence rule for Measured here, Only what you report and Needs a test not aboard, the kit per environment, the planning list, the what-if and the kit check on every assistant request | CONTRACTS.md section 21 “Kit map”, [PRD.md](PRD.md) section 4.12 |
| Assistant conversation, event stream, screen to endpoint map, backend implementation guide | CONTRACTS.md sections 17 to 20 |
| Onboard plans: `onboard-plans.json`, PlanView and PlanRun, the stage order (check another way, do now, watch, if worse, medical officer, return on the Moon only, the ground log last and never blocking), the four modes, supplies and cover, ReturnReadiness, and the invariants EI1 to EI12 | CONTRACTS.md sections 22 (onboard plans), 25 (supplies), 26 (the ground as a reader), 27 (ReturnReadiness) and 28 (EI1 to EI12); [PRD.md](PRD.md) section 4.13 |
| Skills catalog: `skills-catalog.json`, teach steps with their body landmarks, GuidedCheck, CoachingSample and the check against the cameras (count agreement; compression rate, experimental) | CONTRACTS.md section 23 |
| Role rules: `role-rules.json`, RoleAssignment, SkillRecord and its computed freshness, the medical officer cascade, drills and practice patches | CONTRACTS.md section 24 |
| GroundLink: `up`, `delayed` and `down`, `next_contact_at`, `gap_planned` and `gap_kind`, the queued log with its `leaves_at`, catch-up, and the rule that nothing reads the link to decide what the crew can do | CONTRACTS.md section 12 (Ground link) and section 26 (the ground as a reader) |
| The endpoints | `api/openapi.yaml`, with its JSON Schemas and examples in `api/` |
| The design tokens and components | [UX-DESIGN.md](UX-DESIGN.md) section 1 and [design/interaction-prototype.html](design/interaction-prototype.html) |
| The demo fixture `demo-mara-fd19`, its six acts and assertions D01 to D44 | [VALIDATION.md](VALIDATION.md) section 3 |

After 10:30 a contract changes only when the integration owner agrees, and the change goes into the repository with a one-line reason. Each stream builds behind these contracts and can stub the others.

## 3. The first hours, by stream

Each stream's goal for checkpoint 1 at 13:00 is one thin path that runs end to end, not a finished module.

| Stream | First work packages | Done by 13:00 |
|---|---|---|
| A | E01-01 storage and migrations, E01-02 namespaces (patient, operator, scenario), E01-03 idempotent commands and events | A manual observation is written through the hub, read back on Today and survives a restart |
| B | E10-01 import the 911 source rows with provenance, E11-01 the model-independent Someone needs help entry, E27-01 the first kit map rows from their Clinical Findings Forms, E28-02 starting with ICL10's plan, E30-01 starting with the `pulse_check_wrist` skill | The Someone needs help first screen and the three fixed red-flag questions are served by the hub with the model process not running; the first 20 kit map rows written and checked, each with its form link; ICL10's plan and the `pulse_check_wrist` skill written in their configuration documents, checked by a second person and served by the hub |
| C | E17 model runtime on the M3 with HuatuoGPT-3-9B, E06 camera worker with the quality gate | One tool call is parsed end to end from the local model; one camera returns "No reading" with a reason |
| D | E03-04 the stage, body map and cards on the reference design system, E18-01 the fixture and its assertions | Today renders `demo-mara-fd19` from the hub with the six cards, and D01 passes in stub mode |

The crew app looks exactly like the prototype: the same tokens, type, stage, cards and parts. The prototype is the design reference: stream D writes the app in the repository during the event.

## 4. Integration checkpoints

At each checkpoint the integration owner merges main and runs `demo-mara-fd19` in stub mode, through its six acts: 1 The kit and 2 Notice on the space station, then 3 Earth out of reach, 4 Ask and measure, 5 Check it another way and 6 Help, anywhere on the Moon base, with the ground link down from act 3 to the end. Assertions D01 to D44 are in [VALIDATION.md](VALIDATION.md) section 3; scenarios V01 to V98 in section 2. The result is one line per assertion, pass or fail, committed as `CHECKPOINTS.md`. A failing assertion is fixed before new features on that stream. The fixture is never edited to make an assertion pass.

| Time | Checkpoint | Exit evidence ([PRD.md](PRD.md) section 12) | Fixture target |
|---|---|---|---|
| Sat 13:00 | CP1, stage S1 | A complete offline record-to-screen path: observation, episode, the Someone needs help first screen and audit event survive a restart | D01, D02, D05, D15 |
| Sat 18:00 | CP2, stage S2 | Each adapter, the habitat sensors included, shows success, failure, unavailable, stale and identity handling; streak and crew goal recompute from check-completion events; warnings raise and clear from rules and sensor limits; the environment switches to the Moon base and the routine tightens a task with its reason and end; the kit map and the plan count are served; the ground link goes down and every response but the ground text stays the same | Add D03, D04, D07, D08, D09, D14, D16, D17, D18, D19, D20, D21, D28, D29, D31, D32, D33, D34, D35, D43 |
| Sat 23:00 | CP3, stage S3 | Retrieval, tools, pathways, the model's assessment, the plan aboard, the guided check by a crewmate, the role rows, the guided help session and the log for the ground queued for the next contact work together | Add D06, D10, D11, D12, D13, D22, D23, D24, D25, D26, D27, D30, D36, D37, D38, D39, D40, D41, D42, D44 |
| Sun 10:00 | CP4, stage S4 | Band, camera station, Chronos-2 and ground flows integrated; benchmark harness reproducible | All 44 in stub mode; the first `live` run recorded with its failures |
| Sun 14:00 | Demo freeze, stage S5 | Frozen results, the failure demonstration, a truthful capability manifest | All 44 in stub mode and one clean `live` run |

After the demo freeze only fixes for a failing assertion, copy errors and the result panel go in. Tag the commit `demo-freeze`.

## 5. Sunday: video, project page, submission

| Time | What | Owner |
|---|---|---|
| Sun 10:30 to 12:00 | Insurance takes of every shot on the latest green build, following [DEMO-PLAN.md](DEMO-PLAN.md); fallbacks locked at 12:00 | Presenter |
| Sun 14:00 to 15:30 | Freeze and tag `demo-freeze` at 14:00, then the final takes. Real runs, waiting time trimmed, every shot captioned in English | Presenter |
| Sat afternoon to Sun 12:00 | The venue camera study with consenting adult volunteers, crewmate pulse counts against the cameras on [N] pairs, the CPR rate on a manikin against a metronome (experimental), the assistant's turn time on the M3, rules alone against rules plus the assistant on the frozen scenarios | Stream D, with C |
| Sun 14:00 to 18:00 | Result panel and How we know: the kit map counts and how many conditions have a plan aboard, the venue study with its denominators, failures, the HealthBench and scenario numbers as measured | Stream D |
| Sun 15:30 to 18:00 | Edit to 30 seconds or less (checked on the exported file), upload publicly with no login by 18:00, check it in a private window | Presenter |
| Sun 18:00 to 20:00 | Fill every project page field (section 7), save | Submission owner |
| Sun 20:00 | Submitted. Everything after this is an improvement saved over a complete submission | Everyone |
| Sun 22:00 | Final check: every link in a private window, the video length, the AI disclosure, the names | Submission owner |
| Sun 23:59 | Submission closes | |

## 6. Repository layout

The architecture is the local hub of [PRD.md](PRD.md) section 10: the browser is the interface, a Python and FastAPI service owns records, rules, workflows and adapters, SQLite stores records and events, and the model runs as an isolated local inference service.

```
crew-health-check/
  README.md            what it is, how to run it, the walkthrough
  AI-USE-LOG.md        every AI use, from 09:00 Saturday
  THIRD-PARTY.md       every model, dataset, library, font and mesh with its licence
  CHECKPOINTS.md       the fixture result at each checkpoint
  contracts/           the S0 contracts and the OpenAPI document (all streams)
  config/              versioned configuration documents: environments, limits, routine, responder flows, onboard plans, skills catalog, role rules (B, C)
  hub/                 FastAPI service: records, events, permissions, rules, sensor monitor, warnings, routine scheduler, Someone needs help, participation (A, B, C, D)
  ui/crew/             the crew app on the reference design system; renders only what the API serves (D)
  ui/ground/           the ground console (A)
  workers/camera/      camera station capture, quality gate, pulse and breathing (C)
  adapters/            oximeter, band, blood pressure cuff, thermometer, habitat sensors in Live, Replay and Sim (C)
  model/               inference service settings, tool gateway, prompts (C)
  knowledge/           the condition registry and evidence passages (B)
  fixtures/demo-mara-fd19/   the frozen demo fixture (D)
  eval/                scenarios V01 to V98, HealthBench harness, result panel (D)
```

## 7. Submission checklist

The fields of the official project page, as drafted in [PITCH.md](PITCH.md), with one owner each.

| Field or check | Content | Owner | Done |
|---|---|---|---|
| Team and challenge | Every member registered with the team; challenge "Create Health Monitoring Software for Astronauts on Space Missions" selected | Submission owner | |
| Project name | Crew Health Check | Submission owner | |
| High-level summary | The PITCH.md draft, updated with what actually works | Submission owner | |
| Link to project demo | The 30-second video, public, no login | Presenter | |
| Link to final project | The public repository with the README walkthrough and the result panel | Stream A | |
| Detailed project description | What it does, how it works, what we measured with denominators and failures, what it cannot do, tools and hardware | Stream D | |
| Use of AI | Written from the AI-use log. Every member confirms every sentence, above all where the idea came from | Submission owner, then everyone | |
| NASA data | The NASA table in PITCH.md, led by the condition list and the Clinical Findings Forms, each with what it does in the product; all 84 NASA documents and two datasets are in [NASA-SOURCES.md](NASA-SOURCES.md) | Stream B | |
| Space Agency Partner data and other references | THIRD-PARTY.md, with the NASA data files in `nasa-data/` credited from their provenance files: the MakeHuman mesh and targets (CC0), HuatuoGPT-3, Chronos-2, Outfit and DM Mono (SIL Open Font License), every library and quoted source | Stream B | |
| Length | Video 30 seconds or less, or at most seven slides including the title | Presenter | |
| Language | English everywhere, or English subtitles | Presenter | |
| People on camera | Nobody under 18 in any image, video or audio | Presenter | |
| Branding | No NASA logos, flags or mission identifiers; patches are our own designs | Stream D | |
| AI imagery | Any AI-generated image or video frame carries a visible watermark | Presenter | |
| Links | Every link opened in a private window with no login | Submission owner | |

## 8. AI-use log

One row per use, kept in `AI-USE-LOG.md` from 09:00 Saturday. The "Use of AI" field is written from it, so it must be complete.

| Time | Who | Tool and model | What for | What it produced | Where it landed | Checked by |
|---|---|---|---|---|---|---|
| Sat 09:40 | (name) | (tool, model) | (for example: draft the FastAPI route for observations) | (code, text, image) | (file and commit) | (name) |

Also record that the product itself runs a local medical language model.