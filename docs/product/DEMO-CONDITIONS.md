# Demo conditions: seven worked entries

**Unreviewed draft: needs clinician review before live guidance.** Planning document for the Crew Health Check package, 14 November 2026. No clinician has reviewed this page. Nothing on it is a validated diagnostic pathway, an urgent-care protocol or a claim that this kit can test for a condition.

## What this page is

The [condition browser](condition-browser.html) lists the 119 conditions of NASA's active condition list (ICL 1.0 v3.2, S01) and 792 broader inventory rows. Most of those entries are planning synthesis: text shared by a whole condition family, which a clinician must refine before it guides anyone. This page works seven entries in full: the ones the demo story touches or must not miss.

Each entry gives NASA membership, what the crew member reports, the intake questions, the urgent red flags, what this kit can and cannot measure, what would confirm or exclude the condition and whether that is available here, what the assistant may and must never say, the measurement card for the key reading, the body map regions, and sources with short exact quotes.

Rules used on this page:

- **Membership is not capability.** A NASA list entry means the condition is modeled for exploration missions. It does not mean this kit can test for it (S01; [PRD](PRD.md) section 2).
- **The kit is what the PRD names.** Camera station (pulse and breathing), kit pulse oximeter, blood pressure cuff, thermometer and wrist band (sleep and activity estimates), plus the daily check (symptoms, sleep, mood) and the reaction test. There is no ECG, no laboratory, no eye imaging and no live cabin CO2 sensor. The cabin CO2 card is Replay.
- **No invented thresholds.** Where a source gives a number, it is quoted with its context. Where no source was read, the text says "threshold to be set by clinical review".
- **Emergency numbers become the mission's route.** Earth sources name 9-1-1 (United States) or 999 and A&E (United Kingdom). In the product these mean the designated medical and emergency response route of PRD section 9, opened by **Someone needs help** without the model.
- **Quotes are exact.** Every quote is text from the page or PDF as read on 14 November 2026, with the source's own apostrophes. Papers marked "abstract" were read as their PubMed abstract only. Each quote was checked by script against the saved page text. C29 and C30 were read on 14 November 2026 for the plan aboard; their saved text is still to be added to the package and checked the same way.
- **Source IDs.** S IDs are already in [sources.csv](sources.csv). C01 to C30 are new on this page and are not yet in sources.csv; the full list with URLs and dates is in [Sources added by this page](#sources-added).
- **Body map.** Region names come from the fixed list: head, eyes, ear, neck, shoulder, heart, lungs, abdomen, flank, back, wrist, hand, legs, knee, ankle. A filled lime dot is a measurement, a hollow ring is a reported symptom, an open dashed ring is a test that is not available here.

## The demo path at a glance

| Act | What happens ([PITCH.md](PITCH.md)) | Entries it touches |
|---|---|---|
| 1. The kit (station) | What this ship can test, computed from the kit map, and "[P] of 119 have a plan aboard" | The entries on this page are among the kit map's 119 rows; every row and its plan is written at the event from its Clinical Findings Form |
| 2. Notice (station) | Short sleep (band estimate, confirmed by Mara), a slower reaction test, mood below the usual, the cabin CO2 Replay card | [ICL95](#icl95), [ICL18](#icl18), [ICL14](#icl14) for mood, [ICL64](#icl64) for the cabin air card |
| 3. Earth out of reach (Moon base) | The mission moves to the Moon base and Mara confirms it; "Ground link down · next contact in 26 h" (Sim); nothing else changes; Ilya Brandt and Sofia Reyes, the two medical officers, are on surface walk 1 until 11:40 | No condition entry. Every entry below reads the same with the link down |
| 4. Ask and measure (Moon base) | "My heart has been racing since this morning." Fixed urgent questions first, then one timing question and a pulse request; "No reading: too much movement", kept as failed; then a Live camera pulse | [ICL3](#icl3), which the urgent questions must catch, and the [ICL10](#icl10) measurement card |
| 5. Check it another way (Moon base) | What was observed, what might explain it (short sleep, stress), what this kit can't test (rhythm needs an ECG); then Tomas Lind is taught the wrist pulse check and the cameras check the count; then the plan aboard | [ICL10](#icl10) (its check another way and its plan aboard), [ICL14](#icl14), [ICL95](#icl95) |
| 6. Help, anywhere (Moon base) | The walkers come in: lunar dust over its limit; with the model stopped, **Someone needs help** opens at once for Tomas, dust in one eye; roles below the steps; For the ground waits for the next contact | The `eye_injury` flow in [RESPONDER-FLOWS.md](RESPONDER-FLOWS.md); no condition entry |
| Body map | A judge taps the eyes and sees "needs specialist eye examination" | [ICL100](#icl100) |

| Entry | NASA name, as written | Key reading | Can this kit confirm or exclude it? | Body map regions |
|---|---|---|---|---|
| [ICL10](#icl10) | `ATRIAL FIBRILLATION/ ATRIAL FLUTTER` | Heart rate and blood oxygen | No. Needs an ECG | heart, head, hand, wrist |
| [ICL3](#icl3) | `ACUTE CORONARY SYNDROME` | Blood pressure, for the record only | No. Needs ECG and troponin; the urgent route comes first | heart, lungs, neck, shoulder, back, abdomen |
| [ICL14](#icl14) | `BHP - ANXIETY` | Mood | No. Needs a clinical interview and a physical examination | head, heart, lungs, hand |
| [ICL95](#icl95) | `SPACE ADAPTATION - INSOMNIA` | Sleep | No. The band and a sleep diary help a clinician; they do not diagnose | head, wrist |
| [ICL18](#icl18) | `BHP - SLEEP DISTURBANCE` | Reaction test | No. As ICL95, plus a behavioral health interview | eyes, head, wrist |
| [ICL64](#icl64) | `HEADACHE - CO2 INDUCED` | Cabin CO2 (Replay) | No. Needs measured CO2 where the person was and a clinical examination | head, lungs |
| [ICL100](#icl100) | `SPACEFLIGHT ASSOCIATED NEURO-OCULAR SYNDROME (SANS)` | Eye examination (not available here) | No. Needs fundoscopy, OCT or ocular ultrasound | eyes, head |

The NASA names above are copied exactly, including NASA's spaced hyphens and spacing.

**A note for the demo fixture: palpitations that do not go away.** NHS (C01) treats ongoing palpitations as an emergency trigger on their own, not only with other symptoms: "You currently have heart palpitations and they do not go away or you also have any of these symptoms:", followed by chest pain, shortness of breath, and feeling faint or fainting. The demo's fixed urgent questions cover the three symptoms. The timing question covers the fourth trigger. If the person says the racing has been constant since the morning and is still present, the deterministic rule should open **Someone needs help**; how long counts as "do not go away" is a threshold to be set by clinical review. For the recorded demo, the Sim persona's scripted answer should be that it comes and goes and is easing now, so the story stays consistent with the source.

<a id="icl10"></a>

## ICL10: Atrial fibrillation or atrial flutter

**Unreviewed draft: needs clinician review before live guidance.**

| Field | Detail |
|---|---|
| NASA membership | ICL10 `ATRIAL FIBRILLATION/ ATRIAL FLUTTER`, active in ICL 1.0 v3.2. [HRP-48036 Rev B, page 107](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf#page=107) (S01). Establishes membership only |
| Why it is on the demo path | Act 4: "My heart has been racing since this morning." A fast or irregular rhythm is one explanation, and it is the one this kit cannot test. Act 5 says so (the heart's rhythm needs an ECG, and this kit has none), then checks it another way and runs the plan aboard |
| Body map | **heart**: the Live pulse lands here as a filled dot; "ECG: not available here" is an open dashed ring, and it stays open after every check below. **head**: glows while the cameras read the face. **hand**: the oximeter on a fingertip. **wrist**: the band's sleep estimate from Act 2, and in Act 5 the pulse felt by Tomas as a filled dot ("Felt by Tomas") |
| Key reading | Heart rate and blood oxygen (card below) |

### What the crew member reports

A heartbeat that skips, flutters, pounds, or beats too hard or too fast (C02). An arrhythmia is a problem with the rate or the rhythm of the heartbeat (C03). Atrial fibrillation often has no symptoms at all, and its most common symptom is fatigue (C04). Other listed symptoms are low blood pressure, breathlessness, chest pain and dizziness or fainting (C04); the last three are also urgent red flags below.

### Intake questions

Asked in this order. The first group is fixed and runs without the model. Each later question shows its purpose, and every answer can be present, absent, unknown or declined; an unknown answer is never a negative (PRD section 9).

1. **Fixed urgent questions, no model:** Do you have chest pain or discomfort now? Are you short of breath? Have you fainted, or do you feel you might faint? (C01, C02)
2. **The assistant's timing question:** Is it there all the time, or does it come and go, and is it happening now? Purpose: palpitations that do not go away are an urgent trigger in themselves (C01), and timing is what a reviewer asks first (C02).
3. What were you doing when it started? (C02)
4. Has this happened before? Do you have a known heart condition? (C01 lists both as reasons to see a doctor.)
5. Caffeine, alcohol, nicotine or medicines today? (C01 lists them among common causes.)
6. Sleep and stress are not asked again. The assistant reads last night's confirmed band estimate and today's mood from the daily check, because C01 lists lack of sleep and stress and anxiety among common causes.
7. Unusual tiredness, or breathlessness when lying down or exercising? (C04)

### Urgent red flags

Each opens **Someone needs help** at that sign's flow at once, before any model call. A normal reading never cancels one (PRD section 9).

| Red flag | Source |
|---|---|
| Chest pain or discomfort with the racing heart | C01, C02 |
| Shortness of breath or difficulty breathing | C01, C02 |
| Feeling faint or fainting | C01 |
| Palpitations still present that do not go away (how long: threshold to be set by clinical review) | C01 |
| Any heart attack symptom (see [ICL3](#icl3)) | C10 |

### What this kit can measure

| Kit reading | What it tells us | What it does not tell us | Source |
|---|---|---|---|
| Camera pulse (camera station) | Pulse rate over the capture window, with the camera that carried it and a quality result. A failed quality check is "No reading" with its reason | The rhythm. Our pipeline estimates rate only and has no rhythm classifier. Optical irregular-pulse alerts exist in smartwatches, yet even there the ECG decided: 34% of notified people had atrial fibrillation on the later ECG patch. In one camera study, unconstrained body motion greatly reduced accuracy, and the authors advise limiting head motion | C06, C07 |
| Camera breathing | Breathing rate from chest motion, independent of the pulse result | Oxygen level, lung sounds, or why someone is breathless | PRD section 5 (design, not a clinical source) |
| Kit oximeter | An SpO2 estimate and a second pulse rate to compare with the camera. FDA lists a fast or racing pulse among signs of low oxygen | The rhythm. Accuracy depends on circulation, skin pigmentation, skin temperature, tobacco use and nail polish, so it is read with the symptoms | C08 |
| Blood pressure cuff | Pressure at that moment. Low blood pressure is a listed symptom of atrial fibrillation, and a provider measures it in the examination | The rhythm. How the chosen automated cuff behaves during an irregular rhythm is not sourced here: check the device instructions at clinical review | C04, C05 |
| Thermometer | Temperature, as context | Nothing in the sources read for this entry uses temperature to confirm or exclude it | None |
| Wrist band | Last night's sleep estimate, proposed and then confirmed. Lack of sleep is a common cause of palpitations | Heart rhythm. The band is not used for rhythm | C01 |

### What would confirm or exclude it

| Test | Equipment and person | Available here? | Source |
|---|---|---|---|
| ECG while the symptom is present | ECG device, trained operator, clinician to read it | No. An existing ECG report can be imported (PRD section 4.4) | S03, C05 |
| Ambulatory ECG (Holter, event monitor or loop recorder) for symptoms that come and go | Worn or implanted recorder; clinician | No | S03, C05 |
| Blood tests: potassium and other electrolytes, thyroid hormone | Laboratory | No | S03, C05 |
| Echocardiography | Cardiac ultrasound and a trained operator | No | C05 |
| Pulse check for an irregular rhythm by a trained person | No equipment; a trained crew medical officer following a reviewed procedure | Only if both exist, and it does not replace an ECG | C05 |

### Check it another way

The first stage of the plan aboard when the ECG is not aboard. It is a substitute check, not a test that confirms or excludes anything.

| Part | Detail | Source |
|---|---|---|
| What it is | A crewmate without medical training feels and counts the pulse at the wrist for 60 seconds while the cameras count it from the face (skill `pulse_check_wrist`, Talk a crewmate through it) | C29 |
| Who | The helper the role engine proposes: in the demo, Tomas Lind, who practiced the pulse check on day 12, because both medical officers are outside | PRD section 4.13 |
| Teach steps | Team wording from the BHF page, one step at a time, with the base of the thumb lit on the figure: 1 "Mara: hold your arm out, palm up, elbow slightly bent." 2 "Tomas: place the pads of your first and middle fingers on the inside of Mara's wrist, at the base of the thumb." 3 "Press lightly. If you cannot feel anything, press slightly harder." 4 "Tap Beat each time you feel one. Keep going for 60 seconds." 5 "Did it feel regular?" Yes; No, it jumped around; Not sure | C29 |
| Why 60 seconds | "If your pulse feels irregular, you should check for a full 60 seconds." | C29 |
| The camera check | The count against the camera pulse, within [tolerance: team default, to be set by clinical review]. Agree: "Your count 92 · cameras 96 · they agree" (example values). Disagree: the skill's authored correction ("Move your fingers a little toward the thumb, and count again."), a retry, and no value written. No camera reading: "Your count is kept, unchecked", never cited as support | PRD R52 |
| What it can tell us | "You may be able to tell if you have a normal or irregular heart rate by checking your pulse". A helper's "it jumped around" tightens the watch stage of the plan; it is not a diagnosis | C30 |
| What it cannot replace | An ECG. A felt pulse cannot show why the heart is racing or what the rhythm is; "An electrocardiogram (EKG or ECG) is the most common test for diagnosing arrhythmias" (S03). The heart's dashed ring stays, and no text says the rhythm is normal or atrial fibrillation excluded | S03, C05 |
| What gets written | An observation with method crewmate palpation, operator Tomas and subject Mara, with value and duration; the helper's answer as a separate answer (present, absent or unknown); the guided check with the camera reference and the result. A disagreement writes no value | PRD section 4.13 |

### On board, with no ground

The plan aboard for ICL10 (onboard plan ICL10, unreviewed draft), the same with the ground link up, delayed or down. Mode: **Stabilize and watch**.

| Stage | Step | Source |
|---|---|---|
| Check it another way | The wrist pulse check above | C29, C30 |
| Do now | "Rest. No caffeine. Note each time it starts and stops." | C01, C02 |
| Watch | "Pulse at the cameras in [interval to be set by clinical review], and at tonight's check." | Team default |
| If it gets worse | "Chest pain, fainting, hard breathing, or it won't stop: Someone needs help opens." | C01, C02 |
| Medical officer | "Ilya reviews when back inside. Anything more, as in the onboard procedure." No medicine is named and no dose exists in the plan | PRD section 9 |
| Return (Moon base only) | Not part of this demo path. Chest pain that does not settle belongs to [ICL3](#icl3) and its plan, whose return criteria read "[to be set by flight rules]" | PRD section 4.13 |
| Log for the ground | Always last: "Ground link down · next contact in 26 h (Sim). The log goes then. Nothing here waits for it." With the link up: "Ground link up · the log goes now" | PRD R61 |

### What the assistant may say

- "Your pulse was [value] per minute by camera at [time], Live, from [camera]. A pulse rate does not show the heart's rhythm. An ECG would answer that, and this kit has none."
- "The first attempt failed because of movement. It is kept as failed and is not used."
- "Lack of sleep, stress, caffeine and alcohol are common causes of a noticeable heartbeat (C01). Your confirmed sleep last night was short. That is a possible explanation, not a conclusion."
- "We can check it another way: Tomas can feel your pulse at the wrist for 60 seconds while the cameras count it. It cannot replace an ECG."
- "Tomas's count and the cameras agree. That tells us your pulse rate, not your rhythm; only an ECG can show the rhythm, so the plan keeps watching."
- "Here is the plan aboard: rest, no caffeine, and note each time it starts and stops; your pulse again at the cameras later and at tonight's check; if chest pain, fainting or hard breathing starts, or it won't stop, Someone needs help opens. Ilya reviews it when back inside. The log for the ground goes at the next contact, and you can preview it first. Nothing here waits for it."

### What the assistant must never say

- That the rhythm is normal, that this is not atrial fibrillation, or "arrhythmia excluded" ([UX-DESIGN.md](UX-DESIGN.md)).
- That it is only anxiety, only stress or only lack of sleep.
- That a normal pulse or oxygen reading means the person is fine. Serious symptoms are not canceled by a normal number (PRD section 9).
- Any value estimated from the failed reading.
- Any medicine, dose or change to a medicine.
- That a pulse felt by a crewmate, or a count that agrees with the cameras, shows the rhythm or replaces an ECG.
- That anything waits for the ground, or that the next step is to tell Earth.

### Measurement card: Heart rate and blood oxygen

**How to measure it here.** Sit still at the camera station, at rest, because heart rate normally rises with activity (C03). The cameras read the pulse from the face. In one camera study, the smallest beat-interval error came with light above 500 lux from the front, and unconstrained body motion greatly reduced accuracy (C07); our own lighting and motion limits are not yet measured. A failed quality check returns "No reading" with its reason. For the kit oximeter, keep the hand warm, relaxed and below heart level, sit still and wait for one steady number (C08).

**Why it is worth measuring.** A timed pulse while the symptom is present is the first fact a reviewer needs, and providers check the pulse for its speed and for an irregular rhythm (C05). The oximeter adds an oxygen estimate, and FDA lists a fast or racing pulse among the signs of low oxygen (C08). Neither shows the rhythm; that needs an ECG (S03).

**What spaceflight does to it.** On entering microgravity, body fluids shift and, within about 2 days, circulating volume falls by around 15%; studies also show decreased baroreflex sensitivity (C09). Short sleep is common in flight (C15), and lack of sleep is a common cause of palpitations (C01). Compare each reading with the person's own in-flight baseline, not an Earth number.

**When to tell a crewmate or the ground.** At once, through **Someone needs help**, if a racing heart comes with chest pain, shortness of breath or feeling faint, or does not go away (C01). A rate outside the personal baseline band: threshold to be set by clinical review. Palpitations that keep coming back or happen more often go into the plan's watch stage and the medical officer's review on board (C01), and into the log for the ground at the next contact.

**What you can do.** Note when it started, how often it happens and what you were doing (C02). If palpitations are not caused by a health condition, avoiding triggers such as stress, caffeine and alcohol can help (C01). Add an existing ECG report if there is one. Ask a crewmate to check your pulse another way (above). Preview the log for the ground before you approve it; it leaves at the next contact.

### Sources and quotes

- **S03** NHLBI, Arrhythmias: Diagnosis: "An electrocardiogram (EKG or ECG) is the most common test for diagnosing arrhythmias." "Monitors for long-term recording include Holter monitors and implantable loop recorders." "Blood tests measure the level of certain substances in the blood, such as potassium or other electrolytes and thyroid hormone."
- **C01** NHS, Heart palpitations: "You currently have heart palpitations and they do not go away or you also have any of these symptoms:" "feeling faint or fainting" "lack of sleep" "stress and anxiety" "alcohol, caffeine, nicotine and recreational drugs" "they keep coming back or they are happening more often" "Avoiding things that can trigger palpitations, such as stress, smoking, caffeine and alcohol, can help if palpitations are not caused by a health condition."
- **C02** NHLBI, Arrhythmias: Symptoms: "You may also feel like your heart is skipping a beat, fluttering, pounding, or beating too hard or too fast." "If your symptoms are very serious, such as difficulty breathing or chest pain, seek emergency medical care." "Keep track of when and how often you have symptoms, what you feel, what you were doing, and whether these things change over time."
- **C03** NHLBI, What Is an Arrhythmia?: "An arrhythmia, or irregular heartbeat, is a problem with the rate or rhythm of your heartbeat." "It is normal for your heart rate to speed up during physical activity and to slow down while resting or sleeping."
- **C04** NHLBI, Atrial Fibrillation: Symptoms: "It often occurs with no symptoms." "The most common symptom of atrial fibrillation is fatigue, or extreme tiredness." "Low blood pressure" "Difficulty breathing, especially when lying down or when exercising"
- **C05** NHLBI, Atrial Fibrillation: Diagnosis: "A healthcare provider will diagnose atrial fibrillation based on your medical and family history, a physical exam, the results from an electrocardiogram (EKG), and possibly other tests and procedures." "Your provider may check your pulse for an irregular speed or unusual heart rhythm even if you have no symptoms." "Measure your blood pressure, height, and weight" "Echocardiography (ultrasound) looks at the heart valves, chamber sizes and heart pumping function."
- **C06** Perez and others, NEJM 2019 (abstract): "Optical sensors on wearable devices can detect irregular pulses." "Among participants who received notification of an irregular pulse, 34% had atrial fibrillation on subsequent ECG patch readings"
- **C07** Tohma and others, Sensors 2021 (abstract): "the smallest mean absolute R-R interval (RRI) error was obtained when light greater than 500 lux was cast from the front" "The accuracy of the HRV measurement was greatly reduced when the body motion was not constrained; thus, it is necessary to limit the body motion, especially the head motion"
- **C08** FDA, Pulse Oximeter Basics: "uses light beams to estimate a person’s blood oxygen level (oxygen saturation) and their pulse rate." "make sure your hand is warm, relaxed, and held below the level of the heart." "Sit still and do not move the part of your body where the pulse oximeter is located." "Wait a few seconds until the reading stops changing and displays one steady number." "Fast/racing pulse rate." "Be aware multiple factors can affect the accuracy of a pulse oximeter reading, such as poor circulation, skin pigmentation, skin thickness, skin temperature, current tobacco use, and use of fingernail polish."
- **C09** NASA OCHMO-TB-019 Orthostatic Intolerance: "crewmember adapts to the microgravity environment (<2 days) by reducing circulating (intravascular) fluid volume by around 15%" "Studies also confirm autonomic dysfunction, including decreased baroreflex sensitivity"
- **C10** NHLBI, Heart Attack: Symptoms: "Rapid or irregular heartbeat"
- **C15** NASA OCHMO-TB-041 Sleep Accommodations: "astronauts sleep an average of about six hours while in space"
- **C29** British Heart Foundation, Checking your pulse: "If your pulse feels irregular, you should check for a full 60 seconds."
- **C30** British Heart Foundation, Your heart rate: "You may be able to tell if you have a normal or irregular heart rate by checking your pulse"

<a id="icl3"></a>

## ICL3: Acute coronary syndrome

**Unreviewed draft: needs clinician review before live guidance.**

| Field | Detail |
|---|---|
| NASA membership | ICL3 `ACUTE CORONARY SYNDROME`, active in ICL 1.0 v3.2. [HRP-48036 Rev B, page 105](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf#page=105) (S01). Establishes membership only |
| Why it is on the demo path | The serious alternative that the fixed urgent questions must catch before the model runs (Act 3), and the flow **Someone needs help** opens when a red flag is answered yes. NHLBI lists a rapid or irregular heartbeat among heart attack symptoms (C10), so a racing heart alone cannot set this aside |
| Body map | **heart**: reported chest symptoms as hollow rings; "ECG" and "troponin: not available here" as open dashed rings. **lungs**: breathlessness. **neck**, **shoulder**, **back**, **abdomen**: pain that spreads to the arms, back, shoulders, neck, jaw or above the belly button (C10). The figure has no arm or jaw region, so shoulder and neck stand in for them |
| Key reading | Blood pressure, for the record only (card below) |

### What the crew member reports

Chest pain, heaviness or discomfort; pain or discomfort in one or both arms, the back, shoulders, neck, jaw or above the belly button; shortness of breath at rest or with little activity; sweating a lot for no reason; nausea and vomiting; light-headedness or sudden dizziness; a rapid or irregular heartbeat (C10). Symptoms can come and go over several hours, and a heart attack can come with mild symptoms or none (C10).

### Intake questions

1. **Fixed urgent questions, no model:** Do you have chest pain, heaviness or discomfort now? Pain or discomfort in an arm, your back, shoulders, neck, jaw or above your belly button? Are you short of breath at rest? Are you sweating a lot for no reason, sick to your stomach, light-headed or suddenly dizzy? (C10)
2. "Not sure" is treated as yes for routing. NHLBI says to get emergency care even when unsure (C10).
3. Only after a negative screen, and only if the assessment still lists this entry: when did it start, did it come on with effort, has it happened before, is there known heart disease? These answers feed the record; they never delay the urgent route.

### Urgent red flags

| Red flag | Source |
|---|---|
| Chest pain, heaviness or discomfort | C10 |
| Pain or discomfort in the arms, back, shoulders, neck, jaw or above the belly button | C10 |
| Shortness of breath at rest or with little activity | C10 |
| Sweating a lot for no reason; nausea or vomiting; light-headedness or sudden dizziness | C10 |
| A racing heart with chest pain, shortness of breath or feeling faint | C01 |
| Collapse or no response: a heart attack can cause sudden cardiac arrest (ICL106 route) | C11 |

### What this kit can measure

| Kit reading | What it tells us | What it does not tell us | Source |
|---|---|---|---|
| Camera or oximeter pulse | Pulse rate; a rapid or irregular heartbeat is one listed heart attack symptom | Whether this is a heart attack. Mild or absent symptoms are possible in a heart attack, and the tests that decide it are ECG and troponin | C10, S04 |
| Kit oximeter SpO2 | An oxygen estimate. FDA lists chest pain or tightness among signs of low oxygen | Whether the heart muscle is injured. That is the troponin test | C08, S04 |
| Blood pressure cuff | A number with a time stamp for the record and the medical officer | Whether this is a heart attack | S04 |
| Camera breathing | Breathing rate | The cause of breathlessness | PRD section 5 |
| Thermometer, wrist band | Context only | Nothing in the sources read for this entry | None |

### What would confirm or exclude it

Do not wait for any of these, or for another camera reading, before the urgent route (PRD section 7).

| Test | Equipment and person | Available here? | Source |
|---|---|---|---|
| ECG, the most common first test | ECG device; clinician | No | S04 |
| Troponin blood test, repeated over time | Laboratory assay; clinician | No | S04 |
| Heart imaging (chest X-ray, CT) and stress testing | Imaging equipment; clinician | No | S04 |

### What the assistant may say

The model does not speak on the urgent path; the fixed screen does. After the red-flag questions come back negative it may say:

- "You answered no to chest pain, breathlessness and fainting. If any of them starts, press Someone needs help. It works with the model switched off."
- "A blood pressure reading is in your record with its time. It does not decide how urgent this is; your symptoms do."

### What the assistant must never say

- "This is not your heart", "not a heart attack", or that a normal pulse, oxygen or pressure reading rules it out.
- "Wait and repeat the reading first."
- Any medicine or dose. NHLBI's page mentions a medicine; the product gives no medication instructions (PRD section 9).
- A risk percentage or likelihood score.

### Measurement card: Blood pressure

**How to measure it here.** Use the kit cuff exactly as its own instructions describe; the device is not yet chosen in this package, and no other technique guidance has been sourced here. The record keeps the value, time, arm, position and device (input dictionary).

**Why it is worth measuring.** Providers measure blood pressure as part of the heart examination (C05), and low blood pressure is a listed symptom of atrial fibrillation (C04). In a suspected heart attack it is a number for the record; the symptoms decide the urgency (C10).

**What spaceflight does to it.** Microgravity shifts body fluids, circulating volume falls by around 15% within about 2 days, and baroreflex sensitivity decreases (C09). On return to gravity, orthostatic intolerance is caused by an inability to maintain arterial blood pressure and blood flow to the brain (C09).

**When to tell a crewmate or the ground.** Any heart attack symptom opens **Someone needs help** at the chest pain flow, whatever the number (C10). Numeric attention bands for blood pressure: threshold to be set by clinical review.

**What you can do.** With heart attack symptoms, press **Someone needs help** now: "Every minute matters." (C10). Otherwise, keep the reading with its time so the medical officer, and later the ground, can compare it with the person's baseline.

### Sources and quotes

- **S04** NHLBI, Heart Attack: Diagnosis: "An electrocardiogram (EKG) is the most common initial test and may be given within minutes of your arrival at the hospital." "you may get a troponin test to measure the amount of a protein called troponin in your blood." "Blood tests often are repeated to check for changes over time." "Imaging tests, such as a chest X-ray or computed tomography (CT) help your doctor check whether your heart is working properly."
- **C10** NHLBI, Heart Attack: Symptoms: "Chest pain, heaviness, or discomfort in the center or left side of the chest (this is the most common symptom)" "Pain or discomfort in one or both arms, your back, shoulders, neck, jaw, or above your belly button" "Shortness of breath when resting or doing a little bit of physical activity" "Sweating a lot for no reason" "Nausea (feeling sick to the stomach) and vomiting" "Light-headedness or sudden dizziness" "Rapid or irregular heartbeat" "Symptoms also may come and go over several hours." "It is also possible to have mild symptoms or even no symptoms at all and still have a heart attack." "Call 9-1-1 for emergency medical care, even if you are not sure that you’re having a heart attack." "Every minute matters."
- **C11** NHLBI, What Is a Heart Attack?: "A heart attack is a life-threatening medical emergency that requires immediate treatment." "A heart attack can cause sudden cardiac arrest."
- **C01** NHS, Heart palpitations: "You currently have heart palpitations and they do not go away or you also have any of these symptoms:"
- **C08** FDA, Pulse Oximeter Basics: "Chest pain or tightness." "Pulse oximeter readings should be considered in context with other information, including signs and symptoms of low oxygen."
- **C04** NHLBI, Atrial Fibrillation: Symptoms: "Low blood pressure"
- **C05** NHLBI, Atrial Fibrillation: Diagnosis: "Measure your blood pressure, height, and weight"
- **C09** NASA OCHMO-TB-019 Orthostatic Intolerance: "When a crewmember enters a microgravity environment, a large number of physiological changes occur, including a significant shift in bodily fluids." "caused by an inability to maintain arterial blood pressure and cerebral perfusion."

<a id="icl14"></a>

## ICL14: Anxiety

**Unreviewed draft: needs clinician review before live guidance.**

| Field | Detail |
|---|---|
| NASA membership | ICL14 `BHP - ANXIETY`, active in ICL 1.0 v3.2. [HRP-48036 Rev B, page 108](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf#page=108) (S01). Establishes membership only |
| Why it is on the demo path | An alternative explanation that Act 5 may list under "might explain", and only after the red-flag questions come back negative. Act 2's mood below usual and short sleep are its context. It cuts both ways: panic can feel like a heart attack (C12), and anxiety is itself a listed arrhythmia symptom (C02), so anxiety never closes the question of a heart cause |
| Body map | **head**: mood (hollow ring). **heart**: the reported racing (hollow ring until a reading lands). **lungs**: breathing rate. **hand**: tingling or numb hands, if reported |
| Key reading | Mood (card below) |

### What the crew member reports

During a panic attack: a pounding or racing heart, sweating, chills, trembling, difficulty breathing, weakness or dizziness, tingling or numb hands, chest pain, stomach pain or nausea (C12). With generalized anxiety: worry that is hard to control, restlessness, trouble falling or staying asleep or fatigue, sweating, light-headedness or feeling out of breath (C13). In flight, stress and anxiety are among the common behavioral health concerns (C14). Depression can also show up as a racing heart (S10).

### Intake questions

After the red-flag questions. Private; every question can be declined; the journal is never read (PRD R20).

1. Did it start suddenly, as a wave of fear or discomfort, or build gradually? (C12 describes a panic attack as a sudden wave of fear or discomfort.)
2. How long did it last, and is it easing now? (C12)
3. Has this happened before? (Recurrence matters to a clinician, C12. The app does not diagnose.)
4. How have worry, mood and sleep been over the last few days? (C13; the daily check already holds mood and sleep.)
5. Is anything weighing on you: work, the crew, home? (C14 lists family and life stressors and workload among contributing factors.)
6. Would you like a private conversation with the ground team? (C14: private psychological conferences.)

### Urgent red flags

| Red flag | Source |
|---|---|
| Chest pain, shortness of breath or feeling faint. The cardiac urgent questions always run first, because panic can feel like a heart attack | C01, C12 |
| Thoughts of death or suicide | S10 |

### What this kit can measure

| Kit reading | What it tells us | What it does not tell us | Source |
|---|---|---|---|
| Camera pulse, oximeter pulse | Rate, compared with the person's baseline | Whether the cause is anxiety, a rhythm problem or another physical problem. A clinician examines the person to rule out a physical cause | C12 |
| Camera breathing | Breathing rate | Which cause: difficulty breathing appears in both the panic list and the heart attack list | C12, C10 |
| Daily check: mood, stress, fatigue (0 to 10, self-report) | The person's own rating against their usual | A diagnosis; a slider cannot diagnose or exclude psychiatric illness (catalog limitation). It never earns or costs a reward ([PITCH.md](PITCH.md)) | Package |
| Wrist band | Sleep estimate. Trouble sleeping is a listed anxiety symptom, and sleep loss can lead to anxiety | A diagnosis | C13, C15 |
| Blood pressure cuff, thermometer | Context only | Nothing in the sources read for this entry | None |

### What would confirm or exclude it

| Test | Equipment and person | Available here? | Source |
|---|---|---|---|
| Clinical interview with a health care provider or mental health professional | Trained clinician | Not on board. The ground's private conference is simulated in the ground console | C12, C14 |
| Physical examination to make sure a physical problem is not the cause | Trained examiner | No | C12 |
| ECG, to exclude a rhythm cause of the racing heart | ECG device | No | S03 |
| Panic disorder requires recurrent, unexpected attacks, judged by a clinician | Clinician | No, and never the app's call | C12 |
| A validated questionnaire | Exact version, scoring and use rights (assessment family text) | Not selected yet | Package |

### What the assistant may say

- "Stress and anxiety are common causes of a noticeable heartbeat (C01). Your mood today is below your usual, and your sleep was short. These may be part of the picture. A heart cause has not been ruled out, because the rhythm needs an ECG."
- "Would you like to talk privately with the ground team?"

### What the assistant must never say

- "This is just anxiety", "it is in your head", or anything that closes the heart question.
- A diagnosis of panic disorder or generalized anxiety disorder. An isolated panic attack is not a mental disorder (C12).
- Any medicine or dose. C12 lists medicines; the product names none.
- Anything that shares mood or journal content with crewmates, or ties mood to the streak.

### Measurement card: Mood

**How to measure it here.** In the daily check, rate mood from 0 to 10; stress and fatigue are optional, and "prefer not to say" still completes the check ([input dictionary](input-dictionary.csv); PITCH.md).

**Why it is worth measuring.** Stress, depression and anxiety are among the common behavioral health concerns in spaceflight (C14), and sleep loss can lead to depressive symptoms and anxiety (C15).

**What spaceflight does to it.** Isolated, confined and extreme environments affect crew behavioral health and performance; sleep, workload and carbon dioxide exposure are listed contributing factors (C14).

**When to tell a crewmate or the ground.** Thoughts of death or suicide: the urgent route at once (S10). Low mood that persists: the medical officer's review on board, and the log for the ground, if the person agrees to share it; how many days and how far below usual is a threshold to be set by clinical review. NIMH's two-week period for depression is a clinician's diagnostic criterion, not an app alert (S10).

**What you can do.** Ask for a private conversation with the ground (C14). If symptoms persist or do not go away, talk to a health care provider (S10). Protect the next sleep period ([sleep card](#icl95)).

### Sources and quotes

- **C12** NIMH, Panic Disorder: What You Need to Know: "Panic attacks often include physical symptoms that might feel like a heart attack, such as trembling or tingling in the body or a rapid heart rate." "Pounding or racing heart" "Tingly or numb hands" "a sudden wave of fear or discomfort or a sense of losing control even when there is no clear danger or trigger." "A panic attack can last from a few minutes to an hour or sometimes longer." "After discussing your history, the provider may conduct a physical exam to ensure that an unrelated physical problem is not causing your symptoms." "To be diagnosed with panic disorder, a person must experience recurrent, unexpected panic attacks." "An isolated panic attack is not a mental disorder."
- **C13** NIMH, Generalized Anxiety Disorder: What You Need to Know: "Have trouble falling asleep or staying asleep, or feel fatigued" "Sweat a lot, feel lightheaded, or feel out of breath" "Anxiety is the body's reaction to stress and can occur even if there is no current threat."
- **C14** NASA OCHMO-TB-016 Behavioral Health and Performance: "Crew behavioral health and performance are affected by missions in isolated, confined, and extreme (ICE) environments." "Common behavioral health conditions or concerns in the spaceflight environment may include stress, depression, anxiety, relationship problems, and grief." "Family and life stressors" "Workload" "Carbon dioxide exposure" "Private psychological conferences (PPCs) are currently provided"
- **S10** NIMH, Depression: "For some people, symptoms manifest as physical problems (for example, a racing heart, tightened chest, chronic headaches, or digestive issues)." "Thoughts of death or suicide or suicide attempts" "If you show signs or symptoms of depression and they persist or do not go away, talk to a health care provider."
- **C01** NHS, Heart palpitations: "stress and anxiety"
- **C15** NASA OCHMO-TB-041 Sleep Accommodations: "Sleep loss can also be a behavioral stressor and lead to depressive symptoms, anxiety, and other adverse behaviors."
- **C10** NHLBI, Heart Attack: Symptoms: "Shortness of breath when resting or doing a little bit of physical activity"
- **C02** NHLBI, Arrhythmias: Symptoms lists "Anxiety" among other symptoms of arrhythmias.
- **S03** NHLBI, Arrhythmias: Diagnosis: "An electrocardiogram (EKG or ECG) is the most common test for diagnosing arrhythmias."

<a id="icl95"></a>

## ICL95: Space adaptation insomnia

**Unreviewed draft: needs clinician review before live guidance.**

| Field | Detail |
|---|---|
| NASA membership | ICL95 `SPACE ADAPTATION - INSOMNIA`, active in ICL 1.0 v3.2. [HRP-48036 Rev B, page 132](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf#page=132) (S01). Establishes membership only |
| Why it is on the demo path | Act 2 opens with short sleep: the band's estimate, confirmed by Mara. Lack of sleep is a common cause of palpitations (C01), so this entry feeds Act 5's "might explain" line |
| Body map | **wrist**: the confirmed sleep estimate as a filled dot, with the band's own light. **head**: a reported sleep complaint (hollow ring) |
| Key reading | Sleep (card below) |

### What the crew member reports

Lying awake a long time before falling asleep, waking often, waking too early and not getting back to sleep, feeling unrested and sleepy in the day, trouble focusing; insomnia can bring anxiety, low mood or irritability (C18). Short-term insomnia can come from stress or changes in schedule or environment (C17), which is what a mission is.

### Intake questions

1. Confirm or correct the band's estimate. The original estimate stays on record (PRD R10).
2. Trouble falling asleep, staying asleep, or waking too early? (C18)
3. How many nights this week? A clinician's definition of chronic insomnia counts nights per week over months (C17); the app records, it does not diagnose.
4. The sleep station: noise, light, temperature, comfort, a shifted schedule? (C15, C23)
5. Caffeine timing, exercise or large meals close to sleep, screens before bed? (C23, C19)
6. Loud snoring, or waking gasping or out of breath? (C19)
7. Medicines or sleep aids taken: recorded only, with no advice about them (C19). Sleep-promoting drug use was common in flight (C16).
8. Daytime sleepiness, mood and worry (C18).

### Urgent red flags

| Red flag | Route | Source |
|---|---|---|
| Thoughts of death or suicide | Urgent route at once | S10, C20 |
| Nodding off during a task (possible microsleep) | Tell a crewmate and stop any critical task now; the formal response route is to be set by clinical and operations review | C20 |
| Waking gasping or out of breath | Into the medical officer's review on board; whether it should open Someone needs help is to be set by clinical review | C19 |

### What this kit can measure

| Kit reading | What it tells us | What it does not tell us | Source |
|---|---|---|---|
| Wrist band | Estimated sleep timing and duration, proposed for the person to confirm | Wake time and sleep stages reliably. In a laboratory comparison, consumer trackers had low to medium specificity for wake, mixed stage agreement, and did worse on disrupted nights. AASM's actigraphy recommendations apply to FDA-approved devices; our band is not assumed to be one | C22, C21 |
| Daily check: sleep hours and quality | The person's own account; a sleep diary is part of a clinician's assessment | A diagnosis | C19 |
| Reaction test | Alertness after the night ([card](#icl18)) | A sleep disorder | C24 |
| Camera, oximeter, cuff, thermometer | Nothing for this entry | Nothing in the sources read for this entry | None |

### What would confirm or exclude it

| Test | Equipment and person | Available here? | Source |
|---|---|---|---|
| Sleep history and a sleep diary for 1 to 2 weeks | Clinician | Partly. The daily check and band build a diary-like record; the clinician is on the ground | C19 |
| Actigraphy, a wrist sensor worn for 3 to 14 days | Clinical actigraph | No. The band is an experimental estimate | C19, C21 |
| Sleep study for other sleep problems | Sleep laboratory equipment | No | C19 |
| Blood tests for thyroid or other conditions | Laboratory | No | C19 |
| NASA fatigue management screening, including an insomnia questionnaire | Flight surgeon or the Fatigue Management Service | No, a ground service | C23 |

### What the assistant may say

- "Your band estimated [duration] of sleep and you confirmed it. That is shorter than your usual. Short sleep is common in flight (C15), and it slows reaction time (C20)."
- "I cannot diagnose insomnia. A sleep diary over 1 to 2 weeks would help a clinician (C19)."

### What the assistant must never say

- "You have insomnia", or the name of any sleep disorder.
- Any sleep medicine or dose. NASA's circadian brief lists approved medicines (C23); the product never repeats them.
- Sleep stages from the band presented as fact.
- That the person is fit or unfit for duty (PRD section 9).
- Anything that makes a short night cost the streak or a patch (PITCH.md).

### Measurement card: Sleep

**How to measure it here.** Wear the band overnight. In the morning the check proposes the band's estimate and you confirm or correct it; the original estimate and its scoring version stay on record (PRD R10). Trackers like this tend to miss wake time, most of all on disrupted nights (C22).

**Why it is worth measuring.** Top performance depends on enough daily sleep of the right quality (C15). People who are short of sleep take longer to finish tasks, react more slowly and make more mistakes (C20).

**What spaceflight does to it.** Astronauts sleep about six hours a night in space (C15); ISS crew measured by wrist actigraphy averaged about 6.09 hours (C16). Noise, light, temperature, elevated CO2 and shifted schedules disrupt sleep (C15, C23).

**When to tell a crewmate or the ground.** Short sleep against the personal baseline over several nights: threshold to be set by clinical review. Nodding off during a task: tell a crewmate now (C20). Thoughts of death or suicide: the urgent route (S10).

**What you can do.** Keep the sleep space cool, dark and quiet; no caffeine in the 6 to 12 hours before sleep, depending on the person; in the 2 hours before sleep avoid vigorous exercise and large or spicy meals, and avoid bright or blue-enriched light (C23).

### Sources and quotes

- **C15** NASA OCHMO-TB-041 Sleep Accommodations: "Top tier performance depends on the ability to acquire an adequate quantity of daily sleep and the appropriate sleep quality." "astronauts sleep an average of about six hours while in space" "elevated CO2 levels"
- **C16** Barger and others, Lancet Neurology 2014 (abstract): "We assessed sleep-wake timing objectively via wrist actigraphy" "Crew members on ISS missions obtained significantly less sleep during spaceflight (6·09 h [0·67])" "Despite chronic sleep curtailment, use of sleep-promoting drugs was pervasive during spaceflight."
- **C17** NHLBI, What Is Insomnia?: "Short-term insomnia may be caused by stress or changes in your schedule or environment." "Chronic (long-term) insomnia occurs 3 or more nights a week, lasts more than 3 months, and cannot be fully explained by another health problem."
- **C18** NHLBI, Insomnia: Symptoms: "You may lie awake for a long time before you fall asleep." "Waking up too early in the morning and not getting back to sleep is also common." "Insomnia can cause you to feel anxious, depressed, or easily annoyed."
- **C19** NHLBI, Insomnia: Diagnosis: "It may be helpful to keep a sleep diary for 1 to 2 weeks before seeing your doctor." "Whether you use electronic devices or watch TV before bed" "Whether you snore loudly and often or wake up gasping or feeling out of breath" "Actigraphy looks at your periods of rest and activity and measures how well you sleep." "This requires you to wear a small motion sensor on your wrist for 3 to 14 days." "A sleep study looks for other sleep problems, such as circadian rhythm disorders, sleep apnea, and narcolepsy." "Blood tests check for thyroid problems or other medical conditions that can affect sleep."
- **C20** NHLBI, Sleep Deprivation and Deficiency: How Sleep Affects Your Health: "They take longer to finish tasks, have a slower reaction time, and make more mistakes." "Lack of sleep also may lead to microsleep." "Sleep deficiency has also been linked to depression, suicide, and risk-taking behavior."
- **C21** Smith and others, AASM guideline 2018 (abstract): "We suggest that clinicians use actigraphy to estimate sleep parameters in adult patients with insomnia disorder. (Conditional)." "only apply to the use of FDA-approved devices"
- **C22** Chinoy and others, Sleep 2021 (abstract): "specificity was low-to-medium (0.18-0.54), sleep stage comparisons were mixed, and devices tended to perform worse on nights with poorer/disrupted sleep."
- **C23** NASA OCHMO-MTB-009 Circadian Desynchrony: "Ensure a Cool/Dark/Quiet Sleep Space (no TV/computers)" "No caffeine 6-12 hrs prior to sleep (individual dependent)" "Within 2 hours of sleep, avoid:" "Large/spicy meals" "Avoidance of bright light/blue enriched light" "Screening for Insomnia Questionnaire"
- **C24** Basner and Dinges, Sleep 2011 (abstract): "The psychomotor vigilance test (PVT) is among the most widely used measures of behavioral alertness"
- **C01** NHS, Heart palpitations: "lack of sleep"
- **S10** NIMH, Depression: "Thoughts of death or suicide or suicide attempts"

<a id="icl18"></a>

## ICL18: Sleep disturbance (behavioral health)

**Unreviewed draft: needs clinician review before live guidance.**

| Field | Detail |
|---|---|
| NASA membership | ICL18 `BHP - SLEEP DISTURBANCE`, active in ICL 1.0 v3.2. [HRP-48036 Rev B, page 111](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf#page=111) (S01). Establishes membership only |
| Why it is on the demo path | Act 2 puts short sleep beside a slower reaction test and mood below usual. NASA lists this behavioral health entry separately from ICL95; the definitions behind that split are in NASA's condition documentation, which this pass did not read. In the product both share one sleep intake; this entry adds the daytime view: reaction, mood and workload |
| Body map | **eyes**: reaction time. **head**: mood. **wrist**: the band's sleep estimate |
| Key reading | Reaction test (card below) |

### What the crew member reports

Slower reactions, more mistakes, tasks taking longer (C20); feeling anxious, low or easily annoyed (C18); a change in sleep alongside low mood, since difficulty sleeping, waking too early or oversleeping are listed signs of depression (S10).

### Intake questions

The sleep questions of [ICL95](#icl95) are asked once and shared. Then:

1. Have you noticed slower reactions or more mistakes? (C20)
2. How has your mood been alongside the change in sleep? (S10, C18)
3. Is workload or the schedule pushing sleep later or shorter? (C14 lists sleep and workload as contributing factors.)
4. Would you like a private conversation with the ground team? (C14)

### Urgent red flags

| Red flag | Route | Source |
|---|---|---|
| Thoughts of death or suicide | Urgent route at once | S10, C20 |
| Nodding off during a task | Tell a crewmate and stop any critical task now; formal route to be set by clinical and operations review | C20 |

### What this kit can measure

| Kit reading | What it tells us | What it does not tell us | Source |
|---|---|---|---|
| Reaction test | Response speed and lapses against the person's own baseline; the PVT is a widely used alertness measure and is sensitive to sleep loss | A sleep disorder. Mean or median reaction time alone is a weak primary measure, and a test shorter than the standard 10 minutes still needs confirmation | C24 |
| Wrist band and daily check sleep | As in [ICL95](#icl95) | As in ICL95 | C22 |
| Daily check mood | As in [ICL14](#icl14) | As in ICL14 | Package |
| Cabin CO2 card (Replay) | Context only | A cause for a slow test. Recent studies found CO2 at levels currently maintained on board did not reduce cognition in astronaut-like subjects | C25 |
| Camera, oximeter, cuff, thermometer | Nothing for this entry | Nothing in the sources read for this entry | None |

### What would confirm or exclude it

| Test | Equipment and person | Available here? | Source |
|---|---|---|---|
| Behavioral health interview | Clinician; the ground's private conference | Not on board; simulated in the ground console | C14, S10 |
| Sleep assessment: history, diary, actigraphy, sleep study | As in [ICL95](#icl95) | As in ICL95 | C19 |
| Blood tests for medical causes such as thyroid problems | Laboratory | No | C19, S10 |

### What the assistant may say

- "Your reaction test this morning was slower than your usual, after a short night. Sleep loss slows reaction time (C20). This does not diagnose a sleep disorder, and it does not say whether you can do a task."

### What the assistant must never say

- The name of any sleep or mood disorder as a diagnosis.
- "Impaired", "unfit" or any fitness-for-duty statement (PRD section 9).
- A comparison with crewmates or a ranking of any kind (PITCH.md).
- A reward or penalty tied to the score, or any medicine.

### Measurement card: Reaction test

**How to measure it here.** A timed tap test on the laptop; its length is shown before it starts (PRD section 4.2). It reports response speed and lapses, not only the mean, because mean and median are weak primary measures of alertness (C24). Our test is shorter than the standard 10-minute PVT, and shorter versions still need confirmation (C24).

**Why it is worth measuring.** The PVT is among the most widely used measures of behavioral alertness, and its speed and lapse measures are sensitive to sleep loss (C24).

**What spaceflight does to it.** Poor sleep affects sustained attention (C15), and circadian desynchrony is associated with slowed reaction time (C23). At the CO2 levels currently maintained on board, recent studies found no drop in cognition in astronaut-like subjects (C25).

**When to tell a crewmate or the ground.** A slowdown against the personal baseline: threshold to be set by clinical review. In the sources read, a slow result alone is never urgent; nodding off during a task is (tell a crewmate now, C20).

**What you can do.** Protect the next sleep period ([sleep card](#icl95), C23). If you feel fatigued, keep up light exposure through the day (C23).

### Sources and quotes

- **C24** Basner and Dinges, Sleep 2011 (abstract): "The psychomotor vigilance test (PVT) is among the most widely used measures of behavioral alertness" "PVT mean and median metrics, which are among the most widely used outcomes, should be avoided as primary measures of alertness." "some shorter-duration PVT versions may be sensitive to sleep loss, depending on the outcome variable selected, although this will need to be confirmed"
- **C20** NHLBI, Sleep Deprivation and Deficiency: How Sleep Affects Your Health: "They take longer to finish tasks, have a slower reaction time, and make more mistakes." "Lack of sleep also may lead to microsleep." "Sleep deficiency has also been linked to depression, suicide, and risk-taking behavior."
- **C15** NASA OCHMO-TB-041 Sleep Accommodations: "Poor sleep can also affect sustained attention."
- **C23** NASA OCHMO-MTB-009 Circadian Desynchrony: "associated with issues such as cognitive impairment (e.g., inattention; distraction; slowed reaction time; poor decision making), metabolic disturbance, and sleep disruption." "Continue light exposure throughout day if fatigued"
- **C25** NASA OCHMO-TB-004 Carbon Dioxide: "the CO2 exposure at levels currently maintained onboard spacecraft and submarines does not reduce cognition and performance in astronaut-like subjects or submarine officers."
- **C14** NASA OCHMO-TB-016 Behavioral Health and Performance: "Workload" "Private psychological conferences (PPCs) are currently provided"
- **C18** NHLBI, Insomnia: Symptoms: "Insomnia can cause you to feel anxious, depressed, or easily annoyed."
- **S10** NIMH, Depression: "Difficulty sleeping, waking too early in the morning, or oversleeping" "Thoughts of death or suicide or suicide attempts" "Certain medications and medical conditions, such as viruses or thyroid disorders, can cause the same symptoms as depression."
- **C19** NHLBI, Insomnia: Diagnosis: "Blood tests check for thyroid problems or other medical conditions that can affect sleep."
- **C22** Chinoy and others, Sleep 2021 (abstract): "specificity was low-to-medium (0.18-0.54), sleep stage comparisons were mixed, and devices tended to perform worse on nights with poorer/disrupted sleep."

<a id="icl64"></a>

## ICL64: Headache from carbon dioxide

**Unreviewed draft: needs clinician review before live guidance.**

| Field | Detail |
|---|---|
| NASA membership | ICL64 `HEADACHE - CO2 INDUCED`, active in ICL 1.0 v3.2. [HRP-48036 Rev B, page 124](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf#page=124) (S01). Establishes membership only |
| Why it is on the demo path | Act 2's cabin air card replays a week of rising CO2 from NASA's Rodent Research-1 ISS cabin telemetry (2014), reaching a one-hour mean of 4,606 ppm (3.50 mmHg at an assumed 760 mmHg), above NASA's 3 mmHg one-hour limit (PRD section 1.4; C25). It is labeled Replay: another mission's cabin, not Mara's exposure. Mara reports no headache in the story. This entry fixes what the card may and may not be used for when someone does |
| Body map | **head**: a reported headache (hollow ring). **lungs**: breathing rate. Cabin air is not about this body and lights no region |
| Key reading | Cabin CO2, Replay (card below) |

### What the crew member reports

Headache. NASA lists headache, breathlessness and fatigue among symptoms of CO2 build-up, and notes that headaches and vision changes are the most common symptoms in spaceflight (C25). Anecdotal flight reports linked headaches to CO2 levels, with fatigue and malaise at higher levels (C25).

### Intake questions

1. **Fixed urgent questions, no model:** the headache red flags below (C27).
2. When did it start, and did it start suddenly? (C27)
3. Where were you before it started, for example the sleep station or a module? Fixed sensors miss local CO2 pockets (C25).
4. Any change in vision? (C27; C25 pairs headaches with vision changes; see [ICL100](#icl100).)
5. Vomiting, or worse with coughing, sneezing, bending down or exercise? (C27)
6. Sleep, fluids and meals today? NHS lists dehydration and irregular meals among common causes (C27).
7. A headache diary entry: time, place, what helped (C27).

### Urgent red flags

A high CO2 value never explains away a red flag.

| Red flag | Route | Source |
|---|---|---|
| A seizure | Someone needs help | C27 |
| Numbness or weakness in the body or face | Someone needs help | C27 |
| Started suddenly and is extremely painful | Someone needs help | C27 |
| A head injury within the last 3 months | Someone needs help | C27 |
| Difficulty speaking, balancing, walking or remembering | Someone needs help | C27 |
| Drowsy or confused | Someone needs help | C27 |
| Loss of vision | Someone needs help | C27 |
| A very high temperature, a stiff neck, or bright light bothering the eyes | Someone needs help | C27 |
| Headache with vision or eye problems; worse with coughing, sneezing, bending or exercise; headache with vomiting | Prompt review by the medical officer on board; into the log for the ground | C27 |

### What this kit can measure

| Kit reading | What it tells us | What it does not tell us | Source |
|---|---|---|---|
| Cabin CO2 card (Replay in this build) | The habitat's measured ppCO2 over time, against the 3 mmHg one-hour limit | The person's own exposure: fixed sensors miss local fluctuations. Nor the cause: fluid shift in microgravity produces headaches similar to CO2 over-exposure, so timing can suggest an association, never prove one | C25 |
| Kit oximeter | Oxygen saturation and pulse rate | Anything about CO2. An oximeter estimates oxygen; a normal SpO2 is not a CO2 reading | C08 |
| Camera breathing | Breathing rate; breathlessness is a listed CO2 symptom | The CO2 level | C25 |
| Thermometer | Temperature; a very high temperature with headache is a red flag | The cause of the headache | C27 |
| Wrist band | Sleep; elevated CO2 is a listed sleep disruptor | Exposure | C15 |
| Camera pulse, blood pressure cuff | Context only | Nothing in the sources read for this entry | None |

### What would confirm or exclude it

| Test | Equipment and person | Available here? | Source |
|---|---|---|---|
| CO2 measured near the person, aligned in time with the headache | A live vehicle sensor or a portable CO2 monitor where the person was | No. This build has no live cabin feed (PRD section 1.4) | C25 |
| Headache history and examination; further tests when red flags are present | Trained examiner; clinician | No | C27 |
| Weighing other causes: fluid shift, dehydration, sleep loss, eye strain | Clinician | No | C25, C27 |

### What the assistant may say

- "The replayed cabin record (Replay, another mission, 2014) rose to a one-hour mean of 3.50 mmHg, above today's 3 mmHg limit. It is not your exposure."
- If a headache is reported: "On the ISS, each 1 mmHg rise in CO2 doubled the odds of a headache report (C26). A similar headache can also come from the fluid shift of microgravity (C25). The cabin data cannot prove the cause."

### What the assistant must never say

- "Your headache is caused by CO2."
- "CO2 is normal, so the headache is harmless."
- Anything that presents Replay as live data or as the person's exposure.
- Any painkiller or dose. The NHS page names medicines; the product gives none (PRD section 9).
- Anything that sets a red flag aside because CO2 is high.

### Measurement card: Cabin CO2

**How to measure it here.** This build has no live cabin sensor. The card replays NASA OSDR Rodent Research-1 ISS telemetry from 2014, labeled Replay with its original dates (PRD section 1.4). On a vehicle, ppCO2 comes from fixed sensors, which can miss local fluctuations (C25).

**Why it is worth measuring.** CO2 build-up can cause headache, breathlessness and fatigue (C25), and on the ISS higher CO2 went with more headache reports (C26).

**What spaceflight does to it.** A closed cabin relies on CO2 removal equipment instead of natural removal (C25). Fluid shift produces headaches like those of CO2 over-exposure (C25), and crew appear more susceptible to CO2 in flight (C26).

**When to tell a crewmate or the ground.** The card marks any one-hour mean above 3 mmHg, the NASA-STD-3001 Volume 2 limit [V2 6004] as stated in C25. To keep headache risk below 1%, the 7-day average needs to stay below 2.5 mmHg (C25, C26). These are limits for the cabin, not a diagnosis. A headache with any red flag opens **Someone needs help** whatever the CO2 value (C27).

**What you can do.** Report symptoms with their time. If the average is exceeded over several days, the flight control team finds and repairs degraded scrubbing or adds supplemental scrubbing (C25). Air has to keep moving around the face during sleep (C15).

### Sources and quotes

- **C25** NASA OCHMO-TB-004 Carbon Dioxide: "limits the average 1-hour CO2 partial pressure (ppCO2) in the habitable volume to no more than 3 mmHg." "To keep the risk of headache below 1%, average 7-day CO2 needs to be maintained below 2.5 mmHg (current ISS range: 1 to 9 mmHg; 3001 standard requires <3 mmHg)." "cause symptoms such as headache, dyspnea, fatigue, and in extreme cases, death." "The most common symptoms experienced during spaceflight are headaches and vision changes" "Headaches at levels between 2.8-4.5 mmHg; worsening with increasing levels of CO2 accompanied by fatigue and malaise flushing." "Fluid shift due to decreased venous and lymphatic drainage in microgravity largely contributes to symptoms similar to CO2 over-exposure, including headaches." "local fluctuations of CO2 that are not measured by fixed sensors" "relying on CO2 removal equipment" "If the average ppCO2 is exceeded over several days for unknown reasons, the Flight Control Team will take corrective actions to either identify, diagnose, and repair degraded CO2 scrubbing equipment or activate supplemental CO2 scrubbing equipment as needed."
- **C26** Law and others, J Occup Environ Med 2014 (abstract): "For each 1-mm Hg increase in CO(2), the odds of a crew member reporting a headache doubled." "To keep the risk of headache below 1%, average 7-day CO(2) would need to be maintained below 2.5 mm Hg" "results suggest an increased susceptibility to physiological effects of CO(2) in-flight."
- **C27** NHS, Headaches: "has had a seizure (fit)" "has numbness or weakness in the body or face" "it started suddenly and is extremely painful" "has had a head injury within the last 3 months" "are finding it difficult to speak, balance, walk or remember things" "are drowsy or confused" "has loss of vision" "has a very high temperature, a stiff neck, or bright lights are bothering you" "you or a child has a headache and vision or eye problems" "you or a child has a headache triggered or made worse by coughing, sneezing, bending down or exercising" "you or a child has a headache and is being sick (vomiting)" "not drinking enough fluids (dehydration)" "not eating regular meals" "Keeping a headache diary might help you work out what triggers your headaches."
- **C08** FDA, Pulse Oximeter Basics: "uses light beams to estimate a person’s blood oxygen level (oxygen saturation) and their pulse rate."
- **C15** NASA OCHMO-TB-041 Sleep Accommodations: "elevated CO2 levels" "The air must be cleared continuously to avoid carbon dioxide to stall around astronaut’s faces."
- **S12** NASA SANS technical brief, potential risk factors: "CO2 air pockets"

<a id="icl100"></a>

## ICL100: Spaceflight associated neuro-ocular syndrome (SANS)

**Unreviewed draft: needs clinician review before live guidance.**

| Field | Detail |
|---|---|
| NASA membership | ICL100 `SPACEFLIGHT ASSOCIATED NEURO-OCULAR SYNDROME (SANS)`, active in ICL 1.0 v3.2. [HRP-48036 Rev B, page 133](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf#page=133) (S01). Establishes membership only |
| Why it is on the demo path | It is not in the five acts. It is the clearest case of "needs specialist eye examination": a judge who taps the eyes on the body map sees an open dashed ring, "Not available here". NASA's SANS brief lists CO2 air pockets among risk factors and impaired sleep among potential causes (S12), which ties it to the Act 2 cards |
| Body map | **eyes**: open dashed ring, eye examination not available here; a hollow ring for any reported vision change. **head** |
| Key reading | Eye examination, not available here (card below) |

### What the crew member reports

Blurred near vision or eyestrain from a shift toward farsightedness (S12). Many crew report vision changes: 29% of short-duration and 60% of long-duration crew (S12). Signs can be present without symptoms; 70% of crew show one or more signs (S12), which is why NASA checks on a schedule rather than waiting for complaints.

### Intake questions

1. What changed: near or far vision, one eye or both? (S07: acuity is measured in each eye separately.)
2. Sudden or gradual, and since when? (C28)
3. Eye pain, flashing lights, a moving shadow, double vision? (C28)
4. A headache with it? (C27, C25)
5. Earlier eye findings, current glasses, and mission day; the risk grows with time in space (S12).

### Urgent red flags

| Red flag | Route | Source |
|---|---|---|
| Suddenly cannot see from one or both eyes | Someone needs help | C28 |
| Sudden severe eye pain | Someone needs help | C28 |
| Headache with loss of vision | Someone needs help | C27 |
| Blurred or double vision, flashing lights or shapes, a dark shadow moving across the vision, pain in one or both eyes | Prompt review by the medical officer on board; into the log for the ground | C28 |
| Headache with vision or eye problems | Prompt review by the medical officer on board; into the log for the ground | C27 |

### What this kit can measure

None of the kit examines the eye.

| Kit reading | What it tells us | What it does not tell us | Source |
|---|---|---|---|
| Camera station | Pulse and breathing from the face and chest | Anything about the optic disc, retina or refraction. Face video is not an eye examination (UX-DESIGN.md section 4) | S12 lists the methods that are |
| Oximeter, cuff, thermometer, band | Their own readings | Nothing about SANS in the sources read | None |

### What would confirm or exclude it

The tests NASA uses, from the SANS brief (S12). None is available here; an existing eye examination report can be imported (PRD section 4.4). A printed acuity chart is not in this kit.

| Test | What it shows, as S12 describes it | Available here? | Source |
|---|---|---|---|
| Visual acuity (distance and near) and Amsler grid | Part of NASA's eye testing; acuity is measured in each eye separately | No | S12, S07 |
| Fundoscopy | Gross structural changes in the optic nerve head and retina; detects optic disc edema and choroidal folds | No | S12 |
| OCT | Retina, choroid and optic nerve head anatomy; measures optic disc edema and choroidal folds | No | S12 |
| Ocular ultrasound | Structural changes in and behind the eye; measures globe flattening and optic nerve head protrusion in flight | No | S12 |
| MRI of brain and orbits | Structural changes in the eye; globe flattening before and after flight | No | S12 |
| Tonometry | Listed in NASA's pre- and post-flight eye testing; one crew member designated as SANS specialist is trained in it | No | S12 |

### What the assistant may say

- "Many long-duration crew report vision changes (S12). Checking for SANS needs an eye examination such as fundoscopy or OCT, which this kit does not have. I can record what changed and start the vision self-check, which checks it another way but cannot replace an eye examination. The medical officer reviews it on board, and the log goes to the ground at the next contact."

### What the assistant must never say

- "No SANS", "your eyes look normal", or any eye finding from the camera.
- A SANS diagnosis from a headache or from face video (catalog limitation).
- Any medicine. The SANS brief mentions medicines under study; the product names none.
- Advice to stop wearing prescribed glasses.

### Measurement card: Eye examination

**How to measure it here.** Not available here. This kit has no acuity chart, fundoscope, OCT or ocular ultrasound, which are what NASA uses to monitor SANS (S12). Record the change in your own words, and add an existing eye examination report if there is one.

**Why it is worth measuring.** Optic disc swelling carries the highest risk to vision, and it is found by fundoscopy and OCT (S12).

**What spaceflight does to it.** Altered gravity and fluid shifts are the most likely causes (S12). 70% of crew show one or more signs, and 18% are diagnosed with clinically concerning SANS during long-duration flight (S12).

**When to tell a crewmate or the ground.** Sudden loss of sight or sudden severe eye pain: the urgent route (C28). Any new change in vision: the medical officer's review on board, then the log for the ground at the next contact (C28). Specialist criteria, such as retinal thickness change, need OCT (S12).

**What you can do.** NASA's standard requires the capability to give crew glasses of higher power for SANS-related changes (S12); when to use them is set by the onboard procedure and the medical officer. In-flight eye checks are scheduled, and more can be done when clinically indicated, so a new change is a reason to ask for one (S12).

### Sources and quotes

- **S12** NASA SANS technical brief OCHMO-MTB-001: "SANS etiology is not certain, but altered gravity and fluid shifts resulting in venous congestion and intracranial pressure elevations are the most likely causes." "70% of crew are reported to have one or more signs of SANS and 18% of crewmembers are diagnosed with clinically concerning SANS during long duration spaceflight." "29% of short duration crewmembers and 60% of long duration crew have reported vision degradation." "Vision degradation risk increases the longer you are in space." "hyperopic patients may experience eyestrain and/or blurred near vision" "associated with highest risk to crewmember vision" "Fundoscopy assesses the gross structural changes in the optic nerve head and retina; detects ODE, CF" "OCT is used to assess the retina, choroid, and optic nerve head anatomy; measures ODE, CF" "Ultrasound assesses structural changes within/posterior to eye; measures in-flight globe flattening, optic nerve head protrusion, and other ocular defects" "Eye MRI is used to detect structural changes in the eye" "Globe flattening is measured pre- and post-flight using an MRI, and in-flight using ultrasound (less reliable than terrestrial MRI)." "retinal photography, tonometry, OCT, optical biometry" "1.5 hours to one crew designated as SANS specialist" "Ocular questionnaire, visual acuity, Amsler grid, fundoscopy, 2-D imaging ultrasound, OCT (high-resolution)." "Crew are trained in SANS equipment: OCT, fundoscope, and ocular ultrasound." "Additional testing can be performed as clinically indicated at any point during mission" "Capability to provide deployed crewmembers with optical correction (e.g., glasses) with increased levels of power to mitigate SANS-induced changes in refractive error." "CO2 air pockets" "Impaired sleep which may reduce lymphatic/glymphatic clearance from the brain and eye." "This Medical Technical Brief is derived from NASA-STD-3001 and NASA medical operations and is for reference only."
- **S07** NEI, Statement on Detection of Glaucoma and Adult Vision Screening: "The simplest and most practical screening program should include measurement of visual acuity in each eye separately."
- **C28** NHS, Vision loss: "suddenly cannot see from 1 or both eyes" "suddenly have severe eye pain" "you have changes in your vision, such as blurred vision, double vision, or seeing flashing lights or shapes" "you have a dark shadow moving across your vision" "you have pain in 1 or both eyes"
- **C27** NHS, Headaches: "has loss of vision" "you or a child has a headache and vision or eye problems"
- **C25** NASA OCHMO-TB-004 Carbon Dioxide: "The most common symptoms experienced during spaceflight are headaches and vision changes"

<a id="sources"></a>

## Sources

### Package sources used (already in sources.csv)

| ID | Source | Use on this page |
|---|---|---|
| S01 | NASA, [ExMC Evidence Library Methods, HRP-48036 Rev B](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf) | Membership of all seven entries. Page numbers come from the condition-list extract; the PDF pages were not re-rendered in this pass |
| S03 | NHLBI, [Arrhythmias: Diagnosis](https://www.nhlbi.nih.gov/health/arrhythmias/diagnosis) (captured in sources/S03.json) | ICL10 |
| S04 | NHLBI, [Heart Attack: Diagnosis](https://www.nhlbi.nih.gov/health/heart-attack/diagnosis) (captured in sources/S04.json) | ICL3 |
| S07 | NEI, [Statement on Detection of Glaucoma and Adult Vision Screening](https://www.nei.nih.gov/about/education-and-outreach/glaucoma-resources/national-eye-institute-statement-detection-glaucoma-and-adult-vision-screening) (captured in sources/S07.json) | ICL100 |
| S10 | NIMH, [Depression](https://www.nimh.nih.gov/health/publications/depression) (captured in sources/S10.json) | ICL14, ICL95, ICL18 |
| S12 | NASA, [SANS technical brief OCHMO-MTB-001](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf), dated 21 February 2024 | ICL100, ICL64. The PDF was read in full in this pass |

<a id="sources-added"></a>

### Sources added by this page

None of these is in sources.csv yet. "Read" means the page or PDF text was fetched and read on 14 November 2026, except where the row gives another date; "abstract" means the PubMed abstract only.

| ID | Source | Date on the source | Access |
|---|---|---|---|
| C01 | NHS, [Heart palpitations](https://www.nhs.uk/conditions/heart-palpitations/) | Last reviewed 17 March 2026 | Read |
| C02 | NHLBI, [Arrhythmias: Symptoms](https://www.nhlbi.nih.gov/health/arrhythmias/symptoms) | Last updated 24 March 2022 | Read |
| C03 | NHLBI, [What Is an Arrhythmia?](https://www.nhlbi.nih.gov/health/arrhythmias) | Last updated 24 March 2022 | Read |
| C04 | NHLBI, [Atrial Fibrillation: Symptoms](https://www.nhlbi.nih.gov/health/atrial-fibrillation/symptoms) | Last updated 30 November 2022 | Read |
| C05 | NHLBI, [Atrial Fibrillation: Diagnosis](https://www.nhlbi.nih.gov/health/atrial-fibrillation/diagnosis) | Last updated 30 November 2022 | Read |
| C06 | Perez MV and others. Large-Scale Assessment of a Smartwatch to Identify Atrial Fibrillation. N Engl J Med 2019;381(20):1909-1917. [PMID 31722151](https://pubmed.ncbi.nlm.nih.gov/31722151/) | 2019 | Abstract |
| C07 | Tohma A and others. Evaluation of Remote Photoplethysmography Measurement Conditions toward Telemedicine Applications. Sensors 2021;21(24):8357. [PMID 34960451](https://pubmed.ncbi.nlm.nih.gov/34960451/) | 2021 | Abstract |
| C08 | FDA, [Pulse Oximeter Basics](https://www.fda.gov/consumers/consumer-updates/pulse-oximeter-basics) | Content current as of 26 March 2025 | Read |
| C09 | NASA, [OCHMO-TB-019 Orthostatic Intolerance, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-019-orthostatic-intolerance.pdf) | 1 December 2023 | Read (PDF) |
| C10 | NHLBI, [Heart Attack: Symptoms](https://www.nhlbi.nih.gov/health/heart-attack/symptoms) | Last updated 24 March 2022 | Read |
| C11 | NHLBI, [What Is a Heart Attack?](https://www.nhlbi.nih.gov/health/heart-attack) | Last updated 24 March 2022 | Read |
| C12 | NIMH, [Panic Disorder: What You Need to Know](https://www.nimh.nih.gov/health/publications/panic-disorder-when-fear-overwhelms) | Revised 2025 | Read |
| C13 | NIMH, [Generalized Anxiety Disorder: What You Need to Know](https://www.nimh.nih.gov/health/publications/generalized-anxiety-disorder-gad) | Revised 2025 | Read |
| C14 | NASA, [OCHMO-TB-016 Behavioral Health and Performance, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-016-behavioral-health.pdf) | 24 October 2023 | Read (PDF) |
| C15 | NASA, [OCHMO-TB-041 Sleep Accommodations, Rev B](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-041-sleep-accommodations.pdf) | 23 October 2023 | Read (PDF) |
| C16 | Barger LK and others. Prevalence of sleep deficiency and use of hypnotic drugs in astronauts before, during, and after spaceflight: an observational study. Lancet Neurol 2014;13(9):904-912. [PMID 25127232](https://pubmed.ncbi.nlm.nih.gov/25127232/) | 2014 | Abstract |
| C17 | NHLBI, [What Is Insomnia?](https://www.nhlbi.nih.gov/health/insomnia) | Last updated 24 March 2022 | Read |
| C18 | NHLBI, [Insomnia: Symptoms](https://www.nhlbi.nih.gov/health/insomnia/symptoms) | Last updated 24 March 2022 | Read |
| C19 | NHLBI, [Insomnia: Diagnosis](https://www.nhlbi.nih.gov/health/insomnia/diagnosis) | Last updated 24 March 2022 | Read |
| C20 | NHLBI, [Sleep Deprivation and Deficiency: How Sleep Affects Your Health](https://www.nhlbi.nih.gov/health/sleep-deprivation/health-effects) | Last updated 15 June 2022 | Read |
| C21 | Smith MT and others. Use of Actigraphy for the Evaluation of Sleep Disorders and Circadian Rhythm Sleep-Wake Disorders: An American Academy of Sleep Medicine Clinical Practice Guideline. J Clin Sleep Med 2018;14(7):1231-1237. [PMID 29991437](https://pubmed.ncbi.nlm.nih.gov/29991437/) | 2018 | Abstract |
| C22 | Chinoy ED and others. Performance of seven consumer sleep-tracking devices compared with polysomnography. Sleep 2021;44(5):zsaa291. [PMID 33378539](https://pubmed.ncbi.nlm.nih.gov/33378539/) | 2021 | Abstract |
| C23 | NASA, [OCHMO-MTB-009 Circadian Desynchrony](https://www.nasa.gov/wp-content/uploads/2026/03/ochmo-mtb-009-circadian-desynchrony.pdf) | 4 March 2026 | Read (PDF) |
| C24 | Basner M, Dinges DF. Maximizing sensitivity of the psychomotor vigilance test (PVT) to sleep loss. Sleep 2011;34(5):581-591. [PMID 21532951](https://pubmed.ncbi.nlm.nih.gov/21532951/) | 2011 | Abstract |
| C25 | NASA, [OCHMO-TB-004 Carbon Dioxide, Rev D](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-004-carbon-dioxide.pdf) | 10 March 2026 | Read (PDF) |
| C26 | Law J and others. Relationship between carbon dioxide levels and reported headaches on the International Space Station. J Occup Environ Med 2014;56(5):477-483. [PMID 24806559](https://pubmed.ncbi.nlm.nih.gov/24806559/) | 2014 | Abstract |
| C27 | NHS, [Headaches](https://www.nhs.uk/conditions/headaches/) | Last reviewed 17 April 2024 | Read |
| C28 | NHS, [Vision loss](https://www.nhs.uk/conditions/vision-loss/) | Last reviewed 28 August 2025 | Read |
| C29 | British Heart Foundation, [Checking your pulse](https://www.bhf.org.uk/informationsupport/tests/checking-your-pulse): finger placement, pressure, counting for 30 or 60 seconds | Published 18 October 2024 | Read on 14 November 2026 |
| C30 | British Heart Foundation, [Your heart rate](https://www.bhf.org.uk/informationsupport/how-a-healthy-heart-works/your-heart-rate) | Not recorded | Read on 14 November 2026 |

The NASA technical briefs say of themselves that they are for reference only and do not deliver medical care recommendations for individuals (S12). They are used here for spaceflight context, never as instructions to a crew member.

## What could not be sourced

- **Alert thresholds.** No numeric alert band was sourced for pulse, blood pressure, mood, sleep duration or reaction time. Each reads "threshold to be set by clinical review".
- **"Do not go away."** How long palpitations must last to count as not going away (C01).
- **Cuff in an irregular rhythm.** How the chosen automated blood pressure cuff behaves during an irregular rhythm; the device is not yet chosen.
- **Our own sensors.** The accuracy of our camera pulse and breathing on our cameras and lighting, and of our band's sleep estimate. The PRD keeps these experimental until measured.
- **ICL18 versus ICL95.** NASA's definitions that separate the two sleep entries. They are in NASA's condition documentation, which was not read in this pass.
- **The CO2 standard itself.** NASA-STD-3001 Volume 2 was not re-read. The 3 mmHg one-hour limit is quoted from OCHMO-TB-004 Rev D (C25), which cites requirement [V2 6004] as "Volume 2 Rev D" beside the limit and lists related requirements under "Volume 2, Rev E"; the package cites Rev F. Check the revision letter against the standard.
- **NICE guidance.** The NICE pages for atrial fibrillation (NG196), chest pain (CG95) and headaches (CG150) refused automated access (HTTP 403) on 14 November 2026, so nothing from them is quoted. NHS and NHLBI pages cover the red flags used here.
- **FDA safety communication.** FDA's 2021 pulse oximeter safety communication returned HTTP 404 at its old address; FDA's current consumer page (C08) is used instead.
- **Heart rate in flight.** Beyond fluid shift and baroreflex changes (C09), no source read here describes how resting heart rate changes in flight.
- **A crewmate's count against the cameras.** How far apart a counted pulse and the camera pulse may be before they disagree: a team default until clinical review sets it, then measured at the venue on [N] pairs.
- **How often to re-check.** The interval in ICL10's watch stage reads "[interval to be set by clinical review]".
