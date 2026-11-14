# Someone needs help: responder flows

**Unreviewed draft: needs clinician review before live guidance.**

This file is the clinical content behind the always visible **Someone needs help** button. The same content, in machine form, is in [responder-flows.json](responder-flows.json). The saved text of every source is in [research/responder/](research/responder/).

## How to read this

* **Who is who.** The *helper* is any crew member, usually not a doctor. The *CMO* (crew medical officer) is the crewmate with extra medical training; NASA trains two per increment (RS68). The *ground* is the flight surgeon and specialists on Earth, reached over a delayed link that can be down for a day or more. The ground is a reader, not a step: every flow runs to its end with the link down, and its last row is the log for the ground, which leaves at the next contact. Nothing waits for it (RS41, RS08).
* **Fixed steps first.** The first screen and every flow's *Do this now* list are fixed text. They open at once, before any call to the language model, and work with the model and the network switched off.
* **Then the assistant.** HuatuoGPT-3-9B joins after the fixed steps are on screen. It asks one question at a time, asks for measurements and says why, shows reviewed treatment steps, and drafts the log for the ground. Its rules are in the section [The assistant's rules](#the-assistants-rules).
* **Medicines.** No dose appears in the product. Every medicine is given *as in the onboard procedure*, by the CMO; ground advice adds to it while the link is up and is never waited for in an emergency or during loss of signal (RS08). Where an Earth source states a dose for the same use, it is quoted below with its context, for reviewers only.
* **Numbers.** A number appears only when a source gives it. Otherwise the text says *threshold to be set by clinical review* or *to be set by flight rules*. One team default is marked as such (the first screen's *Not sure* answer).
* **Kit.** *If listed* means the step needs an item that the kit inventory (`get_capabilities`) says is on board. The demo kit is the camera station, a pulse oximeter, a BP cuff, a thermometer and an optional wrist band. A glucose meter is used only if one is carried.
* **Quotes** are exact and appear as `[RSxx] "text"`. A script checks each one against the saved source text.
* **Body regions** use the fixed set. An empty list means the flow is not about one place (the brief's null).
* **Historical source.** RS08 is the 2001 station medical checklist. It shows how onboard procedures are organised and tied to the flight surgeon. Where its first aid is out of date, a newer source is used and the difference is noted.

## The first screen

Fixed. No model. At most three questions come before the first steps or the problem list. Every screen also shows three buttons that never move: **Call crew** (raises the medical alarm), **Tell the ground** (the For the ground card previews the drafted log; pressing Tell the ground queues it, it leaves at the next contact, and nothing waits for it) and **Start over**.

**Is the air safe? Look and listen for an alarm, smoke, fire, a burning or sharp smell, or hissing air.** `air_safe`

| Answer | Goes to |
|---|---|
| Yes: no alarm, smoke or smell | question `responding` |
| Smoke, fire, or a burning, sharp or chemical smell | flow: Toxic air, chemical exposure or smoke (`toxic_exposure`) |
| Pressure alarm, hissing air, or ears popping | flow: Cabin pressure loss (`pressure_loss`) |
| Radiation alarm or solar storm warning | flow: Solar particle event: take shelter (`solar_particle_event`) |
| Not sure | flow: Toxic air, chemical exposure or smoke (`toxic_exposure`) |

**Is the person awake and answering you? Say their name loudly and gently shake their shoulders. If you are the person who needs help, answer Yes.** `responding`

| Answer | Goes to |
|---|---|
| Yes, they answer | question `severe_bleeding_check` |
| No, or not sure | question `breathing` |

**Are they breathing normally? Look, listen and feel for up to 10 seconds. Gasping is not normal breathing.** `breathing`

| Answer | Goes to |
|---|---|
| Yes, breathing normally | flow: Not responding but breathing normally (`unresponsive_breathing`) |
| No, gasping, or not sure | flow: Not responding and not breathing normally (`not_breathing`) |

**Is there heavy bleeding? Blood pumping or pouring out, or bleeding that does not slow when you press on it.** `severe_bleeding_check`

| Answer | Goes to |
|---|---|
| Yes | flow: Severe bleeding (`severe_bleeding`) |
| No | question `what_happened` |

**What is the main problem? Pick the closest.** `what_happened`

| Answer | Goes to |
|---|---|
| Choking | flow: Choking (`choking`) |
| Chest pain or tightness | flow: Chest pain (`chest_pain`) |
| Trouble breathing | flow: Trouble breathing (`breathing_difficulty`) |
| Fainted or nearly fainted | flow: Fainting (`fainting`) |
| Seizure, fit or jerking | flow: Seizure (fit) (`seizure`) |
| Hit the head | flow: Head injury (`head_injury`) |
| Eye injury, chemical or dust in the eye | flow: Eye injury or something in the eye (`eye_injury`) |
| Burn or scald | flow: Burns (`burns`) |
| Chemical, smoke or toxic air exposure | flow: Toxic air, chemical exposure or smoke (`toxic_exposure`) |
| Symptoms after a pressure loss | flow: Cabin pressure loss (`pressure_loss`) |
| Symptoms during or after a spacewalk | flow: Decompression sickness after a spacewalk (`dcs_after_eva`) |
| Radiation or solar storm | flow: Solar particle event: take shelter (`solar_particle_event`) |
| Allergic reaction with swelling or breathing trouble | flow: Severe allergic reaction (`severe_allergic_reaction`) |
| Shaky, sweaty, confused (low blood sugar?) | flow: Low blood sugar (`low_blood_sugar`) |
| Severe belly or side pain | flow: Severe belly or side pain (`severe_abdominal_pain`) |
| Mental health crisis | flow: Mental health crisis (`mental_health_crisis`) |
| Bleeding | flow: Severe bleeding (`severe_bleeding`) |
| Something else, or I don't know | flow: Something else, or not sure (`something_else`) |

Notes on the first screen:

* The air question comes first because a helper who is overcome cannot help. The crew's own protective response always comes before care for the person.
* *Not sure* about the air goes to the toxic air flow, whose first step is masks on. This is a **team default**, to be confirmed by clinical and flight review.
* *Not sure* about breathing goes to chest compressions. The sources say to assume cardiac arrest when in doubt.
* A person pressing the button for themselves answers Yes to the second question and goes straight to the bleeding check and the problem list.

Evidence:

* [RS41] "emergencies fall into four categories: Rapid Depressurization, Fire, Toxic Spill, and Medical emergency." Why the air question comes first: three of the four emergency types are about the cabin, not the person.
* [RS01] "Check if they respond by gently shaking their shoulders and asking them loudly, 'are you ok?'."
* [RS01] "Look, listen, and feel for up to 10 seconds for normal breathing."
* [RS02] "If there is any doubt, assume cardiac arrest and start CPR." Why not sure goes to compressions.
* [RS05] "Sometimes when a person is unresponsive their breathing may become noisy or irregular, or they may be gasping."
* [RS12] "Blood is pumping from the wound" One of the signs of a life-threatening bleed.
* [RS12] "The bleeding cannot be stopped or slowed with pressure" Same list.

## Flows

### 1. Not responding and not breathing normally

`not_breathing` &middot; Body regions: heart, lungs

The person does not answer and is not breathing normally. Treat this as a stopped heart. Start chest compressions now. Do not wait for anyone.

**When it opens.** First screen: not responding, and breathing is absent, gasping or not sure. Any other flow sends you here if the person stops responding and is not breathing normally.

**Do this now** (fixed, no model):

1. Raise the medical alarm the way your vehicle's procedure says. Call every crewmate.
2. Start chest compressions now. If you are in doubt, start anyway.
3. Push hard and fast in the centre of the chest: 100 to 120 pushes a minute, 5 to 6 cm deep. Let the chest come back up after each push.
4. In weightlessness, lock yourself to them first: your left leg over their right shoulder, your right leg around their body, ankles locked behind their back. This is the Evetts-Russomano hold.
5. If you cannot push well that way, hold them from behind with your arms around their chest (reverse bear hug) and keep pushing.
6. A second crewmate brings the AED (the box that can shock the heart) and the restraint board. Attach the pads and do what the AED voice says.
7. Only shock when the person is strapped to the restraint board on a surface that does not carry electricity.
8. Once they are strapped down, keep pushing from the handstand position if the cabin allows, or from beside or across them.
9. If you are trained, give 2 rescue breaths after every 30 pushes. If not, keep pushing without stopping.
10. Do not stop to use this app. When a crewmate is free, they press Tell the ground: the log leaves at the next contact, and nothing here waits for it.

**Questions the assistant asks**, one at a time:

* To the free crewmate: when did they collapse, or when were they last seen well?
* Did anyone see what happened: an electric shock, smoke or toxic air, an injury, choking or a fall?
* Is the AED attached? What did it say last?
* Are they now breathing normally, moving or waking up?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Heart rhythm | The AED checks the rhythm itself. Keep hands off only while it says so. | AED |
| Oxygen level and pulse, only once they breathe normally again | Clip on a finger, hand warm and still, wait for one steady number. Never pause compressions to do this. | pulse oximeter |
| Blood pressure, only once they breathe normally again | Cuff on the bare upper arm, as the device instructions say. The CMO decides when. | BP cuff |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Chest compressions: Evetts-Russomano hold first, reverse bear hug if compressions are not good enough | any crew | none | RS04, RS01, RS02 |
| Rescue breaths after every 30 compressions if trained; otherwise continuous compressions | any crew | face shield or pocket mask, if listed | RS01, RS02 |
| Strap the person to the restraint board, then compress in the handstand position, or the standard or straddling position | any crew | crew medical restraint system, if listed | RS04 |
| Attach the AED and follow its voice, with the person restrained on an electrically isolated surface | any crew | AED, if listed | RS02, RS04 |
| Airway device, venous access and medicines, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS04, RS08 |
| Decide when to stop CPR: the most medically qualified crewmember, with the ground while the link is up, or alone when it is not | crew medical officer | none | RS04 |
| Log for the ground. Voice or video support joins only while the link is up, a crewmate is free to handle it and it does not lower CPR quality. It leaves at the next contact; nothing waits for it. | ground | message link; voice and video while the link is up | RS04 |

**Log for the ground.** Log for the ground at once, by a crewmate who is not doing compressions. It leaves at the next contact. Never slow or pause compressions for it, and never wait for the answer.

**The assistant may:**

* Show a compression beat at 100 to 120 a minute and a clock of minutes since the start.
* Ask the free crewmate one short question at a time about what happened.
* Show the AED and restraint steps from the reviewed procedure.
* Draft the log for the ground for a crewmate to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Ask the person doing compressions anything.
* Tell the team to stop CPR. That decision belongs to the most medically qualified crewmember.
* Say the heart has restarted because of a camera or oximeter number.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The ground is usually reachable. Use their support when it does not lower CPR quality and enough crew are present (RS04). |
| `transit` | Same weightless holds and restraint rules as on a station. The link delay grows with distance, so the crew leads and never waits for the ground (RS41). |
| `lunar_surface` | Partial gravity. The holds above were studied for weightlessness. A different technique has been suggested for low gravity (RS04). Which method to use here: to be set by clinical review. |
| `mars_surface` | Each message takes 3 to 23 minutes one way, so live ground help during CPR is impractical (RS04). The crew decides alone. Method for partial gravity: to be set by clinical review. |
| `eva` | A person in a suit cannot get chest compressions. Bring them inside as the spacewalk emergency procedure says, then start this flow. Those steps: to be set by flight rules and clinical review. |

**Evidence:**

* [RS02] "If a person is unresponsive with abnormal breathing, assume they are in cardiac arrest."
* [RS02] "If there is any doubt, assume cardiac arrest and start CPR."
* [RS01] "Look, listen, and feel for up to 10 seconds for normal breathing."
* [RS01] "Push at a rate of 100 to 120 per minute."
* [RS02] "Compress to a depth of at least 5 cm, but not more than 6 cm."
* [RS01] "If you are trained to do so, after 30 compressions, provide two rescue breaths."
* [RS01] "If you are unable or unwilling to provide rescue breaths, give continuous chest compressions."
* [RS04] "CPR in microgravity is feasible and should be applied according to the Earth-based guidelines of the AHA/ERC in relation to fundamental statements, like urgent recognition and action, focus on high-quality chest compressions, compression depth and compression-ventilation ratio." This is why the Earth rate and depth carry over to weightlessness.
* [RS04] "For initial BLS at the site of emergency, the Evetts-Russomano method (ER) SHOULD be applied initially. If the rescuer cannot perform adequate chest compressions with the ER method, the rescuer should switch to the Reverse-Bear-Hug method (RBH)."
* [RS04] "The rescuer places their left leg over the patient’s right shoulder and the right leg around the patient’s torso."
* [RS04] "However, without restraint rescuer and patient could drift and collide with the sides of the spacecraft and, thus, pose a potential risk."
* [RS04] "As soon as the patient has been restrained on the Crew Medical Restraint System chest compressions SHOULD be applied using the Handstand-method (HS) if favored by the dimensions of the spacecraft and provider height."
* [RS04] "A defibrillator SHOULD only be used on a patient that is restrained to an electrically isolated and safe surface."
* [RS02] "As soon as an AED is available, attach it and follow the AED instructions."
* [RS02] "Anyone can use an Automated External Defibrillator (AED)."
* [RS04] "In low earth orbit telemedicine support SHOULD be consulted in the event of a cardiac arrest, when it seems feasible and the manpower for its application is present."
* [RS04] "Communication delay from Mars to Earth is estimated to range between 3 and 23 min each way"
* [RS04] "The decision to terminate resuscitation SHOULD be made by the crewmember with the highest medical qualification after consultation with telemedicine support."
* [RS04] "the Mackaill-Russomano-technique, have been suggested for the special envirnonment of hypogravity" Spelling as in the source. The guideline does not recommend a method for the Moon or Mars.
* [RS07] "For CP1 and CP2, ground support will not be necessary as seconds matter and the algorithm will be very well-delineated." NASA modeling form for sudden cardiac arrest: the crew acts first.
* [RS08] "Unstow bungees and begin CPR only after patient is restrained." Historical 2001 station checklist. Superseded on this point by RS04, which starts compressions at once with the Evetts-Russomano hold.

**To be set by clinical review or flight rules:**

* CPR technique in lunar and Martian gravity.
* Whether the vehicle carries an AED, a restraint board and a face shield (the kit inventory answers this at run time).

Sources for this flow: RS01, RS02, RS04, RS07, RS08, RS41, C08.

### 2. Not responding but breathing normally

`unresponsive_breathing` &middot; Body regions: head, lungs

The person does not answer, but they are breathing normally. Keep their airway open and watch their breathing the whole time.

**When it opens.** First screen: not responding, breathing normally.

**Do this now** (fixed, no model):

1. Call every crewmate and the CMO (crew medical officer, the crewmate trained for medical care).
2. Keep watching their breathing. If it stops or turns to gasping, start chest compressions at once (Not breathing flow).
3. Open the airway: tilt the head back gently and lift the chin.
4. Where there is gravity, roll them onto their side in the recovery position. In weightlessness, how to hold or strap them: to be set by clinical review.
5. If a head or neck injury is possible, keep the head and neck in line with the body while you keep the airway open.
6. Stay with them. Do not leave them alone.

**Questions the assistant asks**, one at a time:

* What happened just before? A blow to the head, a faint, a fit, or an alarm?
* How long have they been like this?
* Do they have diabetes or any known illness, or take any medicine?
* Is there any injury or bleeding you can see?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Breathing rate from the cameras | Only if the person is already still in front of the cameras. Never delay a step for it. If it fails, the helper counts breaths and enters the number. | camera station |
| Blood sugar | Only if the onboard kit carries a glucose meter. Use it as its instructions say and enter the number in mg/dL or mmol/L. The level that needs action: threshold to be set by clinical review. | glucose meter (only if carried) |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Keep the airway open; recovery position where there is gravity; head and neck in line if injury is possible | any crew | none | RS06 |
| Keep checking breathing and start compressions if it becomes abnormal | any crew | none | RS02, RS06 |
| Oxygen and further care, as in the onboard procedure | crew medical officer | oxygen supply, if listed | RS08 |
| Log for the ground: the cause and the next steps, for their review. It leaves at the next contact; nothing waits for it. | ground | message link | RS25, RS29 |

**Log for the ground.** Log for the ground now: a person who does not wake up needs the flight surgeon's review. It leaves at the next contact; the crew leads, and nothing here waits for it.

**The assistant may:**

* Remind the helper every few minutes to check breathing (interval to be set by clinical review).
* Ask the helper one question at a time about what happened.
* Request the oximeter reading and say why.
* Draft the log for the ground for the helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Suggest leaving the person alone.
* Suggest giving food, drink or medicine by mouth to someone who is not awake.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The ground link is usually up: the log leaves at once. The crew still leads. |
| `transit` | Longer link delay. The crew leads (RS41). |
| `lunar_surface` | Partial gravity: whether the recovery position works as on Earth is to be set by clinical review. |
| `mars_surface` | Partial gravity as on the Moon. Long link delay: the crew leads. |
| `eva` | A suited person who stops responding: follow the spacewalk emergency procedure to bring them inside, then this flow. To be set by flight rules. |

**Evidence:**

* [RS06] "It’s safe to place someone in the recovery position who is not responding to you but is breathing normally."
* [RS06] "When someone is put into the recovery position, their airway is kept open and any vomit can drain away without interfering with their breathing." The draining relies on gravity.
* [RS06] "Gently tilt the casualty’s head back and lift their chin to make sure their airway stays open."
* [RS06] "Try to keep the head and spine in a straight line where possible while maintaining an open airway."
* [RS02] "Slow, laboured breathing, as well as other abnormal patterns such as agonal gasping or panting, must be recognised as signs of cardiac arrest."
* [RS25] "has been knocked out and has not woken up" From the list of reasons to call an ambulance after a head injury.

**To be set by clinical review or flight rules:**

* How to position and secure an unresponsive, breathing person in weightlessness.
* How often the helper rechecks breathing.

Sources for this flow: RS02, RS06, RS08, RS25, RS29, RS41, C08.

### 3. Choking

`choking` &middot; Body regions: neck, lungs

Something is stuck in the throat. If they can cough, let them cough. If they cannot breathe, cough or make a sound, act now.

**When it opens.** Problem list: choking. Or the person holds their throat and cannot speak or breathe after eating or drinking.

**Do this now** (fixed, no model):

1. Ask: are you choking? If they can cough, tell them to keep coughing.
2. If they cannot breathe, cough or make any noise, give up to 5 sharp back blows between the shoulder blades with the heel of your hand.
3. If that fails, give up to 5 abdominal thrusts: from behind, a fist between the belly button and the bottom of the chest, your other hand over it, pull sharply in and up.
4. Check the mouth after each blow and each thrust.
5. Repeat 5 back blows and 5 thrusts until the object comes out.
6. If they stop responding, start chest compressions (Not breathing flow).
7. In weightlessness you will push each other apart. Brace against a wall or hold them with your legs. How to brace: to be set by clinical review.

**Questions the assistant asks**, one at a time:

* To the person: can you speak or cough?
* Did the object come out?
* Any pain in the throat or chest, or trouble swallowing now?
* What were they eating, drinking or holding?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Oxygen level (SpO2) and pulse, after the airway is clear | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Breathing rate from the cameras, after the airway is clear | Only if the person is already still in front of the cameras. Never delay a step for it. If it fails, the helper counts breaths and enters the number. | camera station |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Back blows and abdominal thrusts, in cycles of 5 and 5, until the object comes out | any crew | none | RS09, RS03, RS10 |
| Check the mouth after each blow or thrust | any crew | none | RS09 |
| Advanced airway care if the object stays stuck, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS10, RS08 |
| Log for the ground after the event, as a routine report. It leaves at the next contact; nothing waits for it. | ground | message link | RS10 |

**Log for the ground.** Log for the ground after the event, as a routine report, or at once if the person stopped responding at any point or breathing stays hard. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Count the back blows and thrusts on screen.
* After the event, ask one question at a time about pain and swallowing.
* Draft the routine report for the helper to preview.

**The assistant must never:**

* Ask the helper anything while they are giving blows or thrusts.
* Tell anyone to sweep the mouth blindly with a finger.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Weightless: rescuer and person push apart. Bracing method: to be set by clinical review. |
| `transit` | Same as the station. No time to ask the ground first (RS10). |
| `lunar_surface` | Partial gravity: how well Earth technique works here is to be set by clinical review. |
| `mars_surface` | As on the Moon. The link delay does not matter: the crew acts first (RS10). |
| `eva` | Choking inside a suit is outside this flow. Follow the spacewalk emergency procedure. |

**Evidence:**

* [RS09] "If they cannot breathe, cough, or make any noise, then they need your help straight away."
* [RS09] "With the heel of your other hand, give them five sharp back blows between their shoulder blades."
* [RS09] "Place one hand in a clenched fist between their belly button and the bottom of their chest."
* [RS09] "With your other hand, grasp your fist and pull sharply inwards and upwards up to five times."
* [RS09] "After each back blow, check to see if there’s anything in their mouth."
* [RS03] "For adults with severe FBAO, repeated cycles of 5 back blows (slaps) followed by 5 abdominal thrusts should be performed until the object is expelled or the person becomes unresponsive." FBAO means the airway is blocked by a foreign body.
* [RS09] "If they become unresponsive at any point, prepare to start adult CPR"
* [RS10] "Best case treatment is the Heimlich maneuver." NASA modeling form for choking.
* [RS10] "There is no time for consult ground teams before intervening and no need after beyond a routine notification."

**To be set by clinical review or flight rules:**

* How to brace for back blows and thrusts in weightlessness.

Sources for this flow: RS03, RS08, RS09, RS10, C08.

### 4. Severe bleeding

`severe_bleeding` &middot; Body regions: none (not about one place)

Blood is pouring or pumping out, or the bleeding will not slow. Press hard on it and keep pressing.

**When it opens.** First screen: heavy bleeding. Problem list: bleeding.

**Do this now** (fixed, no model):

1. Put on gloves if they are at hand. Do not wait for them.
2. Press hard on the wound with a dressing or a clean cloth. Keep pressing.
3. If something is stuck in the wound, do not pull it out. Press around it.
4. If blood is pumping out, or pressing does not slow it, put a tourniquet from the kit on the arm or leg: 5 to 7 cm above the wound, never over a joint. Tighten until the bleeding stops. Do not loosen it. Write down the time.
5. If blood soaks through, take the dressing off and press again with a new one.
6. In weightlessness, blood floats away as drops. Catch it with gauze or suction.
7. Call the CMO. Keep pressing until the CMO takes over.

**Questions the assistant asks**, one at a time:

* Where is the bleeding? Tap the place on the body map.
* What caused it: a sharp edge, a crush, a fall?
* Any other injury?
* Do they feel faint, cold or thirsty?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Blood pressure | Put the cuff on an arm that is not injured, as the device instructions say. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Pulse from the cameras | Only if the person can already sit still at the camera station. Never move them there or delay a step for it. Movement gives No reading, which is kept as a failed reading. | camera station |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Direct pressure with a dressing or a clean cloth | any crew | dressings, gloves | RS11, RS13 |
| Tourniquet for limb bleeding that pressure cannot control | any crew | tourniquet, if listed | RS12 |
| Haemostatic dressing (a dressing that helps blood clot), as in the onboard procedure | crew medical officer | haemostatic dressing, if listed | RS12 |
| Suction or rinsing to clear floating blood from the wound | crew medical officer | suction, if listed | RS14 |
| Fluids into a vein and other treatment for shock, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS14, RS08 |
| Log for the ground: the injury and what was done. Voice and video support joins only while the link is up. It leaves at the next contact; nothing waits for it. | ground | message link; voice and video while the link is up | RS14 |

**Log for the ground.** Log for the ground at once, and keep voice and video open while the link is up. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Start a timer when a tourniquet goes on and keep it on screen.
* Ask a free crewmate for the BP and pulse and say why.
* Draft the log for the ground for the helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Tell anyone to loosen a tourniquet.
* Tell anyone to pull out an object stuck in a wound.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Floating blood: catch drops with gauze or suction (RS14). The ground link is usually available. |
| `transit` | As on the station. Longer delay: the crew leads. |
| `lunar_surface` | Lunar dust is abrasive and can reach wounds; how to clean a dusty wound: to be set by clinical review. |
| `mars_surface` | Martian dust is likely toxic (RS35). Keep wounds covered. Long delay: the CMO leads. |
| `eva` | A wound with a suit leak is a suit emergency first. Follow the spacewalk emergency procedure, then treat inside. |

**Evidence:**

* [RS11] "With open wounds there’s a risk of infection, so wear protective first aid gloves (if available) to help prevent any infection passing between you both."
* [RS11] "Apply direct firm pressure to the wound using a sterile dressing if possible or a clean non-fluffy cloth to stop the bleeding."
* [RS11] "If there’s an object in the wound, don’t pull it out."
* [RS11] "Your priority is to stop the bleeding."
* [RS11] "If blood comes through the dressing, remove it and reapply pressure with a new dressing or pad to control the bleeding."
* [RS12] "Blood is pumping from the wound" From the signs of a life-threatening bleed.
* [RS12] "The bleeding cannot be stopped or slowed with pressure" Same list.
* [RS12] "Place the tourniquet around the limb approximately five to seven centimetres above the injury."
* [RS12] "Never place a tourniquet directly over a joint."
* [RS12] "Once a tourniquet is applied it should not be loosened as the bleeding will start again."
* [RS12] "Record the time on the time tab (if available) and monitor your casualty until the ambulance arrives."
* [RS13] "Keep pressure on the wound until help arrives."
* [RS14] "Irrigation and suction are needed to remove floating blood from wound sites" NASA modeling form for traumatic shock.
* [RS14] "In both the best and worst case scenarios continuous AV communication is necessary if available given the significant risk of loss of life and limb to a crew member suffering from significant trauma." AV means audio and video.

**To be set by clinical review or flight rules:**

* Whether the kit holds a tourniquet and haemostatic dressings (kit inventory).
* Cleaning dusty wounds on the Moon and Mars.

Sources for this flow: RS08, RS11, RS12, RS13, RS14, RS35, C07, C08.

### 5. Chest pain

`chest_pain` &middot; Body regions: heart, lungs

Chest pain can come from the heart, the lungs or the muscles. This kit cannot tell which. Treat new or severe chest pain as serious.

**When it opens.** Problem list: chest pain or tightness. The I feel unwell screen also opens it.

**Do this now** (fixed, no model):

1. Stop all activity. Help them rest, sitting with knees bent and back supported, or held still in weightlessness.
2. Call the CMO.
3. Bring the AED close. If they stop responding and are not breathing normally, start chest compressions.
4. If the pain started during or after a spacewalk, also open the DCS flow.
5. Give no medicine unless the CMO gives it, as in the onboard procedure.

**Questions the assistant asks**, one at a time:

* When did it start, and what were you doing?
* Is it tight, crushing or squeezing? Does it spread to the arms, neck, jaw or back?
* Are you short of breath, sweating, feeling sick or lightheaded?
* Does it change when you breathe in or press on the chest?
* Did it start during or after a spacewalk or hard exercise?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Pulse from the cameras | Only if the person can already sit still at the camera station. Never move them there or delay a step for it. Movement gives No reading, which is kept as a failed reading. | camera station |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Rest and stop all exertion | any crew | none | RS15, RS16 |
| Medicines for a suspected heart attack, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS08, RS16 |
| Diagnosis needs an ECG and blood tests. This kit has neither. Check it another way: pulse, blood pressure and oxygen, repeated on the onboard plan's cadence. They cannot replace an ECG | any crew | camera station, pulse oximeter, BP cuff | S04, C11 |
| Log for the ground: the symptoms, the readings and the missing tests. It leaves at the next contact; nothing waits for it. | ground | message link | RS08 |

**Log for the ground.** Log for the ground at once if the pain is tight or squeezing, spreads to the arms, neck or jaw, or comes with breathlessness, sweating, sickness or fainting; otherwise with the next log. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Ask the questions above one at a time.
* Request a camera pulse, oximeter and BP reading and say why.
* Say plainly what this kit cannot test: an ECG would answer this, and this kit has none.
* Draft the log for the ground for the person or helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Say the heart is fine because a pulse, oxygen or BP number is normal.
* Tell the person to keep working or exercising.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The ground link is usually up; the flight surgeon can read the log quickly. The crew still leads. |
| `transit` | Longer delay. The CMO leads; the log leaves at the next contact, and nothing waits for it. |
| `lunar_surface` | Rest sitting. The link may be down for a day: the plan aboard runs to its end, and the log leaves at the next contact. |
| `mars_surface` | Long delay: the CMO leads; the log leaves at the next contact, and nothing waits for it. |
| `eva` | Chest pain during a spacewalk: report it at once and follow the flight rules for ending the spacewalk. Heart and lung signs after a spacewalk can be serious DCS (RS49). |

**Evidence:**

* [RS15] "you have chest pain that feels tight or like squeezing on your chest" From the list of reasons to call an ambulance.
* [RS15] "you have chest pain that's spreading to your arms, neck or jaw" Same list.
* [RS15] "Sit and rest on the floor in a comfortable position, with your knees bent and your back supported."
* [RS16] "Help move the casualty into a comfortable position."
* [RS16] "Give them one aspirin tablet (300mg) and ask them to chew it slowly." Earth first aid for a suspected heart attack while waiting for an ambulance, on a page that also says the next line. Onboard, any medicine is given as in the onboard procedure; the assistant never states this dose.
* [RS16] "Do not give aspirin to the casualty if they are under 16 or if they are allergic to it."
* [RS08] "Contact Surgeon before giving any medication marked with an asterisk. In an emergency or during Loss of Signal, begin appropriate treatment; then call Surgeon as soon as possible." Historical 2001 station checklist. Shows how onboard medicine use is tied to the flight surgeon.

**To be set by clinical review or flight rules:**

* Which chest pain medicines the onboard procedure allows, and who gives them.

Sources for this flow: RS08, RS15, RS16, RS49, C07, C08, C11, S04.

### 6. Trouble breathing

`breathing_difficulty` &middot; Body regions: lungs

Breathing is hard or fast. The cause may be the air, the lungs, the heart, an allergy or a pressure change. Check the air first.

**When it opens.** Problem list: trouble breathing.

**Do this now** (fixed, no model):

1. Check the air first. If there is an alarm, smoke or a smell, put on masks and open the Toxic air flow.
2. Help them sit up, or hold them upright in weightlessness.
3. Call the CMO.
4. If their lips or skin turn pale, blue or grey, they become confused, or they cannot get words out, call everyone.
5. If they stop responding and are not breathing normally, start chest compressions.
6. After a spacewalk or a pressure loss, also open the DCS flow. With swelling of the lips or face, open the Allergic reaction flow.
7. A normal oxygen reading does not mean the problem is gone.

**Questions the assistant asks**, one at a time:

* Did it start suddenly or slowly?
* Any chest pain, cough, or coughing blood?
* Any smoke, chemical smell or alarm?
* Swelling of the lips, tongue or throat, or a rash?
* Did it start after a spacewalk or a pressure change?
* Any blow to the chest?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. Carbon monoxide from a fire can make this number look normal when it is not. | pulse oximeter |
| Breathing rate from the cameras | Only if the person is already still in front of the cameras. Never delay a step for it. If it fails, the helper counts breaths and enters the number. | camera station |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Sit upright and rest | any crew | none | RS57, RS18 |
| Reliever inhaler, if the person has one or the kit lists one, as in the onboard procedure | crew medical officer | inhaler, if listed | RS18 |
| Oxygen, as in the onboard procedure | crew medical officer | oxygen supply, if listed | RS08 |
| Log for the ground: the cause and further care, for their review. It leaves at the next contact; nothing waits for it. | ground | message link | RS17 |

**Log for the ground.** Log for the ground at once if breathing is very hard or suddenly worse, or comes with a tight chest, pale, blue or grey lips, or confusion. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Ask the questions above one at a time and route to the matching flow.
* Request oximeter and breathing rate readings and say why.
* Draft the log for the ground for the person or helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Say breathing is fine because the oxygen number is normal.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Check the cabin air card and alarms first. The ground link is usually available. |
| `transit` | As on the station. Longer delay: the CMO leads. |
| `lunar_surface` | Dust brought inside can irritate the airways (RS34). |
| `mars_surface` | Martian dust is likely toxic (RS35). Long delay: the CMO leads. |
| `eva` | Breathing trouble in a suit is a suit emergency first. Follow the spacewalk emergency procedure. |

**Evidence:**

* [RS17] "your chest feels tight or heavy" From the list of reasons to call an ambulance for shortness of breath.
* [RS17] "you feel suddenly confused" Same list.
* [RS15] "your lips or skin are pale, blue or grey" From the heart attack page's list of reasons to call an ambulance.
* [RS18] "Sit them down in a comfortable position." Asthma attack first aid.
* [RS18] "Reassure the casualty and ask them to take their usual dose of their reliever inhaler (usually blue)." For a person with asthma and their own inhaler. Onboard, inhaler use follows the onboard procedure.
* [RS57] "If they are having breathing difficulties, then sit them up."
* [RS08] "Carbon monoxide (CO) may elevate the O2 saturation, SpO2, Pulse Oximeter level falsely." Historical 2001 station checklist, smoke procedure.

**To be set by clinical review or flight rules:**

* The oxygen level that starts oxygen treatment.

Sources for this flow: RS08, RS15, RS17, RS18, RS34, RS35, RS57, C08.

### 7. Fainting

`fainting` &middot; Body regions: head, heart

Fainting is a short loss of consciousness. Most people wake within 30 seconds. Standing up after time in weightlessness can cause it.

**When it opens.** Problem list: fainted or nearly fainted.

**Do this now** (fixed, no model):

1. Check breathing. If they do not wake within 1 minute or are not breathing normally, go to the Not responding flows.
2. If they breathe normally, lay them on their back and raise their legs where there is gravity. In weightlessness, positioning: to be set by clinical review.
3. When they wake, keep them lying or sitting. Do not let them stand up quickly.
4. Call the CMO if they fainted while exercising or while lying down, have chest pain or a pounding, fluttering or irregular heartbeat, were hurt, had jerking movements, or are not fully back to normal.

**Questions the assistant asks**, one at a time:

* What were you doing just before?
* Any chest pain, or a racing, pounding or irregular heartbeat before it?
* Did you hit your head?
* When did you last eat and drink?
* Is this soon after landing or a return to gravity?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Pulse from the cameras | Only if the person can already sit still at the camera station. Never move them there or delay a step for it. Movement gives No reading, which is kept as a failed reading. | camera station |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Lying, then sitting, only when the CMO asks. Follow the device instructions. Timing and the change that matters: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Blood sugar | Only if the onboard kit carries a glucose meter. Use it as its instructions say and enter the number in mg/dL or mmol/L. The level that needs action: threshold to be set by clinical review. | glucose meter (only if carried) |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Lie flat and raise the legs where there is gravity | any crew | none | RS19, RS20 |
| Check for causes, including blood pressure on standing after return to gravity, as in the onboard procedure | crew medical officer | BP cuff | C09 |
| Log for the ground: the cause, for their review. It leaves at the next contact; nothing waits for it. | ground | message link | RS19 |

**Log for the ground.** Log for the ground at once if they fainted during exercise or while lying down, did not wake within 1 minute, had chest pain or a pounding, fluttering or irregular heartbeat, a seizure, or an injury; otherwise as a routine report. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Ask the questions above one at a time.
* Request a pulse, oxygen and BP reading and say why.
* Draft the log for the ground for the person to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Say it was only a simple faint.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Lying down and raising the legs assumes gravity. Positioning in weightlessness: to be set by clinical review. |
| `transit` | As on the station. |
| `lunar_surface` | Partial gravity, and the first days after landing: standing up can cause fainting (C09). |
| `mars_surface` | As on the Moon, after a long time in weightlessness (C09). |
| `eva` | Fainting in a suit: follow the spacewalk emergency procedure. |

**Evidence:**

* [RS19] "Usually, the person who has fainted will wake up within 30 seconds."
* [RS19] "If they’re breathing normally, lay them on their back and raise their legs."
* [RS19] "cannot be woken up within 1 minute" From the list of reasons to call an ambulance after fainting.
* [RS19] "has chest pain or a pounding, fluttering or irregular heartbeat (heart palpitations)" Same list.
* [RS19] "fainted while exercising" Same list.
* [RS19] "fainted while lying down" Same list.
* [RS20] "If they stay unresponsive, open their airway, check their breathing, and prepare to treat someone who is unresponsive"

**To be set by clinical review or flight rules:**

* Positioning after a faint in weightlessness.
* Lying and sitting BP timing and the change that matters.

Sources for this flow: RS19, RS20, C07, C08, C09.

### 8. Seizure (fit)

`seizure` &middot; Body regions: head

A seizure is a burst of abnormal activity in the brain. The person may shake, stiffen or stop responding. Keep them safe and time it.

**When it opens.** Problem list: seizure, fit or jerking movements.

**Do this now** (fixed, no model):

1. Note the time it starts.
2. Move hard or sharp things away. In weightlessness, guide them away from hard structures.
3. Do not hold them down tightly or stop their movements. Do not put anything in their mouth.
4. Protect the head with soft padding.
5. When the jerking stops, open the airway and check breathing. If they breathe normally, recovery position where there is gravity.
6. If they are not breathing normally after it stops, start chest compressions.
7. Call the CMO at once if it lasts more than 5 minutes, another seizure follows, it is their first, they were hurt, or they stay unresponsive.
8. Give no food or drink until they are fully alert.

**Questions the assistant asks**, one at a time:

* To the helper: how long did it last?
* Has this happened before?
* Any blow to the head, fever, toxic air or alarm, or missed sleep?
* Do they take any medicine, or have diabetes?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Seizure length | Press start when it begins and stop when the jerking ends. | app timer |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Blood sugar | Only if the onboard kit carries a glucose meter. Use it as its instructions say and enter the number in mg/dL or mmol/L. The level that needs action: threshold to be set by clinical review. | glucose meter (only if carried) |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Protect from injury, time it, no tight restraint, nothing in the mouth | any crew | soft padding | RS21, RS22, RS08 |
| Open the airway and check breathing after the seizure; recovery position where there is gravity | any crew | none | RS21, RS06 |
| Medicine to stop a long seizure, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS24, RS08 |
| Log for the ground, for the specialists' review. Real time consultation joins only while the link is up. It leaves at the next contact; nothing waits for it. | ground | message link; voice and video while the link is up | RS24 |

**Log for the ground.** Log for the ground after every seizure, and at once if it lasts more than 5 minutes or another follows before they recover. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Run the seizure timer and alert the helper at 5 minutes.
* After it stops, ask the questions above one at a time.
* Draft the log for the ground for the helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Tell anyone to hold the person down or put something in their mouth.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Guide them away from hard structures; do not tightly restrain (RS08). |
| `transit` | As on the station. Longer delay: the CMO leads. |
| `lunar_surface` | Partial gravity: pad the head as on Earth. |
| `mars_surface` | As on the Moon. Long delay: the CMO leads. |
| `eva` | A seizure in a suit: follow the spacewalk emergency procedure. |

**Evidence:**

* [RS21] "Make a note of the time that the seizure started."
* [RS21] "Do not restrain the casualty or move them unless they are in immediate danger. Do not put anything in their mouth."
* [RS21] "Protect their head by placing soft padding underneath it, like a rolled-up towel or pillows."
* [RS21] "When any jerky movements have stopped, open their airway and check their breathing."
* [RS21] "the seizure continues for more than five minutes" From the list of reasons to call an ambulance.
* [RS21] "it is the casualty’s first seizure" Same list.
* [RS22] "Time the seizure to track if it lasts more than 5 minutes."
* [RS22] "Don't hold the person down or stop their movements."
* [RS22] "Don't offer water or food until they are fully alert."
* [RS08] "It is most important to prevent injury during the seizure by guiding the patient away from hard structures; do not tightly restrain. Ensure that the airway is open, but do not insert any object into the mouth." Historical 2001 station checklist.
* [RS24] "Status epilepticus (a seizure lasting longer than 5 minutes, or ≥2 discrete seizures between which there is incomplete recovery of consciousness)" NASA modeling form for seizures.
* [RS24] "However in the best case scenario consultation with mission control and ground specialists in real time would be necessary for optimal management."

**To be set by clinical review or flight rules:**

* Which medicine the onboard procedure uses for a long seizure.

Sources for this flow: RS06, RS08, RS21, RS22, RS24, C08.

### 9. Head injury

`head_injury` &middot; Body regions: head, neck

A blow to the head can hurt the brain even when the person seems fine at first. Watch them closely and do not leave them alone.

**When it opens.** Problem list: hit the head.

**Do this now** (fixed, no model):

1. If they do not respond, go to the Not responding flows. Assume a neck injury: keep the head and neck still and in line.
2. If they are awake, keep them still and hold something cold on the swelling.
3. Press gently on any bleeding wound with a clean dressing.
4. Check how alert they are: eyes open? Do they answer simple questions? Do they react only to pain? Not at all?
5. Stay with them. Do not leave them alone.
6. Call the CMO at once if they were knocked out, are confused or drowsy, are being sick, have a fit, have trouble seeing or hearing, clear fluid from the ears or nose, new numbness or weakness, or trouble walking, balancing or speaking.

**Questions the assistant asks**, one at a time:

* Did you black out?
* Do you remember what happened?
* Do you have a headache? Is it getting worse?
* Are you feeling sick or being sick?
* Any double or blurred vision?
* Any numbness or weakness anywhere?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| How alert they are | Alert, responds to voice, responds only to pain, or unresponsive. Record the answer and the time, and repeat it. | none |
| Pupils | Look at both pupils. Note if one is larger than the other. Take a photo for the CMO; it goes with the log for the ground. | camera station or a light, if listed |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Pulse from the cameras | Only if the person can already sit still at the camera station. Never move them there or delay a step for it. Movement gives No reading, which is kept as a failed reading. | camera station |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Keep head and neck still and in line if a neck injury is possible | any crew | none | RS26, RS06 |
| Cold pack on the swelling; gentle pressure on a bleeding wound | any crew | cold pack, dressings | RS26 |
| Watch them, recheck alertness, and stay with them | any crew | none | RS25, RS26 |
| Nerve and brain examination, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS28, RS08 |
| Log for the ground with the video of the first assessment, for their review of ongoing care. It leaves at the next contact; nothing waits for it. | ground | message link with video | RS28, RS29 |

**Log for the ground.** Log for the ground at once for any warning sign in the steps above; for a mild bump, with the next log. NASA's form expects ground video and audio even in the best case (RS28), so a crewmate repeats the checks above and the video goes with the log. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Remind the helper to recheck alertness (interval to be set by clinical review).
* Ask the questions above one at a time.
* Request the pupil photo and readings and say why.
* Draft the log for the ground for the helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Say a brain injury is ruled out. This kit has no scanner.
* Suggest leaving the person alone.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Ground video review is usually possible (RS28). |
| `transit` | Longer delay. The CMO leads; the video goes with the log at the next contact. |
| `lunar_surface` | The CMO leads; the video goes with the log at the next contact, and nothing waits for it. |
| `mars_surface` | Long delay: the CMO leads; send video early. |
| `eva` | A blow to the helmet: report it. After the spacewalk, check the person with this flow. |

**Evidence:**

* [RS25] "has been knocked out and has not woken up" From the list of reasons to call an ambulance.
* [RS25] "has clear fluid coming from their ears or nose" Same list.
* [RS25] "has new numbness or weakness in any part of their body" Same list.
* [RS25] "has problems with walking, balance, understanding, speaking or writing" Same list.
* [RS25] "make sure an adult stays with you or your child for at least the first 24 hours"
* [RS26] "Try not to move the casualty from the position you found them in as they may have a spinal injury"
* [RS26] "If they are responsive, sit the casualty down and hold something cold against the injury to help reduce the swelling"
* [RS27] "One pupil larger than the other or double vision" CDC danger signs after a head injury.
* [RS27] "A headache that gets worse and does not go away" Same list.
* [RS28] "Communications and Support Needs While a best-case scenario will require ground communication with video and audio" NASA modeling form for minor head injury.
* [RS29] "Communication with ground based support through video would contribute to the initial evaluation and ongoing management of these severe and complicated cases." NASA modeling form for severe head injury.

**To be set by clinical review or flight rules:**

* How often to recheck alertness, and for how long in flight.

Sources for this flow: RS06, RS08, RS25, RS26, RS27, RS28, RS29, C07, C08.

### 10. Eye injury or something in the eye

`eye_injury` &middot; Body regions: eyes

Something got into the eye or hit it. Dust from the Moon or Mars is sharp and can scratch the eye. Rinse it. Do not rub it.

**When it opens.** Problem list: eye injury, chemical or dust in the eye.

**Do this now** (fixed, no model):

1. Do not rub the eye.
2. If an object is stuck in the eye, do not remove it. Cover the eye with a rigid shield that rests on the bone around the eye, not on the eye.
3. Chemical, dust or grit in the eye: hold the eye open and rinse with lots of clean water at once, for at least 20 minutes.
4. In weightlessness, water and dust float. Use the onboard eye wash set. Until it is ready, rinse with a drink bag and soak up the water with towels.
5. Call the CMO.

**Questions the assistant asks**, one at a time:

* What got in, or what hit the eye?
* Was it a chemical, dust from outside, or a sharp object?
* Can you see as well as before with that eye?
* Pain, light hurting, or a gritty feeling?
* Contact lenses in?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Sight in each eye | Cover the other eye. Read the same text on the screen with each eye and record any difference. Test card and distance: to be set by clinical review. | app screen |
| Photo of the eye | A still, close photo in good light for the CMO, kept with the log for the ground. Do not touch the eye. | camera station |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Rinse with lots of clean water | any crew | eye wash set, drink bag, towels | RS30, RS08, RS46, RS34 |
| Rigid shield over an eye with something stuck in it, with no pressure on the eye | any crew | eye shield, if listed | RS32, RS30, RS31 |
| Remove a loose particle and check the eye surface for scratches, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS32, RS08 |
| Eye medicines, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS08 |
| Follow up check by a crewmate: look at the eye and ask about sight at the next check (interval to be set by clinical review), with a photo kept for the log | any crew | camera station | RS32, RS33 |
| Log for the ground with the photos, for a later diagnosis of eye damage. It leaves at the next contact; nothing waits for it. | ground | message link with photos | RS33 |

**Log for the ground.** Log for the ground at once for something stuck in the eye, a chemical in the eye, or any change in sight; otherwise when pain or grittiness does not settle after rinsing (how long to wait: to be set by clinical review). It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Run the 20 minute rinse timer.
* Ask the questions above one at a time.
* Request the eye photo and the sight check and say why.
* Draft the log for the ground for the person to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Tell anyone to remove an object stuck in the eye or to press on the eye.
* Say the eye is undamaged. This kit cannot examine the eye surface.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Floating particles reach the eyes more easily (RS45). Use the eye wash set; soak up floating water with towels (RS08). |
| `transit` | As on the station. |
| `lunar_surface` | Lunar dust is very abrasive and can injure the eye (RS34). Dust carried inside can float in a vehicle in weightlessness (RS35). |
| `mars_surface` | Martian dust is likely toxic (RS35). Rinse and report every exposure. |
| `eva` | Something in the eye inside a helmet cannot be treated until the person is back inside. Report it and follow the spacewalk procedure. |

**Evidence:**

* [RS31] "advise the casualty not to rub their eye"
* [RS31] "If there is a large object in their eye, do not try to remove it."
* [RS30] "do not try to remove any object that's pierced your eye"
* [RS30] "run lots of water over your eyeball for at least 20 minutes" From the NHS steps for washing the eye.
* [RS32] "it is essential to avoid any pressure whatsoever on the globe if this injury is even suspected" NASA modeling form for a foreign body in the eye. The globe is the eyeball.
* [RS32] "Place a rigid shield over the eye and tape it to the skin covering the rigid periorbital bone so that there is no pressure on the globe" Periorbital bone is the bony rim around the eye.
* [RS08] "Prior to SSE configuration, attempt eye irrigation with Drink Bag, Drink Straw." Historical 2001 station checklist. SSE is the eye wash equipment.
* [RS08] "Use Towels to absorb excess water." Same procedure.
* [RS33] "initial diagnosis and cleaning is uncomplicated and likely within the scope of care of onboard crew members. Later diagnosis of damage will require ground communications." NASA modeling form for a chemical burn of the eye.
* [RS34] "Being highly abrasive, lunar dusts can cause dermatitis and eye injury with skin and eye exposure." NASA modeling form for lunar dust exposure.
* [RS34] "Conservative treatment of the best case scenario will likely involve flushing affected mucous membranes with water"
* [RS35] "These features suggest that the Martian dust is likely toxic."
* [RS35] "the dust that is introduced into the return vehicle will “float,” thus increasing the opportunity for ocular and respiratory injury"
* [RS45] "Microgravity will cause the particles to float, increasing the likelihood that they will enter the eyes and respiratory systems of crewmembers and cause irritation."
* [RS46] "Eye: Irrigate immediately (solution/liquid)" NIOSH first aid for ammonia.

**To be set by clinical review or flight rules:**

* Sight check method and distance.
* How long to wait before an eye that still hurts after rinsing goes to the CMO.

Sources for this flow: RS08, RS30, RS31, RS32, RS33, RS34, RS35, RS45, RS46.

### 11. Burns

`burns` &middot; Body regions: none (not about one place)

Heat, electricity or a chemical has burned the skin. Cool it fast and keep the person warm.

**When it opens.** Problem list: burn or scald.

**Do this now** (fixed, no model):

1. Make it safe first. If there is a fire, follow the fire alarm response and put on masks.
2. Cool the burn with cool running water for 20 minutes. Without running water, use cool water or a wet towel.
3. In weightlessness, water floats. Use wet towels or dressings. Method: to be set by clinical review.
4. Take off clothing or jewellery near the burn, unless it is stuck to the skin.
5. Do not use ice, creams or gels. Do not burst blisters.
6. When cooled, lay cling film over it. Do not wrap it around.
7. Keep the person warm. Do not over cool them.
8. Chemical powder: brush it off, then rinse. Never try to neutralise a chemical.
9. Call the CMO.

**Questions the assistant asks**, one at a time:

* What caused it: flame, a hot surface, hot liquid, electricity or a chemical?
* Where is it, and how big?
* Is the face, mouth or throat burned, or is the voice hoarse?
* Was there smoke?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Body temperature, to avoid over cooling | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. Carbon monoxide can make this number look normal when it is not. | pulse oximeter |
| Photo of the burn | A still photo in good light for the CMO, kept with the log for the ground. | camera station |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Cool with water for 20 minutes | any crew | water, wet towels | RS36, RS38 |
| Remove clothing and jewellery near the burn unless stuck | any crew | none | RS36 |
| Cover with cling film or a clean dressing | any crew | cling film or burn dressing, if listed | RS36 |
| Brush off powder chemicals, then rinse | any crew | water | RS39 |
| Burn dressings, pain relief and fluids, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS40, RS08 |
| Log for the ground with the photos, for their advice on further treatment. It leaves at the next contact; nothing waits for it. | ground | message link with photos | RS40 |

**Log for the ground.** Log for the ground at once for a large or deep burn, a burn on the face, a burn from a chemical or electricity, or any smoke exposure; otherwise as a routine report. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Run the 20 minute cooling timer.
* Ask the questions above one at a time.
* Request the burn photo and say why.
* Draft the log for the ground for the person to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Suggest ice, creams, gels, or bursting blisters.
* Suggest neutralising a chemical.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Fire response comes first (RS41). Floating water: cool with wet towels. |
| `transit` | A single cabin vehicle has no clean module to retreat to after a fire (RS41). |
| `lunar_surface` | Partial gravity: water runs off more as on Earth. Cooling method: to be set by clinical review. |
| `mars_surface` | As on the Moon. Long delay: the CMO leads. |
| `eva` | A burn in a suit is a suit emergency. Follow the spacewalk emergency procedure. |

**Evidence:**

* [RS36] "Hold the burn or scald under cool running water for 20 minutes."
* [RS36] "If there's no running water available, you can use cool bottled water or a wet towel."
* [RS36] "When the burn or scald has cooled, lay cling film over it if you can."
* [RS36] "do not burst any blisters"
* [RS36] "has been caused by an acid or chemical, or by electricity" From the list of reasons to call an ambulance or go to hospital.
* [RS37] "Do not use ice, creams or gels."
* [RS38] "Continue cooling the area for at least 20 minutes or until the pain feels better."
* [RS38] "Do not over cool the casualty"
* [RS39] "If the chemical is in powder form, it can be brushed or patted off the skin."
* [RS39] "Never attempt to neutralise acid or alkali burns unless you are trained to do so."
* [RS40] "It is therefore desirable to have as much communication as possible during the duration of diagnosis and initial treatment." NASA modeling form for moderate to severe burns.

**To be set by clinical review or flight rules:**

* How to cool a burn with water in weightlessness.
* What size of burn needs the CMO at once.

Sources for this flow: RS08, RS36, RS37, RS38, RS39, RS40, RS41, C08.

### 12. Toxic air, chemical exposure or smoke

`toxic_exposure` &middot; Body regions: lungs, eyes

The air or a surface may hold something harmful: ammonia, fire smoke, carbon monoxide or a spilled chemical. Protect yourself first. Then help.

**When it opens.** First screen: smoke, fire, a sharp or chemical smell, or not sure the air is safe. Problem list: chemical or smoke exposure. A fire or toxic air alarm.

**Do this now** (fixed, no model):

1. Protect yourself first. Put on your mask before you help anyone.
2. Follow the vehicle's alarm response: warn, gather, then work the problem.
3. Station ammonia alarm: masks on, leave the affected segment, close the hatches. Treat any unexpected toxic air alarm as ammonia.
4. Move the person to clean air, with a mask on them too.
5. Chemical on the skin or in the eyes: rinse with lots of water at once.
6. If they are not breathing normally once you are both in clean air, start chest compressions.
7. Keep watching them. Signs can appear up to 2 hours later.

**Questions the assistant asks**, one at a time:

* What was it: smoke, ammonia, another chemical, or not known?
* How long were they in it? Was a mask on?
* Cough, chest tightness, or trouble breathing?
* Eyes burning or watering?
* Headache, dizziness, feeling sick, or confusion?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. Carbon monoxide can make this number look normal when it is not. | pulse oximeter |
| Breathing rate from the cameras | Only if the person is already still in front of the cameras. Never delay a step for it. If it fails, the helper counts breaths and enters the number. | camera station |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Cabin air readings | Read the habitat sensors for the substance, oxygen and carbon dioxide. The limits come from the vehicle's flight rules. | habitat sensors |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Masks on and move to clean air | any crew | portable breathing apparatus or masks | RS41, RS42, RS44 |
| Rinse skin and eyes with lots of water | any crew | water, eye wash set | RS46, RS42 |
| Breathing support and oxygen, as in the onboard procedure | crew medical officer | oxygen supply, if listed | RS42, RS46 |
| Medicines, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS08 |
| Log for the ground, for their advice on treatment and how long to watch. It leaves at the next contact; nothing waits for it. | ground | message link | RS43 |

**Log for the ground.** Log for the ground once the crew and the person are safe in clean air, and at once if anyone has trouble breathing, chest pain, confusion or burning eyes. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Show the vehicle's protective steps first, from the reviewed procedure.
* Run a 2 hour watch timer after exposure.
* Ask the questions above one at a time.
* Draft the log for the ground for the helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Tell anyone to remove a mask or go back into unsafe air.
* Say the air is safe. Only the vehicle's sensors and flight rules decide that.
* Treat a normal oxygen reading as proof of no harm after smoke.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Segments with hatches and a return vehicle; the memorized ammonia response applies (RS41). |
| `transit` | A single cabin vehicle has no clean module to retreat to (RS41). Masks and suits are the protection. |
| `lunar_surface` | Habitat layout and safe haven: to be set by flight rules. Dust can also irritate the airways (RS34). |
| `mars_surface` | As on the Moon. Martian dust is likely toxic (RS35). |
| `eva` | Crew outside: when to come back in is set by flight rules. |

**Evidence:**

* [RS41] "emergencies fall into four categories: Rapid Depressurization, Fire, Toxic Spill, and Medical emergency." NASA paper on station emergency response.
* [RS41] "the crew’s procedures are broken down into three fundamental steps: Warn, Gather, Work."
* [RS41] "therefore the crew lead the response, and must not be dependent upon any action or assistance from Mission Control."
* [RS41] "The PBAs, therefore, remain the mask of choice for initial response." PBA means portable breathing apparatus.
* [RS41] "The ISS crew has the following response memorized: don protective masks, evacuate from the USOS to the Russian Segment or USCV, and close the hatches to isolate the USOS." USOS is the US segment of the station; USCV is the US crew vehicle.
* [RS41] "crews are trained to assume that any unexpected Toxic Atmosphere alarm" The sentence continues: the alarm is treated as ammonia.
* [RS44] "The crew immediately began putting on oxygen masks." NASA modeling form for smoke inhalation, describing a past event.
* [RS42] "Exposure to a massive concentration of ammonia gas may be fatal within minutes." NASA modeling form for ammonia exposure.
* [RS42] "On-site management consists of personal protection, rapid evacuation, respiratory and fluid support, as well as decontamination."
* [RS42] "extracting the crewmember or patient to a safe environment and initiating cardiopulmonary resuscitation and/or ACLS" ACLS is advanced life support.
* [RS08] "Remove individual from further exposure." Historical 2001 station checklist.
* [RS08] "Signs of toxicity may be delayed for up to 2 hours following exposure event." Same checklist.
* [RS08] "Carbon monoxide (CO) may elevate the O2 saturation, SpO2, Pulse Oximeter level falsely." Same checklist.
* [RS46] "Eye: Irrigate immediately (solution/liquid)" NIOSH first aid for ammonia.
* [RS46] "Breathing: Respiratory support" Same.
* [RS47] "Get away from the area where the ammonia was released and breathe fresh air."
* [RS48] "The most common symptoms of CO poisoning are headache, dizziness, weakness, upset stomach, vomiting, chest pain, and confusion."
* [RS43] "In the best-case scenario, diagnosis and course of treatment is uncomplicated and likely within the scope of care of onboard crew members." NASA modeling form for toxic combustion products.

**To be set by clinical review or flight rules:**

* Which mask or breathing set each vehicle and habitat carries (kit inventory and flight rules).
* Safe haven for lunar and Martian habitats.

Sources for this flow: RS08, RS34, RS35, RS41, RS42, RS43, RS44, RS46, RS47, RS48, C08.

### 13. Cabin pressure loss

`pressure_loss` &middot; Body regions: head, lungs, ear

Cabin pressure is dropping. Less pressure means less oxygen for the body. The signs of low oxygen are easy to miss in yourself.

**When it opens.** First screen: pressure alarm, hissing air or ears popping. Problem list: symptoms after a pressure loss.

**Do this now** (fixed, no model):

1. Follow the vehicle's depressurization response first: warn, gather, then work.
2. On a station: gather in the safe haven, with a clear path to the return vehicle.
3. On a single cabin vehicle: put on your suit at once.
4. Watch yourself and others for low oxygen signs: fast breathing, headache, lightheadedness, dizziness, tingling, sweating, clumsiness, poor judgement, tunnel vision, an odd good mood.
5. Anyone with these signs gets oxygen by mask or suit, as the vehicle procedure says.
6. If someone stops responding and is not breathing normally, start chest compressions once you both have air.
7. Afterwards, report any joint pain, tingling, itchy or blotchy skin: it may be DCS. Open the DCS flow.

**Questions the assistant asks**, one at a time:

* Once safe: headache, dizziness, tingling or blurred vision?
* Joint pain, or itchy or blotchy skin?
* Ear or sinus pain?
* Chest pain or trouble breathing?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Breathing rate from the cameras | Only if the person is already still in front of the cameras. Never delay a step for it. If it fails, the helper counts breaths and enters the number. | camera station |
| Cabin pressure and oxygen | Read the habitat sensors. Limits and reserve time come from the vehicle's flight rules. | habitat sensors |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Vehicle depressurization response: warn, gather, safe haven or suits | any crew | suits, masks, pressure gauge | RS41 |
| Oxygen by mask or suit for low oxygen signs, as the vehicle procedure says | any crew | oxygen masks or suits | RS41, RS50 |
| Treatment of decompression sickness, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS49, RS51 |
| Log for the ground once the crew is safe, for their follow up. It leaves at the next contact; nothing waits for it. | ground | message link | RS41 |

**Log for the ground.** The alarm already warns the ground while the link is up. Log symptoms for the ground once the crew is safe. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Show the vehicle's own depressurization steps from the reviewed procedure.
* Once the crew is safe, ask the questions above one at a time.
* Draft the report for the helper to preview.

**The assistant must never:**

* Ask questions while the crew is working the emergency.
* Say anyone is fine because they feel fine. Low oxygen dulls judgement.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Gather, work out the reserve time, and retreat to the safe haven before it runs out (RS41). |
| `transit` | Single cabin: suits on at once (RS41). |
| `lunar_surface` | Habitat procedure: to be set by flight rules. |
| `mars_surface` | Habitat procedure: to be set by flight rules. |
| `eva` | A suit pressure problem is a suit emergency. Follow the spacewalk emergency procedure. |

**Evidence:**

* [RS41] "the crew’s procedures are broken down into three fundamental steps: Warn, Gather, Work."
* [RS41] "procedures and training additionally account for the need to monitor and react to hypoxic symptomology" Hypoxic means low in oxygen.
* [RS41] "If at any point the TRes reaches ten minute or less, the crew stops performing their pinpoint and isolating procedures" TRes is the reserve time before pressure falls below a safe level.
* [RS41] "In the event of a rapid depress on Orion, the crew immediately begins to don their spacesuits."
* [RS41] "No longer can crew evacuate to a clean module, there is none." About single cabin vehicles.
* [RS50] "The order of symptoms varies among individuals: increased breathing rate, headache, lightheadedness, dizziness, tingling or warm sensations, sweating, poor coordination, impaired judgment, tunnel vision, and euphoria." FAA pilot brochure on hypoxia.
* [RS50] "hypoxia doesn’t give you much warning"

**To be set by clinical review or flight rules:**

* Depressurization response for lunar and Martian habitats.
* The oxygen level that starts oxygen treatment.

Sources for this flow: RS41, RS49, RS50, RS51, C08.

### 14. Decompression sickness after a spacewalk

`dcs_after_eva` &middot; Body regions: shoulder, knee, legs, head, lungs

When pressure drops, gas dissolved in the body can form bubbles. This is decompression sickness (DCS, also called the bends). It can follow a spacewalk or a pressure loss.

**When it opens.** Problem list: symptoms during or after a spacewalk. Also offered after a pressure loss.

**Do this now** (fixed, no model):

1. During a spacewalk, report any symptom at once, even a mild one.
2. Joint or muscle pain, tingling or numbness, itchy or blotchy skin, weakness, confusion, vision change, breathing trouble or chest pain during or after a spacewalk: treat it as DCS until the CMO says otherwise.
3. Signs in the brain, nerves, lungs or heart (serious DCS) end the spacewalk at once.
4. Bring the person back to full cabin pressure and give oxygen by mask, as the procedure says.
5. Call the CMO.

**Questions the assistant asks**, one at a time:

* When did the spacewalk end? Was the full prebreathe done?
* When did the symptom start, and where is it?
* Is it getting worse?
* Any tingling, weakness, confusion, trouble breathing or chest pain?
* Any marbled or blotchy skin?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Breathing rate from the cameras | Only if the person is already still in front of the cameras. Never delay a step for it. If it fails, the helper counts breaths and enters the number. | camera station |
| Photo of any blotchy skin | A still photo in good light for the CMO, kept with the log for the ground. | camera station |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Report symptoms at once; end the spacewalk for serious signs | any crew | none | RS49 |
| Return to full pressure and oxygen by mask, as in the onboard procedure | crew medical officer | oxygen mask, if listed | RS49, RS08 |
| Suit pressure treatment, similar in principle to diving treatment tables, as in the onboard procedure | crew medical officer | suit and oxygen, as in the onboard procedure | RS51 |
| Log for the ground. Real time audio and video advice joins only while the link is up. It leaves at the next contact; nothing waits for it. | ground | message link; voice and video while the link is up | RS52 |

**Log for the ground.** Log for the ground at once for any symptom during or after a spacewalk. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Ask the questions above one at a time.
* Show the time since the spacewalk ended.
* Request readings and the skin photo and say why.
* Draft the log for the ground for the person to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Say a symptom is too mild to report.
* Say DCS is ruled out. No onboard test confirms or excludes it.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Return to cabin pressure and oxygen are the main treatment; there is no hyperbaric chamber in flight (RS51). |
| `transit` | Applies after any pressure loss. |
| `lunar_surface` | Frequent surface walks. Suit and habitat pressures, and the prebreathe, are set by flight rules. |
| `mars_surface` | As on the Moon. Long delay: the CMO leads. |
| `eva` | Report at once. Serious signs end the spacewalk (RS49). |

**Evidence:**

* [RS49] "required to immediately report the onset of any DCS symptoms" NASA evidence report on DCS and hypoxia.
* [RS49] "there may be a bias to not report mild discomfort"
* [RS49] "Type I DCS symptoms are described as “pain only”, localized in muscle(s) or joint(s)."
* [RS49] "Type II symptoms are systemic, involving the central nervous system, cardiopulmonary system" Cardiopulmonary means heart and lungs.
* [RS49] "Type II symptoms require immediate termination of an EVA and jeopardize both mission success and crew health." EVA means spacewalk.
* [RS49] "Ground level O2 is an effective treatment for hypobaric DCS in most cases"
* [RS49] "In some cases, O2 can be supplied by mask to help relieve any symptoms or to aid in differential diagnosis."
* [RS08] "Primary treatment principles consist of repressurization and 100 % 02 over time." Historical 2001 station checklist; spelling as in the source.
* [RS51] "DCS is typically diagnosed based on symptom presentation because the presence of VGE or AGE does not necessarily indicate DCS." NASA technical brief on DCS. VGE and AGE are gas bubbles in the blood.
* [RS51] "While a hyperbaric chamber is not available during spaceflight, crewmembers exhibiting DCS symptoms may undergo a treatment similar in principle to the Navy table treatments."
* [RS51] "The majority of cases present themselves between 60-240 minute and inflammatory responses can last greater than 24 hours."
* [RS52] "In the best and worst-case scenario, real-time audio and video would be helpful" NASA modeling form for spacewalk DCS.

**To be set by clinical review or flight rules:**

* How long after a spacewalk the app keeps DCS questions active.

Sources for this flow: RS08, RS49, RS51, RS52, C08.

### 15. Solar particle event: take shelter

`solar_particle_event` &middot; Body regions: none (not about one place)

The Sun has thrown out a burst of fast particles (a solar particle event). Shelter behind as much mass as you can.

**When it opens.** First screen: radiation alarm or solar storm warning. A warning from the ground or the onboard radiation monitors.

**Do this now** (fixed, no model):

1. When the radiation alarm sounds, the onboard radiation monitors warn, or the ground tells you, go to the shelter area now.
2. Build the shelter: move stowage bags, equipment, food and water to put mass between you and the outside.
3. On the Moon, use the shelter covered with lunar soil if the base has one.
4. Keep your personal radiation tracker on.
5. How long to stay: set by flight rules and the onboard radiation monitors, with the ground's advice while the link is up.

**Questions the assistant asks**, one at a time:

* After the event: any nausea, vomiting, headache or tiredness?
* When did it start?
* Any skin redness?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Radiation dose rate | Read the habitat radiation sensors and your personal tracker. The levels that call for shelter are set by flight rules. | habitat radiation sensors, personal dosimeter |
| Time of first vomiting | If anyone vomits, record the time. The CMO and the ground use it. | app clock |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Shelter behind stowage and equipment mass | any crew | stowage bags, supplies | RS53, RS54 |
| Treat nausea, vomiting and diarrhoea, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS55 |
| Log for the ground with the dose readings, for their dose assessment and advice. It leaves at the next contact; nothing waits for it. | ground | message link | RS55, S08 |

**Log for the ground.** The ground usually sends the warning while the link is up; the onboard radiation monitors do when it is down. Log anyone with nausea, vomiting or skin redness after the event. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Show the shelter steps from the reviewed procedure.
* After the event, ask the questions above one at a time.
* Draft the report for the person to preview.

**The assistant must never:**

* Say when it is safe to leave the shelter.
* Estimate a radiation dose.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The station orbits inside Earth's magnetic protection, and the hull helps (RS53). Activities outside may be postponed (RS53). |
| `transit` | Beyond Earth's protection. On Orion, stowage bags make a dense shelter (RS53). |
| `lunar_surface` | Lunar soil piled over the shelter adds protection (RS53). |
| `mars_surface` | Shelter design: to be set by flight rules. |
| `eva` | Return inside as the flight rules say. |

**Evidence:**

* [RS53] "During an SEP event, the crew will use stowage bags on board Orion to create a dense shelter from radiation." SEP means solar energetic particles.
* [RS53] "“Our strategy in space is to make use of whatever mass is available,”"
* [RS53] "On the Moon, astronauts could pile lunar soil, or regolith, over their shelters"
* [RS53] "The International Space Station cruises through low-Earth orbit, within Earth’s protection, and the station’s hull helps shield crew members from radiation too."
* [RS53] "they might recommend postponing activities that require leaving the safety of the station"
* [RS53] "including storage units already on board or food and water supplies"
* [RS54] "If radiation levels increase, Orion’s onboard systems display warnings accompanied by an audible alarm."
* [RS54] "A higher threshold triggers a recommendation for the crew to take shelter."
* [RS54] "Artemis II astronauts also wear personal radiation trackers called crew active dosimeters."
* [RS55] "typically the prodromal phase involves headache, nausea, and fatigue" NASA modeling form for acute radiation syndrome. Prodromal means early.
* [RS55] "communication is very likely to be a high priority"

**To be set by clinical review or flight rules:**

* Dose thresholds for shelter and for leaving it (flight rules).
* Shelter design for a Martian base.

Sources for this flow: RS53, RS54, RS55, C08, S08.

### 16. Severe allergic reaction

`severe_allergic_reaction` &middot; Body regions: neck, lungs

A severe allergic reaction (anaphylaxis) can swell the throat and drop blood pressure within minutes. Adrenaline is the first treatment.

**When it opens.** Problem list: allergic reaction with swelling, breathing trouble or faintness.

**Do this now** (fixed, no model):

1. Get the adrenaline auto-injector from the kit. It is given as in the onboard procedure.
2. Call the CMO and every crewmate.
3. Struggling to breathe: sit them up. Faint or dizzy: lay them down with legs raised where there is gravity. Do not let them stand or walk.
4. If they are no better after 5 minutes, the onboard procedure covers a second auto-injector.
5. If they stop responding and are not breathing normally, start chest compressions.

**Questions the assistant asks**, one at a time:

* Swollen lips, mouth, tongue or throat?
* Tight throat or trouble swallowing?
* Breathing fast or wheezing?
* A rash?
* What did you eat, touch or take? Any known allergy?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Breathing rate from the cameras | Only if the person is already still in front of the cameras. Never delay a step for it. If it fails, the helper counts breaths and enters the number. | camera station |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Adrenaline auto-injector, as in the onboard procedure. Who may give it when the CMO cannot: to be set by clinical review | crew medical officer | adrenaline auto-injector, if listed | RS56, RS57, RS08 |
| Position: sit up for breathing trouble, lie down with legs raised if faint | any crew | none | RS56, RS57 |
| Further medicines, oxygen and fluids, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS58, RS08 |
| Log for the ground. Audio, video and telemetry support joins as close to real time as the link allows. It leaves at the next contact; nothing waits for it. | ground | message link; voice, video and telemetry while the link is up | RS58 |

**Log for the ground.** Log for the ground at once. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Start a 5 minute timer after the first auto-injector.
* Ask the questions above one at a time.
* Show the person's recorded allergies (no dose advice).
* Draft the log for the ground for the helper to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Tell the person to stand up or walk.
* Say the reaction is over because they feel better.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The ground link is usually up: a free crewmate opens it at once. Nothing waits for it. |
| `transit` | Longer delay. The CMO leads; later treatment depends on what the kit carries. |
| `lunar_surface` | No hospital nearby: later treatment depends on what the kit carries. |
| `mars_surface` | As on the Moon. Long delay: the CMO leads. |
| `eva` | A reaction in a suit: follow the spacewalk emergency procedure. |

**Evidence:**

* [RS56] "Use an adrenaline auto-injector (such as an EpiPen) if you have one" NHS steps for a person who carries their own auto-injector.
* [RS56] "If your symptoms have not improved after 5 minutes, use a 2nd adrenaline auto-injector." Same context. Onboard, the second injector follows the onboard procedure.
* [RS56] "Do not stand or walk at any time, even if you feel better."
* [RS56] "your lips, mouth, throat or tongue suddenly become swollen" From the list of reasons to call an ambulance.
* [RS56] "your throat feels tight or you're struggling to swallow" Same list.
* [RS57] "Monitor their breathing and level of response by lying the casualty down with their legs raised. If they are having breathing difficulties, then sit them up."
* [RS58] "Given the emergent nature of this condition communications with audio, video, and remote telemetry will be needed in as close to real time as possible" NASA modeling form for anaphylaxis.
* [RS08] "Contact Surgeon before giving any medication marked with an asterisk. In an emergency or during Loss of Signal, begin appropriate treatment; then call Surgeon as soon as possible." Historical 2001 station checklist.

**To be set by clinical review or flight rules:**

* Whether any crewmate may give adrenaline when the CMO is the patient or unavailable.

Sources for this flow: RS08, RS56, RS57, RS58, C08.

### 17. Low blood sugar

`low_blood_sugar` &middot; Body regions: none (not about one place)

Blood sugar (glucose) can drop too low. Signs come on fast: shaking, sweating, hunger, confusion, odd behaviour. It is rare in people without diabetes.

**When it opens.** Problem list: shaky, sweaty, confused or acting oddly, or a low reading on a glucose meter.

**Do this now** (fixed, no model):

1. If they do not respond, or cannot swallow safely, give nothing by mouth. Go to the Not responding flows.
2. If they are awake and can swallow, help them sit down and give something sugary from the food stores, as the onboard procedure says.
3. Check blood sugar with a meter if the kit carries one. Enter the number.
4. Stay with them. Call the CMO.

**Questions the assistant asks**, one at a time:

* When did you last eat?
* Hard exercise or a long task without food?
* Any medicine taken?
* Shaky, sweaty, hungry or confused?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Blood sugar | Only if the onboard kit carries a glucose meter. Use it as its instructions say and enter the number in mg/dL or mmol/L. The level that needs action: threshold to be set by clinical review. | glucose meter (only if carried) |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Something sugary by mouth if awake and able to swallow; amount as in the onboard procedure | any crew | food stores | RS60, RS59 |
| Glucose or glucagon by injection when they cannot swallow, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS59 |
| Log for the ground, for finding the cause. It leaves at the next contact; nothing waits for it. | ground | message link | RS59 |

**Log for the ground.** Log for the ground after any episode: it is rare without diabetes, so the cause needs finding. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Ask the questions above one at a time.
* Request a glucose reading if the kit has a meter, and say why.
* Draft the log for the ground for the person to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Suggest food or drink for someone who is not fully alert.
* Name an amount of sugar or glucose.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | Sugary items in the food stores: as the onboard procedure lists. |
| `transit` | As on the station. |
| `lunar_surface` | As on the station. |
| `mars_surface` | As on the station. Long delay: the CMO leads. |
| `eva` | Low blood sugar during a spacewalk: report it and follow the spacewalk procedure. |

**Evidence:**

* [RS59] "It's rare in people without diabetes."
* [RS59] "It's rare to get low blood sugar if you do not have diabetes. It can sometimes be caused by conditions including:"
* [RS59] "Do not give them any food or drink as they will not be able to swallow safely." For a person who is unconscious or very drowsy.
* [RS59] "Give them a glucagon injection straight away, if one is available and you know how to use it." NHS steps for someone with diabetes who is unconscious. Onboard, any injection follows the onboard procedure.
* [RS60] "If you suspect hypoglycaemia (low blood sugar), help the person to sit down."
* [RS60] "If they are not fully alert, don’t try to give them something to eat or drink as they may choke."
* [RS61] "Symptoms of low blood glucose tend to come on quickly and can vary from person to person."

**To be set by clinical review or flight rules:**

* The glucose level that counts as low for this crew.
* Whether the kit carries a glucose meter, glucose gel or glucagon.
* How much sugar to give and when to recheck.

Sources for this flow: RS59, RS60, RS61, C08.

### 18. Severe belly or side pain

`severe_abdominal_pain` &middot; Body regions: abdomen, flank, back

Severe pain in the belly or the side can have many causes, such as appendicitis or a kidney stone. This kit cannot find the cause. Keep them comfortable and get the CMO.

**When it opens.** Problem list: severe pain in the belly, side or lower back.

**Do this now** (fixed, no model):

1. Help them rest in the position that hurts least.
2. Call the CMO.
3. Call everyone and the CMO at once if the pain started suddenly and is severe, they vomit blood, their poo is black and sticky, they cannot pee or pass wind at all, or they struggle to breathe.
4. Give no pain medicine unless the CMO gives it, as in the onboard procedure.

**Questions the assistant asks**, one at a time:

* Point to where it hurts on the body map. Has it moved?
* When did it start? Suddenly?
* Does it come in waves? Does it spread to the groin?
* Fever, or feeling hot, cold or shivery?
* Being sick? Any blood?
* Are pee and poo normal?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Pulse from the cameras | Only if the person can already sit still at the camera station. Never move them there or delay a step for it. Movement gives No reading, which is kept as a failed reading. | camera station |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Urine | Record when they last passed urine and whether they can pass it now. | none |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Record the symptoms and when they started; watch for the warning signs | any crew | none | RS62, RS63 |
| Examination, pain and sickness medicines, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS08 |
| Diagnosis needs tests this kit lacks. Check it another way: temperature, pulse and blood pressure on the onboard plan's cadence; the CMO decides on further care, as in the onboard procedure | crew medical officer | thermometer, BP cuff, camera station | S05, S09 |
| Log for the ground: the symptoms, the readings and the missing tests. It leaves at the next contact; nothing waits for it. | ground | message link | RS08 |

**Log for the ground.** Log for the ground at once for any warning sign in the steps above, or flank pain with a fever; otherwise with the next log. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Ask the questions above one at a time.
* Request a temperature and BP reading and say why.
* Say what this kit cannot test: imaging and blood or urine tests.
* Draft the log for the ground for the person to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Name a diagnosis as certain.
* Say appendicitis or a kidney stone is ruled out.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The ground link is usually available. |
| `transit` | Longer delay: the CMO leads; the log leaves at the next contact, and nothing waits for it. |
| `lunar_surface` | The link may be down for a day: the crew leads, and the log leaves at the next contact. |
| `mars_surface` | Long delay: the CMO leads; the log leaves at the next contact, and nothing waits for it. |
| `eva` | Severe pain during a spacewalk: report it and follow the flight rules for ending the spacewalk. |

**Evidence:**

* [RS62] "it started suddenly and is severe" From the NHS list of reasons to call an ambulance for stomach ache.
* [RS62] "you're vomiting blood or your vomit looks green or like ground coffee" Same list.
* [RS62] "there is a lot of blood coming from your bottom or your poo is black and sticky" Same list.
* [RS62] "you cannot pee at all" Same list.
* [RS62] "you cannot poo or fart at all" Same list.
* [RS62] "Do not self-diagnose."
* [RS63] "usually starts in the side of your tummy (abdomen) or lower back" Kidney stone pain.
* [RS63] "you have a high temperature, or you feel hot, cold or shivery" From the kidney stones list of reasons to get urgent help.

**To be set by clinical review or flight rules:**

* Which pain medicines the onboard procedure allows.

Sources for this flow: RS08, RS62, RS63, C07, C08, S05, S09.

### 19. Mental health crisis

`mental_health_crisis` &middot; Body regions: head

A crewmate is in severe distress: talking about suicide, very agitated, or out of touch with reality. Stay with them, keep them safe, and get help.

**When it opens.** Problem list: mental health crisis, or a crewmate talks about suicide or harming someone.

**Do this now** (fixed, no model):

1. Stay with them. Do not leave them alone.
2. Ask directly: are you thinking about suicide? Asking does not put the idea in their head.
3. Make the area safe: move away anything that could be used to hurt themselves or others.
4. Call the CMO and a second crewmate. Do not deal with a volatile person alone.
5. Speak calmly. Listen.
6. Restraint only as a last resort, as in the onboard procedure.

**Questions the assistant asks**, one at a time:

* To the person: are you thinking about suicide?
* Do you have a plan?
* Have you taken anything?
* What would help right now?
* To the helper: has anyone been hurt?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Pulse and blood pressure, only if the person agrees | Do not insist. Use the oximeter and cuff as their instructions say. | pulse oximeter, BP cuff |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Stay with them, ask directly, and listen | any crew | none | RS65 |
| Remove the means of harm | any crew | none | RS65 |
| Calming, medicines, and restraint as a last resort, as in the onboard procedure | crew medical officer | as in the onboard procedure | RS67, RS08 |
| Log for the ground. Behavioral health consultants and a private conference, arranged by the flight surgeon, join while the link is up and are never a precondition. It leaves at the next contact; nothing waits for it. | ground | message link; private voice or video while the link is up | RS68, C14 |

**Log for the ground.** Log for the ground at once if they talk about suicide or a plan, or if anyone cannot keep themselves or others safe. It leaves at the next contact; the crew stays with them, and nothing here waits for it.

**The assistant may:**

* Suggest calm, plain words the helper can use.
* Ask the helper one question at a time.
* Draft the log for the ground for the helper to preview; the person sees it if they are able. Tell the ground queues it for the next contact.

**The assistant must never:**

* Argue with, judge or diagnose the person.
* Promise to keep anything secret.
* Suggest leaving the person alone.
* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The ground link is usually available. Behavioral health support is arranged through the flight surgeon (RS68). |
| `transit` | Longer delay: the crew manages alone for longer; the CMO leads. |
| `lunar_surface` | The link may be down for a day: the crew leads, and the log leaves at the next contact. |
| `mars_surface` | Long delay: the crew manages alone for longer; the CMO leads. |
| `eva` | Whether a crewmate in crisis takes part in a spacewalk: set by flight rules. |

**Evidence:**

* [RS65] "ASK: “Are you thinking about suicide?”" NIMH, five action steps.
* [RS65] "Studies show that asking people if they are suicidal does not increase suicidal behavior or thoughts."
* [RS65] "Reducing access to highly lethal items or places can help prevent suicide."
* [RS64] "Making a plan or researching ways to die" NIMH warning signs.
* [RS66] "you do not feel you can keep yourself or someone else safe" NHS: when to get urgent help.
* [RS66] "A mental health emergency should be taken as seriously as a physical one."
* [RS67] "Clinical Guidelines recommend calming the patient, not to interview a volatile patient alone, and tranquilizers. Restraints are used as a last resort." NASA modeling form for behavioral emergencies.
* [RS67] "The ISS medical checklist recommends the same treatment, with the major difference being the early use of restrains. All crew members and spacecraft safety come first." Spelling as in the source.
* [RS08] "Backup CMO should remain with restrained crewmember at all times." Historical 2001 station checklist.
* [RS68] "Ensure access to specialized medical, dental, and behavioral health consultants" NASA technical brief on medical operations, flight surgeon duties.

**To be set by clinical review or flight rules:**

* Words the app suggests to the helper (to be written with ground behavioral health).

Sources for this flow: RS08, RS64, RS65, RS66, RS67, RS68, C08, C14.

### 20. Something else, or not sure

`something_else` &middot; Body regions: none (not about one place)

Not sure what is wrong? Stay with them, take the basic readings, and let the assistant help you work it out.

**When it opens.** Problem list: something else, or I don't know.

**Do this now** (fixed, no model):

1. Stay with them. Call the CMO.
2. If at any time they stop responding or breathing normally, go back to the first screen.
3. Ask what is wrong and where. Tap the place on the body map.
4. Take the basic readings below while you wait.

**Questions the assistant asks**, one at a time:

* What is wrong, in your own words?
* When did it start?
* Where is it?
* Is it getting better or worse?
* Has this happened before?

**Measurements:**

| What | How | Instrument |
|---|---|---|
| Pulse from the cameras | Only if the person can already sit still at the camera station. Never move them there or delay a step for it. Movement gives No reading, which is kept as a failed reading. | camera station |
| Oxygen level (SpO2) and pulse | Clip it on a finger. Keep the hand warm, relaxed and still. Wait until it shows one steady number. Read it together with the symptoms: a normal number does not cancel serious symptoms. The level that needs action: threshold to be set by clinical review. | pulse oximeter |
| Blood pressure | Put the cuff on the bare upper arm and follow the device instructions. Keep the arm supported and still. The level that needs action: threshold to be set by clinical review. | BP cuff |
| Temperature | Use the kit thermometer as its instructions say. Record the number and the time. | thermometer |

**Treatment steps:**

| Step | Who | Kit | Sources |
|---|---|---|---|
| Assessment and next steps | crew medical officer | none | RS68 |
| Log for the ground, for their review. It leaves at the next contact; nothing waits for it. | ground | message link | RS68 |

**Log for the ground.** Log for the ground whenever the CMO or the helper is unsure, or the assistant routes to a flow that calls for it. It leaves at the next contact; nothing here waits for it.

**The assistant may:**

* Ask one question at a time and route to the matching flow.
* Request readings and say why.
* Say plainly what this kit cannot test.
* Draft the log for the ground for the person to preview; Tell the ground queues it for the next contact.

**The assistant must never:**

* Delay, reorder or hide the fixed steps.
* Name a medicine dose. Medicines are given as in the onboard procedure.
* Invent a threshold. Where none is reviewed, say: threshold to be set by clinical review.
* Say a condition is ruled out without the test that rules it out.
* Lower or close the urgent state.

**Environments:**

| Environment | Note |
|---|---|
| `station_leo` | The ground link is usually available. |
| `transit` | Longer delay: the CMO leads. |
| `lunar_surface` | The link may be down for a day: the crew leads, and the log leaves at the next contact. |
| `mars_surface` | Long delay: the CMO leads. |
| `eva` | Report any problem during a spacewalk at once and follow the spacewalk procedure. |

**Evidence:**

* [RS68] "Program designates/trains two CMOs for each increment." NASA technical brief on medical operations.
* [RS68] "Receives additional medical training to manage a variety of possible in mission health scenarios." About the CMO.

Sources for this flow: RS68, C07, C08.

## The assistant's rules

These rules hold in every flow. They follow PRD section 9, the action contract in CONTRACTS section 6, and requirements R15 and R28.

**What it does**

1. **Joins after the fixed steps.** The urgent module shows the first screen and the flow's *Do this now* list from `responder-flows.json` before any model call. The model is not on that path. If the model is slow, wrong or off, the fixed steps do not change.
2. **One action per turn.** Each turn is one of `ask`, `request_measurement`, `retrieve_evidence`, `assessment`, `request_review` or `stop_incomplete`. It asks one question at a time, to a helper whose hands are free, never to someone doing compressions, blows or thrusts.
3. **Asks for measurements and says why.** It uses `propose_measurement` with the reason in plain words. The camera station is used only if the person is already still in front of it. A failed reading stays *No reading* with its reason and is never guessed.
4. **Walks the helper through reviewed steps.** It shows treatment steps returned by `get_pathway`, each with its source IDs, and checks each kit item with `get_capabilities`. If an item is missing it says so plainly (*this kit has none*) and proposes the substitute step the onboard plan already names, or says there is none; the gap goes in the log for the ground. It never improvises a substitute.
5. **Says what the kit cannot test.** When a test would decide the question (an ECG, a scan, blood tests) and the kit lacks it, it says so and records the gap with `record_unresolved_gap`.
6. **Keeps the ground informed, never waiting.** It drafts the log with `draft_handoff`. The helper previews it on the For the ground card (and the person, when able); pressing Tell the ground queues it, and it leaves at the next contact. Nothing is ever sent silently, and no step waits for it.
7. **Reads allergies, never doses.** `get_medication_allergy_context` lets it show recorded allergies and what the onboard procedure names. It gives no dose advice.

**What it must never do**

1. Delay, reorder, shorten or hide a fixed step, or put a question in front of one.
2. Invent a dose or a threshold. Medicines are always *as in the onboard procedure*; numbers without a reviewed source are *to be set by clinical review*.
3. Say a condition is excluded without the test that excludes it. Unknown is not negative, and a normal number never cancels serious symptoms. A normal oxygen reading does not end a breathing emergency, and carbon monoxide can make it look normal (RS08).
4. Lower, close or resolve the urgent state. Only a person can resolve it; acknowledging is not resolving.
5. Prescribe, start an invasive procedure, clear anyone for duty, or decide to stop CPR. Stopping CPR is for the most medically qualified crewmember, with the ground when the link allows (RS04).
6. Contact anyone without the helper's preview.
7. Give a treatment step that is not in a reviewed procedure.

**How treatment steps tie to onboard procedures and kit**

* Each treatment step in `responder-flows.json` carries `who` (`any crew`, `crew medical officer` or `ground`), `kit` and `source_ids`. At run time `get_pathway` returns the reviewed step text for the flow, and `get_capabilities` answers whether each kit item is on board.
* *Any crew* steps are first aid that the sources say anyone can do. NASA requires first aid and CPR training for all astronauts, and detailed medical training for the CMO (RS51). The microgravity CPR guideline asks that all crew be trained on the mission AED (RS04).
* *Crew medical officer* steps need extra training or a medicine. They appear as *as in the onboard procedure* and name no dose. The 2001 station checklist shows the pattern: medicines marked in the checklist need the flight surgeon first, except in an emergency or loss of signal, when the crew begins treatment and calls as soon as possible (RS08).
* *Ground* rows are the log for the ground: always the last row, never waited for. The NASA modeling forms (CliFFs) state, condition by condition, how much ground communication the best and worst cases were assumed to need; the flows above quote them, and each flow puts an onboard step where the form puts the ground. The ground reads the log at the next contact and replies with advice for later; a reply never unlocks a step.
* The crew leads every emergency. NASA designs emergency response so the crew does not depend on Mission Control (RS41). The ground is informed, never waited for.

Evidence:

* [RS41] "therefore the crew lead the response, and must not be dependent upon any action or assistance from Mission Control."
* [RS08] "Contact Surgeon before giving any medication marked with an asterisk. In an emergency or during Loss of Signal, begin appropriate treatment; then call Surgeon as soon as possible."
* [RS04] "each crew has at least one Crew Medical Officer (CMO) with extended medical skills"
* [RS68] "Program designates/trains two CMOs for each increment."
* [RS51] "general medical training, including but not limited to, first aid, cardiopulmonary resuscitation (CPR)" This brief quotes NASA-STD-3001 requirement V1 5002 (training for all crew).
* [RS04] "All crewmembers SHOULD be trained in the use of the specific AED provided during the mission."
* [RS51] "Crewmembers who have received a mission assignment as a Crew Medical Officer (CMO) shall be provided with detailed and specific medical training" This brief quotes NASA-STD-3001 requirement V1 5003.
* [RS07] "This version of IMPACT presumes physician level knowledge, skill, and ability will be present on the mission." The CliFFs model a physician aboard. This product assumes a non-doctor helper, so it uses them only for who and when, not for steps.

## Still to be set by clinical review or flight rules

Collected from the flows above. None of these has a number or method in this file.

* Not responding and not breathing normally: CPR technique in lunar and Martian gravity.
* Not responding and not breathing normally: Whether the vehicle carries an AED, a restraint board and a face shield (the kit inventory answers this at run time).
* Not responding but breathing normally: How to position and secure an unresponsive, breathing person in weightlessness.
* Not responding but breathing normally: How often the helper rechecks breathing.
* Choking: How to brace for back blows and thrusts in weightlessness.
* Severe bleeding: Whether the kit holds a tourniquet and haemostatic dressings (kit inventory).
* Severe bleeding: Cleaning dusty wounds on the Moon and Mars.
* Chest pain: Which chest pain medicines the onboard procedure allows, and who gives them.
* Trouble breathing: The oxygen level that starts oxygen treatment.
* Fainting: Positioning after a faint in weightlessness.
* Fainting: Lying and sitting BP timing and the change that matters.
* Seizure (fit): Which medicine the onboard procedure uses for a long seizure.
* Head injury: How often to recheck alertness, and for how long in flight.
* Eye injury or something in the eye: Sight check method and distance.
* Eye injury or something in the eye: How long to wait before an eye that still hurts after rinsing goes to the CMO.
* Burns: How to cool a burn with water in weightlessness.
* Burns: What size of burn needs the CMO at once.
* Toxic air, chemical exposure or smoke: Which mask or breathing set each vehicle and habitat carries (kit inventory and flight rules).
* Toxic air, chemical exposure or smoke: Safe haven for lunar and Martian habitats.
* Cabin pressure loss: Depressurization response for lunar and Martian habitats.
* Cabin pressure loss: The oxygen level that starts oxygen treatment.
* Decompression sickness after a spacewalk: How long after a spacewalk the app keeps DCS questions active.
* Solar particle event: take shelter: Dose thresholds for shelter and for leaving it (flight rules).
* Solar particle event: take shelter: Shelter design for a Martian base.
* Severe allergic reaction: Whether any crewmate may give adrenaline when the CMO is the patient or unavailable.
* Low blood sugar: The glucose level that counts as low for this crew.
* Low blood sugar: Whether the kit carries a glucose meter, glucose gel or glucagon.
* Low blood sugar: How much sugar to give and when to recheck.
* Severe belly or side pain: Which pain medicines the onboard procedure allows.
* Mental health crisis: Words the app suggests to the helper (to be written with ground behavioral health).
* All flows: every vital sign level that needs action (oxygen, blood pressure, pulse, temperature, breathing rate, glucose).
* All flows: positioning and restraint in weightlessness beyond what RS04 covers for CPR.
* First screen: the *Not sure* answer about the air (team default).

## Sources

New sources, saved under `research/responder/`. Access: all were read in full on 14 November 2026 unless the note says otherwise.

| ID | Title | Date | Access | Saved as |
|---|---|---|---|---|
| RS01 | [How to do CPR (St John Ambulance; nhs.uk first aid pages now redirect here)](https://www.sja.org.uk/first-aid-advice/cpr/) | Clinically reviewed 18 September 2026 | Public page; full text captured (HTML) | [research/responder/RS01.txt](research/responder/RS01.txt) |
| RS02 | [Adult basic life support Guidelines 2025 (Resuscitation Council UK, based on the European Resuscitation Council Guidelines 2025)](https://www.resus.org.uk/node/36437) | Published 27 October 2025 | Public page; full text captured (HTML). The ERC journal text itself was not read (publisher access) | [research/responder/RS02.txt](research/responder/RS02.txt) |
| RS03 | [Highlights of the 2025 American Heart Association Guidelines for CPR and ECC, Heart and Stroke Foundation of Canada edition](https://cpr.heartandstroke.ca/resource/GuidelinesHighlightsEN2025) | 2025 (guidelines published October 2025) | Public PDF; full text captured. Canadian edition of the AHA Highlights; only text not marked with the Canadian asterisk is quoted. cpr.heart.org and ahajournals.org refused automated access (HTTP 403) | [research/responder/RS03.txt](research/responder/RS03.txt) |
| RS04 | [Cardiopulmonary resuscitation (CPR) during spaceflight, a guideline for CPR in microgravity from the German Society of Aerospace Medicine (DGLRM) and the European Society of Aerospace Medicine Space Medicine Group (ESAM-SMG). Hinkelbein J and others, Scand J Trauma Resusc Emerg Med 2020;28:108](https://pmc.ncbi.nlm.nih.gov/articles/PMC7607644/) | Published 2 November 2020 | Open access article; full text captured from PubMed Central | [research/responder/RS04.txt](research/responder/RS04.txt) |
| RS05 | [Unresponsive and not breathing: learn first aid (British Red Cross)](https://www.redcross.org.uk/first-aid/learn-first-aid/unresponsive-and-not-breathing) | No date shown; read 14 November 2026 | Public page; full text captured (HTML) | [research/responder/RS05.txt](research/responder/RS05.txt) |
| RS06 | [How to put someone in the recovery position (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/recovery-position/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS06.txt](research/responder/RS06.txt) |
| RS07 | [ICL106 Sudden Cardiac Arrest Clinical Finding Form (CliFF), 2023 (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/sudden-cardiac-arrest-cliff-2023.pdf) | Form generated 24 July 2023 | Public PDF; full text captured | [research/responder/RS07.txt](research/responder/RS07.txt) |
| RS08 | [International Space Station Integrated Medical Group (IMG) Medical Checklist, JSC-48522-E4, ISS All Expeditions (NASA)](https://newspaceeconomy.ca/wp-content/uploads/2022/11/163533main_iss_med_cl.pdf) | 12 November 2001 (historical; superseded) | Third-party mirror of a NASA PDF; full text captured. Lines made only of unmapped font codes (Russian text) removed from the saved text. Historical: some steps (compression rate and ratio, finger sweep) are superseded by RS02 and RS03. The same Loss of Signal rule is in NASA's Shuttle Medical Checklist, JSC-48031 Rev K, p. 6-2 ([archived NASA copy](http://web.archive.org/web/20170212012224/https://www.nasa.gov/centers/johnson/pdf/177788main_MedicalCklist.pdf)) | [research/responder/RS08.txt](research/responder/RS08.txt) |
| RS09 | [Choking (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/choking/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS09.txt](research/responder/RS09.txt) |
| RS10 | [Choking/Obstructed Airway CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/choking-obstructed-airway-cliff-2023.pdf) | Form generated 15 June 2023 | Public PDF; full text captured | [research/responder/RS10.txt](research/responder/RS10.txt) |
| RS11 | [Severe bleeding (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/severe-bleeding/) | Clinically reviewed 8 September 2026 | Public page; full text captured (HTML) | [research/responder/RS11.txt](research/responder/RS11.txt) |
| RS12 | [Life-threatening bleed (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/life-threatening-bleed/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS12.txt](research/responder/RS12.txt) |
| RS13 | [First aid for someone bleeding heavily (British Red Cross)](https://www.redcross.org.uk/first-aid/learn-first-aid/bleeding-heavily) | No date shown; read 14 November 2026 | Public page; full text captured (HTML) | [research/responder/RS13.txt](research/responder/RS13.txt) |
| RS14 | [Traumatic Hypovolemic Shock CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/traumatic-hypovolemic-shock-cliff-2023.pdf) | Form generated 13 February 2023 | Public PDF; full text captured | [research/responder/RS14.txt](research/responder/RS14.txt) |
| RS15 | [Heart attack (NHS)](https://www.nhs.uk/conditions/heart-attack/) | Page last reviewed 31 March 2026 | Public page; full text captured (HTML) | [research/responder/RS15.txt](research/responder/RS15.txt) |
| RS16 | [Heart attack (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/heart-attack/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS16.txt](research/responder/RS16.txt) |
| RS17 | [Shortness of breath (NHS)](https://www.nhs.uk/symptoms/shortness-of-breath/) | Page last reviewed 30 January 2024 | Public page; full text captured (HTML) | [research/responder/RS17.txt](research/responder/RS17.txt) |
| RS18 | [Asthma attack (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/asthma-attack/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS18.txt](research/responder/RS18.txt) |
| RS19 | [Fainting (NHS)](https://www.nhs.uk/conditions/fainting/) | Page last reviewed 17 August 2026 | Public page; full text captured (HTML) | [research/responder/RS19.txt](research/responder/RS19.txt) |
| RS20 | [Fainting (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/fainting/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS20.txt](research/responder/RS20.txt) |
| RS21 | [Seizure (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/seizure/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS21.txt](research/responder/RS21.txt) |
| RS22 | [First Aid for Seizures (CDC)](https://www.cdc.gov/epilepsy/first-aid-for-seizures/index.html) | 15 May 2024 | Public page; full text captured (HTML) | [research/responder/RS22.txt](research/responder/RS22.txt) |
| RS23 | [Epilepsy (NHS)](https://www.nhs.uk/conditions/epilepsy/) | Page last reviewed 6 March 2025 | Public page; full text captured (HTML) | [research/responder/RS23.txt](research/responder/RS23.txt) |
| RS24 | [Seizures CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/seizures-cliff-2023.pdf) | Form generated 22 August 2023 | Public PDF; full text captured | [research/responder/RS24.txt](research/responder/RS24.txt) |
| RS25 | [Head injury and concussion (NHS)](https://www.nhs.uk/conditions/head-injury-and-concussion/) | Page last reviewed 29 May 2025 | Public page; full text captured (HTML) | [research/responder/RS25.txt](research/responder/RS25.txt) |
| RS26 | [Head injury (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/head-injury/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS26.txt](research/responder/RS26.txt) |
| RS27 | [Signs and Symptoms of Concussion (CDC HEADS UP)](https://www.cdc.gov/heads-up/signs-symptoms/index.html) | 15 September 2025 | Public page; full text captured (HTML) | [research/responder/RS27.txt](research/responder/RS27.txt) |
| RS28 | [Minor Head Injury (Trauma) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-minor-head.pdf) | Form generated 22 November 2022 | Public PDF; full text captured | [research/responder/RS28.txt](research/responder/RS28.txt) |
| RS29 | [Severe Head Injury (Trauma) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/trauma-severe-head-cliff-2023.pdf) | Form generated 23 August 2023 | Public PDF; full text captured | [research/responder/RS29.txt](research/responder/RS29.txt) |
| RS30 | [Eye injuries (NHS)](https://www.nhs.uk/conditions/eye-injuries/) | Page last reviewed 12 March 2026 | Public page; full text captured (HTML) | [research/responder/RS30.txt](research/responder/RS30.txt) |
| RS31 | [Eye injury (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/eye-injury/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS31.txt](research/responder/RS31.txt) |
| RS32 | [Eye Foreign Body CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/eye-foreign-body-cliff-2023.pdf) | Form generated 23 August 2023 | Public PDF; full text captured | [research/responder/RS32.txt](research/responder/RS32.txt) |
| RS33 | [Burn Chemical Eye CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-chemical-eye-cliff-2023.pdf) | Form generated 23 August 2023 | Public PDF; full text captured | [research/responder/RS33.txt](research/responder/RS33.txt) |
| RS34 | [Lunar Dust Exposure (EVA/Habitat) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/dust-exposure-lunar-eva-habitat-cliff-2023.pdf) | Form generated 15 June 2023 | Public PDF; full text captured | [research/responder/RS34.txt](research/responder/RS34.txt) |
| RS35 | [Evidence Report: Risk of Adverse Health & Performance Effects of Celestial Dust Exposure (NASA Human Research Program)](https://humanresearchroadmap.nasa.gov/Evidence/reports/Dust.pdf) | Approved for public release 4 August 2015 | Public PDF; full text captured | [research/responder/RS35.txt](research/responder/RS35.txt) |
| RS36 | [Burns and scalds (NHS)](https://www.nhs.uk/conditions/burns-and-scalds/) | Page last reviewed 31 March 2026 | Public page; full text captured (HTML) | [research/responder/RS36.txt](research/responder/RS36.txt) |
| RS37 | [Burns and scalds (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/burns-and-scalds/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS37.txt](research/responder/RS37.txt) |
| RS38 | [Severe burn (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/severe-burn/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS38.txt](research/responder/RS38.txt) |
| RS39 | [Chemical burns (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/chemical-burn/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS39.txt](research/responder/RS39.txt) |
| RS40 | [Burn Moderate-Severe Thermal Skin CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/burn-moderate-to-severe-thermal-cliff-2023.pdf) | Form generated 15 June 2023 | Public PDF; full text captured | [research/responder/RS40.txt](research/responder/RS40.txt) |
| RS41 | [Evolution of Hardware and Philosophy of Emergency Response Actions on the International Space Station and Future Spacecrafts. Tobias B, McMillan C (NASA JSC), SpaceOps 2023](https://ntrs.nasa.gov/api/citations/20220019247/downloads/Emergency%20Ops%20History%20for%20SpaceOps%20Conference%202023.pdf) | Conference 6 to 10 March 2023 | Public PDF; full text captured | [research/responder/RS41.txt](research/responder/RS41.txt) |
| RS42 | [Toxic Exposure (Ammonia) CliFF (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-exposure-ammonia-cliff.pdf) | Form generated 14 May 2020 | Public PDF; full text captured | [research/responder/RS42.txt](research/responder/RS42.txt) |
| RS43 | [Toxic Inhalation Exposure (Combustion Products) CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/toxic-inhalation-exposure-combustion-products-cliff-2023.pdf) | Form generated 15 June 2023 | Public PDF; full text captured | [research/responder/RS43.txt](research/responder/RS43.txt) |
| RS44 | [Smoke Inhalation CliFF (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/smoke-inhalation-cliff.pdf) | Form generated 11 May 2020 | Public PDF; full text captured | [research/responder/RS44.txt](research/responder/RS44.txt) |
| RS45 | [OCHMO-TB-015 Spaceflight Toxicology, Rev B (NASA technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-015-spaceflight-toxicology.pdf) | 30 October 2023 | Public PDF; full text captured | [research/responder/RS45.txt](research/responder/RS45.txt) |
| RS46 | [NIOSH Pocket Guide to Chemical Hazards: Ammonia (CDC/NIOSH)](https://www.cdc.gov/niosh/npg/npgd0028.html) | Page last reviewed 30 October 2019 | Public page; full text captured (HTML) | [research/responder/RS46.txt](research/responder/RS46.txt) |
| RS47 | [Ammonia Chemical Fact Sheet (CDC Chemical Emergencies)](https://www.cdc.gov/chemical-emergencies/chemical-fact-sheets/ammonia.html) | 11 June 2026 | Public page; full text captured (HTML) | [research/responder/RS47.txt](research/responder/RS47.txt) |
| RS48 | [Carbon Monoxide (CO) Poisoning Basics (CDC)](https://www.cdc.gov/carbon-monoxide/about/index.html) | Updated 26 October 2026 | Public page; full text captured (HTML) | [research/responder/RS48.txt](research/responder/RS48.txt) |
| RS49 | [Evidence Report: Risk of Reduced Crew Health and Performance due to Decompression Sickness and Hypoxia (NASA Human Research Program)](https://humanresearchroadmap.nasa.gov/Evidence/other/Evidence%20Report%20Hypoxia%20and%20DCS_FINAL_1-8-2024.pdf) | Approved for public release 31 October 2023 | Public PDF; full text captured | [research/responder/RS49.txt](research/responder/RS49.txt) |
| RS50 | [Hypoxia: The Higher You Fly, The Less Air In The Sky (FAA pilot safety brochure OK-20-0926)](https://www.faa.gov/pilots/safety/pilotsafetybrochures/media/hypoxia.pdf) | 29 April 2020 | Public PDF; full text captured | [research/responder/RS50.txt](research/responder/RS50.txt) |
| RS51 | [OCHMO-TB-037 Decompression Sickness (DCS) Risk Mitigation, Rev D (NASA technical brief)](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-037-decompression-sickness.pdf) | Revision D, 5 October 2026 | Public PDF; full text captured | [research/responder/RS51.txt](research/responder/RS51.txt) |
| RS52 | [EVA-Related Decompression Sickness CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/eva-related-decompression-sickness-cliff-2023.pdf) | Form generated 22 August 2023 | Public PDF; full text captured | [research/responder/RS52.txt](research/responder/RS52.txt) |
| RS53 | [How NASA Will Protect Astronauts From Space Radiation at the Moon (NASA Science)](https://science.nasa.gov/science-research/heliophysics/how-nasa-will-protect-astronauts-from-space-radiation-at-the-moon) | 7 August 2019 | Public page; full text captured (HTML) | [research/responder/RS53.txt](research/responder/RS53.txt) |
| RS54 | [To Protect Artemis II Astronauts, NASA Experts Keep Eyes on Sun (NASA Science)](https://science.nasa.gov/missions/artemis/artemis-2/to-protect-artemis-ii-astronauts-nasa-experts-keep-eyes-on-sun/) | 16 March 2026 (updated 17 March 2026) | Public page; full text captured (HTML) | [research/responder/RS54.txt](research/responder/RS54.txt) |
| RS55 | [Acute Radiation Syndrome CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/acute-radiation-syndrome-cliff-2023.pdf) | Form generated 22 August 2023 | Public PDF; full text captured | [research/responder/RS55.txt](research/responder/RS55.txt) |
| RS56 | [Anaphylaxis (NHS)](https://www.nhs.uk/conditions/anaphylaxis/) | Page last reviewed 21 June 2023 | Public page; full text captured (HTML) | [research/responder/RS56.txt](research/responder/RS56.txt) |
| RS57 | [Anaphylaxis (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/anaphylaxis/) | Clinically reviewed 25 April 2025 | Public page; full text captured (HTML) | [research/responder/RS57.txt](research/responder/RS57.txt) |
| RS58 | [Anaphylaxis CliFF (2023) (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/anaphylaxis-cliff-2023.pdf) | Form generated 23 August 2023 | Public PDF; full text captured | [research/responder/RS58.txt](research/responder/RS58.txt) |
| RS59 | [Low blood sugar (hypoglycaemia) (NHS)](https://www.nhs.uk/conditions/low-blood-sugar-hypoglycaemia/) | Page last reviewed 3 August 2023 | Public page; full text captured (HTML) | [research/responder/RS59.txt](research/responder/RS59.txt) |
| RS60 | [Diabetic emergencies (St John Ambulance)](https://www.sja.org.uk/first-aid-advice/diabetes/) | Clinically reviewed 28 April 2025 | Public page; full text captured (HTML) | [research/responder/RS60.txt](research/responder/RS60.txt) |
| RS61 | [Low Blood Glucose (Hypoglycemia) (NIDDK, NIH)](https://www.niddk.nih.gov/health-information/diabetes/overview/preventing-problems/low-blood-glucose-hypoglycemia) | Last reviewed July 2021 | Public page; full text captured (HTML) | [research/responder/RS61.txt](research/responder/RS61.txt) |
| RS62 | [Stomach ache (NHS)](https://www.nhs.uk/symptoms/stomach-ache/) | Page last reviewed 20 October 2026 | Public page; full text captured (HTML) | [research/responder/RS62.txt](research/responder/RS62.txt) |
| RS63 | [Kidney stones (NHS)](https://www.nhs.uk/conditions/kidney-stones/) | Page last reviewed 17 September 2026 | Public page; full text captured (HTML) | [research/responder/RS63.txt](research/responder/RS63.txt) |
| RS64 | [Warning Signs of Suicide (NIMH)](https://www.nimh.nih.gov/health/publications/warning-signs-of-suicide) | Revised 2025 | Public page; full text captured (HTML) | [research/responder/RS64.txt](research/responder/RS64.txt) |
| RS65 | [5 Action Steps to Help Someone Having Thoughts of Suicide (NIMH)](https://www.nimh.nih.gov/health/publications/5-action-steps-to-help-someone-having-thoughts-of-suicide) | Revised 2024 | Public page; full text captured (HTML) | [research/responder/RS65.txt](research/responder/RS65.txt) |
| RS66 | [Where to get urgent help for mental health (NHS)](https://www.nhs.uk/nhs-services/mental-health-services/where-to-get-urgent-help-for-mental-health/) | Page last reviewed 23 October 2026 | Public page; full text captured (HTML) | [research/responder/RS66.txt](research/responder/RS66.txt) |
| RS67 | [Behavioral Emergency CliFF (NASA)](https://www.nasa.gov/wp-content/uploads/2026/08/behavioral-emergency-cliff.pdf) | Form generated 11 May 2020 | Public PDF; full text captured | [research/responder/RS67.txt](research/responder/RS67.txt) |
| RS68 | [OCHMO-TB-044 Medical Operations Spaceflight Considerations (NASA technical brief)](https://www.nasa.gov/wp-content/uploads/2025/08/ochmo-tb-044-medical-operations.pdf) | 13 August 2025 | Public PDF; full text captured | [research/responder/RS68.txt](research/responder/RS68.txt) |

Existing package sources cited (defined in their own files, not re-saved here):

| ID | Where it is defined |
|---|---|
| C07 | DEMO-CONDITIONS.md (camera pulse and motion, Sensors 2021) |
| C08 | DEMO-CONDITIONS.md (FDA, Pulse Oximeter Basics) |
| C09 | DEMO-CONDITIONS.md (NASA OCHMO-TB-019 Orthostatic Intolerance) |
| C11 | DEMO-CONDITIONS.md (NHLBI, What Is a Heart Attack?) |
| C14 | DEMO-CONDITIONS.md (NASA OCHMO-TB-016 Behavioral Health and Performance) |
| S04 | sources.csv (NHLBI, Heart Attack: Diagnosis) |
| S05 | sources.csv (NIDDK, Diagnosis of Kidney Stones) |
| S08 | sources.csv (REMM, Managing Acute Radiation Syndrome) |
| S09 | sources.csv (NIDDK, Diagnosis of Appendicitis) |

## What could not be sourced or accessed

* **American Heart Association pages** (cpr.heart.org, professional.heart.org, ahajournals.org) refused automated access (HTTP 403). The Heart and Stroke Foundation of Canada edition of the 2025 AHA Highlights was used instead (RS03); only text without the Canadian asterisk is quoted.
* **European Resuscitation Council 2025 guideline text** is on the publisher's site and was not read. The Resuscitation Council UK page that sets out the ERC 2025 adult basic life support points was used (RS02).
* **NHS first aid pages** now redirect to St John Ambulance, so SJA pages are used for first aid steps (RS01, RS06, RS09, RS11, RS12 and others).
* **British Red Cross choking and seizure pages** returned not found (404). SJA and CDC pages were used.
* **ATSDR ammonia medical management guide** loads its text with JavaScript and could not be saved. NIOSH (RS46) and the CDC fact sheet (RS47) were used.
* **FDA epinephrine auto-injector page** returned not found (404). The NHS anaphylaxis page (RS56) was used.
* **The ISS medical checklist** (RS08) was read from a third party mirror of a NASA file and is from 2001. It is used for structure and for the medicine rule, never for current CPR numbers.
* **Not found in any source read:** CPR, choking and recovery positions for lunar and Martian gravity; bracing for back blows and thrusts in weightlessness; cooling burns with water in weightlessness; depressurization and safe haven procedures for surface habitats; radiation shelter design for a Martian base; every vital sign action threshold; dose thresholds for shelter; the oxygen level that starts oxygen treatment. Each is marked in its flow.
* **Current onboard procedures** (the medical checklist in use today, the kit inventory, and vehicle emergency procedures for Orion and Gateway) are not public. Every *as in the onboard procedure* step depends on them.
