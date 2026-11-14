# AI-use log

One row per AI run that produced work (KICKOFF.md section 8). The product itself runs a local medical language model
(HuatuoGPT-3-9B) behind a typed tool gateway; nothing safety related depends on it.

"Coordinator" is the Claude Code session (Claude Opus 5.5) that plans, briefs, reviews and greenlights. Every row
marked "checked by coordinator" was accepted only after its gate commands passed.

| Time | Who | Tool and model | What for | What it produced | Where it landed | Checked by |
|---|---|---|---|---|---|---|
| Sat 09:00 | Coordinator | Claude Code, Claude Opus 5.5 | Root cause the potency engine's failed independent validation | Coordinator decisions, spec changes | docs/requests/COORDINATOR-DECISIONS.md, docs/ENGINE-SPEC.md | Team |
| Sat 09:31 | Builder CX-A | Codex CLI, GPT-6 Astra | Engine foundation round (types, settings, anchors) | Code and tests | pharmacy/, tests/ | Coordinator (gates, review) |
| Sat 10:05 | Builder CX-A2 | Codex CLI, GPT-6 Astra | Foundation follow-up | Code and tests | pharmacy/, tests/ | Coordinator (gates, review) |
| Sat 10:10 | Builders CX-B, CX-C, CX-CAL, CX-D | Codex CLI, GPT-6 Astra | Engine round 2: compression, chemistry, calibration, validation | Code, tests, calibration data | pharmacy/, tests/, data/pharmacy/calibration/ | Coordinator (gates, review) |
| Sat 10:40 | Coordinator | Claude Code, Claude Opus 5.5 | Calibration fixes (mean location, rate weighted internal humidity derivative) | Code, tests, regenerated calibration | pharmacy/calibration.py, tests/unit/calibration/, config/pharmacy/ | Coordinator (gates) |
| Sat 10:50 | Research agents | Claude Code subagents, Claude Opus 5.5 | Deployment research: vehicle computing, surface and comms, medical systems and standards | Research notes with sources | build folder research/ (local) | Coordinator |
| Sat 11:00 | Coordinator | Claude Code, Claude Opus 5.5 | Build charter and design template | Charter, template | docs/BUILD-CHARTER.md, docs/tdd/TEMPLATE.md | Team |
| Sat 11:10 | Design authors | Claude Code workflow, Claude Opus 5.5 | Technical designs for 21 epics | Design documents | docs/tdd/E*.md | GPT-6 Astra review, coordinator |
| Sat 13:54 | Design author (E23) | Claude Code, Claude Fable 5.2 | Technical design for E23 (environment profile and habitat sensors) and its requests | Design document, request file | docs/tdd/E23.md, docs/requests/E23.md | Reviewed in r1 (below) |
| Sat 14:35 | Reviewer (E23) | Claude Code subagent, Claude Opus 5.5 | Independent cold review of the E23 design, round 1 | Review, 36 findings | docs/tdd/reviews/E23.opus-r1.md | Author response with evidence |
| Sat 14:35 | Design author (E23) | Claude Code, Claude Opus 5.5 | Revision of the E23 design and requests for the r1 review | Revised design, requests, response | docs/tdd/E23.md, docs/requests/E23.md, docs/tdd/reviews/E23.response-r1.md | Awaiting review round 2 and the coordinator ruling on R-E23-1 |
| Sat 15:09 | Reviewer (E23) | Claude Code subagent, Claude Opus 5.5 | Independent review of the E23 design, round 2 | Review: 32 of 36 r1 findings resolved, 15 new | docs/tdd/reviews/E23.opus-r2.md | Author response with evidence |
| Sat 15:09 | Design author (E23) | Claude Code, Claude Opus 5.5 | Revision of the E23 design for the r2 review; owner rulings D8 and D9 recorded | Revised design, requests, response, two coordinator decisions | docs/tdd/E23.md, docs/requests/E23.md, docs/tdd/reviews/E23.response-r2.md, docs/requests/COORDINATOR-DECISIONS.md | Awaiting review round 3 |
| Sat 15:13 | Reviewer (E23) | Claude Code subagent, Claude Opus 5.5 | Independent review of the E23 design, round 3 | Review: 17 of 19 earlier findings resolved, 8 new (1 major, 7 minor) | docs/tdd/reviews/E23.opus-r3.md | Author response with evidence |
| Sat 15:13 | Design author (E23) | Claude Code, Claude Opus 5.5 | Revision of the E23 design for the r3 review | Revised design, requests, response | docs/tdd/E23.md, docs/requests/E23.md, docs/tdd/reviews/E23.response-r3.md | Awaiting review round 4 |
| Sat 15:16 | Reviewer (E23) | Claude Code subagent, Claude Opus 5.5 | Independent review of the E23 design, round 4 | Review: GREENLIGHT, 2 non-blocking minors | docs/tdd/reviews/E23.opus-r4.md | Author response with evidence |
| Sat 15:16 | Design author (E23) | Claude Code, Claude Opus 5.5 | Fixes for the two r4 minors | Revised design, requests, response | docs/tdd/E23.md, docs/requests/E23.md, docs/tdd/reviews/E23.response-r4.md | Greenlit by r4 |
| Sat 15:18 | Design author (E23) | Claude Code, Claude Opus 5.5 | getContext added to E23 by owner ruling D10; request R-E23-17 | Revised design, requests, coordinator decision | docs/tdd/E23.md, docs/requests/E23.md, docs/requests/COORDINATOR-DECISIONS.md | Added after the r4 greenlight; not yet reviewed |
| Sat 16:10 | Supervisor SYSTEM | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/SYSTEM.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:29 | Supervisor SYSTEM | Codex CLI, GPT-6 Astra | Cold design review | Review with verdict | docs/tdd/reviews/ | Coordinator |
| Sat 16:32 | Supervisor E02 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E02.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:32 | Supervisor E20 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E20.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:35 | Supervisor E10 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E10.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:47 | Supervisor E02 | Codex CLI, GPT-6 Astra | Cold design review | Review with verdict | docs/tdd/reviews/ | Coordinator |
| Sat 16:48 | Supervisor E01 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E01.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:48 | Supervisor E21 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E21.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:51 | Supervisor E05 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E05.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:51 | Supervisor SYSTEM | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/SYSTEM.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:53 | Supervisor E03 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E03.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:53 | Supervisor E12 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E12.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:55 | Supervisor E23 | Codex CLI, GPT-6 Astra | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E23.md | Cold review (GPT-6 Astra), design lint |
| Sat 16:57 | Supervisor E21 | Codex CLI, GPT-6 Astra | Cold design review | Review with verdict | docs/tdd/reviews/ | Coordinator |
| Sat 17:01 | Supervisor E01 | Codex CLI, GPT-6 Astra | Cold design review | Review with verdict | docs/tdd/reviews/ | Coordinator |
| Sat 17:03 | Supervisor E05 | Codex CLI, GPT-6 Astra | Cold design review | Review with verdict | docs/tdd/reviews/ | Coordinator |
| Sat 17:06 | Supervisor E03 | Codex CLI, GPT-6 Astra | Cold design review | Review with verdict | docs/tdd/reviews/ | Coordinator |
| Sat 17:08 | Supervisor E02 | Codex CLI, GPT-6 Astra | Build plan and design fixes from the cold review | Plan and task briefs | docs/build/E02/ | Coordinator |
| Sat 17:26 | Supervisor E23 | Codex CLI, GPT-6 Astra | Build plan and design fixes from the cold review | Plan and task briefs | docs/build/E23/ | Coordinator |
| Sat 17:29 | Coder E04 | Codex CLI, GPT-6.1 Sol | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E04.md | Cold review (GPT-6 Astra), design lint |
| Sat 17:29 | Supervisor E05 | Codex CLI, GPT-6 Astra | Build plan and design fixes from the cold review | Plan and task briefs | docs/build/E05/ | Coordinator |
| Sat 17:29 | Coder E01 | Codex CLI, GPT-6.1 Sol | Design fix against reviews, SYSTEM.md and decisions | Revised design and requests | docs/tdd/E01.md | Cold review (GPT-6 Astra), design lint |
| Sat 17:32 | Supervisor E21 | Codex CLI, GPT-6 Astra | Build plan and design fixes from the cold review | Plan and task briefs | docs/build/E21/ | Coordinator |
| Sat 17:33 | Supervisor E10 | Codex CLI, GPT-6 Astra | Build plan and design fixes from the cold review | Plan and task briefs | docs/build/E10/ | Coordinator |
| Sat 17:33 | Supervisor E03 | Codex CLI, GPT-6 Astra | Build plan and design fixes from the cold review | Plan and task briefs | docs/build/E03/ | Coordinator |
| Sat 17:36 | Coder E15 | Codex CLI, GPT-6.1 Sol | Build plan from the frozen design | Plan and task briefs | docs/build/E15/ | Plan review (GPT-6 Astra) |
| Sat 17:37 | Supervisor E12 | Codex CLI, GPT-6 Astra | Build plan and design fixes from the cold review | Plan and task briefs | docs/build/E12/ | Coordinator |
| Sat 17:37 | Coder E28 | Codex CLI, GPT-6.1 Sol | Build plan from the frozen design | Plan and task briefs | docs/build/E28/ | Plan review (GPT-6 Astra) |
| Sat 17:37 | Coder E24 | Codex CLI, GPT-6.1 Sol | Build plan from the frozen design | Plan and task briefs | docs/build/E24/ | Plan review (GPT-6 Astra) |
| Sat 17:38 | Coder E32 | Codex CLI, GPT-6.1 Sol | Build plan from the frozen design | Plan and task briefs | docs/build/E32/ | Plan review (GPT-6 Astra) |
