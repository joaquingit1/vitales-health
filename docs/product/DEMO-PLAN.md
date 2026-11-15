# Demo plan

**Crew Health Check** · NASA Space Apps Challenge 2026 · Create Health Monitoring Software for Astronauts on Space Missions

The guide caps the demo video at 30 seconds; a longer one makes the project ineligible for Global Judging. The 30 second cut is the deliverable, cut from a real run of the frozen build on `demo-mara-fd19`: Mara, the Moon base and the ground link are Sim and say so; the presenter's pulse and the crewmate's count are Live. Times are UTC-3; the team submits Sunday 15 November at 20:00.

## 1. The sentence a judge repeats

> "It shows what the ship can't test, teaches a crewmate to check it another way, and two independent readings have to agree. No doctor, no Earth."

Readings, not hands: in the shot the crewmate's count and the camera's reading are the two independent readings; the camera that watches hands (CPR rate) is experimental and stays out of the video.

## 2. The wow moment

**In one continuous real frame, a crewmate's fingers rest on the presenter's wrist while the webcam reads the same pulse from the face, and the verdict lands, "Your count 92 · cameras 96 · they agree", beside "The ring stays: only an ECG answers the rhythm." with the ground link down.**

It is the only candidate that is live, physical and checkable by the viewer, and it shows three things NASA exploration medicine engineers care about together: nobody on the ground in the loop, a crewmate without medical training doing the work, and the honest limit of a small kit. The link drop is an absence and the kit map a computed picture, so both work as its setup, not as the beat.

## 3. The 30 second cut

Browser at 1440 px wide, 100% zoom, no chrome: the width at which E03's parity test (T-E03-059) checks the app against `interaction-prototype.html`. Voice: 72 words on its own track; team names are a caption, so the voice opens on the hook.

| Time | On screen (package copy) | Voice | Must be real |
|---|---|---|---|
| 0:00 to 0:05 | The figure builds from points, then kit view: lime dots, hollow rings, dashed rings. This kit: "[C] of 119", "need a test this ship doesn't carry". Caption "[Team name]: [names]" to 0:02, then "[C] of NASA's 119 mission conditions need a test this ship doesn't carry" | "Every health app shows what it measures. Ours shows what your ship can't test." | E03, E27. D28, V76 |
| 0:05 to 0:08 | Today, Moon base: the Habitat line turns to "Ground link down · next contact in 26 h", Sim; To do does not move. Caption "Moon base · no ground for a day · nothing else changes" | "Then we cut Earth off for a day." | E23, E29. D18, D33 to D35, V81 |
| 0:08 to 0:13 | Three cuts: "My heart has been racing since this morning" and "Your pulse I can measure. Your rhythm I can't: that needs an ECG, and this kit has none."; the face glows, the tile sweeps, "Live, camera 2", the dot lands on the heart; the gap card "Heart rhythm: needs an ECG.", dashed ring. Caption "Medical AI on this laptop, offline · Live pulse from the webcam" | "Its offline AI measures what the kit can. Not the rhythm." | E04, E11, E17, E12, E05, E06, E27. D05, D06, D09, D12, D30, V78 |
| 0:13 to 0:22 | **Wow.** "Ilya Brandt, medical officer, is on a surface walk until 11:40." **Teach Tomas**; the base of the thumb lights; step 2 "Tomas: place the pads of your first and middle fingers on the inside of her wrist, at the base of her thumb." Real frame: fingers on the wrist, **Beat** tapped, a tick per tap (hard cut, caption "60 s count, trimmed"). Verdict line, then the ring line; 1.5 s without voice. Lower third "Live: this pulse and this count · Sim: Mara, the Moon base, the link" | "So it teaches a crewmate to check another way, and two independent readings have to agree." | E30, E31, E06, E03. D37 to D39, V85 to V87, V89 |
| 0:22 to 0:26 | The plan aboard: "1 Now", "2 Watch", "3 If it gets worse", "4 Medical officer", "Stabilize and watch", "Ground link down · next contact in 26 h (Sim). The log goes then. Nothing here waits for it." Then 1 s of **Someone needs help** with "Assistant: stopped" and "Works without the assistant". Caption "Built from NASA's Clinical Findings Forms · help works with the AI off" | "All 119 of NASA's conditions have a plan aboard. Earth gets the log." | E28, E26. D15, D32, D40, D41, V01 |
| 0:26 to 0:30 | End card: the figure with its rings, "Measures what it can. Shows what it can't. Teaches the crew the rest." Caption "Offline on one laptop and its webcam · NASA data: exploration condition list, Clinical Findings Forms, lunar communications architecture, NASA-STD-3001" | "For the Moon, Mars, and any clinic far from a doctor." | A still |

Say "All 119" only if the server counts 119 of 119 at the freeze; otherwise "[P] of NASA's 119 conditions have a plan aboard". Bracketed numbers come from the frozen release. This cut features the assistant and camera vitals; the streak, crew goal and journal are built in full and shown in the extended cut and on the project page.

## 4. Build priority

| # | Must work | Shots | Works by | Polished by |
|---|---|---|---|---|
| 0 | E01 hub and configuration serving, E02 sessions, E03 stage and cards matching `interaction-prototype.html` with a source word on every value, E21 fixture with `chc-harness demo reset` and `demo cue` | All | Sun 02:00 (E01 builds first; E02, E03 and E21 code the moment it passes) | Sun 10:00 |
| 1 | Webcam pulse: E05 request and attempts; E06 quality gate ("No reading" with its reason), Live origin, camera name, demo-operator record | C, D | Sun 08:00 | Sun 10:00, after the Q8 check against a hand count and the oximeter |
| 2 | Teach and check: E30 engine (Beat events, tolerance from `skills-catalog.json`, agree, correction, unchecked) and `pulse_check_wrist`; E03 A crewmate checks; E31 helper row with its reason | D | Sun 08:00 | Sun 10:00 |
| 3 | ICL10: E27 row and ECG gap with the form link; E28 plan and PlanRun, ground stage last; E13 assessment cards | C, D, E | Sun 08:00 | Sun 12:00 |
| 4 | Earth out of reach: E23 `lunar_surface` with confirmation and `eva` overlay; E29 link down and its two lines | B, E | Sun 06:00 | Sun 10:00 |
| 5 | Live assistant: E17 HuatuoGPT-3-9B on the M3, E12 kit check, E04 and E11 fixed questions first | C | Sun 08:00 | Clean live run by Sun 14:00 |
| 6 | Kit map: E27's 119 rows drafted and checked (Q7), counts served; E28 plan count | A, E | Sun 10:00 | Frozen Sun 14:00 |
| 7 | E26 help first screen, model stopped, within 500 ms | E | Sun 06:00 | Sun 10:00 |

Risk: priorities 1 and 4 rest on E05, E06, E23 and E29, which have briefs but no design in `docs/tdd/` yet. Their designs are written as soon as SYSTEM.md settles, and the demo's epics go first in every build queue. The evidence for the 119 kit map rows (what NASA's Clinical Findings Forms say confirms each condition) is being extracted now, ahead of E27.

Video schedule (Sunday): insurance takes of every shot on the latest green build 10:30 to 12:00; fallbacks locked at 12:00; freeze and tag `demo-freeze` at 14:00; final takes 14:00 to 15:30, hard stop; edit 15:30 to 17:30, length checked on the exported file (30.0 s or less); public upload, checked in a private window, by 18:00; submit at 20:00.

## 5. Recording plan

**Who.** Presenter: the subject (Live face and wrist) and the voice. An adult teammate plays Tomas: hands and Beat taps. Operator, off camera with the operator role: resets the fixture before each take, runs the cues (environment, link, model stop and start). Camera: a phone on a tripod, one fixed over-the-shoulder frame holding wrist, fingers, the presenter's face toward the webcam and the screen; the laptop records its own screen. Log keeper: slates each take with its `run_id` and logs the shot, the check result and whether it was used.

**Honest capture.**

1. Every take is a real run: frozen build, model live, presenter readings Live in the demo-operator record. Stub mode (pinned answers) never appears.
2. Editing draws nothing onto the interface; no caption states a value not on screen; Sim and Replay words stay visible.
3. Cuts remove waiting only, as hard cuts; the project page lists them with the measured AI turn time.
4. Retakes fix framing, light, audio or operator error, never the check's result or the pulse value. "They disagree" with its correction, or "Your count is kept, unchecked", is a designed result, and the voice holds for both.
5. Front light above 500 lux, presenter seated still (C07), stated on the project page.
6. The used take's artifacts (`eval/results/demo/<run_id>/`: Beat events with hub times, GuidedCheck, attempts) and the takes log go in the repository.
7. English captions; nobody under 18; no NASA logos, flags or mission identifiers.

**Fallbacks**, locked Sun 12:00, never a mockup or stub:

| Shot | If late | Then, still real |
|---|---|---|
| A | Rows not all checked | Caption "[C] of [M] conditions mapped so far, of NASA's 119"; with no counts, open on the ICL10 dashed ring |
| B | E23 | Stay on the station, link cut by the demo switch (Sim) |
| B | E29 | Drop the shot and every link claim; its 3 s go to the wow beat |
| C | Live model | The card shows its true stopped state; the reading starts from the pulse card. Voice "It measures what the kit can. Not the rhythm." |
| C, D | E06 | The check ends "Your count is kept, unchecked"; the voice stops at "check another way" |
| D | E30 | Teach steps and the real hand count, no verdict line |
| E | E28 | The gap card's "Check it another way", then help with the model stopped. Voice "Help works with the AI off. Earth gets the log." |
| All | Acts 3 to 5 not end to end | One real run per shot on the latest green build; the page says "cut from [n] runs" |

## 6. Extended 40 second cut

**Only where no time limit applies:** the repository README, and local judging once the Local Lead confirms in writing. The Space Apps "Link to project demo" always holds the 30 second cut. It adds the crew's day (two required features), a kept failure and a packing decision. Voice: 97 words.

| Time | Beat | On screen | Voice |
|---|---|---|---|
| 0:05 to 0:08 | What-if, after shot A | Planning view, "+ single-lead ECG: [n] rings fill"; the rings fill, then clear | "Pack an ECG, and those rings fill." |
| 0:08 to 0:12 | Daily check, station | "Check done.", "19 days in a row", "Crew: 24 of 28 checks this week.", Sim swatch. Caption "The journal stays private: the assistant never reads it" | "A daily streak rewards showing up, never the numbers." |
| 0:15 to 0:23 | Shot C with the failure | Before the Live pulse, deliberate movement: "No reading: too much movement", "Kept as failed, not guessed" | Shot C's line, then "Move, and it keeps the failure, never a guess." |

Shot B runs 0:12 to 0:15; shots D, E and F run 0:23 to 0:40. New epics: E27 what-if (D29, V77); E22 and E03 (D03, V41, V26); E06 (D08, V05).
