# Package checks

What was verified in this package, and how. The counts, structure, links and writing were re-checked by script on 14 November 2026. These are checks of documents, data files and the design prototype. They are not tests of an implemented product, and no clinical, sensor or model result exists yet.

## Counts, checked by script against the files

| Item | Count | Checked in |
|---|---|---|
| Product requirements | 62 (R01 to R62) | PRD.md |
| Epics | 33 | epics.json, tdd/E01.md to tdd/E33.md |
| Work packages | 120 | backlog.json and backlog.csv; every story listed in epics.json exists in the backlog and the reverse |
| Integration scenarios | 98 (V01 to V98) | VALIDATION.md section 2 |
| Demo fixture assertions | 44 (D01 to D44) | VALIDATION.md section 3 |
| Tool interface sketches | 23 | tool-catalog.json |
| Environments and hazards | 4 environments plus the spacewalk overlay, 19 hazards, 39 sources | environment-hazards.json, ENVIRONMENTS.md |
| Routine items | 36, with 40 sources; the drill lists the skills it practices | routine-schedule.json, ROUTINE.md |
| Someone needs help | 5 triage questions, 20 flows, 68 sources; in every flow the one ground row is the last row, a log that nothing waits for | responder-flows.json, RESPONDER-FLOWS.md |
| Example configuration documents | Every configuration document, `onboard-plans`, `skills-catalog` and `role-rules` among them | api/examples/config/ |
| Optional input definitions | 156 | input-dictionary.json and .csv |
| Active NASA condition entries | 119, plus 792 broader inventory rows (911 overlapping rows) | condition-assessments-119.json, expanded-inventory-assessments-792.csv |
| NASA documents found by the research | 580 unique links in nine kinds; 15 used in the product, each with what it does | NASA-SOURCES.md |
| Worked demo condition entries | 7, with 196 quotes matched by script to the saved source text | DEMO-CONDITIONS.md |

## Structure and links

- The epic dependency graph has no cycles, and every declared epic and work package dependency exists.
- Every local link in the 104 Markdown and HTML files outside `sources/` resolves to an existing file, and every link to a heading in a generated page finds its anchor. The only unresolved fragments are condition browser rows such as `#ICL3`, which the page creates when it loads; the browser's own test opens them.
- Every Markdown document has a generated web page in the same style as the prototype, linked from the [reading guide](index.html).

## Data

- The replayed cabin CO2 value was recomputed from nasa-data/habitat-environment/eda_rr1_telemetry.csv: the twelve five-minute samples after 14:36 up to 15:36 on 7 October 2014 average 4,606 ppm, 3.50 mmHg at an assumed 760 mmHg. The PRD, the interaction design, the validation fixture, the demo conditions and the prototype all use it.
- The four files in nasa-data/habitat-environment/ and nasa-data/radiation/ are byte-identical copies of the downloaded NASA files, with their provenance files.

## Writing

- No em or en dashes and no spaced hyphens in any document written for this package. The ones that remain are inside NASA condition names, kept exactly as NASA writes them (for example `BHP - ANXIETY`), in source titles in sources.csv, in arithmetic in the prototype's code.
- The pitch, the PRD, the interaction design, the contracts, the validation fixture, the backlog and the epic briefs agree on: six cards on Today, the daily check ending on "Check done." with the streak and crew goal beside it, no separate participation screen, the regions (sleep on the wrist, mood on the head, reaction time on the eyes, cabin air on no region), and the words Live, Replay and Sim.

## The prototype

Checked in a headless browser at 1440 by 900 and at 390 pixels wide, with console errors captured and every element tested for clipped text or sideways scrolling:

- Today matches the team's body dashboard: the same tokens, type, stage and card grid, with cabin air in the activity card's place.
- The daily check, clicked through from "Nothing new" to the journal and "Finish check", ends on "Check done." and moves the streak from 18 to 19 days and the crew goal from 23 to 24 of 28. Today then reads "19 days in a row, done today".
- A concern: the three urgent questions come first; "All the time, still now" on the timing question opens Someone needs help at once, as the NHS source requires; the scripted "Comes and goes, easing now" leads to the pulse request.
- The camera measurement: idle, reading, the failed attempt kept as failed, then the accepted Live pulse.
- The assessment, shared care with the delivery timeline and link outage, records with the ECG import, and Someone needs help: the air check, who needs help, the responding, breathing and bleeding checks and the situation pills come from responder-flows.json; the flow page shows that flow's steps, its note for the current place and the person's latest readings.
- This kit with Test here and Plan here, the kit aboard's supplies and trained hands, and "7 of 7 have a plan aboard" on Today (`#kit`, `#kit/mars`).
- Act 3 on the Moon base: the clock at 018/09:20:40 and "Ground link down, next contact in 26 h (Sim)".
- The assessment's "Can't test here: check it another way" with Teach Tomas, and The plan aboard ending on "Nothing here waits for it" (`#assess`).
- A crewmate checks, clicked through: the teach steps one at a time, the 60 second count, the regular-or-not question, then "Your count 92 · cameras 96 · they agree", after which the assessment lists the pulse check by Tomas (`#teach`).
- Log for the ground with the link down: Queued "leaves at the next contact, in 26 h" and "Waiting for the next contact. The plan keeps running." (`#share`).
- Who does what and the For the ground card on the Moon base, queued after approval, with the flow's last row headed "Log for the ground" (`#help/moon/eye`).
- No console errors, no clipped text and no sideways scrolling on any of these screens.

Each screen opens directly from a link: `design/interaction-prototype.html#today`, `#you`, `#habitat`, `#routine`, `#alerts`, `#check`, `#kit`, `#concern`, `#measure`, `#teach`, `#assess`, `#share`, `#records` and `#help`. A place follows a slash (`#habitat/moon`, `#routine/mars`), and Someone needs help takes a demo session or a flow ID from responder-flows.json: `#help/moon/eye`, `#help/station/bleed`, `#help/station/cpr`, `#help/station/choking`.

## Not verified here

- No product code, model run, camera accuracy figure or benchmark score exists. They are created during the event.
- The clinical content is unreviewed planning synthesis, including the seven worked entries. It needs clinician review before it guides anyone.
- The "Use of AI" wording about where the idea came from must be confirmed by the team; only the team knows it.
