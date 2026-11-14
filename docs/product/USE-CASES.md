# Use cases and flows

Crew Health Check is one health dashboard for everyone aboard. Every situation a crew member meets, from the morning check to a crewmate who stops responding, starts on the same screen and follows a short, plain flow. This page lists who uses it, where, every use case the product covers, and how each one flows. The screens are in the [prototype](design/interaction-prototype.html); the sourced specifics live in three companion documents:

- [ENVIRONMENTS.md](ENVIRONMENTS.md): each mission environment, its hazards, its sensors and their limits.
- [ROUTINE.md](ROUTINE.md): every routine measurement and task, how often, and why.
- [RESPONDER-FLOWS.md](RESPONDER-FLOWS.md): what "Someone needs help" shows, step by step.

The plan aboard behind every condition (its stages, its four modes, teach and check, who helps, supplies and stay or return) is specified in [PRD.md](PRD.md) section 4.13.

Unreviewed draft: needs clinician review before live guidance.

## 1. Seven rules every flow follows

1. **Today says what needs you.** One screen answers "is anything wrong, and what do I do now?" Warnings first, then what is due, then everything else.
2. **Help is one tap away, always.** "Someone needs help" sits at the top of every screen. Its first steps are fixed, stored on the laptop and cached on every crew device: they never wait for the model, a sensor or the ground.
3. **Nothing assumes medical training.** Every task and every warning says what to do in plain words, step by step, and why. A crew member who has never taken a blood pressure can take one, and a crewmate who has never felt a pulse is taught on the body.
4. **The app knows where it is.** The mission environment (station, transit, Moon base, Mars base) and the activity (spacewalk, exercise, sleep) change which hazards are watched, which sensors matter, which tasks are due and what the ground link looks like.
5. **Every warning says who raised it.** A rule, a sensor limit or the assistant, with the evidence. The assistant can add a warning; it can never lower or clear one a rule or sensor raised.
6. **It is not the vehicle's alarm system.** Fire, depressurization and toxic atmosphere alarms belong to the vehicle. The app repeats them, points to the crew's procedure and looks after the people afterwards. It never replaces or silences an alarm.
7. **The ground is a reader, never a step.** Every plan, help flow, warning and routine task runs to its end with the link down. The log for the ground is always the last row and leaves at the next contact; a reply is advice for later and never unlocks anything. No path in this document ends at the ground.

## 2. Who uses it

| Actor | Who | What they need from the app |
|---|---|---|
| Crew member | Anyone aboard, checking on themselves | A short daily check, clear tasks with how-to steps, warnings that explain themselves, private notes |
| Helper | Any crewmate helping someone else, trained or not | Fixed first steps at once, then step-by-step guidance, a manual check taught on the body and checked by the cameras, the person's latest readings, a log that writes itself |
| Crew medical officer | The crew member with the most medical training; the second medical officer takes the role when the first is the patient or away | The same screens, plus the full records, the assessment for each open concern, the medical officer's steps of each plan and the crew's skill readiness |
| Ground medical team | Flight surgeon and support, seconds, minutes or a day away | The log the crew approved, at the next contact: what happened, what was done, what is being watched, what advice is welcome. Their reply is advice for later |
| Rule engine | Deterministic rules on the laptop | Raises warnings from answers and readings, sets how often tasks are due |
| Sensor monitor | Deterministic limits on habitat and wearable data | Raises warnings when a reading crosses a limit or a sensor goes quiet |
| Plan engine | Deterministic plans and the kit map on the laptop | Computes each condition's plan for this place, kit, stock and roster: which steps can run, which are not aboard and their substitutes |
| Role engine | Deterministic role rules | Proposes who does what in an emergency or a plan step, with its reason; the helper can change it |
| Assistant | The medical model on the laptop, offline | Asks the next question, asks for the next measurement, notices patterns, guides a helper from reviewed steps, and explains the plan aboard to the person doing it |

## 3. Where it is used

| Environment | What changes for the body | What changes in the app |
|---|---|---|
| Space station, low Earth orbit | Weightlessness, closed cabin air, radiation partly shielded by Earth's magnetic field, noise | CO2, oxygen, noise, radiation and water sensors; exercise and bone tasks; ground link up most of the time |
| Deep-space transit | Weightlessness for months, no magnetic shield, solar storms, growing distance | Radiation and storm shelter steps come first; the ground is minutes away and sometimes out of reach; the crew is the first responder; return is not an option |
| Lunar surface base | Partial gravity, lunar dust, surface walks, large temperature swings outside | Dust and particle sensors; checks after every surface walk for eyes, lungs and skin; the ground 6 to 10 seconds away when the link is up, and out of reach between contacts at the South Pole; a return to Earth takes days |
| Mars surface base | Partial gravity after a long transit, dust with toxic salts, a thin CO2 atmosphere outside | Dust and chemical sensors, readaptation tasks after landing, the ground 4 to 24 minutes away each way and out of reach for weeks at solar conjunction; return is not an option |

Activities overlay any environment: a **spacewalk or surface walk**, the hours **after** one, **exercise** and **sleep**. During sleep and spacewalks nothing routine is due; only emergencies speak. The sources, hazards, sensor limits and ground link figures for each environment are in [ENVIRONMENTS.md](ENVIRONMENTS.md).

## 4. Warnings

| Level | Shown as | Means | Example | Can be raised by |
|---|---|---|---|---|
| info | For your information | Worth knowing, nothing to do now | Radiation dose this week is above your usual | Rule, sensor limit, assistant |
| watch | Keep an eye on it | Something is drifting; a task or recheck is added | Cabin CO2 averaged over 7 days is at or above 2.5 mmHg | Rule, sensor limit, assistant |
| act | Act now | Do the steps shown, now | CO2 one-hour average above 3 mmHg; lunar dust above 0.3 mg/m³ after a surface walk | Rule, sensor limit |
| emergency | Emergency | Follow the emergency steps; "Someone needs help" opens | A crewmate reports chest pain | Rule, sensor limit, a person |

Every warning shows the same four things, in this order: **what is happening**, **why it matters for your body** (the affected regions light on the figure), **what to do** (numbered steps) and **who raised it, from what** (the rule, the sensor and its reading, or the assistant and its evidence). A warning stays until its cause clears; acknowledging it records that someone saw it, and does not close it. When a rule clears it, the app says why.

The assistant may raise "For your information" and "Keep an eye on it" from patterns across days. It may recommend pressing "Someone needs help". Anything at "Act now" or "Emergency" comes from a deterministic rule, a sensor limit or a person, so the most serious messages never depend on the model.

## 5. Routine

The mission routine is a list of measurements and tasks, each with a **cadence** (continuous, daily, every few days, weekly, monthly, per spacewalk, after an event, by mission phase, or as needed), the time it takes, the equipment, plain how-to steps and the reason for its frequency. [ROUTINE.md](ROUTINE.md) holds the list and its sources. Where no source gives a frequency, it says "frequency to be set by the flight surgeon".

- **Due is a suggestion, never a scold.** Missing a task records "not done" and moves it to the next slot. Nothing is shamed and the streak counts only the daily check.
- **Events change the frequency, for a while.** A trigger (a spacewalk, dust exposure, a radiation storm, a symptom report, a new environment) tightens one task for a stated time with its reason, then returns it to normal on its own: "Eye check: daily for 3 days, after dust on surface walk 4."
- **Not available is a real answer.** If the equipment is missing or broken, the task records that and nothing is filled in.
- **Drills keep skills fresh.** The drill task names the skills it practices (UC24). Completing it earns practice patches; nothing about how well it went is stored.

## 6. Use cases

| ID | Use case | Starts from | Raised by | Main screens |
|---|---|---|---|---|
| UC01 | Morning daily check | To do, or the daily check card | Routine | Daily check |
| UC02 | A routine task is due, with how-to steps | To do | Routine | Routine, task steps |
| UC03 | A task's frequency changes after an event | An event or a report | Rule | Routine, "Changed for now" |
| UC04 | A habitat sensor crosses a limit | Sensor monitor | Sensor limit | Today, warning, Habitat |
| UC05 | A radiation storm in transit | Sensor monitor, ground notice | Sensor limit, rule | Warning, storm steps, Routine |
| UC06 | Dust after a surface walk | End of a walk | Rule, sensor limit | Post-walk checks, Habitat |
| UC07 | A toxic release or fire aboard | The vehicle's alarm | The vehicle, then rule | Someone needs help, after-event checks |
| UC08 | "I feel unwell" | Assistant card or daily check | A person | Concern, measurement, assessment, the plan aboard |
| UC09 | Someone needs help: not responding | Someone needs help | A person | Help, emergency steps |
| UC10 | Someone needs help: injured or ill but responding | Someone needs help | A person | Help, "What happened?", help session |
| UC11 | The assistant notices a pattern | Assistant | Assistant | Today, warning, "How you are" |
| UC12 | Arriving in a new environment | Mission phase change | Rule | Habitat, Routine |
| UC13 | A routine task was missed | Routine | Rule | To do, Routine |
| UC14 | Talking to the ground with a delay or no link | Share, help session, plan | A person | For the ground, delivery |
| UC15 | Records and an existing result | Person button | A person | Records |
| UC16 | The assistant is stopped or unavailable | Anywhere | The system | Every screen, without the model |
| UC17 | A sensor fails or its data goes stale | Sensor monitor | Sensor limit | Habitat, warning |
| UC18 | An emergency shared with the whole crew | Someone needs help | A person | Every crew screen |
| UC19 | What can this ship test, and what is the plan for each? | The This kit card on Today | The kit map | This kit, the kit view (Test here, Plan here) |
| UC20 | A request the kit cannot perform, and what would close the gap | An assistant request, or the kit view's planning list | The kit map | Measurement request, assessment, This kit |
| UC21 | Earth out of reach for a day | The ground link goes down | The link | The Habitat line; nothing else |
| UC22 | Check it another way, with a crewmate taught on the body | A gap card or a plan step | A person | Assessment, A crewmate checks |
| UC23 | The medical officer is the patient or away | A help session or a plan step | The role engine | Someone needs help, the plan card |
| UC24 | A drill keeps a skill fresh | The routine | Rule | Routine, drill mode, Your streak |
| UC25 | A supply runs low | A step's use | Rule | To do, The kit aboard |
| UC26 | Stay or return on the Moon | A lunar plan with a return stage | A person | Plan detail: Return, Who decides |

### UC01 Morning daily check

A crew member opens the app after waking. "To do" lists the daily check first.

1. Anything new since yesterday? Nothing new, something feels wrong (goes to UC08), or prefer not to say.
2. Sleep: the band's estimate, confirmed or corrected.
3. How are you today: mood, energy, stress, private.
4. Due today: the routine tasks due this morning, each with its steps (UC02). Take, not available, or later.
5. Journal: optional, private, never read by the assistant.
6. Done: the streak and the crew goal move. Values never earn or cost anything.

Different by environment: after a surface walk the check adds the post-walk questions (UC06); in transit during a storm watch it adds the radiation questions (UC05).

### UC02 A routine task is due

"To do" shows the task with its cadence and minutes: "Blood pressure, weekly, 3 min, kit cuff". Opening it shows:

1. What it is, in one sentence, and why it is done this often.
2. What you need, laid out.
3. Numbered steps with a picture of where on the body (the figure lights the region).
4. Enter or capture the result; the app checks the format and the range of the device, never the health meaning.
5. What the result means for you: within your usual range, or a recheck is added (UC03), or a warning (section 4).

Not available, or later, are always offered.

### UC03 A frequency changes after an event

A trigger tightens a task with a reason and an end: while cabin CO2 is above its one-hour limit, "Daily check: add a headache and vision question at the next check and again 2 hours later" and "Vision self-check: daily while CO2 stays above the limit". After a radiation storm, "Dosimeter reading: after the storm, then daily for a week". The Routine screen shows every change under "Changed for now", each with its reason, its source and when it ends. When the trigger clears, the task returns to its usual cadence without anyone resetting it.

### UC04 A habitat sensor crosses a limit

The sensor monitor sees a reading over its limit, for example the one-hour CO2 mean above the habitat limit.

1. Today: the "To do" card shows "CO2 above the habitat limit, Act now" at the top; the stage title counts what needs attention.
2. The warning: what is happening (the reading, the limit, where, since when), why it matters (headache, poor focus, poor sleep; the head lights), what to do (the crew's air procedure, move air, check the fan, a headache and vision question added), who raised it (the CO2 sensor and the limit's source).
3. The Habitat screen shows the trend and the limit line.

The app does not decide whether the vehicle is safe. A reading far past the limit shows "Follow the vehicle's procedure" and the vehicle's alarm remains the authority.

### UC05 A radiation storm in transit

A solar storm warning arrives from the ground, or the radiation monitor shows a sharp rise.

1. Emergency: "Go to the storm shelter." The fixed shelter steps appear at once on every crew screen.
2. In the shelter: who is in, who is not; the dose rate and the dose so far, labeled by instrument.
3. Afterwards: the routine adds the post-storm tasks (dosimeter readings, symptom questions) for a stated time.

On the station and on surface bases the same flow uses that location's shelter; the steps come from the mission's reviewed procedure. With the ground link down, the onboard radiation monitor alone starts the flow.

### UC06 Dust after a surface walk

On the Moon or Mars, the end of a surface walk starts the post-walk checks: eyes (irritation, a foreign body), nose and throat, breathing, skin, and the particle count inside the airlock and the habitat. A high particle count raises "Act now" with the cleaning and filtering steps, and a check of eyes, skin and breathing for everyone in the area. Eye or breathing symptoms tighten the follow-up tasks (UC03) and may lead to UC08 or UC10.

### UC07 A toxic release or fire aboard

The vehicle's alarm sounds first. If anyone opens "Someone needs help", its first line says: "An alarm is sounding: follow the vehicle's emergency procedure first. Masks on." The app does not run the emergency. When the crew is safe, it opens the after-event checks for everyone exposed: symptoms, eyes, breathing, oxygen saturation where a kit oximeter is available, and each person's exposure noted. The log for the ground comes last and leaves at the next contact.

### UC08 "I feel unwell"

The concern flow, shown with the demo's racing heart. Every path ends aboard.

1. Describe it in your own words: "My heart has been racing since this morning."
2. Three red-flag questions that work without the model: chest pain or pressure, fainting, hard to breathe. Any yes opens "Someone needs help"; "not sure" is not a no. A racing heart that does not go away opens it too.
3. The assistant asks one question at a time: is it there all the time, or does it come and go?
4. A measurement it asks for, only if the kit can do it: "Your pulse I can measure with the cameras. Your rhythm I can't: that needs an ECG, and this kit has none." A failed reading is kept as failed.
5. The assessment: what was observed, what might explain it, and what this kit cannot test, with the NASA form that says why.
6. The plan aboard for the condition: check it another way when the confirming test is missing (a crewmate feels the pulse while the cameras count it, UC22), then do now ("Rest. No caffeine. Note each time it starts and stops."), watch (a re-check at the cameras and at tonight's check), if it gets worse (chest pain, fainting, hard breathing, or it won't stop: "Someone needs help" opens), and the medical officer's step when the officer is back inside. The plan runs to its end on board.
7. Last, the log for the ground: the crew member previews and approves it, it leaves at the next contact, and nothing waits for it.

### UC09 Someone needs help: not responding

Pressing "Someone needs help" asks first whether the air is safe (an alarm, smoke, a smell, hissing air), with the vehicle's procedure first if an alarm is sounding, then who needs help: me, or a crewmate by name. For a crewmate:

1. Any answer other than "the air is safe" opens that flow at once (toxic air, pressure loss, radiation shelter).
2. Are they responding? No.
3. Are they breathing normally? No, or not sure: the emergency steps start at once (call the crew, the reviewed resuscitation steps for this gravity, the defibrillator). These are fixed, stored on the laptop and cached on every crew device, and need no model. During compressions one line from the cameras gives the rate, or "can't see the hands" (experimental).
4. The crew is alerted on every screen (UC18). Below the steps, who does what: the helper, who brings the defibrillator and the medical officer, each with its reason (UC23). Stopping is the most qualified crewmate's decision.
5. The log records each step with its time, without typing. The log for the ground is drafted and leaves at the next contact; nothing waits for it.

If they are breathing, the steps for an unresponsive person who is breathing appear, then UC10.

### UC10 Someone needs help: injured or ill but responding

1. Air safe, who, responding, then "Is there heavy bleeding?" (yes opens the bleeding steps at once), then "What happened?": chips such as choking, chest pain, trouble breathing, fainted, seizure, hit the head, eye injury or dust in the eye, burn, toxic air, after a spacewalk, allergic reaction, bleeding, something else. Each chip and its steps come from responder-flows.json.
2. "Do this now": the fixed first steps for that chip, numbered and checkable. They show at once.
3. Who does what, below the steps: the role engine's proposals with their reasons (UC23).
4. The help session beside them: the assistant asks what the helper sees, one question at a time, and suggests the next step from the reviewed procedure, the plan aboard and what the kit holds. It never suggests a medicine or a dose that the procedure does not contain.
5. The person's latest readings, with their sources, and the kit measurements to take now.
6. The For the ground card previews the log drafted from the session. Pressing Tell the ground approves it and queues it; it leaves at the next contact, with the delay or the next contact shown. Nothing in the session waits for it.

"Someone needs help" is also where UC08 lands when a red-flag answer is yes.

### UC11 The assistant notices a pattern

The assistant reads the last days of records and raises at most "Keep an eye on it": "Three short nights, and your reaction time is slower." Its evidence is the person's own records only; a replayed habitat channel can be named beside it as a habitat fact, never as the person's exposure. The insight names its evidence, says it is a pattern, never a diagnosis, and offers one action (an earlier night, a recheck, a question). It can be dismissed, with no effect on rule warnings.

### UC12 Arriving in a new environment

A mission phase change (docking, landing, leaving orbit) switches the environment. The app says so plainly ("You are now on the lunar surface base") and shows what changed: new hazards, new sensors, new tasks and their cadence, the new ground link and its contact windows, and the kit for this place, with the plans recomputed for it. The first days after landing add readaptation tasks. In the prototype the environment switch is a demo control on the Habitat screen; in the product it is set by the mission phase, confirmed by a crew member.

### UC13 A routine task was missed

The task moves to its next slot with "not done yesterday". After a stated number of misses the app asks once whether something is in the way: no time, equipment missing, not needed now. The answer is recorded and goes into the next log for the ground. Nothing is shamed and the streak is untouched.

### UC14 Talking to the ground with a delay or no link

The ground link is up, delayed or down. Every outgoing log shows the delay when the link is up and the next contact when it is down. Logs queue and leave at the next contact as a catch-up: what happened, what was done, what is being watched, what advice is welcome. The help session and every plan never wait: with the link down the For the ground card reads "Ground link down · next contact in 25 h. The log is ready to go then. Nothing here waits for it." A reply arrives as a new record and changes no step.

### UC15 Records and an existing result

Every record of the day, measured or reported, with its source (Live, Replay, Sim), the failed readings kept, and existing results such as an ECG report added from a file and confirmed by the crew member before they count.

### UC16 The assistant is stopped or unavailable

Every screen still works: the daily check, routine, warnings from rules and sensors, the help flow with its fixed steps, every plan with its teach steps and camera checks, records and sharing. The assistant's card says it is stopped; nothing waits for it.

### UC17 A sensor fails or its data goes stale

A sensor that stops reporting shows "No data since 08:10", never its last value as if current, and never "safe". The monitor raises "Keep an eye on it: sensor quiet" and the routine adds a manual reading if one exists.

### UC18 An emergency shared with the whole crew

When anyone starts an emergency in "Someone needs help", every crew screen shows it at the top: who needs help, who is helping, where, and the roles accepted so far. Others can join as a second helper. The log is shared.

### UC19 What can this ship test, and what is the plan for each?

The This kit card on Today shows how many of NASA's 119 modeled conditions need a test this ship does not carry, and under it "[P] of 119 have a plan aboard". Opening it turns the figure to kit view: a lime dot where the kit measures, a hollow ring where only the person's report is possible, a dashed ring where the confirming test is not aboard. The list below groups the conditions by state; each line names the missing instrument and links to its NASA Clinical Findings Form. The pill pair **Test here** and **Plan here** switches the list's end column to each condition's plan ("Check another way · 5 steps aboard · medical officer step", with "Ground was for: follow-up (NASA form)"). The card **What would close the most gaps** lists instruments, supplies and training on the planning list as toggles; turning one on recomputes the counts and changes nothing aboard. Switching the environment switches the kit, so the marks and the plans change with the place. Every state and count is computed by the server; none is typed by hand.

### UC20 A request the kit cannot perform

When the assistant wants a measurement, the request card says "This kit can: camera station" or "This kit can't: needs an ECG". A request the kit cannot perform never reaches the person: it is recorded as an unresolved gap and shown on the assessment as "Can't test here: check it another way", with the form that says why. The gap is where the plan starts, never where it ends: the card offers the plan's substitute check (UC22) and the plan aboard follows. The gap goes into the log for the ground as a fact, never as the next step. On the kit view, the same gap counts toward the instrument, supply or training that would close it, so the crew's plan and the mission's packing list read the same data.

### UC21 Earth out of reach for a day

The ground link goes down, for example at the lunar South Pole between contacts.

1. The Habitat line changes: "Ground link down · next contact in 26 h", with its source word (Sim in the demo) and the kind of gap.
2. Nothing else changes: the same warnings, tasks, plans and help flows, with the same steps in the same order.
3. Logs for the ground queue with the time they leave: the next contact.
4. A planned gap raises nothing. Silence past the next contact plus [threshold to be set by clinical review] raises "Keep an eye on it: ground out of contact longer than planned", raised by a rule.
5. When the link returns, the catch-up log goes. A reply is a new record and changes no step.

### UC22 Check it another way, with a crewmate taught on the body

A condition needs a test this kit does not carry; the plan's first stage offers the best check the ship can do.

1. The gap card: "Rhythm: needs an ECG. Not aboard." Then: "Check it another way: a crewmate feels your pulse for 60 seconds while the cameras count it. It cannot replace an ECG."
2. The helper line, from the roster: "Tomas Lind · practiced the pulse check on day 12. Ilya Brandt, medical officer, is on a surface walk until 11:40." **Teach Tomas**, or **Not now**.
3. The "A crewmate checks" screen: the figure lights the landmark (the base of the thumb); one step at a time, the authored steps appear; the helper taps **Beat** for each beat for 60 seconds, then answers "Did it feel regular?" (yes, no, not sure).
4. The cameras read the person's face and the engine compares: "Your count 92 · cameras 96 · they agree"; or the skill's authored correction and a retry, with no value written; or "Cameras: No reading, too much movement. Your count is kept, unchecked."
5. Back on the assessment: "Felt regular by Tomas. 92 in 60 s; cameras 96. They agree. The ring stays: only an ECG answers the rhythm." The plan aboard continues with its watch stage.

A substitute check never confirms or excludes a condition, and the dashed ring stays.

### UC23 The medical officer is the patient or away

A help session opens, or a plan reaches a step that needs the medical officer or a skill.

1. The role engine proposes roles below the fixed steps, each with its reason: the second medical officer when the first is the patient or away ("Sofia Reyes · medical officer: Ilya is the patient"), then the crewmate with the freshest practice of the needed skill when both officers are unavailable.
2. The subject never gets a role, and nobody on a walk gets an on-site role.
3. The helper can change any role with **Change**; the change is logged with who made it.
4. Accepted roles go to every crew screen (UC18).

### UC24 A drill keeps a skill fresh

The routine's drill task is due ("Practice: pulse check", with its cadence and minutes).

1. Drill mode runs the same teach steps and camera checks as a real plan, on a crewmate's real wrist for the pulse check or on a manikin for CPR.
2. The camera's numbers are shown live to the trainee and never stored.
3. Completion updates each participant's skill records (fresh, due or stale) and counts toward the practice patches on Your streak.
4. Nothing about how well it went is kept, ranked or shown on a medical screen; the streak and the crew goal are untouched.

### UC25 A supply runs low

A plan step records a supply use, for example dressings during a bleeding plan.

1. Cover is computed from the stock and the plans' declared rate: "Dressings, [20] · cover for [n] uses".
2. Below [threshold to be set by the flight surgeon], To do shows "Keep an eye on it: [supply], cover for [n] more uses", raised by a rule.
3. A use that would go below zero is refused, and the step shows "Not available" with its substitute when one exists.
4. The planning view shows what adding that supply would change for the next mission.

### UC26 Stay or return on the Moon

A lunar plan with a return stage, for example a penetrating eye injury or chest pain that does not settle.

1. The plan's detail opens **Return**: the criteria from flight rules ("[to be set by flight rules]" until they exist), NASA's modeled chance that the condition ends the mission, treated and untreated, from its Clinical Findings Form, the trip ("a three-day lunar trip"), and what to prepare.
2. **Who decides**: "The commander and the medical officer. The app lists; it does not decide."
3. The plan's other stages keep running while the crew decides.

On Mars and in transit the Return card reads "Return is not an option from here. The plan stays aboard." This use case stays out of the demo until flight-rule criteria exist.

## 7. Where each use case appears on Today

| Today | Shows | Use cases |
|---|---|---|
| Stage title | The person, flight day and environment; the headline; how many things need attention | UC04, UC11, UC12 |
| Someone needs help | Always at the top | UC07, UC09, UC10, UC18, UC23 |
| To do | Warnings first, then what is due now, each with cadence and minutes, including a drill due and a supply's cover | UC01 to UC06, UC13, UC24, UC25 |
| Habitat | The environment, the ground link and the sensors that matter here, against their limits | UC04 to UC07, UC12, UC17, UC21 |
| How you are | Sleep, mood and reaction time against your usual | UC01, UC11 |
| This kit | What this ship can test: the count that needs a test not aboard, "[P] of 119 have a plan aboard", and the way into the kit view | UC19, UC20, UC22 |
| Daily check | Streak, patches, practice patches and crew goal | UC01, UC24 |
| Assistant | One insight, and "I feel unwell" | UC08, UC11, UC16 |

## 8. What the app does not do

- It does not replace the vehicle's caution and warning system, the crew's emergency procedures or the flight surgeon.
- It does not diagnose. It says what was observed, what might explain it and what this kit cannot assess. A check another way never confirms or excludes a condition.
- It does not treat. It holds a plan aboard: steps packed before launch, taught to a crewmate where the sources say anyone can do them, and the medical officer's steps "as in the onboard procedure". No medicine or dose appears that the procedure does not contain.
- It does not wait for the ground. The log for the ground is always last.
- It does not decide to return. On the Moon it lists the criteria; the commander and the medical officer decide.
- It does not judge values. Rewards count participation and practice, never readings or how well a drill went.
- It does not read the journal.
