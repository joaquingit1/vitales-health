# Build board

Who builds what in Crew Health Check, and what can start now. Generated from the repository and the build pipeline (Sat 21:22); only the "Claimed by" column is edited by hand.

| Status | Meaning | Epics |
|---|---|---|
| 🔨 In progress | Being built by the person named | E02, E03, E05, E06, E07, E10, E14, E18, E20, E21, E23, E24, E25, E26, E27, E28, E29, E30, E31, E32 |
| 🔎 In review | Code done; reviewed, fixed or being checked before merge | E01 T1, E01 T2 |
| ⬜ Open | Free to claim and start now | E04, E08, E09, E11, E12, E13, E15, E16, E17, E19, E22, E33 |
| 🔒 Locked | Waits for the E01 foundation (E01 T1); you may claim it now and start when it opens | none |
| ✅ Built | Reviewed, checked and merged | none |

## How to claim and build an epic

1. Pick an Open or Locked epic with an empty "Claimed by" cell. @joaquingit1's build pipeline takes unclaimed epics in queue order ("queue #n", 3 at a time), so claim before its turn comes.
2. Write your GitHub handle in its "Claimed by" cell (for example `@alice`) and push that one line to main, or open a pull request with it. The pipeline never starts an epic someone claimed; a claim on an epic the pipeline already started does not count.
3. Build it: read AGENTS.md, the epic's design (or its product brief when it has none) and its row in docs/SYSTEM.md section 9. Edit only the paths in `owned_paths` of its tasks.json. Where a dependency is not built yet, code against its port and the kernel fakes (docs/SYSTEM.md section 10). Write the tests first, end to end tests included, and run ruff, mypy --strict, import-linter and the tests before you open the pull request.
4. Write `@alice, review` when your pull request is up. After review and merge it shows Built.

Hard dependencies (✅ when built) are what an epic needs to pass its acceptance; it can be coded before them against the kernel fakes. "Unblocks" lists the epics that need this one.

| Epic | Title | Status | Claimed by | Hard dependencies | Unblocks | Files | Design |
|---|---|---|---|---|---|---|---|
| E01 T1 | Platform, identities and durable records: foundation (workspaces, contract types, kernel ports and fakes, module registry) | 🔎 In review | @joaquingit1 (pipeline) | none | every epic | [tasks](E01/tasks.json) | [design](../tdd/E01.md) |
| E01 T2 | Platform, identities and durable records: everything after the foundation | 🔎 In review | @joaquingit1 (pipeline) | E01 T1 | none | [tasks](E01/tasks.json) | [design](../tdd/E01.md) |
| E02 | Consent, authentication and privacy | 🔨 In progress | @joaquingit1 (pipeline) | E01 | E03, E04, E05, E06, E07, E08, E09, E11, E12, E13, E15, E15-core, E16, E19, E21, E23, E26, E27, E28, E30, E31, E32, E33 | [tasks](E02/tasks.json) | [design](../tdd/E02.md) |
| E03 | Crew experience and daily check | 🔨 In progress | | E01, E02 | E04, E22 | [tasks](E03/tasks.json) | [design](../tdd/E03.md) |
| E04 | Guided complaint intake | ⬜ Open (queue #7) |  | E01, E02, E03, E23, E15-core | E15 | [tasks](E04/tasks.json) | [design](../tdd/E04.md) |
| E05 | Capabilities and measurement workflow | 🔨 In progress | @joaquingit1 | E01, E02 | E06, E07, E08, E09, E14, E30 | [tasks](E05/tasks.json) | [design](../tdd/E05.md) |
| E06 | Personal and station cameras | 🔨 In progress | @joaquingit1 | E01, E02, E05 | none | [tasks](E06/tasks.json) | [brief](../product/tdd/E06.md) |
| E07 | Oximeter and clinical device adapters | 🔨 In progress | @joaquingit1 | E01, E02, E05 | none | [tasks](E07/tasks.json) | [brief](../product/tdd/E07.md) |
| E08 | Wrist activity band and sleep pipeline | ⬜ Open (queue #3) |  | E01, E02, E05 | none | [tasks](E08/tasks.json) | [brief](../product/tdd/E08.md) |
| E09 | Reports and specialist findings | ⬜ Open (queue #4) |  | E01, E02, E05 | none | [tasks](E09/tasks.json) | [brief](../product/tdd/E09.md) |
| E10 | NASA registry and evidence database | 🔨 In progress | @joaquingit1 (pipeline) | E01 | E12, E19 | [tasks](E10/tasks.json) | [design](../tdd/E10.md) |
| E11 | Urgent handling and pathway engine | ⬜ Open (queue #8) |  | E01, E02, E15-core, E27-guidance | E13 | [tasks](E11/tasks.json) | [design](../tdd/E11.md) |
| E12 | Retrieval and typed tool gateway | ⬜ Open (queue #1) |  | E01, E02, E10, E27-guidance | E13, E17 | [tasks](E12/tasks.json) | [design](../tdd/E12.md) |
| E13 | Local reasoning and assessment | ⬜ Open (queue #9) |  | E01, E02, E11, E12, E27-guidance | none | [tasks](E13/tasks.json) | [design](../tdd/E13.md) |
| E14 | Trends, environment and Chronos-2 | 🔨 In progress | @joaquingit1 | E01, E05 | none | [tasks](E14/tasks.json) | [brief](../product/tdd/E14.md) |
| E15 | Episodes, follow-up and clinical review | ⬜ Open (queue #10) |  | E01, E02, E04, E15-core | E04, E11, E15, E16 | [tasks](E15/tasks.json) | [design](../tdd/E15.md) |
| E16 | Ground console and delayed handoff | ⬜ Open (queue #12) |  | E01, E02, E15 | none | [tasks](E16/tasks.json) | [brief](../product/tdd/E16.md) |
| E17 | Model integration and tool-use optimization | ⬜ Open (queue #5) |  | E01, E12 | none | [tasks](E17/tasks.json) | [design](../tdd/E17.md) |
| E18 | HealthBench and clinical evaluation | 🔨 In progress | @joaquingit1 (pipeline) | E01 | none | [tasks](E18/tasks.json) | [design](../tdd/E18.md) |
| E19 | Research and content workbench | ⬜ Open (queue #2) |  | E01, E02, E10 | none | [tasks](E19/tasks.json) | [design](../tdd/E19.md) |
| E20 | Accessibility, resilience and packaging | 🔨 In progress | @pgentil | E01 | none | [tasks](E20/tasks.json) | [design](../tdd/E20.md) |
| E21 | Integration, evidence and demonstration | 🔨 In progress | @joaquingit1 (pipeline) | E01, E02 | none | [tasks](E21/tasks.json) | [design](../tdd/E21.md) |
| E22 | Participation streak, patches and crew goal | ⬜ Open (queue #6) | @fedegutman | E01, E03, E23 | none | [tasks](E22/tasks.json) | [design](../tdd/E22.md) |
| E23 | Environment profile and habitat sensors | 🔨 In progress | @fedegutman | E01, E02 | E04, E15-core, E22, E24, E25, E26, E27, E28, E29, E31, E32, E33 | [tasks](E23/tasks.json) | [design](../tdd/E23.md) |
| E24 | Warnings engine | 🔨 In progress | @fedegutman | E01, E23 | none | [tasks](E24/tasks.json) | [design](../tdd/E24.md) |
| E25 | Routine scheduler and task guidance | 🔨 In progress | @joaquingit1 | E01, E23 | none | [tasks](E25/tasks.json) | [design](../tdd/E25.md) |
| E26 | Someone needs help | 🔨 In progress | @joaquingit1 | E01, E02, E23, E27-guidance | E28 | [tasks](E26/tasks.json) | [design](../tdd/E26.md) |
| E27 | Kit map: what this ship can test | 🔨 In progress | @joaquingit1 | E01, E02, E23, E27-guidance | E11, E12, E13, E26, E27, E28, E30, E32 | [tasks](E27/tasks.json) | [design](../tdd/E27.md) |
| E28 | Onboard plans: no dead ends | 🔨 In progress | @joaquingit1 | E01, E02, E23, E26, E27 | E33 | [tasks](E28/tasks.json) | [design](../tdd/E28.md) |
| E29 | The ground as a reader | 🔨 In progress | @joaquingit1 | E01, E23 | none | [tasks](E29/tasks.json) | [brief](../product/tdd/E29.md) |
| E30 | Teach and check | 🔨 In progress | @joaquingit1 | E01, E02, E05, E27-guidance | none | [tasks](E30/tasks.json) | [design](../tdd/E30.md) |
| E31 | Who helps | 🔨 In progress | @joaquingit1 | E01, E02, E23 | none | [tasks](E31/tasks.json) | [design](../tdd/E31.md) |
| E32 | Treat here: supplies and planning | 🔨 In progress | @joaquingit1 | E01, E02, E23, E27 | none | [tasks](E32/tasks.json) | [design](../tdd/E32.md) |
| E33 | Stay or return | ⬜ Open (queue #11) |  | E01, E02, E23, E28 | none | [tasks](E33/tasks.json) | [design](../tdd/E33.md) |
