# Mission routine

**Unreviewed draft: needs clinician review before live guidance.**

This is what each crewmember measures or does as part of the mission routine, how often, and what makes the app ask more often. Each item says who does it, with what, how long it takes, and how to do it step by step. The app prompts these tasks; it never fills in a missed one.

How to read it:

- **Sourced** means a NASA document or an authoritative health page states the cadence, quoted in the item. Nearly all sourced cadences describe ISS practice. Using the same cadence in transit, on the Moon and on Mars is a team default unless the item says otherwise.
- **Team default** means no source we read gives a frequency. For those items the frequency is to be set by the flight surgeon, and the cadence shown is the team's proposal until then.
- **Minutes** are team estimates except where an item marks them as sourced.
- **A reading outside your usual range** is compared with your own baseline. The size of change that counts is a threshold to be set by clinical review. Until then, the team default response is to repeat the reading, then measure more often and tell the crew medical officer.
- **FD 30** means flight day 30. **R-30** means 30 days before return; **R+7** means 7 days after landing.
- **(sensor_limit)** marks a trigger raised by a habitat sensor rather than by the crew.
- **CMO** is the crew medical officer: a crewmember trained to give medical care on board. **Ground** is the flight surgeon and specialists on Earth.

The machine-readable version is `routine-schedule.json`. Saved source texts are in `research/routine/`.

## Summary

| # | Item | Default | Basis | Who | Min | Station (LEO) | Transit | Moon base | Mars base |
|---|---|---|---|---|---|---|---|---|---|
| 1 | [Daily check: symptoms, sleep and mood](#daily-check) | daily | team default | You | 3 | daily | daily | daily | daily |
| 2 | [Resting pulse](#resting-pulse) | daily | team default | You | 2 | daily | daily | daily | daily |
| 3 | [Breathing rate](#breathing-rate) | daily | team default | You, Crewmate | 2 | daily | daily | daily | daily |
| 4 | [Blood pressure](#blood-pressure) | weekly | team default | You, Crewmate | 10 | weekly | weekly | weekly | weekly |
| 5 | [Blood oxygen (pulse oximeter)](#oxygen-saturation) | weekly | team default | You | 2 | weekly | weekly | weekly | weekly |
| 6 | [Body temperature](#body-temperature) | monthly | sourced | You | 3 | monthly | monthly | monthly | monthly |
| 7 | [Body mass](#body-mass) | weekly | team default | You | 5 | weekly | weekly | weekly | weekly |
| 8 | [Fluids and hydration](#hydration) | daily | team default | You | 1 | daily | daily | daily | daily |
| 9 | [Exercise session and log](#exercise-log) | daily | team default | You | 2 | daily | daily | daily | daily |
| 10 | [Reaction test (PVT-B, 3 minutes)](#reaction-test) | every 4 days | sourced | You | 4 | every 4 days | every 4 days | every 4 days | every 4 days |
| 11 | [Monthly thinking test (WinSCAT class)](#cognitive-test) | monthly | sourced | You, Ground | 30 | monthly | monthly | monthly | monthly |
| 12 | [Vision self-check: near reading card and Amsler grid](#vision-self-check) | weekly | team default | You | 5 | weekly | weekly | weekly | weekly |
| 13 | [SANS eye exam (OCT, fundoscopy, ultrasound)](#eye-imaging-sans) | FD 30, FD 90, R-30 | sourced | Crewmate, CMO, Ground | 60 | FD 30, FD 90, R-30 | FD 30, FD 90, then every 90 days | FD 30, R-30 | every 90 days |
| 14 | [Hearing check (on-orbit hearing assessment)](#hearing-check) | monthly | team default | You, Ground | 20 | monthly | monthly | monthly | monthly |
| 15 | [Crew-worn noise dosimeter (24 hours)](#noise-dosimetry) | every 60 days | sourced | You, Crewmate, Ground | 10 | every 60 days | every 60 days | every 60 days | every 60 days |
| 16 | [Personal radiation dosimeter](#radiation-dosimeter) | always worn | sourced | You, Ground | 1 | always worn | always worn | always worn | always worn |
| 17 | [Before a spacewalk](#pre-eva-check) | each spacewalk | sourced | You, CMO, Ground | 20 | each spacewalk | each spacewalk | each spacewalk | each spacewalk |
| 18 | [After a spacewalk: bends, hands, shoulders](#post-eva-check) | each spacewalk | sourced | You, Crewmate, CMO, Ground | 15 | each spacewalk | each spacewalk | each spacewalk | each spacewalk |
| 19 | [Eyes, skin and breathing after dust exposure](#dust-exposure-check) | each surface walk | team default | You, Crewmate | 10 | as needed | as needed | each surface walk | each surface walk |
| 20 | [Weekly wellbeing self-check](#behavioral-self-check) | weekly | team default | You | 5 | weekly | weekly | weekly | weekly |
| 21 | [Private medical conference with the flight surgeon](#private-medical-conference) | weekly | sourced | You, Ground | 15 | weekly | weekly | weekly | weekly |
| 22 | [Private psychological conference](#private-psych-conference) | every 14 days | sourced | You, Ground | 20 | every 14 days | every 14 days | every 14 days | every 14 days |
| 23 | [Monthly health exam (periodic health status)](#periodic-health-exam) | monthly | sourced | CMO, You, Ground | 30 | monthly | monthly | monthly | monthly |
| 24 | [Mouth, teeth and skin check](#dental-skin-check) | monthly | team default | You, Crewmate | 5 | monthly | monthly | monthly | monthly |
| 25 | [Medicines and medical kit check](#medical-kit-inventory) | monthly | team default | CMO, Crewmate | 20 | monthly | monthly | monthly | monthly |
| 26 | [Medical emergency drill](#emergency-drill) | monthly | team default | You, Crewmate, CMO, Ground | 60 | monthly | monthly | monthly | monthly |
| 27 | [Drinking water sampling](#water-sampling) | monthly | sourced | Crewmate, Ground | 20 | monthly | monthly | monthly | monthly |
| 28 | [Cabin air sampling](#air-sampling) | every 90 days | sourced | Crewmate, Ground | 20 | every 90 days | every 90 days | every 90 days | every 90 days |
| 29 | [Surface microbial sampling](#surface-sampling) | every 90 days | sourced | Crewmate, Ground | 30 | every 90 days | every 90 days | every 90 days | every 90 days |
| 30 | [Weekly cleaning](#housekeeping-clean) | weekly | sourced | You, Crewmate | 180 | weekly | weekly | weekly | weekly |
| 31 | [Pre-flight baselines](#preflight-baselines) | pre-flight | sourced | You, Ground | 30 | pre-flight | pre-flight | pre-flight | pre-flight |
| 32 | [Pre-flight quarantine (health stabilization)](#preflight-quarantine) | pre-flight | sourced | You, Ground | 3 | pre-flight | pre-flight | pre-flight | pre-flight |
| 33 | [First days in space: adaptation check](#early-flight-adaptation) | early flight | sourced | You, Ground | 3 | early flight | early flight | early flight | early flight |
| 34 | [After landing on the Moon or Mars: gravity check](#gravity-arrival-check) | after landing | team default | You, Crewmate, CMO | 10 | n/a | n/a | after landing | after landing |
| 35 | [Before return: entry preparation](#pre-return-prep) | return | sourced | You, CMO, Ground | 10 | return | return | return | return |
| 36 | [After landing on Earth: recovery checks](#post-return-recovery) | return | sourced | You, Ground | 10 | return | return | return | return |

Around spacewalks, the routine adds the before and after checks, a private medical conference within 24 hours on each side, dosimeter readings, and on the Moon or Mars the dust check. On return, the private medical conference becomes daily for the last five days before entry.

## How each environment changes the routine

- **Station in low Earth orbit (`station_leo`).** The cadences quoted here come mostly from ISS practice. The ground answers in real time and resupply is regular.
- **Deep-space transit (`transit`).** No resupply, so the monthly kit check also plans for medicines expiring. Messages to the ground are delayed, so conferences may be recorded and sent, and the weekly self-checks carry more weight. Personal radiation monitoring continues the whole way (RT03). Spacewalks happen only for contingencies.
- **Lunar surface base (`lunar_surface`).** Surface walks can be frequent, and each one adds the before and after checks and the dust check. Lunar dust irritates eyes, nose and skin. Arrival in lunar gravity adds the gravity arrival check.
- **Mars surface base (`mars_surface`).** As on the Moon, plus long message delays: conferences and spacewalk clearances are sent ahead and the reply comes later. Mars dust also carries chemicals such as perchlorate. After a long transit, the gravity arrival check matters more.

## Every day and vital signs

<a id="daily-check"></a>

### 1. Daily check: symptoms, sleep and mood

**What it is.** Answer three short questions each day: anything new in your body, how you slept, and how your mood is.

**Why it matters.** Small changes are easiest to catch early, and astronauts often sleep less than they need. Your own answers, day after day, build the baseline that every other reading is compared with.

**Who.** You.

**Instrument.** The app; the wrist band offers a sleep estimate that you confirm or correct.

**Time.** About 3 minutes (team estimate).

**Steps.**

1. Open Today and tap Daily check.
2. Symptoms: tap the body area if something is new or worse. If nothing is new, tap Nothing new.
3. Sleep: look at the wrist band estimate. Keep it if it is right, or change it. Your correction is saved next to the band's number, which is never erased.
4. Mood: move the slider to how you feel right now. There is no right answer.
5. If anything feels urgent, press Someone needs help instead of finishing the check.
6. The check is done when these three questions are answered. Other readings are optional.

**How often.** Default: daily. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | daily |
| Transit | daily |
| Moon base | daily |
| Mars base | daily |

**Do it more often when:**

- You report a new or worse symptom. Then: daily (a follow-up question each morning until you mark it resolved). (team default)
- Cabin CO2 is above the one-hour limit of 3 mmHg (sensor_limit). Then: add a headache and vision question at the next check and again 2 hours later. (team default; C25)
- A solar particle event alert. Then: ask about nausea, vomiting, tiredness and skin each day for the next 3 days. (team default; RT09)
- You come back from a spacewalk. Then: ask about joint pain, numbness, itching or rash that evening and the next morning. (team default; RT11)
- Sleep under 6 hours (band estimate or your own answer). Then: offer the reaction test that morning. (team default; RT25)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT02: "Maintenance and monitoring of work/rest schedules and optimal sleep/circadian rhythm."
- C15: "astronauts sleep an average of about six hours while in space"
- RT25: "sleep durations <6 h were associated with significant reductions in psychomotor response speed, elevated stress, and higher workload."
- C25: "The system shall limit the average one-hour CO2 partial pressure (ppCO2) in the habitable volume to no more than 3 mmHg."

**Note.** No source sets a daily cadence for a crew self-report. Daily is the team default and matches the product's daily check (PRD section 4.2).

<a id="resting-pulse"></a>

### 2. Resting pulse

**What it is.** Sit still for a few minutes and let the camera station read your pulse, or count it at your wrist.

**Why it matters.** Your resting pulse is a simple sign of how your heart and body are coping. A change from your own usual number can point to illness, dehydration, poor sleep or stress.

**Who.** You.

**Instrument.** Camera station (experimental, compared against the oximeter); kit pulse oximeter; or two fingers and a watch.

**Time.** About 2 minutes (team estimate).

**Steps.**

1. Rest quietly for 10 minutes first. Do not take it right after exercise.
2. Camera station: hold still facing the cameras in good light until the reading appears. If it says No reading, keep still and try once more.
3. By hand: place your index and middle finger on the inside of the opposite wrist, below the base of the thumb, and press gently until you feel the beat.
4. Count the beats for 1 full minute, or for 30 seconds and multiply by 2.
5. Never press on both sides of the neck at the same time.
6. Save the number. The app compares it with your own usual range.

**How often.** Default: daily. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | daily (taken with the daily check when you accept the camera reading) |
| Transit | daily (taken with the daily check when you accept the camera reading) |
| Moon base | daily (taken with the daily check when you accept the camera reading) |
| Mars base | daily (taken with the daily check when you accept the camera reading) |

**Do it more often when:**

- A racing, pounding or irregular heartbeat, dizziness, fainting or chest discomfort. Then: as needed (measure now; the fixed urgent questions come first). (team default; RT28)
- A reading outside your usual range. Then: repeat after 10 more minutes of rest; if still outside, daily for 3 days and tell the crew medical officer. (team default; RT28)
- Before and after each spacewalk (part of the pre and post spacewalk exam). Then: each spacewalk. (sourced; RT18, RT17)
- Return to gravity (landing on Earth, the Moon or Mars). Then: daily (sitting and standing readings with a crewmate present, until your crew medical officer stops it). (team default; C09, RT02)

**Body region.** heart.

**Sources, quoted exactly.**

- RT28: "To determine the resting heart rate, you must have been resting for at least 10 minutes."
- RT28: "To measure the pulse at your wrist, place your index and middle finger over the underside of your opposite wrist, below the base of the thumb."
- RT28: "Once you find the pulse, count the beats for 1 full minute. Or, count the beats for 30 seconds and multiply by 2."
- RT28: "do not take the pulses on both sides of the neck at the same time."
- RT28: "Any change from your normal heart rate can indicate a health problem."
- RT17: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file"
- RT02: "Assessing, recording, monitoring, and trending vital signs and additional physiological and behavioral health signs."

**Note.** ISS practice measures heart rate in the monthly periodic health status exam (RT16, RT17). The daily camera reading is the team default for this product.

<a id="breathing-rate"></a>

### 3. Breathing rate

**What it is.** Sit quietly and let the camera station count your breaths for a minute, or have a crewmate count them.

**Why it matters.** How fast you breathe at rest changes early with lung problems, fever, anxiety and bad air. It is hard to count your own breaths honestly, so a camera or a crewmate does it better.

**Who.** You and a crewmate.

**Instrument.** Camera station (experimental); or a crewmate and a watch.

**Time.** About 2 minutes (team estimate).

**Steps.**

1. Sit somewhere quiet and relax for a few minutes.
2. Breathe normally. Do not try to control it.
3. Camera station: hold still until the reading appears.
4. By hand: a crewmate watches your chest rise and fall and counts the breaths for 60 seconds.
5. Save the number with who or what counted it.

**How often.** Default: daily. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | daily (taken with the daily check when you accept the camera reading) |
| Transit | daily (taken with the daily check when you accept the camera reading) |
| Moon base | daily (taken with the daily check when you accept the camera reading) |
| Mars base | daily (taken with the daily check when you accept the camera reading) |

**Do it more often when:**

- Shortness of breath, a new cough or chest tightness. Then: as needed (measure now together with blood oxygen). (team default; C08)
- Cabin CO2 above the one-hour limit (sensor_limit). Then: measure at the next check. (team default; C25)
- Smoke, a chemical release or dust in the cabin. Then: measure now and 2 hours later. (team default; RT13, RT03)

**Body region.** lungs.

**Sources, quoted exactly.**

- RT29: "Finding a quiet location to sit down and relax."
- RT29: "Use your watch and count how many breaths you take in 60 seconds."
- RT29: "It can be difficult to count your own breaths, especially if you're nervous or anxious."
- RT29: "They'll be able to see your chest rising and falling with each breath to get an accurate measurement."

**Note.** No NASA source read here sets a breathing rate cadence. Daily with the camera is the team default.

<a id="blood-pressure"></a>

### 4. Blood pressure

**What it is.** Rest, then take two or three cuff readings one minute apart on your upper arm.

**Why it matters.** Spaceflight shifts body fluids toward the head and changes how blood pressure is controlled. High blood pressure usually causes no symptoms, so it is only found by measuring.

**Who.** You and a crewmate.

**Instrument.** Kit blood pressure cuff (upper arm).

**Time.** About 10 minutes (team estimate).

**Steps.**

1. Empty your bladder. No food, caffeine or exercise in the last 30 minutes.
2. Rest for at least 5 minutes, 10 is better. In weightlessness, use foot loops or a strap to hold a seated posture with your back against a support.
3. Bare your upper arm. Keep the arm supported at heart level.
4. Wrap the cuff snugly. Its lower edge sits about 2.5 cm above the bend of your elbow.
5. Press start and stay still and quiet while it measures.
6. Take 2 or 3 readings, 1 minute apart. Save all of them with the time.

**How often.** Default: weekly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | weekly |
| Transit | weekly |
| Moon base | weekly |
| Mars base | weekly |

**Do it more often when:**

- Headache, dizziness, fainting, or a change in vision. Then: as needed (measure now, then daily for 3 days). (team default; C25)
- A reading outside your usual range. Then: daily (for 5 days, morning and evening readings, then the crew medical officer decides). (team default; RT27)
- Return to gravity (landing on Earth, the Moon or Mars). Then: daily (sitting and standing, with a crewmate ready to help). (team default; C09)
- Before and after each spacewalk (part of the pre and post spacewalk exam). Then: each spacewalk. (sourced; RT18, RT17)

**Body region.** heart.

**Sources, quoted exactly.**

- RT27: "DO NOT take your blood pressure when you are under stress, have had a meal, caffeine, or used tobacco in the past 30 minutes, or have exercised recently."
- RT27: "Rest for at least 5 minutes, 10 minutes is better, before blood pressure is taken."
- RT27: "Your arm should be supported so that your upper arm is at heart level."
- RT27: "The lower edge of the cuff should be 1 inch (2.5 cm) above the skin fold at the bend of your elbow."
- RT27: "Take 2 or 3 readings at a sitting. Take the readings 1 minute apart."
- RT27: "A typical approach for home blood pressure checks is to take your blood pressure before breakfast and dinner for 5 days."
- RT27: "High blood pressure has no symptoms (except in rare, extreme situations), so you may not know if you have this problem."
- RT17: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file"
- C09: "Orthostatic intolerance (OI) is an abnormal response observed during return to a gravitational environment after microgravity exposure."

**Note.** ISS measures blood pressure at least in the monthly periodic health status exam (RT16, RT17). Weekly self-measurement is the team default; frequency to be set by the flight surgeon. The seated posture in RT27 is written for Earth; the restraint advice is the team's adaptation.

<a id="oxygen-saturation"></a>

### 5. Blood oxygen (pulse oximeter)

**What it is.** Clip the pulse oximeter on a warm finger, keep still, and save the steady number.

**Why it matters.** Blood oxygen shows whether your lungs are getting oxygen into your blood. It matters most when breathing feels wrong or the cabin air or pressure changes.

**Who.** You.

**Instrument.** Kit pulse oximeter (finger).

**Time.** About 2 minutes (team estimate).

**Steps.**

1. Warm and relax your hand. Remove any nail polish from that finger.
2. Clip the oximeter on the finger. Keep the hand below heart level.
3. Keep still. Do not move that hand.
4. Wait until the number stops changing and shows one steady value.
5. Save the number with the time. The app also saves the pulse the oximeter shows, to check the camera.

**How often.** Default: weekly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | weekly |
| Transit | weekly |
| Moon base | weekly |
| Mars base | weekly |

**Do it more often when:**

- Shortness of breath, bluish lips or nails, chest tightness, or a racing pulse. Then: as needed (measure now; the fixed urgent questions come first). (team default; C08)
- Cabin pressure drop or low oxygen alarm (sensor_limit). Then: as needed (everyone measures now). (team default; RT03)
- Before and after each spacewalk (part of the pre and post spacewalk exam). Then: each spacewalk. (sourced; RT18, RT17)
- The camera pulse disagrees with how you feel. Then: as needed (use the oximeter pulse as the check). (team default)

**Body region.** lungs.

**Sources, quoted exactly.**

- C08: "Follow your health care provider's advice about when and how often to check your oxygen levels."
- C08: "When placing the pulse oximeter on your finger, make sure your hand is warm, relaxed, and held below the level of the heart. Remove any fingernail polish on that finger."
- C08: "Sit still and do not move the part of your body where the pulse oximeter is located."
- C08: "Wait a few seconds until the reading stops changing and displays one steady number."
- C08: "Bluish coloring in the face, lips, or nails."
- C08: "Be aware multiple factors can affect the accuracy of a pulse oximeter reading, such as poor circulation, skin pigmentation, skin thickness, skin temperature, current tobacco use, and use of fingernail polish."
- RT17: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file"

**Note.** ISS measures blood oxygen in the monthly periodic health status exam (RT17). Weekly is the team default; frequency to be set by the flight surgeon.

<a id="body-temperature"></a>

### 6. Body temperature

**What it is.** Take your temperature under the tongue with the digital thermometer when you feel unwell, and at the monthly exam.

**Why it matters.** A raised temperature is usually a sign of infection. In a closed habitat, one person's infection can spread to the whole crew.

**Who.** You.

**Instrument.** Kit digital thermometer.

**Time.** About 3 minutes (team estimate).

**Steps.**

1. Wait 30 minutes after eating, drinking or exercise.
2. Clean the tip with soap and water, then rinse it.
3. Turn it on and put the tip under your tongue, towards the back of your mouth.
4. Close your lips around it and wait until it beeps or flashes.
5. Save the number and say where you measured (mouth or armpit).
6. If you feel hot, cold or shivery, report it even if the number looks normal.

**How often.** Default: monthly. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly (as part of the periodic health status exam) |
| Transit | monthly (as part of the periodic health status exam) |
| Moon base | monthly (as part of the periodic health status exam) |
| Mars base | monthly (as part of the periodic health status exam) |

**Do it more often when:**

- You feel hot, cold or shivery, or have symptoms of infection. Then: daily (morning and evening until 2 days without fever). (team default; RT30)
- A crewmate is ill with a possible infection. Then: daily (all crew, until the crew medical officer stops it). (team default; RT24)
- Microbial counts above limits in water, air or surfaces. Then: daily (all crew for 5 days). (team default; RT20)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT30: "Normal body temperature is different for everyone and changes during the day."
- RT30: "A high temperature is usually considered to be 38C or above."
- RT30: "Put the tip under your tongue, towards the back of your mouth."
- RT30: "Close your lips around the thermometer."
- RT30: "If you feel hot, cold or shivery, you may have a high temperature even if a thermometer says your temperature is below 38C."
- RT16: "Periodic Health Status (PHS) examinations requires crew members to perform these exams on each other monthly."
- RT17: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file"

**Note.** The monthly cadence comes from the ISS periodic health status exam (RT16, RT17). The 38C figure is the NHS's general adult figure, not a threshold for this product: alert thresholds are to be set by clinical review.

<a id="body-mass"></a>

### 7. Body mass

**What it is.** Measure your body mass on the habitat's mass device at the same time of day each week.

**Why it matters.** Crews often eat less than they need in flight, and losing mass can mean losing muscle and bone. Tracking it shows early whether you are eating enough.

**Who.** You.

**Instrument.** Mass measurement device (ISS class, for example SLAMMD); not in this kit.

**Time.** About 5 minutes (team estimate).

**Steps.**

1. Measure at the same time of day each week, before eating and after using the toilet.
2. Wear the same light clothing each time.
3. Follow the device's own instructions to the end.
4. Save the number and the device used.
5. On the Moon or Mars, a scale made for Earth reads weight, not mass, and reads low. Use the device the habitat provides for mass.

**How often.** Default: weekly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | weekly (mass device built for weightlessness) |
| Transit | weekly (mass device built for weightlessness) |
| Moon base | weekly (use a device that measures mass in lunar gravity) |
| Mars base | weekly (use a device that measures mass in Mars gravity) |

**Do it more often when:**

- Mass falls compared with your usual (amount to be set by clinical review). Then: every 3 days (until stable, and tell the crew medical officer). (team default; RT34)
- Poor appetite, nausea or vomiting for more than a day. Then: every 3 days. (team default; RT04)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT34: "Body mass monitoring is important for monitoring crew health during a mission and to help ensure that crewmembers consume adequate energy intake to mitigate the risks of spaceflight."
- RT34: "After an initial loss in the first 30 d of flight, body mass remained constant through the rest of the mission"
- RT02: "Optimization and periodic monitoring of nutrition intake"
- RT18: "Body Mass Measurement"

**Note.** Body mass measurement is a planned ISS medical activity (RT18), but no source read here states its interval. Frequency to be set by the flight surgeon; weekly is the team default.

<a id="hydration"></a>

### 8. Fluids and hydration

**What it is.** Log roughly how much you drank today and the colour of your urine.

**Why it matters.** Dehydration causes headache, poor sleep and poor performance, raises the risk of kidney stones and urinary infection, and raises the risk of decompression sickness on a spacewalk.

**Who.** You.

**Instrument.** The app; drink bags or the water dispenser count.

**Time.** About 1 minutes (team estimate).

**Steps.**

1. Tap how much you drank today, in drink bags or litres.
2. Pick the urine colour that matches best. Pale is the goal.
3. Tick if you feel thirsty, or are passing urine less often than usual.
4. If your urine is dark or you feel thirsty, drink now and check again later.

**How often.** Default: daily. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | daily (with the daily check) |
| Transit | daily (with the daily check) |
| Moon base | daily (with the daily check) |
| Mars base | daily (with the daily check) |

**Do it more often when:**

- The day before and the morning of a spacewalk. Then: each spacewalk. (team default; RT10)
- Heavy exercise, a hot habitat, vomiting, diarrhoea or fever. Then: check again that evening. (team default; RT31, RT30)
- Before entry and landing. Then: return (fluid loading as prescribed by the flight surgeon). (sourced; RT03, RT23)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT03: "the minimum water needed per crewmember per day is 2.5 L (84.5 fl oz)"
- RT03: "Dehydration of the crewmember will have consequences ranging from poor communication and crewmember performance caused by dry mucous membranes, nosebleeds, headache, malaise, and fitful sleep to urinary tract infection or urinary calculi"
- RT10: "Dehydration reduces plasma volume and can increase blood viscosity, leading to impaired inert gas transport and decreasing bubble removal from the body."
- RT31: "dark yellow, strong-smelling pee"
- RT31: "You should drink enough during the day so that your pee is a pale colour."
- RT31: "Drink more when there's a higher risk of dehydrating."

**Note.** 2.5 L per day is the minimum water the vehicle must provide (RT03), a design requirement, not a personal drinking target. Daily logging is the team default.

<a id="exercise-log"></a>

### 9. Exercise session and log

**What it is.** Do your prescribed exercise and log what you did, how long, and anything that hurt.

**Why it matters.** Without gravity, muscle, bone and heart fitness fall. Daily exercise is the main protection, and you need that strength to walk, egress and work after landing.

**Who.** You.

**Instrument.** Habitat exercise devices; wrist band for activity and pulse; the app log.

**Time.** About 2 minutes (team estimate).

**Steps.**

1. Do the session your strength and conditioning team prescribed.
2. Log the device, minutes and how hard it felt.
3. Note any pain, joint trouble or dizziness. Do not push through sharp pain.
4. If you missed a session, log the reason. A missed session is never filled in for you.

**How often.** Default: daily. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | daily |
| Transit | daily |
| Moon base | daily (surface walks add load; log them as activity) |
| Mars base | daily (surface walks add load; log them as activity) |

**Do it more often when:**

- Two missed sessions in a row. Then: a reminder the next morning and a note to the crew medical officer. (team default)
- New muscle or joint pain, or a shoulder problem after a spacewalk. Then: as needed (stop that exercise and tell the crew medical officer). (team default; RT33)
- After landing on Earth. Then: return (reconditioning 2 hours a day, 7 days a week, for 45 days from the day after landing). (sourced; RT14)

**Body region.** legs.

**Sources, quoted exactly.**

- RT14: "Approximately 30 minutes of interval or steady-state exercise"
- RT14: "30-60 minutes load-bearing training"
- RT14: "average 600 min/week/crewmember"
- RT02: "Maintenance and periodic monitoring of aerobic and strength physical fitness"
- RT14: "Reconditioning is scheduled for 2 hours per day/7 days a week, flexible if needed"

**Note.** The prescription itself (RT14) comes from the strength and conditioning team. The 2 minutes are for logging; the session takes its prescribed time.

## Alertness and thinking

<a id="reaction-test"></a>

### 10. Reaction test (PVT-B, 3 minutes)

**What it is.** Tap the screen as fast as you can each time the counter appears, for 3 minutes.

**Why it matters.** Tiredness slows your reactions before you notice it yourself. This short test is the same kind NASA used on the ISS and shows when sleep loss is affecting you.

**Who.** You.

**Instrument.** The app reaction test (PVT-B style); a quiet spot.

**Time.** About 4 minutes (sourced from RT25: the test itself is 3 minutes; 1 more minute for the questions is a team estimate).

**Steps.**

1. Find a quiet spot where you will not be interrupted for 4 minutes.
2. Answer the short questions on how tired, stressed and sleepy you feel.
3. Watch the box. When the counter appears, tap as fast as you can.
4. Do not tap before it appears. Early taps count as errors.
5. Keep going until the 3 minutes end. The app compares the result with your own usual speed.
6. Note any distraction during the test.

**How often.** Default: every 4 days. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | every 4 days (morning after waking and evening before sleep on test days) |
| Transit | every 4 days (morning after waking and evening before sleep on test days) |
| Moon base | every 4 days (morning after waking and evening before sleep on test days) |
| Mars base | every 4 days (morning after waking and evening before sleep on test days) |

**Do it more often when:**

- Sleep under 6 hours. Then: one test that morning. (team default; RT25)
- A sleep shift or schedule change. Then: daily (during the shift and for 3 days after). (team default; C15, RT02)
- Before a spacewalk or other critical task. Then: each spacewalk. (team default; RT15, RT02)
- Head injury or illness. Then: as needed (with the crew medical officer). (team default; C14)
- After landing on Earth. Then: return (daily for the first week, then 30, 60 and 90 days after landing). (sourced; RT25)

**Body region.** eyes.

**Sources, quoted exactly.**

- RT25: "In-flight on the ISS, astronauts were scheduled to perform testing every fourth day."
- RT25: "The Reaction SelfTest (RST) was sampled twice on each testing day, in the morning after getting up and in the evening in the 2 h pre-bed"
- RT25: "The RST assessed astronaut behavioral alertness using the 3-min Psychomotor Vigilance Test-Brief (PVT-B"
- RT25: "Post-mission testing was conducted daily for the first week upon return to Earth and then conducted 30, 60, and 90 days thereafter."
- RT26: "subjects were instructed to press the response button as soon as each stimulus appeared, in order to keep RT as low as possible, but not to press the button too soon (which yielded a false start warning on the display)."
- RT26: "PVT performance does not improve as a function of repeated administration"

**Note.** The every fourth day cadence is from an ISS research study (RT25), not an operational requirement. The PRD keeps the reaction test optional; this schedule only prompts it.

<a id="cognitive-test"></a>

### 11. Monthly thinking test (WinSCAT class)

**What it is.** Once a month, take the longer computer thinking test that ISS crews use, so changes after an injury or illness can be judged.

**Why it matters.** A monthly result gives a known baseline. If someone hits their head or becomes ill, the crew can compare and see how much thinking is affected and whether it recovers.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** Cognitive test software such as WinSCAT, if the vehicle carries it; not in this kit.

**Time.** About 30 minutes (sourced from RT15: 30 minutes for routine monitoring).

**Steps.**

1. Pick a quiet time when you are not rushed.
2. Start the test and follow its on-screen instructions.
3. Finish all parts in one sitting.
4. The result goes to the ground team, who compare it with your pre-flight baseline.

**How often.** Default: monthly. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly |
| Transit | monthly |
| Moon base | monthly |
| Mars base | monthly |

**Do it more often when:**

- A head injury, or an illness that affects thinking. Then: as needed (with the crew medical officer and the ground). (sourced; C14, RT15)

**Body region.** head.

**Sources, quoted exactly.**

- C14: "Cognitive testing is currently fulfilled by the monthly administration of the Spaceflight Cognitive Assessment Tool for Windows (WinSCAT)."
- C14: "A traumatic injury or illness on-orbit would dictate additional testing and assessment."
- C14: "For missions beyond 6 months and/or beyond LEO, additional minimally obtrusive measures that objectively evaluate psychological and behavioral states within the mission context should be provided."
- RT15: "Every 30 days"
- RT15: "Routine Monitoring 30 minutes"

**Note.** The 30 minutes reads the routine monitoring row of the ISS table in RT15. If the vehicle has no such software, this item is marked Not available here.

## Eyes and ears

<a id="vision-self-check"></a>

### 12. Vision self-check: near reading card and Amsler grid

**What it is.** Read the near card and look at the Amsler grid, one eye at a time, and note anything blurred, wavy or missing.

**Why it matters.** Long missions can change the shape of the eye and blur near vision (SANS). Most crew on long flights show some signs, and the risk grows with time in space. A quick self-check finds changes between the formal eye exams.

**Who.** You.

**Instrument.** Near reading card and Amsler grid card (paper or on screen at a fixed size).

**Time.** About 5 minutes (team estimate).

**Steps.**

1. Wear the glasses you normally use for reading. Use good light.
2. Hold the card at your usual reading distance; for the Amsler grid, 30 to 38 cm.
3. Cover one eye.
4. Near card: read the smallest line you can and save it.
5. Amsler grid: look straight at the centre dot. Without moving your eye, notice whether any lines look blurry, wavy, dark or missing.
6. Repeat with the other eye.
7. Answer the short vision questions: blurred near vision, headaches, eye pain.

**How often.** Default: weekly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | weekly (formal tests at flight day 30, flight day 90 and 30 days before return) |
| Transit | weekly (formal tests at flight day 30, flight day 90 and 30 days before return) |
| Moon base | weekly (formal tests at flight day 30, flight day 90 and 30 days before return) |
| Mars base | weekly (formal tests at flight day 30, flight day 90 and 30 days before return) |

**Do it more often when:**

- Blurred near vision, a new distortion on the grid, or headaches. Then: daily (and tell the crew medical officer and the ground). (team default; S12, C25)
- Cabin CO2 above the one-hour limit (sensor_limit). Then: daily (while CO2 stays above the limit). (team default; C25)
- Flight day 30, flight day 90 and 30 days before return. Then: formal vision tests with the ground. (sourced; S12, RT18)
- Sudden loss of vision, a dark shadow, or severe eye pain. Then: as needed (urgent: press Someone needs help). (team default)

**Body region.** eyes.

**Sources, quoted exactly.**

- S12: "Ocular questionnaire, visual acuity, Amsler grid, fundoscopy, 2-D imaging ultrasound, OCT (high-resolution)."
- S12: "hyperopic patients may experience eyestrain and/or blurred near vision"
- S12: "70% of crew are reported to have one or more signs of SANS"
- S12: "Vision degradation risk increases the longer you are in space."
- RT18: "Vision Testing (Near, Far Acuity, Amsler Grid, Contrast Sensitivity)"
- RT32: "Wearing any glasses you normally use to read, hold the grid 12 to 15 inches away from your face in good light."
- RT32: "While looking directly at the center dot, notice in your side vision if all grid lines look straight or if any lines or areas look blurry, wavy, dark or blank."
- RT02: "Spaceflight Associated Neuro-Ocular Syndrome (SANS) periodic monitoring"

**Note.** The AAO page (RT32) is written for macular degeneration at home; it is used only for how to hold and read the grid. The weekly self-check is a team default; frequency to be set by the flight surgeon.

<a id="eye-imaging-sans"></a>

### 13. SANS eye exam (OCT, fundoscopy, ultrasound)

**What it is.** On scheduled eye weeks, a trained crewmate images your eyes while a ground expert guides by video.

**Why it matters.** Swelling at the back of the eye and folds in the retina cannot be seen in a self-check. Only imaging shows them, and they may not cause symptoms early.

**Who.** A crewmate, the crew medical officer and the ground (flight surgeon or specialists).

**Instrument.** OCT, fundoscope, ocular ultrasound, where the vehicle has them; not in this kit.

**Time.** About 60 minutes (team estimate).

**Steps.**

1. The trained operator sets up the device and opens the video link to the ground guide.
2. Follow the operator's instructions for where to look and when to keep still.
3. The ground team checks image quality while you are still in position.
4. Do the ocular questionnaire, visual acuity and Amsler grid in the same session.
5. Results are reviewed by the ground; the app records the exam as an imported report.

**How often.** Default: on these events: flight day 30, flight day 90 and 30 days before return. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | on these events: flight day 30, flight day 90 and 30 days before return |
| Transit | on these events: flight day 30 and flight day 90 (then every 90 days (team default) and 30 days before return; only if the vehicle carries the devices) |
| Moon base | on these events: flight day 30 and 30 days before return (team default for shorter surface stays; only if the habitat carries the devices) |
| Mars base | every 90 days (team default interval, plus 30 days before departure; only if the habitat carries the devices) |

**Do it more often when:**

- A change on the vision self-check, blurred vision or persistent headache. Then: as needed (as the ground decides). (sourced; S12)

**Body region.** eyes.

**Sources, quoted exactly.**

- S12: "Nominal schedule for 'Eye Weeks', which consists of 2-3 days (FD 30, 90, and R-30) at a time of monitoring and testing including OCT:"
- S12: "During 6-month flights, OCT operator tests 3 times FD 30, 90, and R-30"
- S12: "Additional testing can be performed as clinically indicated at any point during mission"
- S12: "Crew are trained in SANS equipment: OCT, fundoscope, and ocular ultrasound."
- S12: "On ISS, in-flight crew receive assistance from ground with monitoring equipment."

**Note.** The ISS schedule is for 6 month flights. The 90 day interval for longer missions is a team default; frequency to be set by the flight surgeon.

<a id="hearing-check"></a>

### 14. Hearing check (on-orbit hearing assessment)

**What it is.** Take the headphone hearing test in a quiet spot so flight surgeons can see any change in your hearing.

**Why it matters.** Habitat fans and equipment are noisy all day and night. Hearing can shift without you noticing, and ISS tests have found shifts in many crewmembers.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** Audiometer with headphones (ISS class, for example the on-orbit hearing assessment kit); not in this kit. Without it, the hearing questions in the daily check.

**Time.** About 20 minutes (team estimate).

**Steps.**

1. Go to the quietest place in the habitat. Switch off nearby fans you are allowed to switch off.
2. Put on the test headphones and follow the instructions.
3. Press the button each time you hear a tone, even a very faint one.
4. Test both ears.
5. The result goes to the ground for review.

**How often.** Default: monthly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly (if an audiometer is carried; otherwise hearing questions only) |
| Transit | monthly (if an audiometer is carried; otherwise hearing questions only) |
| Moon base | monthly (if an audiometer is carried; otherwise hearing questions only) |
| Mars base | monthly (if an audiometer is carried; otherwise hearing questions only) |

**Do it more often when:**

- Ringing in the ears or muffled hearing, especially after a loud event. Then: as needed (test within a day and tell the crew medical officer). (team default; RT05)
- Noise dosimetry above the flight rule level. Then: hearing test at the next opportunity. (team default; RT08, RT07)

**Body region.** ear.

**Sources, quoted exactly.**

- RT06: "OOHA was implemented to routinely monitor the hearing thresholds of crewmembers who are on orbit for longer than 30 days."
- RT06: "Flight Surgeons on ground monitor these test for significant hearing changes while aboard."
- RT06: "OOHA Data indicates a low frequency hearing shift in 40% of all tests and 60% of all crewmembers."
- RT01: "Hearing assessments"
- RT02: "Hearing conservation and protection (as required in [V2 9057] Hearing Protection Provision), including periodic monitoring."
- RT05: "Noise exposure is generally considered to affect hearing, but it can also cause other physiological effects (e.g., irritation, headaches, and degradation in sleep and relaxation)."

**Note.** No source read here gives the ISS interval for the hearing assessment. Frequency to be set by the flight surgeon; monthly is the team default.

<a id="noise-dosimetry"></a>

### 15. Crew-worn noise dosimeter (24 hours)

**What it is.** Wear the small noise monitor on your clothing for a full day and night, then hand it in for download.

**Why it matters.** People get used to noise and stop noticing it, but it still harms hearing and sleep. Only a measurement shows your real exposure and whether you need hearing protection.

**Who.** You, a crewmate and the ground (flight surgeon or specialists).

**Instrument.** Acoustic dosimeter (ISS class); not in this kit. Habitat sound level sensors run continuously.

**Time.** About 10 minutes (sourced from RT07: the monitor is worn for 24 hours; 10 minutes of handling is a team estimate).

**Steps.**

1. Before breakfast, clip the monitor to your clothing near your shoulder and start it.
2. Wear it all day. Do not cover the microphone.
3. Just before sleep, a crewmate swaps its battery and gives it back to you.
4. Keep it on through the night, near your head.
5. In the morning, stop it before your normal activities and hand it in.
6. Wear your hearing protection as usual. The app notes when you wore it.

**How often.** Default: every 60 days. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | every 60 days |
| Transit | every 60 days (team default, same as ISS) |
| Moon base | every 60 days (team default, same as ISS) |
| Mars base | every 60 days (team default, same as ISS) |

**Do it more often when:**

- New noisy equipment or a habitat change. Then: one 24 hour session. (team default; RT05)
- Crew report noise that disturbs sleep or makes speech hard. Then: one 24 hour session. (team default; RT05)
- A hearing change on the hearing check. Then: one 24 hour session for that person. (team default; RT06)

**Body region.** ear.

**Sources, quoted exactly.**

- RT07: "These activities are generally performed once every other month."
- RT08: "Acoustic dosimeter measurements, obtained when the crewmember wears the dosimeter for 24-hour periods, are conducted onboard ISS every 60 days and compared to ISS flight rules."
- RT07: "the crewmember dons the Acoustic Monitor and starts the measurement before breakfast of the day of the planned activity."
- RT07: "Just before sleep, a crewmember temporarily collects the dosimeters for a battery change-out and then redeploys to the same crewmembers."
- RT08: "If the LEQ16 is higher than 72 dBA, then the crewmembers are directed to wear appropriate HPDs during the activities where high noise exposure levels are present."
- RT03: "Noise exposure levels shall be monitored and quantified for each crewmember as needed for crew health and safety."
- RT01: "Provide crew acoustic dosimetry and monitoring schedule including high noise level reporting and provide countermeasures/hearing protection."
- RT05: "Even noise that the crew does not perceive as harmful may cause deleterious effects, as the crew may become desensitized to elevated noise levels"

**Note.** RT08 also mentions a nominal monthly session; RT08's abstract and RT07 (the later paper) both give about every 60 days, which is used here.

## Radiation

<a id="radiation-dosimeter"></a>

### 16. Personal radiation dosimeter

**What it is.** Wear your dosimeter at all times, check each day that it is on and working, and read it before and after every spacewalk.

**Why it matters.** Space radiation cannot be felt. Your dosimeter keeps your personal dose record, and an alarm tells everyone when to take shelter during a solar storm.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** Personal dosimeter (ISS class); habitat radiation monitor with alarm; not in this kit.

**Time.** About 1 minutes (team estimate).

**Steps.**

1. Wear the dosimeter where the program tells you, inside the habitat and during spacewalks.
2. Each day, tap to confirm it is on you and working.
3. Before and after each spacewalk, read it and save the number.
4. If the radiation alarm sounds, go to the shelter area at once and follow the shelter procedure.
5. During a shelter period, read your dosimeter when asked and save each reading.

**How often.** Default: continuous. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | continuous (personal monitoring per the program's radiation plan) |
| Transit | continuous (personal monitoring required) |
| Moon base | continuous (personal monitoring required, including spacewalks) |
| Mars base | continuous (personal monitoring required, including spacewalks) |

**Do it more often when:**

- Solar particle event alert (sensor_limit). Then: shelter; read the dosimeter at the start, every hour (team default) and at the end. (team default; RT03, RT09)
- Before and after each spacewalk. Then: each spacewalk. (sourced; RT18, RT03)
- Nausea, vomiting or unusual tiredness after a radiation event. Then: as needed (tell the crew medical officer and the ground). (team default; RT09)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT03: "Individual dose monitoring must occur inside vehicles, habitats, and during extravehicular activity."
- RT03: "These devices need to be compact and require minimal crew intervention for dose measurement to ensure crew compliance."
- RT03: "The radiation monitoring system shall alert the crew and operations teams when radiation exposure rates exceed predefined thresholds."
- RT03: "The crew must be able to follow sheltering procedures planned in the event that dose rates exceed predefined thresholds."
- RT09: "Countermeasures: Shielding, monitoring, accurate event determination, dosimetry, and timely reporting to alert crew to seek shelter are essential for crew safety."
- RT09: "Acute radiation syndromes, such as nausea, vomiting, and fatigue, may occur due to in mission radiation exposure, as well as skin injuries"
- RT09: "Real-time alert dosimetry provides a very high level of protection if EVA termination occurs within 2 hrs."
- RT18: "Pille Pre- and Post-EVA"

**Note.** Wearing is continuous (sourced). The daily confirmation tap and the hourly reading during a shelter period are team defaults; the radiation health officer sets the real schedule.

## Spacewalks and surface dust

<a id="pre-eva-check"></a>

### 17. Before a spacewalk

**What it is.** In the day before a spacewalk, do a short health check and a private talk with the flight surgeon, who must clear you.

**Why it matters.** A spacewalk is hard physical work in a pressurized suit. A cold, short sleep, dehydration or an injury can turn into a real problem outside, so each person is cleared first by the medical officer aboard, as in the onboard procedure.

**Who.** You, the crew medical officer and the ground (flight surgeon or specialists).

**Instrument.** Kit cuff, oximeter, thermometer, camera station; reaction test; personal dosimeter.

**Time.** About 20 minutes (team estimate).

**Steps.**

1. Answer the pre-spacewalk questions: new symptoms, ear or sinus problems, sleep, pain, mood.
2. Take pulse, blood pressure, blood oxygen and temperature.
3. Drink to stay well hydrated, as prescribed.
4. Check your hands: trim nails short and note any sore fingertips or nails.
5. Read and save your dosimeter.
6. The medical officer clears you before suiting up, as in the onboard procedure. When the link is up, also join the private medical conference within 24 hours before; the flight surgeon's advice goes into that decision. Log it for the ground.

**How often.** Default: each spacewalk. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | each spacewalk |
| Transit | each spacewalk (contingency spacewalks only) |
| Moon base | each spacewalk (surface walks may be frequent; one check per walk) |
| Mars base | each spacewalk (the ground's reply is delayed; the medical officer clears each person aboard, and the log goes at the next contact) |

**Do it more often when:**

- Any new symptom, short sleep or illness before a planned spacewalk. Then: as needed (the medical officer checks again before suiting up; log it for the ground). (team default; RT02)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT02: "All crewmembers shall be medically cleared to perform an EVA by ground medical support personnel prior to each EVA."
- RT02: "Flight Surgeon on ground evaluates available data from recent medical evaluations, fitness sessions, and other metrics of health and performance to determine readiness for EVA."
- RT04: "Prior to each EVA; conducted within 24 hours of EVA suit donning."
- RT18: "PHS EVA (Includes PHS Pre-EVA and PHS post-EVA)"
- RT10: "Several factors can increase an individual's susceptibility to DCS, including the presence of a patent foramen ovale (PFO), body fat %, hydration, and exercise type."

**Note.** Trimming nails before a spacewalk is a team suggestion tied to the nail injuries in RT12, not a sourced procedure.

<a id="post-eva-check"></a>

### 18. After a spacewalk: bends, hands, shoulders

**What it is.** After the suit comes off, check for signs of the bends, look at your hands and nails, note shoulder pain, and talk privately with the flight surgeon within a day.

**Why it matters.** Decompression sickness (the bends) can start hours after a spacewalk, and people tend to play down mild symptoms. Gloves often injure fingertips and nails, and suits strain shoulders.

**Who.** You, a crewmate, the crew medical officer and the ground (flight surgeon or specialists).

**Instrument.** The app; kit oximeter and cuff; camera of the tablet for hand photos; personal dosimeter.

**Time.** About 15 minutes (team estimate).

**Steps.**

1. Report at once, never wait: joint or muscle pain, itching, a blotchy rash, numbness, tingling, weakness, dizziness, trouble thinking, or trouble breathing.
2. If any of these appear, press Someone needs help. Do not do hard exercise.
3. Look at each finger and nail. Note lifted or dark nails, blisters, sore knuckles or numb fingertips. Take a photo if asked.
4. Move each shoulder through its full range. Note pain or catching.
5. Take pulse and blood oxygen. Read and save your dosimeter.
6. On the Moon or Mars, also do the dust check.
7. Join the private medical conference within 24 hours.

**How often.** Default: each spacewalk. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | each spacewalk |
| Transit | each spacewalk |
| Moon base | each spacewalk (plus the dust check) |
| Mars base | each spacewalk (plus the dust check) |

**Do it more often when:**

- Any symptom that could be decompression sickness. Then: as needed (emergency route at once). (sourced; RT11, RT10)
- No symptoms at the first check. Then: repeat the bends questions that evening and next morning. (team default; RT11)
- Nail, finger or shoulder injury found. Then: daily (look and photo each day until healed; crew medical officer decides). (team default; RT12, RT33)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT11: "crewmember who participate in hyperbaric or hypobaric operations are required to immediately report the onset of any DCS symptoms"
- RT11: "Even if DCS symptoms have occurred, there may be a bias to not report mild discomfort in this type of an operational setting, especially if symptoms are not limiting."
- RT11: "Mean DCS onset time was 120 min"
- RT10: "DCS is typically diagnosed based on symptom presentation"
- RT10: "vigorous or resistive exercises after decompression can exacerbate bubble growth"
- RT12: "Hand injury symptoms of primary concern were fingernail delamination, which was thought to result from excess moisture in the EVA gloves and axial loading of the fingertips"
- RT12: "Many astronauts described the gloves causing small blisters and pain across their metacarpophalangeal (MCP) joints."
- RT33: "Astronauts in particular have experienced a significantly higher number of these injuries from shoulder overuse as a result of the rigid biomechanics of wearing extravehicular activity (EVA) suits during training activities."
- RT04: "Following each EVA; conducted within 24 hours following EVA suit removal."
- RT03: "need to be monitored after EVA"

**Note.** The 120 minute mean comes from ground chamber tests (RT11), not spacewalks. How long to keep watching after a spacewalk is to be set by the flight surgeon; the evening and next morning repeat is a team default.

<a id="dust-exposure-check"></a>

### 19. Eyes, skin and breathing after dust exposure

**What it is.** After a surface walk or any dust in the cabin, rinse your eyes if they sting, check your skin and nails, and note any sneezing, congestion or cough.

**Why it matters.** Moon and Mars dust is sharp and clings to everything. Apollo crews got sore eyes, blocked noses and dust in the cabin. Mars dust also carries chemicals such as perchlorate.

**Who.** You and a crewmate.

**Instrument.** Eyewash, saline, wipes; the app; habitat dust monitor.

**Time.** About 10 minutes (team estimate).

**Steps.**

1. Clean dust off yourself and your gear before entering the living area, as the procedure says.
2. If your eyes sting or feel gritty, do not rub them. Rinse with the eyewash or saline.
3. Look at your skin, under your nails and around the wrists for redness or scratches.
4. Note sneezing, blocked nose, sore throat or cough.
5. Take your breathing rate if you are coughing.
6. Tell the crew medical officer about any eye pain that stays after rinsing.

**How often.** Default: on these events: each surface walk or cabin dust release. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | as needed (only if dust is brought aboard, for example by a vehicle returning from a surface) |
| Transit | as needed (only if dust is brought aboard) |
| Moon base | on these events: each surface walk or habitat dust alert |
| Mars base | on these events: each surface walk or habitat dust alert |

**Do it more often when:**

- Habitat dust monitor alert (sensor_limit). Then: everyone in that area does the check. (team default; RT03)
- Eye irritation, cough or congestion that persists. Then: daily (until it clears; crew medical officer decides). (team default; RT13)

**Body region.** eyes.

**Sources, quoted exactly.**

- RT13: "Have the ability to clean body and rinse eyes/nasal passages of dust."
- RT13: "In each case, ocular irritation occurred that required copious saline irrigation to treat."
- RT13: "Nasal congestion was experienced by most crewmembers"
- RT13: "Risk of eye abrasion and skin irritation (e.g., EVA gloves, under fingernails) are relevant to lunar dust"
- RT13: "Dust monitoring is likely to be needed situationally (e.g., assessing cabin clearing after EVA introduction, anomaly assessment), rather than as a continuous monitored environmental parameter."
- RT03: "The vehicle shall monitor celestial dust and alert the crew locally and remotely when they are approaching defined limits."
- RT03: "Eye wash capability for particulate events is expected, especially for lunar and planetary missions, as there is an increased risk of exposure to dust and regolith on the lunar or other planetary surfaces."
- RT03: "Data from Mars rover missions indicate the presence of certain toxic substances in dust (e.g., perchlorate, manganese) that can exert their own separate chemical-specific toxic effects."

**Note.** No source states a crew self-check cadence after dust exposure. After each surface walk is the team default.

## Mind and private conferences

<a id="behavioral-self-check"></a>

### 20. Weekly wellbeing self-check

**What it is.** Once a week, rate sleep, tiredness, workload, morale, relationships and mood, privately, to bring to your private conferences.

**Why it matters.** Isolation, workload and distance from home wear people down slowly. The same topics flight psychologists ask about every two weeks help you notice changes yourself.

**Who.** You.

**Instrument.** The app (private; never shared without your choice).

**Time.** About 5 minutes (team estimate).

**Steps.**

1. Find a private moment.
2. Rate each topic: sleep, tiredness, workload, your morale, the crew's morale, relationships with the crew and the ground, mood, family and personal life, and the habitat (food, noise, space).
3. Add a note if you want. It stays private.
4. Choose whether to share any of it at your next private conference.
5. If you have thoughts of harming yourself, press Someone needs help now.

**How often.** Default: weekly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | weekly |
| Transit | weekly (conferences are delayed, so the self-check carries more weight) |
| Moon base | weekly |
| Mars base | weekly (conferences are delayed, so the self-check carries more weight) |

**Do it more often when:**

- Mood below your usual on several daily checks (how many to be set by clinical review). Then: every 3 days (and offer an extra private conference). (team default; RT02)
- The death of a crewmember. Then: as needed (extra private psychological conferences, alone or as a group). (sourced; RT02)
- A crew emergency, a serious injury, or bad news from home. Then: as needed (offer an extra private conference). (team default)

**Body region.** head.

**Sources, quoted exactly.**

- RT15: "Sleep (duration and quality) and sleep shift issues"
- RT15: "Workload and pace of work"
- RT15: "Individual and crew morale"
- RT15: "Family and personal relationships"
- RT15: "Environment and habitability issues, including food"
- RT02: "Coordinating extra Private Psychological Conferences (PPCs), either individually or as a group."
- C14: "For missions beyond 6 months and/or beyond LEO, additional minimally obtrusive measures that objectively evaluate psychological and behavioral states within the mission context should be provided."

**Note.** The topics are the ISS private psychological conference topics (RT15). A weekly self-check is a team default; it is not a diagnosis and never scores the person.

<a id="private-medical-conference"></a>

### 21. Private medical conference with the flight surgeon

**What it is.** A private, unrecorded talk with your flight surgeon about your health, each week and around key events.

**Why it matters.** It is the routine way to raise anything about your health in private, and for the flight surgeon to check in before and after demanding events.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** Private audio or video link; store-and-forward messages when there is a delay.

**Time.** About 15 minutes (sourced from RT04: 15 minutes per crewmember).

**Steps.**

1. Go to a private space where no one can see or hear you.
2. Open the app's summary and choose what to share.
3. Talk through anything new: symptoms, sleep, medicines, worries.
4. Note any advice in the app.
5. On Mars or in deep space, record your message and send it; the reply comes later.

**How often.** Default: weekly. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | weekly (live) |
| Transit | weekly (store-and-forward when the delay is long) |
| Moon base | weekly (live with a short delay) |
| Mars base | weekly (store-and-forward) |

**Do it more often when:**

- Launch day and the first five days after launch or docking. Then: daily. (sourced; RT04)
- The first five days after landing at a Moon or Mars base. Then: daily. (team default)
- Before and after each spacewalk (within 24 hours). Then: each spacewalk. (sourced; RT04)
- The five days before entry and the morning of landing. Then: return (daily). (sourced; RT04)
- You, a crewmate, the commander or the flight director asks for one. Then: as needed. (sourced; RT04, RT19)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT04: "Daily for the first five days after docking (15 minutes for each crewmember)."
- RT04: "Weekly after the first five flight days (15 minutes each crewmember)."
- RT04: "crew have daily private medical conferences (PMC) on launch day and daily for 5 days per the NASA Medical Operations Requirements Document (MORD)."
- RT04: "Prior to each EVA; conducted within 24 hours of EVA suit donning."
- RT04: "Following each EVA; conducted within 24 hours following EVA suit removal."
- RT04: "Daily beginning five days prior to entry and on the morning of entry/landing."
- RT04: "At any time during the mission if requested by the FD, Mission Commander or other crewmember, the CS or his designee."
- RT04: "Other programs may follow different monitoring and treatment schedules."
- RT16: "The crewmember additionally has weekly private medical conferences (PMC) with the crew surgeon."
- RT02: "Private medical communications shall be scheduled on a routine basis, as determined by the Flight Surgeon, at a frequency dictated for short- or long-duration missions."
- RT02: "Real time communications are preferred for all private medical communications, however when missions have communication delays, different modalities can be considered (e.g., stored/forward communications)."
- RT01: "Ensure private (visual and auditory) location for FS to conduct PMCs with access to voice loops."

**Note.** The schedule is the ISS one quoted from the ISS medical operations requirements in RT04. Other programs set their own (RT02).

<a id="private-psych-conference"></a>

### 22. Private psychological conference

**What it is.** A private, unrecorded talk with the flight psychologist or psychiatrist every two weeks.

**Why it matters.** Living and working in a small crew far from home is hard. A regular private talk helps with sleep, stress, relationships and family worries before they grow.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** Private video link; store-and-forward messages when there is a delay.

**Time.** About 20 minutes (sourced from RT15: typically 15 to 20 minutes).

**Steps.**

1. Go to a private space.
2. Look at your weekly wellbeing self-check if you want a reminder of what to raise.
3. Talk about whatever matters to you. The usual topics are sleep, tiredness, workload, morale, crew and ground relationships, mood, family and the habitat.
4. Nothing is recorded in the app unless you choose to add a note.

**How often.** Default: every 14 days. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | every 14 days (live, usually at the weekend) |
| Transit | every 14 days (store-and-forward when the delay is long) |
| Moon base | every 14 days |
| Mars base | every 14 days (store-and-forward) |

**Do it more often when:**

- The death of a crewmember. Then: as needed (extra conferences, alone or as a group). (sourced; RT02)
- A crew emergency, a serious injury, a major event at home, or you ask for one. Then: as needed. (team default)

**Body region.** head.

**Sources, quoted exactly.**

- RT15: "Performed every two weeks during ISS expeditions (all crew members)"
- RT15: "Typically lasts 15-20 minutes depending on subject matter discussed and mission phase"
- RT15: "Contents are non-recorded private and confidential medical events"
- RT02: "A PPC shall be scheduled on a routine basis, as determined by the Behavioral Health Provider, at a frequency dictated for short- or long-duration missions."
- RT02: "Coordinating extra Private Psychological Conferences (PPCs), either individually or as a group."

**Note.** Every two weeks is ISS practice as described in 2017 (RT15). Each program's behavioral health provider sets its own schedule (RT02).

## Exams, medicines and drills

<a id="periodic-health-exam"></a>

### 23. Monthly health exam (periodic health status)

**What it is.** Once a month, the crew medical officer examines you and records your vital signs and ear images for the flight surgeon.

**Why it matters.** A regular exam by a trained crewmate catches things you would not notice yourself and gives the flight surgeon a consistent record to compare.

**Who.** The crew medical officer, you and the ground (flight surgeon or specialists).

**Instrument.** Kit cuff, oximeter, thermometer; otoscope and exam form (ISS class).

**Time.** About 30 minutes (team estimate).

**Steps.**

1. The crew medical officer opens the exam form in the app.
2. Pulse, blood pressure, temperature and blood oxygen are taken as in their own items.
3. The officer looks in each ear with the otoscope and saves the images.
4. You answer the exam questions; the officer fills in what they see.
5. The exam is sent to the flight surgeon, who compares it with earlier exams and replies.

**How often.** Default: monthly. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly |
| Transit | monthly (ground review is delayed) |
| Moon base | monthly |
| Mars base | monthly (ground review is delayed) |

**Do it more often when:**

- Before and after each spacewalk. Then: each spacewalk. (sourced; RT18, RT01)
- An illness or injury. Then: as needed (as the crew medical officer decides). (team default; RT01)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT16: "Periodic Health Status (PHS) examinations requires crew members to perform these exams on each other monthly."
- RT17: "Heart rate, blood pressure, temperature, blood oxygen levels, ear and tympanic membrane images (via otoscope), open-ended exam form, are all collected data packaged as the crew member's patient file"
- RT17: "A Flight Surgeon reviews the data, compares it to previously collected data, updates the patient file with recommendations"
- RT01: "In-mission crew are monitored via in-flight medical exam/evaluations/conferences."
- RT18: "PHS EVA (Includes PHS Pre-EVA and PHS post-EVA)"
- RT02: "Periodic monitoring of general health status."

**Note.** The monthly cadence is from a 2006 description of ISS practice (RT16). The 30 minutes is a team estimate.

<a id="dental-skin-check"></a>

### 24. Mouth, teeth and skin check

**What it is.** Once a month, look at your teeth, gums and skin with a mirror and light, and note anything new.

**Why it matters.** Dental abscesses and skin rashes have happened on the ISS. Far from a dentist, a small problem found early is easier to manage.

**Who.** You and a crewmate.

**Instrument.** Mirror, penlight; tablet camera for photos.

**Time.** About 5 minutes (team estimate).

**Steps.**

1. Look at your gums and teeth. Note bleeding, swelling, a broken tooth or a sore that will not heal.
2. Note any tooth pain, especially with hot or cold.
3. Look over your skin, including feet and under straps. Note rashes, sores, dry cracked skin or a changing mole.
4. Ask a crewmate to look at your back.
5. Take a photo of anything new and save it.

**How often.** Default: monthly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly |
| Transit | monthly |
| Moon base | monthly |
| Mars base | monthly |

**Do it more often when:**

- Tooth pain, facial swelling or a gum problem. Then: as needed (tell the crew medical officer now). (team default; RT19)
- A rash or skin sore. Then: daily (photo each day until it clears; crew medical officer decides). (team default; RT19)

**Body region.** head.

**Sources, quoted exactly.**

- RT02: "Periodic monitoring and treatment of dental health as appropriate."
- RT02: "Preventive dental care."
- RT19: "ISS Crew Surgeons have handled many medical conditions on orbit; including skin rashes, dental abscesses, lacerations"

**Note.** The standard asks for periodic dental monitoring without an interval (RT02). Frequency to be set by the flight surgeon; monthly is the team default.

<a id="medical-kit-inventory"></a>

### 25. Medicines and medical kit check

**What it is.** Once a month, check that the medical kit is complete, in date and stowed where the list says.

**Why it matters.** In an emergency there is no time to search. On long missions without resupply, medicines can pass their expiry dates, so the crew needs to know early what is running out.

**Who.** The crew medical officer and a crewmate.

**Instrument.** Medical kits; the inventory list in the app.

**Time.** About 20 minutes (team estimate).

**Steps.**

1. Open the kit list in the app.
2. Check each emergency item first: present, sealed, working.
3. Check expiry dates. Flag anything expiring before the next resupply or the end of the mission.
4. Check that used items were logged and replaced.
5. Check that personal medicines match each person's list.
6. Send the flags to the ground.

**How often.** Default: monthly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly (plus after each resupply) |
| Transit | monthly (no resupply; expiry planning matters more) |
| Moon base | monthly |
| Mars base | monthly (no resupply; expiry planning matters more) |

**Do it more often when:**

- Any item is used from a kit. Then: log and restock the same day. (team default; RT03)
- A resupply arrives. Then: unpack and update the list. (sourced; RT18)
- A solar particle event. Then: check liquid medicines for changes and log it for the ground. (team default; RT22)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT02: "inventory monitoring/maintenance"
- RT03: "The system shall provide an inventory management system to track the locations and quantities of items (including hazardous trash) throughout the mission."
- RT01: "Establish responsibility for the procedures, resupply, and tracking of inventory."
- RT22: "Expiration Concerns include long duration spaceflight planned for 1-5 years which will be beyond the current average medication expiration dates of 1-2 years."
- RT22: "Medications aboard the International Space Station (ISS) have maintained usability in LEO gravity and radiation conditions and are restocked before their expiration dates."
- RT22: "especially in liquid pharmaceuticals thought to be more vulnerable to radiation exposure."
- RT18: "IMAK Medical Kit Unpack (Resupply and Crew"

**Note.** No source read here states how often the crew checks the kit. Frequency to be set by the flight surgeon; monthly is the team default.

<a id="emergency-drill"></a>

### 26. Medical emergency drill

**What it is.** Practise a medical emergency as a crew: who does what, where the kit is, and how to use the Someone needs help screen.

**Why it matters.** Skills fade between training and a real emergency, which may come months later. A drill keeps everyone ready, not only the medical officer.

**Who.** You, a crewmate, the crew medical officer and the ground (flight surgeon or specialists).

**Instrument.** Emergency medical kit; the app's Someone needs help screen in drill mode.

**Skills practised.** `pulse_check_wrist` and `cpr` (`skill_ids`), from the skills catalog ([CONTRACTS.md](CONTRACTS.md) section 23). A drill runs the same teach steps and camera checks as a real event. Completing it updates each participant's skill record and counts toward the practice patches ([UX-DESIGN.md](UX-DESIGN.md) section 11); the drill's camera numbers are never stored, scored or ranked, and a drill never touches health records, the streak or the crew goal.

**Time.** About 60 minutes (team estimate).

**Steps.**

1. The crew medical officer picks a scenario from the drill list.
2. One person plays the patient. Another presses Someone needs help in drill mode.
3. Run the fixed emergency steps. Fetch and open the real kit.
4. Practise each skill with its teach steps and camera checks: the pulse check on a crewmate's real wrist, chest compressions on a manikin, if listed. The camera's numbers show live and are not stored.
5. Practise the log for the ground: preview it and press Tell the ground. It leaves at the next contact, and nothing in the drill waits for it.
6. Debrief: what was slow, what was missing. Log it. Completing the drill updates each participant's skill record and counts toward the practice patches.

**How often.** Default: monthly. Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly |
| Transit | monthly |
| Moon base | monthly |
| Mars base | monthly |

**Do it more often when:**

- A new module or a visiting vehicle. Then: emergency response review. (sourced; RT18)
- After a real medical emergency. Then: debrief and repeat the drill. (team default)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT02: "Additionally, all other crewmembers become familiar with the use of in-mission medical equipment and protocols."
- RT18: "CheCS Emergency Health Maintenance Sys Contingency Drill"
- RT18: "Integrated all-crew Emergency simulations"
- RT18: "Crew Medical Officer CMO Proficiency"
- RT18: "Delta Emergency Response Review for New modules or Visiting vehicles"
- RT01: "Program designates/trains two CMOs for each increment."

**Note.** ISS plans emergency drills and medical officer proficiency sessions (RT18), but no source read here gives their interval. Frequency to be set by the flight surgeon; monthly is the team default.

## Habitat sampling and cleaning

<a id="water-sampling"></a>

### 27. Drinking water sampling

**What it is.** Once a month, take a water sample from the dispenser with the sampling kit and process or stow it as the procedure says.

**Why it matters.** Recycled water can grow bacteria. Regular samples catch a problem before people get sick.

**Who.** A crewmate and the ground (flight surgeon or specialists).

**Instrument.** Water sampling kit (ISS class, for example microbial capture device and coliform test); not in this kit. The habitat water quality sensor runs continuously.

**Time.** About 20 minutes (team estimate).

**Steps.**

1. Collect the sample from the dispenser port named in the procedure. Alternate hot and ambient ports month to month.
2. Label it with date, time and port.
3. Process it with the kit or stow it for return, as the procedure says.
4. Log the sample and any result in the app.

**How often.** Default: monthly. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | monthly |
| Transit | monthly (team default, same as ISS) |
| Moon base | monthly (team default, same as ISS) |
| Mars base | monthly (plus after any change of water source; local water may carry perchlorate) |

**Do it more often when:**

- The water quality sensor approaches a limit (sensor_limit). Then: sample now. (team default; RT03)
- Water tastes, smells or looks different, or several crew have stomach upsets. Then: sample now and log it for the ground. (team default)
- A count above limits. Then: every 7 days (until two clean results). (team default)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT20: "Samples are collected from the Potable Water Dispenser (PWD) on a monthly basis, alternating between hot and ambient ports"
- RT03: "The system shall provide the capability to monitor water quality and notify the crew locally and remotely when parameters are approaching defined limits."
- RT03: "In-flight sampling capability supports real-time contaminant monitoring and remediation of stored or regenerated water systems as needed for long-duration missions."
- RT03: "perchlorate dissolution from Mars regolith into water"
- RT01: "Perform micro analysis of foods, water, cabin air, and internal surfaces as required by program."

**Note.** Monthly microbial sampling is ISS practice described in 2014 (RT20). Chemical water analysis on ISS runs on its own schedule, which was not confirmed from a saved source.

<a id="air-sampling"></a>

### 28. Cabin air sampling

**What it is.** Every three months, run the microbial air sampler in each module, and take an air sample before the return trip.

**Why it matters.** Moulds and bacteria in the air can cause infections and allergies in a closed cabin. A return air sample records what the crew breathed.

**Who.** A crewmate and the ground (flight surgeon or specialists).

**Instrument.** Microbial air sampler kit and air sample containers (ISS class); not in this kit. Habitat CO2, oxygen and trace gas sensors run continuously.

**Time.** About 20 minutes (team estimate).

**Steps.**

1. Take the air sampler to each module on the list.
2. Run it for the time the procedure sets, away from vents.
3. Label and stow each sample plate or container.
4. Log the module, time and any smell or visible growth.

**How often.** Default: every 90 days. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | every 90 days (microbial air sampling; plus a return air sample) |
| Transit | every 90 days (microbial air sampling; plus a return air sample) |
| Moon base | every 90 days (microbial air sampling; plus a return air sample) |
| Mars base | every 90 days (microbial air sampling; plus a return air sample) |

**Do it more often when:**

- A strange smell, smoke, a chemical leak or a contamination event. Then: sample as the ground directs. (sourced; RT01)
- Before the vehicle's return trip. Then: return (return air sample). (sourced; RT01)
- A count above limits, or visible mould. Then: every 30 days (until clean). (team default)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT20: "6 modules sampled quarterly"
- RT01: "Environmental sampling (including return air sample)"
- RT01: "Contingency Ensure monitoring during off-nominal situations for water, air quality, toxic substances, visiting vehicle cross-contamination, target contaminants."
- RT18: "Microbial Air Sampling"

**Note.** Quarterly microbial air sampling is ISS practice described in 2014 (RT20).

<a id="surface-sampling"></a>

### 29. Surface microbial sampling

**What it is.** Every three months, press sample slides or swabs on the listed surfaces and stow them.

**Why it matters.** Surfaces people touch carry germs between crewmembers and can grow mould, especially where it is damp.

**Who.** A crewmate and the ground (flight surgeon or specialists).

**Instrument.** Surface sampler kit, contact slides and swabs (ISS class); not in this kit.

**Time.** About 30 minutes (team estimate).

**Steps.**

1. Go to each surface on the list.
2. Press a contact slide on flat surfaces. Use a wetted swab on uneven ones.
3. Label and stow each sample.
4. Log where you sampled and anything you saw, such as damp or growth.

**How often.** Default: every 90 days. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | every 90 days |
| Transit | every 90 days |
| Moon base | every 90 days |
| Mars base | every 90 days |

**Do it more often when:**

- Visible mould, damp or condensation. Then: sample and clean. (team default; RT21)
- A count above limits, or several crew with skin or stomach infections. Then: every 30 days (until clean). (team default)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT20: "US Segment sampled quarterly"
- RT20: "Swabs wetted with sterile saline used for uneven surfaces"
- RT03: "The system shall provide surfaces that are microbiologically safe for human contact."

**Note.** Quarterly surface sampling is ISS practice described in 2014 (RT20).

<a id="housekeeping-clean"></a>

### 30. Weekly cleaning

**What it is.** Once a week, the crew cleans surfaces, handholds and corners together.

**Why it matters.** Cleaning keeps germs and mould down in a closed habitat. On the Moon or Mars it also removes dust tracked in from outside.

**Who.** You and a crewmate.

**Instrument.** Vacuum, disinfectant wipes, towels.

**Time.** About 180 minutes (sourced from RT21: around 3 hours).

**Steps.**

1. Split the habitat into areas, one per person.
2. Wipe handholds, tables, switches and the toilet area with disinfectant.
3. Vacuum filters, vents and corners where things collect.
4. Report damp spots, growth or dust build-up in the app.

**How often.** Default: weekly. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | weekly |
| Transit | weekly |
| Moon base | weekly (plus dust cleaning after each surface walk) |
| Mars base | weekly (plus dust cleaning after each surface walk) |

**Do it more often when:**

- Dust brought in after a surface walk. Then: clean the entry area. (team default; RT13)
- Visible growth or a spill. Then: as needed. (team default; RT21)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT21: "NASA astronauts on the ISS clean surfaces and crevices of the habitat every Saturday for around 3 hours."
- RT13: "Lunar crews stated that symptoms resolved on lunar surface after initial exposure to dust only to return when reentering the crew module as the particulates floated throughout the cabin in microgravity."

## Mission phases

<a id="preflight-baselines"></a>

### 31. Pre-flight baselines

**What it is.** Before launch, use the app on the ground so it learns your usual readings, and record the baseline tests the medical team does.

**Why it matters.** Every in-flight warning compares you with yourself. Without a good baseline from before flight, the app cannot tell what is normal for you.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** The whole kit; ground clinic tests imported as reports.

**Time.** About 30 minutes (team estimate).

**Steps.**

1. Do the daily check every day for at least the last 2 weeks before launch.
2. Take resting pulse, breathing rate, blood pressure, blood oxygen and temperature several times in those weeks.
3. Do the reaction test about monthly in the last six months, and more often in the final weeks.
4. Do the near vision and Amsler grid self-check, and record the eye exam results.
5. Import the baseline reports: eye exams, hearing test, fitness test, dental exam, cognitive baseline.
6. Set your medicines, allergies and conditions, or decline each field.

**How often.** Default: pre-flight. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | pre-flight |
| Transit | pre-flight |
| Moon base | pre-flight |
| Mars base | pre-flight |

**Do it more often when:**

- A baseline reading is missing or failed. Then: as needed (repeat before launch). (team default)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT02: "Annual and pre-flight physicals/periodic health evaluations."
- RT02: "Preventive dental care."
- RT25: "pre-mission data collection began 180 days prior to launch and repeated every 30 days until one week prior to launch, during which astronauts completed testing."
- RT14: "Pre-flight baseline performed at L-60"
- S12: "MRI brain and orbits, visual acuity (distance and near), refraction (manifest and cycloplegic), Amsler grid"
- RT15: "ISS Preflight Assessments (L-365/180/60)"

**Note.** The 2 weeks of daily checks before launch is a team default. Other timings are quoted from their sources.

<a id="preflight-quarantine"></a>

### 32. Pre-flight quarantine (health stabilization)

**What it is.** In the 14 days before launch, limit contact to approved people and report any sign of illness each day.

**Why it matters.** An infection caught just before launch can spread through the whole crew in a closed vehicle. Quarantine and vaccinations cut that risk.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** The app daily check; kit thermometer.

**Time.** About 3 minutes (team estimate).

**Steps.**

1. Follow the quarantine rules: only approved contacts, protective equipment as required.
2. Do the daily check each day, with temperature.
3. Report sore throat, cough, fever, stomach upset or a rash the same day.
4. Report any contact with someone who is ill.

**How often.** Default: pre-flight (daily for the 14 days before launch). Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | pre-flight (daily for the 14 days before launch) |
| Transit | pre-flight (daily for the 14 days before launch) |
| Moon base | pre-flight (daily for the 14 days before launch) |
| Mars base | pre-flight (daily for the 14 days before launch) |

**Do it more often when:**

- Any symptom of infection or a known exposure. Then: as needed (tell the flight surgeon the same day). (team default; RT24)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT24: "Current NASA/JSC protocol mandates the HSP begin 14 days prior to launch."
- RT24: "Through a combination of these two factors, in-flight infectious diseases (especially upper respiratory and enteric infections) can be mitigated."
- RT02: "Pre-mission Health-Stabilization Program (HSP) to reduce the likelihood of contracting an infectious disease before launch."

**Note.** The daily symptom and temperature report during quarantine is a team default.

<a id="early-flight-adaptation"></a>

### 33. First days in space: adaptation check

**What it is.** For the first five days, answer a short daily check on nausea, headache, stuffy nose, back pain and passing urine.

**Why it matters.** Most people feel sick, congested or sore in the first days as the body adapts to weightlessness. Most of it passes in a few days, but vomiting and not passing urine need help.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** The app; the daily private medical conference.

**Time.** About 3 minutes (team estimate).

**Steps.**

1. Answer each question: nausea or vomiting, headache, stuffy nose or puffy face, back pain, dizziness when you move your head, trouble passing urine, appetite.
2. Rate how much each one affects your work.
3. Sip fluids even if you feel sick.
4. If you cannot pass urine, or keep vomiting, tell the crew medical officer now.
5. Bring the answers to the daily private medical conference.

**How often.** Default: early flight (daily for the first 5 days). Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | early flight (daily for 5 days after docking) |
| Transit | early flight (daily for 5 days after launch) |
| Moon base | early flight (daily for 5 days after launch; see the gravity arrival check for the landing) |
| Mars base | early flight (daily for 5 days after launch; see the gravity arrival check for the landing) |

**Do it more often when:**

- Symptoms still present after 5 days. Then: daily (until resolved). (team default; RT04)

**Body region.** head.

**Sources, quoted exactly.**

- RT04: "crew have daily private medical conferences (PMC) on launch day and daily for 5 days per the NASA Medical Operations Requirements Document (MORD)."
- RT04: "Initial symptoms occur in most crew in the first 3-7 days."
- RT04: "neuromuscular changes affecting voiding reflex can cause urinary retention in the first few days."
- RT01: "Space adaptation sickness can be persistent for a number of days, and motion sickness associated with adaptation can last, on average, for 3 days."

<a id="gravity-arrival-check"></a>

### 34. After landing on the Moon or Mars: gravity check

**What it is.** In the first days after landing on the Moon or Mars, stand up slowly with a crewmate near, and check dizziness, balance, pulse and blood pressure.

**Why it matters.** After weeks or months of weightlessness, standing can make you faint and your balance is off. On Mars there is no ground team to help in real time.

**Who.** You, a crewmate and the crew medical officer.

**Instrument.** Kit cuff and oximeter; camera station; the app.

**Time.** About 10 minutes (team estimate).

**Steps.**

1. Before standing, sit for a minute. Stand up slowly with a crewmate beside you.
2. Take pulse and blood pressure lying or sitting, then after standing still for a few minutes, if you feel well enough to stand.
3. Sit or lie down at once if you feel faint, sweaty or sick.
4. Note dizziness, nausea, unsteady walking or blurred vision.
5. Do not start surface walks until the crew medical officer agrees.

**How often.** Default: on these events: landing on the Moon or Mars (daily for the first 5 days). Basis: team default.
Frequency to be set by the flight surgeon; the cadence shown is a team default.

| Environment | Cadence |
|---|---|
| Station (LEO) | as needed (not applicable) |
| Transit | as needed (not applicable) |
| Moon base | on these events: lunar landing (daily for the first 5 days) |
| Mars base | on these events: Mars landing (daily for the first 5 days; longer if the transit was long) |

**Do it more often when:**

- Fainting or near fainting. Then: as needed (press Someone needs help). (team default; C09)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT02: "Preventive measures for orthostatic intolerance and neuro-vestibular challenges during G-transitions and adaptation to a new gravity environment."
- RT02: "rehabilitation on the planetary surface, analogous to the functions of the post-Earth-landing recovery team"
- C09: "Orthostatic intolerance (OI) is an abnormal response observed during return to a gravitational environment after microgravity exposure."
- C09: "It can result in presyncope and, ultimately, syncope (i.e., loss of consciousness)."
- RT23: "Recumbent seating helps protect cerebral blood flow during Az (head-to-foot) vehicle accelerations (planetary and lunar) and return to gravity (planetary)."

**Note.** No source read here sets a check schedule after a planetary landing. The 5 days are a team default; frequency to be set by the flight surgeon.

<a id="pre-return-prep"></a>

### 35. Before return: entry preparation

**What it is.** In the last weeks before coming home, do the final eye exam, the daily talks in the last five days, and the fluid loading the flight surgeon prescribes.

**Why it matters.** Returning to gravity after a long time in space makes people faint on standing. Drinking a set amount of fluid with salt before entry, and a final check, lower that risk.

**Who.** You, the crew medical officer and the ground (flight surgeon or specialists).

**Instrument.** Kit cuff, oximeter; eye exam devices where available; fluid and salt tablets from the kit.

**Time.** About 10 minutes (team estimate).

**Steps.**

1. About 30 days before return: formal eye tests (and imaging if carried).
2. From five days before entry: daily private medical conference.
3. Keep up exercise to the end.
4. Do the pre-landing medical evaluation with the crew medical officer.
5. Before entry: drink the fluid and salt the flight surgeon prescribed, and note the amount.

**How often.** Default: return. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | return |
| Transit | return (before Earth entry, and before a Mars landing) |
| Moon base | return (before leaving the surface and before Earth entry) |
| Mars base | return (before leaving the surface; Earth entry follows the return transit) |

**Do it more often when:**

- The five days before entry. Then: daily. (sourced; RT04)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT04: "Daily beginning five days prior to entry and on the morning of entry/landing."
- S12: "During 6-month flights, OCT operator tests 3 times FD 30, 90, and R-30"
- RT01: "Pre-landing med evaluations"
- RT03: "Without fluid loading, the crew is more likely to experience orthostatic intolerance (passing out when standing) during and after deorbit."
- RT23: "Crewmembers consume water with salt tablets or an alternative solution, amount based on weight, to increase their blood volume."

<a id="post-return-recovery"></a>

### 36. After landing on Earth: recovery checks

**What it is.** After landing, follow the medical exams and the daily reconditioning plan, and keep doing the app's short checks on the schedule below.

**Why it matters.** Muscles, balance, blood pressure control, eyes and sleep all need weeks to readjust. Regular checks show recovery and catch problems.

**Who.** You and the ground (flight surgeon or specialists).

**Instrument.** The app; ground clinic tests imported as reports.

**Time.** About 10 minutes (team estimate).

**Steps.**

1. Immediately after landing: medical checks by the recovery team. Accept help to move.
2. Do not drive or do heavy physical tasks until cleared.
3. Reconditioning: about 2 hours a day, 7 days a week, for 45 days.
4. Reaction test: daily for the first week, then 30, 60 and 90 days after landing.
5. Eye tests in the first days; behavioral health talks around days 3, 14 and 30 to 45; fitness tests at day 7 and day 30.
6. Keep the daily check going while you recover.

**How often.** Default: return. Basis: sourced.

| Environment | Cadence |
|---|---|
| Station (LEO) | return |
| Transit | return (after the long transit, recovery may take longer) |
| Moon base | return |
| Mars base | return (after the longest missions, recovery may take longer) |

**Do it more often when:**

- Fainting, falls, or a new symptom during recovery. Then: as needed (tell the recovery team). (team default; RT23)
- A long mission. Then: return (extend the plan as the flight surgeon decides). (sourced; RT23, RT14)

**Body region.** none (whole body or not about one place).

**Sources, quoted exactly.**

- RT02: "Physical examinations by a flight surgeon or designated medical support personnel immediately following landing and periodically thereafter, until crewmember status is stable."
- RT23: "Restricted Activities: Crew are usually advised against driving or performing tasks that require significant physical effort to avoid accidents."
- RT23: "While most astronauts can return to normal activities within about 45 days, those who have been on longer missions may need additional time and support to fully recover."
- RT14: "Reconditioning program lasts 45 days starting R+1"
- RT14: "Post-flight performed at R+7 and R+30"
- RT14: "Crewmember, Flight Surgeon and ACSR can request additional time past 45 days if needed"
- RT25: "Post-mission testing was conducted daily for the first week upon return to Earth and then conducted 30, 60, and 90 days thereafter."
- RT15: "R+3; R+14; R+30-45"
- S12: "R+1/3 days:"

## What could not be sourced

For each of these, the frequency or threshold is to be set by the flight surgeon or by clinical review. The schedule uses a labeled team default meanwhile.

- Daily self-report of symptoms, sleep and mood; daily camera pulse and breathing rate; weekly blood pressure and blood oxygen; daily fluid log. ISS measures vital signs in the monthly health exam (RT16, RT17); a more frequent self-measurement is the team's proposal.
- How often ISS crews measure body mass. It is a planned medical activity (RT18) and important to monitor (RT34), but no interval was found.
- How often the on-orbit hearing assessment runs. RT06 says it routinely monitors crew on orbit longer than 30 days, without an interval.
- How often crews check teeth and skin, check the medical kit, and run medical emergency drills. The standards require these (RT02, RT03) and ISS plans them (RT18), but no interval was found.
- How long to watch for decompression sickness after a spacewalk. The 120 minute mean onset (RT11) comes from ground chamber tests.
- Any self-check cadence after dust exposure, after a planetary landing, or in a solar particle event shelter.
- Eye imaging cadence beyond 6 month flights; ISS uses flight day 30, flight day 90 and 30 days before return (S12).
- All alert thresholds for "outside your usual range": threshold to be set by clinical review.

## Sources that could not be accessed

- The ISS Medical Operations Requirements Document (SSP 50260) copy on docplayer.net: the host did not resolve. The private medical conference schedule it contains is quoted instead through OCHMO-MTB-004 (RT04).
- A later medical operations requirements document listed on govtribe.com: access refused (HTTP 403).
- American Heart Association, CDC and Johns Hopkins Medicine pages on home blood pressure, pulse and temperature: access refused (HTTP 403). MedlinePlus, Cleveland Clinic and NHS pages were used instead.
- RT34 (body mass) was read as the PubMed abstract only; the full paper was not available.

## Sources

All saved texts are machine-extracted and unedited, with a short header. Retrieved 14 November 2026.

| ID | Title | Date on source | Access | URL | Saved as |
|---|---|---|---|---|---|
| RT01 | "OCHMO-TB-044 Medical Operations: Spaceflight Considerations" | 13 August 2025 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2025/08/ochmo-tb-044-medical-operations.pdf | `research/routine/RT01.txt` |
| RT02 | "NASA-STD-3001 Volume 1, Revision C: Crew Health" | Approved 15 September 2023 | Read (PDF text; chapters 3 to 6 read closely) | https://www.nasa.gov/wp-content/uploads/2023/11/nasa-std-3001-vol-1-rev-c-with-signature.pdf | `research/routine/RT02.txt` |
| RT03 | "NASA-STD-3001 Volume 2, Revision F: Human Factors, Habitability, and Environmental Health" | Approved 14 July 2026 | Searched (PDF text; sections 6.2, 6.3, 6.6, 6.8, 7, 9 read) | https://standards.nasa.gov/system/files/tmp/NASA-STD-3001%20Vol%202%20Rev%20F.pdf | `research/routine/RT03.txt` |
| RT04 | "OCHMO-MTB-004 Space Adaptation Sickness (SAS), Rev A" | 25 September 2025 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-004-space-adaptation-sickness-sas.pdf | `research/routine/RT04.txt` |
| RT05 | "OCHMO-TB-035 Acoustics, Rev C" | 6 October 2023 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-035-acoustics.pdf | `research/routine/RT05.txt` |
| RT06 | "Effects of Long Duration Space Flight on Low Frequency Hearing (Coste and Robinette, NTRS 20240009887)" | 6 August 2024 | Read in full (poster PDF text) | https://ntrs.nasa.gov/citations/20240009887 | `research/routine/RT06.txt` |
| RT07 | "Status - International Space Station (ISS) Crewmembers' Noise Exposures (Inter-Noise 2021, NTRS 20210016305)" | 2021 | Read (PDF text) | https://ntrs.nasa.gov/citations/20210016305 | `research/routine/RT07.txt` |
| RT08 | "Status: Crewmember Noise Exposures on the International Space Station (ICES 2015, NTRS 20150011048)" | 2015 | Read (PDF text) | https://ntrs.nasa.gov/citations/20150011048 | `research/routine/RT08.txt` |
| RT09 | "OCHMO-TB-020 Design for Ionizing Radiation Protection, Rev G" | 6 June 2025 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-020-radiation-protection.pdf | `research/routine/RT09.txt` |
| RT10 | "OCHMO-TB-037 Decompression Sickness (DCS) Risk Mitigation, Rev D" | 5 October 2026 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-037-decompression-sickness.pdf | `research/routine/RT10.txt` |
| RT11 | "Evidence Report: Risk of Reduced Crew Health and Performance Due to Decompression Sickness and Hypoxia" | Approved for public release 31 October 2023 | Searched (PDF text; reporting and onset sections read) | https://humanresearchroadmap.nasa.gov/Evidence/reports/Evidence%20Report%20Hypoxia%20and%20DCS_FINAL_1-8-2024.pdf | `research/routine/RT11.txt` |
| RT12 | "Evidence Report: Risk of Injury and Compromised Performance Due to EVA Operations" | Approved for public release 16 May 2022 | Searched (PDF text; hand injury sections read) | https://humanresearchroadmap.nasa.gov/Evidence/other/EVA%20Injury%20Evidence%20Report%20FINAL_6-14-2023.pdf | `research/routine/RT12.txt` |
| RT13 | "OCHMO-TB-011 Lunar Dust, Rev C" | 28 November 2023 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-011-lunar-dust-technical-brief.pdf | `research/routine/RT13.txt` |
| RT14 | "OCHMO-TB-031 Exercise Overview" | 2 October 2023 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-031-exercise-overview.pdf | `research/routine/RT14.txt` |
| RT15 | "Behavioral Health and Performance Operations at the NASA Johnson Space Center (Beven, AsMA 2017, NTRS 20170003866)" | 3 May 2017 | Read in full (slide PDF text) | https://ntrs.nasa.gov/citations/20170003866 | `research/routine/RT15.txt` |
| RT16 | "Health and Fitness Evaluations for Long Duration Microgravity Exposure (Roden and Ewert, abstract, NTRS 20060051792)" | 2006 | Read in full (abstract PDF text) | https://ntrs.nasa.gov/citations/20060051792 | `research/routine/RT16.txt` |
| RT17 | "Crew Health and Performance Integrated Data Service Platform (CHP-IDSP): Project Updates (HRP IWS 2025, NTRS 20250000330)" | February 2025 | Read in full (slide PDF text) | https://ntrs.nasa.gov/citations/20250000330 | `research/routine/RT17.txt` |
| RT18 | "Crew Time Analysis of Planned Medical Activities & Estimate of Time Required for 6 Month Increment (Dulin, Easter, Stratton, NTRS 20230015627)" | Posted 6 May 2024 | Read in full (poster PDF text) | https://ntrs.nasa.gov/citations/20230015627 | `research/routine/RT18.txt` |
| RT19 | "International Space Station Medical Operations (Jones, NTRS 20080010877)" | Undated on the document; NTRS record 2008 | Read in full (PDF text) | https://ntrs.nasa.gov/citations/20080010877 | `research/routine/RT19.txt` |
| RT20 | "Microbial Detection and Control on the International Space Station (Castro, November 2014, NTRS 20150001643)" | November 2014 | Read in full (slide PDF text) | https://ntrs.nasa.gov/citations/20150001643 | `research/routine/RT20.txt` |
| RT21 | "OCHMO-TB-046 Microbiology, Rev A" | 19 May 2025 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2025/05/ochmo-tb-046-microbiology-rev-a.pdf | `research/routine/RT21.txt` |
| RT22 | "OCHMO-MTB-012 Pharmaceutical Care, Rev B" | 12 January 2026 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2026/01/ochmo-mtb-012-pharmaceutical-care.pdf | `research/routine/RT22.txt` |
| RT23 | "OCHMO-TB-051 Crew Medical Support for Entry, Descent, and Landing" | 21 May 2026 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2026/05/ochmo-tb-051-crew-medical-support-entry-descent-landing.pdf | `research/routine/RT23.txt` |
| RT24 | "OCHMO-TB-006 Health Stabilization Program, Rev F" | 19 May 2025 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-006-health-stabilization-program.pdf | `research/routine/RT24.txt` |
| RT25 | "Sleep deficiency in spaceflight is associated with degraded neurobehavioral functions and elevated stress in astronauts on six-month missions aboard the International Space Station (Sleep 2022, PMC8919197)" | 2022 | Read (full text, PubMed Central) | https://pmc.ncbi.nlm.nih.gov/articles/PMC8919197/ | `research/routine/RT25.txt` |
| RT26 | "Validity and Sensitivity of a Brief Psychomotor Vigilance Test (PVT-B) to Total and Partial Sleep Deprivation (Acta Astronautica 2011, PMC3197786)" | 2011 | Read (full text, PubMed Central) | https://pmc.ncbi.nlm.nih.gov/articles/PMC3197786/ | `research/routine/RT26.txt` |
| RT27 | "Blood pressure measurement (MedlinePlus Medical Encyclopedia)" | Review date 23 July 2024 | Read in full | https://medlineplus.gov/ency/article/007490.htm | `research/routine/RT27.txt` |
| RT28 | "Pulse (MedlinePlus Medical Encyclopedia)" | Review date 1 January 2025 | Read in full | https://medlineplus.gov/ency/article/003399.htm | `research/routine/RT28.txt` |
| RT29 | "Vital Signs (Cleveland Clinic)" | Last updated 15 March 2023 | Read in full | https://my.clevelandclinic.org/health/articles/10881-vital-signs | `research/routine/RT29.txt` |
| RT30 | "High temperature (fever) in adults (NHS)" | Page last reviewed 19 October 2026 | Read in full | https://www.nhs.uk/symptoms/fever-in-adults/ | `research/routine/RT30.txt` |
| RT31 | "Dehydration (NHS)" | Page last reviewed 1 May 2026 | Read in full | https://www.nhs.uk/conditions/dehydration/ | `research/routine/RT31.txt` |
| RT32 | "All About the Amsler Grid and Daily Vision Checks for AMD (American Academy of Ophthalmology)" | Published 19 December 2025 | Read in full | https://www.aao.org/eye-health/tips-prevention/facts-about-amsler-grid-daily-vision-test | `research/routine/RT32.txt` |
| RT33 | "OCHMO-MTB-006 Shoulder Injury Overview & Treatment Guidance" | 4 September 2025 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-006-shoulder-injury.pdf | `research/routine/RT33.txt` |
| RT34 | "Body mass changes during long-duration spaceflight (Zwart and others, Aviat Space Environ Med 2014;85(9):897-904, PMID 25197887)" | September 2014 | Abstract only (PubMed record) | https://pubmed.ncbi.nlm.nih.gov/25197887/ | `research/routine/RT34.txt` |
| S12 | "OCHMO-MTB-001 Spaceflight Associated Neuro-ocular Syndrome (SANS)" | 21 February 2024 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf | `research/routine/S12.txt` |
| C14 | "OCHMO-TB-016 Behavioral Health and Performance, Rev B" | 24 October 2023 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-016-behavioral-health.pdf | `research/routine/C14.txt` |
| C15 | "OCHMO-TB-041 Sleep Accommodations, Rev B" | 23 October 2023 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-041-sleep-accommodations.pdf | `research/routine/C15.txt` |
| C25 | "OCHMO-TB-004 Carbon Dioxide, Rev D" | 10 March 2026 | Read (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-004-carbon-dioxide.pdf | `research/routine/C25.txt` |
| C08 | "Pulse Oximeter Basics (FDA)" | Content current as of 26 March 2025 | Read in full | https://www.fda.gov/consumers/consumer-updates/pulse-oximeter-basics | `research/routine/C08.txt` |
| C09 | "OCHMO-TB-019 Orthostatic Intolerance, Rev B" | 1 December 2023 | Read in full (PDF text) | https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-019-orthostatic-intolerance.pdf | `research/routine/C09.txt` |

S12, C08, C09, C14, C15 and C25 are existing package IDs (sources.csv and DEMO-CONDITIONS.md); their texts were saved again here so every quote can be checked.
